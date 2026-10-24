"""Normalization specs (versioned YAML), additive normalization runs, quarantine, conformance and ODCS export."""

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from konmari import versions as v
from konmari.canonical import canonical_hash, canonical_json, load_yaml
from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.normalize.normalizers import TYPES, Quarantined, match_key, normalize
from konmari.standardize import PERSONAL_TIERS, lineage_event, log, mask, may_view, metric, tier_of
from konmari.versions import Refused

REQUIRED = ("source", "field", "entity", "attribute", "type", "owner")
PARSER_KEYS = {"date": ["pattern"], "identifier": ["kind"], "phone": ["region"], "quantity": ["target_unit"]}
CANONICAL = {"date": r"\d{4}-\d{2}-\d{2}", "datetime": r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
             "phone": r"\+[1-9]\d{1,14}", "identifier": r"[A-Z0-9]+", "quantity": r"-?\d+(\.\d+)?(e[+-]?\d+)?"}
LOGICAL = {"date": "date", "datetime": "date", "quantity": "number"}


# ---------------------------------------------------------------- specs

def spec_errors(doc) -> list[str]:
    if not isinstance(doc, dict) or not isinstance(doc.get("fields"), list):
        return ["a normalization spec file needs a 'fields' list"]
    errs, seen = [], set()
    for i, f in enumerate(doc["fields"]):
        name = f"fields[{i}]" + (f" ({f.get('source')}.{f.get('field')})" if isinstance(f, dict) else "")
        if not isinstance(f, dict):
            errs.append(f"{name}: must be a mapping")
            continue
        errs += [f"{name}: '{k}' is required" for k in REQUIRED if not f.get(k)]
        if f.get("type") and f["type"] not in TYPES:
            errs.append(f"{name}: type must be one of {', '.join(TYPES)}")
        if "." not in str(f.get("field", ".")):
            errs.append(f"{name}: field must be 'object.column', e.g. debitor.zahlungsbedingung")
        p = f.get("parser") or {}
        errs += [f"{name}: parser.{k} is required for type {f['type']}" for k in PARSER_KEYS.get(f.get("type"), [])
                 if not p.get(k)]
        if f.get("type") == "datetime" and not (p.get("pattern") or p.get("epoch_unit")):
            errs.append(f"{name}: parser.pattern or parser.epoch_unit (s, ms, us) is required for type datetime")
        key = (f.get("source"), f.get("field"))
        if key in seen:
            errs.append(f"{name}: declared twice")
        seen.add(key)
    return errs


def read_specs(path: str | Path) -> dict:
    """One spec set from a directory of *.yaml files (or one file)."""
    path = Path(path)
    files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
    if not files or not all(p.is_file() for p in files):
        raise Refused(f"no normalization spec files at {path}")
    fields = []
    for p in files:
        doc = load_yaml(p)
        if errs := spec_errors(doc):
            raise Refused([f"{p}: {e}" for e in errs])
        fields += doc["fields"]
    doc = {"fields": sorted(fields, key=lambda f: (f["source"], f["field"]))}
    if errs := spec_errors(doc):
        raise Refused(errs)
    return doc


def latest_spec(cat: Catalog) -> dict | None:
    row = cat.one("SELECT * FROM normalization_spec ORDER BY version DESC LIMIT 1")
    if row:
        row["doc"] = json.loads(row["content"])
    return row


def load_specs(cat: Catalog, path: str | Path, author: str) -> dict:
    """Store the spec set as a new version when it changed (author, timestamp, diff); return the current row."""
    doc = read_specs(path)
    last = latest_spec(cat)
    if last and last["content_hash"] == canonical_hash(doc):
        return last
    keyed = lambda d: {f"{f['source']}.{f['field']}": f for f in d["fields"]}  # noqa: E731
    diff = v.diff(keyed(last["doc"]) if last else {}, keyed(doc))
    ver = (last["version"] if last else 0) + 1
    cat.execute("INSERT INTO normalization_spec VALUES (?, ?, ?, ?, ?, ?)",
                (ver, canonical_json(doc), canonical_hash(doc), author, now(), json.dumps(diff, ensure_ascii=False)))
    log(cat, None, "normalize.spec_version", "normalization_spec", last and last["version"], ver, author,
        diff=diff)
    return latest_spec(cat)


def spec_index(doc: dict) -> dict[str, dict]:
    return {f"{f['source']}.{f['field']}": f for f in doc["fields"]}


def is_personal(cat: Catalog, spec: dict) -> bool:
    return tier_of(cat, spec.get("asset")) in PERSONAL_TIERS


# ---------------------------------------------------------------- runs

def read_records(path: str | Path) -> list[dict]:
    """JSONL, one record per line: {"source", "object", "record_id", "values": {column: raw}}."""
    out = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        r = json.loads(line)
        if not all(r.get(k) for k in ("source", "object", "record_id")) or not isinstance(r.get("values"), dict):
            raise Refused(f"{path}:{n}: a record needs source, object, record_id and a values mapping")
        out.append(r)
    return out


def _excluded(cat: Catalog) -> set[str]:
    """Records the discard pass (c04) took out of the corpus are not normalized."""
    return {r["item_id"] for r in cat.query("SELECT item_id FROM corpus_item WHERE state <> 'active'")}


def run(cat: Catalog, records_path: str | Path, specs_path: str | Path, actor: str, transport=None) -> dict:
    spec = load_specs(cat, specs_path, actor)
    idx = spec_index(spec["doc"])
    records = read_records(records_path)
    missing = sorted({f"{r['source']}.{r['object']}.{k}" for r in records for k in r["values"]
                      if f"{r['source']}.{r['object']}.{k}" not in idx})
    if missing:
        raise Refused([f"no normalization spec for field {m}; nothing was normalized" for m in missing])
    input_hash = hashlib.sha256(Path(records_path).read_bytes()).hexdigest()
    run_id = hashlib.sha256(f"{spec['version']}:{input_hash}".encode()).hexdigest()[:16]
    if prior := cat.one("SELECT * FROM normalize_run WHERE run_id = ?", (run_id,)):
        return {"run_id": run_id, "spec_version": spec["version"], "skipped": True,
                "conformance": conformance(cat, run_id)} | {"read": json.loads(prior["metrics"])}
    excluded, ts = _excluded(cat), now()
    values, quarantined, read = [], [], Counter()
    for r in records:
        if r["record_id"] in excluded or f"{r['source']}:{r['record_id']}" in excluded:
            continue
        for col, raw in r["values"].items():
            if raw is None or str(raw).strip() == "":
                continue
            sf = f"{r['source']}.{r['object']}.{col}"
            f = idx[sf]
            read[sf] += 1
            base = (run_id, r["source"], str(r["record_id"]), sf, f["entity"], f["attribute"], str(raw))
            try:
                res = normalize(f["type"], raw, f.get("parser"))
            except Quarantined as q:
                quarantined.append((new_id(), *base, q.reason, q.detail, spec["version"], f["owner"], "open",
                                    None, None, None, ts))
                continue
            values.append((new_id(), *base, res.canonical, res.unit, res.zone, res.match_key, json.dumps(res.flags),
                           f["type"], spec["version"], "auto", ts, None))
    cat.many("INSERT INTO normalized_value VALUES (" + ", ".join("?" * 18) + ")", values)
    cat.many("INSERT INTO quarantine VALUES (" + ", ".join("?" * 17) + ")", quarantined)
    sources = sorted({r["source"] for r in records})
    cat.execute("INSERT INTO normalize_run VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, spec["version"], input_hash, json.dumps(sources), actor, ts, json.dumps(read)))
    conf = conformance(cat, run_id)
    record_conformance(cat, run_id, conf)
    lineage_event(f"normalize.{run_id}", "normalized_value",
                  {sf: [(idx[sf]["source"], *idx[sf]["field"].split(".", 1), "TRANSFORMATION")] for sf in read},
                  transport)
    log(cat, None, "normalize.run", run_id, None, {"values": len(values), "quarantined": len(quarantined)}, actor,
        spec_version=spec["version"], sources=sources)
    return {"run_id": run_id, "spec_version": spec["version"], "skipped": False, "canonical": len(values),
            "quarantined": len(quarantined), "conformance": conf}


def conformance(cat: Catalog, run_id: str) -> dict:
    """Per field: every value read must end up canonical or quarantined with a reason (no silent drops)."""
    r = cat.one("SELECT metrics FROM normalize_run WHERE run_id = ?", (run_id,))
    if r is None:
        raise Refused(f"normalization run '{run_id}' not found")
    read = json.loads(r["metrics"])
    canon = Counter(x["source_field"] for x in cat.query(
        "SELECT source_field FROM normalized_value WHERE run_id = ? AND origin = 'auto'", (run_id,)))
    qrows = cat.query("SELECT source_field, reason FROM quarantine WHERE run_id = ?", (run_id,))
    quar, reasons = Counter(x["source_field"] for x in qrows), defaultdict(Counter)
    for x in qrows:
        reasons[x["source_field"]][x["reason"]] += 1
    out = {}
    for sf, n in sorted(read.items()):
        un = n - canon[sf] - quar[sf]
        out[sf] = {"read": n, "canonical": canon[sf], "quarantined": quar[sf], "unaccounted": un,
                   "canonical_rate": round(canon[sf] / n, 4), "quarantine_rate": round(quar[sf] / n, 4),
                   "reasons": dict(reasons[sf]), "conformant": un == 0}
    return out


def record_conformance(cat: Catalog, run_id: str, conf: dict) -> None:
    for sf, c in conf.items():
        for name in ("canonical_rate", "quarantine_rate", "unaccounted", "conformant"):
            metric(cat, "conformance", name, c[name], run_id, field=sf)
        for reason, n in c["reasons"].items():
            metric(cat, "conformance", f"reason:{reason}", n, run_id, field=sf)


def rollback(cat: Catalog, run_id: str, actor: str, reason: str) -> dict:
    """Drop one run's canonical and quarantine rows. Raw data at the source is never touched."""
    if not cat.one("SELECT run_id FROM normalize_run WHERE run_id = ?", (run_id,)):
        raise Refused(f"normalization run '{run_id}' not found")
    n = cat.one("SELECT COUNT(*) AS n FROM normalized_value WHERE run_id = ?", (run_id,))["n"]
    for t in ("normalized_value", "quarantine", "normalize_run"):
        cat.execute(f"DELETE FROM {t} WHERE run_id = ?", (run_id,))
    log(cat, None, "normalize.rollback", run_id, {"values": n}, None, actor, reason)
    return {"run_id": run_id, "values_removed": n}


def current_values(cat: Catalog, entity: str) -> dict[str, dict[str, dict]]:
    """source:record_id -> attribute -> value row, from the latest run of each source (not withdrawn)."""
    latest = {}
    for r in cat.query("SELECT run_id, sources FROM normalize_run ORDER BY started_at"):
        for s in json.loads(r["sources"]):
            latest[s] = r["run_id"]
    out = defaultdict(dict)
    for r in cat.query("SELECT * FROM normalized_value WHERE entity = ? AND withdrawn_at IS NULL"
                       " ORDER BY processed_at", (entity,)):
        if latest.get(r["source"]) == r["run_id"]:
            out[f"{r['source']}:{r['record_id']}"][r["attribute"]] = r
    return dict(out)


def latest_run_id(cat: Catalog) -> str | None:
    r = cat.one("SELECT run_id FROM normalize_run ORDER BY started_at DESC LIMIT 1")
    return r["run_id"] if r else None


# ---------------------------------------------------------------- quarantine

def quarantine_list(cat: Catalog, sc: Scope | None, viewer: str | None, status: str | None = "open") -> list[dict]:
    spec = latest_spec(cat)
    idx = spec_index(spec["doc"]) if spec else {}
    rows = cat.query("SELECT * FROM quarantine ORDER BY created_at, source_field, record_id")
    out = []
    for r in rows:
        if status and r["status"] != status:
            continue
        personal = is_personal(cat, idx.get(r["source_field"], {}))
        out.append(r | {"personal_data": personal,
                        "raw": mask(r["raw"], personal, may_view(sc, viewer, r["owner"]))})
    return out


def _quarantined(cat: Catalog, qid: str) -> dict:
    q = cat.one("SELECT * FROM quarantine WHERE qid = ?", (qid,))
    if q is None:
        raise Refused(f"quarantine entry '{qid}' not found")
    return q


def resolve_quarantine(cat: Catalog, sc: Scope | None, qid: str, actor: str, value: str | None = None,
                       unfixable: bool = False, spec_change: bool = False, reason: str | None = None) -> dict:
    q = _quarantined(cat, qid)
    if actor != q["owner"]:
        raise Refused(f"only the field owner '{q['owner']}' may resolve quarantine entries of {q['source_field']}")
    if q["status"] != "open":
        raise Refused(f"quarantine entry '{qid}' is {q['status']}, not open")
    if sum([value is not None, unfixable, spec_change]) != 1:
        raise Refused("choose exactly one of --value, --unfixable or --spec-change")
    status = "resolved" if value is not None else "unfixable" if unfixable else "spec_change_requested"
    ts = now()
    if value is not None:
        spec = spec_index(latest_spec(cat)["doc"]).get(q["source_field"], {})
        key = match_key(value, spec.get("parser") or {}) if spec.get("type") in ("name", "text") else None
        cat.execute("INSERT INTO normalized_value VALUES (" + ", ".join("?" * 18) + ")",
                    (new_id(), q["run_id"], q["source"], q["record_id"], q["source_field"], q["entity"],
                     q["attribute"], q["raw"], value, spec.get("parser", {}).get("target_unit"), None, key,
                     json.dumps(["manual"]), "manual", q["spec_version"], "manual", ts, None))
    cat.execute("UPDATE quarantine SET status = ?, resolution_value = ?, resolved_by = ?, resolved_at = ?"
                " WHERE qid = ?", (status, value, actor, ts, qid))
    log(cat, sc, "normalize.quarantine_resolve", qid, "open", {"status": status, "value": value}, actor, reason)
    return _quarantined(cat, qid)


def revert_quarantine(cat: Catalog, sc: Scope | None, qid: str, actor: str, reason: str | None = None) -> dict:
    q = _quarantined(cat, qid)
    if actor != q["owner"]:
        raise Refused(f"only the field owner '{q['owner']}' may revert quarantine resolutions of {q['source_field']}")
    if q["status"] == "open":
        raise Refused(f"quarantine entry '{qid}' is already open")
    cat.execute("UPDATE normalized_value SET withdrawn_at = ? WHERE run_id = ? AND source_field = ? AND record_id = ?"
                " AND origin = 'manual' AND withdrawn_at IS NULL", (now(), q["run_id"], q["source_field"],
                                                                    q["record_id"]))
    cat.execute("UPDATE quarantine SET status = 'open', resolution_value = NULL, resolved_by = NULL,"
                " resolved_at = NULL WHERE qid = ?", (qid,))
    log(cat, sc, "normalize.quarantine_revert", qid, {"status": q["status"], "value": q["resolution_value"]},
        "open", actor, reason)
    return _quarantined(cat, qid)


# ---------------------------------------------------------------- data contract (ODCS v3)

def odcs_contract(spec: dict) -> dict:
    """The spec set as an ODCS v3 data contract: one quality rule per field, checked by check_contract."""
    objects = defaultdict(list)
    for f in spec["doc"]["fields"]:
        obj, col = f["field"].split(".", 1)
        rule = {"type": "custom", "engine": "konmari", "dimension": "conformity",
                "description": "every value is canonical or quarantined with a reason", "mustBe": 0,
                "unit": "unaccounted values",
                "implementation": {"normalizer": f["type"], "parser": f.get("parser") or {},
                                   "canonicalPattern": CANONICAL.get(f["type"])}}
        objects[(f["source"], obj)].append({"name": col, "logicalType": LOGICAL.get(f["type"], "string"),
                                            "businessName": f"{f['entity']}.{f['attribute']}",
                                            "quality": [rule]})
    return {"apiVersion": "v3.0.2", "kind": "DataContract", "id": "konmari-normalization",
            "version": f"{spec['version']}.0.0", "status": "active", "name": "Normalization specs",
            "schema": [{"name": f"{s}.{o}", "physicalName": o, "logicalType": "object", "properties": props}
                       for (s, o), props in sorted(objects.items())]}


def check_contract(cat: Catalog, contract: dict, run_id: str) -> list[str]:
    """Run the contract's quality rules against one normalization run; returns failures (empty = pass)."""
    conf = conformance(cat, run_id)
    failures = []
    for obj in contract["schema"]:
        source = obj["name"].split(".", 1)[0]
        for prop in obj["properties"]:
            sf = f"{source}.{obj['physicalName']}.{prop['name']}"
            for rule in prop["quality"]:
                un = conf.get(sf, {}).get("unaccounted", 0)
                if un != rule["mustBe"]:
                    failures.append(f"{sf}: {un} unaccounted value(s)")
                if pat := rule["implementation"].get("canonicalPattern"):
                    bad = [r["canonical"] for r in cat.query(
                        "SELECT canonical FROM normalized_value WHERE run_id = ? AND source_field = ?"
                        " AND withdrawn_at IS NULL", (run_id, sf)) if not re.fullmatch(pat, r["canonical"])]
                    if bad:
                        failures.append(f"{sf}: {len(bad)} canonical value(s) do not match {pat}")
    return failures
