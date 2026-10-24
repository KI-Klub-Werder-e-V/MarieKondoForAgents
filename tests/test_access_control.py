"""access-control (c07): ACL capture and propagation, enforcement inside retrieval (DuckDB SQL / Postgres RLS),
two identities, least-privilege tools, source policies, leak suite, governance gate. Synthetic data only."""

import json
import logging
from datetime import timedelta

import pytest

from gov_fixture import KB, SUPPORT, add_doc, agent, approve_all, events, govern, scope, set_manifest_acl
from konmari.catalog import now
from konmari.governance import access, acl, gate, pii
from konmari.governance import policy as pol
from konmari.versions import Refused


def _ctx(cat, sc, user, groups, name="faq-bot", scopes=None):
    if access.agent_client(cat, name) is None:
        agent(cat, sc, name, scopes)
    return access.context(cat, name, user, groups)


class FakeGraph:
    def __init__(self, groups):
        self.groups = groups

    def get(self, url):
        return json.dumps({"value": [{"roles": ["read"], "grantedToV2": {"group": {"displayName": g}}}
                                     for g in self.groups]}).encode()


# ---------------------------------------------------------------- Source ACL capture

def test_document_with_group_permissions(gcat):
    sc = scope()
    add_doc(gcat, "sp-doc", SUPPORT, ["Rückgabe innerhalb von 14 Tagen."])
    meta = {"category": SUPPORT, "drive_id": "d1", "sp_item_id": "i1"}
    gcat.execute("UPDATE corpus_item SET meta = ? WHERE item_id = 'sp-doc'", (json.dumps(meta),))
    acl.sync(gcat, sc, "dirk", {"*": acl.SharePointAcl(FakeGraph(["support-team"]))}, force=True)
    it = gcat.one("SELECT * FROM corpus_item WHERE item_id = 'sp-doc'")
    assert list(it["allowed_principals"]) == ["group:support-team"]
    assert it["acl_status"] == "known" and it["acl_version"]  # the sync timestamp when the source has no version


def test_source_exposes_no_acl(gcat, tmp_path):
    from sqlalchemy import create_engine

    sc = scope()
    add_doc(gcat, "legacy", SUPPORT, ["Altes Protokoll ohne Rechte."])
    sqlite = acl.SqlAcl(create_engine(f"sqlite:///{tmp_path}/x.db"))
    assert sqlite({"meta": "{}"}, {"asset_uri": "sql://localhost/x/main/t"}) is None  # connector cannot read ACLs
    approve_all(gcat, sc)
    acl.sync(gcat, sc, "dirk", {"*": sqlite}, force=True)
    row = gcat.one("SELECT * FROM chunk WHERE item_id = 'legacy'")
    assert row["acl_status"] == "unknown" and list(row["allowed_principals"]) == []
    pii.scan(gcat, sc, "dirk", gate_cfg())
    rep = pii.build_index(gcat, sc, "dirk")
    assert [f["reason"] for f in rep["failed"]] == ["acl_missing"]
    assert access.retrieve(gcat, _ctx(gcat, sc, "finn", ["support-team"]), "Protokoll") == []
    # the owner assigns explicit principals in the source policy
    row = pol.edit(gcat, sc, SUPPORT, {"principals": {"allow": ["group:support-team"]}}, "gina", "legacy share")
    pol.approve(gcat, sc, SUPPORT, row["version"], "ben")
    acl.sync(gcat, sc, "dirk", {"*": sqlite}, force=True)
    pii.build_index(gcat, sc, "dirk")
    assert gcat.one("SELECT acl_status FROM chunk WHERE item_id = 'legacy'")["acl_status"] == "policy"
    assert len(access.retrieve(gcat, _ctx(gcat, sc, "finn", ["support-team"]), "Protokoll")) == 1


def gate_cfg():
    from gov_fixture import cfg

    return cfg()


# ---------------------------------------------------------------- propagation

def test_chunks_inherit_acl(gcat):
    sc = scope()
    add_doc(gcat, "manual", KB, [f"Abschnitt {i} der Hallenordnung." for i in range(12)], allow=["group:A", "group:B"])
    acl.sync(gcat, sc, "dirk", force=True)
    rows = gcat.query("SELECT allowed_principals, acl_version FROM chunk WHERE item_id = 'manual'")
    parent = gcat.one("SELECT acl_version FROM corpus_item WHERE item_id = 'manual'")
    assert len(rows) == 12
    assert all(sorted(r["allowed_principals"]) == ["group:A", "group:B"] for r in rows)
    assert {r["acl_version"] for r in rows} == {parent["acl_version"]} == {"src-v1"}


def test_index_write_without_acl_rejected(gcat, caplog):
    with caplog.at_level(logging.ERROR, logger="konmari.governance"):
        with pytest.raises(acl.AclMissing, match="chunk c-1 from source crm-export"):
            acl.require_acl({"chunk_id": "c-1", "allowed_principals": []}, "crm-export")
    assert "c-1" in caplog.text and "crm-export" in caplog.text
    if gcat.dialect == "postgres":  # the database refuses it too
        with pytest.raises(Exception, match="agent_index_acl_present"):
            gcat.execute("INSERT INTO agent_index VALUES ('c-1', 'i', 'kb', 'x', 'internal', ?, ?, 'v', 1, NULL, ?)",
                         ([], [], now()))


# ---------------------------------------------------------------- enforcement inside retrieval

def _two_docs(cat, sc):
    add_doc(cat, "public-faq", SUPPORT, ["Der Mitgliedsbeitrag ist im Januar fällig."], allow=["group:support-team"])
    add_doc(cat, "board-notes", SUPPORT, ["Vertraulich: Mitgliedsbeitrag Erhöhung im Januar geplant."],
            allow=["group:board"], deny=["user:x@example.org"])
    approve_all(cat, sc)
    govern(cat, sc)


def test_user_without_access_queries(gcat):
    sc = scope()
    _two_docs(gcat, sc)
    hits = access.retrieve(gcat, _ctx(gcat, sc, "finn", ["support-team"]), "Vertraulich Erhöhung Mitgliedsbeitrag")
    assert [h["item_id"] for h in hits] == ["public-faq"]  # best match is the board chunk, which never comes back


def test_explicit_deny_wins(gcat):
    sc = scope()
    _two_docs(gcat, sc)
    assert access.retrieve(gcat, _ctx(gcat, sc, "x@example.org", ["board"]), "Vertraulich Erhöhung") == []
    assert [h["item_id"] for h in access.retrieve(gcat, _ctx(gcat, sc, "y@example.org", ["board"]),
                                                    "Vertraulich Erhöhung")] == ["board-notes"]


@pytest.mark.postgres
def test_principal_context_does_not_leak_across_pooled_connections(pg_url):
    from konmari.catalog import connect

    cat, sc = connect(pg_url), scope()
    _two_docs(cat, sc)
    board = access.retrieve(cat, _ctx(cat, sc, "y@example.org", ["board"]), "Vertraulich")
    other = access.retrieve(cat, _ctx(cat, sc, "finn", ["support-team"]), "Vertraulich Mitgliedsbeitrag")
    assert [h["item_id"] for h in board] == ["board-notes"] and [h["item_id"] for h in other] == ["public-faq"]
    left = cat.one("SELECT current_setting('app.principals', true) AS p, current_user AS u")
    assert not left["p"] and left["u"] != "konmari_agent"
    with cat.transaction():  # RLS, not the application, filters: as the agent role without context nothing shows
        cat.execute("SET LOCAL ROLE konmari_agent")
        assert cat.query("SELECT chunk_id FROM agent_index") == []
    cat.close()


@pytest.mark.postgres
def test_rls_benchmark_uses_principal_index(pg_url, capsys):
    """Task 5.5: EXPLAIN ANALYZE of the filtered query under RLS on a synthetic pilot-sized sample (5 000 chunks).
    shortcut: pgvector is not installed in CI, so the ANN part is measured in c08/c09; this checks the ACL filter."""
    import statistics
    import time

    from konmari.catalog import connect

    cat = connect(pg_url)
    rows = [(f"c{i}", f"i{i // 10}", KB, f"Abschnitt {i}", "internal", [f"group:g{i % 50}"], [], "v", 1, None, now())
            for i in range(5000)]
    cat.many("INSERT INTO agent_index VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    cat.execute("ANALYZE agent_index")
    times = []
    for _ in range(30):
        with cat.transaction():
            cat.execute("SET LOCAL ROLE konmari_agent")
            cat.execute("SELECT set_config('app.principals', 'group:g7,user:u', true),"
                        " set_config('app.categories', ?, true)", (KB,))
            t = time.perf_counter()
            plan = cat.query("EXPLAIN ANALYZE SELECT chunk_id FROM agent_index WHERE category_id = ?", (KB,))
            times.append((time.perf_counter() - t) * 1000)
    p95 = sorted(times)[int(0.95 * (len(times) - 1))]
    text = "\n".join(next(iter(r.values())) for r in plan)
    assert "rows=100 " in text  # 5000 / 50 groups
    with capsys.disabled():
        print(f"\nRLS filter p95 {p95:.1f} ms (median {statistics.median(times):.1f} ms) over 5000 chunks")
    assert p95 < 500
    cat.close()


# ---------------------------------------------------------------- freshness and revocation

def test_access_revoked_in_source(gcat):
    sc = scope()
    _two_docs(gcat, sc)
    ctx = _ctx(gcat, sc, "finn", ["support-team"])
    assert access.retrieve(gcat, ctx, "Mitgliedsbeitrag")
    set_manifest_acl(gcat, "public-faq", ["group:board"])
    acl.sync(gcat, sc, "dirk", force=True)
    assert access.retrieve(gcat, ctx, "Mitgliedsbeitrag") == []
    ev = events(gcat, "govern.acl_revoke")
    assert ev and ev[0]["details"]["item_id"] == "public-faq" and ev[0]["details"]["removed"] == ["group:support-team"]
    sync = gcat.one("SELECT * FROM acl_sync WHERE policy_id = ? ORDER BY started_at DESC LIMIT 1", (SUPPORT,))
    assert sync["status"] == "ok" and sync["lag_seconds"] is not None


def test_sync_overdue(gcat, monkeypatch):
    sc = scope()
    _two_docs(gcat, sc)
    ctx = _ctx(gcat, sc, "finn", ["support-team"])
    later = now() + timedelta(hours=25)
    monkeypatch.setattr(acl, "now", lambda: later)
    assert {r["category_id"]: r["status"] for r in gate.status(gcat, sc, at=later)}[SUPPORT] == "acl_stale"
    assert access.retrieve(gcat, ctx, "Mitgliedsbeitrag") == []
    acl.sync(gcat, sc, "dirk", at=later)  # due now: no --force needed
    assert access.retrieve(gcat, ctx, "Mitgliedsbeitrag")


# ---------------------------------------------------------------- identities

def test_agent_broader_than_user(gcat):
    sc = scope()
    add_doc(gcat, "produkt-a", SUPPORT, ["Produkt A: Reset per Knopfdruck."], allow=["group:produkt-a"])
    add_doc(gcat, "produkt-b", SUPPORT, ["Produkt B: Reset per App."], allow=["group:produkt-b"])
    approve_all(gcat, sc)
    govern(gcat, sc)
    hits = access.retrieve(gcat, _ctx(gcat, sc, "finn", ["produkt-a"]), "Produkt Reset")
    assert [h["item_id"] for h in hits] == ["produkt-a"]
    # and the user's permissions never widen the agent's scopes
    narrow = _ctx(gcat, sc, "finn", ["produkt-a"], name="kb-bot", scopes=[f"read:{KB}"])
    assert access.retrieve(gcat, narrow, "Produkt Reset") == []


def test_foreign_token(gcat):
    sc = scope()
    agent(gcat, sc)
    v = access.HmacValidator("test-secret")
    tok = access.sign_hs256({"aud": "some-other-api", "sub": "finn", "act": {"sub": "faq-bot"}}, "test-secret")
    with pytest.raises(access.AccessDenied) as e:
        access.from_token(gcat, tok, v, "konmari")
    assert (e.value.status, e.value.reason) == (401, "invalid_audience")
    ev = events(gcat, "agent.request")[-1]
    assert ev["result"] == "deny" and ev["details"]["reason"] == "invalid_audience"
    ok = access.from_token(gcat, access.sign_hs256({"aud": ["konmari"], "sub": "finn", "groups": ["support-team"],
                                                    "act": {"sub": "faq-bot"}}, "test-secret"), v, "konmari")
    assert (ok.agent, ok.on_behalf_of) == ("faq-bot", "finn") and "group:support-team" in ok.principals
    with pytest.raises(access.AccessDenied, match="invalid_token"):
        access.from_token(gcat, tok[:-2] + "xx", v, "konmari")


def test_autonomous_agent_without_user(gcat):
    sc = scope()
    _two_docs(gcat, sc)
    add_doc(gcat, "kb-doc", KB, ["Hallenzeiten im Winter."], allow=["group:staff"])
    row = pol.edit(gcat, sc, KB, {"service_principals": ["nightly-report"]}, "gina")
    pol.approve(gcat, sc, KB, row["version"], "clara")
    govern(gcat, sc)
    agent(gcat, sc, "nightly-report", service=True)
    ctx = access.context(gcat, "nightly-report", None)
    hits = access.retrieve(gcat, ctx, "Hallenzeiten Mitgliedsbeitrag Winter Januar")
    assert [h["item_id"] for h in hits] == ["kb-doc"]
    assert events(gcat, "agent.tool_call")[-1]["details"]["on_behalf_of"] == "none"
    agent(gcat, sc)
    with pytest.raises(access.AccessDenied, match="missing_on_behalf_of"):
        access.context(gcat, "faq-bot", None)


# ---------------------------------------------------------------- least-privilege tools

def _writer(cat, sc):
    agent(cat, sc, "ticket-bot", [f"read:{SUPPORT}", f"write:{SUPPORT}"])
    return access.context(cat, "ticket-bot", "finn", ["support-team"])


def test_write_tool_without_approval(gcat):
    sc = scope()
    ctx = _writer(gcat, sc)
    with pytest.raises(access.AccessDenied) as e:
        access.write(gcat, ctx, "create_ticket", SUPPORT, lambda: ("done", ["ticket-1"]))
    assert e.value.status == 403
    ev = events(gcat, "agent.tool_call")[-1]
    assert ev["details"]["decision"] == "deny" and ev["details"]["operation"] == "write"


def test_write_grant_expires(gcat):
    sc = scope()
    ctx = _writer(gcat, sc)
    g = pol.grant(gcat, sc, "ticket-bot", SUPPORT, "create_ticket", "ben")
    assert g["expires_at"] - g["approved_at"] == timedelta(days=90)
    assert access.write(gcat, ctx, "create_ticket", SUPPORT, lambda: ("done", ["ticket-1"])) == "done"
    gcat.execute("UPDATE write_grant SET expires_at = ? WHERE grant_id = ?", (now() - timedelta(seconds=1),
                                                                              g["grant_id"]))
    with pytest.raises(access.AccessDenied, match="grant_expired"):
        access.write(gcat, ctx, "create_ticket", SUPPORT, lambda: ("done", ["ticket-2"]))
    with pytest.raises(Refused, match="data owner"):
        pol.grant(gcat, sc, "ticket-bot", SUPPORT, "create_ticket", "clara")
    pol.grant(gcat, sc, "ticket-bot", SUPPORT, "create_ticket", "ben", days=30)  # renewal
    assert access.write(gcat, ctx, "create_ticket", SUPPORT, lambda: ("done", ["ticket-3"])) == "done"


def test_wildcard_scope_requested(gcat):
    sc = scope()
    for bad in ("*", "all", "read:*", "write:all"):
        with pytest.raises(Refused) as e:
            access.register_agent(gcat, sc, "greedy", [f"read:{KB}", bad], "gina")
        assert any(f"'{bad}'" in r for r in e.value.reasons)
    with pytest.raises(Refused, match="wildcard"):
        pol.grant(gcat, sc, "ticket-bot", SUPPORT, "*", "ben")


# ---------------------------------------------------------------- source policies

def test_policy_change_pending(gcat):
    sc = scope()
    approve_all(gcat, sc, exposure="blocked")
    v = pol.active(gcat, SUPPORT)["version"]
    row = pol.edit(gcat, sc, SUPPORT, {"exposure": "allowed"}, "gina", "pilot ready")
    assert row["status"] == "pending"
    assert pol.active(gcat, SUPPORT)["version"] == v and pol.active(gcat, SUPPORT)["doc"]["exposure"] == "blocked"
    with pytest.raises(Refused, match="neither the data_owner nor the compliance"):
        pol.approve(gcat, sc, SUPPORT, row["version"], "gina")


def test_revert_policy(gcat):
    sc = scope()
    approve_all(gcat, sc, exposure="blocked")
    first = pol.active(gcat, SUPPORT)["version"]
    row = pol.edit(gcat, sc, SUPPORT, {"exposure": "allowed"}, "gina")
    pol.approve(gcat, sc, SUPPORT, row["version"], "ben")
    rev = pol.revert(gcat, sc, SUPPORT, first, "eva", "too early")
    assert rev["status"] == "approved" and rev["reverted_from"] == first and rev["doc"]["exposure"] == "blocked"
    assert [r["status"] for r in pol.versions(gcat, SUPPORT)][-3:] == ["superseded", "superseded", "approved"]
    ev = events(gcat, "govern.policy.revert")[-1]
    assert ev["actor"] == "eva" and ev["details"]["reverted_to"] == first


def test_policy_seed_follows_inventory(cat):
    sc = scope()
    rows = pol.seed(cat, sc, "gina")
    assert [r["policy_id"] for r in rows] == list(sc.categories)
    assert all(r["status"] == "pending" and r["doc"]["owner"] == sc.approver(r["policy_id"]) for r in rows)
    assert pol.seed(cat, sc, "gina") == []


# ---------------------------------------------------------------- leak suite and gate

SUITE = {"principals": [{"id": "support", "user": "test-support", "groups": ["support-team"]},
                        {"id": "outsider", "user": "test-outsider", "groups": []}]}


def test_leak_detected(gcat, monkeypatch):
    sc = scope()
    _two_docs(gcat, sc)
    real = access._rows
    monkeypatch.setattr(access, "_rows", lambda cat, ctx, cats, ids: real(
        cat, access.Context(ctx.agent, ctx.on_behalf_of, ctx.principals + ["group:board"], ctx.categories), cats,
        ids))  # a broken filter
    run = gate.run_leak_suite(gcat, sc, SUITE, "dirk")
    assert not run["passed"]
    assert {"principal": "support", "chunk_id": "board-notes#0", "category_id": SUPPORT} in run["failures"]
    assert "leak_suite" in {r["category_id"]: r for r in gate.status(gcat, sc)}[SUPPORT]["reasons"]


def test_clean_run(gcat):
    sc = scope()
    _two_docs(gcat, sc)
    run = gate.run_leak_suite(gcat, sc, SUITE, "dirk")
    assert run["passed"] and run["queries"] >= 3
    stored = gcat.one("SELECT * FROM leak_run WHERE run_id = ?", (run["run_id"],))
    assert json.loads(stored["policy_versions"]) == pol.active_versions(gcat)


def _ready(cat, sc):
    _two_docs(cat, sc)
    add_doc(cat, "kb-doc", KB, ["Hallenzeiten im Winter."], allow=["group:staff"])
    govern(cat, sc)
    return gate.check(cat, sc, "dirk", SUITE)


def test_source_not_ready(gcat):
    sc = scope()
    add_doc(gcat, "big", KB, [f"Kapitel {i}" for i in range(97)], allow=["group:staff"])
    add_doc(gcat, "orphan", KB, [f"Anhang {i}" for i in range(3)])  # no ACL readable: 3 % unknown
    approve_all(gcat, sc)
    govern(gcat, sc)
    res = {r["category_id"]: r for r in gate.check(gcat, sc, "dirk", SUITE)["sources"]}
    assert res[KB]["status"] == "blocked" and "acl_coverage" in res[KB]["reasons"]
    assert res[KB]["acl_coverage"] == 0.97
    assert KB not in [r["category_id"] for r in gate.exposable(gcat, sc)]
    assert gcat.one("SELECT status FROM governance_status WHERE category_id = ?", (KB,))["status"] == "blocked"


def test_source_ready(gcat):
    sc = scope()
    res = {r["category_id"]: r for r in _ready(gcat, sc)["sources"]}
    assert res[KB] == {"category_id": KB, "status": "approved", "reasons": [], "policy_version": 2,
                       "acl_coverage": 1.0}
    assert {r["category_id"] for r in gate.exposable(gcat, sc)} == {KB, SUPPORT}
    ev = [e for e in events(gcat, "govern.gate") if e["details"]["policy"] == KB][-1]
    assert ev["details"]["new"] == "approved"


def test_openfga_prefilter(gcat):
    import io

    sc = scope()
    _two_docs(gcat, sc)
    seen = {}

    def fake(req, timeout):
        seen["body"] = json.loads(req.data)
        return io.BytesIO(json.dumps({"objects": ["document:board-notes"]}).encode())

    fga = access.OpenFga("http://fga.local", "store-1", urlopen_=fake)
    ctx = _ctx(gcat, sc, "y@example.org", ["board", "support-team"])
    hits = access.retrieve(gcat, ctx, "Mitgliedsbeitrag Januar", prefilter=fga)
    assert [h["item_id"] for h in hits] == ["board-notes"] and seen["body"]["user"] == "user:y@example.org"


def test_openfga_prefilter_from_config():
    from gov_fixture import cfg

    c = cfg()
    assert access.openfga_prefilter(c) is None
    c["openfga"].update(enabled=True, url="http://fga.local", store_id="s1")
    assert isinstance(access.openfga_prefilter(c), access.OpenFga)


def test_fileshare_acl_reads_group_permission(tmp_path):
    import grp
    import os

    f = tmp_path / "hallenordnung.txt"
    f.write_text("Hallenordnung")
    os.chmod(f, 0o640)
    got = acl.fileshare_acl({"content_ref": str(f), "meta": "{}"}, {})
    assert got["allow"] == [f"group:{grp.getgrgid(f.stat().st_gid).gr_name}"] and got["version"]
    os.chmod(f, 0o600)  # owner only: the user is recorded because no group grant exists
    assert acl.fileshare_acl({"content_ref": str(f), "meta": "{}"}, {})["allow"][0].startswith("user:")
    assert acl.fileshare_acl({"content_ref": str(tmp_path / "missing"), "meta": "{}"}, {}) is None


def test_golden_records_inherit_policy_acl(sot):
    from konmari.policy import golden

    cat, sc = sot["cat"], scope()
    row = pol.edit(cat, sc, SUPPORT, {"entities": ["customer"], "principals": {"allow": ["group:sales"]}}, "gina")
    pol.approve(cat, sc, SUPPORT, row["version"], "ben")
    golden.build(cat, sot["sc"], "customer", "ben")
    rows = cat.query("SELECT DISTINCT allowed_principals, acl_status FROM golden_attribute")
    assert [(list(r["allowed_principals"]), r["acl_status"]) for r in rows] == [(["group:sales"], "policy")]


def test_chunking_copies_the_item_acl(c06):
    from konmari.chunking import store as cst

    cat, sc = c06["cat"], c06["sc"]
    acl.sync(cat, sc, "dirk", force=True)  # ACLs captured before the chunking run
    cst.run(cat, sc, "ben")
    rows = cat.query("SELECT DISTINCT allowed_principals, acl_status FROM chunk")
    assert [(sorted(r["allowed_principals"]), r["acl_status"]) for r in rows] == \
        [(["group:Geschäftsstelle", "group:Vorstand"], "known")]
