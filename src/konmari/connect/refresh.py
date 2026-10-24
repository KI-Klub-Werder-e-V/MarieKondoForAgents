"""Scheduled refresh with change tracking (design D4): cursor-based incremental merge from each source table,
deletes by key reconciliation and retention tombstones (c04), contract tests on the staged rows, then one
transaction that swaps the changes in. A failed test or error keeps the previous rows and `as_of`.
shortcut: staging is in memory and the merge is our own (no dlt), fine for pilot-sized products; switch to dlt
staging tables when a product outgrows memory. Log-based CDC (Debezium) is not built in."""

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import text

from konmari.catalog import Catalog, new_id, now
from konmari.connect import descriptor as dsc
from konmari.connect import lineage
from konmari.connect import products as prd
from konmari.governance import acl as gacl
from konmari.inventory.connectors import Sql, resolve_secret
from konmari.governance import policy as pol
from konmari.versions import Refused

LIVE = ("draft", "approved", "published", "superseded", "deprecated")


def _plain(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def _fields(cat: Catalog, pid: str) -> list[str]:
    """Contract fields of every live version: undeclared source columns are never copied."""
    names = []
    for v in prd.versions(cat, pid):
        if v["state"] in LIVE:
            names += [f["name"] for f in dsc.fields(v["desc"]) if f["name"] not in names]
    return names


def _descriptor(cat: Catalog, pid: str) -> dict:
    """Sources and key come from the current version, else the newest live one (first refresh before publish)."""
    cur = prd.current(cat, pid)
    live = [v for v in prd.versions(cat, pid) if v["state"] in LIVE]
    if not cur and not live:
        raise Refused(f"{pid} has no live version to refresh")
    return (cur or live[-1])["desc"]


def _acl(row: dict, src: dict, default: dict | None) -> dict:
    if src.get("aclColumn"):
        raw = row.get(src["aclColumn"]) or ""
        return gacl.normalize_acl([p for p in str(raw).replace(";", ",").split(",") if p.strip()])
    return default or {"allow": [], "deny": []}  # no ACL: the row is never returned (fail closed)


def _tombstones(cat: Catalog, src: dict) -> set[str]:
    """Keys of records archived, excluded or erased by a c04 retention decision.
    shortcut: a record's c04 item_id must equal its key value in the source table."""
    rows = cat.query("SELECT item_id FROM corpus_item WHERE asset_uri = ? AND state != 'active'",
                     (src.get("assetUri"),))
    erased = cat.query("SELECT item_id FROM erasure_record")
    return {r["item_id"] for r in rows} | {r["item_id"] for r in erased}


def read_source(src: dict, cols: list[str], since: str | None) -> tuple[list[dict], set[str]]:
    """Changed rows since the cursor plus every current key (for delete reconciliation). Through the c02 SQL
    connector: read-only, no password in the locator (`secretRef: env:NAME` or `file:/path` instead)."""
    eng = Sql(src["locator"], resolve_secret(src.get("secretRef"))).engine
    want = list(dict.fromkeys([src["key"], src["cursor"], *cols, *([src["aclColumn"]] if src.get("aclColumn")
                                                                   else [])]))
    try:
        with eng.connect() as c:
            have = {k.lower() for k in c.execute(text(f"SELECT * FROM {src['table']} WHERE 1 = 0")).keys()}
            sel = ", ".join(x for x in want if x.lower() in have)
            where = f" WHERE {src['cursor']} > :since" if since is not None else ""
            rows = [dict(r._mapping) for r in c.execute(
                text(f"SELECT {sel} FROM {src['table']}{where} ORDER BY {src['cursor']}, {src['key']}"),
                {"since": since} if since is not None else {})]
            keys = {str(r[0]) for r in c.execute(text(f"SELECT {src['key']} FROM {src['table']}"))}
    finally:
        eng.dispose()
    return rows, keys


def _as_of(cursor) -> datetime | None:
    """A timestamp cursor as naive UTC; None for non-time cursors (then as_of is the run time)."""
    try:
        t = datetime.fromisoformat(str(cursor))
    except ValueError:
        return None
    return t if t.tzinfo is None else t.astimezone(timezone.utc).replace(tzinfo=None)


def refresh(cat: Catalog, pid: str, actor: str = "konmari-refresh", transport=None) -> dict:
    """One refresh run: counts, cursor, `as_of`, contract test, lineage START and COMPLETE/FAIL."""
    prd.product(cat, pid)
    desc = _descriptor(cat, pid)
    i, cols = dsc.info(desc), _fields(cat, pid)
    last = cat.one("SELECT cursor_to, as_of FROM refresh_run WHERE product_id = ? AND status = 'ok'"
                   " ORDER BY started_at DESC LIMIT 1", (pid,))
    cur_ver = (prd.current(cat, pid) or {}).get("version") or i["version"]
    run = {"run_id": new_id(), "product_id": pid, "started_at": now(), "cursor_from": last and last["cursor_to"]}
    cat.execute("INSERT INTO refresh_run (run_id, product_id, started_at, status, mode, cursor_from, inserted,"
                " updated, deleted) VALUES (?, ?, ?, 'running', 'cursor', ?, 0, 0, 0)",
                (run["run_id"], pid, run["started_at"], run["cursor_from"]))
    lin = lineage.start(cat, f"product.refresh.{pid}", [f"sources:{s['id']}.{s['table']}" for s in i["sources"]],
                        [f"{pid}@{cur_ver}"], transport)
    old = {r["row_key"]: r for r in cat.query("SELECT * FROM product_row WHERE product_id = ?", (pid,))}
    p = pol.active(cat, i["masking_policy"])
    principals = (p or {}).get("doc", {}).get("principals")
    default_acl = gacl.normalize_acl(principals["allow"], principals.get("deny", [])) if principals else None
    try:
        cursors = json.loads(run["cursor_from"]) if run["cursor_from"] else {}
        staged, seen, high = {k: dict(v) for k, v in old.items()}, set(), dict(cursors)
        ins = upd = dele = 0
        for s in i["sources"]:
            rows, keys = read_source(s, cols, cursors.get(s["id"]))
            dead = _tombstones(cat, s)
            for r in rows:
                k = str(r[s["key"]])
                if k in dead:
                    continue
                data = {c: _plain(r.get(c)) for c in cols if c in r}
                new = {"row_key": k, "data": json.dumps(data, sort_keys=True, ensure_ascii=False),
                       "acl": json.dumps(_acl(r, s, default_acl), sort_keys=True),
                       "cursor_value": str(_plain(r[s["cursor"]]))}
                if k not in staged:
                    ins += 1
                elif (staged[k]["data"], staged[k]["acl"]) != (new["data"], new["acl"]):
                    upd += 1
                else:
                    continue
                staged[k] = new
            if rows:  # rows are newer than the stored cursor, so their maximum is the new high-water mark
                high[s["id"]] = _plain(max(r[s["cursor"]] for r in rows))
            seen |= keys - dead
        for k in [k for k in staged if k not in seen]:
            del staged[k]
            dele += 1
        test = prd.run_tests(cat, pid, cur_ver, [json.loads(r["data"]) for r in staged.values()], "refresh", actor)
        cursor_to = json.dumps(high, sort_keys=True)
        marks = [t for t in (_as_of(v) for v in high.values()) if t]
        if not test["passed"]:
            failed = ", ".join(f"{f['rule']} {f['field']} ({f['violations']})" for f in test["failures"])
            raise _TestFailed(test["test_id"], f"contract test failed: {failed}")
        changed = [r for k, r in staged.items() if old.get(k) != r]
        with cat.transaction():  # the swap: all changes or none
            for k in [k for k in old if k not in staged] + [r["row_key"] for r in changed if r["row_key"] in old]:
                cat.execute("DELETE FROM product_row WHERE product_id = ? AND row_key = ?", (pid, k))
            if changed:
                cat.many("INSERT INTO product_row VALUES (?, ?, ?, ?, ?)",
                         [(pid, r["row_key"], r["data"], r["acl"], r["cursor_value"]) for r in changed])
            # as_of = the source high-water mark (the oldest one when a product has several sources)
            as_of = min(marks) if marks else run["started_at"]
            cat.execute("UPDATE refresh_run SET status = 'ok', ended_at = ?, cursor_to = ?, inserted = ?, updated = ?,"
                        " deleted = ?, as_of = ?, test_id = ? WHERE run_id = ?",
                        (now(), cursor_to, ins, upd, dele, as_of, test["test_id"], run["run_id"]))
    except Exception as e:  # never overwrite good data; record the failure for freshness monitoring (c09)
        test_id = e.test_id if isinstance(e, _TestFailed) else None
        cat.execute("UPDATE refresh_run SET status = 'failed', ended_at = ?, as_of = ?, test_id = ?, error = ?"
                    " WHERE run_id = ?", (now(), last and last["as_of"], test_id, str(e)[:500], run["run_id"]))
        lineage.finish(cat, lin, "FAIL", transport, str(e)[:500])
        prd.event(cat, "product.refresh", actor, result="failed", product=pid, run=run["run_id"], error=str(e)[:200])
        return get_run(cat, run["run_id"])
    lineage.finish(cat, lin, "COMPLETE", transport)
    prd.event(cat, "product.refresh", actor, result="ok", product=pid, run=run["run_id"], inserted=ins,
              updated=upd, deleted=dele)
    return get_run(cat, run["run_id"])


class _TestFailed(Exception):
    def __init__(self, test_id: str, msg: str):
        super().__init__(msg)
        self.test_id = test_id


def get_run(cat: Catalog, run_id: str) -> dict:
    return cat.one("SELECT * FROM refresh_run WHERE run_id = ?", (run_id,))


# ---------------------------------------------------------------- schedule

def interval(sla: timedelta) -> timedelta:
    """Refresh at half the freshness SLA, so one failed run still leaves time before the data is stale;
    never more often than every 15 minutes and at least daily."""
    return min(max(sla / 2, timedelta(minutes=15)), timedelta(days=1))


def schedule(desc: dict, catalog_url: str | None = None) -> dict:
    """`konmari product schedule`: cron line and systemd timer derived from the freshness SLA."""
    i = dsc.info(desc)
    if i["sla"] is None:
        raise Refused(f"{i['id']} has no freshness SLA")
    step = interval(i["sla"])
    mins = int(step.total_seconds() // 60)
    if mins >= 1440:
        cron = "0 2 * * *"
    elif mins >= 60:
        cron = f"0 */{mins // 60} * * *"
    else:
        cron = f"*/{mins} * * * *"
    cmd = f"konmari{' --catalog ' + catalog_url if catalog_url else ''} product refresh {i['id']} --as konmari-refresh"
    unit = f"konmari-refresh-{i['id']}"
    timer = (f"[Unit]\nDescription=Refresh data product {i['id']} (freshness SLA {i['sla']})\n\n"
             f"[Timer]\nOnBootSec=5min\nOnUnitActiveSec={mins}min\nUnit={unit}.service\n\n"
             "[Install]\nWantedBy=timers.target\n")
    service = f"[Unit]\nDescription=konmari product refresh {i['id']}\n\n[Service]\nType=oneshot\nExecStart={cmd}\n"
    return {"product": i["id"], "interval_minutes": mins, "cron": f"{cron} {cmd}", "timer": timer,
            "service": service, "unit": unit}
