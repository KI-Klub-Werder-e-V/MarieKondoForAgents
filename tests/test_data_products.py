"""data-products (c08): descriptor lint, upstream checks, contract tests, semver gate, approval, publish, rollback,
deprecation window, change-tracked refresh, lineage, catalog. Synthetic data only."""

import copy
import io
import json
from datetime import timedelta

import pytest

import c08_fixture as fx
from konmari.catalog import connect, now
from konmari.connect import descriptor as dsc
from konmari.connect import lineage
from konmari.connect import products as prd
from konmari.connect import refresh as rf
from konmari.versions import Refused


def _tables(cat):
    return {r["table_name"] for r in cat.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')")}


C08 = {"data_product", "data_product_version", "product_approval", "product_test", "product_row", "refresh_run",
       "lineage_event", "tool_pin"}


def bump(desc: dict, version: str, edit=None) -> dict:
    d = copy.deepcopy(desc)
    d["product"]["version"] = d["contract"]["version"] = d["product"]["outputPorts"][0]["version"] = version
    if edit:
        edit(d)
    return d


def props(d: dict) -> list[dict]:
    return d["contract"]["schema"][0]["properties"]


def release(c, desc, owner="ben", at=None):
    """register, test, approve, publish one more version of an existing product."""
    cat, sc = c["cat"], c["sc"]
    i = dsc.info(desc)
    assert dsc.lint(desc, cat, sc, fx.charter_doc()) == []
    prd.register(cat, sc, desc, owner)
    rf.refresh(cat, i["id"], owner)
    prd.run_tests(cat, i["id"], i["version"])
    prd.approve(cat, sc, i["id"], i["version"], owner)
    return prd.publish(cat, sc, i["id"], i["version"], owner, at=at)


# ---------------------------------------------------------------- 1. catalog schema

def _up_down_up(cat):
    assert C08 <= _tables(cat)
    cat.rollback("008_c08_connect_agent_interfaces.sql")
    assert not C08 & _tables(cat) and "agent_index" in _tables(cat)
    cat.migrate()
    assert C08 <= _tables(cat)


def test_migration_up_down_up_duckdb(tmp_path):
    _up_down_up(connect(f"duckdb:///{tmp_path}/c.duckdb"))


@pytest.mark.postgres
def test_migration_up_down_up_postgres(pg_url):
    _up_down_up(connect(pg_url))


def _state_machine(cat, sc):
    desc = fx.faq_desc("sqlite:///unused.db")
    prd.register(cat, sc, desc, "ben")
    with pytest.raises(Refused, match="draft -> published is not allowed"):
        prd.transition(cat, "support-faq", "1.0.0", "published", "ben")
    for to in ("approved", "draft", "approved", "published", "withdrawn"):
        prd.transition(cat, "support-faq", "1.0.0", to, "ben", sc)
    for to in TRANSITIONS_FROM_WITHDRAWN:
        with pytest.raises(Refused):
            prd.transition(cat, "support-faq", "1.0.0", to, "ben")
    ev = fx.events(cat, "product.transition")
    assert [(e["details"]["previous"], e["details"]["new"]) for e in ev] == [
        ("draft", "approved"), ("approved", "draft"), ("draft", "approved"), ("approved", "published"),
        ("published", "withdrawn")]
    assert all(e["step"] == "connect" and e["charter_id"] == sc.charter_id for e in ev)


TRANSITIONS_FROM_WITHDRAWN = ("draft", "approved", "published", "superseded", "deprecated", "sunset")


def test_version_state_machine(gcat):
    _state_machine(gcat, fx.scope())


# ---------------------------------------------------------------- 2. descriptor

def test_init_writes_a_skeleton_that_lint_rejects(tmp_path):
    desc = dsc.skeleton("contracts-index", "Contracts", "policies-and-contracts", "clara", "Member FAQ assistant")
    dsc.write(desc, tmp_path / "p")
    loaded = dsc.load(tmp_path / "p")
    assert loaded == desc and dsc.custom(loaded["product"])["scopes"] == ["product:contracts-index:read"]
    errs = dsc.lint(loaded)
    assert "product.description.purpose: missing" in errs
    assert any("description: missing business definition for field 'id'" in e for e in errs)


def test_complete_descriptor_is_accepted(c08, engagement, cli):
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    dsc.write(fx.faq_desc(c08["loc"]), engagement / "products" / "support-faq")
    code, out, err = cli("product", "lint", "products/support-faq", "--as", "ben")
    assert code == 0, err
    assert "support-faq 1.0.0: descriptor valid (draft" in out
    assert prd.get(c08["cat"], "support-faq", "1.0.0")["state"] == "draft"


@pytest.mark.parametrize("edit, path", [
    (lambda d: d["product"].pop("team"), "product.team.members[role=owner]: missing"),
    (lambda d: d["product"]["description"].pop("purpose"), "product.description.purpose: missing"),
    (lambda d: d["contract"].pop("slaProperties"), "contract.slaProperties[latency]: missing freshness SLA"),
    (lambda d: props(d)[2].pop("description"),
     "contract.schema[0].properties[2].description: missing business definition for field 'answer'"),
])
def test_missing_mandatory_element_is_rejected(edit, path):
    d = fx.faq_desc("sqlite:///x.db")
    edit(d)
    assert any(e.startswith(path) for e in dsc.lint(d)), dsc.lint(d)


def test_missing_elements_are_all_listed_by_path_with_nonzero_exit(c08, engagement, cli):
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    d = fx.faq_desc(c08["loc"])
    d["product"].pop("team")
    d["contract"].pop("slaProperties")
    dsc.write(d, engagement / "p")
    code, _, err = cli("product", "lint", "p")
    assert code == 1
    assert "product.team.members[role=owner]: missing" in err and "contract.slaProperties[latency]" in err


def test_schema_violation_is_reported_by_path():
    d = fx.faq_desc("sqlite:///x.db")
    d["contract"]["apiVersion"] = "v9"
    assert any(e.startswith("contract.apiVersion:") for e in dsc.lint(d))


def _lint(c, d):
    return dsc.lint(d, c["cat"], c["sc"], fx.charter_doc())


def test_purpose_not_in_pilot_charter(c08):
    d = fx.faq_desc(c08["loc"])
    d["product"]["customProperties"][0]["value"] = "Contract negotiation bot"
    assert any("out of pilot scope" in e for e in _lint(c08, d))


def test_source_failed_readiness(c08):
    fx.keep(c08["cat"], fx.FAQ_URI, fx.KB, to="fix")
    errs = _lint(c08, fx.faq_desc(c08["loc"]))
    assert any("'office-faq' (manual://kb/faq) has readiness scorecard result 'fix'" in e for e in errs)


def test_unjustified_personal_data_field(c08):
    d = fx.faq_desc(c08["loc"])
    props(d)[4]["customProperties"] = []  # no piiEntity -> no masking rule
    errs = _lint(c08, d)
    assert any("field 'author_email'" in e and "data minimisation" in e for e in errs)
    d["product"]["customProperties"][5]["value"] = {"author_email": "members need to contact the author"}
    assert _lint(c08, d) == []


def test_source_of_truth_and_policy_checks(c08):
    cat = c08["cat"]
    d = fx.faq_desc(c08["loc"])
    d["product"]["customProperties"][4]["value"][0]["sourceOfTruth"] = "secondary"
    assert any("ranks it primary" in e for e in _lint(c08, d))
    d = fx.faq_desc(c08["loc"])
    d["product"]["customProperties"][4]["value"][0]["entity"] = "faq"
    cat.execute("INSERT INTO conflict (conflict_id, entity, cluster_id, field, candidates, detected_at, owner, due_date,"
                " status, personal_data) VALUES ('c1', 'faq', 'k', 'answer', '[]', ?, 'ben', '2026-12-01', 'open',"
                " false)", (now(),))
    assert any("1 unresolved source-of-truth conflict(s) for entity 'faq'" in e for e in _lint(c08, d))
    d = fx.faq_desc(c08["loc"])
    d["product"]["customProperties"][3]["value"] = "customer-records"
    assert any("no approved access and masking policy 'customer-records'" in e for e in _lint(c08, d))


def test_glossary_term_must_be_approved(c08):
    d = fx.faq_desc(c08["loc"])
    props(d)[1]["customProperties"] = [{"property": "glossaryTerm", "value": "Mitgliedsbeitrag"}]
    assert any("glossary term 'Mitgliedsbeitrag' is not approved" in e for e in _lint(c08, d))


# ---------------------------------------------------------------- contract tests

def test_quality_rule_fails(c08, engagement, cli):
    cat, sc = c08["cat"], c08["sc"]
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    d = fx.faq_desc(c08["loc"])
    props(d)[1]["unique"] = True
    prd.register(cat, sc, d, "ben")
    assert rf.refresh(cat, "support-faq")["status"] == "ok"
    for k in ("faq-001", "faq-002"):  # not-null and uniqueness violated in the materialised data
        cat.execute("UPDATE product_row SET data = ? WHERE row_key = ?",
                    (json.dumps({"id": k, "question": None if k == "faq-001" else "Frage 3 zum Mitgliedsbeitrag?",
                                 "answer": "a", "topic": "fees", "author_email": None}), k))
    code, out, err = cli("product", "test", "support-faq")
    assert code == 1
    assert "FAILED required on question: 1 violation(s)" in err and "FAILED unique on question: 1 violation(s)" in err
    t = cat.one("SELECT * FROM product_test ORDER BY run_at DESC LIMIT 1")
    assert not t["passed"] and {f["rule"] for f in json.loads(t["failures"])} == {"required", "unique"}


def test_odcs_library_metrics():
    d = fx.faq_desc("sqlite:///x.db")
    props(d)[3]["quality"] = [{"metric": "invalidValues", "mustBe": 0,
                               "arguments": {"validValues": ["fees", "training"]}}]
    d["contract"]["schema"][0]["quality"] = [{"metric": "rowCount", "mustBeGreaterThan": 2}]
    rows = [{"id": "1", "question": "q", "topic": "fees"}, {"id": "2", "question": "q", "topic": "golf"}]
    fails = prd.evaluate(d, rows)
    assert {"rule": "invalidValues", "field": "topic", "violations": 1} in fails
    assert {"rule": "rowCount", "field": None, "violations": 2} in fails


# ---------------------------------------------------------------- 3. versioning, approval, publish

@pytest.fixture
def v120(c08):
    fx.ship(c08["cat"], c08["sc"], fx.faq_desc(c08["loc"], "1.2.0"))
    return c08


def test_breaking_change_with_minor_bump_is_blocked(v120, engagement, cli):
    cat, sc = v120["cat"], v120["sc"]
    d = bump(fx.faq_desc(v120["loc"]), "1.3.0", lambda d: props(d).pop(2))  # remove `answer`
    res = prd.diff(cat, "support-faq", d)
    assert res["breaking"] and res["required"] == "major" and not res["ok"]
    assert "requires version 2.0.0" in res["message"]
    prd.register(cat, sc, d, "ben")
    prd.transition(cat, "support-faq", "1.3.0", "approved", "ben")  # even with a (forced) approval
    with pytest.raises(Refused) as e:
        prd.publish(cat, sc, "support-faq", "1.3.0", "ben")
    assert any("requires version 2.0.0" in r for r in e.value.reasons)
    dsc.write(d, engagement / "p")
    code, out, _ = cli("product", "diff", "p")
    assert code == 1 and json.loads(out)["breaking"] is True


def test_compatible_addition(v120):
    cat, sc = v120["cat"], v120["sc"]
    d = bump(fx.faq_desc(v120["loc"]), "1.3.0", lambda d: props(d).append(
        {"name": "form_url", "logicalType": "string", "description": "Link to the form", "classification": "public"}))
    res = prd.diff(cat, "support-faq", d)
    assert not res["breaking"] and res["required"] == "minor" and res["ok"]
    prd.register(cat, sc, d, "ben")
    rf.refresh(cat, "support-faq")
    prd.run_tests(cat, "support-faq", "1.3.0")
    assert prd.approve(cat, sc, "support-faq", "1.3.0", "ben")["state"] == "approved"


def test_description_edit_requires_patch(v120):
    d = bump(fx.faq_desc(v120["loc"]), "1.2.0", lambda d: props(d)[2].update(description="The answer text"))
    res = prd.diff(v120["cat"], "support-faq", d)
    assert res["required"] == "patch" and not res["ok"] and "requires version 1.2.1" in res["message"]
    assert prd.diff(v120["cat"], "support-faq", bump(d, "1.2.1"))["ok"]


def test_publish_without_approval(v120, engagement, cli):
    cat, sc = v120["cat"], v120["sc"]
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    d = bump(fx.faq_desc(v120["loc"]), "1.2.1", lambda d: props(d)[2].update(description="The answer text"))
    prd.register(cat, sc, d, "ben")
    rf.refresh(cat, "support-faq")
    prd.run_tests(cat, "support-faq", "1.2.1")
    code, _, err = cli("product", "publish", "support-faq", "--version", "1.2.1", "--as", "ben")
    assert code == 1 and "no recorded owner approval" in err
    assert prd.current(cat, "support-faq")["version"] == "1.2.0"
    ev = fx.events(cat, "product.publish")[-1]
    assert ev["result"] == "refused" and ev["details"]["version"] == "1.2.1"


def test_only_the_owner_approves(v120):
    d = bump(fx.faq_desc(v120["loc"]), "1.2.1", lambda d: props(d)[2].update(description="x"))
    prd.register(v120["cat"], v120["sc"], d, "ben")
    with pytest.raises(Refused, match="not the owner"):
        prd.approve(v120["cat"], v120["sc"], "support-faq", "1.2.1", "clara")


def test_failed_tests_block_publish(v120):
    cat, sc = v120["cat"], v120["sc"]
    d = bump(fx.faq_desc(v120["loc"]), "1.3.0", lambda d: props(d).append(
        {"name": "form_url", "logicalType": "string", "description": "Link", "classification": "public",
         "required": True}))
    prd.register(cat, sc, d, "ben")
    rf.refresh(cat, "support-faq")
    assert not prd.run_tests(cat, "support-faq", "1.3.0")["passed"]  # source has no form_url
    prd.approve(cat, sc, "support-faq", "1.3.0", "ben")
    with pytest.raises(Refused) as e:
        prd.publish(cat, sc, "support-faq", "1.3.0", "ben")
    assert any("contract tests failed: required form_url" in r for r in e.value.reasons)


def _v130(c):
    return bump(fx.faq_desc(c["loc"]), "1.3.0", lambda d: props(d).append(
        {"name": "internal_note", "logicalType": "string", "description": "Office note", "classification":
         "internal"}))


def test_approved_publish(v120):
    cat = v120["cat"]
    release(v120, _v130(v120))
    assert prd.current(cat, "support-faq")["version"] == "1.3.0"
    assert prd.get(cat, "support-faq", "1.2.0")["state"] == "superseded"
    ev = [e for e in fx.events(cat, "product.transition") if e["details"]["new"] == "published"][-1]
    assert ev["details"]["approver"] == "ben" and ev["details"]["diff_summary"]["bump"] == "minor"
    appr = cat.one("SELECT * FROM product_approval WHERE version = '1.3.0'")
    assert appr["approver"] == "ben" and json.loads(appr["diff_summary"])["base"] == "1.2.0" and appr["test_id"]


def test_rollback(v120, engagement, cli):
    cat = v120["cat"]
    release(v120, _v130(v120))
    assert cli("charter", "approve", "--as", "anna")[0] == 0
    code, out, err = cli("product", "publish", "support-faq", "--rollback", "--reason", "wrong field", "--as", "ben")
    assert code == 0, err
    assert prd.current(cat, "support-faq")["version"] == "1.2.0"
    assert prd.get(cat, "support-faq", "1.3.0")["state"] == "withdrawn"
    ev = fx.events(cat, "product.rollback")[-1]
    assert ev["details"].items() >= {"previous": "1.3.0", "new": "1.2.0", "reason": "wrong field"}.items()
    assert cat.one("SELECT kind FROM product_approval WHERE kind = 'rollback'")


def test_deprecated_major_still_callable(v120):
    cat = v120["cat"]
    t = now()
    v2 = bump(fx.faq_desc(v120["loc"]), "2.0.0", lambda d: props(d).pop(2))
    release(v120, v2, at=t)
    later = {r["id"]: r for r in prd.listing(cat, at=t + timedelta(days=30))}["support-faq"]
    assert later["current_version"] == "2.0.0" and later["callable_versions"] == ["1.2.0", "2.0.0"]
    dep = later["deprecated_versions"][0]
    assert dep["version"] == "1.2.0" and dep["successor"] == "2.0.0"
    assert dep["sunset"].startswith((t + timedelta(days=90)).date().isoformat())
    after = {r["id"]: r for r in prd.listing(cat, at=t + timedelta(days=91))}["support-faq"]
    assert after["callable_versions"] == ["2.0.0"] and after["versions"]["1.2.0"] == "sunset"


def test_frozen_published_version(v120):
    d = bump(fx.faq_desc(v120["loc"]), "1.2.0", lambda d: props(d)[2].update(description="edited"))
    with pytest.raises(Refused, match="frozen"):
        prd.register(v120["cat"], v120["sc"], d, "ben")


# ---------------------------------------------------------------- 4. refresh

def test_incremental_refresh(v120):
    cat, loc = v120["cat"], v120["loc"]
    before = prd.as_of(cat, "support-faq")
    t = (now() - timedelta(minutes=5)).replace(microsecond=0)
    for n in range(12):
        fx.touch(loc, "UPDATE faq SET answer = ?, modified_at = ? WHERE id = ?",
                 (f"Neu {n}", (t + timedelta(seconds=n)).isoformat(), f"faq-{n:03d}"))
    run = rf.refresh(cat, "support-faq")
    assert (run["status"], run["inserted"], run["updated"], run["deleted"]) == ("ok", 0, 12, 0)
    assert prd.as_of(cat, "support-faq") == t + timedelta(seconds=11) > before
    assert json.loads(run["cursor_to"]) == {"office-faq": (t + timedelta(seconds=11)).isoformat()}
    assert {r["answer"] for r in prd.materialised(cat, "support-faq")} == {f"Neu {n}" for n in range(12)}


def test_re_run_is_idempotent(v120):
    cat = v120["cat"]
    snap = cat.query("SELECT * FROM product_row ORDER BY row_key")
    run = rf.refresh(cat, "support-faq")
    assert (run["inserted"], run["updated"], run["deleted"]) == (0, 0, 0)
    run2 = rf.refresh(cat, "support-faq")
    assert (run2["inserted"], run2["updated"], run2["deleted"]) == (0, 0, 0)
    assert cat.query("SELECT * FROM product_row ORDER BY row_key") == snap
    assert run2["as_of"] == run["as_of"]


def test_deletes_are_propagated(v120):
    cat, loc = v120["cat"], v120["loc"]
    fx.touch(loc, "DELETE FROM faq WHERE id = 'faq-000'")
    t = now()
    cat.execute("INSERT INTO corpus_item (item_id, asset_uri, charter_id, title, content_ref, text_ref, meta, state,"
                " state_changed_at, first_seen, last_seen) VALUES ('faq-001', ?, 'verein-faq-assistant', 'x', 'x',"
                " NULL, '{}', 'archived', ?, ?, ?)", (fx.FAQ_URI, t, t, t))  # a c04 retention decision
    run = rf.refresh(cat, "support-faq")
    assert run["deleted"] == 2
    keys = {r["id"] for r in prd.materialised(cat, "support-faq")}
    assert "faq-000" not in keys and "faq-001" not in keys and len(keys) == 10


def test_undeclared_columns_are_not_copied(v120):
    row = v120["cat"].one("SELECT data FROM product_row LIMIT 1")
    assert "internal_note" not in json.loads(row["data"]) and "Kassenstand" not in row["data"]


def test_refresh_fails_contract_test(v120):
    cat, loc = v120["cat"], v120["loc"]
    d = bump(fx.faq_desc(loc), "1.3.0", lambda d: props(d)[1].update(unique=True))
    release(v120, d)
    snap, as_of = prd.materialised(cat, "support-faq"), prd.as_of(cat, "support-faq")
    fx.touch(loc, "UPDATE faq SET question = 'Frage 1 zum Mitgliedsbeitrag?', modified_at = ? WHERE id = 'faq-002'",
             (now().isoformat(),))
    run = rf.refresh(cat, "support-faq")
    assert run["status"] == "failed" and "unique question (1)" in run["error"]
    assert prd.materialised(cat, "support-faq") == snap and prd.as_of(cat, "support-faq") == as_of
    assert run["as_of"] == as_of and not json.loads(
        cat.one("SELECT failures FROM product_test WHERE test_id = ?", (run["test_id"],))["failures"]) == []
    assert prd.last_refresh(cat, "support-faq")["status"] == "failed"


def test_refresh_error_keeps_previous_data(v120):
    cat = v120["cat"]
    fx.touch(v120["loc"], "DROP TABLE faq")
    snap = prd.materialised(cat, "support-faq")
    run = rf.refresh(cat, "support-faq")
    assert run["status"] == "failed" and "faq" in run["error"] and prd.materialised(cat, "support-faq") == snap


@pytest.mark.parametrize("sla, cron, minutes", [
    ({"value": 1, "unit": "d"}, "0 */12 * * *", 720),
    ({"value": 7, "unit": "d"}, "0 2 * * *", 1440),
    ({"value": 1, "unit": "h"}, "*/30 * * * *", 30),
    ({"value": 10, "unit": "m"}, "*/15 * * * *", 15),
])
def test_schedule_derived_from_sla(sla, cron, minutes):
    d = fx.faq_desc("sqlite:///x.db")
    d["contract"]["slaProperties"] = [{"property": "latency", **sla}]
    s = rf.schedule(d)
    assert s["interval_minutes"] == minutes and s["cron"].startswith(cron + " konmari product refresh support-faq")
    assert f"OnUnitActiveSec={minutes}min" in s["timer"] and "ExecStart=konmari product refresh" in s["service"]


def test_schedule_cli(v120, engagement, cli):
    code, out, _ = cli("product", "schedule", "support-faq", "--format", "systemd")
    assert code == 0 and "[Timer]" in out and "OnUnitActiveSec=720min" in out


def test_lineage_of_a_refresh(v120):
    cat = v120["cat"]
    release(v120, _v130(v120))
    run_id = cat.one("SELECT run_id FROM lineage_event WHERE job = 'product.refresh.support-faq'"
                     " ORDER BY event_time DESC LIMIT 1")["run_id"]
    rf.refresh(cat, "support-faq")
    last = cat.one("SELECT run_id FROM lineage_event WHERE job = 'product.refresh.support-faq'"
                   " ORDER BY event_time DESC, event_type LIMIT 1")["run_id"]
    assert last != run_id
    evs = lineage.events(cat, last)
    assert [e["eventType"] for e in evs] == ["START", "COMPLETE"]
    assert all(e["outputs"] == [{"namespace": "konmari://products", "name": "support-faq@1.3.0"}] for e in evs)
    assert evs[0]["inputs"] == [{"namespace": "konmari://sources", "name": "office-faq.faq"}]
    pub = cat.query("SELECT event_type FROM lineage_event WHERE job = 'product.publish.support-faq'")
    assert {r["event_type"] for r in pub} == {"START", "COMPLETE"}
    lines = (v120["tmp"] / "lineage.jsonl").read_text().splitlines()
    assert any(json.loads(x)["run"]["runId"] == last for x in lines)


def test_failed_refresh_emits_fail_event_and_marquez_transport(v120):
    cat, sent = v120["cat"], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        sent.append((req.full_url, json.loads(req.data)))
        return Resp(b"")
    t = lineage.HttpTransport("http://marquez:5000/api/v1/lineage", fake)
    fx.touch(v120["loc"], "DROP TABLE faq")
    rf.refresh(cat, "support-faq", transport=t)
    assert [e["eventType"] for _, e in sent] == ["START", "FAIL"]
    assert sent[1][1]["run"]["facets"]["errorMessage"]["message"]
    assert sent[0][0] == "http://marquez:5000/api/v1/lineage"


# ---------------------------------------------------------------- 5. catalog

def test_list_catalog_as_json(v120, engagement, cli):
    cat, sc = v120["cat"], v120["sc"]
    prd.register(cat, sc, _v130(v120), "ben")  # a draft
    code, out, _ = cli("product", "list", "--json")
    assert code == 0
    entry = {e["id"]: e for e in json.loads(out)}["support-faq"]
    assert set(entry) >= {"id", "name", "purpose", "owner", "current_version", "deprecated_versions",
                          "freshness_sla", "as_of", "last_refresh", "classification", "tools", "resources"}
    assert entry["callable_versions"] == ["1.2.0"] and entry["versions"]["1.3.0"] == "draft"
    assert entry["tools"] == ["support_faq.v1.get", "support_faq.v1.search"]
    assert entry["freshness_sla"] == "P1D" and entry["freshness"] == "fresh" and entry["last_refresh"] == "ok"
    assert entry["classification"] == {"personal": 1, "public": 4}
    assert entry["resources"] == ["konmari://products/support-faq/1.2.0"]


def test_withdrawn_versions_are_not_callable(v120):
    cat = v120["cat"]
    release(v120, _v130(v120))
    prd.rollback(cat, v120["sc"], "support-faq", "ben", "test")
    assert [v["version"] for v in prd.callable_versions(cat)] == ["1.2.0"]


def test_openmetadata_export_with_recorded_fixture(v120):
    recorded = {"id": "8f1c", "name": "support-faq", "fullyQualifiedName": "support-faq", "version": 0.1}
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake(req, timeout):
        seen.update(url=req.full_url, method=req.get_method(), body=json.loads(req.data),
                    auth=req.get_header("Authorization"))
        return Resp(json.dumps(recorded).encode())
    out = prd.export_openmetadata(v120["cat"], "support-faq", "http://om:8585/", "bot-token", fake)
    assert out == recorded
    assert seen["url"] == "http://om:8585/api/v1/dataProducts" and seen["method"] == "PUT"
    assert seen["body"]["name"] == "support-faq" and seen["body"]["domains"] == ["kb-and-sops"]
    assert seen["auth"] == "Bearer bot-token"


def test_personal_values_stay_out_of_the_audit_trail(v120):
    trail = json.dumps([r["details"] for r in v120["cat"].query("SELECT details FROM audit_event")])
    assert "helfer1@example.org" not in trail


def test_init_cli(engagement, cli):
    code, out, _ = cli("product", "init", "support-faq", "--category", "kb-and-sops", "--owner", "ben",
                       "--use-case", "Member FAQ assistant")
    assert code == 0 and (engagement / "products" / "support-faq" / "contract.yaml").is_file()
    assert cli("product", "init", "support-faq", "--category", "kb-and-sops", "--owner", "ben",
               "--use-case", "x")[0] == 1


def test_manual_deprecation_with_window(v120):
    t = now()
    row = prd.deprecate(v120["cat"], v120["sc"], "support-faq", "1.2.0", "ben", "2.0.0", days=30, at=t)
    assert row["state"] == "deprecated" and row["successor"] == "2.0.0" and row["sunset_at"] == t + timedelta(days=30)
    assert [v["version"] for v in prd.callable_versions(v120["cat"], t + timedelta(days=29))] == ["1.2.0"]
    assert prd.callable_versions(v120["cat"], t + timedelta(days=31)) == []


def test_source_locator_without_password():
    d = fx.faq_desc("postgresql://reader:geheim@db.example.org/office")
    assert any("locator: contains a password" in e for e in dsc.lint(d))
