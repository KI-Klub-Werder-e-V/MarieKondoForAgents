"""Synthetic c05 pilot fixture: about 1,000 fictional customers spread over a CRM, an ERP and a SharePoint list,
with known cross-source duplicates, DST edge cases, invalid IBAN/VAT IDs and mixed units. No real personal data."""

import csv
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import phonenumbers
from stdnum.iso7064 import mod_11_10

from conftest import valid_iban

FIXTURES = Path(__file__).parent / "fixtures" / "sot"
SYL = ["ber", "lin", "ham", "wal", "ster", "dor", "men", "sach", "rin", "tal", "wei", "feld", "stein", "bach", "ko",
       "ra", "ul", "mo", "ne", "fi"]
BASE = ["Müller", "Schröder", "Bäcker", "Göbel", "Fröhlich", "Jäger", "Köhler", "Krüger"]
FORMS = ["GmbH", "AG", "KG", "e.V.", "UG"]
AREAS = ["30", "40", "89", "221", "69"]
POSTCODES = [f"{10115 + 977 * i:05d}" for i in range(60)]
TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "AE", "Ö": "OE", "Ü": "UE", "ß": "SS"})
CRM, ERP, SP = range(0, 800), range(300, 1000), range(700, 900)
REVIEW = {i for i in ERP if i % 50 == 7 and i in CRM}  # ERP copy with another phone and no VAT ID -> review band


def vat_id(rnd) -> str:
    b = str(rnd.randint(10_000_000, 99_999_999))
    return "DE" + b + mod_11_10.calc_check_digit(b)


def phone(rnd) -> tuple[str, str]:
    while True:
        area, num = rnd.choice(AREAS), str(rnd.randint(2_000_000, 9_999_999))
        if phonenumbers.is_valid_number(phonenumbers.parse(f"0{area}{num}", "DE")):
            return area, num


def customers(seed=5):
    rnd = random.Random(seed)
    out = []
    for i in range(1000):
        unique = (SYL[i % 20] + SYL[(i // 20) % 20] + SYL[(i // 400) % 20]).capitalize()
        out.append({"i": i, "name": f"{unique} {BASE[i % 8]} {FORMS[i % 5]}", "vat": vat_id(rnd), "phone": phone(rnd),
                    "phone2": phone(rnd), "plz": rnd.choice(POSTCODES), "pref": rnd.choice(["email", "phone", "post"]),
                    "iban": valid_iban(1000 + i), "kg": rnd.randint(200, 9000),
                    "ts": datetime(2025, 1, 6, 9, 0) + timedelta(hours=rnd.randint(0, 6000))})
    return out


def records(seed=5) -> list[dict]:
    rows = []
    for c in customers(seed):
        i, (area, num), kg = c["i"], c["phone"], c["kg"]
        if i in CRM:
            ts = c["ts"].strftime("%d.%m.%Y %H:%M")
            if i % 100 == 42:
                ts = "26.10.2025 02:30"  # repeated hour at the DST fall-back -> ambiguous_local_time
            if i % 100 == 46:
                ts = "30.03.2025 02:30"  # skipped hour at the DST spring-forward -> nonexistent_local_time
            vat = c["vat"][:-1] + str((int(c["vat"][-1]) + 1) % 10) if i % 100 == 43 else c["vat"]
            rows.append({"source": "crm", "object": "kontakt", "record_id": f"K-{i:04d}", "values": {
                "firma": c["name"], "ust_id": f"{vat[:2]} {vat[2:5]} {vat[5:8]} {vat[8:]}", "telefon": f"0{area} {num}",
                "plz": c["plz"], "kontaktweg": c["pref"], "zahlungsziel": "14 Tage",
                "email": f"kunde{i}@example.org", "geaendert": ts}})
        if i in ERP:
            name = c["name"].translate(TRANSLIT).upper().replace(" ", "  ") if i % 10 == 0 else c["name"]
            a2, n2 = c["phone2"] if i in REVIEW else c["phone"]
            iban = c["iban"][:-1] + str((int(c["iban"][-1]) + 1) % 10) if i % 100 == 44 else c["iban"]
            qty = "3 m" if i % 100 == 45 else (f"{kg // 1000},{kg % 1000:03d} t", f"{kg} kg", f"{kg * 1000} g")[i % 3]
            stand = c["ts"].replace(tzinfo=timezone(timedelta(hours=1))) + timedelta(days=2)
            rows.append({"source": "erp", "object": "debitor", "record_id": f"D-{i:04d}", "values": {
                "name": name, "ustid": None if i % 3 == 0 or i in REVIEW else c["vat"], "tel": f"+49 {a2} {n2}",
                "plz": c["plz"], "kontaktart": "post", "zahlungsbedingung": "30 Tage netto", "iban": iban,
                "liefermenge": qty, "stand": stand.isoformat()}})
        if i in SP:
            ms = int((c["ts"] - timedelta(days=30)).replace(tzinfo=timezone.utc).timestamp() * 1000)
            rows.append({"source": "sharepoint", "object": "kundenliste", "record_id": str(5000 + i), "values": {
                "titel": c["name"], "telefon": f"0{area}/{num}", "plz": c["plz"],
                "menge": f"{kg} kg" if i % 2 else f"{round(kg / 0.45359237)} [lb_av]", "erstellt": str(ms)}})
    # two sister companies at one address and phone: Splink links them, a reviewer has to split them
    sis = {"plz": "10115", "telefon": "030 7654321"}
    rows.append({"source": "crm", "object": "kontakt", "record_id": "K-S1", "values": {
        "firma": "Weber Bau GmbH", "telefon": sis["telefon"], "plz": sis["plz"], "kontaktweg": "email",
        "zahlungsziel": "14 Tage", "geaendert": "01.02.2025 10:00"}})
    rows.append({"source": "erp", "object": "debitor", "record_id": "D-S2", "values": {
        "name": "Weber Bau Nord GmbH", "tel": "+49 30 7654321", "plz": sis["plz"], "kontaktart": "post",
        "zahlungsbedingung": "30 Tage netto", "stand": "2025-02-03T10:00:00+01:00"}})
    return rows


def known_duplicates(seed=5) -> list[tuple[str, str]]:
    out = []
    for i in range(1000):
        keys = [k for k, rng in ((f"crm:K-{i:04d}", CRM), (f"erp:D-{i:04d}", ERP), (f"sharepoint:{5000 + i}", SP))
                if i in rng]
        out += [tuple(sorted((a, b))) for n, a in enumerate(keys) for b in keys[n + 1:]]
    return out


def write(dirpath: Path, seed=5) -> dict:
    dirpath.mkdir(parents=True, exist_ok=True)
    rec = dirpath / "records.jsonl"
    rec.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records(seed)), encoding="utf-8")
    known = dirpath / "known-duplicates.csv"
    with known.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["rec_a", "rec_b"])
        w.writerows(known_duplicates(seed))
    return {"records": rec, "known": known, "specs": FIXTURES / "normalize", "policy": FIXTURES / "policy" /
            "customer.yaml", "resolve": FIXTURES / "resolve.yaml", "exposure": FIXTURES / "exposure.yaml"}
