"""c06 semantic-metadata: scenarios of specs/semantic-metadata/spec.md (synthetic data, mocked connectors)."""

import json
from datetime import date
from pathlib import Path

import c06_fixture as fx
import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from konmari.canonical import dump_yaml
from konmari.chunking import chunker as ck
from konmari.chunking import store as cst
from konmari.enrich import store as est
from konmari.enrich.providers import ContextModel, SharePointAcl, normalize_acl
from konmari.inventory.connectors import GRAPH
from konmari.inventory.lineage import MemoryTransport
from konmari.versions import Refused

OL = Path(__file__).parent / "fixtures" / "openlineage"
CRM = "manual://crm/invoices"


@pytest.fixture
def en(c06):
    cst.run(c06["cat"], c06["sc"], "ben")
    return {**c06, "transport": MemoryTransport()}


def enrich(e, **kw):
    kw.setdefault("transport", e["transport"])
    return est.run(e["cat"], e["sc"], "ben", **kw)


def chunks_of(e, suffix):
    return [c for c in cst.active_chunks(e["cat"]) if c["item_id"].endswith(suffix)]


def meta(e, subject):
    return est.metadata(e["cat"], subject)


# ---------------------------------------------------------------- 3.1 envelope model

def test_envelope_schema_validation():
    good = {"source_id": "s", "source_uri": "u", "source_system": "sharepoint", "content_type": "kb-and-sops",
            "owner": "ben", "created_at": est.iso("2025-01-10T09:00:00+01:00"), "modified_at": est.iso("2026-03-02"),
            "freshness_class": "monthly", "authority": "primary", "lineage": [{"step": "source"}],
            "acl_tags": {"allow": ["group:Vorstand"], "deny": []}, "sensitivity": "internal", "language": "de",
            "enriched_at": est.iso("2026-10-24T10:00:00Z")}
    assert est.envelope_errors(good) == []
    assert good["modified_at"] == "2026-03-02T00:00:00+00:00"
    assert est.envelope_errors({**good, "created_at": "2025-01-10T09:00:00"}) == ["created_at has no time zone"]
    assert est.envelope_errors({**good, "valid_until": "31.12.2025"}) == ["valid_until is not ISO 8601"]
    assert est.envelope_errors({**good, "owner": None, "acl_tags": {"allow": [], "deny": []}}) == [
        "missing owner", "missing acl_tags"]
    assert est.envelope_errors({**good, "authority": "maybe", "colour": 1}) == [
        "authority must be primary or secondary", "unknown field colour"]


def test_envelope_present_on_chunk(en):
    enrich(en)
    docx = chunks_of(en, "policies.docx")
    assert docx
    for c in docx:
        m = meta(en, c["chunk_id"])
        assert all(m["envelope"].get(f) for f in est.MANDATORY), m["envelope"]
        assert m["status"] == "ready-for-agents" and m["complete"]
        env = m["envelope"]
        assert (env["owner"], env["content_type"], env["sensitivity"], env["freshness_class"], env["language"]) == \
            ("ben", "policies-and-contracts", "confidential", "monthly", "de")
        assert env["created_at"] == "2025-01-10T09:00:00+01:00" and env["modified_at"].endswith("+00:00")
        assert env["acl_tags"] == {"allow": ["group:Geschäftsstelle", "group:Vorstand"], "deny": []}


def _schema_file(e, extra_cols=(), **table):
    cols = [{"name": "kd_nr", "description": "Kundennummer", "type": "text", "nullable": False},
            {"name": "umsatz", "description": "Jahresumsatz", "type": "decimal", "unit": "EUR", "nullable": True},
            {"name": "geaendert", "description": "Letzte Änderung", "type": "timestamp", "nullable": False},
            *extra_cols]
    p = e["tmp"] / "schema.yaml"
    p.write_text(dump_yaml({"tables": [{"table": CRM, "created_at": "2024-05-01T00:00:00Z", "language": "de",
                                        "modified_at": "2026-03-01T10:00:00Z",
                                        "acl": {"allow": ["group:Geschäftsstelle"]}, **table, "columns": cols}]}),
                 encoding="utf-8")
    return p


def test_envelope_on_structured_record(en):
    est.load_schema(en["cat"], en["sc"], _schema_file(en), "ben")
    rep = enrich(en)
    m = meta(en, CRM)
    assert m["subject_kind"] == "table" and m["status"] == "ready-for-agents", m["reasons"]
    rec = est.record_envelope(en["cat"], CRM, fx.CRM_ROWS[0]["geaendert"])
    assert all(rec.get(f) for f in est.MANDATORY)
    assert rec["modified_at"] == "2026-03-01T10:00:00+00:00" and rec["sensitivity"] == "personal"
    assert rep["schema"][CRM]["coverage"] == 1.0


# ---------------------------------------------------------------- 3.2 derivation

def _activate_policy(cat, sources):
    rules = {"address": {"strategy": "source_priority", "sources": sources, "tie_breaker": "most_recent",
                         "timestamp_field": "updated_at", "owner": "ben", "rationale": "test"}}
    cat.execute("INSERT INTO policy_version VALUES ('customer', 1, 'approved', TRUE, ?, 'h', 'ben', ?, 'ben', ?,"
                " '[]', '[]')", (json.dumps({"entity": "customer", "rules": rules}), "2026-01-01", "2026-01-01"))


def test_authority_from_source_of_truth_policy(en):
    _activate_policy(en["cat"], ["erp", "crm"])
    enrich(en)
    erp = {meta(en, c["chunk_id"])["authority"] for c in chunks_of(en, "prices.pdf")}
    crm = {meta(en, c["chunk_id"])["authority"] for c in chunks_of(en, "notes.txt")}
    assert erp == {"primary"} and crm == {"secondary"}


def test_manual_override_survives_rerun(en):
    item = chunks_of(en, "faq.html")[0]["item_id"]
    enrich(en)
    est.override(en["cat"], en["sc"], item, "owner", "clara", "ben", "Clara maintains the FAQ")
    enrich(en)
    enrich(en)
    assert {meta(en, c["chunk_id"])["owner"] for c in chunks_of(en, "faq.html")} == {"clara"}
    ev = [json.loads(r["details"]) for r in en["cat"].query(
        "SELECT details FROM audit_event WHERE action = 'enrich.override'")]
    assert ev == [{**ev[0], "subject": item, "field": "owner", "previous": "ben", "new": "clara",
                   "reason": "Clara maintains the FAQ"}]


def test_override_refuses_lineage_and_unknown_subject(en):
    with pytest.raises(Refused, match="cannot be overridden"):
        est.override(en["cat"], en["sc"], CRM, "lineage", "x", "ben", "r")
    with pytest.raises(Refused, match="unknown"):
        est.override(en["cat"], en["sc"], "nope", "owner", "x", "ben", "r")


# ---------------------------------------------------------------- 3.4 ACL sync

class FakeGraph:
    """Recorded Graph responses (URL -> body); `down` simulates a connector outage."""

    def __init__(self, groups):
        self.groups, self.down, self.calls = groups, False, []

    def get(self, url):
        self.calls.append(url)
        if self.down:
            raise ConnectionError("SharePoint unreachable")
        assert url == f"{GRAPH}/drives/b!pol/items/it7/permissions"
        return json.dumps({"value": [{"roles": ["read"], "grantedToV2": {"group": {"displayName": g}}}
                                     for g in self.groups] + [{"roles": ["sp.limited"], "grantedToV2": {
                                         "group": {"displayName": "Gäste"}}}]}).encode()


@pytest.fixture
def sp(c06):
    """The policies document comes from a SharePoint library whose permissions are read through Graph."""
    rows = fx.entries(c06["files"])
    rows[0] |= {"drive_id": "b!pol", "sp_item_id": "it7"}
    rows[0].pop("acl")
    fx.register(c06["cat"], fx.write_manifest(c06["tmp"] / "sp.jsonl", rows))
    c06["cat"].execute("UPDATE inventory_source SET connector = 'sharepoint'")
    cst.run(c06["cat"], c06["sc"], "ben")
    graph = FakeGraph(["Vertrieb", "Vorstand"])
    return {**c06, "graph": graph, "acl": {"sharepoint": SharePointAcl(graph)}, "transport": MemoryTransport()}


def test_permission_change_propagates(sp):
    enrich(sp, acl=sp["acl"])
    docx = chunks_of(sp, "policies.docx")
    assert all("group:Vertrieb" in meta(sp, c["chunk_id"])["acl_tags"]["allow"] for c in docx)
    assert len(sp["graph"].calls) == 1  # one permission lookup per item, not per chunk
    sp["graph"].groups = ["Vorstand"]
    rep = enrich(sp, acl=sp["acl"])
    assert all(meta(sp, c["chunk_id"])["acl_tags"]["allow"] == ["group:Vorstand"] for c in docx)
    ev = sp["cat"].query("SELECT * FROM audit_event WHERE action = 'enrich.acl_change'")
    d = json.loads(ev[-1]["details"])
    assert rep["acl_changes"] == 1 and "group:Vertrieb" in d["previous"]["allow"] and d["new"]["allow"] == \
        ["group:Vorstand"] and set(d["chunks"]) == {c["chunk_id"] for c in docx}


def test_source_unreachable_marks_stale(sp):
    enrich(sp, acl=sp["acl"])
    sp["graph"].down = True
    rep = enrich(sp, acl=sp["acl"])
    docx = [c["chunk_id"] for c in chunks_of(sp, "policies.docx")]
    for cid in docx:
        m = meta(sp, cid)
        assert m["acl_stale"] and m["acl_tags"]["allow"] == ["group:Vertrieb", "group:Vorstand"]
    assert set(rep["acl_stale"]) == set(docx) and "acl_stale" in est.render_report(rep)
    assert sp["cat"].one("SELECT COUNT(*) AS n FROM chunk_acl WHERE acl_stale")["n"] == len(docx)


def test_normalize_acl():
    assert normalize_acl(["Vorstand", "user:Ben@Example.org", "group: Büro "]) == {
        "allow": ["group:Büro", "group:Vorstand", "user:ben@example.org"], "deny": []}


# ---------------------------------------------------------------- 3.5 freshness

def test_freshness_classification():
    assert [est.freshness_class(f) for f in ("continuous", "PT15M", "P1D", "PT12H", "P7D", "P1W", "P1M", "P3M",
                                             "irregular", "unknown", None)] == \
        ["live", "live", "daily", "daily", "weekly", "weekly", "monthly", "static", "static", "static", "static"]


def test_expired_policy_flagged(en):
    item = chunks_of(en, "policies.docx")[0]["item_id"]
    est.override(en["cat"], en["sc"], item, "valid_until", "2025-12-31", "clara", "policy ended")
    rep = enrich(en, today=date(2026, 10, 24))
    docx = [c["chunk_id"] for c in chunks_of(en, "policies.docx")]
    assert all(meta(en, c)["expired"] for c in docx) and set(docx) <= set(rep["expired"])
    m = meta(en, docx[0])
    assert m["stale_after"] == "2026-04-01T12:00:00+00:00"  # modified_at + 30 days (monthly)
    assert not meta(en, chunks_of(en, "faq.html")[0]["chunk_id"])["expired"]


# ---------------------------------------------------------------- 3.6 completeness gate

def test_missing_acl_blocks_readiness(c06):
    rows = fx.entries(c06["files"])
    rows[2].pop("acl")  # the HTML FAQ export carries no permissions
    fx.register(c06["cat"], fx.write_manifest(c06["tmp"] / "m.jsonl", rows))
    cst.run(c06["cat"], c06["sc"], "ben")
    rep = est.run(c06["cat"], c06["sc"], "ben", transport=MemoryTransport())
    faq = [c["chunk_id"] for c in cst.active_chunks(c06["cat"]) if c["item_id"].endswith("faq.html")]
    for cid in faq:
        m = est.metadata(c06["cat"], cid)
        assert m["status"] == "needs-review" and "missing acl_tags" in m["reasons"] and m["acl_tags"] is None
    assert set(rep["missing"]["acl_tags"]) == set(faq)
    assert "missing acl_tags" in est.render_report(rep)


def test_missing_owner_is_never_defaulted(en):
    en["cat"].execute("UPDATE source_asset SET owner = NULL WHERE asset_uri = 'manual://kb/faq-export'")
    rep = enrich(en)
    faq = [c["chunk_id"] for c in chunks_of(en, "faq.html")]
    assert all(meta(en, c)["owner"] is None and meta(en, c)["status"] == "needs-review" for c in faq)
    assert set(rep["missing"]["owner"]) == set(faq)


def test_completeness_reported_to_scorecard(en):
    rep = enrich(en)
    rows = en["cat"].query("SELECT * FROM standardize_metric WHERE kind = 'metadata_completeness'")
    assert {r["entity"]: r["value"] for r in rows} == rep["completeness"] == {
        "kb-and-sops": 1.0, "policies-and-contracts": 1.0, "support-history": 1.0}
    view = en["cat"].query("SELECT * FROM metadata_completeness ORDER BY category_id")
    assert [v["category_id"] for v in view] == ["kb-and-sops", "policies-and-contracts", "support-history"]


# ---------------------------------------------------------------- 3.7 clean table schemas

def test_undocumented_column_blocks_table(en):
    est.load_schema(en["cat"], en["sc"], _schema_file(en, [{"name": "kd_stat", "type": "text", "nullable": False}]),
                    "ben")
    rep = enrich(en)
    m = meta(en, CRM)
    assert m["status"] == "needs-review" and "column kd_stat: no description or glossary link" in m["reasons"]
    assert rep["schema"][CRM]["gaps"] == {"kd_stat": ["no description or glossary link"]}
    ev = en["cat"].query("SELECT payload FROM asset_evidence WHERE kind = 'glossary_coverage'")
    assert json.loads(ev[-1]["payload"]) == {"described": False, "mapped": 0, "total": 4,
                                             "contract_status": "incomplete"}


def test_numeric_column_needs_unit_and_type(en):
    est.load_schema(en["cat"], en["sc"], _schema_file(en, [{"name": "betrag", "description": "Betrag",
                                                            "type": "decimal", "nullable": True},
                                                           {"name": "x", "description": "X"}]), "ben")
    gaps = est.schema_coverage(en["cat"], CRM)["gaps"]
    assert gaps == {"betrag": ["numeric without unit"], "x": ["no data type", "nullability not declared"]}


def test_column_described_via_glossary(en):
    from konmari.glossary import store as gl

    cat, sc = en["cat"], en["sc"]
    gl.set_stewards(cat, sc, ["clara", "ben"], "anna")
    t = gl.add(cat, sc, {"labels": {"pref": {"de": "Kundenstatus", "en": "customer status"}},
                         "definition": "Ob ein Kunde aktiv betreut wird.", "owner": "ben"}, "ben")
    gl.submit(cat, sc, t["term_id"], "ben")
    gl.decide(cat, sc, t["term_id"], "clara", True)
    est.load_schema(cat, sc, _schema_file(en, [{"name": "kd_stat", "type": "text", "nullable": False,
                                                "term": "Kundenstatus"}]), "ben")
    rep = enrich(en)
    assert rep["schema"][CRM]["gaps"] == {} and meta(en, CRM)["status"] == "ready-for-agents"
    col = next(c for c in est.schema_view(cat, CRM) if c["column_name"] == "kd_stat")
    assert col["term_id"] == "kundenstatus" and col["definition"] == "Ob ein Kunde aktiv betreut wird."


# ---------------------------------------------------------------- 3.8 context header, LLM context

class FakeModel(ContextModel):
    def __init__(self, self_hosted=True, processor=None):
        self.name, self.self_hosted, self.processor, self.prompts = "fake-local-7b", self_hosted, processor, []

    def complete(self, prompt):
        self.prompts.append(prompt)
        return "Kontext: Abschnitt der Vereinsunterlagen."


def test_deterministic_header_created(en):
    model = FakeModel()
    enrich(en, model=model)  # llm_context is off by default: the model is never asked
    assert model.prompts == []
    ex = next(c for c in chunks_of(en, "policies.docx") if c["heading_path"][-1] == "Exceptions")
    h = meta(en, ex["chunk_id"])["context_header"]
    assert h == "policies.docx > Policies > Returns > Exceptions | sharepoint | 2026-03-02"
    assert all(ck.RegexTokenizer().count(meta(en, c["chunk_id"])["context_header"]) <= 100
               for c in cst.active_chunks(en["cat"]))
    long = est.context_header("T", [f"Überschrift {i} mit vielen Wörtern" for i in range(40)], "sp", "2026-01-01")
    assert ck.RegexTokenizer().count(long) <= 100 and long.startswith("T > ")


def test_personal_data_not_sent_to_llm(en):
    cat, sc = en["cat"], en["sc"]
    cst.set_profile(cat, sc, "kb-and-sops", {"llm_context": True}, "ben", "try contextual retrieval")
    faq = chunks_of(en, "faq.html")
    est.override(cat, sc, faq[0]["item_id"], "sensitivity", "personal", "eva", "member names in the FAQ")
    model = FakeModel()
    rep = enrich(en, model=model)
    pdf = chunks_of(en, "prices.pdf")
    assert rep["llm"]["skipped_personal"] == len(faq) and rep["llm"]["called"] == len(pdf) == len(model.prompts)
    assert not any("Familien" in p or "Mitgliedsbeitrag" in p for p in model.prompts)
    assert all(meta(en, c["chunk_id"])["generated_context"] is None for c in faq)
    g = meta(en, pdf[0]["chunk_id"])["generated_context"]
    assert g["model"] == "fake-local-7b" and g["label"] == "generated by fake-local-7b"
    enrich(en, model=model)
    assert len(model.prompts) == len(pdf)  # unchanged chunks reuse the stored context


def test_unapproved_processor_gets_nothing(en):
    cst.set_profile(en["cat"], en["sc"], "kb-and-sops", {"llm_context": True}, "ben", "try")
    model = FakeModel(self_hosted=False, processor="cloud-inc")
    rep = enrich(en, model=model)
    assert model.prompts == [] and "approved processor" in rep["llm"]["refused"]
    cst.set_profile(en["cat"], en["sc"], "kb-and-sops", {"approved_processors": ["cloud-inc"]}, "eva", "DPA signed")
    assert enrich(en, model=model)["llm"]["called"] > 0


# ---------------------------------------------------------------- 3.9 lineage

def test_chunk_traceable_to_source(en, cli):
    rep = enrich(en)
    c = chunks_of(en, "prices.pdf")[0]
    code, out, err = cli("enrich", "lineage", c["chunk_id"])
    assert code == 0, err
    steps = {s["step"]: s for s in json.loads(out)}
    assert steps["source"]["uri"] == str(en["files"]["pdf"]) and steps["source"]["version"] == c["source_version"]
    assert steps["normalize"]["category_step"] == "complete"
    assert steps["chunk"]["run"] == cst.active_set(en["cat"], c["item_id"])["run_id"]
    assert steps["chunk"]["profile"]["max_tokens"] == 400 and steps["chunk"]["tools"]["konmari"]
    assert steps["enrich"]["run"] == rep["run_id"]


def _validators():
    reg = Registry().with_resources([(s["$id"], Resource.from_contents(s)) for s in
                                     (json.loads(p.read_text()) for p in OL.glob("*.json"))])
    ol = json.loads((OL / "OpenLineage-2-0-2.json").read_text())
    facet = json.loads((Path(est.__file__).parent.parent / "schemas" / "chunk-metadata-facet.schema.json").read_text())
    return (Draft202012Validator({"$ref": f"{ol['$id']}#/$defs/RunEvent"}, registry=reg),
            Draft202012Validator({"$ref": f"{ol['$id']}#/$defs/DatasetFacet"}, registry=reg),
            Draft202012Validator(facet))


def test_openlineage_event_emitted(en):
    est.load_schema(en["cat"], en["sc"], _schema_file(en), "ben")
    rep = enrich(en)
    ev = en["transport"].events[-1]
    assert ev is rep["lineage_event"] and ev["eventType"] == "COMPLETE"
    run_v, base_v, facet_v = _validators()
    assert not list(run_v.iter_errors(ev))
    for out in ev["outputs"]:
        f = out["facets"]
        assert {"ownership", "schema", "documentation", "konmari_chunkMetadata"} <= set(f)
        for v in f.values():
            assert not list(base_v.iter_errors(v))
        assert not list(facet_v.iter_errors(f["konmari_chunkMetadata"]))
        assert f["konmari_chunkMetadata"]["_schemaURL"].endswith("/v0.1.0/src/konmari/schemas/"
                                                                 "chunk-metadata-facet.schema.json")
    table = next(o for o in ev["outputs"] if o["name"] == f"chunk_metadata/{CRM}")
    assert {x["name"] for x in table["facets"]["schema"]["fields"]} == {"kd_nr", "umsatz", "geaendert"}
    assert table["facets"]["ownership"]["owners"] == [{"name": "ben", "type": "owner"}]


# ---------------------------------------------------------------- 3.10 run report and rollback

def test_rollback_restores_previous_metadata(en, cli):
    first = enrich(en)
    faq = chunks_of(en, "faq.html")
    before = {c["chunk_id"]: meta(en, c["chunk_id"]) for c in faq}
    est.override(en["cat"], en["sc"], faq[0]["item_id"], "language", "en", "ben", "test")
    second = enrich(en)
    assert {meta(en, c["chunk_id"])["envelope"]["language"] for c in faq} == {"en"}
    code, out, err = cli("enrich", "rollback", second["run_id"], "--reason", "wrong language", "--as", "ben")
    assert code == 0, err
    for cid, m in before.items():
        after = meta(en, cid)
        assert after["envelope"] == m["envelope"] and after["run_id"] == first["run_id"]
    ev = en["cat"].query("SELECT * FROM audit_event WHERE action = 'enrich.rollback'")
    assert json.loads(ev[-1]["details"])["reason"] == "wrong language"
    code, _, err = cli("enrich", "rollback", second["run_id"], "--as", "ben")
    assert code == 1 and "already rolled back" in err


def test_enrich_cli_run_and_override(en, cli):
    code, out, err = cli("enrich", "run", "--as", "ben", "--no-lineage")
    assert code == 0, err
    assert "Enrichment report" in out and "completeness per category" in out
    item = chunks_of(en, "notes.txt")[0]["item_id"]
    code, out, err = cli("enrich", "override", item, "--field", "acl_tags", "--value", "group:Vorstand", "--reason",
                         "manual ACL", "--as", "ben")
    assert code == 0 and "group:Vorstand" in out
    code, out, err = cli("enrich", "schema", "report")
    assert code == 0
