"""c05 cross-system entity resolution: the ER scenarios of
openspec/changes/c05-standardize-source-of-truth/specs/normalization/spec.md, on the synthetic pilot fixture."""

import csv
import json

import pytest

from konmari.catalog import now
from konmari.inventory.lineage import MemoryTransport
from konmari.normalize import store as nst
from konmari.policy import golden
from konmari.resolve import linkage as erl
from konmari.versions import Refused

from conftest import example_scope, write_mini

E = "customer"


def cluster_of(cat, key):
    src, rid = key.split(":", 1)
    return cat.one("SELECT cluster_id, link_origin FROM crosswalk WHERE entity = ? AND source = ? AND record_id = ?"
                   " AND active", (E, src, rid))


def test_known_cross_source_duplicates_are_scored(sot):
    import sot_fixture as fx

    scored = {(r["rec_a"], r["rec_b"]) for r in sot["cat"].query("SELECT rec_a, rec_b FROM er_pair")}
    known = fx.known_duplicates()
    assert len(scored & set(known)) / len(known) > 0.98


def test_high_probability_pair_auto_linked(sot):
    assert erl.route(0.995, 0.99, 0.50) == "auto"
    cat = sot["cat"]
    pair = cat.one("SELECT * FROM er_pair WHERE rec_a = 'crm:K-0301' AND rec_b = 'erp:D-0301'")
    assert pair["probability"] >= 0.99 and pair["origin"] == "auto"
    a, b = cluster_of(cat, "crm:K-0301"), cluster_of(cat, "erp:D-0301")
    assert a["cluster_id"] == b["cluster_id"] and a["link_origin"] == b["link_origin"] == "auto"


def test_borderline_pair_goes_to_review(sot):
    assert erl.route(0.80, 0.99, 0.50) == "review" and erl.route(0.49, 0.99, 0.50) is None
    cat = sot["cat"]
    queued = [(r["rec_a"], r["rec_b"]) for r in erl.queue(cat, E)]
    assert ("crm:K-0307", "erp:D-0307") in queued
    assert all(0.5 <= r["probability"] < 0.99 for r in erl.queue(cat, E))
    assert cluster_of(cat, "crm:K-0307")["cluster_id"] != cluster_of(cat, "erp:D-0307")["cluster_id"]
    erl.label_pairs(cat, sot["sc"], E, [("crm:K-0307", "erp:D-0307", "match")], "clara")
    assert cluster_of(cat, "crm:K-0307")["cluster_id"] == cluster_of(cat, "erp:D-0307")["cluster_id"]
    assert cluster_of(cat, "erp:D-0307")["link_origin"] == "review"


def test_reviewer_rejects_a_pair(sot):
    cat, sc = sot["cat"], sot["sc"]
    erl.label_pairs(cat, sc, E, [("crm:K-0357", "erp:D-0357", "non_match")], "clara", "different firms")
    erl.run(cat, sc, E, erl.load_config(sot["files"]["resolve"]), "ben")
    r = cat.one("SELECT * FROM er_pair WHERE rec_a = 'crm:K-0357' AND rec_b = 'erp:D-0357'")
    assert r["label"] == "non_match" and r["reviewer"] == "clara"
    assert cluster_of(cat, "crm:K-0357")["cluster_id"] != cluster_of(cat, "erp:D-0357")["cluster_id"]
    ev = cat.one("SELECT * FROM audit_event WHERE action = 'resolve.review' AND details LIKE '%K-0357%'")
    d = json.loads(ev["details"])
    assert (ev["actor"], d["new"], d["reason"]) == ("clara", "non_match", "different firms")
    with pytest.raises(Refused, match="stakeholder"):
        erl.label_pairs(cat, sc, E, [("crm:K-0407", "erp:D-0407", "match")], "mallory")


def test_review_csv_round_trip(sot, tmp_path):
    cat = sot["cat"]
    out = tmp_path / "pairs.csv"
    n = erl.export_review(cat, E, out, sample=5)
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert n == len(rows) == 12 + 5 and rows[0]["kind"] == "review" and rows[-1]["kind"] == "precision_sample"
    for r in rows:
        r["label"] = "match"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    assert erl.import_review(cat, sot["sc"], out, "clara") == 17
    assert not erl.queue(cat, E)


def test_within_source_duplicates_come_from_dedup_detection(cat, tmp_path):
    fields = [{"source": s, "field": f"{o}.name", "entity": E, "attribute": "legal_name", "type": "name",
               "owner": "ben", "parser": {"transliterate_de": True, "strip_legal_forms": True}} for s, o in
              (("crm", "kontakt"), ("erp", "debitor"))]
    fields += [{"source": s, "field": f"{o}.tel", "entity": E, "attribute": "phone", "type": "phone", "owner": "ben",
                "parser": {"region": "DE"}} for s, o in (("crm", "kontakt"), ("erp", "debitor"))]
    recs = [{"source": "crm", "object": "kontakt", "record_id": rid, "values": {"name": "Jäger KG", "tel": "030 2345678"}}
            for rid in ("K-1", "K-1b")]
    recs.append({"source": "erp", "object": "debitor", "record_id": "D-1",
                 "values": {"name": "Jaeger KG", "tel": "+49 30 2345678"}})
    rec, specs = write_mini(tmp_path / "m", recs, fields)
    nst.run(cat, rec, specs, "ben", MemoryTransport())
    cat.execute("INSERT INTO cluster VALUES ('c04-1', 'r', 'exact', ?, 'K-1', 'completeness')",
                (json.dumps(["K-1", "K-1b"]),))
    cfg = {"entities": {E: {"blocking": [["phone"]], "comparisons": [
        {"attribute": "legal_name", "method": "exact", "m": [0.9, 0.1], "u": [0.001, 0.999]},
        {"attribute": "phone", "method": "exact", "m": [0.9, 0.1], "u": [0.001, 0.999]}]}}}
    rep = erl.run(cat, example_scope(), E, cfg, "ben")
    assert rep["dedup_links"] == 1
    pairs = {(r["rec_a"], r["rec_b"]) for r in cat.query("SELECT * FROM er_pair")}
    assert pairs == {("crm:K-1", "erp:D-1")}  # K-1b is not re-decided here
    assert len({cluster_of(cat, k)["cluster_id"] for k in ("crm:K-1", "crm:K-1b", "erp:D-1")}) == 1
    assert cluster_of(cat, "crm:K-1b")["link_origin"] == "dedup"


def test_thresholds_and_floors_come_from_success_metrics(cat):
    sc = example_scope()
    cat.execute("INSERT INTO metric_set_version VALUES (?, 1, 'approved', '{}', 'h', ?, 'ben', NULL, NULL)",
                (sc.charter_id, now()))
    for mid, target in (("er_upper_threshold", 0.97), ("er_precision", 0.9)):
        cat.execute("INSERT INTO metric_definition VALUES (?, 1, ?, ?, 'data_readiness', 'ratio', '>=', ?, 'target',"
                    " 'ben', 'run')", (sc.charter_id, mid, mid, target))
    t = erl.success_targets(cat, sc)
    assert (t["upper"], t["lower"], t["precision"], t["recall"]) == (0.97, 0.5, 0.9, 0.95)


# ---------------------------------------------------------------- 3.4 quality gate

def test_precision_below_floor_blocks_publication(sot, cli):
    import sot_fixture as fx

    cat, sc = sot["cat"], sot["sc"]
    auto = cat.query("SELECT rec_a, rec_b FROM er_pair WHERE origin = 'auto' ORDER BY rec_a, rec_b")
    cat.execute("UPDATE er_pair SET label = NULL")
    labels = [(r["rec_a"], r["rec_b"], "non_match" if i < 5 else "match") for i, r in enumerate(auto[:100])]
    erl.label_pairs(cat, sc, E, labels, "clara")
    erl.run(cat, sc, E, erl.load_config(sot["files"]["resolve"]), "ben")
    with pytest.raises(Refused) as e:
        erl.publish(cat, sc, E, "ben", fx.known_duplicates())
    assert "precision 0.95 is below the floor 0.98" in e.value.reasons[0]
    assert not erl.published(cat, E)


def test_missing_recall_evidence(sot):
    cat, sc = sot["cat"], sot["sc"]
    erl.run(cat, sc, E, erl.load_config(sot["files"]["resolve"]), "ben")
    assert erl.quality(cat, sc, E, None)["recall"] == "unmeasured"
    with pytest.raises(Refused, match="recall unmeasured"):
        erl.publish(cat, sc, E, "ben")
    with pytest.raises(Refused, match="not a data owner"):
        erl.publish(cat, sc, E, "finn", override_reason="owner has no list yet")
    rep = erl.publish(cat, sc, E, "ben", override_reason="owner has no list yet")
    assert rep["recall"] == "unmeasured" and erl.published(cat, E)
    ev = cat.query("SELECT * FROM audit_event WHERE action = 'resolve.publish' ORDER BY seq")[-1]
    d = json.loads(ev["details"])
    assert (ev["actor"], d["override"], d["reason"]) == ("ben", True, "owner has no list yet")
    r = cat.one("SELECT * FROM er_run ORDER BY created_at DESC LIMIT 1")
    assert r["override_reason"] == "owner has no list yet" and json.loads(r["metrics"])["quality"]["precision"] == 1.0


def test_run_records_model_version_thresholds_and_metrics(sot):
    r = sot["cat"].one("SELECT * FROM er_run ORDER BY created_at LIMIT 1")
    assert r["model_version"].startswith("splink-5.0.0:") and (r["upper_threshold"], r["lower_threshold"]) == (0.99, 0.5)
    q = json.loads(r["metrics"])["quality"]
    assert q["precision"] == 1.0 and q["recall"] >= 0.95 and q["labelled"] == 120


# ---------------------------------------------------------------- 3.5 split and restore

def test_wrongly_merged_entities_are_split(sot):
    cat, sc = sot["cat"], sot["sc"]
    cid = cluster_of(cat, "crm:K-S1")["cluster_id"]
    assert cluster_of(cat, "erp:D-S2")["cluster_id"] == cid  # the sister companies were auto-linked
    assert {g["source"] for g in golden.record(cat, E, cid)} >= {"crm", "erp"}
    rep = erl.split(cat, sc, E, cid, ["erp:D-S2"], "clara", "two sister companies at one address")
    a, b = cluster_of(cat, "crm:K-S1")["cluster_id"], cluster_of(cat, "erp:D-S2")["cluster_id"]
    assert a != b and set(rep["clusters"]) == {a, b}
    assert {g["source"] for g in golden.record(cat, E, a)} == {"crm"}
    assert {g["source"] for g in golden.record(cat, E, b)} == {"erp"}
    assert cat.one("SELECT * FROM er_cluster WHERE cluster_id = ? AND retired_at IS NOT NULL", (cid,))
    assert cat.one("SELECT * FROM audit_event WHERE action = 'resolve.split'")["actor"] == "clara"
    erl.restore(cat, sc, rep["split_id"], "clara", "the owner confirmed one firm after all")
    assert cluster_of(cat, "crm:K-S1")["cluster_id"] == cluster_of(cat, "erp:D-S2")["cluster_id"] == cid
    assert {g["source"] for g in golden.record(cat, E, cid)} >= {"crm", "erp"}


def test_split_needs_a_proper_subset(sot):
    cat = sot["cat"]
    cid = cluster_of(cat, "crm:K-S1")["cluster_id"]
    with pytest.raises(Refused, match="proper subset"):
        erl.split(cat, sot["sc"], E, cid, ["crm:K-S1", "erp:D-S2"], "clara", "x")


# ---------------------------------------------------------------- 3.6 regression

def test_splink_regression_pins_cluster_counts(sot):
    """Pinned on the synthetic fixture: a change here means Splink, the model config or the fixture drifted."""
    er = sot["er"]
    assert (er["records"], er["pairs"], er["auto"], er["review"], er["clusters"]) == (1702, 801, 789, 12, 1011)
