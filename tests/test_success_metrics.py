"""Scenarios of specs/success-metrics."""

import copy
import json

import pytest

from konmari.canonical import canonical_hash, load_yaml
from konmari.metrics.validation import validate_golden_set, validate_metrics

from conftest import EXAMPLE, edit_yaml


@pytest.fixture
def metrics_doc():
    return copy.deepcopy(load_yaml(EXAMPLE / "metrics.yaml"))


@pytest.fixture
def golden_doc():
    return copy.deepcopy(load_yaml(EXAMPLE / "golden-set.yaml"))


def merrs(metrics, charter):
    return "\n".join(validate_metrics(metrics, charter))


def approved(cli):
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    assert cli("metrics", "approve", "--as", "ben")[0] == 0


# --- Complete metric definitions

def test_metric_without_owner_rejected(metrics_doc, charter_doc):
    del metrics_doc["metrics"][0]["owner"]
    metrics_doc["metrics"][1]["owner"] = "zoe"
    e = merrs(metrics_doc, charter_doc)
    assert "metrics[0].owner: required field is missing" in e
    assert "metrics.abstention_rate.owner: 'zoe' is not in the stakeholder register" in e


def test_metric_without_owner_cli_exit(engagement, cli):
    edit_yaml("metrics.yaml", lambda d: d["metrics"][0].update(owner="zoe"))
    code, _, err = cli("metrics", "validate")
    assert code != 0 and "answer_accuracy.owner" in err


@pytest.mark.parametrize("field", ["id", "name", "family", "unit", "formula", "data_source", "method", "target",
                                   "direction", "tier", "cadence"])
def test_metric_missing_required_field(metrics_doc, charter_doc, field):
    del metrics_doc["metrics"][3][field]
    assert f"metrics[3].{field}: required field is missing" in merrs(metrics_doc, charter_doc)


def test_valid_metric_set(engagement, cli, cat):
    code, out, _ = cli("metrics", "validate")
    assert code == 0 and "metric set version 1 (draft)" in out
    rows = cat.query("SELECT * FROM metric_definition WHERE charter_id = 'verein-faq-assistant'")
    assert len(rows) == 6


# --- Minimum metric coverage

def test_missing_business_outcome(metrics_doc, charter_doc):
    for m in metrics_doc["metrics"]:
        if m["family"] == "business_outcome":
            m["tier"] = "target"
    assert "technical metrics alone do not show business value" in merrs(metrics_doc, charter_doc)


def test_missing_data_readiness_family(metrics_doc, charter_doc):
    metrics_doc["metrics"] = [m for m in metrics_doc["metrics"] if m["family"] != "data_readiness"]
    assert "no metric in family 'data_readiness'" in merrs(metrics_doc, charter_doc)


def test_human_approval_metric_required(metrics_doc, charter_doc):
    ha = next(m for m in metrics_doc["metrics"] if m["id"] == "human_approved_decisions")
    ha["target"] = 95
    assert "human_approved_decisions.target: must be 100%" in merrs(metrics_doc, charter_doc)
    metrics_doc["metrics"].remove(ha)
    assert "governance metric 'human_approved_decisions'" in merrs(metrics_doc, charter_doc)


# --- Golden question set specification

def test_too_few_abstention_questions(golden_doc, charter_doc):
    neg = [q for q in golden_doc["questions"] if q["expected_behaviour"] != "answer"]
    pos = [q for q in golden_doc["questions"] if q["expected_behaviour"] == "answer"]
    golden_doc["questions"] = pos + neg[:3] + [dict(pos[0], id=f"x{i}") for i in range(40 - len(pos) - 3)]
    assert len(golden_doc["questions"]) == 40
    e = "\n".join(validate_golden_set(golden_doc, charter_doc))
    assert "at least 5 abstention questions are required" in e


def test_too_few_questions(golden_doc, charter_doc):
    golden_doc["questions"] = golden_doc["questions"][-20:]
    assert "20 questions, at least 30 are required" in "\n".join(validate_golden_set(golden_doc, charter_doc))


def test_facilitator_only_authorship_flagged(golden_doc, charter_doc):
    for q in golden_doc["questions"]:
        q["author"] = "gina"
    e = "\n".join(validate_golden_set(golden_doc, charter_doc))
    assert "questions must come from agent users or domain experts" in e


def test_expected_answer_required(golden_doc, charter_doc):
    del golden_doc["questions"][0]["expected_answer"]
    assert "questions[0].expected_answer: required field is missing" in "\n".join(
        validate_golden_set(golden_doc, charter_doc))


def test_personal_data_in_questions_needs_dpia(golden_doc, charter_doc):
    golden_doc["questions"][0]["contains_personal_data"] = True
    assert "contain personal data" in "\n".join(validate_golden_set(golden_doc, charter_doc))
    charter_doc["compliance"]["dpia_screening"]["result"] = "required"
    assert validate_golden_set(golden_doc, charter_doc) == []


def test_golden_set_hash_stored_with_measurement(engagement, cli, cat):
    approved(cli)
    cli("metrics", "measure", "answer_accuracy", "--kind", "gate", "--step", "inventory", "--value", 0.6,
        "--method", "graded run", "--as", "clara")
    row = cat.one("SELECT * FROM metric_measurement")
    assert row["golden_set_hash"] == canonical_hash(load_yaml(engagement / "golden-set.yaml"))
    assert (row["charter_version"], row["metric_set_version"], row["step"]) == (1, 1, "inventory")


# --- Baseline before target and before data work (gate cases: test_gate.py)

def test_baseline_recorded(engagement, cli, cat):
    approved(cli)
    code, _, _ = cli("metrics", "baseline", "answer_accuracy", "--value", 0.41, "--method", "50 sampled tickets",
                     "--as", "clara")
    assert code == 0
    row = cat.one("SELECT * FROM metric_measurement")
    assert (row["kind"], row["value"], row["method"], row["author"]) == ("baseline", 0.41, "50 sampled tickets",
                                                                         "clara")
    assert row["measured_on"] and row["unit"] == "ratio"


def test_planned_baseline_needs_plan_date_before_close(engagement, cli):
    approved(cli)
    assert cli("metrics", "baseline", "time_to_answer", "--not-yet-measured", "--as", "ben")[0] != 0
    code, _, err = cli("metrics", "baseline", "time_to_answer", "--not-yet-measured", "--plan-date", "2026-09-01",
                       "--as", "ben")
    assert code != 0 and "pilot close" in err
    assert cli("metrics", "baseline", "time_to_answer", "--not-yet-measured", "--plan-date", "2026-03-01",
               "--as", "ben")[0] == 0


# --- Approved targets and audited target changes

def test_metrics_approval_needs_accountable(engagement, cli, cat):
    code, _, err = cli("metrics", "approve", "--as", "anna")
    assert code != 0 and "not Accountable for metric approval" in err
    assert cli("metrics", "approve", "--as", "ben")[0] == 0
    ap = cat.one("SELECT * FROM approval WHERE subject = 'metrics'")
    assert ap["approver_id"] == "ben" and ap["approved_at"] and len(ap["content_hash"]) == 64


def test_target_lowered_after_measurement(engagement, cli, cat):
    approved(cli)
    cli("metrics", "measure", "answer_accuracy", "--kind", "gate", "--step", "inventory", "--value", 0.72,
        "--method", "graded run", "--as", "clara")
    edit_yaml("metrics.yaml", lambda d: d["metrics"][0].update(target=0.70))
    code, out, _ = cli("metrics", "amend", "--reason", "first gate showed 0.72", "--as", "ben")
    assert code == 0 and "target_lowered_after_measurement: answer_accuracy" in out
    rows = cat.query("SELECT version, status, flags, content FROM metric_set_version ORDER BY version")
    assert [(r["version"], r["status"]) for r in rows] == [(1, "approved"), (2, "draft")]
    assert "target_lowered_after_measurement" in rows[1]["flags"]
    assert json.loads(rows[0]["content"])["metrics"][0]["target"] == 0.85
    assert cat.one("SELECT * FROM audit_event WHERE action = 'target_lowered_after_measurement'")


def test_raising_target_not_flagged(engagement, cli):
    approved(cli)
    cli("metrics", "baseline", "answer_accuracy", "--value", 0.4, "--method", "m", "--as", "clara")
    edit_yaml("metrics.yaml", lambda d: d["metrics"][0].update(target=0.9))
    code, out, _ = cli("metrics", "amend", "--reason", "more ambitious", "--as", "ben")
    assert code == 0 and "FLAG" not in out


def test_approved_metric_set_edit_needs_amend(engagement, cli):
    approved(cli)
    edit_yaml("metrics.yaml", lambda d: d["metrics"][0].update(target=0.9))
    code, _, err = cli("metrics", "validate")
    assert code != 0 and "metrics amend" in err


# Scenario "Unapproved metric set blocks gate": test_gate.py::test_unapproved_metric_set_blocks_gate


# --- Re-measurement at phase gates and status report

def _all_baselines(cli):
    for m, val in [("answer_accuracy", 0.4), ("abstention_rate", 0.2), ("time_to_answer", 12),
                   ("human_approved_decisions", 100)]:
        cli("metrics", "baseline", m, "--value", val, "--method", "as-is sample", "--as", "ben")


def test_pilot_close_with_unmet_must_have(engagement, cli):
    approved(cli)
    _all_baselines(cli)
    for m, val in [("answer_accuracy", 0.80), ("abstention_rate", 0.9), ("time_to_answer", 4),
                   ("human_approved_decisions", 100)]:
        cli("metrics", "measure", m, "--kind", "final", "--step", "evaluate", "--value", val, "--method", "final run",
            "--as", "ben")
    code, out, _ = cli("metrics", "report")
    assert code == 0 and "Pilot verdict: not_successful" in out
    assert "| answer_accuracy | must_have | 0.4 | 0.8 | >= 0.85 ratio | not_met |" in out


def test_pilot_close_all_met_is_successful(engagement, cli):
    approved(cli)
    _all_baselines(cli)
    for m, val in [("answer_accuracy", 0.9), ("abstention_rate", 0.9), ("time_to_answer", 4),
                   ("human_approved_decisions", 100)]:
        cli("metrics", "measure", m, "--kind", "final", "--step", "evaluate", "--value", val, "--method", "final run",
            "--as", "ben")
    assert "Pilot verdict: successful" in cli("metrics", "report")[1]


def test_must_have_not_measured_at_close_fails(engagement, cli):
    approved(cli)
    cli("metrics", "measure", "answer_accuracy", "--kind", "final", "--step", "evaluate", "--value", 0.9,
        "--method", "final run", "--as", "clara")
    rep = json.loads(cli("metrics", "report", "--format", "json")[1])
    assert rep["verdict"] == "not_successful" and "time_to_answer" in rep["failing_must_have"]


def test_json_export(engagement, cli):
    approved(cli)
    cli("metrics", "baseline", "answer_accuracy", "--value", 0.4, "--method", "m", "--as", "clara")
    cli("metrics", "measure", "answer_accuracy", "--kind", "gate", "--step", "discard", "--value", 0.9,
        "--method", "m", "--as", "clara")
    code, out, _ = cli("metrics", "report", "--format", "json")
    rep = json.loads(out)
    assert code == 0 and rep["verdict"] == "in_progress" and len(rep["metrics"]) == 6
    m = rep["metrics"][0]
    assert {k: m[k] for k in ("baseline", "latest", "target", "tier", "status")} == {
        "baseline": 0.4, "latest": 0.9, "target": 0.85, "tier": "must_have", "status": "met"}
    for item in rep["metrics"]:
        assert {"baseline", "latest", "target", "tier", "status"} <= set(item)
