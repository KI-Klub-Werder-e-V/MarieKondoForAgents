"""c05 normalization: scenarios from openspec/changes/c05-standardize-source-of-truth/specs/normalization/spec.md
(entity resolution scenarios are in test_entity_resolution.py). All records are synthetic."""

import json
import tomllib
from pathlib import Path

import pytest
from stdnum.iso7064 import mod_11_10

from konmari.catalog import connect
from konmari.canonical import load_yaml
from konmari.normalize import store as nst
from konmari.normalize.normalizers import Quarantined, normalize
from konmari.inventory.lineage import MemoryTransport
from konmari.versions import Refused

from conftest import example_scope, write_mini

ROOT = Path(__file__).parent.parent
C05 = {"normalization_spec", "normalize_run", "normalized_value", "quarantine", "er_run", "er_pair", "er_cluster",
       "crosswalk", "policy_version", "policy_rule", "golden_attribute", "conflict", "standardize_metric"}
CRM_DATE = {"source": "crm", "field": "kontakt.geburtstag", "entity": "customer", "attribute": "founded",
            "type": "date", "owner": "ben", "parser": {"pattern": "DD.MM.YYYY"}}


def q(type_, raw, **parser):
    try:
        return normalize(type_, raw, parser)
    except Quarantined as e:
        return e.reason


# ---------------------------------------------------------------- 1. setup: dependencies, migrations, fixture

def test_c05_dependencies_are_pinned():
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    pinned = {d.split("==")[0] for d in deps if "==" in d}
    assert {"splink", "python-stdnum", "phonenumbers", "pint", "ucumvert", "openlineage-python"} <= pinned


def _tables(cat):
    return {r["table_name"] for r in cat.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')")}


def _up_down_up(cat):
    assert C05 <= _tables(cat)
    cat.rollback("005_c05_standardize_source_of_truth.sql")
    assert not C05 & _tables(cat) and "corpus_item" in _tables(cat)
    cat.migrate()
    assert C05 <= _tables(cat)


def test_migration_up_down_up_duckdb(tmp_path):
    _up_down_up(connect(f"duckdb:///{tmp_path}/c.duckdb"))


@pytest.mark.postgres
def test_migration_up_down_up_postgres(pg_url):
    _up_down_up(connect(pg_url))


def test_synthetic_fixture_has_known_duplicates_and_edge_cases(sot):
    recs = nst.read_records(sot["files"]["records"])
    assert {r["source"] for r in recs} == {"crm", "erp", "sharepoint"} and 1000 <= len(recs) <= 2000
    reasons = {r["reason"] for r in sot["cat"].query("SELECT reason FROM quarantine")}
    assert {"ambiguous_local_time", "nonexistent_local_time", "invalid_checksum", "incompatible_dimension"} <= reasons
    assert "example.org" in json.dumps(recs) and len((sot["files"]["known"]).read_text().splitlines()) > 500


# ---------------------------------------------------------------- 2.1 / 2.2 specs and runs

def test_field_without_spec_is_rejected(engagement, cli, tmp_path):
    cli("charter", "approve", "--as", "anna")
    rec, specs = write_mini(tmp_path / "m", [
        {"source": "crm", "object": "kontakt", "record_id": "K-1", "values": {"geburtstag": "03.04.2025"}},
        {"source": "crm", "object": "contact", "record_id": "K-1", "values": {"birthdate": "1980-01-01"}}], [CRM_DATE])
    code, out, err = cli("normalize", "--records", rec, "--specs", specs, "--as", "ben")
    assert code == 1 and "crm.contact.birthdate" in err
    cat = connect()
    assert cat.one("SELECT COUNT(*) AS n FROM normalized_value")["n"] == 0
    assert cat.one("SELECT COUNT(*) AS n FROM normalize_run")["n"] == 0


def test_spec_versions_are_recorded(cat, tmp_path):
    rec, specs = write_mini(tmp_path / "m", [
        {"source": "crm", "object": "kontakt", "record_id": "K-1", "values": {"geburtstag": "03.04.2025"}}], [CRM_DATE])
    r1 = nst.run(cat, rec, specs, "ben", MemoryTransport())
    write_mini(tmp_path / "m", [], [CRM_DATE | {"owner": "clara"}])
    v2 = nst.load_specs(cat, specs, "clara")
    assert v2["version"] == 2 and v2["author"] == "clara" and v2["created_at"]
    assert any("owner" in d and "clara" in d for d in json.loads(v2["diff"]))
    assert nst.load_specs(cat, specs, "clara")["version"] == 2  # unchanged content: no new version
    rows = cat.query("SELECT spec_version FROM normalized_value WHERE run_id = ?", (r1["run_id"],))
    assert rows and all(r["spec_version"] == 1 for r in rows)
    write_mini(tmp_path / "m", [{"source": "crm", "object": "kontakt", "record_id": "K-1",
                                 "values": {"geburtstag": "03.04.2025"}}], [CRM_DATE | {"owner": "clara"}])
    r2 = nst.run(cat, rec, specs, "clara", MemoryTransport())
    rows = cat.query("SELECT spec_version FROM normalized_value WHERE run_id = ?", (r2["run_id"],))
    assert rows and all(r["spec_version"] == 2 for r in rows)


def test_spec_validation_names_the_problem():
    errs = nst.spec_errors({"fields": [{"source": "crm", "field": "kontakt.d", "entity": "c", "attribute": "d",
                                        "type": "date", "owner": "ben"}]})
    assert errs == ["fields[0] (crm.kontakt.d): parser.pattern is required for type date"]


def test_raw_value_survives_normalization(cat, tmp_path):
    rec, specs = write_mini(tmp_path / "m", [
        {"source": "crm", "object": "kontakt", "record_id": "K-1", "values": {"geburtstag": "03.04.2025"}}], [CRM_DATE])
    run = nst.run(cat, rec, specs, "ben", MemoryTransport())
    row = cat.one("SELECT * FROM normalized_value")
    assert (row["raw"], row["canonical"], row["spec_version"], row["run_id"]) == ("03.04.2025", "2025-04-03", 1,
                                                                                 run["run_id"])


def test_rerunning_a_run_is_idempotent(cat, tmp_path):
    rec, specs = write_mini(tmp_path / "m", [
        {"source": "crm", "object": "kontakt", "record_id": f"K-{i}", "values": {"geburtstag": f"0{i}.04.2025"}}
        for i in range(1, 5)], [CRM_DATE])
    first = nst.run(cat, rec, specs, "ben", MemoryTransport())
    before = cat.query("SELECT record_id, canonical FROM normalized_value ORDER BY record_id")
    second = nst.run(cat, rec, specs, "ben", MemoryTransport())
    assert second["skipped"] and second["run_id"] == first["run_id"]
    assert cat.query("SELECT record_id, canonical FROM normalized_value ORDER BY record_id") == before
    assert len(before) == 4


def test_discarded_records_are_not_normalized(cat, tmp_path):
    from konmari.catalog import now

    ts = now()
    cat.execute("INSERT INTO corpus_item (item_id, asset_uri, charter_id, title, content_ref, text_ref, meta, state,"
                " state_changed_at, restricted_until, masked_ref, first_seen, last_seen) VALUES ('crm:K-2', 'manual://crm', 'x', 't', 'r', NULL, '{}', 'archived', ?,"
                " NULL, NULL, ?, ?)", (ts, ts, ts))
    rec, specs = write_mini(tmp_path / "m", [
        {"source": "crm", "object": "kontakt", "record_id": f"K-{i}", "values": {"geburtstag": "01.04.2025"}}
        for i in (1, 2)], [CRM_DATE])
    nst.run(cat, rec, specs, "ben", MemoryTransport())
    assert [r["record_id"] for r in cat.query("SELECT record_id FROM normalized_value")] == ["K-1"]


# ---------------------------------------------------------------- 2.3 dates and times

def test_local_time_with_declared_zone():
    r = normalize("datetime", "2025-03-30 14:00", {"pattern": "YYYY-MM-DD HH:mm", "zone": "Europe/Berlin"})
    assert (r.canonical, r.zone) == ("2025-03-30T12:00:00Z", "Europe/Berlin")


def test_ambiguous_local_time_is_quarantined(cat, tmp_path):
    spec = CRM_DATE | {"field": "kontakt.termin", "type": "datetime",
                       "parser": {"pattern": "YYYY-MM-DD HH:mm", "zone": "Europe/Berlin"}}
    rec, specs = write_mini(tmp_path / "m", [
        {"source": "crm", "object": "kontakt", "record_id": "K-1", "values": {"termin": "2025-10-26 02:30"}}], [spec])
    nst.run(cat, rec, specs, "ben", MemoryTransport())
    assert cat.one("SELECT reason FROM quarantine")["reason"] == "ambiguous_local_time"
    assert cat.one("SELECT COUNT(*) AS n FROM normalized_value")["n"] == 0
    assert q("datetime", "2025-03-30 02:30", pattern="YYYY-MM-DD HH:mm", zone="Europe/Berlin") == \
        "nonexistent_local_time"


def test_missing_offset_without_declared_zone():
    assert q("datetime", "2025-03-30 14:00", pattern="YYYY-MM-DD HH:mm") == "missing_timezone"
    r = normalize("datetime", "2025-03-30T14:00:00+02:00", {"pattern": "ISO"})
    assert (r.canonical, r.zone) == ("2025-03-30T12:00:00Z", "+02:00")


def test_dates_are_not_guessed_and_epochs_declare_their_unit():
    assert normalize("date", "03.04.2025", {"pattern": "DD.MM.YYYY"}).canonical == "2025-04-03"
    assert normalize("date", "03.04.2025", {"pattern": "MM.DD.YYYY"}).canonical == "2025-03-04"
    assert q("date", "2025-04-03", pattern="DD.MM.YYYY") == "unparseable"
    assert normalize("datetime", "1700000000", {"epoch_unit": "s"}).canonical == \
        normalize("datetime", "1700000000000", {"epoch_unit": "ms"}).canonical == "2023-11-14T22:13:20Z"


def test_event_time_and_processing_time_are_separate(cat, tmp_path):
    spec = CRM_DATE | {"field": "kontakt.termin", "type": "datetime", "parser": {"pattern": "ISO"}}
    rec, specs = write_mini(tmp_path / "m", [{"source": "crm", "object": "kontakt", "record_id": "K-1",
                                              "values": {"termin": "2020-01-01T10:00:00+01:00"}}], [spec])
    nst.run(cat, rec, specs, "ben", MemoryTransport())
    row = cat.one("SELECT canonical, processed_at FROM normalized_value")
    assert row["canonical"] == "2020-01-01T09:00:00Z" and row["processed_at"].year >= 2026


# ---------------------------------------------------------------- 2.4 identifiers and phones

def test_vat_id_compacted():
    vat = "DE12345678" + mod_11_10.calc_check_digit("12345678")
    r = normalize("identifier", f"DE 123 456 78{vat[-1]}", {"kind": "de.vat"})
    assert (r.canonical, r.flags) == (vat, ["syntax_valid"])


def test_invalid_iban_quarantined(cat, tmp_path):
    spec = CRM_DATE | {"field": "kontakt.iban", "type": "identifier", "parser": {"kind": "iban"}}
    rec, specs = write_mini(tmp_path / "m", [{"source": "crm", "object": "kontakt", "record_id": "K-1",
                                              "values": {"iban": "DE89370400440532013001"}}], [spec])
    nst.run(cat, rec, specs, "ben", MemoryTransport())
    row = cat.one("SELECT * FROM quarantine")
    assert (row["reason"], row["raw"]) == ("invalid_checksum", "DE89370400440532013001")
    assert normalize("identifier", "DE89 3704 0044 0532 0130 00", {"kind": "iban"}).canonical == \
        "DE89370400440532013000"


def test_phone_number_to_e164():
    r = normalize("phone", "030 1234567", {"region": "DE"})
    assert (r.canonical, r.flags) == ("+49301234567", ["syntax_valid"])
    assert q("phone", "12", region="DE") in ("invalid_phone", "invalid_format")


# ---------------------------------------------------------------- 2.5 names

def test_equivalent_names_share_a_match_key():
    p = {"transliterate_de": True, "strip_legal_forms": True}
    a, b = normalize("name", "Müller GmbH", p), normalize("name", "MUELLER  GmbH", p)
    assert a.match_key == b.match_key == "mueller"
    assert (a.canonical, b.canonical) == ("Müller GmbH", "MUELLER GmbH")


def test_transliteration_off_by_default():
    assert normalize("name", "Müller", {}).match_key != normalize("name", "Mueller", {}).match_key


# ---------------------------------------------------------------- 2.6 units

def test_tonnes_to_kilograms():
    r = normalize("quantity", "1,5 t", {"target_unit": "kg", "decimal_comma": True})
    assert (r.canonical, r.unit) == ("1500", "kg")
    assert normalize("quantity", "2 [lb_av]", {"target_unit": "kg", "vocabulary": "ucum"}).canonical == "0.90718474"


def test_incompatible_unit():
    assert q("quantity", "3 m", target_unit="kg") == "incompatible_dimension"
    assert q("quantity", "250 g", target_unit="kg", allowed_units=["kg", "t"]) == "unit_not_allowed"
    assert q("quantity", "3 parsec-ish", target_unit="kg") == "unknown_unit"


# ---------------------------------------------------------------- 2.7 quarantine

def _quarantined_date(cat, tmp_path):
    rec, specs = write_mini(tmp_path / "m", [{"source": "crm", "object": "kontakt", "record_id": "K-1",
                                              "values": {"geburtstag": "2025/04/03"}}], [CRM_DATE])
    nst.run(cat, rec, specs, "ben", MemoryTransport())
    return cat.one("SELECT qid FROM quarantine")["qid"]


def test_owner_resolves_a_quarantined_value(engagement, cli, tmp_path):
    cli("charter", "approve", "--as", "anna")
    qid = _quarantined_date(connect(), tmp_path)
    code, out, err = cli("normalize", "quarantine", "resolve", qid, "--value", "2025-04-03", "--as", "clara")
    assert code == 1 and "only the field owner 'ben'" in err
    code, out, err = cli("normalize", "quarantine", "resolve", qid, "--value", "2025-04-03", "--as", "ben")
    assert code == 0, err
    cat = connect()
    v = cat.one("SELECT * FROM normalized_value")
    assert (v["canonical"], v["origin"]) == ("2025-04-03", "manual")
    e = cat.one("SELECT * FROM quarantine")
    assert (e["status"], e["resolved_by"]) == ("resolved", "ben") and e["resolved_at"]


def test_reverting_a_manual_resolution(cat, tmp_path):
    sc = example_scope()
    qid = _quarantined_date(cat, tmp_path)
    nst.resolve_quarantine(cat, sc, qid, "ben", value="2025-04-03", reason="checked the paper form")
    nst.revert_quarantine(cat, sc, qid, "ben", reason="wrong form")
    assert cat.one("SELECT status FROM quarantine")["status"] == "open"
    assert cat.one("SELECT withdrawn_at FROM normalized_value WHERE origin = 'manual'")["withdrawn_at"]
    actions = [r["action"] for r in cat.query("SELECT action FROM audit_event ORDER BY seq")]
    assert actions[-2:] == ["normalize.quarantine_resolve", "normalize.quarantine_revert"]
    with pytest.raises(Refused):
        nst.revert_quarantine(cat, sc, qid, "ben")


def test_quarantined_values_are_not_passed_on(sot):
    keys = {(r["source_field"], r["record_id"]) for r in sot["cat"].query("SELECT * FROM quarantine")}
    vals = nst.current_values(sot["cat"], "customer")
    assert keys
    for sf, rid in keys:
        attrs = vals.get(f"{sf.split('.')[0]}:{rid}", {})
        assert all(r["source_field"] != sf for r in attrs.values())


# ---------------------------------------------------------------- 2.8 / 2.9 conformance and contract

def test_silent_drop_fails_conformance(cat, tmp_path):
    rec, specs = write_mini(tmp_path / "m", [
        {"source": "crm", "object": "kontakt", "record_id": f"K-{i}",
         "values": {"geburtstag": "2025/04/03" if i < 5 else "03.04.2025"}} for i in range(1000)], [CRM_DATE])
    run = nst.run(cat, rec, specs, "ben", MemoryTransport())
    ids = [r["value_id"] for r in cat.query("SELECT value_id FROM normalized_value ORDER BY record_id LIMIT 5")]
    for i in ids:  # simulate a buggy normalizer that drops values silently: 990 canonical + 5 quarantined
        cat.execute("DELETE FROM normalized_value WHERE value_id = ?", (i,))
    c = nst.conformance(cat, run["run_id"])["crm.kontakt.geburtstag"]
    assert (c["read"], c["canonical"], c["quarantined"], c["unaccounted"], c["conformant"]) == (1000, 990, 5, 5, False)


def test_metrics_reach_the_scorecard(sot):
    from konmari.standardize import latest_metrics

    m = {(r["field"], r["name"]): r["value"] for r in latest_metrics(sot["cat"], "conformance")}
    assert m[("crm.kontakt.geaendert", "reason:ambiguous_local_time")] == 8
    assert 0 < m[("crm.kontakt.geaendert", "canonical_rate")] < 1 and m[("crm.kontakt.geaendert", "unaccounted")] == 0
    assert m[("erp.debitor.zahlungsbedingung", "canonical_rate")] == 1


def test_odcs_contract_rules_pass_on_the_fixture(sot, cli, tmp_path):
    out = tmp_path / "contract.yaml"
    code, _, err = cli("normalize", "contract", "export", "-o", out)
    assert code == 0, err
    contract = load_yaml(out)
    assert contract["kind"] == "DataContract" and contract["apiVersion"].startswith("v3")
    props = {p["name"]: p for o in contract["schema"] for p in o["properties"]}
    assert props["zahlungsbedingung"]["quality"][0]["mustBe"] == 0
    code, out_, err = cli("normalize", "contract", "check", out)
    assert code == 0, err
    sot["cat"].execute("UPDATE normalized_value SET canonical = '26.10.2025' WHERE source_field = 'crm.kontakt.geaendert'"
                       " AND record_id = 'K-0001'")
    code, _, err = cli("normalize", "contract", "check", out)
    assert code == 1 and "crm.kontakt.geaendert: 1 canonical value(s) do not match" in err
