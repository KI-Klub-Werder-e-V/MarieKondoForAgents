"""c09 end to end through the CLI: golden items import/approve/activate, a run against a STDIO MCP agent, the CI
example workflow (run + gate) on the fixture project, baseline approve/revert, monitor run, incidents, schedule."""

import shlex
import sys
from pathlib import Path

import pytest
import yaml

import c09_fixture as fx
from konmari.canonical import dump_yaml
from konmari.charter import store as cs
from konmari.evaluate import golden as gd
from konmari.evaluate import judge as jd

ROOT = Path(__file__).parent.parent
WORKFLOW = ROOT / "examples" / "ci" / "konmari-eval.yml"


def _project(engagement, c09, cli):
    def ok(*args):
        code, out, err = cli(*args)
        assert code == 0, (args, out, err)
        return out
    ok("charter", "approve", "--as", "anna")
    cat = c09["cat"]
    sc = cs.scope(sc_id := fx.scope().charter_id, cat)
    items = [fx.item(f"a{n:02d}", question=f"a{n:02d}") for n in range(40)]
    items += [fx.item(f"n{n:02d}", "abstain", question=f"n{n:02d}") for n in range(10)]
    fx.write_items(engagement / "items.yaml", items)
    assert "50 stored" in ok("eval", "items", "import", "items.yaml", "--as", "clara")
    for it in items:
        gd.approve(cat, sc, it["id"], "clara")
    assert "golden set v1 active: 50 approved" in ok("eval", "set", "activate", "--as", "ben")
    rows = [{"item_id": f"cal{n}", "human": "pass", "judge": "pass"} for n in range(50)]
    jd.calibrate(cat, sc, rows, "konmari-lexical", "1", "clara")
    (engagement / "eval.yaml").write_text(dump_yaml({
        "agent": {"command": [sys.executable, str(ROOT / "tests" / "stub_agent_stdio.py")], "version": "stub@1",
                  "timeout_seconds": 30},
        "principal": fx.PRINCIPAL, "judge": {"provider": "lexical"}}), encoding="utf-8")
    assert sc_id == sc.charter_id
    return ok, cat, sc


def test_sample_workflow_passes_on_fixture_project(engagement, c09, cli):
    ok, cat, sc = _project(engagement, c09, cli)
    wf = yaml.safe_load(WORKFLOW.read_text())
    paths = wf[True]["pull_request"]["paths"]  # YAML 1.1 reads the key `on` as True
    assert {"eval.yaml", "products/**", "policy/**"} <= set(paths)
    steps = [s["run"] for s in wf["jobs"]["eval"]["steps"] if "konmari eval" in s.get("run", "")]
    assert len(steps) == 2
    for run in steps:
        args = shlex.split(run.replace("${{ vars.KONMARI_EVAL_ACTOR }}", "ben"))[3:]  # drop "uv run konmari"
        out = ok(*args)
    assert "gate passed" in out
    rid = cat.one("SELECT run_id FROM eval_run")["run_id"]
    assert cat.one("SELECT agent_version, status FROM eval_run") == {"agent_version": "stub@1", "status": "completed"}
    ok("eval", "baseline", "approve", rid, "--as", "ben")
    code, _, err = cli("eval", "baseline", "revert", "--as", "ben")
    assert code == 1 and "no previous baseline" in err
    assert '"run_id"' in ok("eval", "report")
    assert "active category: -" in ok("eval", "next-category")


def test_gate_cli_exit_code_on_failure(engagement, c09, cli):
    ok, cat, sc = _project(engagement, c09, cli)
    jd.calibrate(cat, sc, [{"item_id": f"x{n}", "human": "pass", "judge": "pass"} for n in range(50)], "other",
                 "9", "clara")  # the latest calibration no longer matches the judge
    ok("eval", "run", "--as", "ben")
    code, out, _ = cli("eval", "gate")
    assert code == 1 and "uncalibrated" in out


def test_monitor_cli(engagement, c09, cli):
    cli("charter", "approve", "--as", "anna")
    from test_freshness_monitoring import asset

    from konmari.catalog import now

    asset(c09["cat"], "sql://crm/contacts", age_days=20, at=now(), source="office-share")
    code, out, err = cli("monitor", "run", "--dry-run")
    assert code == 0 and "dry run, nothing written" in out and "error: freshness sql://crm/contacts" in out
    code, out, err = cli("monitor", "run")
    assert code == 0, err
    inc = c09["cat"].one("SELECT incident_id FROM incident")["incident_id"]
    code, out, err = cli("monitor", "incident", "acknowledge", inc, "--reason", "looking into it", "--as", "ben")
    assert code == 0 and "acknowledged" in out
    code, out, _ = cli("monitor", "schedule", "--format", "systemd")
    assert "[Timer]" in out
    assert (engagement / ".konmari" / "notifications.jsonl").is_file()


@pytest.mark.postgres
def test_eval_and_monitor_on_postgres(pg_url, tmp_path, monkeypatch):
    from konmari.catalog import connect
    from konmari.evaluate import gate as eg
    from konmari.evaluate import run as er
    from konmari.monitor import checks as mc
    from konmari.monitor import incidents as mi
    from test_freshness_monitoring import AT, _Mem, asset, items

    monkeypatch.setenv("KONMARI_LINEAGE_FILE", str(tmp_path / "lineage.jsonl"))
    cat = connect(pg_url)
    fx.governed(cat)
    sc = fx.scope()
    fx.golden_set(cat, sc, tmp_path)
    jd.calibrate(cat, sc, [{"item_id": f"c{n}", "human": "pass", "judge": "pass"} for n in range(50)],
                 "konmari-lexical", "1", "clara")
    srv, _ = fx.stub_agent(fx.responder())
    r = er.run(cat, sc, er.McpAgent(srv), fx.PRINCIPAL, "pg@1", "ben")
    assert r["status"] == "completed" and eg.evaluate(cat, sc, r["run_id"])["passed"]
    eg.feedback(cat, sc, r["run_id"])
    asset(cat, "sql://crm/contacts", age_days=20)
    items(cat, "sql://crm/contacts", 3)
    rep = mc.run(cat, sc, mc.load_config("none.yaml"), send=mi.MemoryNotifier(), at=AT, transport=_Mem())
    assert any(x["status"] == "error" for x in rep["results"])
    assert cat.one("SELECT COUNT(*) AS n FROM incident")["n"] == 1
    cat.close()
