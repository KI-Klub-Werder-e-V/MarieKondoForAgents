"""c03 readiness-scorecard: scenarios from openspec/changes/c03-joy-test-scorecard/specs/readiness-scorecard/spec.md.
All assets, people and ACLs are synthetic."""

import csv
import json
import random
from datetime import timedelta
from pathlib import Path

import pytest

from konmari.catalog import AppendOnlyError, connect, now
from konmari.charter.store import scope
from konmari.scorecard import engine as se
from konmari.scorecard import probes as sp
from konmari.scorecard import report as sr
from konmari.scorecard import review as sv
from konmari.scorecard import rubric as rb
from konmari.versions import Refused

from conftest import approve_pilot

CID = "verein-faq-assistant"
FIX = Path(__file__).parent / "fixtures" / "scorecard"
CRIT = rb.CRITERIA
CFG = dict(se.DEFAULT_CONFIG)
COLS = ["asset_uri", "title", "system_type", "owner", "steward", "update_frequency", "last_modified", "format",
        "sensitivity_tier", "ropa_ref"]


def ago(days):
    return now() - timedelta(days=days)


def asset(uri="manual://kb/faq", **kw):
    return {"asset_uri": uri, "title": uri.rsplit("/", 1)[-1], "system_type": "manual", "owner": "ben",
            "steward": "clara", "update_frequency": "P1M", "last_modified": ago(5), "format": "text/markdown",
            "sensitivity_tier": "internal", "tier_state": "confirmed", "ropa_ref": None, "last_seen": now()} | kw


def good_evidence(cat, uri, acl_at=None):
    sp.record_evidence(cat, uri, "acl_snapshot", sp.acl_payload(["ben", "clara"]), "acl:snap-1", "dirk", acl_at)
    sp.record_evidence(cat, uri, "glossary_coverage", {"described": True, "mapped": 4, "total": 4}, "gloss:1", "clara")
    sp.record_evidence(cat, uri, "stable_id", {"present": True}, "chunks:1", "dirk")
    sp.record_evidence(cat, uri, "dedup_check", {"open_clusters": 0}, "dedup:1", "dirk")


@pytest.fixture
def joy(engagement, cli, tmp_path, monkeypatch, request):
    """Approved fixture pilot with one csv-import source; `setup(rows)` imports, categorises and hands off."""
    if "pg_url" in request.fixturenames:
        monkeypatch.setenv("KONMARI_CATALOG", request.getfixturevalue("pg_url"))
    monkeypatch.setenv("KONMARI_LINEAGE_FILE", str(tmp_path / "lineage.jsonl"))
    csvf = tmp_path / "assets.csv"
    approve_pilot(cli, [{"id": "manual-assets", "connector": "csv-import", "locator": str(csvf),
                         "categories": ["kb-and-sops", "policies-and-contracts"]}],
                  ["kb-and-sops", "policies-and-contracts"])

    def ok(*args):
        code, out, err = cli(*args)
        assert code == 0, (args, err)
        return out

    ok("metrics", "baseline", "time_to_answer", "--value", 120, "--method", "stopwatch", "--as", "ben")  # score gate

    def setup(rows, categories=None):
        with csvf.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, COLS)
            w.writeheader()
            for r in rows:
                w.writerow({k: (r[k].isoformat() if k == "last_modified" and r[k] else r[k] or "")
                            for k in COLS})
        ok("inventory", "source", "add", "--connector", "csv-import", "--locator", csvf, "--as", "dirk")
        ok("inventory", "import", csvf, "--as", "dirk")
        for r in rows:
            if not r["owner"]:
                ok("inventory", "waive", r["asset_uri"], "--reason", "owner left the club", "--as", "ben")
            ok("categories", "assign", r["asset_uri"], (categories or {}).get(r["asset_uri"], "kb-and-sops"),
               "--as", "ben")
        ok("categories", "order", "--propose", "--as", "ben")
        ok("categories", "approve", "--as", "ben")
        ok("categories", "handoff")
        return connect()

    return {"setup": setup, "ok": ok, "csv": csvf, "lineage": tmp_path / "lineage.jsonl"}


def ctx_for(cat, cfg=None, sources=None):
    return sp.Context(cat, cfg or CFG, {"ben": {}, "clara": {}}, sources)


def run(probe, a, cat, **kw):
    return probe.run(a, ctx_for(cat, **kw))


def tables(cat):
    return {r["table_name"] for r in cat.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')")}


# ---------------------------------------------------------------- 1. catalog schema and rubric

C03 = {"rubric_version", "scorecard_run", "asset_evidence", "criterion_score", "verdict", "verdict_review",
       "category_maturity"}


def _up_down_up(cat):
    assert C03 <= tables(cat)
    cat.rollback("003_c03_joy_test_scorecard.sql")
    assert not C03 & tables(cat) and "source_asset" in tables(cat)
    cat.migrate()
    assert C03 <= tables(cat)


def test_migrate_up_down_up_duckdb(tmp_path):
    _up_down_up(connect(f"duckdb:///{tmp_path}/c.duckdb"))


@pytest.mark.postgres
def test_migrate_up_down_up_postgres(pg_url):
    cat = connect(pg_url)
    _up_down_up(cat)
    rb.publish(cat, rb.load(), "maintainer")
    import psycopg
    with pytest.raises(psycopg.errors.RaiseException):  # database trigger, bypassing the catalog layer
        cat.conn.execute("UPDATE rubric_version SET published_by = 'x'")


def test_default_rubric_validates_against_schema(tmp_path):
    doc = rb.load()
    assert [c["id"] for c in doc["criteria"]] == CRIT
    assert rb.gates(doc) == {"authoritative_current", "permissions_explicit"}
    assert all(set(c["levels"]) == {"0", "1", "2", "3"} and set(c["standards"]) == {"dama", "iso25012", "fair"}
               for c in doc["criteria"])
    bad = tmp_path / "bad.yaml"
    bad.write_text("rubric_version: x\nscale: {}\ncriteria: []\n")
    with pytest.raises(Refused):
        rb.load(bad)


def test_published_rubric_cannot_be_edited(tmp_path):
    cat = connect(f"duckdb:///{tmp_path}/c.duckdb")
    doc = rb.load()
    rb.publish(cat, doc, "maintainer")
    rb.publish(cat, doc, "maintainer")  # identical content: no-op
    descr = json.loads(json.dumps(doc))
    descr["criteria"][0]["levels"]["2"] = "changed descriptor"
    with pytest.raises(rb.RubricImmutable, match="new rubric_version"):
        rb.publish(cat, descr, "maintainer")
    doc["criteria"][1]["gate"] = True
    with pytest.raises(rb.RubricImmutable, match="new rubric_version"):
        rb.publish(cat, doc, "maintainer")
    assert rb.get(cat, "konmari-joy-1")["doc"] == rb.load()
    with pytest.raises(AppendOnlyError):
        cat.execute("UPDATE rubric_version SET content = '{}'")
    doc["rubric_version"] = "konmari-joy-2"
    assert rb.publish(cat, doc, "maintainer")["version"] == "konmari-joy-2"


def test_default_rubric_is_available(joy):
    cat = joy["setup"]([asset()])
    out = joy["ok"]("score", "--as", "ben")
    assert "rubric konmari-joy-1" in out
    row = rb.get(cat, "konmari-joy-1")
    assert row and rb.gates(row["doc"]) == {"authoritative_current", "permissions_explicit"}
    assert [c["id"] for c in row["doc"]["criteria"]] == CRIT
    assert all(all(c["levels"][k].strip() for k in "0123") for c in row["doc"]["criteria"])
    assert cat.one("SELECT rubric_version FROM scorecard_run")["rubric_version"] == "konmari-joy-1"


def test_scores_reference_rubric_version(joy):
    cat = joy["setup"]([asset(), asset("manual://kb/sop")])
    se.score(cat, actor="ben")
    groups = cat.query("SELECT rubric_version, COUNT(*) AS n FROM criterion_score GROUP BY rubric_version")
    assert groups == [{"rubric_version": "konmari-joy-1", "n": 12}]


def test_config_override_recorded_in_run(joy):
    cat = joy["setup"]([asset()])
    Path("scorecard.yaml").write_text("stale_multiple: 3\nevidence_max_age_days: 14\n")
    s = se.score(cat, actor="ben", cfg=se.load_config())
    cfg = json.loads(cat.one("SELECT config FROM scorecard_run WHERE run_id = ?", (s["run_id"],))["config"])
    assert cfg == CFG | {"stale_multiple": 3, "evidence_max_age_days": 14}
    Path("scorecard.yaml").write_text("keep_lvl: 2\n")
    with pytest.raises(Refused, match="unknown setting"):
        se.load_config()


# ---------------------------------------------------------------- 2. pilot scoping

def test_out_of_scope_asset_is_skipped(joy, cli):
    cat = joy["setup"]([asset(), asset("manual://finance/budget")],
                       {"manual://finance/budget": "financial-ops-metrics"})
    code, out, err = cli("score", "--as", "ben")
    assert code == 0, err
    assert "out of scope: manual://finance/budget (category 'financial-ops-metrics' is not in the pilot" in out
    assert not cat.query("SELECT * FROM criterion_score WHERE asset_uri = 'manual://finance/budget'")
    assert not cat.query("SELECT * FROM verdict WHERE asset_uri = 'manual://finance/budget'")
    run = cat.one("SELECT * FROM scorecard_run")
    assert run["charter_id"] == CID and "manual://finance/budget" in run["summary"]


def test_no_active_charter(engagement, cli):
    code, out, err = cli("score")
    assert code == 1 and "no approved pilot charter for 'charter.yaml'" in err
    cat = connect()
    assert cat.one("SELECT COUNT(*) AS n FROM criterion_score")["n"] == 0
    assert cat.one("SELECT COUNT(*) AS n FROM verdict")["n"] == 0


# ---------------------------------------------------------------- 3. evidence probes

def test_probe_interface():
    for p in sp.PROBES:
        assert p.name and p.criterion in CRIT and callable(p.run)
    assert sorted(p.criterion for p in sp.PROBES) == sorted(CRIT)


def test_stale_asset_detected(cat):
    r = run(sp.AuthoritativeCurrent(), asset(last_modified=ago(75), update_frequency="P30D"), cat)
    assert r.level is not None and r.level <= 1
    assert r.evidence["expected_frequency"] == "P30D" and r.evidence["last_modified"]
    sp.record_evidence(cat, "manual://kb/old", "superseded", {}, "dedup:sup-1", "dirk")
    assert run(sp.AuthoritativeCurrent(), asset("manual://kb/old"), cat).level == 0


def test_missing_owner_yields_level_0(cat):
    assert run(sp.OwnedMaintained(), asset(owner=None), cat).level == 0
    assert run(sp.OwnedMaintained(), asset(), cat).level == 3
    assert run(sp.OwnedMaintained(), asset(owner="someone.else@example.org"), cat).level == 1


@pytest.mark.parametrize("mapped,level", [(0, 1), (2, 2), (4, 3)])
def test_glossary_coverage_levels(cat, mapped, level):
    sp.record_evidence(cat, "manual://kb/faq", "glossary_coverage", {"described": True, "mapped": mapped, "total": 4},
                       "gloss:1", "clara")
    assert run(sp.MeaningClear(), asset(), cat).level == level


def test_missing_evidence(cat):
    r = run(sp.RetrievablePrecise(), asset(), cat)
    assert r.level is None
    levels = dict.fromkeys(CRIT, 3) | {"retrievable_precise": None}
    assert se.decide(levels, set(), CFG)["proposed"] != "keep"


def test_open_cluster_yields_level_le_1(cat):
    sp.record_evidence(cat, "manual://kb/faq", "dedup_check", {"open_clusters": 1, "cluster_ids": ["c-7"]},
                       "dedup:1", "dirk")
    assert run(sp.ConflictsResolved(), asset(), cat).level <= 1


def test_broad_acl_detected(cat):
    captured = ago(1)
    sp.record_evidence(cat, "manual://kb/faq", "acl_snapshot",
                       sp.acl_payload(["Everyone except external users", "ben"]), "acl:spo-dag-42", "dirk", captured)
    r = run(sp.PermissionsExplicit(), asset(), cat)
    assert r.level == 0 and r.evidence["broad_grants"] == ["eeeu"]
    assert r.evidence_ref == "acl:spo-dag-42" and r.evidence["captured_at"] == captured.isoformat()


class FakeAclSource:
    kind = "acl_snapshot"

    def __init__(self):
        self.captures = []

    def capture(self, a):
        self.captures.append(a["asset_uri"])
        return sp.acl_payload(["ben"]), "acl:fresh", now()


def test_expired_evidence(cat):
    sp.record_evidence(cat, "manual://kb/faq", "acl_snapshot", sp.acl_payload(["ben"]), "acl:old", "dirk", ago(10))
    res, stale = sp.run_probe(sp.PermissionsExplicit(), asset(), ctx_for(cat))
    assert stale and res.level is None and res.evidence["stale_evidence"]
    src = FakeAclSource()
    res, stale = sp.run_probe(sp.PermissionsExplicit(), asset(), ctx_for(cat, sources=[src]))
    assert src.captures == ["manual://kb/faq"] and not stale and res.level == 3 and res.evidence_ref == "acl:fresh"


def test_personal_data_asset_evidence(joy, cli):
    secret = ["Max Mustermann", "max.mustermann@example.org", "Erika Musterfrau"]
    a = asset("manual://crm/members", sensitivity_tier="personal", ropa_ref="ropa-3")
    cat = joy["setup"]([a])
    code, _, err = cli("score", "evidence", a["asset_uri"], "--kind", "acl_snapshot", "--data",
                       json.dumps({"principals": secret}), "--ref", "acl:members", "--as", "dirk")
    assert code == 0, err
    with pytest.raises(Refused, match="not free text"):
        sp.record_evidence(cat, a["asset_uri"], "freshness_alert", {"status": "Max Mustermann wohnt in Musterstadt"},
                           "alert:1", "dirk")
    se.score(cat, actor="ben")
    stored = "\n".join(repr(r) for t in ("asset_evidence", "criterion_score", "verdict", "verdict_review")
                       for r in cat.query(f"SELECT * FROM {t}"))
    assert stored and not any(s.lower() in stored.lower() for s in secret)


def test_soda_and_odcs_adapters(joy, cli):
    cat = joy["setup"]([asset("manual://crm/main.tickets", system_type="sql")])
    code, out, err = cli("score", "ingest", "--soda", FIX / "soda-results.json", "--as", "dirk")
    assert code == 0, err
    rows = cat.query("SELECT payload, evidence_ref, captured_at FROM asset_evidence WHERE kind = 'soda_check'"
                     " ORDER BY payload")
    assert [json.loads(r["payload"])["check_type"] for r in rows] == ["duplicate_count", "freshness"]
    assert "skipped: check on table 'unknown_table'" in err
    code, out, err = cli("score", "ingest", "--odcs", FIX / "odcs-contract.yaml", "--as", "dirk")
    assert code == 0, err
    g = cat.one("SELECT * FROM asset_evidence WHERE kind = 'glossary_coverage'")
    assert json.loads(g["payload"]) == {"described": True, "mapped": 1, "total": 2, "contract_status": "active"}
    assert g["evidence_ref"] == "odcs:tickets-contract@1.0.0"
    a = asset("manual://crm/main.tickets")
    assert run(sp.AuthoritativeCurrent(), a, cat).level == 3  # passing freshness check = monitored
    assert run(sp.MeaningClear(), a, cat).level == 2


# ---------------------------------------------------------------- 4. verdict engine

@pytest.mark.parametrize("kw,reason,ref", [
    ({"irrelevant_ref": "evidence:1"}, "irrelevant", "evidence:1"),
    ({"winner": "manual://kb/faq-v2"}, "redundant", "manual://kb/faq-v2"),
    ({"stale_ratio": 2.5, "owner_active": False}, "stale_unowned", None),
])
def test_discard_reasons(kw, reason, ref):
    v = se.decide(dict.fromkeys(CRIT, 3), {"permissions_explicit"}, CFG, **kw)
    assert (v["proposed"], v["reason"], v["reason_ref"]) == ("discard", reason, ref)
    assert se.decide(dict.fromkeys(CRIT, 3), set(), CFG, stale_ratio=2.5, owner_active=True)["proposed"] == "keep"


def test_all_criteria_meet_the_bar():
    assert se.decide(dict(zip(CRIT, [2, 3, 2, 3, 2, 2])), {"permissions_explicit"}, CFG)["proposed"] == "keep"


def test_gate_blocks_keep():
    levels = dict.fromkeys(CRIT, 3) | {"permissions_explicit": 0}
    v = se.decide(levels, {"authoritative_current", "permissions_explicit"}, CFG)
    assert v["proposed"] == "fix"
    assert v["fix_items"] == [{"criterion": "permissions_explicit", "level": 0, "capability": "access-control"}]
    # a gate blocks keep even when a pilot lowers the keep level
    assert se.decide(levels, {"permissions_explicit"}, CFG | {"keep_level": 0})["proposed"] == "fix"


def test_fix_routing_covers_all_criteria():
    assert set(se.FIX_ROUTES) == set(CRIT)
    v = se.decide(dict.fromkeys(CRIT), set(), CFG)
    assert [i["criterion"] for i in v["fix_items"]] == CRIT and all(i["level"] == "unknown" for i in v["fix_items"])


def test_property_no_keep_with_gate_zero_or_unknown():
    rnd = random.Random(3)
    gates = {"authoritative_current", "permissions_explicit"}
    for _ in range(5000):
        levels = {c: rnd.choice([None, 0, 1, 2, 3]) for c in CRIT}
        cfg = CFG | {"keep_level": rnd.choice([0, 1, 2, 3])}
        v = se.decide(levels, gates, cfg)
        if any(levels[g] == 0 for g in gates) or None in levels.values():
            assert v["proposed"] != "keep", levels


# ---------------------------------------------------------------- 5. human review and audit

def _proposed(joy, rows=None, evidence=True):
    rows = rows or [asset()]
    cat = joy["setup"](rows)
    for r in rows:
        if evidence:
            good_evidence(cat, r["asset_uri"])
    se.score(cat, actor="ben")
    return cat, scope(CID, cat)


def audits(cat, prefix):
    return [r | {"d": json.loads(r["details"])} for r in cat.query(
        "SELECT * FROM audit_event WHERE action LIKE ? ORDER BY ts", (prefix + "%",))]


def test_reviewer_accepts(joy, cli):
    cat, _ = _proposed(joy)
    code, out, err = cli("review", "accept", "manual://kb/faq", "--as", "clara")
    assert code == 0, err
    st = sv.status(cat, "manual://kb/faq")
    assert (st["status"], st["effective"]) == ("approved", "keep")
    a = audits(cat, "scorecard.review.accept")[-1]
    assert a["actor"] == "clara" and a["ts"] and a["result"] == "approved"


def test_unauthorised_reviewer(joy, cli):
    cat, _ = _proposed(joy)
    code, _, err = cli("review", "accept", "manual://kb/faq", "--as", "eva")
    assert code == 1 and "not a reviewer" in err and "logged" in err
    assert sv.status(cat, "manual://kb/faq")["status"] == "proposed"
    denied = audits(cat, "scorecard.review.accept")[-1]
    assert denied["actor"] == "eva" and denied["result"] == "denied"


def test_override_without_justification(joy, cli):
    cat, _ = _proposed(joy, evidence=False)
    assert sv.status(cat, "manual://kb/faq")["proposed"] == "fix"
    code, _, err = cli("review", "override", "manual://kb/faq", "--to", "keep", "--as", "clara")
    assert code == 1 and "justification is required" in err
    assert sv.status(cat, "manual://kb/faq")["status"] == "proposed"
    code, _, err = cli("review", "override", "manual://kb/faq", "--to", "keep", "--justification",
                       "evidence collected manually, see ticket 12", "--as", "clara")
    assert code == 0, err
    assert sv.effective_verdict(cat, "manual://kb/faq") == "keep"


def test_reversal(joy, cli):
    cat, sc = _proposed(joy, evidence=False)
    sp.record_evidence(cat, "manual://kb/faq", "relevance", {"relevant": False, "marked_by": "clara"}, "expert:clara",
                       "clara")
    se.score(cat, actor="ben")
    hist0 = len(se.reviews(cat, "manual://kb/faq"))  # fix proposal, then the discard proposal
    sv.accept(cat, sc, "manual://kb/faq", "clara")
    sv.reverse(cat, sc, "manual://kb/faq", "fix", "still needed for training-time questions", "ben")
    hist = se.reviews(cat, "manual://kb/faq")[hist0 - 1:]
    assert [(h["action"], h["to_verdict"]) for h in hist] == [("propose", "discard"), ("accept", "discard"),
                                                              ("reverse", "fix")]
    assert sv.effective_verdict(cat, "manual://kb/faq") == "fix"
    with pytest.raises(AppendOnlyError):
        cat.execute("DELETE FROM verdict_review")


def test_review_event_payload(joy):
    cat, sc = _proposed(joy, evidence=False)
    sv.override(cat, sc, "manual://kb/faq", "discard", "duplicate of the printed handbook", "clara")
    d = audits(cat, "scorecard.review.override")[-1]["d"]
    assert (d["actor"], d["from"], d["to"], d["rubric_version"]) == ("clara", "fix", "discard", "konmari-joy-1")
    events = [json.loads(line) for line in joy["lineage"].read_text().splitlines()]
    runs = [e for e in events if e.get("job", {}).get("name") == "scorecard.score"]
    assert [e["eventType"] for e in runs] == ["START", "COMPLETE"]
    assert runs[1]["run"]["facets"]["konmari"]["rubric_version"] == "konmari-joy-1"


def test_bulk_accept_skips_fix_and_discard(joy, cli):
    rows = [asset("manual://kb/a"), asset("manual://kb/b"), asset("manual://kb/c")]
    cat = joy["setup"](rows)
    good_evidence(cat, "manual://kb/a")
    good_evidence(cat, "manual://kb/c")
    sp.record_evidence(cat, "manual://kb/c", "relevance", {"relevant": False, "marked_by": "clara"}, "expert:clara",
                       "clara")
    se.score(cat, actor="ben")
    code, out, err = cli("review", "accept", "--all-keep", "--as", "clara")
    assert code == 0 and "accepted 1 keep proposal(s); 2 need a one-by-one review" in out
    assert [sv.status(cat, r["asset_uri"])["status"] for r in rows] == ["approved", "proposed", "proposed"]


class MockConnector(FakeAclSource):
    """A source connector: reads are allowed, any write call is recorded."""

    def __init__(self):
        super().__init__()
        self.writes = []

    def __getattr__(self, name):
        if name.startswith(("set", "write", "delete", "move", "update", "archive")):
            return lambda *a, **k: self.writes.append(name)
        raise AttributeError(name)


def test_source_untouched(joy):
    rows = [asset(), asset("manual://kb/old")]
    cat = joy["setup"](rows)
    before_csv = joy["csv"].read_text()
    before = cat.query("SELECT * FROM source_asset ORDER BY asset_uri")
    for r in rows:
        good_evidence(cat, r["asset_uri"], acl_at=ago(30))
    sp.record_evidence(cat, "manual://kb/old", "relevance", {"relevant": False, "marked_by": "clara"}, "expert:clara",
                       "clara")
    conn = MockConnector()
    se.score(cat, actor="ben", sources=[conn])
    sc = scope(CID, cat)
    sv.accept(cat, sc, "manual://kb/old", "clara")
    assert sv.effective_verdict(cat, "manual://kb/old") == "discard"
    assert conn.captures and conn.writes == []
    assert cat.query("SELECT * FROM source_asset ORDER BY asset_uri") == before
    assert joy["csv"].read_text() == before_csv


# ---------------------------------------------------------------- 6. re-scoring

def test_fix_completed(joy, cli):
    cat = joy["setup"]([asset()])
    sp.record_evidence(cat, "manual://kb/faq", "acl_snapshot", sp.acl_payload(["ben"]), "acl:1", "dirk")
    sp.record_evidence(cat, "manual://kb/faq", "stable_id", {"present": True}, "chunks:1", "dirk")
    sp.record_evidence(cat, "manual://kb/faq", "dedup_check", {"open_clusters": 0}, "dedup:1", "dirk")
    sp.record_evidence(cat, "manual://kb/faq", "glossary_coverage", {"described": True, "mapped": 0, "total": 4},
                       "gloss:1", "clara")
    se.score(cat, actor="ben")
    sc = scope(CID, cat)
    sv.accept(cat, sc, "manual://kb/faq", "clara")
    assert sv.status(cat, "manual://kb/faq")["status"] == "approved"
    # 06 business-glossary maps the remaining terms and marks the fix item complete
    sp.record_evidence(cat, "manual://kb/faq", "glossary_coverage", {"described": True, "mapped": 4, "total": 4},
                       "gloss:2", "clara")
    code, out, err = cli("score", "fix-done", "manual://kb/faq", "meaning_clear", "--as", "clara")
    assert code == 0, err
    assert "proposed keep: manual://kb/faq" in out
    st = sv.status(cat, "manual://kb/faq")
    assert (st["proposed"], st["status"]) == ("keep", "proposed")
    assert cat.one("SELECT run_trigger FROM scorecard_run ORDER BY started_at DESC LIMIT 1")["run_trigger"] == \
        "fix_completed"
    # an unchanged re-score keeps the review status
    sv.accept(cat, sc, "manual://kb/faq", "clara")
    se.rescore(cat, "manual://kb/faq", "freshness_alert", "dirk")
    assert sv.status(cat, "manual://kb/faq")["status"] == "approved"


def test_inventory_change_triggers_rescore(joy, cli):
    cat = joy["setup"]([asset(), asset("manual://kb/sop")])
    se.score(cat, actor="ben")
    assert se.changed_assets(cat, scope(CID, cat)) == set()
    joy["ok"]("inventory", "set", "manual://kb/sop", "--steward", "finn", "--as", "ben")
    code, out, _ = cli("score", "--changed", "--as", "ben")
    assert code == 0 and "1 scored" in out and "manual://kb/sop" in out


# ---------------------------------------------------------------- 7. category maturity and reports

def test_category_rollup_computed():
    levels = [dict.fromkeys(CRIT, 2) for _ in range(9)] + [dict.fromkeys(CRIT, 2) | {"permissions_explicit": 1}]
    access = next(m for m in se.rollup(levels, CFG) if m["aspect"] == "Access")
    assert (access["share"], access["state"], access["assets"]) == (0.9, "agent_ready", 10)
    upd = next(m for m in se.rollup(levels, CFG) if m["aspect"] == "Update mechanism")
    assert upd["state"] == "poorly_prepared"  # needs authoritative_current = 3 (automated)


def test_unknown_scores_count_against_readiness():
    levels = [dict.fromkeys(CRIT, 3) for _ in range(5)] + [dict.fromkeys(CRIT, 3) | {"permissions_explicit": None}]
    access = next(m for m in se.rollup(levels, CFG) if m["aspect"] == "Access")
    assert (access["meeting"], access["share"], access["state"]) == (5, 0.8333, "agent_ready")
    assert se.rollup(levels[:1] + [dict.fromkeys(CRIT)] * 2, CFG)[2]["state"] == "poorly_prepared"


def test_report_generated(joy, cli):
    cat, _ = _proposed(joy, [asset(), asset("manual://kb/sop")])
    code, out, err = cli("scorecard", "report", "--format", "json")
    assert code == 0, err
    rep = json.loads(out)
    assert sr.validate(rep) == []
    assert {a["asset_uri"] for a in rep["assets"]} == {"manual://kb/faq", "manual://kb/sop"}
    assert all(len(a["criteria"]) == 6 and a["verdict"]["proposed"] == "keep" for a in rep["assets"])
    assert rep["charter_id"] == CID and rep["rubric_version"] == "konmari-joy-1" and rep["aggregation_rule"]
    code, out, _ = cli("scorecard", "report", "--format", "csv")
    assert code == 0 and out.count("\n") == 3 and "permissions_explicit_evidence_at" in out.splitlines()[0]
    code, out, _ = cli("scorecard", "report", "--format", "csv", "--level", "category")
    assert code == 0 and out.count("\n") == 7
    code, out, _ = cli("scorecard", "report")
    assert code == 0 and "Aggregation rule:" in out


def _snapshot_report():
    crit = {c: {"level": lv, "probe": "p", "evidence_ref": "r", "evidence_at": "2026-10-20T08:00:00",
                "stale_evidence": False} for c, lv in zip(CRIT, [2, 3, "unknown", 2, 1, 0])}
    return {"charter_id": CID, "charter_version": 1, "run_id": "run-1", "rubric_version": "konmari-joy-1",
            "generated_at": "2026-10-24T09:00:00", "config": CFG, "aggregation_rule": se.aggregation_rule(CFG),
            "aspect_criteria": {},
            "assets": [{"asset_uri": "manual://kb/faq", "title": "faq", "category": "kb-and-sops", "criteria": crit,
                        "rubric_version": "konmari-joy-1", "run_id": "run-1",
                        "verdict": {"proposed": "fix", "reason": None, "reason_ref": None, "status": "proposed",
                                    "effective": None, "fix_items": se.decide(
                                        {c: None if v["level"] == "unknown" else v["level"] for c, v in crit.items()},
                                        {"permissions_explicit"}, CFG)["fix_items"]}}],
            "categories": [{"category": "kb-and-sops", "assets": 1, "verdicts": {"keep": 0, "fix": 0, "discard": 0,
                                                                                   "unreviewed": 1},
                            "aspects": se.rollup([{c: None if v["level"] == "unknown" else v["level"]
                                                   for c, v in crit.items()}], CFG)}]}


def test_report_never_shows_aggregate_without_criteria():
    rep = _snapshot_report()
    assert sr.validate(rep) == []
    md = sr.render_md(rep)
    assert md == (FIX / "report.snapshot.md").read_text(encoding="utf-8")
    row = next(line for line in md.splitlines() if line.startswith("| manual://kb/faq"))
    assert row.split(" | ")[2:9] == ["2", "3", "unknown", "2", "1", "0", "unknown"]  # six levels, then the min


def test_cross_rubric_comparison_refused(joy, cli, tmp_path):
    cat = joy["setup"]([asset()])
    a = se.score(cat, actor="ben")["run_id"]
    custom = rb.load()
    custom["rubric_version"] = "verein-joy-2"
    custom["criteria"][2]["gate"] = True
    p = tmp_path / "rubric.yaml"
    p.write_text(json.dumps(custom))
    b = se.score(cat, actor="ben", rubric_path=p)["run_id"]
    code, _, err = cli("scorecard", "compare", a, b)
    assert code == 1 and "--allow-rubric-mismatch" in err
    code, out, _ = cli("scorecard", "compare", a, b, "--allow-rubric-mismatch")
    assert code == 0 and out.startswith("WARNING: rubric versions differ")


# ---------------------------------------------------------------- 8. end to end

def _e2e(joy, cli):
    """One category, 10 assets covering keep, fix and discard: score -> review -> report."""
    rows = [asset(f"manual://kb/keep-{i}") for i in range(5)] + [
        asset("manual://kb/eeeu"),                                                     # fix: gate
        asset("manual://kb/no-glossary"),                                              # fix: unknown meaning
        asset("manual://kb/faq-copy"),                                                 # discard: redundant
        asset("manual://kb/old-flyer"),                                                # discard: irrelevant
        asset("manual://kb/orphan", owner=None, last_modified=ago(90)),                # discard: stale_unowned
    ]
    cat = joy["setup"](rows)
    for r in rows:
        if r["asset_uri"] != "manual://kb/no-glossary":
            good_evidence(cat, r["asset_uri"])
        else:
            sp.record_evidence(cat, r["asset_uri"], "acl_snapshot", sp.acl_payload(["ben"]), "acl:1", "dirk")
            sp.record_evidence(cat, r["asset_uri"], "stable_id", {"present": True}, "chunks:1", "dirk")
            sp.record_evidence(cat, r["asset_uri"], "dedup_check", {"open_clusters": 0}, "dedup:1", "dirk")
    ok = joy["ok"]
    ok("score", "evidence", "manual://kb/eeeu", "--kind", "acl_snapshot", "--data",
       json.dumps({"principals": ["Everyone except external users"]}), "--ref", "acl:dag-7", "--as", "dirk")
    ok("score", "evidence", "manual://kb/faq-copy", "--kind", "dedup_check", "--data",
       json.dumps({"open_clusters": 1, "winner": "manual://kb/keep-0", "cluster_ids": ["c-1"]}), "--ref", "dedup:c-1",
       "--as", "dirk")
    ok("score", "irrelevant", "manual://kb/old-flyer", "--reason", "event ended in 2024", "--as", "clara")
    out = ok("score", "--as", "ben")
    assert "10 scored, 0 out of scope" in out
    assert "proposed discard (redundant: manual://kb/keep-0): manual://kb/faq-copy" in out
    assert "proposed discard (irrelevant: evidence:" in out
    assert "proposed discard (stale_unowned): manual://kb/orphan" in out
    assert "proposed fix: manual://kb/eeeu; fix: permissions_explicit" in out
    assert "proposed fix: manual://kb/no-glossary; fix: meaning_clear" in out

    assert "accepted 5 keep proposal(s); 5 need a one-by-one review" in ok("review", "accept", "--all-keep", "--as",
                                                                            "clara")
    for u in ("manual://kb/eeeu", "manual://kb/no-glossary", "manual://kb/faq-copy", "manual://kb/old-flyer"):
        ok("review", "accept", u, "--as", "clara")
    ok("review", "override", "manual://kb/orphan", "--to", "fix", "--justification",
       "new owner agreed in steering meeting", "--as", "ben")
    hist = ok("review", "list", "manual://kb/orphan")
    assert "override by ben: discard -> fix" in hist and "status approved" in hist
    sc = scope(CID, cat)
    from konmari.categories import store as cats
    assert cats.step_status(cat, sc.charter_id, "kb-and-sops", "score")["status"] == "complete"

    rep = json.loads(ok("scorecard", "report", "--format", "json"))
    assert sr.validate(rep) == []
    eff = {a["asset_uri"]: a["verdict"]["effective"] for a in rep["assets"]}
    assert eff == {**{f"manual://kb/keep-{i}": "keep" for i in range(5)}, "manual://kb/eeeu": "fix",
                   "manual://kb/no-glossary": "fix", "manual://kb/faq-copy": "discard",
                   "manual://kb/old-flyer": "discard", "manual://kb/orphan": "fix"}
    (c,) = rep["categories"]
    assert c["verdicts"] == {"keep": 5, "fix": 3, "discard": 2, "unreviewed": 0}
    shares = {m["aspect"]: (m["share"], m["state"]) for m in c["aspects"]}
    assert shares["Access"] == (0.9, "agent_ready")              # only the EEEU asset fails
    assert shares["Metadata"] == (0.8, "agent_ready")            # no glossary + no owner
    assert shares["Update mechanism"] == (0.0, "poorly_prepared")  # nothing monitored automatically yet
    md = ok("scorecard", "report")
    assert "| manual://kb/eeeu | kb-and-sops | 2 | 3 | 3 | 2 | 2 | 0 | 0 | fix | approved fix |" in md
    stored = cat.query("SELECT aspect, share FROM category_maturity WHERE aspect = 'Access'")
    assert stored == [{"aspect": "Access", "share": 0.9}]


def test_fixture_pilot_end_to_end_duckdb(joy, cli):
    _e2e(joy, cli)


@pytest.mark.postgres
def test_fixture_pilot_end_to_end_postgres(pg_url, joy, cli):
    _e2e(joy, cli)
