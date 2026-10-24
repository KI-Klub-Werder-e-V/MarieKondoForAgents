"""Conflict register: detection from golden-record builds, owner resolution with write-back ticket or accepted
deviation, reopening on recurrence, personal-data flag and overdue report (DSGVO Art. 5(1)(d), Art. 16)."""

import csv
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from konmari.catalog import Catalog, now
from konmari.charter.store import Scope
from konmari.normalize.store import is_personal, latest_run_id, latest_spec, spec_index
from konmari.policy.store import DUE_DAYS
from konmari.standardize import log, mask, may_view, metric, notify
from konmari.versions import Refused

OPEN = ("open", "reopened")


def _row(r):
    if r:
        r["candidates"] = json.loads(r["candidates"])
    return r


def entries(cat: Catalog, entity: str | None = None) -> list[dict]:
    rows = cat.query("SELECT * FROM conflict ORDER BY detected_at, conflict_id")
    return [_row(r) for r in rows if entity in (None, r["entity"])]


def get(cat: Catalog, conflict_id: str) -> dict:
    r = _row(cat.one("SELECT * FROM conflict WHERE conflict_id = ?", (conflict_id,)))
    if r is None:
        raise Refused(f"conflict '{conflict_id}' not found")
    return r


def _cands(r: dict) -> str:
    # only the candidate values the decision needs, never the whole source record (data minimisation)
    return json.dumps([{"source": c["source"], "record_id": c["record_id"], "value": c["value"], "unit": c["unit"]}
                       for c in r["candidates"]], ensure_ascii=False)


def sync(cat: Catalog, sc: Scope | None, entity: str, results: list[dict], doc: dict, actor: str) -> None:
    existing = {(c["cluster_id"], c["field"]): c for c in entries(cat, entity)}
    spec = latest_spec(cat)
    idx = spec_index(spec["doc"]) if spec else {}
    run_id, ts = latest_run_id(cat), now()
    for r in results:
        key, c = (r["cluster_id"], r["field"]), existing.get((r["cluster_id"], r["field"]))
        if not r["disagree"]:
            if c and c["status"] in OPEN:
                cat.execute("UPDATE conflict SET status = 'resolved', reason = ?, approver = 'konmari', decided_at = ?,"
                            " prev_status = status WHERE conflict_id = ?",
                            ("candidate values agree after normalization", ts, c["conflict_id"]))
                log(cat, sc, "conflicts.close", c["conflict_id"], c["status"], "resolved", actor,
                    "candidate values agree after normalization")
            continue
        status = "overridden" if r["winner"] is not None else "open"
        personal = any(is_personal(cat, idx.get(x["source_field"], {})) for x in r["candidates"])
        if c is None:
            cid = "K-" + hashlib.sha256(f"{entity}|{key[0]}|{key[1]}".encode()).hexdigest()[:10]
            due = date.today() + timedelta(days=int(doc.get("conflict_due_days", DUE_DAYS)))
            cat.execute("INSERT INTO conflict VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL,"
                        " NULL, NULL)", (cid, entity, *key, _cands(r), ts, r["rule"]["owner"], due, status, personal))
            if status == "open":
                log(cat, sc, "conflicts.detect", cid, None, "open", actor, field=f"{entity}.{key[1]}",
                    cluster_id=key[0], personal_data=personal)
            continue
        new = c["status"]
        if status == "overridden":
            new = "overridden" if c["status"] in OPEN else c["status"]  # the active rule decides it now
        elif c["status"] == "overridden":
            new = "open"
        elif c["status"] == "resolved" and c["decided_run"] != run_id:
            new = "reopened"  # the source still disagrees after a later normalization run
        cat.execute("UPDATE conflict SET candidates = ?, status = ?, personal_data = ? WHERE conflict_id = ?",
                    (_cands(r), new, personal, c["conflict_id"]))
        if new == "reopened" and c["status"] != "reopened":
            log(cat, sc, "conflicts.reopen", c["conflict_id"], c["status"], new, actor,
                "the disagreement recurred in a later run; the source was not corrected", ticket=c["ticket"])
            notify(c["owner"], f"conflict {c['conflict_id']} reopened: {entity}.{key[1]} still disagrees at the source",
                   conflict_id=c["conflict_id"], ticket=c["ticket"])


def listing(cat: Catalog, sc: Scope | None, viewer: str | None, status: str | None = None) -> list[dict]:
    """Register view; candidate values of personal-data conflicts are masked for viewers without access."""
    out = []
    for c in entries(cat):
        if status and c["status"] != status:
            continue
        allowed = may_view(sc, viewer, c["owner"])
        c["candidates"] = [x | {"value": mask(x["value"], c["personal_data"], allowed)} for x in c["candidates"]]
        c["decision"] = mask(c["decision"], c["personal_data"], allowed)
        out.append(c)
    return out


def _golden_value(cat: Catalog, c: dict):
    g = cat.one("SELECT value, status, origin FROM golden_attribute WHERE entity = ? AND cluster_id = ? AND field = ?",
                (c["entity"], c["cluster_id"], c["field"]))
    return g and {"value": g["value"], "status": g["status"], "origin": g["origin"]}


def decide(cat: Catalog, sc: Scope | None, conflict_id: str, actor: str, source: str | None = None,
           value: str | None = None, ticket: str | None = None, accept_reason: str | None = None) -> dict:
    """Resolve (with a write-back ticket for the source) or accept the deviation (with a reason)."""
    from konmari.policy import golden

    c = get(cat, conflict_id)
    if actor != c["owner"]:
        raise Refused(f"only the field owner '{c['owner']}' may decide conflict {conflict_id}")
    if c["status"] not in OPEN:
        raise Refused(f"conflict {conflict_id} is {c['status']}; only open or reopened conflicts are decided")
    if (source is None) == (value is None):
        raise Refused("choose a candidate with --source or enter a corrected value with --value")
    if source is not None:
        pick = next((x for x in c["candidates"] if x["source"] == source), None)
        if pick is None:
            raise Refused(f"no candidate from source '{source}' (candidates: "
                          f"{', '.join(x['source'] for x in c['candidates'])})")
        value = pick["value"]
    accepted = accept_reason is not None
    if not accepted and not ticket:
        raise Refused("state how the source gets corrected (--ticket <write-back ticket>) or accept the deviation")
    before = _golden_value(cat, c)
    status = "accepted_deviation" if accepted else "resolved"
    cat.execute("UPDATE conflict SET status = ?, decision = ?, decision_src = ?, ticket = ?, reason = ?, approver = ?,"
                " decided_at = ?, decided_run = ?, prev_status = ? WHERE conflict_id = ?",
                (status, value, source or "manual", ticket, accept_reason, actor, now(), latest_run_id(cat),
                 c["status"], conflict_id))
    golden.rebuild_if_ready(cat, sc, c["entity"], actor)
    log(cat, sc, f"conflicts.{'accept' if accepted else 'resolve'}", conflict_id, before, _golden_value(cat, c), actor,
        accept_reason or f"write-back ticket {ticket}", ticket=ticket, status=status)
    return get(cat, conflict_id)


def revert(cat: Catalog, sc: Scope | None, conflict_id: str, actor: str, reason: str | None = None) -> dict:
    from konmari.policy import golden

    c = get(cat, conflict_id)
    if actor != c["owner"]:
        raise Refused(f"only the field owner '{c['owner']}' may revert conflict {conflict_id}")
    if c["status"] not in ("resolved", "accepted_deviation"):
        raise Refused(f"conflict {conflict_id} is {c['status']}; nothing to revert")
    before = _golden_value(cat, c)
    back = c["prev_status"] if c["prev_status"] in OPEN else "open"
    cat.execute("UPDATE conflict SET status = ?, decision = NULL, decision_src = NULL, ticket = NULL, reason = NULL,"
                " approver = NULL, decided_at = NULL, decided_run = NULL, prev_status = ? WHERE conflict_id = ?",
                (back, c["status"], conflict_id))
    golden.rebuild_if_ready(cat, sc, c["entity"], actor)
    log(cat, sc, "conflicts.revert", conflict_id, before, _golden_value(cat, c), actor, reason,
        previous_status=c["status"], ticket=c["ticket"])
    return get(cat, conflict_id)


def overdue(cat: Catalog, today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for c in entries(cat):
        due = c["due_date"] if isinstance(c["due_date"], date) else date.fromisoformat(str(c["due_date"]))
        if c["status"] in OPEN and due < today:
            out.append(c | {"age_days": (today - c["detected_at"].date()).days, "due": due})
    return out


def record_metrics(cat: Catalog, entity: str) -> None:
    rows = [c for c in entries(cat, entity)]
    metric(cat, "conflicts", "open", sum(c["status"] in OPEN for c in rows), entity=entity)
    late = [c for c in overdue(cat) if c["entity"] == entity]
    metric(cat, "conflicts", "overdue", len(late), entity=entity)
    metric(cat, "conflicts", "overdue_personal_data", sum(c["personal_data"] for c in late), entity=entity)


def export_tickets(cat: Catalog, path: str | Path) -> int:
    """Write-back list (CSV) of resolved conflicts with their ticket; no candidate values (data minimisation)."""
    rows = [c for c in entries(cat) if c["status"] == "resolved" and c["ticket"]]
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["conflict_id", "entity", "cluster_id", "field", "ticket", "owner", "decision_source", "decided_at"])
        for c in rows:
            w.writerow([c["conflict_id"], c["entity"], c["cluster_id"], c["field"], c["ticket"], c["owner"],
                        c["decision_src"], c["decided_at"]])
    return len(rows)
