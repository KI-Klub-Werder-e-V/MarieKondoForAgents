"""c04 dedup-detection: scenarios from openspec/changes/c04-discard-pass/specs/dedup-detection/spec.md.
All documents, people and identifiers are synthetic."""

import csv
import importlib.util
import json
import random
import re
from pathlib import Path

import pytest

from konmari.catalog import AppendOnlyError, connect
from konmari.discard import calibrate as dcal
from konmari.discard import corpus, decisions as dd
from konmari.discard import detect as ddet
from konmari.discard import report as drep

from conftest import dump_catalog

CID = "verein-faq-assistant"
GOLDEN = Path(__file__).parent / "fixtures" / "discard" / "report.golden.md"
SYL = ["ka", "lo", "mi", "ne", "ru", "ta", "be", "so", "fi", "du", "ge", "pa", "wo", "hi", "za"]
VOCAB = sorted({a + b + c for a in SYL for b in SYL for c in SYL})


def words(seed, n=200) -> list[str]:
    rnd = random.Random(seed)
    return [rnd.choice(VOCAB) for _ in range(n)]


def edit(ws, k, seed=0) -> list[str]:
    """Replace k words, spaced so that each change touches its own 5 shingles."""
    out = list(ws)
    for i in range(k):
        out[10 + i * 12] = f"qq{VOCAB[(seed * 31 + i) % len(VOCAB)]}"
    return out


def jaccard(a, b, k=5):
    sa, sb = ddet.shingles(" ".join(a), k), ddet.shingles(" ".join(b), k)
    return len(sa & sb) / len(sa | sb)


def run(dp, entries, **cfg):
    dp["items"](entries)
    return ddet.detect(dp["cat"], actor="dirk", cfg=cfg or None, manifest=dp["manifest"])


def flags(cat, **where):
    rows = cat.query("SELECT * FROM flag ORDER BY created_at, flag_id")
    return [r for r in rows if all(r[k] == v for k, v in where.items())]


def kb(name, text, **meta):
    return {"asset_uri": "manual://kb/sops", "name": name, "text": text, **meta}


# ---------------------------------------------------------------- 1. schema, append-only, packaging

C04 = {"discard_run", "fingerprint", "flag", "cluster", "calibration_label", "proposal", "archive_manifest",
       "retention_rule", "legal_hold", "erasure_record", "corpus_item", "candidate_pair", "calibration",
       "derived_artifact", "retention_check_run"}


def _tables(cat):
    return {r["table_name"] for r in cat.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')")}


def _up_down_up(cat):
    assert C04 | {"decision_event", "kept_item"} <= _tables(cat)
    cat.rollback("004_c04_discard_pass.sql")
    assert not C04 & _tables(cat) and "verdict" in _tables(cat)
    cat.migrate()
    assert C04 <= _tables(cat)


def test_migration_up_down_up_duckdb(tmp_path):
    _up_down_up(connect(f"duckdb:///{tmp_path}/c.duckdb"))


@pytest.mark.postgres
def test_migration_up_down_up_postgres(pg_url):
    _up_down_up(connect(pg_url))


def test_decision_event_update_and_delete_denied_duckdb(tmp_path):
    from konmari.catalog import audit

    cat = connect(f"duckdb:///{tmp_path}/c.duckdb")
    audit(cat, "discard.test", actor="t", step="discard")
    for sql in ("UPDATE decision_event SET actor = 'x'", "DELETE FROM decision_event"):
        with pytest.raises(AppendOnlyError):
            cat.execute(sql)
        with pytest.raises(Exception):  # the view itself is not updatable in DuckDB
            cat.conn.execute(sql)
    assert cat.one("SELECT actor FROM decision_event")["actor"] == "t"


@pytest.mark.postgres
def test_decision_event_update_and_delete_denied_postgres(pg_url):
    import psycopg

    from konmari.catalog import audit

    cat = connect(pg_url)
    audit(cat, "discard.test", actor="t", step="discard")
    for sql in ("UPDATE decision_event SET actor = 'x'", "DELETE FROM decision_event"):
        with pytest.raises(psycopg.errors.RaiseException):  # trigger on audit_event, reached through the view
            cat.conn.execute(sql)
    priv = cat.one("SELECT has_table_privilege('konmari_app', 'decision_event', 'INSERT') AS i,"
                   " has_table_privilege('konmari_app', 'decision_event', 'UPDATE') AS u,"
                   " has_table_privilege('konmari_app', 'audit_event', 'DELETE') AS d")
    assert priv == {"i": True, "u": False, "d": False}


def test_core_install_works_without_semantic_extra():
    assert importlib.util.find_spec("datasketch") is not None
    assert ddet.DEFAULTS["semantic"] is False  # nothing imports sentence-transformers unless enabled
    import sys
    assert "sentence_transformers" not in sys.modules


# ---------------------------------------------------------------- 2. pilot scoping and exact duplicates

def _corpus_1200(dp, duplicates=180):
    """1,200 inventoried items (duplicates of them are exact copies) plus 38,800 manifest entries of a
    SharePoint site that is not in the inventory."""
    entries = []
    for i in range(1200 - duplicates):
        entries.append({"asset_uri": "manual://kb/sops" if i % 2 else "manual://support/tickets",
                        "name": f"doc{i:04d}.txt", "text": " ".join(words(i, 40))})
    for i in range(duplicates):
        entries.append({"asset_uri": "manual://kb/sops", "name": f"copy{i:04d}.txt",
                        "text": " ".join(words(i, 40))})
    outside = [{"asset_uri": "sharepoint://contoso.sharepoint.com/sites/support/all", "path": f"/nowhere/f{i}.docx"}
               for i in range(38_800)]
    dp["items"](entries, outside)
    return ddet.detect(dp["cat"], actor="dirk", manifest=dp["manifest"])


def test_run_limited_to_pilot_inventory(dp):
    rep = _corpus_1200(dp)
    cat = dp["cat"]
    assert cat.one("SELECT COUNT(*) AS n FROM fingerprint")["n"] == 1200
    assert rep["outside_inventory"] == 38_800
    r = cat.one("SELECT * FROM discard_run")
    assert r["charter_id"] == CID and r["charter_version"] == 1 and r["inventory_snapshot"].startswith("inv-")
    assert r["finished_at"] >= r["started_at"] and json.loads(r["config"])["jaccard"] == 0.8


def test_run_report(dp):
    rep = _corpus_1200(dp)
    assert rep["duplicate_rate"] == 0.15 and rep["non_canonical_items"] == 180
    md = drep.render_md(rep)
    assert "**Duplicate rate: 15.0%**" in md
    assert md.replace(rep["run_id"], "<run>") == GOLDEN.read_text(encoding="utf-8")
    md_file, js_file = drep.write(rep, dp["tmp"] / "reports")
    assert json.loads(js_file.read_text())["duplicate_rate"] == 0.15 and md_file.read_text() == md
    stored = drep.latest(dp["cat"], CID)
    assert stored["calibration"]["minhash"].startswith("uncalibrated") and stored["flags_by_method"]["exact"] == 180


def test_no_approved_charter(engagement, cli, tmp_path):
    Path("items.jsonl").write_text("")
    code, out, err = cli("discard", "detect", "--items", "items.jsonl")
    assert code == 1 and "no approved pilot charter" in err and "charter" in err
    cat = connect()
    assert cat.one("SELECT COUNT(*) AS n FROM fingerprint")["n"] == 0
    assert cat.one("SELECT COUNT(*) AS n FROM discard_run")["n"] == 0


def test_normalization_v1():
    assert corpus.normalize("Satzung  des\r\n\tVereins Ä") == "Satzung des Vereins Ä"
    assert corpus.normalize("Groß") != corpus.normalize("groß")  # case preserved
    assert corpus.NORM_VERSION == "v1-nfc-whitespace"


def test_whitespace_only_difference(dp):
    run(dp, [kb("a.txt", "Beitragsordnung\n\nDer Beitrag   ist fällig."),
             kb("b.txt", "Beitragsordnung Der Beitrag ist fällig.\n")])
    cat = dp["cat"]
    fp = cat.query("SELECT * FROM fingerprint ORDER BY item_id")
    assert fp[0]["text_sha256"] == fp[1]["text_sha256"] and fp[0]["raw_sha256"] != fp[1]["raw_sha256"]
    assert {f["norm_version"] for f in fp} == {corpus.NORM_VERSION}
    c = cat.one("SELECT * FROM cluster WHERE family = 'exact'")
    assert len(json.loads(c["members"])) == 2


def test_same_document_in_two_systems(dp):
    pdf = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\nTrainingszeiten Halle 2\n%%EOF\n"
    run(dp, [{"asset_uri": "manual://kb/sops", "name": "Trainingszeiten.pdf", "text": pdf, "modified": "2025-01-01"},
             {"asset_uri": "manual://support/tickets", "name": "Anhang_Halle2.pdf", "text": pdf,
              "modified": "2026-02-03"}])
    cat = dp["cat"]
    [f] = flags(cat, flag_type="duplicate")
    assert f["method"] == "exact" and f["score"] == 1.0
    c = cat.one("SELECT * FROM cluster WHERE cluster_id = ?", (f["cluster_id"],))
    assert sorted(json.loads(c["members"])) == ["manual://kb/sops/Trainingszeiten.pdf",
                                               "manual://support/tickets/Anhang_Halle2.pdf"]


# ---------------------------------------------------------------- 3. near-duplicate, containment, semantic

def test_near_duplicate_flagged_after_verification(dp):
    base = words(1)
    v2 = edit(base, 3)
    assert round(jaccard(base, v2), 2) == 0.86
    run(dp, [kb("sop_v1.txt", " ".join(base)), kb("sop_v2.txt", " ".join(v2))])
    [f] = flags(dp["cat"], method="minhash")
    assert f["flag_type"] == "duplicate" and f["score"] == round(jaccard(base, v2), 4)
    assert json.loads(f["params"]) == {"jaccard_threshold": 0.8, "num_perm": 128, "shingle_words": 5}
    assert f["run_id"] and json.loads(f["related_ids"])


def test_lsh_false_positive_discarded(dp):
    base = words(2)
    other = edit(base, 9)
    assert 0.6 < jaccard(base, other) < 0.65
    rep = run(dp, [kb("a.txt", " ".join(base)), kb("b.txt", " ".join(other))])
    cat = dp["cat"]
    assert not flags(cat, method="minhash")
    cand = cat.one("SELECT * FROM candidate_pair WHERE detector = 'minhash'")
    assert 0.6 < cand["score"] < 0.65
    assert rep["rejected_candidates"] == 1


def test_document_embedded_in_a_larger_one(dp):
    faq = words(3, 100)
    manual = words(4, 600) + edit(faq, 1) + words(5, 700)
    run(dp, [kb("faq.txt", " ".join(faq)), kb("handbuch.txt", " ".join(manual))])
    [f] = flags(dp["cat"], method="containment")
    assert f["item_id"] == "manual://kb/sops/faq.txt" and f["score"] >= 0.9
    assert json.loads(f["signals"])["contained_item"] == "manual://kb/sops/faq.txt"
    assert json.loads(f["params"])["jaccard"] < 0.1
    assert not flags(dp["cat"], method="minhash")


class FakeEmbedder:
    """Deterministic stand-in for a multilingual model: fixed vectors per document (no download in tests)."""
    model_id = "fake-multilingual@1"

    def __init__(self, vectors):
        self.vectors = vectors

    def embed(self, texts):
        return [next(v for k, v in self.vectors.items() if k in t) for t in texts]


POLICY_DE = "Datenschutzrichtlinie: Mitgliederdaten werden nur für die Vereinsverwaltung genutzt."
POLICY_EN = "Privacy policy: member data is used only for club administration."


def _semantic(dp):
    emb = FakeEmbedder({"Datenschutz": [1.0, 0.0], "Privacy": [0.95, 0.3122], "Halle": [0.0, 1.0]})
    dp["items"]([kb("richtlinie_de.txt", POLICY_DE), kb("policy_en.txt", POLICY_EN),
                 kb("halle.txt", "Hallenplan Halle 2 montags")])
    return ddet.detect(dp["cat"], actor="dirk", cfg={"semantic": True}, manifest=dp["manifest"], embedder=emb)


def test_semantic_pair_flagged(dp):
    _semantic(dp)
    [f] = flags(dp["cat"], method="semantic")
    assert round(f["score"], 2) == 0.95
    assert json.loads(f["params"]) == {"cosine_threshold": 0.93, "model": "fake-multilingual@1"}
    run_cfg = json.loads(dp["cat"].one("SELECT config FROM discard_run")["config"])
    assert run_cfg["model"] == "fake-multilingual@1" and run_cfg["cosine"] == 0.93


def test_semantic_only_evidence_cannot_propose_erasure(dp):
    assert dd.cap("erase", {"semantic"}) == "archive" and dd.cap("exclude", {"semantic"}) == "archive"
    assert dd.cap("exclude", {"semantic", "category"}) == "exclude"
    _semantic(dp)
    cat = dp["cat"]
    from konmari.charter.store import scope
    sc = scope(CID, cat)
    dcal.override(cat, sc, "semantic", None, "pilot test", "ben")
    res = dd.propose(cat, sc, "dirk")
    assert [p["action"] for p in res["created"]] == ["archive"]
    assert not cat.query("SELECT * FROM proposal WHERE action IN ('erase', 'exclude')")


def test_evidence_has_no_text_excerpts(dp):
    letter = ("Sehr geehrte Frau Kunzmann, Ihre Mitgliedsnummer lautet Wolkenstein und Ihr Beitrag "
              + " ".join(words(7, 120)))
    letter2 = letter.replace("Kunzmann", "Kunzmannova")
    run(dp, [kb("brief1.txt", letter), kb("brief2.txt", letter2)])
    cat = dp["cat"]
    assert flags(cat, method="minhash")
    stored = "\n".join(repr(r) for t in ("flag", "candidate_pair", "cluster", "discard_run", "fingerprint")
                       for r in cat.query(f"SELECT * FROM {t}"))
    for w in ("Kunzmann", "Wolkenstein", "Mitgliedsnummer", words(7, 120)[5]):
        assert w not in stored


# ---------------------------------------------------------------- 4. calibration

def _calibration_corpus(dp):
    entries = []
    for i in range(30):
        base = words(100 + i)
        entries.append(kb(f"base{i}.txt", " ".join(base)))
        for k in (1, 4, 7):
            entries.append(kb(f"var{i}_{k}.txt", " ".join(edit(base, k, seed=i))))
    return run(dp, entries)


def test_calibration_report(dp, cli):
    _calibration_corpus(dp)
    code, out, err = cli("discard", "calibrate", "--detector", "minhash", "--sample", 60, "-o", "pairs.csv",
                         "--as", "clara")
    assert code == 0, err
    rows = list(csv.DictReader(open("pairs.csv", encoding="utf-8")))
    assert len(rows) == 60
    scores = sorted(float(r["score"]) for r in rows)
    assert scores[0] < 0.7 and scores[-1] > 0.9  # spread over the score range
    assert "<table" in Path("pairs.html").read_text()  # side-by-side diff view
    for r in rows:
        r["label"] = "duplicate" if float(r["score"]) >= 0.8 else "related-but-distinct"
    with open("pairs.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    code, out, err = cli("discard", "calibrate", "--detector", "minhash", "--labels", "pairs.csv",
                         "--threshold", 0.8, "--as", "clara")
    assert code == 0, err
    assert "60 label(s) stored" in out and "threshold 0.8 selected for minhash by clara" in out
    for t in ("0.70", "0.80", "0.90"):
        assert f"threshold {t}: precision" in out
    assert "threshold 0.80: precision 1.0, recall 1.0" in out
    c = dcal.status(dp["cat"], CID, "minhash")
    assert c["threshold"] == 0.8 and c["decided_by"] == "clara" and c["labels"] == 60


def test_uncalibrated_detector_blocked(dp, cli):
    _semantic(dp)
    code, out, err = cli("discard", "propose", "--as", "dirk")
    assert code == 0, err
    assert "calibration missing for detector 'semantic'" in out and "0 proposal(s) created" in out
    assert not dp["cat"].query("SELECT * FROM proposal")


def test_calibration_needs_50_labels_or_override(dp, cli):
    _calibration_corpus(dp)
    code, _, err = cli("discard", "calibrate", "--detector", "minhash", "--threshold", 0.8, "--as", "clara")
    assert code == 1 and "at least 50 are needed" in err
    code, _, err = cli("discard", "calibrate", "--detector", "minhash", "--override-reason", "x", "--as", "finn")
    assert code == 1 and "only an approver may override" in err
    code, out, err = cli("discard", "calibrate", "--detector", "minhash", "--override-reason",
                         "expert reviewed 20 pairs by hand", "--as", "ben")
    assert code == 0, err
    code, out, err = cli("discard", "propose", "--as", "dirk")
    assert code == 0 and "calibration missing for detector 'minhash'" not in out
    assert dp["cat"].query("SELECT * FROM proposal WHERE method = 'minhash'")


# ---------------------------------------------------------------- 5. clusters, superseded, relevance, sensitive

def test_canonical_by_source_version_metadata(dp):
    text = "Hallenordnung " + " ".join(words(8, 80))
    run(dp, [kb("Hallenordnung_sp3.txt", text, version="3.0", modified="2024-01-01"),
             kb("Hallenordnung_sp41.txt", text, version="4.1", modified="2024-06-01"),
             {"asset_uri": "manual://support/tickets", "name": "Hallenordnung_copy.txt", "text": text,
              "modified": "2026-10-01"}])
    c = dp["cat"].one("SELECT * FROM cluster")
    assert c["canonical_item"] == "manual://kb/sops/Hallenordnung_sp41.txt"
    assert c["canonical_reason"] == "source-version"


def test_canonical_ranking_order_and_mtime_ignored():
    def m(i, length, **meta):
        return {"item_id": i, "_length": length, "meta": meta}
    assert ddet.canonical([m("a", 9, version="9"), m("b", 1, source_of_truth=True)]) == ("b", "source-of-truth")
    assert ddet.canonical([m("a", 9, version="9", effective_date="2024-01-01"),
                           m("b", 1, version="1", effective_date="2025-01-01")]) == ("b", "effective-date")
    assert ddet.canonical([m("a", 9, modified="2026-01-01"), m("b", 10, modified="2020-01-01")]) == \
        ("b", "completeness")


def test_superseded_by_version_history(dp):
    run(dp, [kb("Preisliste_2024-03.txt", "Preisliste Kurse " + " ".join(words(9, 60)), logical_id="preisliste",
                version="2024-03"),
             kb("Preisliste_2025-01.txt", "Preisliste Kurse " + " ".join(words(10, 60)), logical_id="preisliste",
                version="2025-01")])
    [f] = flags(dp["cat"], flag_type="superseded")
    assert f["item_id"] == "manual://kb/sops/Preisliste_2024-03.txt"
    assert json.loads(f["related_ids"]) == ["manual://kb/sops/Preisliste_2025-01.txt"]
    assert "version-history" in json.loads(f["signals"])["signals"]


def test_superseded_by_named_replacement_and_valid_until(dp):
    text = "Anmeldeformular " + " ".join(words(11, 80))
    run(dp, [kb("Anmeldung_alt.txt", "Dieses Formular wird ersetzt durch Anmeldung_2026.txt\n" + " ".join(words(12))),
             kb("Anmeldung_2026.txt", " ".join(words(13))),
             kb("Formular_a.txt", text, valid_until="2025-12-31", effective_date="2025-01-01"),
             kb("Formular_b.txt", text, effective_date="2026-01-01"),
             kb("Hallenplan_alt.txt", "Hallenplan gültig bis 31.7.2025\n" + " ".join(words(16, 80)),
                effective_date="2025-01-01"),
             kb("Hallenplan_neu.txt", "Hallenplan gültig bis 31.7.2025\n" + " ".join(words(16, 80)) + " neu",
                effective_date="2025-08-01")])
    sup = {f["item_id"]: json.loads(f["signals"])["signals"] for f in flags(dp["cat"], flag_type="superseded")}
    assert "named-replacement" in sup["manual://kb/sops/Anmeldung_alt.txt"]
    assert "valid-until-passed" in sup["manual://kb/sops/Formular_a.txt"]
    assert "valid-until-passed" in sup["manual://kb/sops/Hallenplan_alt.txt"]  # date taken from the text
    assert "manual://kb/sops/Hallenplan_neu.txt" not in sup


def test_filename_alone_is_insufficient(dp):
    run(dp, [kb("Angebot_final.docx", " ".join(words(14))), kb("Angebot_final_v2.docx", " ".join(words(15)))])
    cat = dp["cat"]
    assert not flags(cat, flag_type="superseded")
    [f] = flags(cat, flag_type="review")
    assert json.loads(f["signals"])["signals"] == ["filename-only"]


def test_category_outside_pilot_scope(dp):
    run(dp, [{"asset_uri": "manual://finance/payroll", "name": "Lohnabrechnung_Okt.txt", "text": "Lohnlauf Oktober"},
             {"asset_uri": "manual://support/tickets", "name": "ticket1.txt", "text": "Frage zur Halle",
              "category": "financial-ops-metrics"}])
    off = {f["item_id"]: f for f in flags(dp["cat"], flag_type="off-scope")}
    assert set(off) == {"manual://finance/payroll/Lohnabrechnung_Okt.txt", "manual://support/tickets/ticket1.txt"}
    assert all(json.loads(f["signals"]) == {"reason": "category-not-in-charter"} for f in off.values())


def test_relevance_scores_never_flag_on_their_own(dp, cli):
    run(dp, [kb("a.txt", "Trainingszeiten")])
    assert not flags(dp["cat"], flag_type="off-scope")
    code, out, err = cli("discard", "mark-off-scope", "manual://kb/sops/a.txt", "--reason", "Jugendarchiv",
                         "--as", "clara")
    assert code == 0, err
    [f] = flags(dp["cat"], flag_type="off-scope")
    assert f["method"] == "reviewer" and f["created_by"] == "clara"


def test_personal_data_found_in_an_faq_export(dp, cli):
    text = "FAQ Export: Kontakt max.muster@example.org oder anna@example.net, Telefon 0331 1234567."
    rep = run(dp, [{"asset_uri": "manual://kb/faq-export", "name": "faq_export.txt", "text": text}])
    [f] = flags(dp["cat"], flag_type="sensitive-review")
    sig = json.loads(f["signals"])
    assert sig == {"entities": {"EMAIL_ADDRESS": 2, "PHONE_NUMBER": 1}, "tier": "public"}
    assert rep["inventory_mismatches"] == [{"item_id": "manual://kb/faq-export/faq_export.txt",
                                            "asset_uri": "manual://kb/faq-export", "inventory_tier": "public",
                                            "entities": {"EMAIL_ADDRESS": 2, "PHONE_NUMBER": 1}}]
    assert "max.muster@example.org" not in dump_catalog(dp["cat"]) and "1234567" not in dump_catalog(dp["cat"])
    code, out, err = cli("discard", "report")
    assert code == 0 and "inventory tier public, found EMAIL_ADDRESS x2, PHONE_NUMBER x1" in out


def test_sensitive_tier_flags_without_detector_hit(dp):
    run(dp, [{"asset_uri": "manual://policies/contracts", "name": "Mietvertrag.txt", "text": "Mietvertrag Halle"}])
    [f] = flags(dp["cat"], flag_type="sensitive-review")
    assert f["method"] == "inventory-tier" and json.loads(f["signals"])["tier"] == "confidential"
    assert not re.search(r"Mietvertrag Halle", repr(f))
