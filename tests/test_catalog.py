import pytest

from konmari.canonical import canonical_hash, load_yaml
from konmari.catalog import AppendOnlyError, audit, connect

TABLES = ["charter_version", "stakeholder", "raci_assignment", "metric_set_version", "metric_definition",
          "metric_measurement", "approval", "decision_log", "audit_event"]


def _tables(cat):
    return {r["table_name"] for r in cat.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')")}


def test_migrations_apply_on_duckdb(tmp_path):
    cat = connect(f"duckdb:///{tmp_path}/c.duckdb")
    assert set(TABLES) <= _tables(cat)
    cat.migrate()  # idempotent
    assert len(cat.query("SELECT * FROM schema_migration")) == 1


def test_migrations_apply_on_postgres(pg_url):
    cat = connect(pg_url)
    assert set(TABLES) <= _tables(cat)
    cat.migrate()
    assert len(cat.query("SELECT * FROM schema_migration")) == 1


def test_canonical_hash_ignores_key_order_and_whitespace():
    a = load_yaml("b: 1\na:\n  y: [1, 2]\n  x: '2026-01-01'\n")
    b = load_yaml("a:   {x: 2026-01-01,   y: [1,2]}\n\n# comment\nb:    1\n")
    assert canonical_hash(a) == canonical_hash(b)
    assert canonical_hash(a) != canonical_hash({**a, "b": 2})


def _audit_mutations_refused(cat):
    audit(cat, "test.event", actor="tester", details={"k": 1})
    with pytest.raises(AppendOnlyError):
        cat.execute("UPDATE audit_event SET actor = 'x'")
    with pytest.raises(AppendOnlyError):
        cat.execute("delete from audit_event")
    assert cat.one("SELECT actor FROM audit_event")["actor"] == "tester"


def test_audit_update_and_delete_refused_duckdb(tmp_path):
    _audit_mutations_refused(connect(f"duckdb:///{tmp_path}/c.duckdb"))


def test_audit_update_and_delete_refused_postgres(pg_url):
    import psycopg

    cat = connect(pg_url)
    _audit_mutations_refused(cat)
    for sql in ("UPDATE audit_event SET actor = 'x'", "DELETE FROM audit_event", "TRUNCATE audit_event"):
        with pytest.raises(psycopg.errors.RaiseException):  # database trigger, bypassing the catalog layer
            cat.conn.execute(sql)


@pytest.mark.parametrize("group,cmds", [
    ("charter", ["init", "validate", "approve", "amend", "show", "diff", "log", "status", "gate"]),
    ("metrics", ["validate", "approve", "amend", "baseline", "measure", "report"]),
])
def test_help_lists_all_subcommands(group, cmds):
    import subprocess
    import sys

    out = subprocess.run([sys.executable, "-m", "konmari.cli", group, "--help"], capture_output=True, text=True).stdout
    assert all(c in out for c in cmds)
