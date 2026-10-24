"""c07 end to end through the CLI on the c06 fixture pilot (task 8.4, the govern step dry run): policies, ACL sync,
PII scan, labelled sample, masked index, leak suite, gate check and status, audit verify and export, Art. 30
entry. The result is recorded in the catalog (governance_check). Same run on Postgres. Synthetic data only."""

import json

import pytest

from konmari.canonical import dump_yaml, load_yaml
from konmari.catalog import connect, verify_chain
from test_c06_e2e import pipeline

APPROVER = {"policies-and-contracts": "clara"}


def govern_step(c06, cli):
    ok = pipeline(c06, cli)
    d = c06["tmp"] / "engagement"
    # the fixture corpus is tiny: the labelled sample minimum is lowered for this rehearsal only
    (d / "governance.yaml").write_text(dump_yaml({"pii": {"min_sample": 2}}), encoding="utf-8")
    (d / "leak-suite.yaml").write_text(dump_yaml({"principals": [
        {"id": "vorstand", "user": "test-vorstand", "groups": ["Vorstand"]},
        {"id": "outsider", "user": "test-outsider", "groups": ["Gäste"]}]}), encoding="utf-8")
    ok("govern", "config")
    ok("govern", "policy", "seed", "--as", "gina")
    shown = json.loads(ok("govern", "policy", "show"))
    for p in {r["policy_id"] for r in shown}:
        ok("govern", "policy", "edit", p, "--exposure", "allowed", "--mask", "PERSON=pseudonymize", "--as", "gina")
        ok("govern", "policy", "approve", p, "--version", 2, "--reason", "Pilot", "--as", APPROVER.get(p, "ben"))
    ok("govern", "agent", "faq-bot", "--scope", "read:kb-and-sops", "--as", "gina")
    code, _, err = cli("govern", "agent", "greedy", "--scope", "*", "--as", "gina")
    assert code == 1 and "'*'" in err
    ok("govern", "acl-sync", "--as", "dirk")
    ok("govern", "scan", "--as", "dirk")
    ok("govern", "label", "export", "--category", "kb-and-sops", "-o", d / "labels.csv", "--as", "clara")
    ok("govern", "label", "import", d / "labels.csv", "--as", "clara")
    ok("govern", "review", "list")
    ok("govern", "mask", "--as", "dirk")
    ok("govern", "leak-test", "--as", "dirk")
    code, out, err = cli("govern", "check", "--as", "dirk")
    assert code == 1, err  # customer-records has no chunks (only the CRM table) and stays blocked
    status = ok("govern", "status")
    ok("audit", "verify")
    ok("discard", "log", "verify")
    ok("audit", "export", "--subject", "test-outsider", "-o", d / "art15.jsonl", "--as", "eva")
    ok("govern", "ropa", "-o", d / "ropa.yaml")
    ok("enrich", "run", "--as", "ben")  # acl_tags now come from the captured ACL
    return out, status, d


def _assert_governed(c06, out, status, d):
    cat = c06["cat"]
    assert "kb-and-sops: approved (policy v2) -> exposable" in status
    assert "customer-records: blocked" in status and "no_chunks" in status
    assert "LEAK" not in out
    rows = {r["category_id"]: r for r in cat.query("SELECT * FROM governance_status")}
    assert rows["kb-and-sops"]["status"] == "approved" and rows["customer-records"]["status"] == "blocked"
    idx = cat.query("SELECT * FROM agent_index")
    assert idx and all(sorted(r["allowed_principals"]) == ["group:Geschäftsstelle", "group:Vorstand"] for r in idx)
    exported = [json.loads(line) for line in (d / "art15.jsonl").read_text(encoding="utf-8").splitlines()]
    assert exported and all(json.loads(e["details"])["on_behalf_of"] == "test-outsider" for e in exported)
    ropa = load_yaml(d / "ropa.yaml")["art30_entry"]
    assert ropa["retention"]["audit_log_months"] == 6 and ropa["source_policies"]["kb-and-sops"] == 2
    tags = {r["acl_tags"] for r in cat.query("SELECT acl_tags FROM chunk_metadata WHERE subject_kind = 'chunk'")}
    assert tags == {json.dumps({"allow": ["group:Geschäftsstelle", "group:Vorstand"], "deny": []},
                               separators=(",", ":"), ensure_ascii=False)}
    steps = {r["step"] for r in cat.query("SELECT DISTINCT step FROM audit_event")}
    assert {"discard", "enrich", "govern"} <= steps and verify_chain(cat) is None


def test_govern_step_end_to_end(c06, cli):
    _assert_governed(c06, *govern_step(c06, cli))


@pytest.mark.postgres
def test_govern_step_on_postgres(pg_url, c06, cli):
    assert c06["cat"].dialect == "postgres"
    _assert_governed(c06, *govern_step(c06, cli))
    cat = connect(pg_url)
    with cat.transaction():  # the agent role sees nothing without a principal context
        cat.execute("SET LOCAL ROLE konmari_agent")
        assert cat.query("SELECT chunk_id FROM agent_index") == []
    cat.close()
