"""agent-evaluation (c09): golden items and sets, runs through the agent's MCP interface, retrieval, grounding,
citation and abstention scoring, judge calibration, regression gates, baselines and scorecard feedback.
Synthetic data and an in-process stub agent only."""

import csv
import json
from datetime import date

import pytest

import c09_fixture as fx
from c09_fixture import KB, PRINCIPAL, SUPPORT
from konmari.catalog import new_id, now
from konmari.evaluate import gate as eg
from konmari.evaluate import golden as gd
from konmari.evaluate import judge as jd
from konmari.evaluate import run as er
from konmari.governance import access
from konmari.versions import Refused

C09 = {"golden_item", "golden_set", "eval_run", "eval_result", "citation_check", "judge_calibration", "baseline",
       "monitored_asset", "check_result", "incident", "drift_result", "category_focus"}


def _tables(cat):
    return {r["table_name"] for r in cat.query("SELECT table_name FROM information_schema.tables")}


def test_c09_migration_up_down(gcat):
    assert C09 <= _tables(gcat)
    gcat.rollback("009_c09_evaluate_and_monitor.sql")
    assert not C09 & _tables(gcat) and "agent_index" in _tables(gcat)
    gcat.migrate()
    assert C09 <= _tables(gcat)


# ---------------------------------------------------------------- helpers

def run_with(c09, overrides=None, judge=None, timeout=5, **kw):
    srv, calls = fx.stub_agent(fx.responder(overrides), **kw)
    r = er.run(c09["cat"], c09["sc"], er.McpAgent(srv, timeout=timeout), PRINCIPAL, "faq-bot@1.4", "ben", judge)
    return r, calls


def result(cat, run_id, item_id):
    return next(r for r in er.results(cat, run_id) if r["item_id"] == item_id)


def calibrated(cat, sc, judge=jd.LexicalJudge()):
    rows = [{"item_id": f"cal{n}", "human": "pass", "judge": "pass"} for n in range(50)]
    return jd.calibrate(cat, sc, rows, judge.model, judge.version, "clara")


def fake_run(cat, sc, agg, status="completed", judge_status="calibrated", categories=None):
    rid = new_id()[:12]
    cat.execute("INSERT INTO eval_run VALUES (?, ?, 1, 'a1', '{}', 'konmari-lexical', '1', ?, ?, ?, '[]', ?, ?, ?,"
                " 'ben')", (rid, sc.charter_id, judge_status, status, "boom" if status == "failed" else None,
                            json.dumps({"run": agg, "categories": categories or {}}), now(), now()))
    return rid


# ---------------------------------------------------------------- golden items

def test_item_missing_a_required_field_is_rejected(c09):
    cat, sc, tmp = c09["cat"], c09["sc"], c09["tmp"]
    bad = [fx.item("x1", ref=""), fx.item("x2", sources=[]), fx.item("ok1")]
    res = gd.import_items(cat, sc, fx.write_items(tmp / "i.yaml", bad), "clara")
    assert "x1: reference_answer: required field is missing (expected behaviour 'answer')" in res["errors"]
    assert "x2: expected_sources: required field is missing (expected behaviour 'answer')" in res["errors"]
    assert {r["item_id"] for r in cat.query("SELECT item_id FROM golden_item")} == {"ok1"}


def test_item_import_from_csv(c09):
    cat, sc, tmp = c09["cat"], c09["sc"], c09["tmp"]
    p = tmp / "items.csv"
    with p.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "question", "origin", "category", "expected_behaviour", "reference_answer",
                    "expected_sources"])
        w.writerow(["q1", "Wie hoch ist der Beitrag?", "domain_expert", KB, "answer", "120 Euro", "fee-rules;hall-times"])
        w.writerow(["q2", "Wer gewinnt 2030?", "synthetic", KB, "abstain", "", ""])
        w.writerow(["q3", "Was?", "domain_expert", "no-such-category", "answer", "x", "fee-rules"])
    res = gd.import_items(cat, sc, p, "clara")
    assert res["stored"] == ["q1 v1", "q2 v1"]
    assert res["errors"] == ["q3: category: 'no-such-category' is not in the category map"]
    assert gd.latest(cat, "q1")["expected_sources"] == ["fee-rules", "hall-times"]


def test_personal_data_masked_on_import(c09):
    cat, sc, tmp = c09["cat"], c09["sc"], c09["tmp"]
    raw = "Frau Petra Schulz mit Kundennummer KD-482913 fragt, wann der Beitrag fällig ist."
    it = fx.item("u1", origin="user_log", question=raw)
    it["asked_by"] = "petra.schulz@example.de"  # a user identity in the log export is never stored
    gd.import_items(cat, sc, fx.write_items(tmp / "u.yaml", [it]), "clara", today=date(2026, 10, 24))
    row = gd.latest(cat, "u1")
    assert row["question"] == "Frau <PERSON> mit Kundennummer <CUSTOMER_NUMBER> fragt, wann der Beitrag fällig ist."
    assert row["retention_until"] == date(2027, 10, 24)
    dump = repr(cat.query("SELECT * FROM golden_item")) + repr(cat.query("SELECT * FROM audit_event"))
    for value in ("Petra", "Schulz", "482913", "petra.schulz"):
        assert value not in dump


def test_editing_creates_a_new_version(c09):
    cat, sc, tmp = c09["cat"], c09["sc"], c09["tmp"]
    gd.import_items(cat, sc, fx.write_items(tmp / "a.yaml", [fx.item("e1")]), "clara")
    gd.approve(cat, sc, "e1", "clara")
    res = gd.import_items(cat, sc, fx.write_items(tmp / "b.yaml", [fx.item("e1", ref="120 EUR pro Jahr")]), "clara")
    assert res["stored"] == ["e1 v2"]
    rows = cat.query("SELECT version, status, reference_answer, approved_by FROM golden_item WHERE item_id = 'e1'"
                     " ORDER BY version")
    assert rows == [{"version": 1, "status": "approved", "reference_answer": "120 Euro", "approved_by": "clara"},
                    {"version": 2, "status": "draft", "reference_answer": "120 EUR pro Jahr", "approved_by": None}]
    assert gd.import_items(cat, sc, tmp / "b.yaml", "clara")["unchanged"] == ["e1"]


def test_only_domain_experts_or_source_owners_approve(c09):
    cat, sc, tmp = c09["cat"], c09["sc"], c09["tmp"]
    gd.import_items(cat, sc, fx.write_items(tmp / "a.yaml", [fx.item("e1")]), "clara")
    with pytest.raises(Refused, match="not a domain expert or source owner"):
        gd.approve(cat, sc, "e1", "finn")


# ---------------------------------------------------------------- golden set composition

def test_too_few_negative_items(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"], answer=54, abstain=6, activate=False)
    with pytest.raises(Refused) as e:
        gd.activate(cat, sc, "ben")
    assert "6 items expect abstain or flag_conflict, 12 such items are required" in e.value.reasons
    assert fx.events(cat, "eval.set.activate")[-1]["result"] == "refused"


def test_too_few_items_refused(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"], answer=30, abstain=10, activate=False)
    with pytest.raises(Refused, match="40 approved items, at least 50 are required"):
        gd.activate(cat, sc, "ben")


def test_synthetic_share_exceeded(c09):
    cat, sc = c09["cat"], c09["sc"]
    extra = [fx.item(f"s{n:02d}", origin="synthetic", question=f"s{n:02d}") for n in range(31)]
    fx.golden_set(cat, sc, c09["tmp"], answer=19, abstain=10, activate=False, extra=extra)
    with pytest.raises(Refused, match=r"synthetic share is 52% \(31 of 60 items\); at most 50% allowed"):
        gd.activate(cat, sc, "ben")


def test_charter_override(c09):
    cat, sc = c09["cat"], c09["sc"]
    doc = {"charter_id": sc.charter_id, "golden_set": {"file": "g.yaml", "min_questions": 30}, "metrics": []}
    cat.execute("INSERT INTO metric_set_version VALUES (?, 1, 'approved', ?, 'h', ?, 'ben', NULL, NULL)",
                (sc.charter_id, json.dumps(doc), now()))
    fx.golden_set(cat, sc, c09["tmp"], answer=24, abstain=6, activate=False)
    out = gd.activate(cat, sc, "ben")
    assert out["approved"] == 30 and out["minimums"]["source"] == "charter"
    ev = fx.events(cat, "eval.set.activate")[-1]["details"]
    assert ev["override"] == "charter success metrics" and ev["minimums"]["min_items"] == 30


def test_retention_expiry(c09):
    cat, sc, tmp = c09["cat"], c09["sc"], c09["tmp"]
    items = [fx.item("u1", origin="user_log", question="Wann ist der Beitrag fällig?"),
             fx.item("u2", origin="user_log", question="Wie hoch ist der Beitrag?")]
    gd.import_items(cat, sc, fx.write_items(tmp / "u.yaml", items), "clara", today=date(2025, 1, 1))
    gd.approve(cat, sc, "u2", "clara", today=date(2026, 6, 1))  # re-approved: retention extended
    assert gd.retention(cat, sc, "dirk", today=date(2026, 3, 1)) == ["u1"]
    assert gd.latest(cat, "u1") is None and gd.latest(cat, "u2") is not None
    ev = fx.events(cat, "eval.item.expire")
    assert len(ev) == 1 and ev[0]["details"]["item_id"] == "u1"


# ---------------------------------------------------------------- runs

def test_run_recorded_with_versions(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    r, calls = run_with(c09)
    assert r["status"] == "completed" and (r["set_version"], r["agent_version"]) == (1, "faq-bot@1.4")
    assert (r["judge_model"], r["judge_version"]) == ("konmari-lexical", "1") and r["ended_at"] >= r["started_at"]
    assert cat.one("SELECT COUNT(*) AS n FROM eval_run")["n"] == 1
    res = er.results(cat, r["run_id"])
    assert len(res) == 50 and all(x["latency_ms"] >= 0 for x in res)
    a = result(cat, r["run_id"], "a00")
    assert a["passed"] and a["retrieved"] == ["fee-rules#0"] and a["cited"] == ["fee-rules#0"]
    assert a["behaviour"] == "answer" and a["faithfulness"] == 1.0
    assert calls[0]["meta"]["konmari/test_principal"] == PRINCIPAL  # the declared test principal
    assert r["aggregates"]["run"]["pass_rate"] == 1.0


def test_unapproved_item_excluded(c09):
    cat, sc, tmp = c09["cat"], c09["sc"], c09["tmp"]
    fx.golden_set(cat, sc, tmp, activate=False)
    gd.import_items(cat, sc, fx.write_items(tmp / "d.yaml", [fx.item("draft1", question="draft1")]), "clara")
    gd.activate(cat, sc, "ben")
    r, calls = run_with(c09)
    assert r["excluded"] == [{"item_id": "draft1", "version": 1, "reason": "not_approved"}]
    assert "draft1" not in [c["question"] for c in calls]


def test_agent_unreachable(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    r, _ = run_with(c09, hang_after=3, timeout=0.3)
    assert r["status"] == "failed" and "TimeoutError" in r["error"]
    assert len(er.results(cat, r["run_id"])) == 3  # partial results kept
    calibrated(cat, sc)
    g = eg.evaluate(cat, sc, r["run_id"])
    assert not g["passed"] and any("no gate passes on a failed run" in f for f in g["failures"])


def test_unsupported_claim_lowers_faithfulness(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    four = ("Der Jahresbeitrag für Erwachsene beträgt 120 Euro. Der Beitrag ist am 31. Januar fällig. "
            "Für Erwachsene beträgt der Jahresbeitrag 120 Euro. Kinder zahlen keinen Beitrag.")
    r, _ = run_with(c09, {"a00": {**fx.GOOD, "answer": four}})
    a = result(cat, r["run_id"], "a00")
    assert a["faithfulness"] == 0.75 and a["details"]["unsupported_claims"] == ["Kinder zahlen keinen Beitrag."]
    assert not a["passed"] and "unfaithful" in a["reasons"]
    assert r["aggregates"]["run"]["faithfulness"] == round((39 + 0.75) / 40, 4)


def test_expected_source_not_retrieved(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    r, _ = run_with(c09, {"a01": {**fx.GOOD, "retrieved": ["hall-times#0"]}})
    a = result(cat, r["run_id"], "a01")
    assert a["context_recall"] < 1.0 and a["details"]["missing_sources"] == ["fee-rules"]
    assert a["context_precision"] == 0.0


# ---------------------------------------------------------------- citations

def test_superseded_source_cited(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    stale = {**fx.GOOD, "statements": [{"text": "Der Jahresbeitrag für Erwachsene beträgt 120 Euro.",
                                        "citations": ["fee-rules#2019"]}]}
    r, _ = run_with(c09, {"a02": stale})
    a = result(cat, r["run_id"], "a02")
    assert not a["citation_ok"] and not a["passed"] and "stale_source" in a["reasons"]
    assert cat.one("SELECT status FROM citation_check WHERE run_id = ? AND item_id = 'a02'",
                   (r["run_id"],))["status"] == "stale_source"


def test_out_of_scope_source_cited(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    leak = {**fx.GOOD, "statements": [*fx.GOOD["statements"],
                                      {"text": "Der Vorstand beschließt den Haushalt.", "citations": ["board-minutes"]}]}
    r, _ = run_with(c09, {"a03": leak})
    a = result(cat, r["run_id"], "a03")
    assert not a["passed"] and "out_of_scope" in a["reasons"]
    finding = fx.events(cat, "eval.access_finding")
    assert len(finding) == 1 and finding[0]["details"]["source_id"] == "board-minutes"
    assert finding[0]["details"]["agent"] == "eval-bot"


def test_missing_source_cited(c09):
    cat, sc = c09["cat"], c09["sc"]
    ctx = access.context(cat, "eval-bot", "test-eval", ["support-team"])
    assert er.check_citation(cat, ctx, "no-such-doc") == "missing"
    assert er.check_citation(cat, ctx, "fee-rules#0") == "ok"


class LabelJudge:
    """Hand labels: (statement, frozenset of passages) -> supported."""
    model, version = "hand", "1"

    def __init__(self, supported):
        self.supported = supported

    def supports(self, statement, passages):
        return any(self.supported.get((statement, p)) for p in passages) or \
            self.supported.get((statement, "both")) and len(passages) >= 2


def test_citation_precision_and_recall_hand_labelled(c09):
    cat = c09["cat"]
    fee, hall = fx.FEES, fx.HALL
    # s1 supported by its first citation only (second citation irrelevant); s2 not supported; s3 needs both
    judge = LabelJudge({("s1", fee): True, ("s3", "both"): True})
    st = [{"text": "s1", "citations": ["fee-rules#0", "hall-times#0"]}, {"text": "s2", "citations": ["hall-times#0"]},
          {"text": "s3", "citations": ["fee-rules#0", "hall-times#0"]}]
    out = er.citation_scores(cat, judge, st)
    assert out["citation_recall"] == round(2 / 3, 4)
    assert out["citation_precision"] == round(3 / 5, 4)  # s1/fee, s3/fee, s3/hall


# ---------------------------------------------------------------- abstention

def test_agent_guesses_on_unanswerable_item(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    r, _ = run_with(c09, {"n00": fx.GOOD})
    n = result(cat, r["run_id"], "n00")
    assert not n["passed"] and n["reasons"] == ["hallucinated_answer"]
    assert r["aggregates"]["run"]["abstention_rate"] == 0.9


def test_over_abstention_is_visible(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    r, _ = run_with(c09, {f"a{n:02d}": fx.ABSTAIN for n in range(12)})  # 12 of 40 = 30 %
    agg = r["aggregates"]["run"]
    assert agg["answer_rate"] == 0.7 and agg["abstention_rate"] == 1.0
    assert "over_abstention" in result(cat, r["run_id"], "a00")["reasons"]


def test_conflict_items(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"], answer=38, abstain=10, conflict=2)
    r, _ = run_with(c09, {"c01": fx.GOOD})
    assert result(cat, r["run_id"], "c00")["passed"]
    assert result(cat, r["run_id"], "c01")["reasons"] == ["conflict_not_flagged"]


# ---------------------------------------------------------------- judge

def test_judge_version_pinned(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])

    class Pinned(jd.LexicalJudge):
        model, version = "qwen3-judge", "2026-09-01"
    r, _ = run_with(c09, judge=Pinned())
    assert (r["judge_model"], r["judge_version"]) == ("qwen3-judge", "2026-09-01")
    assert fx.events(cat, "eval.run.start")[0]["details"]["judge_version"] == "2026-09-01"
    with pytest.raises(Refused, match="judge.version is required"):
        jd.make({"provider": "ragas", "model": "m"})


def test_calibration_passes(c09):
    cat, sc = c09["cat"], c09["sc"]
    pairs = [("pass", "pass")] * 20 + [("fail", "fail")] * 32 + [("pass", "fail")] * 4 + [("fail", "pass")] * 4
    rows = [{"item_id": f"h{n}", "human": h, "judge": j} for n, (h, j) in enumerate(pairs)]
    out = jd.calibrate(cat, sc, rows, "qwen3-judge", "2026-09-01", "clara")
    assert out["kappa"] == 0.7222 and out["status"] == "calibrated"
    row = cat.one("SELECT * FROM judge_calibration")
    assert json.loads(row["confusion"]) == {"fail": {"fail": 32, "pass": 4}, "pass": {"fail": 4, "pass": 20}}
    assert (row["judge_version"], row["kappa"]) == ("2026-09-01", 0.7222)
    assert jd.status(cat, sc.charter_id, "qwen3-judge", "2026-09-01") == "calibrated"


def test_calibration_needs_held_out_subset(c09):
    cat, sc = c09["cat"], c09["sc"]
    rows = [{"item_id": f"h{n}", "human": "pass", "judge": "pass"} for n in range(50)]
    with pytest.raises(Refused, match="at least 50"):
        jd.calibrate(cat, sc, rows[:49], "m", "1", "clara")
    with pytest.raises(Refused, match="used to tune the judge prompt: h3"):
        jd.calibrate(cat, sc, rows, "m", "1", "clara", tuning_ids={"h3"})
    low = [{"item_id": f"h{n}", "human": "pass" if n % 2 else "fail", "judge": "pass"} for n in range(50)]
    assert jd.calibrate(cat, sc, low, "m", "1", "clara")["status"] == "uncalibrated"
    assert jd.status(cat, sc.charter_id, "m", "1") == "uncalibrated"


def test_judge_model_changed(c09):
    cat, sc = c09["cat"], c09["sc"]
    fx.golden_set(cat, sc, c09["tmp"])
    calibrated(cat, sc)
    r1, _ = run_with(c09)
    assert r1["judge_status"] == "calibrated" and eg.evaluate(cat, sc, r1["run_id"])["passed"]

    class Upgraded(jd.LexicalJudge):
        version = "2"
    r2, _ = run_with(c09, judge=Upgraded())
    assert r2["judge_status"] == "uncalibrated"
    g = eg.evaluate(cat, sc, r2["run_id"])
    assert not g["passed"] and {f.split(":")[0] for f in g["failures"]} == set(eg.JUDGE_METRICS)


def test_ragas_judge_adapter():
    pytest.importorskip("ragas")
    with pytest.raises(Refused):
        jd.make({"provider": "ragas"})
    j = jd.make({"provider": "ragas", "model": "local-judge", "version": "1", "base_url": "http://127.0.0.1:9/v1"})
    assert (j.model, j.version) == ("local-judge", "1") and j.supports("x", []) is False


# ---------------------------------------------------------------- gates and baselines

GOOD_AGG = {"faithfulness": 0.9, "citation_precision": 0.9, "citation_recall": 0.9, "citation_check": 1.0,
            "abstention_rate": 0.92, "answer_rate": 0.9, "pass_rate": 0.9, "items": 50}


@pytest.mark.parametrize("metric,value", [("faithfulness", 0.79), ("citation_precision", 0.79),
                                          ("citation_recall", 0.79), ("citation_check", 0.98),
                                          ("abstention_rate", 0.89), ("answer_rate", 0.84)])
def test_each_default_threshold(c09, metric, value):
    cat, sc = c09["cat"], c09["sc"]
    assert eg.evaluate(cat, sc, fake_run(cat, sc, GOOD_AGG))["passed"]
    g = eg.evaluate(cat, sc, fake_run(cat, sc, {**GOOD_AGG, metric: value}))
    assert not g["passed"] and g["failures"] == [f"{metric}: {value} is below the threshold "
                                                 f"{eg.DEFAULTS[metric]} (default)"]


def test_regression_blocks_release(c09):
    cat, sc = c09["cat"], c09["sc"]
    eg.approve_baseline(cat, sc, fake_run(cat, sc, GOOD_AGG), "ben")
    new = fake_run(cat, sc, {**GOOD_AGG, "faithfulness": 0.83})
    g = eg.evaluate(cat, sc, new)
    assert not g["passed"] and len(g["failures"]) == 1
    assert "faithfulness regressed: baseline 0.9" in g["failures"][0] and "new 0.83, allowed drop 0.05" in \
        g["failures"][0]
    assert eg.evaluate(cat, sc, fake_run(cat, sc, {**GOOD_AGG, "faithfulness": 0.86}))["passed"]


def test_charter_threshold_wins(c09):
    cat, sc = c09["cat"], c09["sc"]
    doc = {"charter_id": sc.charter_id, "golden_set": {"file": "g.yaml"}, "metrics": [
        {"id": "abstention_rate", "family": "abstention", "unit": "ratio", "target": 0.95, "direction": ">="}]}
    cat.execute("INSERT INTO metric_set_version VALUES (?, 1, 'approved', ?, 'h', ?, 'ben', NULL, NULL)",
                (sc.charter_id, json.dumps(doc), now()))
    g = eg.evaluate(cat, sc, fake_run(cat, sc, {**GOOD_AGG, "abstention_rate": 0.92}))
    assert not g["passed"] and g["failures"] == ["abstention_rate: 0.92 is below the threshold 0.95 (charter metric "
                                                 "abstention_rate (metric set v1))"]


def test_gate_fails_on_failed_run_and_uncalibrated_metrics(c09):
    cat, sc = c09["cat"], c09["sc"]
    g = eg.evaluate(cat, sc, fake_run(cat, sc, GOOD_AGG, status="failed"))
    assert not g["passed"] and "is failed: boom" in g["failures"][0]
    g = eg.evaluate(cat, sc, fake_run(cat, sc, GOOD_AGG, judge_status="uncalibrated"))
    assert not g["passed"] and len(g["failures"]) == 3 and all("uncalibrated" in f for f in g["failures"])


def test_baseline_promotion_audited(c09):
    cat, sc = c09["cat"], c09["sc"]
    r1, r2 = fake_run(cat, sc, GOOD_AGG), fake_run(cat, sc, GOOD_AGG)
    eg.approve_baseline(cat, sc, r1, "ben")
    eg.approve_baseline(cat, sc, r2, "clara")
    ev = fx.events(cat, "eval.baseline.approve")[-1]
    assert (ev["actor"], ev["details"]["previous"], ev["details"]["new"]) == ("clara", r1, r2) and ev["ts"]
    with pytest.raises(Refused, match="only a completed run"):
        eg.approve_baseline(cat, sc, fake_run(cat, sc, GOOD_AGG, status="failed"), "ben")


def test_baseline_revert(c09):
    cat, sc = c09["cat"], c09["sc"]
    r1, r2 = fake_run(cat, sc, GOOD_AGG), fake_run(cat, sc, GOOD_AGG)
    eg.approve_baseline(cat, sc, r1, "ben")
    eg.approve_baseline(cat, sc, r2, "ben")
    assert eg.revert_baseline(cat, sc, "ben") == {"previous": r2, "baseline": r1}
    assert eg.baseline(cat, sc.charter_id)["run_id"] == r1
    ev = fx.events(cat, "eval.baseline.revert")
    assert len(ev) == 1 and ev[0]["details"]["new"] == r1
    with pytest.raises(Refused, match="no previous baseline"):
        eg.revert_baseline(cat, sc, "ben")


# ---------------------------------------------------------------- feedback

def _asset(cat, uri, category):
    t = now()
    cat.execute("INSERT INTO source_asset (asset_uri, source_id, charter_id, system_type, origin, title, owner,"
                " update_frequency, ropa_missing, first_seen, last_seen, status) VALUES (?, 'src', ?, 'sql', 'scan',"
                " ?, 'ben', 'P7D', false, ?, ?, 'complete')", (uri, "verein-faq-assistant", uri, t, t))
    cat.execute("INSERT INTO asset_category VALUES (?, ?, true, 'accepted', NULL, NULL, ?, 'ben')", (uri, category, t))


def test_failing_category_flagged(c09):
    cat, sc = c09["cat"], c09["sc"]
    _asset(cat, "manual://policies/contracts", "policies-and-contracts")
    cats_ = {"policies-and-contracts": {**GOOD_AGG, "citation_check": 0.5}, KB: GOOD_AGG}
    rid = fake_run(cat, sc, GOOD_AGG, categories=cats_)
    fb = eg.feedback(cat, sc, rid)
    assert fb["fix"] == ["policies-and-contracts"]
    ev = cat.one("SELECT * FROM asset_evidence WHERE asset_uri = 'manual://policies/contracts' AND kind = 'agent_eval'")
    assert ev["evidence_ref"] == f"eval-run:{rid}" and json.loads(ev["payload"])["status"] == "fix"
    assert eg.propose_next(cat, sc, rid) is None  # a failing category: no next category yet


def test_next_category_needs_confirmation(c09):
    cat, sc = c09["cat"], c09["sc"]
    rid = fake_run(cat, sc, GOOD_AGG, categories={KB: GOOD_AGG, SUPPORT: GOOD_AGG})
    p = eg.propose_next(cat, sc, rid)
    assert p["status"] == "proposed" and p["category"] not in (KB, SUPPORT)
    assert eg.active_category(cat, sc.charter_id) is None  # unchanged without confirmation
    with pytest.raises(Refused, match="cannot confirm"):
        eg.confirm_next(cat, sc, p["proposal_id"], "finn")
    assert eg.confirm_next(cat, sc, p["proposal_id"], "ben")["category"] == p["category"]
    assert eg.active_category(cat, sc.charter_id) == p["category"]
    assert fx.events(cat, "eval.next_category.confirm")[0]["actor"] == "ben"


# ---------------------------------------------------------------- audit of every state change

def test_each_state_change_writes_one_audit_entry(c09):
    cat, sc, tmp = c09["cat"], c09["sc"], c09["tmp"]
    count = lambda: cat.one("SELECT COUNT(*) AS n FROM audit_event")["n"]  # noqa: E731
    gd.import_items(cat, sc, fx.write_items(tmp / "a.yaml", [fx.item("e1")]), "clara")
    for fn in (lambda: gd.approve(cat, sc, "e1", "clara"),
               lambda: eg.approve_baseline(cat, sc, fake_run(cat, sc, GOOD_AGG), "ben"),
               lambda: eg.approve_baseline(cat, sc, fake_run(cat, sc, GOOD_AGG), "ben"),
               lambda: eg.revert_baseline(cat, sc, "ben")):
        before = count()
        fn()
        assert count() == before + 1
