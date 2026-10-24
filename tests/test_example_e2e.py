"""Example engagement (fictional Verein FAQ assistant) end to end via the CLI, on DuckDB and on Postgres."""

import json

import pytest


def _flow(cli):
    assert cli("charter", "validate")[0] == 0
    assert cli("metrics", "validate")[0] == 0
    assert cli("charter", "gate", "--step", "inventory")[0] == 1  # draft
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    assert cli("metrics", "approve", "--as", "ben")[0] == 0
    for m, v in [("answer_accuracy", 0.41), ("abstention_rate", 0.2), ("human_approved_decisions", 100)]:
        assert cli("metrics", "baseline", m, "--value", v, "--method", "as-is sample", "--as", "clara")[0] == 0
    assert cli("metrics", "baseline", "time_to_answer", "--not-yet-measured", "--plan-date", "2026-03-01",
               "--as", "ben")[0] == 0
    assert cli("charter", "gate", "--step", "inventory")[0] == 0
    assert cli("charter", "gate", "--step", "discard")[0] == 1
    assert cli("metrics", "baseline", "time_to_answer", "--value", 12, "--method", "50 questions", "--as", "ben")[0] == 0
    code, out, _ = cli("charter", "gate", "--step", "discard", "--json")
    assert code == 0 and json.loads(out)["charter_version"] == 1
    assert cli("charter", "log", "--meeting", "steering", "--date", "2026-02-10", "--attendees", "anna,eva",
               "--decision", "go for discard", "--as", "gina")[0] == 0
    assert cli("charter", "status")[0] == 0
    rep = json.loads(cli("metrics", "report", "--format", "json")[1])
    assert rep["verdict"] == "in_progress"


def test_example_engagement_on_duckdb(engagement, cli):
    _flow(cli)


@pytest.mark.postgres
def test_example_engagement_on_postgres(engagement, cli, pg_url, monkeypatch):
    monkeypatch.setenv("KONMARI_CATALOG", pg_url)
    _flow(cli)
