"""Readiness catalog: DuckDB (default, local) or Postgres 16, same migrations, plus the append-only audit writer."""

import getpass
import hashlib
import json
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

DEFAULT_URL = "duckdb:///.konmari/catalog.duckdb"
# audit_event (and its decision_event view), published rubric versions and the verdict review history are append-only
_AUDIT_MUTATION = re.compile(
    r"^\s*(update|delete\s+from|truncate)\s+(table\s+)?(audit_event|decision_event|rubric_version|verdict_review)\b",
                             re.I)


class AppendOnlyError(RuntimeError):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_id() -> str:
    return uuid.uuid4().hex


def os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no login name in some containers
        return "unknown"


class Catalog:
    def __init__(self, conn, dialect: str):
        self.conn, self.dialect = conn, dialect

    def execute(self, sql: str, params=()):
        if m := _AUDIT_MUTATION.match(sql):
            raise AppendOnlyError(f"{m[3]} is append-only: UPDATE, DELETE and TRUNCATE are refused")
        if self.dialect == "postgres":
            sql = sql.replace("?", "%s")
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def cursor(self):
        # a DuckDB cursor is a separate connection; inside transaction() all statements use the one connection
        return self.conn if self.__dict__.get("_tx") and self.dialect == "duckdb" else self.conn.cursor()

    def many(self, sql: str, rows: list, chunk: int = 500) -> None:
        """INSERT ... VALUES (?, ...) for many rows, sent as multi-row statements (executemany is slow on DuckDB)."""
        head, _, tpl = sql.rpartition("VALUES")
        for i in range(0, len(rows), chunk):
            part = rows[i:i + chunk]
            self.execute(f"{head}VALUES {', '.join([tpl.strip()] * len(part))}", [x for r in part for x in r])

    def query(self, sql: str, params=()) -> list[dict]:
        cur = self.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def one(self, sql: str, params=()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def migrate(self) -> None:
        cur = self.conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS schema_migration (name TEXT PRIMARY KEY, applied_at TIMESTAMP NOT NULL)")
        cur.execute("SELECT name FROM schema_migration")
        done = {r[0] for r in cur.fetchall()}
        mig = files("konmari") / "migrations"
        for f in sorted(p.name for p in mig.iterdir() if p.name.endswith(".sql") and p.name.count(".") == 1):
            if f in done:
                continue
            cur.execute(mig.joinpath(f).read_text())
            extra = mig / f.replace(".sql", f".{self.dialect}.sql")
            if extra.is_file():
                cur.execute(extra.read_text())
            self.execute("INSERT INTO schema_migration (name, applied_at) VALUES (?, ?)", (f, now()))

    def rollback(self, name: str) -> None:
        """Undo one migration with its .down.sql file (new tables only; source systems are never touched)."""
        mig = files("konmari") / "migrations"
        extra = mig / name.replace(".sql", f".down.{self.dialect}.sql")
        if extra.is_file():
            self.conn.cursor().execute(extra.read_text())
        self.conn.cursor().execute((mig / name.replace(".sql", ".down.sql")).read_text())
        self.execute("DELETE FROM schema_migration WHERE name = ?", (name,))

    @contextmanager
    def transaction(self):
        """One transaction; SET LOCAL settings (principal context, role) end with it."""
        if self.dialect == "postgres":
            with self.conn.transaction():
                yield self
            return
        self.conn.execute("BEGIN")
        self._tx = True
        try:
            yield self
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")
        finally:
            self._tx = False

    def ensure_audit_partition(self, ts: datetime) -> None:
        """Postgres: the monthly audit_event partition for `ts` (c07); a no-op on DuckDB."""
        month = ts.strftime("%Y_%m")
        done = self.__dict__.setdefault("_partitions", set())
        if self.dialect != "postgres" or month in done:
            return
        start = ts.replace(day=1).date()
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        cur = self.conn.cursor()
        cur.execute("SELECT 1 FROM pg_partitioned_table p JOIN pg_class c ON c.oid = p.partrelid"
                    " WHERE c.relname = 'audit_event'")
        if cur.fetchone():
            cur.execute(f"CREATE TABLE IF NOT EXISTS audit_event_{month} PARTITION OF audit_event"
                        f" FOR VALUES FROM ('{start}') TO ('{end}')")
        done.add(month)

    def close(self) -> None:
        self.conn.close()


def _duckdb_path(url: str) -> str:
    # duckdb:///relative/path or duckdb:////absolute/path; a bare path is used as is
    return url.removeprefix("duckdb:///")


def connect(url: str | None = None) -> Catalog:
    """Open (and migrate) the catalog. URL: duckdb:///path, a *.duckdb path, or postgresql://..."""
    url = url or os.environ.get("KONMARI_CATALOG") or DEFAULT_URL
    if url.startswith(("postgres://", "postgresql://")):
        import psycopg  # optional extra: pip install konmari[postgres]

        cat = Catalog(psycopg.connect(url, autocommit=True), "postgres")
    else:
        import duckdb

        path = _duckdb_path(url)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        cat = Catalog(duckdb.connect(path), "duckdb")
    cat.migrate()
    return cat


_CHAIN = ("id", "ts", "actor", "action", "charter_id", "charter_version", "step", "result", "details")


def event_hash(prev_hash: str, row: dict) -> str:
    """SHA-256 over the previous hash plus the canonical JSON of the event (c04 design D6)."""
    body = {k: row[k].isoformat() if isinstance(row[k], datetime) else row[k] for k in _CHAIN}
    return hashlib.sha256((prev_hash + json.dumps(body, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def audit(cat: Catalog, action: str, *, actor: str | None = None, charter_id=None, charter_version=None,
          step=None, result=None, details: dict | None = None) -> None:
    """The only writer for audit_event; every command records its decisions here, hash-chained to the previous
    event."""
    # shortcut: seq = last + 1 assumes one writer at a time; add a lock or sequence for concurrent writers
    last = cat.one("SELECT seq, hash FROM audit_event WHERE seq IS NOT NULL ORDER BY seq DESC LIMIT 1")
    row = {"id": new_id(), "ts": now(), "actor": actor or os_user(), "action": action, "charter_id": charter_id,
           "charter_version": charter_version, "step": step, "result": result,
           "details": json.dumps(details or {}, sort_keys=True, default=str)}
    prev = last["hash"] if last else ""
    cat.ensure_audit_partition(row["ts"])
    cat.execute(
        "INSERT INTO audit_event (id, ts, actor, action, charter_id, charter_version, step, result, details, seq,"
        " prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*row.values(), (last["seq"] + 1) if last else 1, prev, event_hash(prev, row)),
    )


def verify_chain(cat: Catalog) -> dict | None:
    """The first chained event whose hash, predecessor or position no longer matches, or None if the chain holds.
    A purge leaves a checkpoint event (c07): the chain then starts after the checkpoint's last purged event.
    Events written before the chain existed are covered by `audit.adopt` events holding their hashes."""
    rows = cat.query("SELECT * FROM audit_event WHERE seq IS NOT NULL ORDER BY seq")
    details = {r["id"]: json.loads(r["details"] or "{}") for r in rows
               if r["action"] in ("audit.checkpoint", "audit.adopt")}
    checkpoints = {d["to_seq"]: d for i, d in details.items() if "to_seq" in d}
    prev, seq = "", 0
    if rows and rows[0]["seq"] != 1:
        cp = checkpoints.get(rows[0]["seq"] - 1)
        if cp is None:
            return rows[0]
        prev, seq = cp["last_hash"], cp["to_seq"]
    for r in rows:
        seq += 1
        if r["seq"] != seq or r["prev_hash"] != prev or r["hash"] != event_hash(prev, r):
            return r
        prev = r["hash"]
    legacy = {r["id"]: r for r in cat.query("SELECT * FROM audit_event WHERE seq IS NULL")}
    purged = {i for cp in checkpoints.values() for i in cp.get("legacy_ids", [])}
    for r in rows:
        if r["action"] == "audit.adopt":
            for eid, h in details[r["id"]]["events"].items():
                if eid in legacy and event_hash("", legacy[eid]) != h or eid not in legacy and eid not in purged:
                    return legacy.get(eid, r)
    return None


def audit_change(cat: Catalog, action: str, entity: str, previous, new, *, actor: str | None = None,
                 reason: str | None = None, charter_id=None, step=None, result="recorded", **extra) -> None:
    """Audit one decision as actor, timestamp, entity, previous value, new value and reason."""
    audit(cat, action, actor=actor, charter_id=charter_id, step=step, result=result,
          details={"entity": entity, "previous": previous, "new": new, "reason": reason, **extra})
