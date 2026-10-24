"""audit-logging (c07): hardens the one hash-chained `audit_event` trail of c01/c04. Payload-free agent events,
legacy adoption, verification with checkpoints, retention purge, Art. 15 export, OpenTelemetry correlation."""

import calendar
import csv
import io
import json
from contextlib import contextmanager
from datetime import datetime

from opentelemetry import trace

from konmari.catalog import Catalog, audit, event_hash, new_id, now, verify_chain
from konmari.governance import config
from konmari.versions import Refused

STEP = "govern"
# Payload content never enters the audit trail (design D7): prompts, completions, tool arguments, chunk text.
FORBIDDEN = {"prompt", "completion", "args", "arguments", "content", "text", "chunk_text", "query"}


class AuditUnavailable(RuntimeError):
    """The audit event could not be written; the tool call fails closed."""


def _check_payload(obj, path="details") -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k).lower() in FORBIDDEN:
                raise ValueError(f"audit event field '{path}.{k}' is payload content and is not allowed")
            _check_payload(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _check_payload(v, path)


def current_trace_id() -> str:
    ctx = trace.get_current_span().get_span_context()
    return f"{ctx.trace_id:032x}" if ctx.is_valid else new_id()


@contextmanager
def span(operation: str, name: str, **attrs):
    """OTel GenAI span (`invoke_agent` / `execute_tool`) with content capture disabled: IDs only, no payloads."""
    attributes = {"gen_ai.operation.name": operation, **{k: v for k, v in attrs.items() if v is not None}}
    with trace.get_tracer("konmari.governance").start_as_current_span(
            f"{operation} {name}", attributes=attributes, record_exception=False) as s:  # no tracebacks, no args
        yield s


def write_event(cat: Catalog, action: str, *, actor: str, result: str = "recorded", charter_id=None,
                step: str = STEP, trace_id: str | None = None, **details) -> str:
    """The governance writer: canonical JSON, hash-chained through catalog.audit, payload fields rejected."""
    _check_payload(details)
    details["trace_id"] = trace_id or current_trace_id()
    audit(cat, action, actor=actor, charter_id=charter_id, step=step, result=result, details=details)
    return details["trace_id"]


def adopt_legacy(cat: Catalog, actor: str = "konmari-audit") -> int:
    """Events written before the chain existed (seq NULL) join the chain by hash, without rewriting them."""
    adopted = set()
    for r in cat.query("SELECT details FROM audit_event WHERE action = 'audit.adopt' AND seq IS NOT NULL"):
        adopted |= set(json.loads(r["details"])["events"])
    todo = {r["id"]: event_hash("", r) for r in cat.query("SELECT * FROM audit_event WHERE seq IS NULL ORDER BY ts")
            if r["id"] not in adopted}
    if todo:
        write_event(cat, "audit.adopt", actor=actor, events=todo, count=len(todo))
    return len(todo)


def verify(cat: Catalog) -> dict | None:
    """`konmari audit verify`: adopt unchained legacy events, then check the whole chain (all steps)."""
    adopt_legacy(cat)
    return verify_chain(cat)


def months_ago(t: datetime, months: int) -> datetime:
    y, m = divmod(t.year * 12 + t.month - 1 - months, 12)
    return t.replace(year=y, month=m + 1, day=min(t.day, calendar.monthrange(y, m + 1)[1]))


def _raw(cat: Catalog, sql: str, params=()) -> None:
    """Retention deletes bypass the catalog's append-only guard; Postgres still checks the retention role."""
    cat.cursor().execute(sql.replace("?", "%s") if cat.dialect == "postgres" else sql, params)


def purge(cat: Catalog, cfg: dict, actor: str, charter: dict | None = None, at: datetime | None = None) -> dict:
    """Delete events older than the retention period (never the newest one) and write a checkpoint event."""
    if errs := config.validate(cfg, charter):
        raise Refused(errs)
    months = cfg["audit"]["retention_months"]
    cutoff = months_ago(at or now(), months)
    rows = cat.query("SELECT seq, ts, hash FROM audit_event WHERE seq IS NOT NULL ORDER BY seq")
    victims = []
    for r in rows[:-1]:
        if r["ts"] >= cutoff:
            break
        victims.append(r)
    legacy = [r["id"] for r in cat.query("SELECT id FROM audit_event WHERE seq IS NULL AND ts < ?", (cutoff,))]
    if not victims and not legacy:
        return {"deleted": 0, "cutoff": cutoff.isoformat()}
    with cat.transaction():
        if cat.dialect == "postgres":
            _raw(cat, "SET LOCAL ROLE konmari_retention")
        if victims:
            _raw(cat, "DELETE FROM audit_event WHERE seq <= ?", (victims[-1]["seq"],))
        if legacy:
            _raw(cat, "DELETE FROM audit_event WHERE seq IS NULL AND ts < ?", (cutoff,))
    out = {"from_seq": victims[0]["seq"] if victims else None, "to_seq": victims[-1]["seq"] if victims else None,
           "count": len(victims) + len(legacy), "last_hash": victims[-1]["hash"] if victims else None,
           "legacy_ids": legacy, "cutoff": cutoff.isoformat(), "retention_months": months}
    write_event(cat, "audit.checkpoint", actor=actor, **out)
    return {"deleted": out["count"], **out}


EXPORT_COLS = ("id", "ts", "seq", "actor", "action", "step", "result", "charter_id", "hash", "details")


def export(cat: Catalog, actor: str, subject: str | None = None, start: datetime | None = None,
           end: datetime | None = None, fmt: str = "jsonl") -> tuple[str, int]:
    """Events of one data subject (on-behalf-of user, actor or pseudonym token) or of a time range; audited."""
    if not subject and not (start or end):
        raise Refused("give --subject or --from/--to")
    rows = []
    for r in cat.query("SELECT * FROM audit_event ORDER BY ts, seq"):
        d = json.loads(r["details"] or "{}")
        if subject and subject not in (r["actor"], d.get("on_behalf_of"), d.get("token")):
            continue
        if (start and r["ts"] < start) or (end and r["ts"] > end):
            continue
        rows.append({k: (r[k].isoformat() if isinstance(r[k], datetime) else r[k]) for k in EXPORT_COLS})
    if fmt == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, EXPORT_COLS)
        w.writeheader()
        w.writerows(rows)
        text = buf.getvalue()
    else:
        text = "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in rows)
    write_event(cat, "audit.export", actor=actor, subject=subject, start=start and start.isoformat(),
                end=end and end.isoformat(), format=fmt, events=len(rows))
    return text, len(rows)


def heartbeat(cat: Catalog, actor: str) -> bool:
    """Audit logging is active: a write succeeds and the chain verifies."""
    try:
        write_event(cat, "audit.heartbeat", actor=actor)
    except Exception:
        return False
    return verify_chain(cat) is None
