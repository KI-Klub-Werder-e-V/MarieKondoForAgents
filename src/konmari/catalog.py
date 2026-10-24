"""Readiness catalog: DuckDB (default, local) or Postgres 16, same migrations, plus the append-only audit writer."""

import getpass
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
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
        cur = self.conn.cursor()
        cur.execute(sql, params)
        return cur

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
        self.conn.cursor().execute((files("konmari") / "migrations" / name.replace(".sql", ".down.sql")).read_text())
        self.execute("DELETE FROM schema_migration WHERE name = ?", (name,))

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
    cat.execute(
        "INSERT INTO audit_event (id, ts, actor, action, charter_id, charter_version, step, result, details, seq,"
        " prev_hash, hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (*row.values(), (last["seq"] + 1) if last else 1, prev, event_hash(prev, row)),
    )


def verify_chain(cat: Catalog) -> dict | None:
    """The first chained event whose hash, predecessor or position no longer matches, or None if the chain holds."""
    prev, seq = "", 0
    for r in cat.query("SELECT * FROM audit_event WHERE seq IS NOT NULL ORDER BY seq"):
        seq += 1
        if r["seq"] != seq or r["prev_hash"] != prev or r["hash"] != event_hash(prev, r):
            return r
        prev = r["hash"]
    return None


def audit_change(cat: Catalog, action: str, entity: str, previous, new, *, actor: str | None = None,
                 reason: str | None = None, charter_id=None, step=None, result="recorded", **extra) -> None:
    """Audit one decision as actor, timestamp, entity, previous value, new value and reason."""
    audit(cat, action, actor=actor, charter_id=charter_id, step=step, result=result,
          details={"entity": entity, "previous": previous, "new": new, "reason": reason, **extra})
