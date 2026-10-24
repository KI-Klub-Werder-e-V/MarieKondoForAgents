"""Readiness gate scenarios (pilot-charter "Readiness gate for downstream steps" and success-metrics gate cases)."""

import json

import pytest

from konmari.charter import gate

from conftest import edit_yaml

MUST_HAVE = ["answer_accuracy", "abstention_rate", "time_to_answer", "human_approved_decisions"]


def ready(cli, planned=()):
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    assert cli("metrics", "approve", "--as", "ben")[0] == 0
    for m in MUST_HAVE:
        if m in planned:
            args = ["--not-yet-measured", "--plan-date", "2026-03-01"]
        else:
            args = ["--value", 100 if m == "human_approved_decisions" else 0.4, "--method", "as-is sample"]
        assert cli("metrics", "baseline", m, *args, "--as", "ben")[0] == 0


def last_gate_event(cat):
    return cat.query("SELECT * FROM audit_event WHERE action = 'charter.gate' ORDER BY ts DESC LIMIT 1")[0]


def test_inventory_blocked_by_draft_charter(engagement, cli, cat):
    cli("charter", "validate")
    cli("metrics", "validate")
    res = gate("inventory", catalog=cat)
    assert not res.passed and any("not approved" in u for u in res.unmet)
    ev = last_gate_event(cat)
    assert (ev["step"], ev["result"], ev["charter_version"]) == ("inventory", "failed", 1)
    code, _, err = cli("charter", "gate", "--step", "inventory")
    assert code == 1 and "version 1 is 'draft'" in err


def test_gate_passes(engagement, cli, cat):
    ready(cli)
    code, out, _ = cli("charter", "gate", "--step", "discard", "--json")
    res = json.loads(out)
    assert code == 0 and res["passed"] and (res["charter_id"], res["charter_version"]) == ("verein-faq-assistant", 1)
    ev = last_gate_event(cat)
    assert (ev["step"], ev["result"], ev["charter_version"]) == ("discard", "passed", 1)


def test_gate_lists_every_unmet_condition(engagement, cli, cat):
    cli("charter", "validate")
    cli("metrics", "validate")
    res = gate("inventory", catalog=cat)
    assert len(res.unmet) == 2 + len(MUST_HAVE)  # charter draft, metric set draft, 4 baselines


def test_gate_fails_on_hash_mismatch(engagement, cli, cat):
    ready(cli)
    edit_yaml("charter.yaml", lambda d: d.update(title="edited on disk"))
    res = gate("inventory", catalog=cat)
    assert not res.passed and any("content hash" in u for u in res.unmet)


def test_missing_baseline_blocks_gate(engagement, cli, cat):
    cli("charter", "approve", "--as", "anna")
    cli("metrics", "approve", "--as", "ben")
    res = gate("inventory", catalog=cat)
    assert not res.passed and "metrics: must_have metric 'time_to_answer' has no baseline or measurement plan" in res.unmet


def test_planned_baseline_allows_inventory_only(engagement, cli, cat):
    ready(cli, planned={"time_to_answer"})
    assert gate("inventory", catalog=cat).passed
    res = gate("discard", catalog=cat)
    assert not res.passed and any("time_to_answer" in u for u in res.unmet)
    assert cli("charter", "gate", "--step", "discard")[0] == 1
    cli("metrics", "baseline", "time_to_answer", "--value", 12, "--method", "50 sampled questions", "--as", "ben")
    assert gate("discard", catalog=cat).passed


def test_unapproved_metric_set_blocks_gate(engagement, cli, cat):
    ready(cli)
    edit_yaml("metrics.yaml", lambda d: d["metrics"][3].update(target=0.05))
    cli("metrics", "amend", "--reason", "tighter escalation target", "--as", "ben")
    res = gate("inventory", catalog=cat)
    assert not res.passed and "metrics: metric set version 2 is 'draft', not approved" in res.unmet


def test_gate_rejects_unknown_step(engagement, cat):
    with pytest.raises(ValueError):
        gate("tidy-everything", catalog=cat)
