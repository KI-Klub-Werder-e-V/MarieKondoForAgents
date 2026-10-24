"""Source inventory in the catalog: registration, scans, records, completeness gate, tiers, report, DCAT export."""

import csv
import io
import json
import re
from datetime import datetime
from itertools import islice
from pathlib import Path

import yaml

from konmari.catalog import Catalog, audit_change, new_id, now
from konmari.charter.store import Scope
from konmari.inventory import lineage, sensitivity
from konmari.inventory.connectors import Asset, connector_for
from konmari.versions import Refused

STEP = "inventory"
CONNECTORS = ["sharepoint", "sql", "fileshare", "csv-import"]
FREQ_WORDS = {"continuous", "irregular", "unknown"}
ISO_DURATION = re.compile(r"^P(?!$)(\d+Y)?(\d+M)?(\d+W)?(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+S)?)?$")
# Words that mark a team, role or shared mailbox instead of one person (design decision 7).
GROUP_WORDS = {"team", "group", "gruppe", "info", "office", "buero", "büro", "support", "admin", "kontakt", "contact",
               "sales", "service", "noreply", "no-reply", "mail", "list", "liste", "verteiler", "all", "alle", "hr",
               "it", "vorstand", "board", "geschaeftsstelle", "datenschutz", "postmaster", "helpdesk"}
COLS = ["asset_uri", "source_id", "charter_id", "system_type", "origin", "title", "path", "owner", "steward",
        "update_frequency", "last_modified", "format", "size", "sensitivity_tier", "tier_state",
        "personal_data_categories", "ropa_ref", "ropa_missing", "access_rights_note", "content_hash", "schema_hash",
        "schema_fields", "first_seen", "last_seen", "status", "missing_fields", "previous_status", "missing_since",
        "waiver"]
REPORT_COLS = ["asset_uri", "source_id", "system_type", "origin", "title", "owner", "steward", "update_frequency",
               "last_modified", "format", "size", "sensitivity_tier", "tier_state", "personal_data_categories",
               "ropa_ref", "access_rights_note", "first_seen", "last_seen", "status", "missing_fields", "missing_since"]

# DCAT 3 / Dublin Core vocabulary for the inventory fields (design decision 1).
DCAT = {
    "title": "dcterms:title",
    "owner": "dcterms:publisher",
    "steward": "dcat:contactPoint",
    "update_frequency": "dcterms:accrualPeriodicity",
    "last_modified": "dcterms:modified",
    "format": "dcterms:format",
    "access_rights_note": "dcterms:accessRights",
    "first_seen": "dcterms:issued",
    "size": "konmari:size",
    "system_type": "konmari:systemType",
    "sensitivity_tier": "konmari:sensitivityTier",
    "personal_data_categories": "konmari:personalDataCategories",
    "ropa_ref": "konmari:ropaRef",
    "status": "konmari:inventoryStatus",
    "path": "dcat:landingPage",
}
JSONLD_CONTEXT = {"dcat": "http://www.w3.org/ns/dcat#", "dcterms": "http://purl.org/dc/terms/",
                  "konmari": "https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten/ns#"}


def valid_frequency(v) -> bool:
    return v in FREQ_WORDS or bool(isinstance(v, str) and ISO_DURATION.match(v))


def approvers(sc: Scope) -> set[str]:
    return {c["approver"] for c in sc.categories.values()}


def is_named_individual(owner: str, stakeholders: dict) -> bool:
    if owner in stakeholders:
        return True
    if re.search(r"[,;&/+]| und | and ", owner):
        return False  # more than one person
    local = owner.split("@")[0].lower()
    return not set(re.split(r"[.\-_ ]+", local)) & GROUP_WORDS


def missing_fields(rec: dict, stakeholders: dict) -> list[dict]:
    out = []
    owner = (rec.get("owner") or "").strip()
    if owner in ("", "unknown"):
        out.append({"field": "owner", "reason": "owner is not set"})
    elif not is_named_individual(owner, stakeholders):
        out.append({"field": "owner", "reason": "owner must be a named individual"})
    for f in ("update_frequency", "format"):
        if rec.get(f) in (None, "", "unknown"):
            out.append({"field": f, "reason": f"{f} is not set"})
    if rec.get("sensitivity_tier") in (None, "", "unknown"):
        out.append({"field": "sensitivity_tier", "reason": "sensitivity_tier is not set"})
    elif rec.get("tier_state") != "confirmed":
        out.append({"field": "sensitivity_tier", "reason": "proposed tier is not confirmed by the owner or compliance"})
    return out


def _refresh(rec: dict, sc: Scope) -> dict:
    miss = missing_fields(rec, sc.stakeholders)
    rec["missing_fields"] = json.dumps(miss)
    if not rec.get("missing_since"):
        rec["status"] = "incomplete" if miss else "complete"
    return rec


def _save(cat: Catalog, rec: dict, new: bool) -> None:
    if new:
        cat.execute(f"INSERT INTO source_asset ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})",
                    tuple(rec[c] for c in COLS))
    else:
        cat.execute(f"UPDATE source_asset SET {', '.join(f'{c} = ?' for c in COLS[1:])} WHERE asset_uri = ?",
                    tuple(rec[c] for c in COLS[1:]) + (rec["asset_uri"],))


def get(cat: Catalog, uri: str) -> dict:
    rec = cat.one("SELECT * FROM source_asset WHERE asset_uri = ?", (uri,))
    if rec is None:
        raise Refused(f"no inventory record for '{uri}'")
    return rec


def records(cat: Catalog, charter_id: str) -> list[dict]:
    return cat.query("SELECT * FROM source_asset WHERE charter_id = ? ORDER BY asset_uri", (charter_id,))


def _emit(transport, rec: dict) -> None:
    if transport is not None:
        transport.emit(lineage.dataset_event(rec))


# ---------------------------------------------------------------- sources

def _same_locator(a: str, b: str, connector: str) -> bool:
    if a.rstrip("/\\") == b.rstrip("/\\"):
        return True
    local = connector in ("fileshare", "csv-import") and not a.startswith(("smb:", "\\\\"))
    return local and Path(a).resolve() == Path(b).resolve()


def register_source(cat: Catalog, sc: Scope, connector: str, locator: str, actor: str, secret_ref: str | None = None,
                    title: str | None = None) -> dict:
    if connector not in CONNECTORS:
        raise Refused(f"unknown connector '{connector}'; valid: {', '.join(CONNECTORS)}")
    if secret_ref and secret_ref.split(":")[0] not in ("env", "file"):
        raise Refused("unsupported secret reference; use env:NAME or file:PATH (the value is not shown)")
    match = [s for s in sc.sources if s["connector"] == connector and _same_locator(s["locator"], locator, connector)]
    if not match:
        audit_change(cat, "inventory.source.add", f"{connector}:{locator}", None, None, actor=actor,
                     charter_id=sc.charter_id, step=STEP, result="refused")
        raise Refused(f"source {connector} '{locator}' is not listed in approved charter '{sc.charter_id}' "
                      f"version {sc.charter_version} (data_scope.sources); amend the charter first")
    sid = match[0]["id"]
    row = {"source_id": sid, "charter_id": sc.charter_id, "charter_version": sc.charter_version,
           "connector": connector, "locator": locator, "credential_ref": secret_ref, "title": title or sid}
    old = cat.one("SELECT * FROM inventory_source WHERE source_id = ?", (sid,))
    if old:
        cat.execute("UPDATE inventory_source SET charter_version = ?, locator = ?, credential_ref = ?, title = ?"
                    " WHERE source_id = ?", (sc.charter_version, locator, secret_ref, row["title"], sid))
        prev = {k: old[k] for k in ("locator", "credential_ref", "title", "charter_version")}
        audit_change(cat, "inventory.source.update", sid, prev, {k: row[k] for k in prev}, actor=actor,
                     charter_id=sc.charter_id, step=STEP)
    else:
        cat.execute("INSERT INTO inventory_source (source_id, charter_id, charter_version, connector, locator,"
                    " credential_ref, title, registered_at, registered_by, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (sid, sc.charter_id, sc.charter_version, connector, locator, secret_ref, row["title"], now(),
                     actor, "{}"))
        audit_change(cat, "inventory.source.add", sid, None, row, actor=actor, charter_id=sc.charter_id, step=STEP)
    return row


def sources(cat: Catalog, charter_id: str) -> list[dict]:
    return cat.query("SELECT * FROM inventory_source WHERE charter_id = ? ORDER BY source_id", (charter_id,))


# ---------------------------------------------------------------- scans

def _apply_hints(cat: Catalog, rec: dict, hints: dict, sample_size: int, actor, sc: Scope) -> None:
    cat.execute("DELETE FROM sensitivity_hint WHERE asset_uri = ?", (rec["asset_uri"],))
    for et, h in sorted(hints.items()):
        cat.execute("INSERT INTO sensitivity_hint VALUES (?, ?, ?, ?, ?, ?)",
                    (rec["asset_uri"], et, h["count"], h["max_score"], sample_size, now()))
    floor = sensitivity.hint_tier(hints) or "internal"  # no hints: propose internal, a human still confirms
    pdc = sorted(set(json.loads(rec["personal_data_categories"] or "[]")) | set(sensitivity.categories(hints)))
    rec["personal_data_categories"] = json.dumps(pdc)
    if rec["tier_state"] == "confirmed" and sensitivity.rank(floor) <= sensitivity.rank(rec["sensitivity_tier"]):
        return
    if (rec["sensitivity_tier"], rec["tier_state"]) != (floor, "proposed"):
        prev = {"tier": rec["sensitivity_tier"], "state": rec["tier_state"]}
        rec["sensitivity_tier"], rec["tier_state"] = floor, "proposed"
        audit_change(cat, "inventory.tier.propose", rec["asset_uri"], prev, {"tier": floor, "state": "proposed"},
                     actor=actor, reason=f"hints: {', '.join(sorted(hints)) or 'none'}", charter_id=sc.charter_id,
                     step=STEP)


def apply_assets(cat: Catalog, sc: Scope, src: dict, assets: list[Asset], actor, origin="scan",
                 sample_size=sensitivity.DEFAULT_SAMPLE, threshold=sensitivity.DEFAULT_THRESHOLD,
                 transport=None) -> dict:
    """Upsert assets by asset_uri; mark assets of this source that are no longer present as missing."""
    t = now()
    known = {r["asset_uri"]: r for r in cat.query("SELECT * FROM source_asset WHERE source_id = ?", (src["source_id"],))}
    counts = {"created": 0, "updated": 0, "unchanged": 0, "missing": 0, "restored": 0}
    for a in assets:
        rec = known.pop(a.uri, None)
        new, restored = rec is None, bool(rec and rec["missing_since"])
        if new:
            rec = {c: None for c in COLS} | {
                "asset_uri": a.uri, "source_id": src["source_id"], "charter_id": sc.charter_id, "origin": origin,
                "update_frequency": "unknown", "personal_data_categories": "[]", "ropa_missing": False,
                "first_seen": t}
        rec |= {"title": a.title, "system_type": a.system_type, "path": a.path, "format": a.format, "size": a.size,
                "last_modified": a.last_modified, "content_hash": a.content_hash, "schema_hash": a.schema_hash,
                "schema_fields": json.dumps(a.fields) if a.fields else None, "last_seen": t, "missing_since": None}
        for k, v in a.meta.items():
            if v is not None:
                rec[k] = v
        if a.changed and a.samples and sample_size > 0:
            hints = sensitivity.analyze(islice(a.samples(sample_size), sample_size), threshold)
            _apply_hints(cat, rec, hints, sample_size, actor, sc)
        _refresh(rec, sc)  # a reappearing asset gets back the status it had before it went missing
        _save(cat, rec, new)
        kind = "created" if new else "restored" if restored else "updated" if a.changed else "unchanged"
        counts[kind] += 1
        if new:
            audit_change(cat, "inventory.asset.create", a.uri, None, {"status": rec["status"], "origin": origin},
                         actor=actor, charter_id=sc.charter_id, step=STEP)
        elif restored:
            audit_change(cat, "inventory.asset.restore", a.uri, "missing", rec["status"], actor=actor,
                         charter_id=sc.charter_id, step=STEP)
        if new or a.changed or restored:
            _emit(transport, rec)
    for rec in known.values():
        if rec["missing_since"]:
            continue
        rec |= {"previous_status": rec["status"], "status": "missing", "missing_since": t}
        _save(cat, rec, False)
        counts["missing"] += 1
        audit_change(cat, "inventory.asset.missing", rec["asset_uri"], rec["previous_status"], "missing", actor=actor,
                     charter_id=sc.charter_id, step=STEP)
    return counts


def scan(cat: Catalog, sc: Scope, source_id: str | None = None, *, actor, sample_size=sensitivity.DEFAULT_SAMPLE,
         threshold=sensitivity.DEFAULT_THRESHOLD, http=None, transport=None) -> dict[str, dict]:
    srcs = [s for s in sources(cat, sc.charter_id) if s["connector"] != "csv-import"
            and (source_id is None or s["source_id"] == source_id)]
    if source_id and not srcs:
        raise Refused(f"source '{source_id}' is not registered as a scannable source")
    out = {}
    for src in srcs:
        run = new_id()
        cat.execute("INSERT INTO inventory_run (run_id, source_id, started_at, actor, sample_size, status)"
                    " VALUES (?, ?, ?, ?, ?, 'running')", (run, src["source_id"], now(), actor, sample_size))
        try:
            assets, state = connector_for(src, http).scan(json.loads(src["state"] or "{}"))
            counts = apply_assets(cat, sc, src, assets, actor, "scan", sample_size, threshold, transport)
        except Exception:
            cat.execute("UPDATE inventory_run SET status = 'failed', finished_at = ? WHERE run_id = ?", (now(), run))
            raise
        cat.execute("UPDATE inventory_source SET state = ? WHERE source_id = ?", (json.dumps(state), src["source_id"]))
        cat.execute("UPDATE inventory_run SET status = 'done', finished_at = ?, assets_seen = ?, assets_missing = ?"
                    " WHERE run_id = ?", (now(), len(assets), counts["missing"], run))
        audit_change(cat, "inventory.scan", src["source_id"], None, counts, actor=actor, charter_id=sc.charter_id,
                     step=STEP, run_id=run, sample_size=sample_size)
        out[src["source_id"]] = counts
    return out


# ---------------------------------------------------------------- manual import

_IMPORT_FIELDS = ["asset_uri", "title", "system_type", "owner", "steward", "update_frequency", "last_modified",
                  "format", "size", "sensitivity_tier", "personal_data_categories", "ropa_ref", "access_rights_note"]


def _parse_row(row: dict) -> tuple[Asset | None, list[str]]:
    row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
    row = {k: (None if v in ("", None) else v) for k, v in row.items()}
    errs = [f"unknown column '{k}'" for k in row if k not in _IMPORT_FIELDS]
    for f in ("asset_uri", "title"):
        if not row.get(f):
            errs.append(f"missing required '{f}'")
    freq = row.get("update_frequency") or "unknown"
    if not valid_frequency(str(freq)):
        errs.append(f"invalid update_frequency '{freq}' (ISO 8601 duration such as P1W, or continuous, irregular, "
                    "unknown)")
    tier = row.get("sensitivity_tier")
    if tier not in (None, "unknown") and tier not in sensitivity.TIERS:
        errs.append(f"invalid sensitivity_tier '{tier}' ({', '.join(sensitivity.TIERS)})")
    lm = row.get("last_modified")
    try:
        lm = datetime.fromisoformat(str(lm)) if lm else None
    except ValueError:
        errs.append(f"invalid last_modified '{lm}' (ISO date)")
    size = row.get("size")
    try:
        size = int(size) if size is not None else None
    except ValueError:
        errs.append(f"invalid size '{size}' (whole number)")
    if errs:
        return None, errs
    pdc = row.get("personal_data_categories")
    pdc = pdc if isinstance(pdc, list) else [p.strip() for p in (pdc or "").split(";") if p.strip()]
    tier = None if tier == "unknown" else tier
    meta = {"owner": row.get("owner"), "steward": row.get("steward"), "update_frequency": str(freq),
            "sensitivity_tier": tier, "tier_state": "confirmed" if tier else None,  # filled in by the responsible humans
            "personal_data_categories": json.dumps(pdc), "ropa_ref": row.get("ropa_ref"),
            "access_rights_note": row.get("access_rights_note")}
    return Asset(uri=row["asset_uri"], title=str(row["title"]), system_type=row.get("system_type") or "manual",
                 format=row.get("format"), size=size, last_modified=lm, changed=False, meta=meta), []


def parse_import(path: str | Path) -> tuple[list[Asset], list[str]]:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    assets, errors = [], []
    if p.suffix.lower() in (".yaml", ".yml"):
        doc = yaml.safe_load(text) or []
        rows = [(f"record {i}", r) for i, r in enumerate(doc if isinstance(doc, list) else [], 1)]
        if not isinstance(doc, list):
            errors.append(f"{p}: expected a YAML list of records")
    else:
        reader = csv.DictReader(io.StringIO(text))
        rows = [(f"line {reader.line_num}", r) for r in reader]
    for where, row in rows:
        a, errs = _parse_row(row) if isinstance(row, dict) else (None, ["not a mapping"])
        if a:
            assets.append(a)
        errors += [f"{where}: {e}" for e in errs]
    return assets, errors


def import_file(cat: Catalog, sc: Scope, path: str, actor: str, transport=None) -> tuple[dict, list[str]]:
    src = next((s for s in sources(cat, sc.charter_id)
                if s["connector"] == "csv-import" and _same_locator(s["locator"], path, "csv-import")), None)
    if src is None:
        raise Refused(f"'{path}' is not a registered csv-import source; run "
                      f"'konmari inventory source add --connector csv-import --locator {path}' first")
    assets, errors = parse_import(path)
    counts = apply_assets(cat, sc, src, assets, actor, origin="manual", sample_size=0, transport=transport)
    audit_change(cat, "inventory.import", src["source_id"], None, counts, actor=actor, charter_id=sc.charter_id,
                 step=STEP, errors=errors)
    return counts, errors


# ---------------------------------------------------------------- human decisions

SETTABLE = {"owner", "steward", "update_frequency", "format", "ropa_ref", "ropa_missing", "access_rights_note",
            "personal_data_categories"}


def set_fields(cat: Catalog, sc: Scope, uri: str, changes: dict, actor: str, reason: str | None = None,
               transport=None) -> dict:
    changes = {k: v for k, v in changes.items() if v is not None}
    if not changes or set(changes) - SETTABLE:
        raise Refused(f"nothing to set; settable fields: {', '.join(sorted(SETTABLE))}")
    if "update_frequency" in changes and not valid_frequency(changes["update_frequency"]):
        raise Refused(f"invalid update_frequency '{changes['update_frequency']}' (ISO 8601 duration such as P1W, "
                      "or continuous, irregular, unknown)")
    if "personal_data_categories" in changes:
        changes["personal_data_categories"] = json.dumps(sorted(changes["personal_data_categories"]))
    rec = get(cat, uri)
    prev = {k: rec[k] for k in changes}
    rec |= changes
    _save(cat, _refresh(rec, sc), False)
    audit_change(cat, "inventory.asset.set", uri, prev, changes, actor=actor, reason=reason,
                 charter_id=sc.charter_id, step=STEP)
    if "owner" in changes:
        _emit(transport, rec)
    return rec


def set_tier(cat: Catalog, sc: Scope, uri: str, actor: str, tier: str | None = None, reason: str | None = None) -> dict:
    rec = get(cat, uri)
    person = sc.stakeholders.get(actor, {})
    if "compliance" not in person.get("roles", []) and rec["owner"] not in (actor, person.get("name")):
        raise Refused(f"'{actor}' is neither the owner of '{uri}' nor a compliance stakeholder")
    if tier is None:
        if not rec["sensitivity_tier"]:
            raise Refused(f"'{uri}' has no proposed tier to confirm; set one with --set TIER --reason ...")
        tier, action = rec["sensitivity_tier"], "inventory.tier.confirm"
    else:
        if tier not in sensitivity.TIERS:
            raise Refused(f"invalid tier '{tier}'; valid: {', '.join(sensitivity.TIERS)}")
        if not (reason or "").strip():
            raise Refused("a tier override needs a reason (--reason)")
        action = "inventory.tier.override"
    prev = {"tier": rec["sensitivity_tier"], "state": rec["tier_state"]}
    rec |= {"sensitivity_tier": tier, "tier_state": "confirmed"}
    _save(cat, _refresh(rec, sc), False)
    audit_change(cat, action, uri, prev, {"tier": tier, "state": "confirmed"}, actor=actor, reason=reason,
                 charter_id=sc.charter_id, step=STEP)
    return rec


def waive(cat: Catalog, sc: Scope, uri: str, actor: str, reason: str) -> dict:
    if actor not in approvers(sc):
        raise Refused(f"'{actor}' is not a category approver in charter '{sc.charter_id}'")
    if not (reason or "").strip():
        raise Refused("a waiver needs a reason (--reason)")
    rec = get(cat, uri)
    w = {"by": actor, "reason": reason, "at": now().isoformat()}
    prev = rec["waiver"]
    rec["waiver"] = json.dumps(w)
    _save(cat, rec, False)
    audit_change(cat, "inventory.waive", uri, json.loads(prev) if prev else None, w, actor=actor, reason=reason,
                 charter_id=sc.charter_id, step=STEP)
    return rec


# ---------------------------------------------------------------- gate, report, export

def gate_blockers(cat: Catalog, charter_id: str) -> list[dict]:
    """In-scope records that block the handoff: incomplete and not waived."""
    return [r for r in records(cat, charter_id) if r["status"] == "incomplete" and not r["waiver"]]


def ropa_gaps(recs: list[dict]) -> list[dict]:
    return [r for r in recs if r["status"] != "missing" and r["sensitivity_tier"] in ("personal", "special-category")
            and not r["ropa_ref"]]


def summary(cat: Catalog, charter_id: str) -> dict:
    recs = records(cat, charter_id)
    present = [r for r in recs if r["status"] != "missing"]
    by = {s: [r for r in recs if r["status"] == s] for s in ("complete", "incomplete", "missing")}
    tiers = {t: sum(r["sensitivity_tier"] == t for r in present) for t in [*sensitivity.TIERS, None]}
    pct = round(100 * len(by["complete"]) / len(present)) if present else 0
    run = cat.one("SELECT sample_size FROM inventory_run r JOIN inventory_source s ON s.source_id = r.source_id"
                  " WHERE s.charter_id = ? ORDER BY r.started_at DESC LIMIT 1", (charter_id,))
    return {"records": recs, "by_status": by, "tiers": tiers, "completeness_pct": pct, "ropa_gaps": ropa_gaps(recs),
            "sample_size": run["sample_size"] if run else None}


def render_md(charter_id: str, s: dict) -> str:
    by = s["by_status"]
    lines = [f"# Inventory report: {charter_id}", "",
             f"Records: {len(s['records'])} (complete {len(by['complete'])}, incomplete {len(by['incomplete'])}, "
             f"missing {len(by['missing'])})", f"Completeness: {s['completeness_pct']} %"]
    if s["sample_size"]:
        lines.append(f"Sensitivity hints: sample of up to {s['sample_size']} items or rows per asset; tiers are "
                     "confirmed by humans and feed the RoPA/DPIA process, they do not replace it.")
    for status in ("complete", "incomplete"):
        lines += ["", f"## {status.capitalize()} records", ""]
        for r in by[status]:
            extra = ""
            if status == "incomplete":
                extra = "; missing: " + ", ".join(f"{m['field']} ({m['reason']})" for m in json.loads(r["missing_fields"]))
                extra += f"; waived by {json.loads(r['waiver'])['by']}" if r["waiver"] else ""
            lines.append(f"- {r['asset_uri']} ({r['title']}), owner: {r['owner'] or 'unknown'}{extra}")
        lines += [] if by[status] else ["none"]
    lines += ["", "## Sensitivity tiers", "", "| tier | records |", "|---|---|"]
    lines += [f"| {t or 'not set'} | {n} |" for t, n in s["tiers"].items()]
    lines += ["", "## RoPA gaps for compliance", ""]
    lines += [f"- {r['asset_uri']} ({r['title']}), tier {r['sensitivity_tier']}, owner: {r['owner'] or 'unknown'}"
              + (" (flagged ropa_missing)" if r["ropa_missing"] else "") for r in s["ropa_gaps"]] or ["none"]
    lines += ["", "## Missing assets", ""]
    lines += [f"- {r['asset_uri']} ({r['title']}), missing since {r['missing_since']:%Y-%m-%d %H:%M}"
              for r in by["missing"]] or ["none"]
    return "\n".join(lines) + "\n"


def write_csv(path: str | Path, recs: list[dict]) -> None:
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REPORT_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(recs)


def dcat_jsonld(recs: list[dict], categories: dict[str, str] | None = None) -> dict:
    """DCAT 3 JSON-LD catalog of the inventory records; `categories` maps asset_uri to its primary category."""
    graph = []
    for r in recs:
        d = {"@id": r["asset_uri"], "@type": "dcat:Dataset"}
        for col, term in DCAT.items():
            v = r.get(col)
            if v is None or v == "":
                continue
            if col == "personal_data_categories":
                v = json.loads(v)
            elif isinstance(v, datetime):
                v = v.isoformat()
            d[term] = v
        if categories and r["asset_uri"] in categories:
            d["dcat:theme"] = categories[r["asset_uri"]]
        graph.append(d)
    return {"@context": JSONLD_CONTEXT, "@graph": graph}
