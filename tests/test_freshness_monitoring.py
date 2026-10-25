"""freshness-monitoring (c09): monitored asset registry, freshness tiers, quality and drift checks, OpenLineage
facets, dbt/Soda import, owner-routed incidents, escalation, agent exposure, scorecard and discard feedback.
Synthetic data only."""

import json
from datetime import datetime, timedelta

import pytest
from jsonschema import Draft202012Validator

import c09_fixture as fx
from c09_fixture import KB
from konmari.catalog import now
from konmari.governance import access
from konmari.monitor import checks as mc
from konmari.monitor import incidents as mi
from konmari.schema import load_schema
from konmari.versions import Refused

AT = datetime(2026, 10, 19, 9, 0)  # a Monday
BEN = "geschaeftsstelle@example.org"


def asset(cat, uri, freq="P7D", age_days=1.0, owner="ben", title=None, tier="internal", at=AT, pd="[]", source="src"):
    t = now()
    cat.execute("INSERT INTO source_asset (asset_uri, source_id, charter_id, system_type, origin, title, owner,"
                " update_frequency, last_modified, sensitivity_tier, personal_data_categories, ropa_missing,"
                " first_seen, last_seen, status) VALUES (?, ?, 'verein-faq-assistant', 'sql', 'scan', ?, ?, ?, ?,"
                " ?, ?, false, ?, ?, 'complete')",
                (uri, source, title or uri.rsplit("/", 1)[-1], owner, freq, at - timedelta(days=age_days), tier, pd, t, t))


def items(cat, uri, n, prefix="doc"):
    t = now()
    cat.many("INSERT INTO corpus_item (item_id, asset_uri, charter_id, title, content_ref, text_ref, meta, state,"
             " state_changed_at, first_seen, last_seen) VALUES (?, ?, 'verein-faq-assistant', ?, 'x', NULL, '{}',"
             " 'active', ?, ?, ?)", [(f"{prefix}-{i:04d}", uri, f"{prefix} {i}", t, t, t) for i in range(n)])


def monitor(c09, at=AT, cfg=None, dry_run=False):
    send = mi.MemoryNotifier()
    rep = mc.run(c09["cat"], c09["sc"], cfg or mc.load_config("none.yaml"), send=send, at=at, dry_run=dry_run,
                 transport=c09.setdefault("lineage", _Mem()))
    return rep, send


class _Mem:
    def __init__(self):
        self.events = []

    def emit(self, e):
        self.events.append(e)


def check(rep, asset_id, name):
    return next(r for r in rep["results"] if r["asset_id"] == asset_id and r["check_name"] == name)


# ---------------------------------------------------------------- registry and freshness

def test_asset_without_update_interval(c09):
    asset(c09["cat"], "sql://crm/contacts", freq="unknown", owner="clara")  # the use_case_owner is ben
    asset(c09["cat"], "sql://crm/orphan", owner=None)
    rep, send = monitor(c09)
    assert {"asset": "sql://crm/contacts", "reason": "missing_update_interval"} in rep["unmonitorable"]
    assert {"asset": "sql://crm/orphan", "reason": "missing_owner"} in rep["unmonitorable"]
    assert [m["to"] for m in send.sent if "unmonitorable: sql://crm/contacts" in m["subject"]] == [BEN]
    _, again = monitor(c09, AT + timedelta(days=1))
    assert not [m for m in again.sent if "unmonitorable" in m["subject"]]  # reported once


def test_stale_source_becomes_error(c09):
    asset(c09["cat"], "sql://crm/contacts", freq="P7D", age_days=15)
    asset(c09["cat"], "sql://crm/orders", freq="P7D", age_days=8)
    asset(c09["cat"], "sql://crm/fresh", freq="P7D", age_days=2)
    rep, _ = monitor(c09)
    r = check(rep, "sql://crm/contacts", "freshness")
    assert r["status"] == "error" and r["observed"] == {"age_days": 15.0} and r["expected"]["error_after_days"] == 14
    assert check(rep, "sql://crm/orders", "freshness")["status"] == "warn"
    assert check(rep, "sql://crm/fresh", "freshness")["status"] == "ok"


def test_owner_override_audited(c09):
    cat, sc = c09["cat"], c09["sc"]
    asset(cat, "sql://crm/contacts", freq="P7D", age_days=15)
    monitor(c09)
    with pytest.raises(Refused, match="only the owner"):
        mc.set_threshold(cat, sc, "sql://crm/contacts", "clara", error_after=30)
    mc.set_threshold(cat, sc, "sql://crm/contacts", "ben", error_after=30, reason="monthly export")
    rep, _ = monitor(c09, AT + timedelta(hours=1))
    r = check(rep, "sql://crm/contacts", "freshness")
    assert r["status"] == "warn" and r["expected"]["error_after_days"] == 30
    ev = fx.events(cat, "monitor.threshold.override")[0]
    assert ev["actor"] == "ben" and ev["details"]["owner"] == "ben"
    assert ev["details"]["previous"]["error_after_days"] is None and ev["details"]["new"]["error_after_days"] == 30


def test_data_products_are_monitored(c08):
    import c08_fixture as f8

    cat, sc = c08["cat"], c08["sc"]
    f8.ship(cat, sc, f8.faq_desc(c08["loc"]))
    reg = {a["asset_id"]: a for a in mc.registry(cat, sc)}
    p = reg["product:support-faq"]
    assert (p["owner"], p["interval_days"], p["reason"]) == ("ben", 1.0, None) and p["last_change"] is not None
    rep = mc.run(cat, sc, mc.load_config("none.yaml"), send=mi.MemoryNotifier(), transport=_Mem())
    assert check(rep, "product:support-faq", "freshness")["status"] == "ok"
    assert check(rep, "product:support-faq", "volume")["observed"] == 12


# ---------------------------------------------------------------- quality

def test_volume_drop_detected(c09):
    cat = c09["cat"]
    asset(cat, "sharepoint://lib/docs")
    items(cat, "sharepoint://lib/docs", 1000)
    monitor(c09)
    cat.execute("UPDATE corpus_item SET state = 'archived' WHERE asset_uri = 'sharepoint://lib/docs' AND item_id >="
                " 'doc-0400'")
    rep, _ = monitor(c09, AT + timedelta(hours=1))
    r = check(rep, "sharepoint://lib/docs", "volume")
    assert (r["status"], r["observed"], r["expected"]) == ("error", 400, {"min": 800, "max": 1200})


def test_missing_metadata_share(c09):
    cat = c09["cat"]
    asset(cat, "sharepoint://lib/kb")
    items(cat, "sharepoint://lib/kb", 20, "kb")
    for i in range(20):
        cat.execute("INSERT INTO chunk_metadata (subject_id, subject_kind, item_id, run_id, envelope, acl_stale, expired,"
                    " glossary_terms, context_header, complete, status, reasons, updated_at) VALUES (?, 'chunk', ?, 'r',"
                    " '{}', false, false, '[]', '', ?, 'ready-for-agents', '[]', ?)",
                    (f"kb-{i:04d}#0", f"kb-{i:04d}", i >= 3, now()))
    rep, send = monitor(c09)
    r = check(rep, "sharepoint://lib/kb", "metadata_completeness")
    assert r["status"] == "error" and r["observed"] == 0.15
    alert = next(m for m in send.sent if "metadata_completeness" in m["subject"])
    assert alert["payload"]["samples"] == ["kb-0000#0", "kb-0001#0", "kb-0002#0"]


def test_schema_change_warns(c09):
    cat = c09["cat"]
    asset(cat, "sql://crm/contacts")
    cat.execute("UPDATE source_asset SET schema_hash = 'h1'")
    monitor(c09)
    cat.execute("UPDATE source_asset SET schema_hash = 'h2'")
    rep, _ = monitor(c09, AT + timedelta(hours=1))
    assert check(rep, "sql://crm/contacts", "schema_change")["status"] == "warn"


# ---------------------------------------------------------------- drift

def test_question_drift_detected(c09):
    cat, sc = c09["cat"], c09["sc"]
    old = ["Wie hoch ist der Mitgliedsbeitrag?", "Wann ist die Halle geöffnet?", "Wann ist der Beitrag fällig?"]
    new = ["Gibt es Rehasport für Senioren?", "Wann beginnt der Rehasport-Kurs?", "Kostet Rehasport extra?"]
    log = [{"ts": AT - timedelta(days=10, hours=i), "text": q} for i, q in enumerate(old * 3)]
    log += [{"ts": AT - timedelta(days=2, hours=i), "text": q} for i, q in enumerate(new * 3)]
    send = mi.MemoryNotifier()
    cfg = {**mc.load_config("none.yaml"), "topics": {"fees": ["beitrag"], "hall": ["halle"], "rehab": ["rehasport"]}}
    rows = mc.drift(cat, sc, {"questions": log}, cfg, "dirk", send, at=AT)
    d = rows[0]
    assert (d["status"], d["method"], d["subject"]) == ("warn", "topic_share_tvd", "questions")
    assert d["score"] == 1.0 and d["details"]["new_topics"] == ["rehab"]
    assert d["reference_window"].startswith((AT - timedelta(days=14)).isoformat()) and d["current_window"]
    assert cat.one("SELECT status FROM drift_result")["status"] == "warn"
    assert send.sent[0]["to"] == BEN and "golden items" in send.sent[0]["payload"]["message"]


def test_no_drift_on_same_topics(c09):
    q = ["Wie hoch ist der Mitgliedsbeitrag?", "Wann ist die Halle geöffnet?"]
    log = [{"ts": AT - timedelta(days=d), "text": t} for d in (2, 10) for t in q]
    rows = mc.drift(c09["cat"], c09["sc"], {"questions": log}, mc.load_config("none.yaml"), "dirk", at=AT)
    assert rows[0]["status"] == "ok" and rows[0]["score"] == 0.0


def test_evidently_adapter():
    pytest.importorskip("evidently")
    out = mc.EvidentlyDrift()(["Beitrag fällig?"] * 30, ["Rehasport Kurs?"] * 30)
    assert out["method"] == "evidently_text_drift" and 0 <= out["score"] <= 1


# ---------------------------------------------------------------- lineage and imports

def test_result_emitted_as_facet(c09):
    asset(c09["cat"], "sql://crm/contacts", title="crm.contacts", age_days=20)
    rep, _ = monitor(c09)
    ev = c09["lineage"].events[-1]
    inp = next(i for i in ev["inputs"] if i["name"] == "crm.contacts")
    facet = inp["inputFacets"]["dataQualityAssertions"]
    assert {"assertion": "freshness", "success": False} in facet["assertions"]
    assert list(Draft202012Validator(load_schema("data-quality-assertions-facet")).iter_errors(facet)) == []
    stored = json.loads(c09["cat"].one("SELECT event FROM lineage_event WHERE job = 'monitor.run'")["event"])
    assert stored == ev


def test_dbt_sources_json_import(c09, tmp_path):
    cat, sc = c09["cat"], c09["sc"]
    asset(cat, "sql://crm/contacts", title="crm.contacts")
    monitor(c09)
    p = tmp_path / "sources.json"
    p.write_text(json.dumps({"results": [
        {"unique_id": "source.verein.crm.contacts", "status": "error", "max_loaded_at_time_ago_in_s": 1300000,
         "criteria": {"warn_after": {"count": 7, "period": "day"}, "error_after": {"count": 14, "period": "day"}}},
        {"unique_id": "source.verein.crm.unknown", "status": "pass"}]}))
    send = mi.MemoryNotifier()
    out = mc.import_results(cat, sc, p, "dbt", "dirk", send, at=AT)
    assert out["imported"] == 1 and out["skipped"] == ["crm.unknown"]
    r = cat.one("SELECT * FROM check_result WHERE origin = 'dbt'")
    assert (r["asset_id"], r["check_name"], r["status"]) == ("sql://crm/contacts", "freshness", "error")
    assert len(send.sent) == 1 and send.sent[0]["payload"]["check"] == "freshness"


def test_soda_results_import(c09, tmp_path):
    cat, sc = c09["cat"], c09["sc"]
    asset(cat, "sql://crm/contacts", title="crm.contacts")
    monitor(c09)
    p = tmp_path / "soda.json"
    p.write_text(json.dumps({"checks": [{"name": "duplicate_count(email) = 0", "table": "crm.contacts",
                                         "outcome": "fail"}]}))
    assert mc.import_results(cat, sc, p, "soda", "dirk", mi.MemoryNotifier(), at=AT)["imported"] == 1
    assert cat.one("SELECT status FROM check_result WHERE origin = 'soda'")["status"] == "error"


def test_monitor_dry_run(c09):
    asset(c09["cat"], "sql://crm/contacts", age_days=20)
    rep, send = monitor(c09, dry_run=True)
    assert rep["dry_run"] and check(rep, "sql://crm/contacts", "freshness")["status"] == "error"
    for t in ("check_result", "monitored_asset", "incident"):
        assert c09["cat"].one(f"SELECT COUNT(*) AS n FROM {t}")["n"] == 0
    assert send.sent == [] and c09["lineage"].events == []


def test_monitor_schedule_examples():
    s = mc.schedule("duckdb:///.konmari/catalog.duckdb")
    assert s["cron"] == "0 6 * * * konmari --catalog duckdb:///.konmari/catalog.duckdb monitor run"
    assert "OnUnitActiveSec=24h" in s["timer"] and "ExecStart=konmari" in s["service"]
    assert mc.schedule(every_hours=6)["cron"].startswith("0 */6 * * *")


# ---------------------------------------------------------------- alerts and incidents

def test_repeated_failure_does_not_spam(c09):
    asset(c09["cat"], "sql://crm/contacts", age_days=20)
    sent = []
    for n in range(3):
        _, send = monitor(c09, AT + timedelta(hours=n))
        sent += [m for m in send.sent if "freshness" in m["subject"]]
    rows = c09["cat"].query("SELECT * FROM incident WHERE check_name = 'freshness'")
    assert len(rows) == 1 and rows[0]["failure_count"] == 3 and rows[0]["status"] == "open"
    assert len(sent) == 1 and sent[0]["to"] == BEN


def test_personal_data_kept_out_of_alert(c09):
    cat = c09["cat"]
    asset(cat, "sql://crm/customers", title="crm.customers", tier="personal", pd='["name", "contact"]')
    items(cat, "sql://crm/customers", 10, "kunde-4711-mueller")
    for i in range(4):
        cat.execute("INSERT INTO flag (flag_id, run_id, flag_type, method, item_id, related_ids, params, signals,"
                    " created_at, created_by) VALUES (?, 'r', 'duplicate', 'exact', ?, '[]', '{}', '{}', ?, 'x')",
                    (f"f{i}", f"kunde-4711-mueller-{i:04d}", now()))
        cat.execute("INSERT INTO chunk_metadata (subject_id, subject_kind, item_id, run_id, envelope, acl_stale,"
                    " expired, glossary_terms, context_header, complete, status, reasons, updated_at) VALUES"
                    " (?, 'chunk', ?, 'r', '{}', false, false, '[]', '', false, 'needs-review', '[]', ?)",
                    (f"kunde-4711-mueller-{i:04d}#0", f"kunde-4711-mueller-{i:04d}", now()))
    _, send = monitor(c09)
    dup = next(m for m in send.sent if "duplicate_share" in m["subject"])
    assert dup["payload"]["dataset"] == "crm.customers" and dup["payload"]["observed"] == 0.4
    text = json.dumps(send.sent)
    assert "4711" not in text and "mueller" not in text


def test_incident_lifecycle_transitions(c09):
    cat, sc = c09["cat"], c09["sc"]
    asset(cat, "sql://a/x", age_days=20)
    asset(cat, "sql://a/y", age_days=20)
    monitor(c09)
    ix = mi.open_incident(cat, "sql://a/x", "freshness")["incident_id"]
    iy = mi.open_incident(cat, "sql://a/y", "freshness")["incident_id"]
    with pytest.raises(Refused, match="a reason is required"):
        mi.decide(cat, sc, ix, "acknowledge", "ben", "")
    assert mi.decide(cat, sc, ix, "acknowledge", "ben", "export broken, fixing")["status"] == "acknowledged"
    assert mi.decide(cat, sc, ix, "resolve", "ben", "export repaired")["status"] == "resolved"
    assert mi.decide(cat, sc, iy, "accept-risk", "ben", "archive table, no updates expected")["status"] == \
        "accepted_risk"
    with pytest.raises(Refused, match="needs status"):
        mi.decide(cat, sc, ix, "acknowledge", "ben", "again")
    for action in ("acknowledge", "resolve", "accept-risk"):
        ev = fx.events(cat, f"monitor.incident.{action}")
        assert len(ev) == 1 and ev[0]["actor"] == "ben" and ev[0]["details"]["reason"]


def test_escalation_on_silence(c09):
    cat = c09["cat"]
    asset(cat, "sql://crm/contacts", age_days=20, owner="clara")  # owner clara, use_case_owner ben
    _, first = monitor(c09)
    assert [m["to"] for m in first.sent] == ["training@example.org"]
    _, send = monitor(c09, AT + timedelta(days=2))  # Wednesday: 2 business days, not more
    assert not [m for m in send.sent if "escalation" in m["subject"]]
    _, send = monitor(c09, AT + timedelta(days=3))  # Thursday
    esc = [m for m in send.sent if "escalation" in m["subject"]]
    assert len(esc) == 1 and esc[0]["to"] == BEN
    inc = cat.one("SELECT * FROM incident")
    assert inc["escalated_at"] == AT + timedelta(days=3)
    assert len(fx.events(cat, "monitor.incident.escalate")) == 1
    assert mi.business_days(datetime(2026, 10, 23, 9), datetime(2026, 10, 27, 9)) == 2  # Fri -> Tue


def test_auto_resolve_on_recovery(c09):
    cat = c09["cat"]
    asset(cat, "sql://crm/contacts", age_days=20)
    monitor(c09)
    cat.execute("UPDATE source_asset SET last_modified = ?", (AT,))
    rep, _ = monitor(c09, AT + timedelta(hours=1))
    inc = cat.one("SELECT * FROM incident WHERE check_name = 'freshness'")
    assert (inc["status"], inc["decided_by"], inc["resolving_run"]) == ("resolved", "system", rep["run_id"])


# ---------------------------------------------------------------- agent exposure and feedback

def test_stale_asset_flagged_to_agent(c09):
    cat = c09["cat"]
    asset(cat, f"manual://{KB}", freq="P7D", age_days=20)
    monitor(c09, now())
    ctx = access.context(cat, "eval-bot", "test-eval", ["support-team"])
    hit = next(h for h in access.retrieve(cat, ctx, "Jahresbeitrag Erwachsene") if h["item_id"] == "fee-rules")
    assert hit["freshness_status"] == "error" and hit["last_change"].endswith("Z")


def test_long_stale_asset_proposed_for_discard(c09):
    cat = c09["cat"]
    asset(cat, "sharepoint://lib/old", age_days=60)
    items(cat, "sharepoint://lib/old", 2, "old")
    monitor(c09, AT - timedelta(days=31))
    before = cat.query("SELECT * FROM source_asset WHERE asset_uri = 'sharepoint://lib/old'")
    rep, _ = monitor(c09, AT - timedelta(days=1))  # 30 days in error: not yet
    assert rep["discard_candidates"] == []
    rep, _ = monitor(c09, AT)
    cand = rep["discard_candidates"]
    assert len(cand) == 1 and cand[0]["asset"] == "sharepoint://lib/old" and cand[0]["days"] == 31
    props = cat.query("SELECT * FROM proposal ORDER BY item_id")
    assert [p["status"] for p in props] == ["pending", "pending"] and cand[0]["incident_id"] in props[0]["reason"]
    assert {r["state"] for r in cat.query("SELECT state FROM corpus_item WHERE asset_uri = 'sharepoint://lib/old'")} \
        == {"active"}
    assert cat.query("SELECT * FROM source_asset WHERE asset_uri = 'sharepoint://lib/old'") == before
    assert monitor(c09, AT + timedelta(hours=1))[0]["discard_candidates"] == []  # proposed once


def test_scorecard_updated(c09):
    from konmari.scorecard import probes as sp

    cat = c09["cat"]
    asset(cat, "sql://crm/contacts", age_days=20, at=now())
    asset(cat, "sql://crm/fresh", age_days=1, at=now())
    rep, _ = monitor(c09, now())
    rows = {r["asset_uri"]: r for r in cat.query("SELECT * FROM asset_evidence WHERE kind = 'freshness_alert'")}
    assert json.loads(rows["sql://crm/contacts"]["payload"]) == {"status": "error"}
    assert json.loads(rows["sql://crm/fresh"]["payload"]) == {"status": "ok"}
    assert {r["evidence_ref"] for r in rows.values()} == {f"monitor-run:{rep['run_id']}"}
    ctx = sp.Context(cat, {"stale_multiple": 2, "evidence_max_age_days": 90}, at=now())
    a = cat.one("SELECT * FROM source_asset WHERE asset_uri = 'sql://crm/fresh'")
    assert sp.AuthoritativeCurrent().run(a, ctx).level == 2  # an `ok` status does not lower the level
    stale = cat.one("SELECT * FROM source_asset WHERE asset_uri = 'sql://crm/contacts'")
    assert sp.AuthoritativeCurrent().run(stale, ctx).level == 0


def test_email_and_webhook_channels(monkeypatch):
    posted, mails = [], []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        posted.append((req.full_url, json.loads(req.data)))
        return Resp()
    mi.WebhookNotifier("https://chat.example.org/hook", fake_urlopen).send("ben@example.org", "s", {"check": "volume"})
    assert posted == [("https://chat.example.org/hook", {"to": "ben@example.org", "subject": "s", "check": "volume"})]

    class FakeSMTP:
        def __init__(self, host, port):
            self.host = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def login(self, user, pw):
            mails.append(("login", user, pw))

        def send_message(self, msg):
            mails.append((msg["To"], msg["Subject"]))
    monkeypatch.setenv("SMTP_PW", "synthetic")
    mi.EmailNotifier("mail.example.org", 587, "konmari@example.org", "konmari", "SMTP_PW", FakeSMTP).send(
        "ben@example.org", "[konmari] error", {"check": "freshness"})
    assert mails == [("login", "konmari", "synthetic"), ("ben@example.org", "[konmari] error")]
    assert isinstance(mi.notifier({"notify": {"channel": "webhook", "url": "https://x"}}), mi.WebhookNotifier)
    with pytest.raises(Refused, match="not email, webhook or log"):
        mi.notifier({"notify": {"channel": "pigeon"}})
