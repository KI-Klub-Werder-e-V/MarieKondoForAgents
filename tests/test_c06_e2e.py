"""c06 end to end through the CLI on the fixture corpus: chunk -> enrich -> glossary approve -> export, plus the
no-network check for the default profile and the same pipeline on Postgres. Synthetic data only."""

import json
import socket

import pytest
from rdflib import Graph

from konmari.canonical import dump_yaml
from konmari.catalog import verify_chain
from konmari.chunking import store as cst
from konmari.enrich import store as est
from konmari.glossary import store as gl
from konmari.inventory.lineage import MemoryTransport

CRM = "manual://crm/invoices"


def _schema(tmp):
    p = tmp / "schema.yaml"
    p.write_text(dump_yaml({"tables": [{"table": CRM, "created_at": "2024-05-01T00:00:00Z", "language": "de",
                                        "modified_at": "2026-03-01T10:00:00Z",
                                        "acl": {"allow": ["group:Geschäftsstelle"]}, "columns": [
        {"name": "kd_nr", "description": "Kundennummer", "type": "text", "nullable": False},
        {"name": "kd_stat", "type": "text", "nullable": False, "term": "Kundenstatus"},
        {"name": "umsatz", "description": "Jahresumsatz", "type": "decimal", "unit": "EUR", "nullable": True}]}]}),
                 encoding="utf-8")
    return p


def _term(tmp, name, doc):
    p = tmp / f"{name}.yaml"
    p.write_text(dump_yaml(doc), encoding="utf-8")
    return p


def pipeline(c06, cli):
    def ok(*args):
        code, out, err = cli(*args)
        assert code == 0, (args, err)
        return out

    tmp = c06["tmp"]
    ok("chunk", "run", "--dry-run", "--as", "ben")
    ok("chunk", "run", "--as", "ben")
    ok("enrich", "schema", "load", _schema(tmp), "--as", "ben")
    ok("glossary", "stewards", "--steward", "clara", "--steward", "ben", "--as", "anna")
    ok("glossary", "add", "--file", _term(tmp, "status", {"labels": {"pref": {"de": "Kundenstatus",
                                                                              "en": "customer status"}},
                                                          "definition": "Ob ein Kunde aktiv betreut wird.",
                                                          "owner": "ben", "category": "customer-records"}),
       "--as", "ben")
    ok("glossary", "add", "--file", _term(tmp, "churn", {"labels": {"pref": {"de": "Kündigungsquote"},
                                                                    "alt": {"de": ["Churn-Rate"]}},
                                                         "definition": "Anteil der Mitglieder, die kündigen.",
                                                         "owner": "ben"}), "--as", "ben")
    for t in ("kundenstatus", "kuendigungsquote"):
        ok("glossary", "submit", t, "--as", "ben")
        ok("glossary", "approve", t, "--comment", "geprüft", "--as", "clara")
    ok("glossary", "harvest", "--top", 5, "--as", "ben")
    out = ok("enrich", "run", "--as", "ben")
    assert "Enrichment report" in out
    ok("glossary", "export", "--format", "skos", "-o", tmp / "glossary.ttl")
    ok("glossary", "export", "--format", "json", "-o", tmp / "glossary.json")
    return ok


def _assert_outcome(c06):
    cat = c06["cat"]
    statuses = {}
    for item in cat.query("SELECT item_id FROM corpus_item ORDER BY item_id"):
        iid = item["item_id"]
        doc = cst.doc_status(cat, iid)
        rows = cat.query("SELECT status, reasons FROM chunk_metadata WHERE item_id = ?", (iid,))
        statuses[iid] = doc if doc == "needs-review" else {r["status"] for r in rows}
        for r in rows:
            assert r["status"] == "ready-for-agents" or json.loads(r["reasons"])
    assert statuses == {"manual://kb/faq-export/faq.html": {"ready-for-agents"},
                        "manual://kb/sops/prices.pdf": {"ready-for-agents"},
                        "manual://kb/sops/scan.pdf": "needs-review",
                        "manual://policies/contracts/policies.docx": {"ready-for-agents"},
                        "manual://support/tickets/notes.txt": {"ready-for-agents"}}
    assert est.metadata(cat, CRM)["status"] == "ready-for-agents"
    notes = [c for c in cst.active_chunks(cat) if c["item_id"].endswith("notes.txt")]
    assert "kuendigungsquote" in est.metadata(cat, notes[0]["chunk_id"])["glossary_terms"]
    actions = {r["action"] for r in cat.query("SELECT action FROM audit_event WHERE step = 'enrich'")}
    assert {"chunk.run", "enrich.column", "enrich.override", "glossary.stewards", "glossary.add", "glossary.submit",
            "glossary.approve", "enrich.run"} <= actions
    assert verify_chain(cat) is None


def test_end_to_end_chunk_enrich_glossary_export(c06, cli):
    pipeline(c06, cli)
    _assert_outcome(c06)
    graph = Graph().parse(c06["tmp"] / "glossary.ttl", format="turtle")
    assert len(set(graph.subjects())) == 3  # scheme + two approved terms; harvested drafts stay out
    exported = json.loads((c06["tmp"] / "glossary.json").read_text())
    assert [t["term_id"] for t in exported] == ["kuendigungsquote", "kundenstatus"]
    last = c06["lineage"].read_text(encoding="utf-8").splitlines()[-1]
    assert len(json.loads(last)["outputs"]) == 5  # four chunked documents and the CRM table


def test_no_network_in_default_profile(c06, cli, monkeypatch):
    def blocked(*a, **k):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    pipeline(c06, cli)
    _assert_outcome(c06)


@pytest.mark.postgres
def test_pipeline_on_postgres(pg_url, c06):
    cat, sc = c06["cat"], c06["sc"]
    assert cat.dialect == "postgres"
    rep = cst.run(cat, sc, "ben")
    assert cst.run(cat, sc, "ben")["new_chunks"] == 0 and rep["chunks"] > 0
    est.load_schema(cat, sc, _schema(c06["tmp"]), "ben")
    gl.set_stewards(cat, sc, ["clara"], "anna")
    tid = gl.add(cat, sc, {"labels": {"pref": {"de": "Kundenstatus"}}, "definition": "d", "owner": "ben"},
                 "ben")["term_id"]
    gl.submit(cat, sc, tid, "ben")
    gl.decide(cat, sc, tid, "clara", True)
    first = est.run(cat, sc, "ben", transport=MemoryTransport())
    assert first["ready"] == first["subjects"] and not first["needs_review"]
    est.override(cat, sc, "manual://kb/sops/prices.pdf", "owner", "clara", "ben", "pg test")
    second = est.run(cat, sc, "ben", transport=MemoryTransport())
    est.rollback(cat, sc, second["run_id"], "ben", "pg")
    assert {est.metadata(cat, c["chunk_id"])["owner"] for c in cst.active_chunks(cat, "manual://kb/sops/prices.pdf")} \
        == {"ben"}
    cst.rollback(cat, sc, rep["run_id"], "ben", "pg")
    assert "kundenstatus" in gl.export_skos(cat, sc.charter_id)
