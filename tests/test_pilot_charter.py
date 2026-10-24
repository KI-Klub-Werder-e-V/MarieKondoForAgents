"""Scenarios of specs/pilot-charter (except the readiness gate, see test_gate.py)."""

import copy
import re
from datetime import date

import pytest

from konmari import charter
from konmari.canonical import canonical_hash, load_yaml
from konmari.charter import store
from konmari.charter.validation import validate_charter

from conftest import edit_yaml


def errs(doc):
    return "\n".join(validate_charter(doc))


# --- Single pilot use case per charter

def test_valid_single_use_case(engagement, cli):
    code, out, _ = cli("charter", "validate")
    assert code == 0 and "is valid" in out


def test_second_use_case_rejected(engagement, cli):
    edit_yaml("charter.yaml", lambda d: d["use_cases"].append(copy.deepcopy(d["use_cases"][0])))
    code, _, err = cli("charter", "validate")
    assert code != 0 and "a second use case needs its own charter" in err


def test_missing_out_of_scope_list(charter_doc):
    charter_doc["use_cases"][0]["out_of_scope"] = []
    assert "use_cases[0].out_of_scope: must not be empty" in errs(charter_doc)


@pytest.mark.parametrize("field", ["name", "purpose", "deployment_setting", "in_scope", "misuse_cases"])
def test_missing_use_case_field_named(charter_doc, field):
    del charter_doc["use_cases"][0][field]
    assert f"use_cases[0].{field}: required field is missing" in errs(charter_doc)


@pytest.mark.parametrize("template", ["full", "minimal"])
@pytest.mark.parametrize("lang", ["en", "de"])
def test_init_templates_pass_once_placeholders_filled(tmp_path, cli, template, lang):
    out = tmp_path / "charter.yaml"
    assert cli("charter", "init", "--template", template, "--lang", lang, "-o", out)[0] == 0
    raw = out.read_text()
    assert validate_charter(load_yaml(raw))  # placeholders are reported
    filled = re.sub(r"<FILL[^>]*>", "Synthetic value used only for this test", raw)
    assert validate_charter(load_yaml(filled)) == []


# --- Stakeholder register with RACI

def test_two_accountables_rejected(charter_doc):
    charter_doc["raci"]["retention_discard_approval"]["A"] = ["ben", "clara"]
    e = errs(charter_doc)
    assert "raci.retention_discard_approval.A: exactly one Accountable required, found 2" in e
    assert "Ben Muster (ben)" in e and "Clara Probe (clara)" in e


def test_facilitator_as_accountable_rejected(charter_doc):
    charter_doc["raci"]["charter_approval"]["A"] = ["gina"]
    assert "the organization's own staff must be Accountable" in errs(charter_doc)


def test_required_roles_present(charter_doc):
    for s in charter_doc["stakeholders"]:
        s["roles"] = [r for r in s["roles"] if r not in ("data_owner", "compliance", "agent_user")] or ["data_custodian"]
    e = errs(charter_doc)
    assert "missing required role(s): data_owner, compliance, agent_user" in e


def test_responsible_required_per_activity(charter_doc):
    charter_doc["raci"]["go_live_approval"]["R"] = []
    assert "raci.go_live_approval.R: at least one Responsible required" in errs(charter_doc)


def test_dpia_decision_accountable_must_hold_compliance(charter_doc):
    charter_doc["raci"]["dpia_decision"]["A"] = ["ben"]
    assert "raci.dpia_decision.A: Ben Muster (ben) must hold the compliance role" in errs(charter_doc)


def test_unknown_role_rejected(charter_doc):
    charter_doc["stakeholders"][0]["roles"] = ["boss"]
    assert "stakeholders[0].roles[0]" in errs(charter_doc)


# --- DSGVO and EU AI Act screening fields

def test_pending_dpia_blocks_approval(engagement, cli):
    edit_yaml("charter.yaml", lambda d: d["compliance"]["dpia_screening"].update(result="pending"))
    code, _, err = cli("charter", "approve", "--as", "anna")
    assert code != 0 and "DPIA screening is 'pending'" in err


def test_pending_ai_act_blocks_approval(engagement, cli):
    edit_yaml("charter.yaml", lambda d: d["compliance"]["ai_act"].update(classification="pending"))
    code, _, err = cli("charter", "approve", "--as", "anna")
    assert code != 0 and "AI Act classification is 'pending'" in err


def test_prohibited_classification_never_approved(engagement, cli):
    edit_yaml("charter.yaml", lambda d: d["compliance"]["ai_act"].update(classification="prohibited"))
    code, _, err = cli("charter", "approve", "--as", "anna")
    assert code != 0 and "cannot be approved" in err


def test_high_risk_classification_needs_explicit_compliance_sign_off(engagement, cli, cat):
    edit_yaml("charter.yaml", lambda d: d["compliance"]["ai_act"].update(classification="high"))
    code, _, err = cli("charter", "approve", "--as", "anna")
    assert code != 0 and "--compliance-signoff" in err
    assert cli("charter", "approve", "--as", "ben", "--compliance-signoff")[0] != 0  # not compliance
    assert cli("charter", "approve", "--as", "eva", "--compliance-signoff")[0] == 0
    signoff = cat.one("SELECT * FROM approval WHERE subject = 'ai_act_high'")
    assert signoff["approver_id"] == "eva" and "classification high" in signoff["note"]
    assert cli("charter", "approve", "--as", "anna")[0] == 0


def test_personal_data_declared(charter_doc):
    del charter_doc["compliance"]["personal_data_categories"]
    assert "compliance.personal_data_categories: required field is missing" in errs(charter_doc)
    charter_doc["compliance"]["personal_data_categories"] = "none"
    assert errs(charter_doc) == ""
    charter_doc["compliance"]["personal_data_categories"] = ["contact data"]
    assert errs(charter_doc) == ""


def test_short_compliance_reason_rejected(charter_doc):
    charter_doc["compliance"]["dpia_screening"]["reason"] = "n/a"
    assert "compliance.dpia_screening.reason" in errs(charter_doc)


# --- Engagement cadence and decision log

def test_missing_post_pilot_review_rejected(charter_doc):
    del charter_doc["cadence"]["post_pilot_review"]
    assert "the engagement must not be a one-off" in errs(charter_doc)


def test_steering_must_include_sponsor_and_compliance(charter_doc):
    charter_doc["cadence"]["steering"]["attendees"] = ["ben"]
    e = errs(charter_doc)
    assert "must include a sponsor" in e and "must include a compliance" in e


def test_overdue_steering_meeting_reported(engagement, cat, charter_doc):
    store.register(cat, charter_doc, "test")
    store.log_decision(cat, charter_doc["charter_id"], "gina", "steering", "2026-03-01", ["anna", "eva"],
                       ["kick off inventory"], [])
    st = store.status(cat, charter_doc["charter_id"], today=date(2026, 3, 23))  # 22 days, interval 14
    assert st["cadence_breach"] and "Anna Beispiel (anna)" in st["cadence_breach"]
    assert store.status(cat, charter_doc["charter_id"], today=date(2026, 3, 22))["cadence_breach"] is None


def test_overdue_steering_meeting_reported_by_cli(engagement, cli):
    from datetime import timedelta

    cli("charter", "validate")
    old = (date.today() - timedelta(days=22)).isoformat()
    assert cli("charter", "log", "--meeting", "steering", "--date", old, "--attendees", "anna,eva",
               "--decision", "kick off inventory", "--as", "gina")[0] == 0
    code, out, _ = cli("charter", "status")
    assert code == 0 and "cadence breach" in out and "Anna Beispiel (anna)" in out


def test_decision_logged(engagement, cli, cat):
    cli("charter", "validate")
    code, _, _ = cli("charter", "log", "--meeting", "steering", "--date", "2026-02-10", "--attendees", "anna,eva",
                     "--decision", "start the inventory", "--action", "dirk: prepare read-only access", "--as", "gina")
    assert code == 0
    row = cat.one("SELECT * FROM decision_log")
    assert row["author"] == "gina" and row["charter_version"] == 1 and row["logged_at"]
    code, out, _ = cli("charter", "show", "--log")
    assert "start the inventory" in out and "prepare read-only access (owner: dirk)" in out


# --- Human approval with recorded identity

def test_approval_recorded(engagement, cli, cat, charter_doc):
    code, _, _ = cli("charter", "approve", "--as", "anna")
    assert code == 0
    ap = cat.one("SELECT * FROM approval WHERE subject = 'charter'")
    assert (ap["approver_id"], ap["role"], ap["version"]) == ("anna", "sponsor", 1)
    assert ap["approved_at"] and ap["os_user"] and ap["content_hash"] == canonical_hash(charter_doc)
    assert cat.one("SELECT status FROM charter_version WHERE version = 1")["status"] == "approved"


def test_wrong_approver_refused(engagement, cli, cat):
    code, _, err = cli("charter", "approve", "--as", "ben")
    assert code != 0 and "not Accountable for charter approval" in err
    assert cat.one("SELECT status FROM charter_version")["status"] == "draft"
    assert cat.one("SELECT COUNT(*) AS n FROM approval")["n"] == 0


def test_tampering_detected(engagement, cli):
    cli("charter", "approve", "--as", "anna")
    edit_yaml("charter.yaml", lambda d: d["use_cases"][0]["in_scope"].append("sneaky new task"))
    code, _, err = cli("charter", "validate")
    assert code != 0 and "content hash no longer matches approved version 1" in err


# --- Versioned, reversible amendments

def test_amendment_creates_a_new_version(engagement, cli, cat):
    cli("charter", "approve", "--as", "anna")
    edit_yaml("charter.yaml", lambda d: d["data_scope"]["categories"][1].update(approver="clara"))
    code, _, _ = cli("charter", "amend", "--reason", "add second data owner", "--as", "gina")
    assert code == 0
    rows = cat.query("SELECT version, status, reason FROM charter_version ORDER BY version")
    assert [(r["version"], r["status"]) for r in rows] == [(1, "approved"), (2, "draft")]
    code, out, _ = cli("charter", "show", "--version", "1")
    assert code == 0 and "(approved)" in out and "approval: charter by anna" in out
    ev = cat.one("SELECT * FROM audit_event WHERE action = 'charter.amend'")
    assert ev["actor"] == "gina" and "add second data owner" in ev["details"] and ev["ts"]


def test_amend_needs_reason(engagement, cli):
    cli("charter", "approve", "--as", "anna")
    assert cli("charter", "amend", "--reason", " ", "--as", "gina")[0] != 0


def test_diff_between_versions(engagement, cli):
    cli("charter", "approve", "--as", "anna")
    edit_yaml("charter.yaml", lambda d: (d["data_scope"]["categories"][1].update(approver="clara"),
                                         d["cadence"].update(working_session_interval_days=14)))
    cli("charter", "amend", "--reason", "add second data owner", "--as", "gina")
    code, out, _ = cli("charter", "diff", 1, 2)
    assert code == 0
    assert 'data_scope.categories[1].approver: "ben" -> "clara"' in out
    assert "cadence.working_session_interval_days: 7 -> 14" in out


def test_revert_by_amend_round_trip(engagement, cli, cat, charter_doc):
    cli("charter", "approve", "--as", "anna")
    edit_yaml("charter.yaml", lambda d: d["use_cases"][0]["in_scope"].append("event calendar questions"))
    cli("charter", "amend", "--reason", "add event calendar", "--as", "gina")
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    code, _, _ = cli("charter", "amend", "--reason", "revert: calendar not ready", "--as", "gina", "--from-version", 1)
    assert code == 0
    rows = cat.query("SELECT version, status, content_hash FROM charter_version ORDER BY version")
    assert len(rows) == 3 and rows[2]["content_hash"] == rows[0]["content_hash"] == canonical_hash(charter_doc)
    assert canonical_hash(load_yaml(engagement / "charter.yaml")) == rows[0]["content_hash"]
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    assert len(store.approvals(cat, "verein-faq-assistant")) == 3  # all earlier approvals kept


# --- Pilot data scope and category approvers

def test_category_without_approver_rejected(charter_doc):
    charter_doc["data_scope"]["categories"].append({"id": "customer-records", "reviewers": ["finn"]})
    assert "data_scope.categories.customer-records: needs one approver holding the data_owner role" in errs(charter_doc)


def test_category_approver_must_be_data_owner(charter_doc):
    charter_doc["data_scope"]["categories"][0]["approver"] = "finn"
    assert "'finn' is not a data_owner" in errs(charter_doc)


def test_unknown_category_id_rejected(charter_doc):
    charter_doc["data_scope"]["categories"].append({"id": "payroll", "approver": "ben", "reviewers": ["clara"]})
    e = errs(charter_doc)
    assert "unknown category 'payroll'" in e and "kb-and-sops, customer-records" in e
    charter_doc["data_scope"]["category_extensions"] = [{"id": "payroll", "description": "pilot extension"}]
    assert errs(charter_doc) == ""


def test_downstream_reads_approvers_from_the_charter(engagement, cli, cat):
    cli("charter", "approve", "--as", "anna")
    edit_yaml("charter.yaml", lambda d: d["data_scope"]["categories"][1].update(approver="clara"))
    cli("charter", "amend", "--reason", "clara takes over contracts", "--as", "gina")  # draft, not yet effective
    sc = charter.scope("verein-faq-assistant", cat)
    assert sc.charter_version == 1 and sc.approver("policies-and-contracts") == "ben"
    assert sc.reviewers("kb-and-sops") == ["clara", "finn"]
    assert [s["id"] for s in sc.sources] == ["office-share", "fee-rules"]
    with pytest.raises(KeyError):
        sc.approver("customer-records")
    cli("charter", "approve", "--as", "anna")
    assert charter.scope("verein-faq-assistant", cat).approver("policies-and-contracts") == "clara"


def test_scope_requires_approved_charter(engagement, cli, cat):
    cli("charter", "validate")
    with pytest.raises(LookupError):
        charter.scope("verein-faq-assistant", cat)

