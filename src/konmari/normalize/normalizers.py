"""Built-in normalizers. Each returns a Result or raises Quarantined; none of them guesses (design decision 3)."""

import importlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TYPES = ("date", "datetime", "identifier", "phone", "name", "text", "quantity")
EPOCH = {"s": 1, "ms": 1_000, "us": 1_000_000, "µs": 1_000_000}
LEGAL_FORMS = {"gmbh", "mbh", "ag", "kg", "kgaa", "ug", "ohg", "gbr", "ev", "e v", "se", "co", "haftungsbeschrankt",
               "haftungsbeschraenkt", "ltd", "inc", "llc", "sarl", "bv", "nv"}
TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss"})
_TOKENS = [("YYYY", "%Y"), ("MM", "%m"), ("DD", "%d"), ("HH", "%H"), ("mm", "%M"), ("ss", "%S")]


class Quarantined(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason, detail)
        self.reason, self.detail = reason, detail


@dataclass
class Result:
    canonical: str
    unit: str | None = None
    zone: str | None = None
    match_key: str | None = None
    flags: list[str] = field(default_factory=list)


def strptime_pattern(pattern: str) -> str:
    for tok, fmt in _TOKENS:
        pattern = pattern.replace(tok, fmt)
    return pattern


def _parse(raw: str, pattern: str) -> datetime:
    try:
        return datetime.fromisoformat(raw) if pattern == "ISO" else datetime.strptime(raw, strptime_pattern(pattern))
    except ValueError as e:
        raise Quarantined("unparseable", f"does not match pattern {pattern}: {e}") from None


def norm_date(raw: str, p: dict) -> Result:
    return Result(_parse(raw, p["pattern"]).date().isoformat())


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def norm_datetime(raw: str, p: dict) -> Result:
    if unit := p.get("epoch_unit"):
        try:
            return Result(_utc(datetime.fromtimestamp(int(raw) / EPOCH[unit], timezone.utc)), zone="UTC")
        except ValueError:
            raise Quarantined("unparseable", f"not an integer epoch in {unit}") from None
    dt = _parse(raw, p["pattern"])
    if dt.tzinfo is not None:
        off = dt.utcoffset()
        sign, off = ("-", -off) if off < timedelta(0) else ("+", off)
        return Result(_utc(dt), zone=f"{sign}{off.seconds // 3600:02d}:{off.seconds % 3600 // 60:02d}")
    if not p.get("zone"):
        raise Quarantined("missing_timezone", "no offset in the value and no source zone in the spec")
    try:
        tz = ZoneInfo(p["zone"])
    except ZoneInfoNotFoundError:
        raise Quarantined("unknown_timezone", p["zone"]) from None
    a, b = dt.replace(tzinfo=tz, fold=0), dt.replace(tzinfo=tz, fold=1)
    if a.utcoffset() != b.utcoffset():
        # the wall time either repeats (fall back) or does not exist (spring forward)
        roundtrip = a.astimezone(timezone.utc).astimezone(tz).replace(tzinfo=None)
        reason = "ambiguous_local_time" if roundtrip == dt else "nonexistent_local_time"
        raise Quarantined(reason, f"{raw} in {p['zone']}")
    return Result(_utc(a), zone=p["zone"])


def norm_identifier(raw: str, p: dict) -> Result:
    from stdnum.exceptions import ValidationError

    try:
        mod = importlib.import_module(f"stdnum.{p['kind']}")
    except ImportError:
        raise Quarantined("unknown_identifier_kind", p["kind"]) from None
    try:
        canon = mod.validate(raw)
    except ValidationError as e:
        reason = re.sub(r"(?<!^)(?=[A-Z])", "_", type(e).__name__).lower()  # InvalidChecksum -> invalid_checksum
        raise Quarantined(reason, str(e)) from None
    if p["kind"].endswith(".vat") and canon[:2].isdigit():  # stdnum drops the country prefix, VIES keeps it
        canon = p["kind"].split(".")[0].upper() + canon
    return Result(canon, flags=["syntax_valid"])  # check digits prove syntax only, not that the ID exists


def norm_phone(raw: str, p: dict) -> Result:
    import phonenumbers

    try:
        num = phonenumbers.parse(raw, p.get("region"))
    except phonenumbers.NumberParseException as e:
        raise Quarantined("invalid_format", str(e)) from None
    if not phonenumbers.is_valid_number(num):
        raise Quarantined("invalid_phone", "not a valid number for its region")
    return Result(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164), flags=["syntax_valid"])


def match_key(text: str, p: dict) -> str:
    k = unicodedata.normalize("NFKC", text)
    if p.get("transliterate_de"):
        k = k.translate(TRANSLIT)
    k = re.sub(r"[^\w]+", " ", k.casefold()).strip()
    if p.get("strip_legal_forms"):
        for form in sorted(LEGAL_FORMS, key=len, reverse=True):
            k = re.sub(rf"\b{form}\b", " ", k)
    return " ".join(k.split())


def norm_name(raw: str, p: dict) -> Result:
    display = " ".join(unicodedata.normalize("NFC", raw).split())
    if not display:
        raise Quarantined("empty", "only whitespace")
    return Result(display, match_key=match_key(display, p))


@cache
def _registry(vocabulary: str):
    if vocabulary == "ucum":
        from ucumvert import PintUcumRegistry, UcumToPintStrTransformer, get_ucum_parser

        reg = PintUcumRegistry()
        parser, transformer = get_ucum_parser(), UcumToPintStrTransformer()
        reg.preprocessors.append(lambda s: str(transformer.transform(parser.parse(s))))  # one parser, not one per call
        return reg
    import pint

    return pint.UnitRegistry()


@cache
def _unit(vocabulary: str, unit: str):
    return _registry(vocabulary)(unit)


def norm_quantity(raw: str, p: dict) -> Result:
    import pint

    m = re.fullmatch(r"\s*([+-]?[\d.,]+)\s*(\S.*?)\s*", raw)
    if not m:
        raise Quarantined("unparseable", "expected '<number> <unit>'")
    num, unit = m[1], m[2]
    num = num.replace(".", "").replace(",", ".") if p.get("decimal_comma") else num.replace(",", "")
    try:
        value = float(num)
    except ValueError:
        raise Quarantined("unparseable", f"number {m[1]!r}") from None
    try:
        q = value * _unit(p.get("vocabulary", "pint"), unit)  # runs the registry's preprocessors (UCUM -> pint)
        q.to(q.units)
    except Exception:  # pint and the UCUM grammar raise several error types for unknown unit strings
        raise Quarantined("unknown_unit", unit) from None
    try:
        v = q.to(_unit(p.get("vocabulary", "pint"), p["target_unit"]).units).magnitude
    except pint.DimensionalityError:
        raise Quarantined("incompatible_dimension", f"{unit} cannot be converted to {p['target_unit']}") from None
    if p.get("allowed_units") and unit not in p["allowed_units"]:
        raise Quarantined("unit_not_allowed", f"{unit} is not in {p['allowed_units']}")
    return Result(f"{v:.12g}", unit=p["target_unit"])


NORMALIZERS = {"date": norm_date, "datetime": norm_datetime, "identifier": norm_identifier, "phone": norm_phone,
               "name": norm_name, "text": norm_name, "quantity": norm_quantity}


def normalize(type_: str, raw: str, parser: dict) -> Result:
    return NORMALIZERS[type_](str(raw).strip(), parser or {})
