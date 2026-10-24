"""c06 business-glossary: scenarios of specs/business-glossary/spec.md (synthetic terms only)."""

import json

import pytest
from rdflib import Graph, Literal
from rdflib.namespace import OWL, RDF, SKOS

from konmari.canonical import dump_yaml
from konmari.chunking import store as cst
from konmari.enrich import store as est
from konmari.glossary import store as gl
from konmari.inventory.lineage import MemoryTransport
from konmari.versions import Refused

from test_semantic_metadata import FakeModel


@pytest.fixture
def g(c06):
    cat, sc = c06["cat"], c06["sc"]
    gl.set_stewards(cat, sc, ["clara", "ben"], "anna")
    return c06


def term(cat, sc, de, definition="Definition.", actor="ben", alt=None, **kw):
    lab = {"pref": {"de": de}}
    if alt:
        lab["alt"] = {"de": alt}
    return gl.add(cat, sc, {"labels": lab, "definition": definition, "owner": "ben", **kw}, actor)["term_id"]


def approved(g, de, actor="ben", approver="clara", **kw):
    tid = term(g["cat"], g["sc"], de, actor=actor, **kw)
    gl.submit(g["cat"], g["sc"], tid, actor)
    gl.decide(g["cat"], g["sc"], tid, approver, True, "ok")
    return tid


# ---------------------------------------------------------------- 4.1 term model

def test_duplicate_preferred_label_rejected(g):
    first = approved(g, "Kunde")
    with pytest.raises(Refused) as e:
        term(g["cat"], g["sc"], "kunde")
    assert f"already exists: term '{first}'" in e.value.reasons[0]


def test_label_used_twice_in_one_term_rejected(g):
    with pytest.raises(Refused) as e:
        term(g["cat"], g["sc"], "Umsatz", alt=["Erlös", "Umsatz"])
    assert any("label-disjointness" in r for r in e.value.reasons)
    assert gl.term_errors({"labels": {"pref": {"de": "Umsatz"}, "hidden": {"de": ["Umsatz"]}}})
    assert gl.term_errors({"labels": {"pref": {"fr": "Client"}}}) == [
        "a term needs a preferred label in German or English (labels.pref.de / labels.pref.en)"]
    assert gl.term_errors({"labels": {"pref": {"de": ["A", "B"]}}})


# ---------------------------------------------------------------- 4.2 harvesting

def test_acronym_proposed_as_candidate(g, cli):
    cat, sc = g["cat"], g["sc"]
    md = g["tmp"] / "kennzahlen.md"
    md.write_text("".join(f"# Monat {i}\n\nDer MRR im Monat {i} lag bei {i}00 EUR.\n\n" for i in range(39)),
                  encoding="utf-8")  # plus the FAQ's "MRR-Dashboard": 40 chunks
    from c06_fixture import register, write_manifest

    register(cat, write_manifest(g["tmp"] / "k.jsonl", [{"asset_uri": "manual://kb/sops", "path": str(md)}]))
    cst.run(cat, sc, "ben")
    schema = g["tmp"] / "schema.yaml"
    schema.write_text(dump_yaml({"tables": [{"table": "manual://crm/invoices", "columns": [
        {"name": "mrr_eur", "description": "MRR in EUR", "type": "decimal", "unit": "EUR", "nullable": True},
        {"name": "MRR_ziel", "description": "Ziel", "type": "decimal", "unit": "EUR", "nullable": True}]}]}),
                      encoding="utf-8")
    est.load_schema(cat, sc, schema, "ben")
    cands = gl.harvest(cat, sc, "ben")
    mrr = next(c for c in cands if c["label"] == "MRR")
    assert (mrr["count"], mrr["chunks"], mrr["columns"]) == (42, 40, 2)
    assert [c["count"] for c in cands] == sorted((c["count"] for c in cands), reverse=True)
    assert len(mrr["examples"]) == 5 and mrr["examples"][0].startswith("chunk:")
    t = gl.term(cat, mrr["term_id"])
    assert gl.status(t) == "draft" and t["latest"]["content"]["sources"][0]["harvest"]["count"] == 42
    assert mrr["term_id"] not in {r["term_id"] for r in gl.exposed(cat)}
    assert "MRR" not in {c["label"] for c in gl.harvest(cat, sc, "ben")}  # known labels are not proposed again


# ---------------------------------------------------------------- 4.3 lifecycle

def test_author_cannot_self_approve(g, cli):
    tid = term(g["cat"], g["sc"], "Mitglied", actor="clara")
    gl.submit(g["cat"], g["sc"], tid, "clara")
    code, _, err = cli("glossary", "approve", tid, "--as", "clara")
    assert code == 1 and "cannot approve" in err
    assert gl.term(g["cat"], tid)["latest"]["status"] == "in_review"
    ev = g["cat"].query("SELECT result FROM audit_event WHERE action = 'glossary.approve'")
    assert ev[-1]["result"] == "refused"
    code, out, err = cli("glossary", "approve", tid, "--as", "ben", "--comment", "passt")
    assert code == 0 and "approved" in out


def test_edit_of_approved_term_goes_back_to_review(g):
    cat, sc = g["cat"], g["sc"]
    tid = approved(g, "Aktiver Kunde", definition="Kunde mit Kauf in den letzten 12 Monaten.")
    gl.edit(cat, sc, tid, {"definition": "Kunde mit Kauf in den letzten 6 Monaten."}, "clara")
    t = gl.term(cat, tid)
    assert (t["latest"]["version"], t["latest"]["status"], t["exposed_version"]) == (2, "in_review", 1)
    assert gl.exposed(cat)[0]["content"]["definition"] == "Kunde mit Kauf in den letzten 12 Monaten."
    with pytest.raises(Refused):
        gl.decide(cat, sc, tid, "clara", True)  # clara wrote version 2
    gl.decide(cat, sc, tid, "ben", True)
    assert gl.exposed(cat)[0]["content"]["definition"] == "Kunde mit Kauf in den letzten 6 Monaten."
    actions = [r["action"] for r in cat.query("SELECT action FROM audit_event WHERE action LIKE 'glossary.%'"
                                             " ORDER BY seq")]
    assert actions == ["glossary.stewards", "glossary.add", "glossary.submit", "glossary.approve", "glossary.edit",
                       "glossary.approve", "glossary.approve"]


def test_glossary_without_steward_blocked(c06):
    cat, sc = c06["cat"], c06["sc"]
    tid = term(cat, sc, "Beitrag")
    with pytest.raises(Refused, match="no steward"):
        gl.submit(cat, sc, tid, "ben")
    assert gl.term(cat, tid)["latest"]["status"] == "draft"


def test_transitions_logged_with_actor_and_comment(g):
    tid = term(g["cat"], g["sc"], "Spende")
    gl.submit(g["cat"], g["sc"], tid, "ben")
    gl.decide(g["cat"], g["sc"], tid, "clara", False, "zu unklar")
    ev = g["cat"].query("SELECT * FROM audit_event WHERE action = 'glossary.reject'")[-1]
    assert ev["actor"] == "clara" and json.loads(ev["details"])["reason"] == "zu unklar" and ev["ts"]
    assert gl.status(gl.term(g["cat"], tid)) == "rejected"


# ---------------------------------------------------------------- 4.4 AI-drafted definitions

def test_ai_draft_needs_approval(g):
    cat, sc = g["cat"], g["sc"]
    cst.run(cat, sc, "ben")
    est.run(cat, sc, "ben", transport=MemoryTransport())
    est.override(cat, sc, "manual://support/tickets/notes.txt", "sensitivity", "personal", "eva", "Namen")
    est.run(cat, sc, "ben", transport=MemoryTransport())
    tid = term(cat, sc, "Churn", definition="")
    model = FakeModel()
    t = gl.ai_draft(cat, sc, tid, model, "ben", [])
    assert t["latest"]["status"] == "draft" and t["latest"]["ai_drafted"] == {"model": "fake-local-7b"}
    assert t["latest"]["content"]["definition"] == "Kontext: Abschnitt der Vereinsunterlagen."
    assert tid not in {r["term_id"] for r in gl.exposed(cat)}
    assert "Churn-Rate" not in model.prompts[0]  # the personal notes chunk was not sent
    with pytest.raises(Refused, match="approved processor"):
        gl.ai_draft(cat, sc, tid, FakeModel(self_hosted=False, processor="x"), "ben", [])


# ---------------------------------------------------------------- 4.5 links

def test_synonym_match_links_chunk(g):
    cat, sc = g["cat"], g["sc"]
    cst.run(cat, sc, "ben")
    est.run(cat, sc, "ben", transport=MemoryTransport())
    tid = approved(g, "Kündigungsquote", alt=["Churn-Rate"])
    notes = [c for c in cst.active_chunks(cat) if c["item_id"].endswith("notes.txt")]
    assert all(tid in est.metadata(cat, c["chunk_id"])["glossary_terms"] for c in notes)
    others = [c for c in cst.active_chunks(cat) if not c["item_id"].endswith("notes.txt")]
    assert not any(tid in est.metadata(cat, c["chunk_id"])["glossary_terms"] for c in others)
    assert {(x["kind"], x["target"]) for x in gl.links(cat, tid)} == {("chunk", c["chunk_id"]) for c in notes}
    est.run(cat, sc, "ben", transport=MemoryTransport())  # a re-run keeps the link
    assert all(tid in est.metadata(cat, c["chunk_id"])["glossary_terms"] for c in notes)


def test_metric_definition_complete(g):
    cat, sc = g["cat"], g["sc"]
    m = {"formula": "SUM(beitrag) / 12", "unit": "EUR", "owner": "ben"}
    tid = term(cat, sc, "Monatlicher wiederkehrender Umsatz", metric=m)
    gl.submit(cat, sc, tid, "ben")
    with pytest.raises(Refused) as e:
        gl.decide(cat, sc, tid, "clara", True)
    assert e.value.reasons == ["metric definition: missing field 'grain'"]
    gl.edit(cat, sc, tid, {"metric": {**m, "grain": "month"}}, "ben")
    gl.decide(cat, sc, tid, "clara", True)
    row = cat.one("SELECT * FROM glossary_metric WHERE term_id = ?", (tid,))
    assert (row["grain"], row["unit"], row["formula"]) == ("month", "EUR", "SUM(beitrag) / 12")
    assert ("metric", tid) in {(x["kind"], x["target"]) for x in gl.links(cat, tid)}


# ---------------------------------------------------------------- 4.6 conflicts

def _two_tables(g):
    p = g["tmp"] / "schema.yaml"
    p.write_text(dump_yaml({"tables": [
        {"table": "manual://crm/invoices", "columns": [{"name": "revenue", "type": "decimal", "unit": "kEUR",
                                                        "nullable": True, "term": "Umsatz"}]},
        {"table": "manual://kb/sops", "columns": [{"name": "umsatz", "type": "decimal", "unit": "EUR",
                                                   "nullable": True, "term": "Umsatz"}]}]}), encoding="utf-8")
    est.load_schema(g["cat"], g["sc"], p, "ben")


def test_unit_conflict_reported(g, cli):
    cat, sc = g["cat"], g["sc"]
    _two_tables(g)
    tid = approved(g, "Umsatz")
    rep = gl.report(cat, sc.charter_id)
    c = next(c for c in rep["conflicts"] if c["kind"] == "unit")
    assert c["terms"] == [tid] and set(c["values"].values()) == {"EUR", "kEUR"}
    assert all(x["flagged"] for x in gl.links(cat, tid) if x["kind"] == "column")
    with pytest.raises(Refused, match="not a steward"):
        gl.resolve_conflict(cat, sc, c["conflict_id"], "eva", "x")
    code, out, err = cli("glossary", "conflict", c["conflict_id"], "--decision", "CRM column is converted to EUR",
                         "--as", "clara")
    assert code == 0, err
    assert not gl.report(cat, sc.charter_id)["conflicts"]
    assert not any(x["flagged"] for x in gl.links(cat, tid))


def test_label_clash_across_categories_reported(g):
    a = approved(g, "Beitrag", alt=["Gebühr"], category="kb-and-sops")
    b = approved(g, "Kursgebühr", alt=["Gebühr"], category="financial-ops-metrics")
    c = next(c for c in gl.report(g["cat"], g["sc"].charter_id)["conflicts"] if c["kind"] == "label")
    assert c["label"] == "gebühr@de" and c["terms"] == sorted([a, b]) and len(c["categories"]) == 2


# ---------------------------------------------------------------- 4.7 export and coverage

def test_export_excludes_drafts(g, cli):
    cat, sc = g["cat"], g["sc"]
    ok = approved(g, "Kündigungsquote", alt=["Churn-Rate"], definition="Anteil gekündigter Mitglieder.")
    new = approved(g, "Austrittsquote")
    old = approved(g, "Abgangsrate")
    gl.deprecate(cat, sc, old, "clara", new, "ersetzt")
    term(cat, sc, "Entwurf")
    review = term(cat, sc, "Prüfung")
    gl.submit(cat, sc, review, "ben")
    rej = term(cat, sc, "Abgelehnt")
    gl.submit(cat, sc, rej, "ben")
    gl.decide(cat, sc, rej, "clara", False, "nein")
    out = g["tmp"] / "glossary.ttl"
    code, _, err = cli("glossary", "export", "--format", "skos", "-o", out)
    assert code == 0, err
    graph = Graph().parse(out, format="turtle")
    concepts = {str(s).rsplit(":", 1)[1] for s in graph.subjects(RDF.type, SKOS.Concept)}
    assert concepts == {ok, new, old}
    s = next(s for s in graph.subjects(RDF.type, SKOS.Concept) if str(s).endswith(ok))
    assert graph.value(s, SKOS.prefLabel) == Literal("Kündigungsquote", lang="de")
    assert graph.value(s, SKOS.altLabel) == Literal("Churn-Rate", lang="de")
    assert str(graph.value(s, SKOS.definition)) == "Anteil gekündigter Mitglieder."
    d = next(s for s in graph.subjects(RDF.type, SKOS.Concept) if str(s).endswith(old))
    assert graph.value(d, OWL.deprecated).toPython() is True
    code, out, _ = cli("glossary", "export", "--format", "json")
    assert {t["term_id"]: t["status"] for t in json.loads(out)} == {ok: "approved", new: "approved",
                                                                    old: "deprecated"}


def test_coverage_reported(g, cli):
    _two_tables(g)
    p = g["tmp"] / "more.yaml"
    p.write_text(dump_yaml({"tables": [{"table": "manual://crm/invoices", "columns": [
        {"name": "kd_nr", "description": "Kundennummer", "type": "text", "nullable": False},
        {"name": "x", "type": "text", "nullable": True}]}]}), encoding="utf-8")
    est.load_schema(g["cat"], g["sc"], p, "ben")
    approved(g, "Umsatz")
    term(g["cat"], g["sc"], "Entwurf")
    code, out, err = cli("glossary", "report")
    rep = json.loads(out)
    assert (rep["columns"], rep["documented"], rep["linked"], rep["coverage"]) == (4, 3, 2, 0.75)
    assert rep["status_counts"] == {"approved": 1, "draft": 1} and len(rep["conflicts"]) == 1


# ---------------------------------------------------------------- 4.8 revert and deprecation

def test_revert_to_previous_version(g, cli):
    cat, sc = g["cat"], g["sc"]
    tid = approved(g, "Ehrenamt", definition="Version eins.")
    gl.edit(cat, sc, tid, {"definition": "Version zwei."}, "ben")
    gl.decide(cat, sc, tid, "clara", True)
    code, out, err = cli("glossary", "revert", tid, "--to", 1, "--reason", "v2 war falsch", "--as", "clara")
    assert code == 0, err
    t = gl.term(cat, tid)
    assert t["exposed_version"] == 1 and gl.exposed(cat)[0]["content"]["definition"] == "Version eins."
    assert t["versions"] == [1, 2]
    ev = cat.query("SELECT * FROM audit_event WHERE action = 'glossary.revert'")[-1]
    d = json.loads(ev["details"])
    assert (d["previous"], d["new"], d["reason"], ev["actor"]) == (2, 1, "v2 war falsch", "clara")


def test_deprecated_term_stays_resolvable(g, cli):
    cat, sc = g["cat"], g["sc"]
    new = approved(g, "Mitgliedsbeitrag")
    old = approved(g, "Jahresbeitrag")
    with pytest.raises(Refused, match="unknown term"):
        gl.deprecate(cat, sc, old, "clara", "gibt-es-nicht", "x")
    gl.deprecate(cat, sc, old, "clara", new, "zusammengelegt")
    code, out, _ = cli("glossary", "show", old)
    shown = json.loads(out)
    assert shown["status"] == "deprecated" and shown["replaced_by"] == new
    assert gl.resolve_approved(cat, "Jahresbeitrag") is None  # no new links to a deprecated term
