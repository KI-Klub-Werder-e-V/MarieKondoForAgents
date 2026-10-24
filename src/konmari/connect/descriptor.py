"""Data product descriptor (design D1): an ODPS v1.0 product document plus one ODCS v3.1 contract, YAML in git.
Kit fields live in `customProperties`. `lint` checks the JSON Schemas, the mandatory elements and the upstream
steps (charter use case, scorecard verdicts, source of truth, governance policies, personal-data fields)."""

import json
import re
from datetime import timedelta
from functools import cache
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft201909Validator

from konmari.canonical import dump_yaml, load_yaml
from konmari.catalog import Catalog
from konmari.charter.store import Scope
from konmari.inventory.sensitivity import TIERS, rank

SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PRODUCT_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
UNITS = {"m": "minutes", "min": "minutes", "h": "hours", "d": "days", "w": "weeks", "y": "days"}
LOGICAL = {"string", "integer", "number", "boolean", "date", "timestamp"}
PERSONAL = rank("personal")
PRODUCT_FILE, CONTRACT_FILE = "product.yaml", "contract.yaml"


@cache
def _validator(name: str) -> Draft201909Validator:
    return Draft201909Validator(json.loads((files("konmari.connect") / "schemas" / name).read_text()))


def custom(obj: dict | None) -> dict:
    """ODPS/ODCS customProperties [{property, value}] as a dict."""
    return {p["property"]: p.get("value") for p in (obj or {}).get("customProperties") or [] if "property" in p}


def load(path: str | Path) -> dict:
    """A product directory with product.yaml (ODPS) and contract.yaml (ODCS).
    shortcut: one output port and one contract per product; add a contract per port when a pilot needs two."""
    d = Path(path)
    return {"product": load_yaml(d / PRODUCT_FILE) or {}, "contract": load_yaml(d / CONTRACT_FILE) or {}}


def skeleton(pid: str, name: str, category: str, owner: str, use_case: str) -> dict:
    """`konmari product init`: a descriptor to fill in; lint fails until every TODO is replaced."""
    contract_id = f"{pid}-contract"
    product = {
        "apiVersion": "v1.0.0", "kind": "DataProduct", "id": pid, "name": name, "version": "1.0.0",
        "status": "draft", "domain": category,
        "description": {"purpose": "TODO: what the agent may use this product for (reviewed by the DPO)",
                        "usage": "TODO: when to use it", "limitations": "TODO: when not to use it"},
        "team": {"members": [{"username": owner, "role": "owner"}]},
        "outputPorts": [{"name": pid, "version": "1.0.0", "type": "mcp", "contractId": contract_id}],
        "customProperties": [
            {"property": "charterUseCase", "value": use_case},
            {"property": "category", "value": category},
            {"property": "scopes", "value": [f"product:{pid}:read"]},
            {"property": "maskingPolicy", "value": category},
            {"property": "sources", "value": [{"id": "TODO-source", "assetUri": "TODO://asset",
                                               "locator": "sqlite:///TODO.db", "table": "TODO", "key": "id",
                                               "cursor": "modified_at", "sourceOfTruth": "primary"}]},
            {"property": "personalDataJustification", "value": {}},
        ],
    }
    contract = {
        "apiVersion": "v3.1.0", "kind": "DataContract", "id": contract_id, "name": name, "version": "1.0.0",
        "status": "draft", "domain": category,
        "description": {"purpose": "TODO"},
        "schema": [{"name": pid.replace("-", "_"), "physicalType": "table", "description": "TODO",
                    "properties": [{"name": "id", "logicalType": "string", "primaryKey": True, "required": True,
                                    "description": "TODO: business definition (glossary)",
                                    "classification": "internal"}]}],
        "slaProperties": [{"property": "latency", "value": 1, "unit": "d"}],
    }
    return {"product": product, "contract": contract}


def write(desc: dict, path: str | Path) -> None:
    d = Path(path)
    d.mkdir(parents=True, exist_ok=True)
    (d / PRODUCT_FILE).write_text(dump_yaml(desc["product"]), encoding="utf-8")
    (d / CONTRACT_FILE).write_text(dump_yaml(desc["contract"]), encoding="utf-8")


def owner(desc: dict) -> str | None:
    return next((m.get("username") for m in (desc["product"].get("team") or {}).get("members") or []
                 if m.get("role") == "owner"), None)


def table(desc: dict) -> dict:
    return (desc["contract"].get("schema") or [{}])[0]


def fields(desc: dict) -> list[dict]:
    """Contract properties with the kit custom properties flattened in."""
    out = []
    for p in table(desc).get("properties") or []:
        cp = custom(p)
        out.append({"name": p.get("name"), "type": p.get("logicalType"), "description": p.get("description"),
                    "classification": p.get("classification"), "required": bool(p.get("required")),
                    "unique": bool(p.get("unique")), "primary_key": bool(p.get("primaryKey")),
                    "filterable": bool(cp.get("filterable")), "pii_entity": cp.get("piiEntity"),
                    "glossary_term": cp.get("glossaryTerm"), "quality": p.get("quality") or []})
    return out


def key_field(desc: dict) -> str | None:
    return next((f["name"] for f in fields(desc) if f["primary_key"]), None)


def sla(desc: dict) -> timedelta | None:
    """The freshness SLA: ODCS slaProperties `latency` (value + unit d/h/m)."""
    for s in desc["contract"].get("slaProperties") or []:
        if s.get("property") in ("latency", "freshness") and isinstance(s.get("value"), (int, float)):
            unit = UNITS.get(str(s.get("unit", "d")).lower())
            if unit:
                v = s["value"] * (365 if str(s.get("unit")).lower() == "y" else 1)
                return timedelta(**{unit: v})
    return None


def info(desc: dict) -> dict:
    p, cp = desc["product"], custom(desc["product"])
    return {"id": p.get("id"), "name": p.get("name"), "version": p.get("version"),
            "purpose": (p.get("description") or {}).get("purpose"),
            "usage": (p.get("description") or {}).get("usage"),
            "limitations": (p.get("description") or {}).get("limitations"), "owner": owner(desc),
            "use_case": cp.get("charterUseCase"), "category": cp.get("category") or p.get("domain"),
            "scopes": cp.get("scopes") or [], "masking_policy": cp.get("maskingPolicy") or cp.get("category"),
            "sources": cp.get("sources") or [], "justified": cp.get("personalDataJustification") or {},
            "sla": sla(desc), "table": table(desc).get("name"), "key": key_field(desc)}


def major(version: str) -> int:
    return int(version.split(".")[0])


def parse_version(version: str) -> tuple[int, int, int]:
    return tuple(int(x) for x in version.split("."))


# ---------------------------------------------------------------- lint

def _schema_errors(doc: dict, name: str, root: str) -> list[str]:
    errs = []
    for e in sorted(_validator(name).iter_errors(doc), key=lambda e: list(map(str, e.absolute_path))):
        path = "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in e.absolute_path)
        errs.append(f"{root}{path}: {e.message}")
    return errs


def _todo(v) -> bool:
    return v is None or (isinstance(v, str) and (not v.strip() or v.strip().startswith("TODO")))


def _has_password(locator: str) -> bool:
    from sqlalchemy.engine import make_url

    try:
        return bool(make_url(locator).password)
    except Exception:  # not an SQLAlchemy URL: the refresh reports it
        return False


def mandatory_errors(desc: dict) -> list[str]:
    """Every mandatory element, reported by path."""
    p, c, i = desc["product"], desc["contract"], info(desc)
    errs = []
    if not PRODUCT_ID.match(str(p.get("id") or "")):
        errs.append("product.id: missing or not a lowercase id (a-z, 0-9, -)")
    for path, v in (("product.name", i["name"]), ("product.description.purpose", i["purpose"]),
                    ("product.team.members[role=owner]", i["owner"]),
                    ("product.customProperties.charterUseCase", i["use_case"]),
                    ("product.customProperties.category", i["category"])):
        if _todo(v):
            errs.append(f"{path}: missing")
    if not SEMVER.match(str(i["version"] or "")):
        errs.append("product.version: missing or not semantic (MAJOR.MINOR.PATCH)")
    elif str(c.get("version")) != i["version"]:
        errs.append(f"contract.version: '{c.get('version')}' differs from product.version '{i['version']}'")
    if f"product:{i['id']}:read" not in i["scopes"]:
        errs.append(f"product.customProperties.scopes: must contain 'product:{i['id']}:read'")
    if i["sla"] is None:
        errs.append("contract.slaProperties[latency]: missing freshness SLA (value + unit d/h/m)")
    if not i["sources"]:
        errs.append("product.customProperties.sources: at least one source dataset is required")
    for n, s in enumerate(i["sources"]):
        for k in ("id", "assetUri", "locator", "table", "key", "cursor"):
            if _todo(s.get(k)):
                errs.append(f"product.customProperties.sources[{n}].{k}: missing")
        for k in ("table", "key", "cursor", "aclColumn"):
            if s.get(k) and not _todo(s.get(k)) and not IDENT.match(str(s[k])):
                errs.append(f"product.customProperties.sources[{n}].{k}: '{s[k]}' is not a plain identifier")
        if s.get("locator") and not _todo(s["locator"]) and _has_password(str(s["locator"])):
            errs.append(f"product.customProperties.sources[{n}].locator: contains a password; use secretRef "
                        "(env:NAME or file:/path)")
        if s.get("sourceOfTruth") not in ("primary", "secondary"):
            errs.append(f"product.customProperties.sources[{n}].sourceOfTruth: must be primary or secondary")
    schema = c.get("schema") or []
    if len(schema) != 1:
        errs.append("contract.schema: exactly one table per contract")
    flds = fields(desc)
    if not flds:
        errs.append("contract.schema[0].properties: at least one field is required")
    for n, f in enumerate(flds):
        path = f"contract.schema[0].properties[{n}]"
        if not f["name"] or not IDENT.match(str(f["name"])):
            errs.append(f"{path}.name: missing or not a plain identifier")
        if _todo(f["description"]):
            errs.append(f"{path}.description: missing business definition for field '{f['name']}'")
        if f["classification"] not in TIERS:
            errs.append(f"{path}.classification: one of {', '.join(TIERS)} is required for field '{f['name']}'")
        if f["type"] not in LOGICAL:
            errs.append(f"{path}.logicalType: one of {', '.join(sorted(LOGICAL))} is required")
    if sum(f["primary_key"] for f in flds) != 1:
        errs.append("contract.schema[0].properties: exactly one primaryKey field (the merge key) is required")
    return errs


def upstream_errors(cat: Catalog, sc: Scope, charter_doc: dict, desc: dict) -> list[str]:
    """Only ready and governed data enters a product (c01, c03, c05, c06, c07)."""
    from konmari.enrich import store as est
    from konmari.glossary import store as gls
    from konmari.governance import policy as pol
    from konmari.scorecard import review

    i, errs = info(desc), []
    cases = [u.get("name") for u in charter_doc.get("use_cases") or []]
    if i["use_case"] not in cases:
        errs.append(f"product.customProperties.charterUseCase: '{i['use_case']}' is not a use case of the approved "
                    f"charter {sc.charter_id} v{sc.charter_version}; the product is out of pilot scope")
    if i["category"] not in sc.categories:
        errs.append(f"product.customProperties.category: '{i['category']}' is not in the pilot data scope")
    for s in i["sources"]:
        verdict = review.effective_verdict(cat, s.get("assetUri"))
        if verdict != "keep":
            errs.append(f"source dataset '{s.get('id')}' ({s.get('assetUri')}) has readiness scorecard result "
                        f"'{verdict or 'none'}'; only 'keep' may enter a product")
        auth = est.authority(cat, s.get("sourceSystem"), s.get("id"))
        if s.get("sourceOfTruth") and s["sourceOfTruth"] != auth:
            errs.append(f"source dataset '{s.get('id')}' is declared {s['sourceOfTruth']} but the source-of-truth "
                        f"policy ranks it {auth}")
        if s.get("entity"):
            n = cat.one("SELECT COUNT(*) AS n FROM conflict WHERE entity = ? AND status IN ('open', 'reopened')",
                        (s["entity"],))["n"]
            if n:
                errs.append(f"source dataset '{s.get('id')}': {n} unresolved source-of-truth conflict(s) for "
                            f"entity '{s['entity']}'")
    policy = pol.active(cat, i["masking_policy"]) if i["masking_policy"] else None
    if policy is None:
        errs.append(f"product.customProperties.maskingPolicy: no approved access and masking policy "
                    f"'{i['masking_policy']}' (konmari govern policy approve)")
    elif policy["doc"].get("exposure") != "allowed":
        errs.append(f"policy '{i['masking_policy']}' does not allow agent exposure")
    rules = (policy or {}).get("doc", {}).get("masking") or {}
    for f in fields(desc):
        if f["glossary_term"] and gls.resolve_approved(cat, f["glossary_term"]) is None:
            errs.append(f"field '{f['name']}': glossary term '{f['glossary_term']}' is not approved")
        if f["classification"] in TIERS and rank(f["classification"]) >= PERSONAL:
            masked = f["pii_entity"] and f["pii_entity"] in rules and f["pii_entity"] != "default"
            if not masked and not i["justified"].get(f["name"]):
                errs.append(f"field '{f['name']}' is {f['classification']} data with no masking policy rule and no "
                            "justification in the purpose statement (DSGVO Art. 5(1)(c) data minimisation)")
    return errs


def lint(desc: dict, cat: Catalog | None = None, sc: Scope | None = None, charter_doc: dict | None = None
         ) -> list[str]:
    errs = _schema_errors(desc["product"], "odps-1.0.0.schema.json", "product")
    errs += _schema_errors(desc["contract"], "odcs-3.1.0.schema.json", "contract")
    errs += mandatory_errors(desc)
    if cat is not None and sc is not None and not errs:
        errs += upstream_errors(cat, sc, charter_doc or {}, desc)
    return errs
