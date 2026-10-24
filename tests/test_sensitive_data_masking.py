"""sensitive-data-masking (c07): DE/EN detection before indexing, classification, masking operators, key
separation and audited re-identification, recall gate, human review. Synthetic data only (no real persons)."""

import csv
import re

import pytest

from conftest import valid_kvnr
from gov_fixture import KB, SUPPORT, add_doc, agent, approve_all, cfg, events, govern, scope
from konmari.governance import access, acl, gate, pii
from konmari.governance import policy as pol
from konmari.versions import Refused

PETRA = "Frau Petra Schulz, petra.schulz@example.de, IBAN DE89 3704 0044 0532 0130 00"


def _rows(cat, item):
    return cat.query("SELECT * FROM agent_index WHERE item_id = ? ORDER BY chunk_id", (item,))


def test_german_text_with_personal_data(gcat):
    sc = scope()
    add_doc(gcat, "ticket-1", SUPPORT, [f"Rückruf erbeten: {PETRA}. Danke!"], allow=["group:support-team"])
    pii.scan(gcat, sc, "dirk", cfg())
    found = pii.findings(gcat, "ticket-1#0")
    assert {"PERSON", "EMAIL_ADDRESS", "IBAN_CODE"} <= {f["entity_type"] for f in found}
    text = gcat.one("SELECT content FROM chunk WHERE chunk_id = 'ticket-1#0'")["content"]
    person = next(f for f in found if f["entity_type"] == "PERSON")
    assert text[person["start_pos"]:person["end_pos"]] == "Petra Schulz" and person["score"] > 0
    stored = repr(gcat.query("SELECT * FROM pii_finding")) + repr(gcat.query("SELECT * FROM pii_scan"))
    for value in ("Petra", "Schulz", "petra.schulz@example.de", "DE89", "0532"):
        assert value not in stored
    assert gcat.one("SELECT language, detector_version FROM pii_scan")["detector_version"] == pii.detector_version()


def test_english_chunk_uses_english_detector(gcat):
    sc = scope()
    add_doc(gcat, "ticket-en", SUPPORT, ["Please call back Mrs Jane Doe at jane.doe@example.com, the order is late."])
    pii.scan(gcat, sc, "dirk", cfg())
    assert gcat.one("SELECT language FROM pii_scan")["language"] == "en"
    assert {"PERSON", "EMAIL_ADDRESS"} <= {f["entity_type"] for f in pii.findings(gcat, "ticket-en#0")}


def test_chunk_not_yet_scanned(gcat):
    sc = scope()
    add_doc(gcat, "kb", KB, ["Hallenzeiten im Winter."], allow=["group:staff"])
    approve_all(gcat, sc)
    acl.sync(gcat, sc, "dirk", force=True)
    rep = pii.build_index(gcat, sc, "dirk")
    assert rep["failed"] == [{"chunk_id": "kb#0", "item_id": "kb", "reason": "pii_unscanned"}]
    assert _rows(gcat, "kb") == []
    assert "pii_unscanned" in {r["category_id"]: r for r in gate.status(gcat, sc)}[KB]["reasons"]


def test_internal_source_with_personal_data(gcat):
    sc = scope()
    add_doc(gcat, "notes", KB, ["Protokoll: Herr Max Mustermann leitet das Training."], allow=["group:staff"])
    approve_all(gcat, sc)  # seeded sensitivity: internal
    assert pol.active(gcat, KB)["doc"]["sensitivity"] == "internal"
    govern(gcat, sc)
    assert _rows(gcat, "notes")[0]["sensitivity"] == "personal"


def test_health_data(gcat):
    sc = scope()
    add_doc(gcat, "claim", SUPPORT, [f"Krankenversichertennummer {valid_kvnr(3)} für die Abrechnung."],
            allow=["group:support-team"])
    approve_all(gcat, sc)
    rep = govern(gcat, sc)
    assert pii.classify(pii.findings(gcat, "claim#0"), "internal") == "special-category"
    assert rep["excluded"] == [{"chunk_id": "claim#0", "reason": "special_category"}] and _rows(gcat, "claim") == []
    row = pol.edit(gcat, sc, SUPPORT, {"legal_basis": "Art. 9(2)(h) DSGVO"}, "gina")
    with pytest.raises(Refused, match="compliance"):
        pol.approve(gcat, sc, SUPPORT, row["version"], "ben")
    pol.approve(gcat, sc, SUPPORT, row["version"], "eva", "Abrechnung mit Kasse")
    pii.build_index(gcat, sc, "dirk")
    out = _rows(gcat, "claim")
    assert out[0]["sensitivity"] == "special-category" and valid_kvnr(3) not in out[0]["content"]


def test_pseudonymize_customer_names(gcat):
    sc = scope()
    add_doc(gcat, "t1", SUPPORT, ["Frau Erika Beispiel meldet einen Defekt."], allow=["group:support-team"])
    add_doc(gcat, "t2", SUPPORT, ["Rückfrage von Frau Erika  Beispiel zum Defekt."], allow=["group:support-team"])
    approve_all(gcat, sc, masking={"default": "replace-with-type", "PERSON": "pseudonymize"})
    govern(gcat, sc)
    a, b = _rows(gcat, "t1")[0]["content"], _rows(gcat, "t2")[0]["content"]
    tok = re.search(r"<PERSON_[0-9a-f]{8}>", a)[0]
    assert tok in b and "Erika" not in a + b and "Beispiel" not in a + b


def test_redact_phone_numbers(gcat):
    sc = scope()
    add_doc(gcat, "t3", SUPPORT, ["Bitte zurückrufen, Telefon 030 1234567, ab 14 Uhr."], allow=["group:support-team"])
    approve_all(gcat, sc, masking={"default": "replace-with-type", "PHONE_NUMBER": "redact"})
    govern(gcat, sc)
    out = _rows(gcat, "t3")[0]["content"]
    assert "1234567" not in out and "030" not in out and not re.search(r"\d{3}", out.replace("14 Uhr", ""))


def test_mask_operator(gcat):
    sc = scope()
    add_doc(gcat, "t4", SUPPORT, ["Kontakt: petra.schulz@example.de"], allow=["group:support-team"])
    approve_all(gcat, sc, masking={"default": "mask"})
    govern(gcat, sc)
    assert _rows(gcat, "t4")[0]["content"] == "Kontakt: " + "*" * len("petra.schulz@example.de")


# ---------------------------------------------------------------- key separation

def test_agent_attempts_reidentification(gcat):
    sc = scope()
    pii.key(gcat)  # the key exists
    agent(gcat, sc)
    ctx = access.context(gcat, "faq-bot", "finn", [])
    with pytest.raises(access.AccessDenied, match="keys_schema"):
        pii.agent_key_lookup(gcat, ctx)
    ev = events(gcat, "agent.key_access")[-1]
    assert ev["result"] == "deny" and ev["details"]["agent"] == "faq-bot"


def test_authorized_reidentification(gcat):
    sc = scope()
    add_doc(gcat, "t1", SUPPORT, ["Frau Erika Beispiel meldet einen Defekt."], allow=["group:support-team"])
    approve_all(gcat, sc, masking={"PERSON": "pseudonymize"})
    govern(gcat, sc)
    tok = re.search(r"<PERSON_[0-9a-f]{8}>", _rows(gcat, "t1")[0]["content"])[0]
    with pytest.raises(Refused, match="purpose"):
        pii.reidentify(gcat, sc, tok, "eva", " ")
    with pytest.raises(access.AccessDenied):
        pii.reidentify(gcat, sc, tok, "ben", "Neugier")
    assert pii.reidentify(gcat, sc, tok, "eva", "Auskunftsersuchen Art. 15") == ["Erika Beispiel"]
    ev = events(gcat, "govern.reidentify")
    assert [e["result"] for e in ev] == ["deny", "recorded"]
    assert (ev[-1]["actor"], ev[-1]["details"]["purpose"]) == ("eva", "Auskunftsersuchen Art. 15")
    assert "Erika" not in repr(ev)
    assert "Erika" not in dump_catalog_index(gcat)


def dump_catalog_index(cat) -> str:
    return repr(cat.query("SELECT * FROM agent_index")) + repr(cat.query("SELECT * FROM audit_event"))


# ---------------------------------------------------------------- recall gate

NAMES = [("Anna", "Acker"), ("Bert", "Bauer"), ("Carla", "Clausen"), ("Dora", "Dietz"), ("Emil", "Engel"),
         ("Fritz", "Funk"), ("Greta", "Gross"), ("Hanna", "Hahn"), ("Ida", "Imhof"), ("Jonas", "Jung"),
         ("Karl", "Kunz"), ("Lea", "Lorenz"), ("Mia", "Mohr"), ("Nils", "Naumann"), ("Olga", "Ott"),
         ("Paul", "Pohl"), ("Rita", "Roth"), ("Sven", "Seidel"), ("Tina", "Thiel"), ("Udo", "Ulrich"),
         ("Vera", "Vogt"), ("Willi", "Wolf"), ("Xenia", "Xaver"), ("Yara", "Yilmaz"), ("Zoe", "Zander")]


def _labelled_sample(cat, sc, tmp_path):
    texts = []
    for i, (first, last) in enumerate(NAMES):  # 22 with a salutation (detected), 3 without (missed)
        texts.append(f"{'Frau' if i < 22 else 'Notiz:'} {first} {last} hat wegen der Halle angerufen.")
    texts += [f"Allgemeine Frage {i} zu Hallenzeiten." for i in range(175)]
    ids = add_doc(cat, "tickets", SUPPORT, texts, allow=["group:support-team"])
    pii.scan(cat, sc, "dirk", cfg())
    out = tmp_path / "labels.csv"
    pii.export_sample(cat, sc, SUPPORT, out, "clara")
    label = {cid: f"PERSON={f} {lname}" for cid, (f, lname) in zip(ids, NAMES)}
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 200
    for r in rows:
        r["labels"] = label.get(r["chunk_id"], "")
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return out, ids


def test_recall_below_threshold(gcat, tmp_path):
    sc = scope()
    approve_all(gcat, sc, sensitivity="personal")
    path, ids = _labelled_sample(gcat, sc, tmp_path)
    res = pii.import_labels(gcat, sc, path, "clara", cfg())
    assert res["recall"]["PERSON"] == {"labelled": 25, "detected": 22, "recall": 0.88}
    assert not res["passed"] and any("PERSON recall 0.88" in r for r in res["reasons"])
    assert sorted(m.split(":")[0] for m in res["missed"]) == sorted(ids[22:25])
    acl.sync(gcat, sc, "dirk", force=True)
    st = {r["category_id"]: r for r in gate.status(gcat, sc)}[SUPPORT]
    assert st["status"] == "blocked" and "pii_recall" in st["reasons"]
    assert "Acker" not in repr(gcat.query("SELECT * FROM pii_label")) + repr(gcat.query("SELECT * FROM pii_gate_result"))


def test_recall_gate_passes_and_needs_sample_size(gcat, tmp_path):
    sc = scope()
    approve_all(gcat, sc, sensitivity="personal")
    path, _ = _labelled_sample(gcat, sc, tmp_path)
    assert pii.import_labels(gcat, sc, path, "clara", cfg(recall_threshold=0.85))["passed"]
    assert pii.gate_state(gcat, SUPPORT) == "passed"
    small = pii.import_labels(gcat, sc, path, "clara", cfg(recall_threshold=0.85, min_sample=500))
    assert not small["passed"] and "at least 500" in small["reasons"][0]


def test_detector_updated(gcat, tmp_path, monkeypatch):
    sc = scope()
    approve_all(gcat, sc, sensitivity="personal")
    path, _ = _labelled_sample(gcat, sc, tmp_path)
    pii.import_labels(gcat, sc, path, "clara", cfg(recall_threshold=0.85))
    monkeypatch.setattr(pii, "RECOGNIZERS_VERSION", "2")
    assert pii.gate_state(gcat, SUPPORT) == "outdated"
    acl.sync(gcat, sc, "dirk", force=True)
    assert {"pii_recall", "pii_unscanned"} <= set({r["category_id"]: r for r in gate.status(gcat, sc)}[SUPPORT]
                                                  ["reasons"])
    assert pii.build_index(gcat, sc, "dirk")["failed"]  # re-evaluated before the next index build


# ---------------------------------------------------------------- human review

def _hermes(cat, sc):
    add_doc(cat, "kb-hermes", KB, ["Der Versand erfolgt mit Hermes innerhalb von drei Tagen."], allow=["group:staff"])
    approve_all(cat, sc)
    govern(cat, sc, low=0.4, high=0.85)
    text = "Der Versand erfolgt mit Hermes innerhalb von drei Tagen."
    s = text.index("Hermes")  # a mid-score NER hit on a product name (the blank test model has no NER)
    cat.execute("INSERT INTO pii_finding VALUES ('f-hermes', 'kb-hermes#0', 'PERSON', ?, ?, 0.6, ?, 'pending',"
                " NULL, NULL)", (s, s + 6, pii.detector_version()))
    pii.build_index(cat, sc, "dirk")
    assert "Hermes" not in _rows(cat, "kb-hermes")[0]["content"]  # pending findings are masked (fail safe)
    assert [f["finding_id"] for f in pii.queue(cat, sc)] == ["f-hermes"]


def test_reviewer_rejects_false_positive(gcat):
    sc = scope()
    _hermes(gcat, sc)
    with pytest.raises(Refused, match="reviewer roles"):
        pii.review(gcat, sc, "f-hermes", "reject", "finn")
    pii.review(gcat, sc, "f-hermes", "reject", "clara", "Produktname")
    pii.build_index(gcat, sc, "dirk")
    assert "mit Hermes innerhalb" in _rows(gcat, "kb-hermes")[0]["content"]
    ev = events(gcat, "govern.pii_review")[-1]
    assert (ev["actor"], ev["details"]["previous"], ev["details"]["new"]) == ("clara", "pending", "rejected")
    assert pii.queue(gcat, sc) == []


def test_decision_reverted(gcat):
    sc = scope()
    _hermes(gcat, sc)
    pii.review(gcat, sc, "f-hermes", "reject", "clara")
    pii.build_index(gcat, sc, "dirk")
    pii.review(gcat, sc, "f-hermes", "revert", "eva", "doch ein Name")
    pii.build_index(gcat, sc, "dirk")
    assert "Hermes" not in _rows(gcat, "kb-hermes")[0]["content"]
    assert [e["details"]["new"] for e in events(gcat, "govern.pii_review")] == ["rejected", "pending"]
