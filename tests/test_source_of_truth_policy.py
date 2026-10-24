"""c05 source-of-truth policy, golden records and conflict register: scenarios from
openspec/changes/c05-standardize-source-of-truth/specs/source-of-truth-policy/spec.md (synthetic data only)."""

import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from konmari.canonical import dump_yaml, load_yaml
from konmari.catalog import now
from konmari.normalize import store as nst
from konmari.policy import conflicts as cf
from konmari.policy import golden
from konmari.policy import store as pst
from konmari.standardize import MASK, latest_metrics
from konmari.versions import Refused

E = "customer"
OL = Path(__file__).parent / "fixtures" / "openlineage"


def cid_of(cat, source, rid):
    return cat.one("SELECT cluster_id FROM crosswalk WHERE source = ? AND record_id = ? AND active",
                   (source, rid))["cluster_id"]


def attr(cat, cid, field):
    return cat.one("SELECT * FROM golden_attribute WHERE entity = ? AND cluster_id = ? AND field = ?", (E, cid, field))


def edited_policy(tmp_path, fn) -> Path:
    import sot_fixture as fx

    doc = load_yaml(fx.FIXTURES / "policy" / "customer.yaml")
    fn(doc)
    p = tmp_path / "customer.yaml"
    p.write_text(dump_yaml(doc), encoding="utf-8")
    return p


def legal_name_conflict(cat):
    cid = cid_of(cat, "erp", "D-0300")  # i % 10 == 0: ERP writes the name in capitals
    return cid, next(c for c in cf.entries(cat, E) if c["cluster_id"] == cid and c["field"] == "legal_name")


def personal_asset(cat):
    ts = now()
    cat.execute("INSERT INTO source_asset (asset_uri, source_id, charter_id, system_type, origin, title,"
                " update_frequency, sensitivity_tier, ropa_missing, first_seen, last_seen, status)"
                " VALUES ('manual://crm/kontakte', 'crm', 'verein-faq-assistant', 'crm', 'manual', 'Kontakte', 'P1D',"
                " 'personal', FALSE, ?, ?, 'complete')", (ts, ts))


# ---------------------------------------------------------------- 4.1 policy schema

def test_crm_wins_contact_preferences_erp_wins_billing_terms(sot):
    cat = sot["cat"]
    cid = cid_of(cat, "crm", "K-0300")
    assert cid == cid_of(cat, "erp", "D-0300")
    cp, pt = attr(cat, cid, "contact_preference"), attr(cat, cid, "payment_terms")
    assert (cp["source"], pt["source"]) == ("crm", "erp")
    assert (pt["value"], pt["rule"]) == ("30 Tage netto", "source_priority")


def test_most_recent_requires_a_timestamp_field(tmp_path):
    p = edited_policy(tmp_path, lambda d: d["rules"]["updated_at"].pop("timestamp_field"))
    with pytest.raises(Refused) as e:
        pst.read_policy(p)
    assert any("customer.updated_at: most_recent needs a 'timestamp_field'" in r for r in e.value.reasons)
    assert pst.policy_errors({"entity": E, "rules": {"x": {"strategy": "last_write_wins", "owner": "ben",
                                                           "tie_breaker": "owner_decision", "rationale": "r"}}})


# ---------------------------------------------------------------- 4.3 coverage

def test_uncovered_field_blocks_publication(sot_cli, cli, tmp_path):
    sot = sot_cli
    exp = tmp_path / "exposure.yaml"
    exp.write_text(dump_yaml({"exposed": {E: ["legal_name", "phone", "email"]}}), encoding="utf-8")
    code, out, err = cli("policy", "check", "--exposure", exp)
    assert code == 1 and "customer.email" in err and "customer.phone" not in err
    before = sot["cat"].one("SELECT MAX(selected_at) AS t FROM golden_attribute")["t"]
    code, out, err = cli("policy", "build", E, "--exposure", exp)
    assert code == 1 and "customer.email" in err
    assert sot["cat"].one("SELECT MAX(selected_at) AS t FROM golden_attribute")["t"] == before
    code, out, err = cli("policy", "check", "--exposure", sot["files"]["exposure"])
    assert code == 0, err


# ---------------------------------------------------------------- 4.2 lifecycle

def test_unapproved_change_stays_inactive(sot, tmp_path):
    cat, sc = sot["cat"], sot["sc"]
    p = edited_policy(tmp_path, lambda d: d["rules"]["payment_terms"].update(sources=["crm", "erp"]))
    row = pst.load(cat, sc, p, "dirk")
    assert (row["version"], row["status"], row["active"]) == (2, "pending", False)
    assert pst.active_policy(cat, E)["version"] == 1
    cid = cid_of(cat, "crm", "K-0300")
    assert attr(cat, cid, "payment_terms")["source"] == "erp"
    impact = json.loads(row["impact"])
    assert {i["field"] for i in impact} == {"payment_terms"} and any(
        i["cluster_id"] == cid and i["from"] == "30 Tage netto" and i["to"] == "14 Tage" for i in impact)
    assert any("payment_terms.sources" in d for d in json.loads(row["diff"]))
    with pytest.raises(Refused, match="field owner"):
        pst.approve(cat, sc, E, 2, "clara")


def test_rollback_restores_previous_winners(sot, tmp_path):
    cat, sc = sot["cat"], sot["sc"]
    p = edited_policy(tmp_path, lambda d: d["rules"]["payment_terms"].update(sources=["crm", "erp"]))
    pst.load(cat, sc, p, "ben")
    pst.approve(cat, sc, E, 2, "ben")
    cid = cid_of(cat, "crm", "K-0300")
    assert (attr(cat, cid, "payment_terms")["source"], attr(cat, cid, "payment_terms")["policy_version"]) == ("crm", 2)
    pst.rollback(cat, sc, E, 1, "ben", "accounting objected")
    assert (attr(cat, cid, "payment_terms")["source"], attr(cat, cid, "payment_terms")["policy_version"]) == ("erp", 1)
    ev = cat.query("SELECT * FROM audit_event WHERE action = 'policy.rollback'")[-1]
    d = json.loads(ev["details"])
    assert (ev["actor"], d["previous"], d["new"], d["reason"]) == ("ben", 2, 1, "accounting objected")
    with pytest.raises(Refused, match="already active"):
        pst.rollback(cat, sc, E, 1, "ben", "again")


# ---------------------------------------------------------------- 4.4 / 4.5 golden records and lineage

def test_provenance_on_every_attribute(sot):
    cat = sot["cat"]
    cid = cid_of(cat, "crm", "K-0301")
    rows = golden.record(cat, E, cid)
    assert {r["field"] for r in rows} >= {"contact_preference", "payment_terms", "phone", "postcode", "updated_at"}
    for r in rows:
        assert r["status"] == "resolved" and r["source"] and r["record_id"] and r["rule"] and r["selected_at"]
        assert r["policy_version"] == 1 and r["source_field"].startswith(r["source"] + ".")
    assert attr(cat, cid, "updated_at")["source_ts"]


def _validator():
    reg = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in
                                     (json.loads(p.read_text()) for p in OL.glob("*.json"))])
    ol = json.loads((OL / "OpenLineage-2-0-2.json").read_text())
    facet = json.loads((OL / "ColumnLineageDatasetFacet-1-2-0.json").read_text())
    run_event = {"$ref": f"{ol['$id']}#/$defs/RunEvent"}
    return (Draft202012Validator(run_event, registry=reg),
            Draft202012Validator({"$ref": f"{facet['$id']}#/$defs/ColumnLineageDatasetFacet"}, registry=reg))


def test_lineage_event_emitted(sot):
    rep = golden.build(sot["cat"], sot["sc"], E, "ben", transport=sot["transport"])
    ev = sot["transport"].events[-1]
    assert ev is rep["lineage"] or ev == rep["lineage"]
    event_v, facet_v = _validator()
    for e in (ev, sot["events"][0]):  # golden build and the normalization run
        assert e["eventType"] == "COMPLETE"
        assert not list(event_v.iter_errors(e))
        assert not list(facet_v.iter_errors(e["outputs"][0]["facets"]["columnLineage"]))
    fields = ev["outputs"][0]["facets"]["columnLineage"]["fields"]
    inputs = {f"{i['namespace'].removeprefix('konmari://')}.{i['name']}.{i['field']}"
              for i in fields["payment_terms"]["inputFields"]}
    assert "erp.debitor.zahlungsbedingung" in inputs
    assert fields["payment_terms"]["inputFields"][0]["transformations"][0] == {"type": "DIRECT", "subtype": "IDENTITY"}
    norm = sot["events"][0]["outputs"][0]["facets"]["columnLineage"]["fields"]
    assert norm["erp.debitor.zahlungsbedingung"]["inputFields"][0]["transformations"][0]["subtype"] == "TRANSFORMATION"


# ---------------------------------------------------------------- 5.1 detection

def test_normalization_only_difference_is_no_conflict(sot):
    cat = sot["cat"]
    vals = nst.current_values(cat, E)
    assert vals["crm:K-0301"]["phone"]["raw"] != vals["erp:D-0301"]["phone"]["raw"]
    assert vals["crm:K-0301"]["phone"]["canonical"] == vals["erp:D-0301"]["phone"]["canonical"]
    assert not [c for c in cf.entries(cat, E) if c["field"] == "phone"]


def test_undecidable_disagreement_creates_conflict(sot):
    cat = sot["cat"]
    cid, c = legal_name_conflict(cat)
    assert c["status"] == "open" and {x["source"] for x in c["candidates"]} == {"crm", "erp"}
    assert attr(cat, cid, "legal_name")["status"] == "unresolved" and attr(cat, cid, "legal_name")["value"] is None
    overridden = [x for x in cf.entries(cat, E) if x["cluster_id"] == cid and x["field"] == "contact_preference"]
    assert overridden and overridden[0]["status"] == "overridden"


# ---------------------------------------------------------------- 5.2 / 5.3 resolution and reopening

def test_owner_resolves_with_write_back(sot_cli, cli):
    sot = sot_cli
    cat = sot["cat"]
    cid, c = legal_name_conflict(cat)
    erp_value = next(x["value"] for x in c["candidates"] if x["source"] == "erp")
    code, out, err = cli("conflicts", "resolve", c["conflict_id"], "--source", "erp", "--ticket", "IT-4711",
                         "--as", "clara")
    assert code == 1 and "only the field owner 'ben'" in err
    code, out, err = cli("conflicts", "resolve", c["conflict_id"], "--source", "erp", "--ticket", "IT-4711",
                         "--as", "ben")
    assert code == 0, err
    g = attr(cat, cid, "legal_name")
    assert (g["value"], g["origin"], g["status"], g["source"]) == (erp_value, "owner_decision", "resolved", "erp")
    e = cf.get(cat, c["conflict_id"])
    assert (e["status"], e["ticket"], e["approver"]) == ("resolved", "IT-4711", "ben") and e["decided_at"]
    out_csv = sot["tmp"] / "tickets.csv"
    assert cli("conflicts", "export-tickets", "-o", out_csv)[0] == 0 and "IT-4711" in out_csv.read_text()
    code, out, err = cli("conflicts", "revert", c["conflict_id"], "--reason", "wrong pick", "--as", "ben")
    assert code == 0, err
    assert cf.get(cat, c["conflict_id"])["status"] == "open"
    assert attr(cat, cid, "legal_name")["status"] == "unresolved"


def _renormalize(sot):
    """A later run where nothing was corrected at the source (one new unrelated record)."""
    rec = sot["tmp"] / "records2.jsonl"
    extra = {"source": "sharepoint", "object": "kundenliste", "record_id": "9999",
             "values": {"titel": "Neu Verein e.V.", "plz": "99999"}}
    rec.write_text(sot["files"]["records"].read_text(encoding="utf-8") + json.dumps(extra) + "\n", encoding="utf-8")
    nst.run(sot["cat"], rec, sot["files"]["specs"], "ben", sot["transport"])


def test_recurring_conflict_reopens(sot):
    cat, sc = sot["cat"], sot["sc"]
    cid, c = legal_name_conflict(cat)
    other = next(x for x in cf.entries(cat, E) if x["field"] == "legal_name" and x["conflict_id"] != c["conflict_id"])
    cf.decide(cat, sc, c["conflict_id"], "ben", source="erp", ticket="IT-4711")
    cf.decide(cat, sc, other["conflict_id"], "ben", source="crm", accept_reason="ERP prints the short form on purpose")
    golden.build(cat, sc, E, "ben")
    assert cf.get(cat, c["conflict_id"])["status"] == "resolved"  # same run: nothing new to detect
    _renormalize(sot)
    golden.build(cat, sc, E, "ben")
    assert cf.get(cat, c["conflict_id"])["status"] == "reopened"
    assert cf.get(cat, other["conflict_id"])["status"] == "accepted_deviation"
    note = [json.loads(line) for line in sot["notify"].read_text().splitlines()]
    assert note[-1]["to"] == "ben" and note[-1]["conflict_id"] == c["conflict_id"] and note[-1]["ticket"] == "IT-4711"
    assert cat.one("SELECT * FROM audit_event WHERE action = 'conflicts.reopen'")["actor"] == "ben"
    assert attr(cat, cid, "legal_name")["status"] == "unresolved"


# ---------------------------------------------------------------- 5.4 / 5.5 personal data

def test_overdue_personal_data_conflict_reported(sot, cli):
    cat, sc = sot["cat"], sot["sc"]
    personal_asset(cat)
    golden.build(cat, sc, E, "ben")
    _, c = legal_name_conflict(cat)
    assert c["personal_data"] and c["due_date"] == c["detected_at"].date() + timedelta(days=30)
    code, out, _ = cli("conflicts", "report", "--overdue")
    assert code == 0 and c["conflict_id"] not in out
    cat.execute("UPDATE conflict SET due_date = ?, detected_at = ? WHERE conflict_id = ?",
                (date.today() - timedelta(days=1), now() - timedelta(days=31), c["conflict_id"]))
    code, out, _ = cli("conflicts", "report", "--overdue")
    line = next(x for x in out.splitlines() if c["conflict_id"] in x)
    assert "owner ben" in line and "31 days old" in line and "personal data" in line


def test_masked_for_unauthorized_viewers(sot):
    cat, sc = sot["cat"], sot["sc"]
    personal_asset(cat)
    golden.build(cat, sc, E, "ben")
    _, c = legal_name_conflict(cat)

    def values(viewer):
        e = next(x for x in cf.listing(cat, sc, viewer) if x["conflict_id"] == c["conflict_id"])
        return {x["value"] for x in e["candidates"]}
    assert values("finn") == {MASK} and values(None) == {MASK}
    assert MASK not in values("ben") and MASK not in values("eva")  # field owner and compliance
    nst.quarantine_list(cat, sc, "finn")  # non-personal quarantine stays readable
    q = [x for x in nst.quarantine_list(cat, sc, "finn", None) if x["source_field"] == "crm.kontakt.firma"]
    assert all(x["raw"] == MASK for x in q)


# ---------------------------------------------------------------- 6.1 - 6.3 integration

def test_agent_sees_unresolved_status(sot):
    cat = sot["cat"]
    cid, c = legal_name_conflict(cat)
    r = golden.serve(cat, E, cid, "legal_name")
    assert (r["status"], r["value"], r["candidate_sources"]) == ("unresolved", None, ["crm", "erp"])
    ok = golden.serve(cat, E, cid, "payment_terms")
    assert (ok["status"], ok["value"], ok["source"], ok["rule"]) == ("resolved", "30 Tage netto", "erp",
                                                                     "source_priority")


def test_resolution_appears_in_audit_log(sot):
    cat, sc = sot["cat"], sot["sc"]
    cid, c = legal_name_conflict(cat)
    cf.decide(cat, sc, c["conflict_id"], "ben", value="Muster Müller GmbH", ticket="IT-4712")
    ev = cat.query("SELECT * FROM audit_event WHERE action = 'conflicts.resolve'")[-1]
    d = json.loads(ev["details"])
    assert ev["actor"] == "ben" and ev["ts"] and ev["step"] == "standardize"
    assert d["previous"]["status"] == "unresolved" and d["new"] == {"value": "Muster Müller GmbH",
                                                                    "status": "resolved", "origin": "owner_decision"}
    assert d["reason"] == "write-back ticket IT-4712"


def test_each_action_type_is_audited(sot, tmp_path):
    cat, sc = sot["cat"], sot["sc"]
    _, c = legal_name_conflict(cat)
    cf.decide(cat, sc, c["conflict_id"], "ben", source="crm", ticket="IT-1")
    cf.revert(cat, sc, c["conflict_id"], "ben", "undo")
    q = cat.one("SELECT qid FROM quarantine WHERE status = 'open' LIMIT 1")["qid"]
    nst.resolve_quarantine(cat, sc, q, "ben", unfixable=True, reason="paper form lost")
    actions = {r["action"]: r for r in cat.query("SELECT * FROM audit_event WHERE step = 'standardize'")}
    for a in ("policy.load", "policy.approve", "resolve.review", "resolve.publish", "normalize.quarantine_resolve",
              "conflicts.resolve", "conflicts.revert", "conflicts.detect"):
        d = json.loads(actions[a]["details"])
        assert actions[a]["actor"] and actions[a]["ts"] and {"previous", "new", "reason"} <= set(d), a


def test_metrics_published_for_scorecard_and_monitoring(sot):
    m = {(r["kind"], r["name"]) for r in latest_metrics(sot["cat"])}
    assert {("conformance", "canonical_rate"), ("er", "precision"), ("er", "recall"),
            ("conflicts", "open"), ("conflicts", "overdue")} <= m
    pst.coverage(sot["cat"], {E: ["legal_name", "email"]})
    cov = [r for r in latest_metrics(sot["cat"], "policy_coverage") if r["entity"] == E]
    assert cov[0]["value"] == 0.5
