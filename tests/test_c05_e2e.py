"""c05 end to end through the CLI: normalize -> resolve -> review -> publish -> policy approve -> golden records ->
conflicts, then a full rollback that leaves the raw data unchanged. Synthetic data only."""

import csv
import hashlib
import json

import pytest

import sot_fixture as fx
from konmari.catalog import connect, verify_chain
from konmari.inventory.lineage import MemoryTransport
from konmari.normalize import store as nst
from konmari.policy import conflicts as cf
from konmari.policy import golden
from konmari.policy import store as pst
from konmari.resolve import linkage as erl

from conftest import example_scope, write_mini


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_end_to_end_with_full_rollback(engagement, cli, tmp_path):
    def ok(*args):
        code, out, err = cli(*args)
        assert code == 0, (args, err)
        return out

    ok("charter", "approve", "--as", "anna")
    f = fx.write(tmp_path / "pilot")
    raw_before = sha(f["records"])
    out = ok("normalize", "--records", f["records"], "--specs", f["specs"], "--as", "ben")
    run_id = out.split()[1]
    ok("resolve", "run", "--entity", "customer", "--config", f["resolve"], "--as", "ben")
    pairs = tmp_path / "pairs.csv"
    ok("resolve", "review", "export", "--entity", "customer", "-o", pairs, "--sample", 100)
    known = set(fx.known_duplicates())
    rows = list(csv.DictReader(pairs.open(encoding="utf-8")))
    for r in rows:
        r["label"] = "match" if (r["rec_a"], r["rec_b"]) in known else "non_match"
    with pairs.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    ok("resolve", "review", "import", pairs, "--as", "clara")
    code, _, err = cli("resolve", "publish", "--entity", "customer", "--as", "ben")
    assert code == 1 and "recall unmeasured" in err
    out = ok("resolve", "publish", "--entity", "customer", "--known", f["known"], "--as", "ben")
    assert "recall 1.0" in out  # the review queue was accepted, so every known duplicate is linked
    out = ok("policy", "load", "--file", f["policy"], "--as", "ben")
    assert "version 1 is pending" in out
    ok("policy", "approve", "customer", "--version", 1, "--as", "ben")
    ok("policy", "check", "--exposure", f["exposure"])
    ok("policy", "build", "customer", "--exposure", f["exposure"])
    cat = connect()
    cid = cat.one("SELECT cluster_id FROM crosswalk WHERE record_id = 'D-0300' AND active")["cluster_id"]
    served = {r["field"]: r for r in json.loads(ok("policy", "golden", "customer", cid))}
    assert served["legal_name"]["status"] == "unresolved" and served["payment_terms"]["source"] == "erp"
    conflict = next(c for c in cf.entries(cat, "customer") if c["cluster_id"] == cid and c["field"] == "legal_name")
    assert conflict["conflict_id"] in ok("conflicts", "list", "--status", "open", "--as", "ben")
    ok("conflicts", "resolve", conflict["conflict_id"], "--source", "erp", "--ticket", "IT-4711", "--as", "ben")
    served = json.loads(ok("policy", "golden", "customer", cid, "--field", "legal_name"))[0]
    assert (served["status"], served["origin"]) == ("resolved", "owner_decision")
    raw = cat.one("SELECT raw FROM normalized_value WHERE record_id = 'D-0300' AND attribute = 'legal_name'")["raw"]
    assert raw == next(r for r in nst.read_records(f["records"]) if r["record_id"] == "D-0300")["values"]["name"]

    # full rollback: decisions reverted, canonical rows dropped, raw data untouched
    ok("conflicts", "revert", conflict["conflict_id"], "--reason", "e2e rollback", "--as", "ben")
    ok("normalize", "rollback", run_id, "--reason", "e2e rollback", "--as", "ben")
    assert cat.one("SELECT COUNT(*) AS n FROM normalized_value WHERE run_id = ?", (run_id,))["n"] == 0
    assert cat.one("SELECT COUNT(*) AS n FROM quarantine WHERE run_id = ?", (run_id,))["n"] == 0
    assert sha(f["records"]) == raw_before
    assert verify_chain(cat) is None  # the hash-chained audit trail is intact
    actions = [r["action"] for r in cat.query("SELECT action FROM audit_event WHERE step = 'standardize' ORDER BY seq")]
    assert actions[-2:] == ["conflicts.revert", "normalize.rollback"]


def _mini_pipeline(cat, tmp_path):
    sc = example_scope()
    fields = [{"source": s, "field": f"{o}.{c}", "entity": "customer", "attribute": a, "type": t, "owner": "ben",
               "parser": p} for s, o in (("crm", "kontakt"), ("erp", "debitor"))
              for c, a, t, p in (("name", "legal_name", "name", {"transliterate_de": True}),
                                 ("tel", "phone", "phone", {"region": "DE"}),
                                 ("terms", "payment_terms", "text", {}))]
    recs = [{"source": "crm", "object": "kontakt", "record_id": "K-1",
             "values": {"name": "Jäger KG", "tel": "030 2345678", "terms": "14 Tage"}},
            {"source": "erp", "object": "debitor", "record_id": "D-1",
             "values": {"name": "JAEGER KG", "tel": "+49 30 2345678", "terms": "30 Tage"}}]
    rec, specs = write_mini(tmp_path / "m", recs, fields)
    nst.run(cat, rec, specs, "ben", MemoryTransport())
    cfg = {"entities": {"customer": {"prior": 0.1, "blocking": [["phone"]], "comparisons": [
        {"attribute": "phone", "method": "exact", "m": [0.95, 0.05], "u": [0.0001, 0.9999]}]}}}
    erl.run(cat, sc, "customer", cfg, "ben")
    erl.label_pairs(cat, sc, "customer", [("crm:K-1", "erp:D-1", "match")], "clara")
    erl.publish(cat, sc, "customer", "ben", override_reason="two-record smoke test")
    pol = tmp_path / "policy.yaml"
    pol.write_text(json.dumps({"entity": "customer", "rules": {
        "legal_name": {"strategy": "owner_decision", "tie_breaker": "owner_decision", "owner": "ben",
                       "rationale": "r"},
        "payment_terms": {"strategy": "source_priority", "sources": ["erp", "crm"], "tie_breaker": "owner_decision",
                          "owner": "ben", "rationale": "r"}}}), encoding="utf-8")
    pst.load(cat, sc, pol, "ben")
    pst.approve(cat, sc, "customer", 1, "ben")
    assert not golden.record(cat, "customer", cat.one("SELECT cluster_id FROM crosswalk LIMIT 1")["cluster_id"])
    golden.build(cat, sc, "customer", "ben")
    cid = cat.one("SELECT cluster_id FROM crosswalk WHERE active LIMIT 1")["cluster_id"]
    assert golden.serve(cat, "customer", cid, "payment_terms")["value"] == "30 Tage"
    c = cf.entries(cat, "customer")
    open_ = [x for x in c if x["status"] == "open"]
    assert len(open_) == 1 and open_[0]["field"] == "legal_name"
    cf.decide(cat, sc, open_[0]["conflict_id"], "ben", source="crm", ticket="IT-4711")
    assert golden.serve(cat, "customer", cid, "legal_name")["value"] == "Jäger KG"
    cf.revert(cat, sc, open_[0]["conflict_id"], "ben")
    assert golden.serve(cat, "customer", cid, "legal_name")["status"] == "unresolved"
    nst.rollback(cat, nst.latest_run_id(cat), "ben", "smoke")
    assert cat.one("SELECT COUNT(*) AS n FROM normalized_value")["n"] == 0


def test_mini_pipeline_duckdb(tmp_path):
    _mini_pipeline(connect(f"duckdb:///{tmp_path}/c.duckdb"), tmp_path)


@pytest.mark.postgres
def test_mini_pipeline_postgres(pg_url, tmp_path):
    _mini_pipeline(connect(pg_url), tmp_path)
