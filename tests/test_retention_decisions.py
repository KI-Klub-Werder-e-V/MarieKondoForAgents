"""c04 retention-decisions: scenarios from openspec/changes/c04-discard-pass/specs/retention-decisions/spec.md.
All documents, people and records are synthetic."""

import json
import os
import stat
import zlib
from datetime import date, timedelta

import pytest

from konmari.catalog import connect, new_id, now, verify_chain
from konmari.charter.store import scope
from konmari.discard import calibrate as dcal
from konmari.discard import corpus
from konmari.discard import decisions as dd
from konmari.discard import detect as ddet
from konmari.discard import events as dev
from konmari.discard import retention as ret
from konmari.discard.archive import FsArchive, JsonIndex
from konmari.inventory.lineage import MemoryTransport
from konmari.versions import Refused

from test_dedup_detection import words

CID = "verein-faq-assistant"


def sc_(dp):
    return scope(CID, dp["cat"])


def detect(dp, entries, **cfg):
    dp["items"](entries)
    return ddet.detect(dp["cat"], actor="dirk", cfg=cfg or None, manifest=dp["manifest"])


def item(asset, name, text=None, **meta):
    return {"asset_uri": asset, "name": name, "text": text or " ".join(words(zlib.crc32(name.encode()), 60)), **meta}


def dup_pair(asset, base, i=0):
    text = " ".join(words(5000 + i, 50))
    return [item(asset, f"{base}.txt", text), item(asset, f"{base}_kopie.txt", text)]


def proposals(cat, **where):
    return [p for p in cat.query("SELECT * FROM proposal ORDER BY created_at, proposal_id")
            if all(p[k] == v for k, v in where.items())]


def approved_archive(dp, actor="ben"):
    """One exact duplicate in kb-and-sops, proposed by dirk and approved by ben."""
    detect(dp, dup_pair("manual://kb/sops", "Satzung"))
    sc = sc_(dp)
    [p] = dd.propose(dp["cat"], sc, "dirk")["created"]
    dd.decide(dp["cat"], sc, p["proposal_id"], actor)
    return sc, p


# ---------------------------------------------------------------- 6. proposals, approval, decision log

def test_proposal_for_a_non_canonical_duplicate(dp):
    detect(dp, [item("manual://kb/sops", "Satzung_2024.txt", "Satzung " + " ".join(words(1, 60)), version="2"),
                item("manual://kb/sops", "Satzung_alt.txt", "Satzung " + " ".join(words(1, 60)), version="1")])
    cat = dp["cat"]
    [p] = dd.propose(cat, sc_(dp), "dirk")["created"]
    cl = cat.one("SELECT * FROM cluster WHERE family = 'exact'")
    assert p["action"] == "archive" and p["item_id"] == "manual://kb/sops/Satzung_alt.txt"
    assert cl["canonical_item"] == "manual://kb/sops/Satzung_2024.txt"
    assert p["reason"].startswith("exact duplicate of Satzung_2024.txt")
    [f] = json.loads(p["flag_ids"])
    assert cat.one("SELECT cluster_id FROM flag WHERE flag_id = ?", (f,))["cluster_id"] == cl["cluster_id"]


def test_proposal_does_not_change_data(dp, cli):
    entries = [e for i in range(200) for e in dup_pair("manual://kb/sops", f"doc{i}", i)]
    detect(dp, entries)
    before = {p.name: p.read_bytes() for p in dp["files"].iterdir()}
    code, out, err = cli("discard", "propose", "--as", "dirk")
    assert code == 0 and "200 proposal(s) created" in out
    cat = dp["cat"]
    assert {r["state"] for r in cat.query("SELECT state FROM corpus_item")} == {"active"}
    assert not dp["archive"].exists() or not any(dp["archive"].iterdir())
    assert not cat.query("SELECT * FROM archive_manifest") and len(corpus.kept_items(cat, CID)) == 400
    assert {p.name: p.read_bytes() for p in dp["files"].iterdir()} == before
    assert not dp["lineage"].exists() or "konmari_discard" not in dp["lineage"].read_text()


def test_proposals_from_scorecard_discard_verdicts(dp):
    detect(dp, [item("manual://support/tickets", "alt_ticket.txt")])
    cat, sc = dp["cat"], sc_(dp)
    vid = new_id()
    cat.execute("INSERT INTO verdict VALUES (?, 'r', 'manual://support/tickets', ?, 'support-history', 'konmari-joy-1',"
                " 'discard', 'irrelevant', NULL, '[]', FALSE, 'h', ?)", (vid, CID, now()))
    from konmari.scorecard.engine import add_review
    add_review(cat, "manual://support/tickets", vid, "accept", "ben", "discard", "discard", None, "konmari-joy-1")
    [p] = dd.propose(cat, sc, "dirk")["created"]
    assert p["action"] == "exclude" and p["method"] == "scorecard" and "irrelevant" in p["reason"]


def test_unauthorized_approver(dp):
    detect(dp, dup_pair("manual://policies/contracts", "Mietvertrag"))
    cat, sc = dp["cat"], sc_(dp)
    p = next(p for p in dd.propose(cat, sc, "dirk")["created"] if p["action"] == "archive")
    with pytest.raises(Refused, match="authorization error"):
        dd.decide(cat, sc, p["proposal_id"], "ben")  # clara approves policies-and-contracts in this charter
    ev = cat.one("SELECT * FROM decision_event WHERE action = 'discard.approve' AND result = 'denied'")
    assert ev["actor"] == "ben" and json.loads(ev["details"])["item_id"] == p["item_id"]
    assert dd.get(cat, p["proposal_id"])["status"] == "pending"
    assert dd.decide(cat, sc, p["proposal_id"], "clara")["status"] == "approved"


def test_proposer_may_not_approve(dp):
    detect(dp, dup_pair("manual://kb/sops", "Satzung"))
    cat, sc = dp["cat"], sc_(dp)
    [p] = dd.propose(cat, sc, "ben")["created"]
    with pytest.raises(Refused, match="may not decide it"):
        dd.decide(cat, sc, p["proposal_id"], "ben")


def test_approver_changes_the_action(dp, cli):
    detect(dp, [{"asset_uri": "manual://finance/payroll", "name": "Lohn.txt", "text": "Lohnlauf"}])
    cat = dp["cat"]
    [p] = dd.propose(cat, sc_(dp), "dirk")["created"]
    assert p["action"] == "exclude"
    code, out, err = cli("discard", "approve", p["proposal_id"], "--action", "keep", "--comment",
                         "still referenced by support team", "--as", "ben")
    assert code == 0, err
    q = dd.get(cat, p["proposal_id"])
    assert (q["action"], q["decided_action"], q["decided_by"], q["comment"]) == \
        ("exclude", "keep", "ben", "still referenced by support team")
    assert q["decided_at"] is not None


def test_bulk_approval_preview(dp, cli):
    detect(dp, [e for i in range(120) for e in dup_pair("manual://kb/sops", f"sop{i}", i)])
    cli("discard", "propose", "--as", "dirk")
    cat = dp["cat"]
    args = ("discard", "bulk-approve", "--action", "archive", "--category", "kb-and-sops", "--method", "exact",
            "--as", "ben")
    code, out, err = cli(*args)
    assert code == 0 and "120 proposal(s) would be approved; repeat with --yes" in out
    assert len([x for x in out.splitlines() if x.startswith("manual://kb/sops/")]) == 120
    assert not proposals(cat, status="approved")
    code, out, err = cli(*args, "--yes")
    assert code == 0 and len(proposals(cat, status="approved")) == 120
    with pytest.raises(Refused, match="one by one"):
        dd.bulk_preview(cat, sc_(dp), "erase", "kb-and-sops", "exact")


def test_chain_verification_detects_tampering(dp, cli):
    approved_archive(dp)
    assert cli("discard", "log", "verify")[0] == 0
    cat = dp["cat"]
    target = cat.one("SELECT * FROM decision_event WHERE action = 'discard.approve'")
    cat.conn.execute("UPDATE audit_event SET actor = 'mallory' WHERE id = ?", (target["id"],))  # bypasses the guard
    code, out, err = cli("discard", "log", "verify")
    assert code == 1 and f"chain broken at event {target['seq']}" in err
    assert verify_chain(cat)["id"] == target["id"]


def test_log_export(dp, cli):
    approved_archive(dp)
    code, out, err = cli("discard", "log", "export", "-o", "log.jsonl")
    assert code == 0
    rows = [json.loads(x) for x in open("log.jsonl", encoding="utf-8")]
    assert rows and all(r["hash"] and r["seq"] for r in rows)


def test_full_history_of_one_item(dp, cli):
    sc, p = approved_archive(dp)
    cat = dp["cat"]
    archive = FsArchive(dp["archive"])
    dd.execute(cat, sc, "dirk", archive)
    dd.restore(cat, sc, p["item_id"], "ben", archive)
    hist = dev.history(cat, p["item_id"])
    assert [(h["action"], h["actor"]) for h in hist] == [("discard.propose", "dirk"), ("discard.approve", "ben"),
                                                         ("discard.execute", "dirk"), ("discard.restore", "ben")]
    code, out, err = cli("discard", "history", p["item_id"])
    assert code == 0 and "discard.restore by ben" in out and out.index("propose") < out.index("restore")


# ---------------------------------------------------------------- 7. execution

def test_archive_target_is_readable_only_by_the_archive_role(tmp_path):
    a = FsArchive(tmp_path / "cold")
    a.put("k", b"data")
    assert stat.S_IMODE(os.stat(tmp_path / "cold").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(tmp_path / "cold" / "k").st_mode) == 0o600
    assert a.get("k") == b"data"
    a.delete("k")
    assert not (tmp_path / "cold" / "k").exists()


@pytest.mark.skipif(not os.environ.get("KONMARI_TEST_S3_URL"), reason="needs MinIO (KONMARI_TEST_S3_URL, CI)")
def test_archive_target_s3_minio(monkeypatch):
    import boto3

    from konmari.discard.archive import S3Archive

    monkeypatch.setenv("KONMARI_S3_ENDPOINT", os.environ["KONMARI_TEST_S3_URL"])
    boto3.client("s3", endpoint_url=os.environ["KONMARI_TEST_S3_URL"]).create_bucket(Bucket="konmari-test")
    a = S3Archive("s3://konmari-test/archive")
    a.put("k", b"data")
    assert a.get("k") == b"data"
    a.delete("k")
    anon = boto3.client("s3", endpoint_url=os.environ["KONMARI_TEST_S3_URL"], aws_access_key_id="nobody",
                        aws_secret_access_key="wrong-secret")
    with pytest.raises(Exception):  # only the archive role's credentials can read the bucket
        anon.list_objects_v2(Bucket="konmari-test")


def test_verified_archive(dp):
    sc, p = approved_archive(dp)
    cat = dp["cat"]
    src = corpus.require(cat, p["item_id"])
    before = open(src["content_ref"], "rb").read()
    t = MemoryTransport()
    [res] = dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]), transport=t)
    assert res["result"] == "executed"
    m = cat.one("SELECT * FROM archive_manifest")
    assert m["sha256"] == corpus.sha256(before) == corpus.sha256((dp["archive"] / m["object_key"]).read_bytes())
    assert m["original_location"] == src["content_ref"] and m["proposal_id"] == p["proposal_id"]
    assert corpus.require(cat, p["item_id"])["state"] == "archived"
    assert open(src["content_ref"], "rb").read() == before  # source unchanged
    assert p["item_id"] not in {i["item_id"] for i in corpus.kept_items(cat, CID)}
    assert t.events[0]["dataset"]["facets"]["konmari_discard"]["state"] == "archived"


class CorruptingArchive(FsArchive):
    def put(self, key, data):
        super().put(key, data + b"!")


def test_checksum_mismatch_aborts(dp):
    sc, p = approved_archive(dp)
    cat = dp["cat"]
    [res] = dd.execute(cat, sc, "dirk", CorruptingArchive(dp["archive"]))
    assert res["result"] == "failed"
    assert corpus.require(cat, p["item_id"])["state"] == "active"
    assert not list(dp["archive"].iterdir()) and not cat.query("SELECT * FROM archive_manifest")
    ev = cat.one("SELECT * FROM decision_event WHERE action = 'discard.execute' AND result = 'failed'")
    assert "checksum" in json.loads(ev["details"])["reason"]


def test_sensitive_item_waits_for_masking(dp):
    detect(dp, [item("manual://support/tickets", "export.txt", "Ticket von kunde@example.org: Halle zu kalt")])
    cat, sc = dp["cat"], sc_(dp)
    [p] = dd.propose(cat, sc, "dirk")["created"]
    assert p["action"] == "route-to-masking"
    dd.decide(cat, sc, p["proposal_id"], "ben")
    dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]))
    assert corpus.require(cat, p["item_id"])["state"] == "awaiting-masking"
    assert [i["item_id"] for i in dd.awaiting_masking(cat, CID)] == [p["item_id"]]
    assert not corpus.kept_items(cat, CID)
    dd.register_masked(cat, sc, p["item_id"], "masked://c07/export.txt", "dirk")
    assert [i["masked_ref"] for i in corpus.kept_items(cat, CID)] == ["masked://c07/export.txt"]


def test_exclude_and_restore_from_unchanged_source(dp):
    detect(dp, [{"asset_uri": "manual://finance/payroll", "name": "Lohn.txt", "text": "Lohnlauf"}])
    cat, sc = dp["cat"], sc_(dp)
    [p] = dd.propose(cat, sc, "dirk")["created"]
    dd.decide(cat, sc, p["proposal_id"], "ben")
    dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]))
    assert corpus.require(cat, p["item_id"])["state"] == "excluded" and not list(dp["archive"].iterdir())
    assert dd.restore(cat, sc, p["item_id"], "ben", FsArchive(dp["archive"]))["state"] == "active"


def test_restore_archived_item(dp, cli):
    sc, p = approved_archive(dp)
    cat = dp["cat"]
    dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]))
    code, out, err = cli("discard", "restore", p["item_id"], "--as", "ben")
    assert code == 0, err
    assert corpus.require(cat, p["item_id"])["state"] == "active"
    ev = dev.history(cat, p["item_id"])[-1]
    assert ev["action"] == "discard.restore" and ev["details"]["decision_ref"] == p["proposal_id"]
    assert ev["details"]["checksum_verified"] is True


def test_restore_after_erasure_refused(dp):
    sc, p, _ = _erased(dp)
    cat = dp["cat"]
    with pytest.raises(Refused, match=r"was erased on \d{4}-\d{2}-\d{2}"):
        dd.restore(cat, sc, p["item_id"], "ben", FsArchive(dp["archive"]))
    assert cat.one("SELECT * FROM decision_event WHERE action = 'discard.restore' AND result = 'refused'")


def test_stale_chunks_detected(dp, cli):
    sc, p = approved_archive(dp)
    cat = dp["cat"]
    corpus.register_derived(cat, p["item_id"], "chunk-1", "chunk")
    corpus.register_derived(cat, p["item_id"], "chunk-2", "chunk")
    idx = dp["tmp"] / "index.json"
    idx.write_text(json.dumps(["chunk-1", "chunk-2", "other"]))
    t = MemoryTransport()
    dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]), transport=t)
    assert t.events[0]["dataset"]["facets"]["konmari_discard"]["derived_artifacts_marked"] == ["chunk-1", "chunk-2"]
    assert all(r["marked_for_removal_at"] for r in cat.query("SELECT * FROM derived_artifact"))
    code, out, err = cli("discard", "check-propagation", "--index", idx)
    assert code == 1 and f"{p['item_id']}: chunk-1, chunk-2" in out
    JsonIndex(idx).delete(["chunk-1", "chunk-2"])
    code, out, err = cli("discard", "check-propagation", "--index", idx)
    assert code == 0 and "propagation check passed" in out


# ---------------------------------------------------------------- 8. retention rules, holds, restriction, erasure

def _rule(dp, category="customer-records", period="P8Y", start="end-of-calendar-year-of-last-entry", action="erase"):
    cat, sc = dp["cat"], sc_(dp)
    r = ret.add_rule(cat, sc, category, "Buchungsbelege aufbewahren", "Art. 6(1)(c) DSGVO",
                     "§147(3) AO / §257(4) HGB", start, period, action, "eva")
    return ret.approve_rule(cat, sc, r["rule_id"], "eva")


def test_rule_with_statutory_source(dp, cli):
    detect(dp, [item("manual://finance/payroll", "Beleg_17.txt", last_entry="2020-05-04")])
    code, out, err = cli("retention", "rule", "add", "--category", "financial-ops-metrics", "--purpose",
                         "Buchungsbelege", "--legal-basis", "Art. 6(1)(c) DSGVO", "--source",
                         "§147(3) AO / §257(4) HGB",
                         "--start-event", "end-of-calendar-year-of-last-entry", "--period", "P8Y",
                         "--expiry-action", "erase", "--as", "eva")
    assert code == 0, err
    rid = out.split()[1]
    assert cli("retention", "rule", "approve", rid, "--as", "ben")[0] == 1  # not compliance
    code, out, err = cli("retention", "rule", "approve", rid, "--as", "eva")
    assert code == 0 and f"rule {rid} for financial-ops-metrics approved by eva" in out
    r = ret.approved_rule(dp["cat"], CID, "financial-ops-metrics")
    assert r["approver"] == "eva" and r["approved_at"] and r["source"] == "§147(3) AO / §257(4) HGB"
    it = corpus.require(dp["cat"], "manual://finance/payroll/Beleg_17.txt")
    assert ret.expiry_date(r, it) == date(2028, 12, 31)
    assert "P8Y from end-of-calendar-year-of-last-entry" in cli("retention", "rule", "list")[1]


def test_expiry_dates_at_year_boundaries():
    rule = {"start_event": "end-of-calendar-year-of-last-entry", "period": "P8Y"}
    exp = lambda d: ret.expiry_date(rule, {"meta": {"last_entry": d}})  # noqa: E731
    assert exp("2017-12-31") == date(2025, 12, 31)
    assert exp("2018-01-01") == date(2026, 12, 31)
    rule = {"start_event": "last-entry", "period": "P6M"}
    assert exp("2025-08-31") == date(2026, 2, 28) and exp("2023-12-31") == date(2024, 6, 30)
    assert ret.add_period(date(2024, 2, 29), "P1Y") == date(2025, 2, 28)
    assert ret.expiry_date(rule, {"meta": {}}) is None
    with pytest.raises(Refused):
        ret.parse_period("8 years")


def test_one_period_for_all_categories_rejected(dp, cli):
    code, out, err = cli("retention", "rule", "add", "--category", "*", "--purpose", "p", "--legal-basis", "b",
                         "--source", "s", "--start-event", "last-entry", "--period", "P10Y", "--expiry-action",
                         "erase", "--as", "eva")
    assert code == 1 and "register one rule per category" in err


def _erase_request(dp, name="Rechnung_4711.txt", last_entry="2015-03-01", asset="manual://crm/invoices"):
    detect(dp, [item(asset, name, last_entry=last_entry)])
    cat, sc = dp["cat"], sc_(dp)
    p = dd.create_proposal(cat, sc, corpus.require(cat, f"{asset}/{name}"), "erase", "request", [], "dirk",
                           "erasure request of the data subject")
    return cat, sc, p


def _double_approve(cat, sc, p):
    dd.decide(cat, sc, p["proposal_id"], "ben")
    return dd.decide(cat, sc, p["proposal_id"], "eva", compliance=True)


def test_missing_rule_blocks_erasure(dp):
    cat, sc, p = _erase_request(dp)
    [res] = dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]), proposal_id=p["proposal_id"])
    assert res["result"] == "refused" and "retention rule missing" in res["message"]
    assert dd.get(cat, p["proposal_id"])["status"] == "pending"


def test_hold_blocks_erasure(dp, cli):
    _rule(dp)
    cat, sc, p = _erase_request(dp)
    code, out, err = cli("retention", "hold", "add", "--item", p["item_id"], "--reason", "pending dispute 2026-17",
                         "--owner", "eva", "--review-date", "2027-01-31", "--as", "eva")
    assert code == 0, err
    _double_approve(cat, sc, p)
    [res] = dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]), at=now() + timedelta(days=15))
    assert res["result"] == "refused" and "pending dispute 2026-17" in res["message"]
    assert corpus.require(cat, p["item_id"])["state"] == "active"
    ev = cat.one("SELECT * FROM decision_event WHERE action = 'discard.execute' AND result = 'refused'")
    assert "legal hold" in json.loads(ev["details"])["reason"]


def test_erasure_request_during_retention_period(dp):
    _rule(dp)
    cat, sc, p = _erase_request(dp, last_entry=f"{date.today().year - 2}-06-30")
    _double_approve(cat, sc, p)
    [res] = dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]), at=now() + timedelta(days=15))
    assert res["result"] == "restricted"
    it = corpus.require(cat, p["item_id"])
    end = date(date.today().year - 2 + 8, 12, 31)
    assert it["state"] == "restricted" and it["restricted_until"] == end
    assert not corpus.kept_items(cat, CID) and not cat.query("SELECT * FROM erasure_record")
    ev = dev.history(cat, p["item_id"])[-1]
    assert ev["details"]["new_state"] == "restricted" and ev["details"]["rule_id"]


def test_single_approval_insufficient(dp):
    _rule(dp)
    cat, sc, p = _erase_request(dp)
    q = dd.decide(cat, sc, p["proposal_id"], "ben")
    assert q["status"] == "approved" and q["execute_after"] is None
    [res] = dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]), at=now() + timedelta(days=30))
    assert res["result"] == "refused" and "not scheduled" in res["message"]
    with pytest.raises(Refused, match="compliance role"):
        dd.decide(cat, sc, p["proposal_id"], "clara", compliance=True)


def test_erasure_waiting_period_and_cancel(dp):
    _rule(dp)
    cat, sc, p = _erase_request(dp)
    q = _double_approve(cat, sc, p)
    assert q["status"] == "scheduled" and (q["execute_after"] - now()).days in (13, 14)
    [res] = dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]))
    assert res["result"] == "waiting"
    assert dd.cancel(cat, sc, p["proposal_id"], "eva", "data subject withdrew")["status"] == "cancelled"
    assert corpus.require(cat, p["item_id"])["state"] == "active"


def _erased(dp):
    _rule(dp)
    cat, sc, p = _erase_request(dp)
    corpus.register_derived(cat, p["item_id"], "chunk-9", "chunk")
    _double_approve(cat, sc, p)
    idx = JsonIndex(dp["tmp"] / "index.json")
    idx.path.write_text(json.dumps(["chunk-9"]))
    [res] = dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]), index=idx, at=now() + timedelta(days=15))
    return sc, p, res


def test_erasure_executed_after_waiting_period(dp):
    sc, p, res = _erased(dp)
    cat = dp["cat"]
    assert res["result"] == "erased"
    assert corpus.get(cat, p["item_id"]) is None and not cat.query("SELECT * FROM fingerprint")
    assert json.loads((dp["tmp"] / "index.json").read_text()) == []
    assert dd.check_propagation(cat, CID, JsonIndex(dp["tmp"] / "index.json")) == {}
    er = cat.one("SELECT * FROM erasure_record")
    assert er["item_id"] == p["item_id"] and json.loads(er["approvers"]) == ["ben", "eva"] and er["rule_id"]
    [act] = json.loads(er["open_actions"])
    assert act["owner"] == "ben" and act["action"].startswith("delete at the source: ")
    assert set(er) == {"item_id", "charter_id", "category_id", "rule_id", "approvers", "erased_at", "open_actions"}


def test_erased_item_does_not_reenter_corpus_on_redetect(dp):
    sc, p, _ = _erased(dp)
    cat = dp["cat"]
    ddet.detect(cat, actor="dirk", manifest=dp["manifest"])  # the source copy is still listed in the manifest
    assert corpus.get(cat, p["item_id"]) is None
    assert not cat.query("SELECT * FROM fingerprint WHERE item_id = ?", (p["item_id"],))


def test_erasure_of_archived_item_deletes_archive_copy(dp):
    _rule(dp, "kb-and-sops", "P1Y", "last-entry")
    detect(dp, [item("manual://kb/sops", "a.txt", "Satzung alt", last_entry="2015-01-01"),
                item("manual://kb/sops", "b.txt", "Satzung alt", last_entry="2015-01-01")])
    cat, sc = dp["cat"], sc_(dp)
    [p] = dd.propose(cat, sc, "dirk")["created"]
    dd.decide(cat, sc, p["proposal_id"], "ben")
    dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]))
    assert len(list(dp["archive"].iterdir())) == 1
    q = dd.create_proposal(cat, sc, corpus.require(cat, p["item_id"]), "erase", "request", [], "dirk", "expired")
    _double_approve(cat, sc, q)
    [res] = dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]), at=now() + timedelta(days=15))
    assert res["result"] == "erased" and not list(dp["archive"].iterdir())
    assert not cat.query("SELECT * FROM archive_manifest")


def test_archived_item_expires(dp):
    _rule(dp, "customer-records", "P6M", "last-entry")
    detect(dp, [item("manual://hr/applications", "Bewerbung_X.txt", "Bewerbung", last_entry="2025-01-15"),
                item("manual://hr/applications", "Bewerbung_X_kopie.txt", "Bewerbung", last_entry="2025-01-15")])
    cat, sc = dp["cat"], sc_(dp)
    [p] = [p for p in dd.propose(cat, sc, "dirk")["created"] if p["action"] == "archive"]
    dd.decide(cat, sc, p["proposal_id"], "ben")
    dd.execute(cat, sc, "dirk", FsArchive(dp["archive"]))
    for q in proposals(cat, status="pending"):
        dd.decide(cat, sc, q["proposal_id"], "ben", verdict="reject")
    out = ret.check(cat, sc, "dirk", today=date(2025, 8, 1))
    by_item = {q["item_id"]: q for q in out}
    assert by_item[p["item_id"]]["action"] == "erase"  # the archived copy is covered too
    q = by_item[p["item_id"]]
    assert _double_approve(cat, sc, q)["status"] == "scheduled"
    run = cat.one("SELECT * FROM retention_check_run")
    assert run["finished_at"] and run["window_days"] == 30 and run["schedule"] == "P7D"


def test_check_overdue(dp, cli):
    _rule(dp)
    detect(dp, [item("manual://kb/sops", "a.txt")])
    cat = dp["cat"]
    assert "never run" in ret.overdue(cat, CID)
    assert cli("retention", "check", "--as", "dirk", "--schedule", "P1D")[0] == 0
    assert ret.overdue(cat, CID) is None
    assert "overdue" in ret.overdue(cat, CID, at=now() + timedelta(days=2))
    cat.execute("UPDATE retention_check_run SET finished_at = ?", (now() - timedelta(days=3),))
    code, out, err = cli("discard", "report")
    assert code == 0 and "WARNING: retention check is overdue" in out


def test_calibration_override_needed_for_proposals_in_e2e_style(dp):
    """Containment flags count as calibrated detector output too."""
    faq = words(3, 100)
    detect(dp, [item("manual://kb/sops", "faq.txt", " ".join(faq)),
                item("manual://kb/sops", "handbuch.txt", " ".join(words(4, 600) + faq))])
    cat, sc = dp["cat"], sc_(dp)
    assert dd.propose(cat, sc, "dirk")["blocked"] == {"containment": 1}
    dcal.override(cat, sc, "containment", None, "two documents, checked by hand", "ben")
    [p] = dd.propose(cat, sc, "dirk")["created"]
    assert p["method"] == "containment" and p["item_id"] == "manual://kb/sops/faq.txt"


# ---------------------------------------------------------------- 9. end to end

def _e2e(dp, cli):
    ok = dp["ok"]
    sop = "Schlüsselübergabe: Der Hallenwart übergibt den Schlüssel montags. " + " ".join(words(21, 120))
    entries = [
        item("manual://kb/sops", "Schluessel_SOP.txt", sop, version="2.0"),
        item("manual://support/tickets", "Schluessel_SOP_Anhang.txt", sop),
        item("manual://kb/sops", "Beitraege_2024.txt", "Beitragsordnung " + " ".join(words(22, 80)),
             logical_id="beitragsordnung", version="2024"),
        item("manual://kb/sops", "Beitraege_2025.txt", "Beitragsordnung " + " ".join(words(23, 80)),
             logical_id="beitragsordnung", version="2025"),
        item("manual://finance/payroll", "Lohnabrechnung.txt", "Lohnlauf Übungsleiter"),
        item("manual://support/tickets", "Ticket_88.txt", "Frage von erika.mustermann@example.org zur Halle"),
        item("manual://crm/invoices", "Rechnung_0815.txt", "Rechnung Mitgliedsbeitrag", last_entry="2014-11-02"),
    ]
    dp["items"](entries)
    ok("discard", "detect", "--items", dp["manifest"], "--as", "dirk")
    ok("discard", "calibrate", "--detector", "minhash", "--override-reason", "small pilot, pairs checked by hand",
       "--as", "ben")
    out = ok("discard", "propose", "--as", "dirk")
    cat = connect()
    by = {p["item_id"].rsplit("/", 1)[1]: p for p in dd.list_proposals(cat, CID)}
    assert by["Schluessel_SOP_Anhang.txt"]["action"] == "archive"
    assert by["Beitraege_2024.txt"]["action"] == "archive" and "superseded" in by["Beitraege_2024.txt"]["reason"]
    assert by["Lohnabrechnung.txt"]["action"] == "exclude"
    assert by["Ticket_88.txt"]["action"] == "route-to-masking"
    for name in ("Schluessel_SOP_Anhang.txt", "Beitraege_2024.txt", "Lohnabrechnung.txt", "Ticket_88.txt"):
        ok("discard", "approve", by[name]["proposal_id"], "--as", "ben")
    assert by["Rechnung_0815.txt"]["action"] == "route-to-masking"  # personal tier; kept as is by the owner
    ok("discard", "approve", by["Rechnung_0815.txt"]["proposal_id"], "--action", "keep", "--comment",
       "booking voucher, keep unmasked for the treasurer", "--as", "ben")
    out = ok("discard", "execute", "--as", "dirk")
    assert out.count(": executed") == 5
    kept = {i["item_id"].rsplit("/", 1)[1] for i in corpus.kept_items(cat, CID)}
    assert kept == {"Schluessel_SOP.txt", "Beitraege_2025.txt", "Rechnung_0815.txt"}
    ok("discard", "restore", by["Beitraege_2024.txt"]["item_id"], "--as", "ben")
    ok("retention", "rule", "add", "--category", "customer-records", "--purpose", "Rechnungen",
       "--legal-basis", "Art. 6(1)(c) DSGVO", "--source", "§147(3) AO", "--start-event",
       "end-of-calendar-year-of-last-entry", "--period", "P8Y", "--expiry-action", "erase", "--as", "eva")
    rid = ret.rules(cat, CID)[0]["rule_id"]
    ok("retention", "rule", "approve", rid, "--as", "eva")
    out = ok("retention", "check", "--as", "dirk")
    assert "erase manual://crm/invoices/Rechnung_0815.txt" in out
    assert "decision log verified" in ok("discard", "log", "verify")
    assert "Duplicate rate:" in ok("discard", "report")
    cat.close()


def test_end_to_end_german_pilot_duckdb(dp, cli):
    _e2e(dp, cli)


@pytest.mark.postgres
def test_end_to_end_german_pilot_postgres(pg_url, dp, cli):
    _e2e(dp, cli)
