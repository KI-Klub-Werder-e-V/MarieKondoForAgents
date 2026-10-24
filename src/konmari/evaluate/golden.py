"""Golden question items and sets (design D9, D10): import with field validation, masking of user-derived questions
before storage, versioning, human approval, set activation with composition checks, retention of user-log items."""

import csv
import json
import math
import re
from datetime import date, timedelta
from pathlib import Path

from konmari import versions as v
from konmari.canonical import canonical_hash, load_yaml
from konmari.catalog import Catalog, now
from konmari.categories import store as cats
from konmari.charter.store import Scope
from konmari.enrich.store import language
from konmari.governance import pii
from konmari.governance.audit import write_event
from konmari.versions import Refused

STEP = "evaluate"
ORIGINS = ("user_log", "domain_expert", "synthetic")
BEHAVIOURS = ("answer", "abstain", "flag_conflict")
NEGATIVE = ("abstain", "flag_conflict")
APPROVER_ROLES = {"data_owner", "data_steward"}  # domain experts and source owners
RETENTION_DAYS = 365        # assumption (open question in design.md): 12 months unless the pilot's DPO decides
MIN_ITEMS, NEGATIVE_SHARE, MAX_SYNTHETIC = 50, 0.2, 0.5
FLOOR_ITEMS, FLOOR_NEGATIVE = 30, 5  # charter floor (c01 success-metrics)
PII_LOW = 0.4               # c07 default detection threshold (governance.yaml pii.low)
CUSTOMER_NUMBER = re.compile(r"(?i)\b(?:kunden-?(?:nummer|nr\.?)|kd\.?-?nr\.?|customer\s+(?:no\.?|number))\s*:?\s*"
                             r"([A-Z]{0,3}-?\d{4,})")


def event(cat: Catalog, action: str, actor: str, sc: Scope | None = None, result: str = "recorded", **details):
    write_event(cat, action, actor=actor, charter_id=sc and sc.charter_id, step=STEP, result=result, **details)


# ---------------------------------------------------------------- import

def load_items(path: str | Path) -> list[dict]:
    """CSV (expected_sources separated by ';') or YAML (a list, or {items: [...]})."""
    p = Path(path)
    if p.suffix.lower() == ".csv":
        with p.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["expected_sources"] = [s.strip() for s in (r.get("expected_sources") or "").split(";") if s.strip()]
        return rows
    doc = load_yaml(p)
    items = doc.get("items") if isinstance(doc, dict) else doc
    if not isinstance(items, list):
        raise Refused(f"{p}: expected a list of items or a mapping with 'items'")
    return items


def validate(item: dict, categories: set[str]) -> list[str]:
    errs = []
    for f in ("id", "question", "origin", "category", "expected_behaviour"):
        if not str(item.get(f) or "").strip():
            errs.append(f"{f}: required field is missing")
    if item.get("origin") and item["origin"] not in ORIGINS:
        errs.append(f"origin: '{item['origin']}' is not one of {', '.join(ORIGINS)}")
    if item.get("category") and item["category"] not in categories:
        errs.append(f"category: '{item['category']}' is not in the category map")
    b = item.get("expected_behaviour")
    if b and b not in BEHAVIOURS:
        errs.append(f"expected_behaviour: '{b}' is not one of {', '.join(BEHAVIOURS)}")
    if b == "answer" and not str(item.get("reference_answer") or "").strip():
        errs.append("reference_answer: required field is missing (expected behaviour 'answer')")
    srcs = item.get("expected_sources")
    if b in ("answer", "flag_conflict") and not (isinstance(srcs, list) and srcs):
        errs.append(f"expected_sources: required field is missing (expected behaviour '{b}')")
    return errs


def mask(text: str) -> str:
    """c07 sensitive-data masking (Presidio, DE/EN) plus customer numbers; placeholders like <PERSON>."""
    found = [{"start_pos": f["start"], "end_pos": f["end"], "entity_type": f["entity_type"]}
             for f in pii.analyze(text, language(text) or "de", PII_LOW)]
    found += [{"start_pos": m.start(1), "end_pos": m.end(1), "entity_type": "CUSTOMER_NUMBER"}
              for m in CUSTOMER_NUMBER.finditer(text)]
    return pii.apply_mask(text, found, {"default": "replace-with-type"}, b"")


def latest(cat: Catalog, item_id: str) -> dict | None:
    return _row(cat.one("SELECT * FROM golden_item WHERE item_id = ? ORDER BY version DESC LIMIT 1", (item_id,)))


def _row(r):
    if r:
        r["expected_sources"] = json.loads(r["expected_sources"])
    return r


def items(cat: Catalog, charter_id: str) -> list[dict]:
    """Latest version of every item of the pilot."""
    rows = [_row(r) for r in cat.query("SELECT * FROM golden_item WHERE charter_id = ? ORDER BY item_id, version",
                                       (charter_id,))]
    return list({r["item_id"]: r for r in rows}.values())


def import_items(cat: Catalog, sc: Scope, path: str | Path, actor: str, today: date | None = None) -> dict:
    """Valid items are stored as draft versions (an edit of an existing item is a new version); invalid ones are
    reported per item and not stored. Only the listed fields are kept: no user identity, no raw user question."""
    today = today or date.today()
    categories = {c["category_id"] for c in cats.categories(cat, sc)}
    out = {"stored": [], "unchanged": [], "errors": []}
    for raw in load_items(path):
        iid = str(raw.get("id") or "?")
        if errs := validate(raw, categories):
            out["errors"] += [f"{iid}: {e}" for e in errs]
            continue
        user = raw["origin"] == "user_log"
        doc = {"question": mask(raw["question"]) if user else raw["question"], "origin": raw["origin"],
               "category_id": raw["category"], "expected_behaviour": raw["expected_behaviour"],
               "reference_answer": raw.get("reference_answer") or None,
               "expected_sources": list(raw.get("expected_sources") or [])}
        h = canonical_hash(doc)
        prev = latest(cat, iid)
        if prev and prev["charter_id"] != sc.charter_id:
            out["errors"].append(f"{iid}: id: belongs to pilot '{prev['charter_id']}'")
            continue
        if prev and prev["content_hash"] == h:
            out["unchanged"].append(iid)
            continue
        version = prev["version"] + 1 if prev else 1
        cat.execute("INSERT INTO golden_item VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', NULL, NULL, ?, ?, ?, ?)",
                    (iid, version, sc.charter_id, doc["question"], doc["origin"], doc["category_id"],
                     doc["expected_behaviour"], doc["reference_answer"], json.dumps(doc["expected_sources"]),
                     today + timedelta(days=RETENTION_DAYS) if user else None, h, actor, now()))
        event(cat, "eval.item.import", actor, sc, item_id=iid, version=version, origin=doc["origin"],
              masked=user)
        out["stored"].append(f"{iid} v{version}")
    return out


# ---------------------------------------------------------------- approval

def approve(cat: Catalog, sc: Scope, item_id: str, actor: str, version: int | None = None,
            today: date | None = None) -> dict:
    roles = set((sc.stakeholders.get(actor) or {}).get("roles") or [])
    if not roles & APPROVER_ROLES:
        raise Refused(f"'{actor}' is not a domain expert or source owner (roles {', '.join(sorted(APPROVER_ROLES))})")
    row = _row(cat.one("SELECT * FROM golden_item WHERE item_id = ? AND version = ?", (item_id, version))) \
        if version else latest(cat, item_id)
    if row is None or row["charter_id"] != sc.charter_id:
        raise Refused(f"unknown golden item '{item_id}'" + (f" version {version}" if version else ""))
    keep = (today or date.today()) + timedelta(days=RETENTION_DAYS) if row["origin"] == "user_log" else None
    cat.execute("UPDATE golden_item SET status = 'approved', approved_by = ?, approved_at = ?, retention_until = ?"
                " WHERE item_id = ? AND version = ?", (actor, now(), keep, item_id, row["version"]))
    event(cat, "eval.item.approve", actor, sc, item_id=item_id, version=row["version"], previous=row["status"],
          retention_until=keep)
    return {**row, "status": "approved", "approved_by": actor}


# ---------------------------------------------------------------- golden set

def charter_minimums(cat: Catalog, charter_id: str) -> dict:
    """Defaults, or the approved success metrics' golden_set minimums (never below the charter floor)."""
    ms = v.latest_approved(cat, "metric_set_version", charter_id)
    gs = (ms or {}).get("doc", {}).get("golden_set") or {}
    if "min_questions" not in gs and "min_abstention" not in gs:
        return {"min_items": MIN_ITEMS, "min_negative": None, "source": "default"}
    return {"min_items": max(gs.get("min_questions", MIN_ITEMS), FLOOR_ITEMS),
            "min_negative": max(gs["min_abstention"], FLOOR_NEGATIVE) if "min_abstention" in gs else None,
            "source": "charter", "metric_set_version": ms["version"]}


def composition_errors(approved: list[dict], mins: dict) -> list[str]:
    n = len(approved)
    neg = sum(i["expected_behaviour"] in NEGATIVE for i in approved)
    syn = sum(i["origin"] == "synthetic" for i in approved)
    need_neg = max(mins["min_negative"] or math.ceil(NEGATIVE_SHARE * n - 1e-9), FLOOR_NEGATIVE)
    errs = []
    if n < mins["min_items"]:
        errs.append(f"{n} approved items, at least {mins['min_items']} are required")
    if neg < need_neg:
        errs.append(f"{neg} items expect abstain or flag_conflict, {need_neg} such items are required")
    if n and syn / n > MAX_SYNTHETIC:
        errs.append(f"synthetic share is {syn / n:.0%} ({syn} of {n} items); at most {MAX_SYNTHETIC:.0%} allowed")
    return errs


def activate(cat: Catalog, sc: Scope, actor: str) -> dict:
    """Pin the newest approved version of every item (drafts without one are pinned and excluded at run time)."""
    pins, approved = [], []
    for it in items(cat, sc.charter_id):
        ok = cat.one("SELECT version FROM golden_item WHERE item_id = ? AND status = 'approved' ORDER BY version DESC"
                     " LIMIT 1", (it["item_id"],))
        if ok and ok["version"] != it["version"]:
            it = _row(cat.one("SELECT * FROM golden_item WHERE item_id = ? AND version = ?",
                              (it["item_id"], ok["version"])))
        pins.append([it["item_id"], it["version"]])
        if it["status"] == "approved":
            approved.append(it)
    mins = charter_minimums(cat, sc.charter_id)
    if errs := composition_errors(approved, mins):
        event(cat, "eval.set.activate", actor, sc, result="refused", reasons=errs, minimums=mins)
        raise Refused(errs)
    last = cat.one("SELECT MAX(version) AS v FROM golden_set WHERE charter_id = ?", (sc.charter_id,))
    version = (last["v"] or 0) + 1
    cat.execute("UPDATE golden_set SET status = 'retired' WHERE charter_id = ? AND status = 'active'",
                (sc.charter_id,))
    cat.execute("INSERT INTO golden_set VALUES (?, ?, ?, 'active', ?, ?, ?)",
                (sc.charter_id, version, json.dumps(pins), json.dumps(mins), actor, now()))
    event(cat, "eval.set.activate", actor, sc, version=version, items=len(pins), approved=len(approved),
          minimums=mins, override="charter success metrics" if mins["source"] == "charter" else None)
    return {"version": version, "items": len(pins), "approved": len(approved), "minimums": mins}


def active_set(cat: Catalog, charter_id: str) -> dict | None:
    r = cat.one("SELECT * FROM golden_set WHERE charter_id = ? AND status = 'active'", (charter_id,))
    if r:
        r["items"], r["minimums"] = json.loads(r["items"]), json.loads(r["minimums"])
    return r


def pinned(cat: Catalog, gs: dict) -> list[dict]:
    out = []
    for iid, ver in gs["items"]:
        r = _row(cat.one("SELECT * FROM golden_item WHERE item_id = ? AND version = ?", (iid, ver)))
        out.append(r or {"item_id": iid, "version": ver, "status": "deleted"})
    return out


# ---------------------------------------------------------------- retention

def retention(cat: Catalog, sc: Scope, actor: str, today: date | None = None) -> list[str]:
    """Delete user-derived items whose retention date passed without re-approval (all versions)."""
    today = today or date.today()
    gone = []
    for it in items(cat, sc.charter_id):
        if it["origin"] == "user_log" and it["retention_until"] and it["retention_until"] < today:
            cat.execute("DELETE FROM golden_item WHERE item_id = ?", (it["item_id"],))
            event(cat, "eval.item.expire", actor, sc, item_id=it["item_id"],
                  retention_until=it["retention_until"], reason="retention date passed without re-approval")
            gone.append(it["item_id"])
    return gone
