"""c10 guided-operation: `konmari next`, tests named after the spec scenarios."""

import csv
import json
from pathlib import Path

import pytest
from conftest import approve_pilot, edit_yaml

from konmari.catalog import connect

CATS = ["kb-and-sops", "policies-and-contracts"]
ASSETS = {"manual://kb/faq": "kb-and-sops", "manual://kb/sops": "kb-and-sops",
          "manual://policies/fees": "policies-and-contracts"}


def nxt(cli) -> dict:
    code, out, err = cli("next", "--json")
    assert code == 0, err
    return json.loads(out)


def ok(cli, *args):
    code, out, err = cli(*args)
    assert code == 0, (args, err)
    return out


def counts(c) -> dict:
    tables = c.query("SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')"
                     " AND table_type = 'BASE TABLE'")
    return {t["table_name"]: c.one(f"SELECT COUNT(*) AS n FROM {t['table_name']}")["n"] for t in tables}


def registered_sources(cli, tmp_path) -> Path:
    """Approved pilot (baselines recorded) with one csv-import source holding three synthetic assets."""
    csvf = tmp_path / "assets.csv"
    approve_pilot(cli, [{"id": "manual-assets", "connector": "csv-import", "locator": str(csvf), "categories": CATS}],
                  CATS, {"policies-and-contracts": "clara"})
    ok(cli, "metrics", "baseline", "time_to_answer", "--value", 120, "--method", "stopwatch", "--as", "ben")
    with csvf.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["asset_uri", "title", "system_type", "owner", "steward", "update_frequency", "format",
                               "sensitivity_tier"])
        w.writeheader()
        for uri in ASSETS:
            w.writerow({"asset_uri": uri, "title": uri.rsplit("/", 1)[-1], "system_type": "manual", "owner": "ben",
                        "steward": "clara", "update_frequency": "P1M", "format": "text/plain",
                        "sensitivity_tier": "internal"})
    return csvf


def handed_off(cli, tmp_path):
    csvf = registered_sources(cli, tmp_path)
    ok(cli, "inventory", "source", "add", "--connector", "csv-import", "--locator", csvf, "--as", "dirk")
    ok(cli, "inventory", "import", csvf, "--as", "dirk")
    for uri, c in ASSETS.items():
        ok(cli, "categories", "assign", uri, c, "--as", "ben")
    ok(cli, "categories", "order", "--propose", "--as", "ben")
    ok(cli, "categories", "approve", "--as", "ben")
    ok(cli, "categories", "handoff")


# ---------------------------------------------------------------- read-only report

def _no_state_written(cli, tmp_path):
    handed_off(cli, tmp_path)
    c = connect()
    before = counts(c)
    c.close()
    for args in [("next",), ("next", "--json")]:
        assert cli(*args)[0] == 0
    c = connect()
    assert counts(c) == before
    c.close()


def test_no_audit_events_or_state_written(engagement, cli, tmp_path):
    _no_state_written(cli, tmp_path)


@pytest.mark.postgres
def test_no_audit_events_or_state_written_on_postgres(engagement, cli, tmp_path, pg_url, monkeypatch):
    monkeypatch.setenv("KONMARI_CATALOG", pg_url)
    _no_state_written(cli, tmp_path)


def test_exit_code_zero_while_blocked(engagement, cli):
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    assert cli("metrics", "approve", "--as", "ben")[0] == 0
    code, out, _ = cli("next")
    assert code == 0 and "needs_input" in out
    with pytest.raises(SystemExit) as e:
        cli("next", "--bogus")
    assert e.value.code == 2


# ---------------------------------------------------------------- charter and metric set

def test_no_charter_yet(engagement, cli, tmp_path):
    (engagement / "charter.yaml").unlink()
    n = nxt(cli)["next"]
    assert n["step"] == "charter" and n["status"] == "needs_input"
    assert "konmari charter init --template minimal --lang <de|en>" in n["commands"]
    assert not (tmp_path / "catalog.duckdb").exists()


def test_invalid_charter_lists_every_error(engagement, cli):
    edit_yaml("charter.yaml", lambda d: (d.pop("compliance"), d["raci"].pop("dpia_decision")))
    from konmari.canonical import load_yaml
    from konmari.charter.validation import validate_charter

    errors = validate_charter(load_yaml(Path("charter.yaml")))
    n = nxt(cli)["next"]
    assert n["status"] == "needs_input" and len(errors) >= 2 and n["reasons"] == errors


def test_charter_waiting_for_approval(engagement, cli):
    n = nxt(cli)["next"]
    assert n["status"] == "needs_approval" and "konmari charter approve --as anna" in n["commands"]
    assert n["approval_needed"] == {"who": "anna", "role": "Accountable for charter_approval",
                                    "what": "approve charter version 1"}


def test_metric_set_waiting_for_approval(engagement, cli):
    ok(cli, "charter", "approve", "--as", "anna")
    rep = nxt(cli)
    n = rep["next"]
    assert rep["charter"]["state"] == "approved" and n["step"] == "metrics" and n["status"] == "needs_approval"
    assert "konmari metrics approve --as ben" in n["commands"]
    assert n["approval_needed"]["who"] == "ben" and n["approval_needed"]["role"] == "Accountable for metric_approval"


# ---------------------------------------------------------------- human-only values

def test_missing_baselines_flagged_as_input(engagement, cli):
    ok(cli, "charter", "approve", "--as", "anna")
    ok(cli, "metrics", "approve", "--as", "ben")
    n = nxt(cli)["next"]
    assert n["status"] == "needs_input"
    owners = {i["metric"]: i["who"] for i in n["input_needed"]}
    assert owners == {"answer_accuracy": "clara", "abstention_rate": "clara", "time_to_answer": "ben",
                      "human_approved_decisions": "ben"}
    value_cmds = [c for c in n["commands"] if "--value" in c]
    assert len(value_cmds) == 4 and all("--value <value>" in c for c in value_cmds)
    assert not any(ch.isdigit() for c in value_cmds for ch in c.split("--value")[1].split("--as")[0])


# ---------------------------------------------------------------- inventory and categories

def test_unregistered_charter_sources_come_first(engagement, cli, tmp_path):
    csvf = registered_sources(cli, tmp_path)
    n = nxt(cli)["next"]
    assert n["step"] == "inventory" and n["status"] == "ready"
    assert n["commands"] == [f"konmari inventory source add --connector csv-import --locator {csvf} --as <you>"]


def test_category_order_approval_names_the_category_approver(engagement, cli, tmp_path):
    csvf = registered_sources(cli, tmp_path)
    ok(cli, "inventory", "source", "add", "--connector", "csv-import", "--locator", csvf, "--as", "dirk")
    ok(cli, "inventory", "import", csvf, "--as", "dirk")
    for uri, c in ASSETS.items():
        ok(cli, "categories", "assign", uri, c, "--as", "ben")
    ok(cli, "categories", "order", "--propose", "--as", "ben")
    n = nxt(cli)["next"]
    assert n["status"] == "needs_approval" and "konmari categories approve --as ben" in n["commands"]
    assert n["approval_needed"]["who"] == "ben"


def test_categories_in_the_approved_order(engagement, cli, tmp_path):
    handed_off(cli, tmp_path)
    rep = nxt(cli)
    order = json.loads(connect().one("SELECT categories FROM category_order_version WHERE status = 'approved'")
                       ["categories"])
    assert [c["category"] for c in rep["categories"]] == order
    assert all(c["steps"]["inventory"] == "complete" for c in rep["categories"])
    n = rep["next"]
    assert (n["step"], n["category"], n["status"]) == ("score", order[0], "ready")
    assert n["commands"] == ["konmari score --as <you>"]


def test_step_completion_names_the_category_approver(engagement, cli, tmp_path):
    from konmari.categories import store as cats
    from konmari.charter.store import scope

    handed_off(cli, tmp_path)
    c = connect()
    sc = scope("verein-faq-assistant", c)
    order = cats.approved_order(c, sc.charter_id)["categories"]
    for cid in order:
        cats.set_step(c, sc, cid, "score", "complete", "ben")
    c.close()
    n = nxt(cli)["next"]
    first = order[0]
    assert (n["step"], n["category"]) == ("discard", first)
    approver = {"kb-and-sops": "ben", "policies-and-contracts": "clara"}[first]
    assert n["commands"][-1] == f"konmari categories step {first} discard --status complete --as {approver}"
    assert n["approval_needed"]["who"] == approver


# ---------------------------------------------------------------- output

def test_json_shape(engagement, cli):
    for i in range(2):
        if i:
            ok(cli, "charter", "approve", "--as", "anna")
        rep = nxt(cli)
        assert {"charter", "metrics", "categories", "next"} <= rep.keys()
        assert {"step", "category", "status", "reasons", "commands", "approval_needed", "input_needed"} \
            <= rep["next"].keys()
