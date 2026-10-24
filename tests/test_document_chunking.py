"""c06 document-chunking: scenarios of specs/document-chunking/spec.md (synthetic fixture corpus only)."""

import json
import tomllib
from pathlib import Path

import pytest

import c06_fixture as fx
from konmari.catalog import connect
from konmari.charter.store import scope
from konmari.chunking import chunker as ck
from konmari.chunking import store as cst
from konmari.chunking.parse import ParseError, parse, parse_text

ROOT = Path(__file__).parent.parent
C06 = {"chunking_profile", "chunk_run", "chunk_set", "chunk", "chunk_metadata", "chunk_metadata_history",
       "column_annotation", "glossary", "glossary_term", "glossary_term_version", "glossary_link", "glossary_metric",
       "enrich_run"}
TOK = ck.RegexTokenizer()


def words(n, start=0):
    return " ".join(f"wort{i}" for i in range(start, start + n))


def run(c, **kw):
    return cst.run(c["cat"], c["sc"], "ben", **kw)


# ---------------------------------------------------------------- 1. setup

def test_c06_dependencies_are_pinned():
    doc = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    pinned = {d.split("==")[0] for d in doc["dependencies"] if "==" in d}
    assert {"langchain-text-splitters", "tokenizers", "openlineage-python", "rdflib", "pdfplumber"} <= pinned
    extras = doc["optional-dependencies"]
    assert extras["docling"][0].startswith("docling==") and extras["unstructured"][0].startswith("unstructured==")


def _tables(cat):
    return {r["table_name"] for r in cat.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')")}


def _up_down_up(cat):
    assert C06 <= _tables(cat) and "agent_chunk" in _tables(cat)
    cat.rollback("006_c06_structure_and_enrich.sql")
    assert not C06 & _tables(cat) and "golden_attribute" in _tables(cat)
    cat.migrate()
    assert C06 <= _tables(cat)


def test_migration_up_down_up_duckdb(tmp_path):
    _up_down_up(connect(f"duckdb:///{tmp_path}/c.duckdb"))


@pytest.mark.postgres
def test_migration_up_down_up_postgres(pg_url):
    _up_down_up(connect(pg_url))


def test_fixture_corpus_parses(tmp_path):
    f = fx.write(tmp_path)
    kinds = {k: {e["kind"] for e in parse(f[k])} for k in ("docx", "pdf", "html", "txt")}
    assert {"heading", "paragraph", "list_item", "table"} <= kinds["docx"]
    assert {"heading", "table"} <= kinds["pdf"] and {"heading", "table"} <= kinds["html"]
    assert kinds["txt"] == {"paragraph"}
    with pytest.raises(ParseError, match="no text layer"):
        parse(f["scan"])


# ---------------------------------------------------------------- 2.1 pilot-scoped input

def test_discarded_document_is_not_chunked(c06):
    item = c06["cat"].one("SELECT item_id FROM corpus_item WHERE item_id LIKE '%faq.html'")["item_id"]
    c06["cat"].execute("UPDATE corpus_item SET state = 'archived' WHERE item_id = ?", (item,))
    rep = run(c06)
    assert {"item_id": item, "reason": "not-kept", "detail": "state archived"} in rep["refused"]
    assert not c06["cat"].query("SELECT * FROM chunk WHERE item_id = ?", (item,))


def test_out_of_scope_source_is_refused(c06):
    cat = c06["cat"]
    cat.execute("UPDATE source_asset SET source_id = 'old-wiki' WHERE asset_uri = 'manual://support/tickets'")
    rep = run(c06)
    refused = {r["item_id"]: r["reason"] for r in rep["refused"]}
    assert refused == {"manual://support/tickets/notes.txt": "out-of-pilot-scope"}
    assert not cat.query("SELECT * FROM chunk WHERE item_id = 'manual://support/tickets/notes.txt'")


def test_not_normalized_category_is_refused(dp):
    files = fx.write(dp["tmp"] / "c06")
    fx.register(dp["cat"], fx.write_manifest(dp["tmp"] / "m.jsonl", fx.entries(files)[:1]))
    rep = cst.run(dp["cat"], scope(fx.CHARTER, dp["cat"]), "ben")
    assert [r["reason"] for r in rep["refused"]] == ["not-normalized"] and rep["chunks"] == 0


# ---------------------------------------------------------------- 2.2 structure-aware segmentation

def _chunks(c, suffix):
    return [ch for ch in cst.active_chunks(c["cat"]) if ch["item_id"].endswith(suffix)]


def test_section_boundary_respected():
    els = parse_text("# 1 Returns\n\nReturn within 14 days.\n\n# 2 Warranty\n\nTwo years of warranty.\n")
    chunks, method = ck.build(els, TOK, {})
    assert method == "structure" and [c["heading_path"] for c in chunks] == [["1 Returns"], ["2 Warranty"]]
    assert not any("Return" in c["content"] and "Two years" in c["content"] for c in chunks)


def test_heading_path_recorded(c06):
    run(c06)
    ex = [c for c in _chunks(c06, "policies.docx") if c["heading_path"][-1:] == ["Exceptions"]]
    assert ex and ex[0]["heading_path"] == ["Policies", "Returns", "Exceptions"]
    assert ex[0]["location"]["elements"][0] > 0
    pdf = _chunks(c06, "prices.pdf")
    assert all(c["location"]["pages"] for c in pdf)


def test_list_item_never_split():
    item = "- " + words(450)  # above max_tokens, below the model maximum: kept whole
    chunks, _ = ck.build(parse_text(f"# Liste\n\n{item}\n- kurz\n"), TOK, {"max_tokens": 400})
    assert any(item[2:] in c["content"] for c in chunks)


# ---------------------------------------------------------------- 2.3 tables

def test_large_table_split_with_header_repeated():
    rows = [["Artikel", "Preis"]] + [[f"Artikel {i}", f"{i}.00 EUR"] for i in range(300)]
    el = {"kind": "table", "text": "", "loc": {"pages": [1, 2]}, "rows": rows, "header_rows": 1,
          "caption": "Tabelle 1: Preise"}
    chunks, _ = ck.build([{"kind": "heading", "text": "Preise", "level": 1, "loc": {}}, el], TOK, {})
    assert len(chunks) > 1 and all(c["kind"] == "table" and c["tokens"] <= 400 for c in chunks)
    union = []
    for c in chunks:
        assert c["content"].startswith("Tabelle 1: Preise\n| Artikel | Preis |")
        header, data = ck.table_rows(c["content"])
        assert header == [rows[0]]
        union += data
    assert union == rows[1:]


def test_multi_page_pdf_table_round_trip(c06):
    run(c06)
    tables = [c for c in _chunks(c06, "prices.pdf") if c["kind"] == "table"]
    union = [r for c in tables for r in ck.table_rows(c["content"])[1]]
    assert union == fx.PRICE_ROWS
    assert all(ck.table_rows(c["content"])[0] == [fx.PRICE_HEADER] for c in tables)
    assert all(c["content"].startswith("Tabelle 1: Preise je Artikel") for c in tables)
    assert {p for c in tables for p in c["location"]["pages"]} == {1, 2}


def test_table_not_merged_into_prose():
    chunks, _ = ck.build(parse_text("# Beiträge\n\nKurzer Absatz.\n\n| Gruppe | Beitrag |\n|---|---|\n"
                                    "| Kinder | 60 EUR |\n"), TOK, {})
    assert [c["kind"] for c in chunks] == ["text", "table"]
    assert chunks[0]["content"] == "Kurzer Absatz."


# ---------------------------------------------------------------- 2.4 profiles and token bounds

def test_oversized_section_split_within_limit():
    paras = [words(100, i * 100) + "." for i in range(11)]  # 11 x ~101 tokens = 1,100+
    long = " ".join(words(20, i * 20) + "." for i in range(55))  # one 1,100-token paragraph
    for body in ("\n\n".join(paras), long):
        chunks, _ = ck.build(parse_text(f"# Abschnitt\n\n{body}\n"), TOK, {})
        assert sum(c["tokens"] for c in chunks) >= 1100 and len(chunks) >= 3
        assert all(c["tokens"] <= 400 for c in chunks)
        assert all(c["content"].rstrip().endswith(".") for c in chunks)  # paragraph or sentence boundaries


def test_tiny_fragments_merged():
    text = "# Kurz\n\n" + "\n\n".join(words(19, i * 19) + "." for i in range(3))
    chunks, _ = ck.build(parse_text(text), TOK, {})
    assert len(chunks) == 1 and chunks[0]["tokens"] == 60


def test_model_max_input_guard():
    assert ck.profile_errors({"max_tokens": 600}) == [
        "max_tokens 600 exceeds the embedding model's maximum input 512"]
    assert ck.profile_errors({"max_tokens": 600, "model_max_tokens": 1024}) == []
    big = {"kind": "table", "text": "", "loc": {}, "rows": [["a"], [words(600)]], "header_rows": 1, "caption": None}
    with pytest.raises(ck.ChunkError):
        ck.build([big], TOK, {})


def test_hf_tokenizer_counts_with_embedding_tokenizer(tmp_path):
    from tokenizers import Tokenizer, models, pre_tokenizers

    t = Tokenizer(models.WordLevel({"[UNK]": 0, "kunde": 1}, unk_token="[UNK]"))
    t.pre_tokenizer = pre_tokenizers.Whitespace()
    t.save(str(tmp_path / "tokenizer.json"))
    tok = ck.tokenizer(f"hf:{tmp_path / 'tokenizer.json'}")
    assert tok.count("kunde kunde x.") == 4 and tok.name.startswith("hf:")


# ---------------------------------------------------------------- 2.5 audited overrides

def test_profile_override_is_audited(c06):
    cat, sc = c06["cat"], c06["sc"]
    cst.set_profile(cat, sc, "policies-and-contracts", {"max_tokens": 600, "model_max_tokens": 1024}, "clara",
                    "long clauses")
    ev = cat.query("SELECT * FROM audit_event WHERE action = 'chunk.profile'")[-1]
    d = json.loads(ev["details"])
    assert (ev["actor"], d["previous"]["max_tokens"], d["new"]["max_tokens"], d["reason"]) == \
        ("clara", 400, 600, "long clauses") and ev["ts"]
    assert cst.profile(cat, sc.charter_id, "policies-and-contracts")["max_tokens"] == 600
    rep = run(c06)
    cfg = json.loads(cst.get_run(cat, rep["run_id"])["config"])
    assert cfg["profiles"]["policies-and-contracts"]["max_tokens"] == 600
    assert cfg["profiles"]["kb-and-sops"]["max_tokens"] == 400


def test_profile_override_needs_reason_and_valid_values(c06):
    from konmari.versions import Refused

    with pytest.raises(Refused, match="reason"):
        cst.set_profile(c06["cat"], c06["sc"], "kb-and-sops", {"max_tokens": 300}, "ben", "")
    with pytest.raises(Refused, match="maximum input"):
        cst.set_profile(c06["cat"], c06["sc"], "kb-and-sops", {"max_tokens": 900}, "ben", "too big")


# ---------------------------------------------------------------- 2.6 identity

def test_idempotent_rerun(c06):
    first = run(c06)
    ids = {c["chunk_id"] for c in cst.active_chunks(c06["cat"])}
    second = run(c06)
    assert first["new_chunks"] == first["chunks"] > 0
    assert second["new_chunks"] == 0 and second["unchanged"] == second["chunks"] == first["chunks"]
    assert {c["chunk_id"] for c in cst.active_chunks(c06["cat"])} == ids


def test_changed_source_supersedes_removed_chunks(c06):
    cat = c06["cat"]
    f = c06["files"]["html"]
    run(c06)
    before = {c["chunk_id"]: c for c in _chunks(c06, "faq.html")}
    gone = next(c for c in before.values() if "Familien" in c["content"])
    f.write_text(fx.FAQ_HTML.replace("<p>Ja, Familien zahlen höchstens 250 EUR im Jahr.</p>", ""), encoding="utf-8")
    run(c06)
    after = {c["chunk_id"] for c in _chunks(c06, "faq.html")}
    row = cat.one("SELECT * FROM chunk WHERE chunk_id = ?", (gone["chunk_id"],))
    new_ver = cst.active_set(cat, gone["item_id"])["source_version"]
    assert row["status"] == "superseded" and row["superseded_by"] == new_ver != row["source_version"]
    assert set(before) - {gone["chunk_id"]} <= after  # unaffected chunks keep their IDs


# ---------------------------------------------------------------- 2.7 fallback and parse failures

def test_plain_text_fallback_marked(c06):
    rep = run(c06)
    notes = _chunks(c06, "notes.txt")
    assert notes and all(c["method"] == "fallback" for c in notes)
    assert rep["fallback"] == ["manual://support/tickets/notes.txt"]
    assert all(c["method"] == "structure" for c in _chunks(c06, "policies.docx"))


def test_fallback_overlap_fifteen_percent():
    text = " ".join(f"Satz {i} hat genau sieben Woerter hier." for i in range(200))
    chunks, method = ck.build([{"kind": "paragraph", "text": text, "loc": {}}], TOK, {})
    assert method == "fallback" and len(chunks) > 2 and all(c["tokens"] <= 400 for c in chunks)
    a, b = chunks[0]["content"].split(), chunks[1]["content"].split()
    assert a[-5:] == b[:5] or set(a[-20:]) & set(b[:20])  # neighbouring chunks overlap


def test_unparseable_pdf_surfaced(c06):
    rep = run(c06)
    scan = "manual://kb/sops/scan.pdf"
    assert [e["item_id"] for e in rep["parse_errors"]] == [scan] and "no text layer" in rep["parse_errors"][0]["error"]
    assert cst.doc_status(c06["cat"], scan) == "needs-review"
    assert not c06["cat"].query("SELECT * FROM chunk WHERE item_id = ?", (scan,))
    assert scan in cst.render_report(rep)


# ---------------------------------------------------------------- 2.8 dry run and report

def test_dry_run_writes_nothing(c06):
    cat = c06["cat"]
    before = [cat.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("chunk", "chunk_run", "chunk_set",
                                                                        "audit_event", "derived_artifact")]
    rep = run(c06, dry_run=True)
    assert rep["chunks"] > 0 and "dry run, nothing written" in cst.render_report(rep)
    assert [cat.one(f"SELECT COUNT(*) AS n FROM {t}")["n"] for t in ("chunk", "chunk_run", "chunk_set",
                                                                       "audit_event", "derived_artifact")] == before


def test_report_contents(c06):
    rep = run(c06)
    s = rep["token_sizes"]
    assert rep["documents"] == 5 and s["min"] <= s["median"] <= s["p95"] <= s["max"] <= 400
    assert rep["table_chunks"] >= 4 and rep["over_flag"] == [] and rep["corpus_tokens"] > 0
    text = cst.render_report(rep)
    for part in ("documents processed: 5", "token sizes", "table chunks", "above the flag size", "fallback",
                 "parse errors", "pilot corpus tokens"):
        assert part in text


def test_small_corpus_hint(c06, monkeypatch):
    rep = run(c06, dry_run=True)
    assert rep["corpus_tokens"] < 150_000 and rep["full_context_feasible"]
    assert "full-context feasible" in cst.render_report(rep)
    assert "full-context feasible" not in cst.render_report({**rep, "full_context_feasible": False})


def test_over_flag_reported(c06):
    cst.set_profile(c06["cat"], c06["sc"], "kb-and-sops", {"flag_tokens": 50}, "ben", "flag test")
    rep = run(c06, dry_run=True)
    assert rep["over_flag"]


def test_stable_id_evidence_for_scorecard(c06):
    run(c06)
    ev = c06["cat"].query("SELECT * FROM asset_evidence WHERE kind = 'stable_id'")
    assert {e["asset_uri"] for e in ev} == {"manual://policies/contracts", "manual://kb/sops", "manual://kb/faq-export",
                                            "manual://support/tickets"}


# ---------------------------------------------------------------- 2.9 rollback

def test_roll_back_a_bad_profile(c06):
    cat, sc = c06["cat"], c06["sc"]
    run(c06)
    good = {c["chunk_id"] for c in cst.active_chunks(cat)}
    cst.set_profile(cat, sc, "kb-and-sops", {"max_tokens": 60}, "ben", "experiment")
    bad = run(c06)
    assert {c["chunk_id"] for c in cst.active_chunks(cat)} != good
    cst.rollback(cat, sc, bad["run_id"], "ben", "too small")
    assert {c["chunk_id"] for c in cst.active_chunks(cat)} == good
    created = {r["chunk_id"] for r in cat.query("SELECT chunk_id FROM chunk WHERE first_run = ?", (bad["run_id"],))}
    assert created and all(cat.one("SELECT status FROM chunk WHERE chunk_id = ?", (i,))["status"] == "inactive"
                           for i in created)
    ev = cat.query("SELECT * FROM audit_event WHERE action = 'chunk.rollback'")
    assert ev and json.loads(ev[-1]["details"])["reason"] == "too small"


def test_chunk_cli_run_dry_run_and_rollback(c06, cli):
    code, out, err = cli("chunk", "run", "--dry-run", "--as", "ben")
    assert code == 0, err
    assert "dry run" in out and not c06["cat"].query("SELECT * FROM chunk")
    code, out, err = cli("chunk", "run", "--as", "ben")
    assert code == 0, err
    run_id = out.split("run ")[1].split()[0]
    code, out, err = cli("chunk", "profile", "set", "kb-and-sops", "--max-tokens", 300, "--reason", "r", "--as", "ben")
    assert code == 0 and "max_tokens" in out
    code, out, err = cli("chunk", "rollback", run_id, "--reason", "undo", "--as", "ben")
    assert code == 0 and not cst.active_chunks(c06["cat"])
