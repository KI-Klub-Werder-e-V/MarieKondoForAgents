"""audit-logging (c07): payload-free agent events, fail-closed tool calls, append-only storage, verify with
checkpoints and legacy adoption, retention, Art. 15 export, OTel correlation, one shared trail. Synthetic data."""

import json
from datetime import datetime, timedelta

import pytest

from gov_fixture import SUPPORT, add_doc, agent, approve_all, events, govern, scope
from konmari import catalog
from konmari.catalog import AppendOnlyError, audit, connect, verify_chain
from konmari.governance import access, audit as gaudit, config as gcfg
from konmari.governance import policy as pol
from konmari.versions import Refused


def _support_pilot(cat, n=5):
    sc = scope()
    add_doc(cat, "faq", SUPPORT, [f"Frage {i}: Wie melde ich Sportart {i} an?" for i in range(n)],
            allow=["group:support-team"])
    approve_all(cat, sc)
    govern(cat, sc)
    agent(cat, sc)
    return sc, access.context(cat, "faq-bot", "finn", ["support-team"])


def _tamper(cat, sql, params=()):
    """A superuser editing the table directly, around the append-only guards."""
    if cat.dialect == "postgres":
        cur = cat.conn.cursor()
        cur.execute("ALTER TABLE audit_event DISABLE TRIGGER USER")
        cur.execute(sql.replace("?", "%s"), params)
        cur.execute("ALTER TABLE audit_event ENABLE TRIGGER USER")
    else:
        cat.conn.cursor().execute(sql, params)


# ---------------------------------------------------------------- agent access events

def test_allowed_retrieval(gcat):
    sc, ctx = _support_pilot(gcat)
    hits = access.retrieve(gcat, ctx, "Wie melde ich Sportart an", k=5)
    assert len(hits) == 5
    ev = events(gcat, "agent.tool_call")[-1]
    d = ev["details"]
    assert (ev["actor"], d["on_behalf_of"], d["tool"], d["operation"], d["decision"]) == \
        ("faq-bot", "finn", "search", "read", "allow")
    assert sorted(d["resource_ids"]) == sorted(h["chunk_id"] for h in hits)
    assert d["policy_versions"] == pol.active_versions(gcat) and d["trace_id"]
    assert "Sportart" not in json.dumps(ev, default=str)  # no chunk text, no query text


def test_audit_write_fails(gcat, monkeypatch):
    sc, ctx = _support_pilot(gcat)

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(access, "write_event", broken)
    with pytest.raises(gaudit.AuditUnavailable):
        access.retrieve(gcat, ctx, "Sportart")


def test_payload_field_rejected(gcat):
    for field in ("prompt", "completion", "args", "content"):
        with pytest.raises(ValueError, match=field):
            gaudit.write_event(gcat, "agent.tool_call", actor="faq-bot", **{field: "Frau Petra Schulz"})
    with pytest.raises(ValueError, match="details.call.arguments"):
        gaudit.write_event(gcat, "agent.tool_call", actor="faq-bot", call={"arguments": {"q": "x"}})
    assert events(gcat, "agent.tool_call") == []


# ---------------------------------------------------------------- governance decisions

def test_policy_approved(gcat):
    sc = scope()
    approve_all(gcat, sc)  # versions 1 (seed) and 2 (approved)
    row = pol.edit(gcat, sc, SUPPORT, {"tools": ["search", "get_document"]}, "gina")
    pol.approve(gcat, sc, SUPPORT, row["version"], "ben", "Werkzeug für Dokumentabruf")
    d = events(gcat, "govern.policy.approve")[-1]
    assert d["actor"] == "ben"
    assert (d["details"]["policy"], d["details"]["previous"], d["details"]["new"], d["details"]["reason"]) == \
        (SUPPORT, 2, 3, "Werkzeug für Dokumentabruf")


# ---------------------------------------------------------------- append-only storage

def test_update_attempt(gcat):
    audit(gcat, "test.event", actor="ben")
    with pytest.raises(AppendOnlyError):
        gcat.execute("UPDATE audit_event SET actor = 'mallory'")
    if gcat.dialect == "postgres":
        import psycopg

        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with gcat.transaction():
                gcat.cursor().execute("SET LOCAL ROLE konmari_app")
                gcat.cursor().execute("UPDATE audit_event SET actor = 'mallory'")
        with pytest.raises(psycopg.errors.RaiseException):  # even the owner hits the trigger
            gcat.cursor().execute("DELETE FROM audit_event")


def test_tampered_row_detected(engagement, cli):
    cat = connect()
    for i in range(3):
        audit(cat, "test.event", actor="ben", details={"n": i})
    bad = cat.query("SELECT id FROM audit_event ORDER BY seq")[1]["id"]
    _tamper(cat, "UPDATE audit_event SET actor = 'mallory' WHERE id = ?", (bad,))
    cat.close()
    code, out, err = cli("audit", "verify")
    assert code == 1 and bad in err


def test_migrations_apply_and_roll_back(gcat):
    name = "007_c07_governance.sql"
    audit(gcat, "test.event", actor="ben")
    gcat.rollback(name)
    tables = {r["table_name"] for r in gcat.query("SELECT table_name FROM information_schema.tables")}
    assert not tables & {"agent_index", "source_policy", "pii_finding", "pseudonym_key"}
    assert verify_chain(gcat) is None  # audit rows survive the rollback unchanged
    gcat.migrate()
    assert gcat.one("SELECT COUNT(*) AS n FROM agent_index")["n"] == 0 and verify_chain(gcat) is None
    if gcat.dialect == "postgres":
        part = gcat.one("SELECT COUNT(*) AS n FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhparent"
                        " WHERE c.relname = 'audit_event'")
        assert part["n"] >= 2  # default + current month


# ---------------------------------------------------------------- retention

def _old_events(cat, monkeypatch, months=8, n=3):
    t0 = catalog.now()
    monkeypatch.setattr(catalog, "now", lambda: t0 - timedelta(days=30 * months))
    for i in range(n):
        audit(cat, "test.old", actor="ben", details={"n": i})
    monkeypatch.setattr(catalog, "now", lambda: t0)
    for i in range(2):
        audit(cat, "test.new", actor="ben", details={"n": i})


def test_purge_after_retention(gcat, monkeypatch):
    _old_events(gcat, monkeypatch)
    rep = gaudit.purge(gcat, gcfg.load("x.yaml"), "eva")
    assert rep["deleted"] == 3 and rep["to_seq"] == 3
    assert {r["action"] for r in gcat.query("SELECT action FROM audit_event")} == \
        {"test.new", "audit.checkpoint"}
    assert verify_chain(gcat) is None
    gaudit.write_event(gcat, "test.after", actor="ben")
    assert verify_chain(gcat) is None
    # deleting a non-purged event is still detected
    victim = gcat.one("SELECT id FROM audit_event WHERE action = 'test.new' ORDER BY seq LIMIT 1")["id"]
    _tamper(gcat, "DELETE FROM audit_event WHERE id = ?", (victim,))
    assert verify_chain(gcat) is not None


def test_retention_misconfigured():
    cfg = gcfg.load("x.yaml")
    cfg["audit"].update(retention_months=3, ai_act_high_risk=True)
    assert any("Art. 26(6)" in e for e in gcfg.validate(cfg))
    with pytest.raises(Refused, match="Art. 26"):
        gaudit.purge(None, cfg, "eva")
    cfg["audit"].update(retention_months=36)
    assert any("Art. 30" in e for e in gcfg.validate(cfg))
    cfg["audit"].update(retention_months=3, ai_act_high_risk=None)
    assert gcfg.validate(cfg, {"compliance": {"ai_act": {"classification": "high"}}})
    assert gcfg.validate(cfg, {"compliance": {"ai_act": {"classification": "minimal"}}}) == []


# ---------------------------------------------------------------- export

def test_data_subject_request(gcat):
    sc, ctx = _support_pilot(gcat)
    access.retrieve(gcat, ctx, "Sportart")
    other = access.context(gcat, "faq-bot", "lena", ["support-team"])
    access.retrieve(gcat, other, "Sportart")
    access.retrieve(gcat, ctx, "anmelden")
    text, n = gaudit.export(gcat, "eva", subject="finn")
    rows = [json.loads(line) for line in text.splitlines()]
    assert n == 2 and all(json.loads(r["details"])["on_behalf_of"] == "finn" for r in rows)
    ids = {r["id"] for r in rows}
    want = {e["id"] for e in events(gcat, "agent.tool_call") if e["details"]["on_behalf_of"] == "finn"}
    assert ids == want
    exp = events(gcat, "audit.export")[-1]
    assert exp["actor"] == "eva" and exp["details"]["subject"] == "finn" and exp["details"]["events"] == 2
    csv_text, m = gaudit.export(gcat, "eva", start=datetime(2000, 1, 1), fmt="csv")
    assert csv_text.startswith("id,ts,seq") and m > n


# ---------------------------------------------------------------- trace correlation

def test_correlate_a_denied_call(gcat, spans):
    sc = scope()
    agent(gcat, sc, "ticket-bot", [f"write:{SUPPORT}"])
    ctx = access.context(gcat, "ticket-bot", "finn", [])
    spans.clear()
    with pytest.raises(access.AccessDenied), access.invoke(ctx):
        access.write(gcat, ctx, "create_ticket", SUPPORT, lambda: ("Bitte Frau Schulz zurückrufen", ["t-1"]))
    ev = events(gcat, "agent.tool_call")[-1]
    assert ev["details"]["decision"] == "deny"
    span = next(s for s in spans.get_finished_spans() if f"{s.context.trace_id:032x}" == ev["details"]["trace_id"])
    assert span.name == "execute_tool create_ticket"
    assert span.attributes["gen_ai.operation.name"] == "execute_tool"
    assert "Schulz" not in json.dumps(dict(span.attributes)) and not span.events
    parent = next(s for s in spans.get_finished_spans() if s.name == "invoke_agent ticket-bot")
    assert span.parent.span_id == parent.context.span_id


# ---------------------------------------------------------------- one shared trail

def test_earlier_decisions_in_same_chain(engagement, cli):
    cat = connect()
    audit(cat, "inventory.waive", actor="ben", step="inventory", details={"entity": "asset-1"})
    audit(cat, "discard.approve", actor="ben", step="discard", details={"item_id": "doc-1"})
    sc = scope()
    approve_all(cat, sc)
    # an event written before the chain existed (c01-c03 catalogs migrated later): seq NULL
    cat.conn.cursor().execute("INSERT INTO audit_event (id, ts, actor, action, step, result, details) VALUES"
                              " ('legacy-1', TIMESTAMP '2025-01-02 10:00:00', 'anna', 'charter.approve', NULL,"
                              " 'recorded', '{}')")
    cat.close()
    code, out, err = cli("audit", "verify")
    assert code == 0, err
    code2, out2, err2 = cli("discard", "log", "verify")
    assert code2 == 0, err2
    cat = connect()
    steps = {r["step"] for r in cat.query("SELECT step FROM audit_event WHERE seq IS NOT NULL")}
    assert {"inventory", "discard", "govern"} <= steps
    assert events(cat, "audit.adopt")[0]["details"]["events"].keys() == {"legacy-1"}
    _tamper(cat, "UPDATE audit_event SET actor = 'mallory' WHERE id = 'legacy-1'")
    cat.close()
    assert cli("audit", "verify")[0] == 1
    assert cli("discard", "log", "verify")[0] == 1  # the same chain, the same verdict
