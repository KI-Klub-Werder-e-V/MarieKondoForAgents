"""c02 source-inventory: scenarios from openspec/changes/c02-category-inventory/specs/source-inventory/spec.md."""

import builtins
import copy
import csv
import json
import os
import shutil
import socket
from pathlib import Path

import pytest

from konmari.catalog import connect
from konmari.charter.store import scope
from konmari.inventory import connectors as cn
from konmari.inventory import lineage, sensitivity
from konmari.inventory import store as inv
from konmari.inventory.connectors import Asset
from konmari.versions import Refused

from conftest import (SP_SITE, approve_pilot, dump_catalog, fake_token, valid_iban, valid_kvnr,
                      valid_tax_id)

CID = "verein-faq-assistant"
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "graph" / "support-site.json").read_text())


class FakeGraph:
    """Serves the recorded Graph fixture by URL and records every URL requested."""

    def __init__(self, *phases):
        self.map, self.calls = {}, []
        for p in phases or ("scan1",):
            self.phase(p)

    def phase(self, name):
        self.map.update(copy.deepcopy(FIXTURE[name]))

    def get(self, url):
        self.calls.append(url)
        body = self.map[url]
        return (body if isinstance(body, str) else json.dumps(body)).encode()


def audits(cat, action, entity=None):
    rows = [r | {"d": json.loads(r["details"])} for r in cat.query(
        "SELECT * FROM audit_event WHERE action = ? ORDER BY ts", (action,))]
    return [r for r in rows if entity is None or r["d"].get("entity") == entity]


def sc_(cat):
    return scope(CID, cat)


def add_source(cli, connector, locator, *extra, actor="dirk"):
    code, out, err = cli("inventory", "source", "add", "--connector", connector, "--locator", locator, *extra,
                         "--as", actor)
    assert code == 0, err
    return out


def records(cat):
    return inv.records(cat, CID)


def rec(cat, uri):
    return inv.get(cat, uri)


@pytest.fixture
def sp(pilot, cli, monkeypatch):
    monkeypatch.setenv("SP_TOKEN", fake_token("Sites.Selected"))
    add_source(cli, "sharepoint", SP_SITE, "--secret-ref", "env:SP_TOKEN")
    return FakeGraph()


def scan(cat, source, **kw):
    return inv.scan(cat, sc_(cat), source, actor="dirk", **kw)


# ---------------------------------------------------------------- 1.1 migrations

def _c02_tables(cat):
    return {r["table_name"] for r in cat.query(
        "SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')")}


C02 = {"inventory_source", "inventory_run", "source_asset", "sensitivity_hint", "category", "asset_category",
       "category_order_version", "category_step"}


def _up_down(cat):
    assert C02 <= _c02_tables(cat) and "audit_event" in _c02_tables(cat)
    cat.rollback("002_c02_category_inventory.sql")
    assert not C02 & _c02_tables(cat)
    assert "audit_event" in _c02_tables(cat)  # c01 tables untouched
    cat.migrate()
    assert C02 <= _c02_tables(cat)


def test_migrate_up_down_duckdb(tmp_path):
    _up_down(connect(f"duckdb:///{tmp_path}/c.duckdb"))


@pytest.mark.postgres
def test_migrate_up_down_postgres(pg_url):
    _up_down(connect(pg_url))


# ---------------------------------------------------------------- registration

def test_source_in_charter_is_accepted(sp, cat):
    row = cat.one("SELECT * FROM inventory_source WHERE source_id = 'support-site'")
    assert row["charter_id"] == CID and row["connector"] == "sharepoint" and row["credential_ref"] == "env:SP_TOKEN"
    [a] = audits(cat, "inventory.source.add", "support-site")
    assert a["actor"] == "dirk" and a["result"] == "recorded" and a["d"]["new"]["locator"] == SP_SITE


def test_source_outside_charter_is_rejected(pilot, cli, cat):
    code, _, err = cli("inventory", "source", "add", "--connector", "fileshare", "--locator", r"\\fs01\hr",
                       "--as", "dirk")
    assert code == 1 and f"approved charter '{CID}'" in err
    assert cat.one("SELECT COUNT(*) AS n FROM inventory_source")["n"] == 0
    assert cat.one("SELECT COUNT(*) AS n FROM source_asset")["n"] == 0
    assert audits(cat, "inventory.source.add")[0]["result"] == "refused"


def test_stable_source_id_across_renamed_sources(pilot, cli, cat):
    (pilot["share"] / "Handbuch").mkdir()
    (pilot["share"] / "Handbuch" / "faq.md").write_text("Wann ist Training?")
    add_source(cli, "fileshare", str(pilot["share"]), "--title", "Office NAS")
    scan(cat, "office-share")
    add_source(cli, "fileshare", str(pilot["share"]) + "/", "--title", "Office NAS (renamed)")
    scan(cat, "office-share")
    src = cat.query("SELECT * FROM inventory_source")
    assert [(s["source_id"], s["title"]) for s in src] == [("office-share", "Office NAS (renamed)")]
    assert [(r["source_id"], r["status"] != "missing") for r in records(cat)] == [("office-share", True)]
    assert len(audits(cat, "inventory.source.update", "office-share")) == 1


def test_sharepoint_library_rename_keeps_record(sp, cat):
    scan(cat, "support-site", http=sp)
    site = sp.map[f"{cn.GRAPH}/sites/contoso.sharepoint.com:/sites/support"]["id"]
    sp.map[f"{cn.GRAPH}/sites/{site}/drives"]["value"][0]["name"] = "Tickets"
    for d in ("docs", "pol"):  # empty delta pages: nothing changed inside the libraries
        link = f"{cn.GRAPH}/drives/b!{d}/root/delta?token={d[0]}1"
        sp.map[link] = {"value": [], "@odata.deltaLink": link}
    scan(cat, "support-site", http=sp)
    r = rec(cat, "sharepoint://contoso.sharepoint.com/drives/b!docs")
    assert r["title"] == "Tickets" and r["size"] == 3 and len(records(cat)) == 2


# ---------------------------------------------------------------- least privilege, secrets

@pytest.mark.parametrize("role", ["Sites.Read.All", "Files.ReadWrite.All"])
def test_tenant_wide_permission_is_refused(role):
    http = FakeGraph()
    with pytest.raises(Refused) as e:
        cn.SharePoint(SP_SITE, fake_token("Sites.Selected", role), http).scan({})
    assert role in e.value.reasons[0] and "Sites.Selected" in e.value.reasons[0]
    assert http.calls == []


def test_token_without_sites_selected_is_refused():
    with pytest.raises(Refused, match="lacks Sites.Selected"):
        cn.SharePoint(SP_SITE, fake_token("User.Read.All"), FakeGraph())


def test_no_search_endpoint_called(sp, cat):
    scan(cat, "support-site", http=sp)
    assert sp.calls and not any("search" in u.lower() for u in sp.calls)
    with pytest.raises(Refused, match="Graph Search is forbidden"):
        cn.GraphHttp("t").get(f"{cn.GRAPH}/search/query")


def test_secret_reference_forms(tmp_path, monkeypatch):
    monkeypatch.setenv("X_PW", "pw1")
    (tmp_path / "s").write_text("pw2\n")
    assert cn.resolve_secret("env:X_PW") == "pw1" and cn.resolve_secret(f"file:{tmp_path}/s") == "pw2"
    for bad in ("pw1", "vault:x", "env:NOPE_NOT_SET"):
        with pytest.raises(Refused) as e:
            cn.resolve_secret(bad)
        assert "pw1" not in str(e.value.reasons)


def test_sql_locator_with_inline_password_refused():
    with pytest.raises(Refused, match="secret reference"):
        cn.Sql("postgresql+psycopg://reader:hunter2@db/crm")


def _pg_crm(pg_server, name="crm"):
    import psycopg

    with psycopg.connect(pg_server, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        c.execute(f'CREATE DATABASE "{name}"')
    base, _, query = pg_server.partition("?")
    url = f"{base.rsplit('/', 1)[0]}/{name}" + (f"?{query}" if query else "")
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("CREATE TABLE tickets (id int primary key, customer text, body text)")
        c.execute("INSERT INTO tickets VALUES (1, 'kunde@example.org', 'Rechnung fehlt'), (2, 'b@example.org', 'x')")
        c.execute("CREATE TABLE attachments (id int, ticket_id int, filename text)")
        c.execute("CREATE VIEW open_tickets AS SELECT id, body FROM tickets")
    return url.replace("postgresql://", "postgresql+psycopg://"), url


@pytest.mark.postgres
def test_secret_never_persisted(engagement, cli, pg_server, monkeypatch, tmp_path, capsys):
    locator, _ = _pg_crm(pg_server)
    secret = "S3cret-Value-For-Test-only"
    monkeypatch.setenv("CRM_DB_PASSWORD", secret)
    monkeypatch.setenv("KONMARI_LINEAGE_FILE", str(tmp_path / "lineage.jsonl"))
    approve_pilot(cli, [{"id": "crm-db", "connector": "sql", "locator": locator, "categories": ["customer-records"]}])
    add_source(cli, "sql", locator, "--secret-ref", "env:CRM_DB_PASSWORD")
    code, out, err = cli("inventory", "scan", "--as", "dirk")
    assert code == 0, err
    cat = connect()
    dump = dump_catalog(cat)
    assert "env:CRM_DB_PASSWORD" in dump and secret not in dump
    cat.close()
    blobs = [out, err, (tmp_path / "lineage.jsonl").read_text()]
    blobs += [p.read_bytes().decode("latin-1") for p in tmp_path.glob("catalog.duckdb*")]
    assert all(secret not in b for b in blobs)


# ---------------------------------------------------------------- connectors

def test_record_created_for_a_document_library(sp, cat):
    scan(cat, "support-site", http=sp)
    rows = [r for r in records(cat) if r["title"] == "Shared Documents"]
    assert len(rows) == 1
    r = rows[0]
    assert r["asset_uri"] == "sharepoint://contoso.sharepoint.com/drives/b!docs" and r["system_type"] == "sharepoint"
    assert r["format"] == "text/csv" and r["size"] == 3 and str(r["last_modified"]) == "2026-02-01 09:30:00"
    assert len(audits(cat, "inventory.asset.create", r["asset_uri"])) == 1


def test_deleted_library_marked_missing(sp, cat):
    """Graph delta: the deleted facet on a file shrinks the library; on the root it marks the library missing."""
    scan(cat, "support-site", http=sp)
    sp.phase("scan2")
    scan(cat, "support-site", http=sp)
    assert f"{cn.GRAPH}/drives/b!docs/root/delta?token=d1" in sp.calls  # delta link reused
    docs = rec(cat, "sharepoint://contoso.sharepoint.com/drives/b!docs")
    assert docs["size"] == 2 and docs["status"] != "missing"
    pol = rec(cat, "sharepoint://contoso.sharepoint.com/drives/b!pol")
    assert pol["status"] == "missing" and pol["missing_since"] is not None
    assert len(audits(cat, "inventory.asset.missing", pol["asset_uri"])) == 1


def _sqlite_crm(path):
    import sqlite3

    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE tickets (id INTEGER PRIMARY KEY, customer TEXT, body TEXT)")
        c.executemany("INSERT INTO tickets VALUES (?, ?, ?)", [(1, "a@example.org", "Hallo"), (2, None, "x")])
        c.execute("CREATE TABLE contacts (id INTEGER, name TEXT)")
        c.execute("CREATE VIEW open_tickets AS SELECT id FROM tickets")


def test_sql_connector_lists_tables_and_views_sqlite(tmp_path):
    _sqlite_crm(tmp_path / "crm.db")
    assets, state = cn.Sql(f"sqlite:///{tmp_path}/crm.db").scan({})
    by = {a.title: a for a in assets}
    assert set(by) == {"crm.main.tickets", "crm.main.contacts", "crm.main.open_tickets"}
    t = by["crm.main.tickets"]
    assert t.format == "table" and t.size == 2 and [f["name"] for f in t.fields] == ["id", "customer", "body"]
    assert by["crm.main.open_tickets"].size is None
    again, _ = cn.Sql(f"sqlite:///{tmp_path}/crm.db").scan(state)
    assert not any(a.changed for a in again)  # unchanged schema hash: no resampling


@pytest.mark.postgres
def test_sql_connector_on_postgres_fixture_db(pg_server):
    locator, url = _pg_crm(pg_server)
    assets, state = cn.Sql(locator).scan({})
    by = {a.title: a for a in assets}
    assert set(by) == {"crm.public.tickets", "crm.public.attachments", "crm.public.open_tickets"}
    assert by["crm.public.tickets"].size == 2 and by["crm.public.tickets"].schema_hash
    import psycopg

    with psycopg.connect(url, autocommit=True) as c:
        c.execute("ALTER TABLE attachments ADD COLUMN size int")
    again = {a.title: a for a in cn.Sql(locator).scan(state)[0]}
    assert again["crm.public.attachments"].changed and not again["crm.public.tickets"].changed
    with pytest.raises(Exception, match="read-only"):
        with cn.Sql(locator).engine.connect() as c:
            from sqlalchemy import text

            c.execute(text("DELETE FROM tickets"))


@pytest.mark.postgres
def test_lineage_event_emitted_for_postgres_table(engagement, cli, pg_server, cat):
    locator, _ = _pg_crm(pg_server)
    approve_pilot(cli, [{"id": "crm-db", "connector": "sql", "locator": locator, "categories": ["customer-records"]}])
    add_source(cli, "sql", locator)
    mem = lineage.MemoryTransport()
    scan(cat, "crm-db", transport=mem)
    ev = next(e for e in mem.events if e["dataset"]["name"] == "crm.public.tickets")
    assert ev["dataset"]["facets"]["schema"]["fields"][0] == {"name": "id", "type": "INTEGER"}
    inv.set_fields(cat, sc_(cat), "sql://localhost/crm/public/tickets", {"owner": "ben"}, "ben", transport=mem)
    assert mem.events[-1]["dataset"]["facets"]["ownership"]["owners"] == [{"name": "ben", "type": "owner"}]


def test_lineage_event_emitted(pilot, cli, cat):
    _sqlite_crm(pilot["db"])
    add_source(cli, "sql", pilot["sqlite"])
    mem = lineage.MemoryTransport()
    scan(cat, "crm-db", transport=mem)
    uri = "sql://localhost/crm/main/tickets"
    inv.set_fields(cat, sc_(cat), uri, {"owner": "Ben Muster"}, "ben", transport=mem)
    ev = mem.events[-1]
    assert ev["dataset"]["name"] == "crm.main.tickets" and ev["dataset"]["namespace"] == "sql://localhost"
    assert ev["dataset"]["facets"]["ownership"]["owners"] == [{"name": "Ben Muster", "type": "owner"}]
    assert {f["name"] for f in ev["dataset"]["facets"]["schema"]["fields"]} == {"id", "customer", "body"}
    assert ev["schemaURL"].endswith("#/definitions/DatasetEvent")


def _tree(root):
    for rel, text in {"Vertraege/Mietvertrag.txt": "Mietvertrag Halle", "Vertraege/2025/Pacht.txt": "Pacht",
                      "Handbuch/faq.md": "# FAQ", "readme.txt": "Bitte lesen"}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_fileshare_connector_on_temp_tree(tmp_path):
    _tree(tmp_path)
    assets, state = cn.FileShare(str(tmp_path)).scan({})
    by = {a.title: a for a in assets}
    assert set(by) == {"Vertraege", "Handbuch", tmp_path.name}  # folder subtrees plus loose root files
    v = by["Vertraege"]
    assert v.size == 2 and v.format == "text/plain" and v.uri == f"file://{tmp_path}/Vertraege"
    assert v.last_modified is not None and v.content_hash
    again = {a.title: a for a in cn.FileShare(str(tmp_path)).scan(state)[0]}
    assert not any(a.changed for a in again.values())
    (tmp_path / "Handbuch" / "faq.md").write_text("# FAQ v2")
    os.utime(tmp_path / "Handbuch" / "faq.md", (1, 1))
    again = {a.title: a for a in cn.FileShare(str(tmp_path)).scan(state)[0]}
    assert again["Handbuch"].changed and not again["Vertraege"].changed


# ---------------------------------------------------------------- read-only guards (2.7)

def test_sharepoint_connector_is_read_only(monkeypatch):
    """The real GraphHttp transport against the fixture: only GET requests, never a write."""
    fx = FakeGraph()

    class Resp:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return self.body

    def fake_urlopen(req, timeout=None):
        assert req.get_method() == "GET", f"write call {req.get_method()} {req.full_url}"
        assert req.data is None and req.get_header("Authorization", "").startswith("Bearer ")
        return Resp(fx.get(req.full_url))

    monkeypatch.setattr(cn, "urlopen", fake_urlopen)
    assets, _ = cn.SharePoint(SP_SITE, fake_token("Sites.Selected")).scan({})
    assert len(assets) == 2 and list(assets[0].samples(5))
    assert len(fx.calls) >= 5


def test_sql_connector_is_read_only(tmp_path):
    from sqlalchemy import event, text
    from sqlalchemy.engine import Engine

    _sqlite_crm(tmp_path / "crm.db")
    seen = []
    listener = lambda conn, cur, stmt, *a: seen.append(stmt)  # noqa: E731
    event.listen(Engine, "before_cursor_execute", listener)
    try:
        s = cn.Sql(f"sqlite:///{tmp_path}/crm.db")
        for a in s.scan({})[0]:
            list(a.samples(10))
        with pytest.raises(Refused, match="read-only"), s.engine.connect() as c:
            c.execute(text("UPDATE tickets SET body = 'x'"))
    finally:
        event.remove(Engine, "before_cursor_execute", listener)
    reads = [q for q in seen if not q.lstrip().upper().startswith("UPDATE")]
    assert reads and all(q.lstrip().lower().startswith(("select", "pragma")) for q in reads)


def test_fileshare_connector_is_read_only(tmp_path, monkeypatch):
    _tree(tmp_path)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    real_open = builtins.open

    def guarded_open(file, mode="r", *a, **kw):
        if str(file).startswith(str(tmp_path)) and any(c in mode for c in "wax+"):
            raise AssertionError(f"write to share: {file} {mode}")
        return real_open(file, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", guarded_open)
    for fn in ("remove", "unlink", "rename", "replace", "chmod", "mkdir", "rmdir", "utime"):
        monkeypatch.setattr(os, fn, lambda *a, _fn=fn, **k: (_ for _ in ()).throw(AssertionError(f"os.{_fn}")))
    for a in cn.FileShare(str(tmp_path)).scan({})[0]:
        list(a.samples(10))
    monkeypatch.undo()
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


# ---------------------------------------------------------------- records, rescans

def test_rescan_does_not_duplicate(pilot, cli, cat):
    _tree(pilot["share"])
    add_source(cli, "fileshare", str(pilot["share"]))
    scan(cat, "office-share")
    first = {r["asset_uri"]: r for r in records(cat)}
    scan(cat, "office-share")
    second = {r["asset_uri"]: r for r in records(cat)}
    assert set(first) == set(second) and len(second) == 3
    for uri, r in second.items():
        assert r["first_seen"] == first[uri]["first_seen"] and r["last_seen"] > first[uri]["last_seen"]
        assert {k: v for k, v in r.items() if k != "last_seen"} == {k: v for k, v in first[uri].items()
                                                                    if k != "last_seen"}
    assert len(audits(cat, "inventory.asset.create")) == 3 and len(audits(cat, "inventory.scan")) == 2


def test_reappearing_asset_restored(pilot, cli, cat):
    _tree(pilot["share"])
    add_source(cli, "fileshare", str(pilot["share"]))
    scan(cat, "office-share")
    uri = f"file://{pilot['share']}/Handbuch"
    inv.set_fields(cat, sc_(cat), uri, {"owner": "ben", "update_frequency": "P1M"}, "ben")
    inv.set_tier(cat, sc_(cat), uri, "ben")
    assert rec(cat, uri)["status"] == "complete"
    shutil.move(pilot["share"] / "Handbuch", pilot["tmp"] / "Handbuch")
    scan(cat, "office-share")
    r = rec(cat, uri)
    assert r["status"] == "missing" and r["missing_since"] and r["previous_status"] == "complete"
    shutil.move(pilot["tmp"] / "Handbuch", pilot["share"] / "Handbuch")
    scan(cat, "office-share")
    r = rec(cat, uri)
    assert r["status"] == "complete" and r["missing_since"] is None
    assert len(audits(cat, "inventory.asset.restore", uri)) == 1


def _write_import(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _row(i, **kw):
    return {"asset_uri": f"crm://saas/objects/obj{i:02d}", "title": f"CRM object {i}", "system_type": "crm",
            "owner": "Ben Muster", "steward": "Clara Probe", "update_frequency": "P1D", "format": "table",
            "size": "100", "sensitivity_tier": "internal", "personal_data_categories": "",
            "access_rights_note": "office volunteers read", **kw}


def test_partially_invalid_import(pilot, cli, cat):
    rows = [_row(i) for i in range(1, 11)]
    rows[5]["update_frequency"] = "weekly-ish"  # file line 7 (line 1 is the header)
    _write_import(pilot["csv"], rows)
    add_source(cli, "csv-import", str(pilot["csv"]))
    code, out, err = cli("inventory", "import", pilot["csv"], "--as", "ben")
    assert code == 1 and "created 9" in out
    assert "line 7: invalid update_frequency 'weekly-ish'" in err and err.count("error:") == 1
    recs = records(cat)
    assert len(recs) == 9 and {r["origin"] for r in recs} == {"manual"}
    assert len(audits(cat, "inventory.import")) == 1


def test_yaml_import(engagement, cli, cat, tmp_path):
    y = tmp_path / "manual.yaml"
    approve_pilot(cli, [{"id": "manual-y", "connector": "csv-import", "locator": str(y),
                         "categories": ["customer-records"]}])
    y.write_text("- {asset_uri: 'crm://a', title: A, owner: ben, update_frequency: continuous, format: table,"
                 " sensitivity_tier: personal, personal_data_categories: [contact]}\n- {title: no uri}\n")
    add_source(cli, "csv-import", str(y))
    counts, errors = inv.import_file(cat, sc_(cat), str(y), "ben")
    assert counts["created"] == 1 and errors == ["record 2: missing required 'asset_uri'"]
    assert json.loads(rec(cat, "crm://a")["personal_data_categories"]) == ["contact"]


# ---------------------------------------------------------------- owner rule, completeness gate

def _manual(cat, n, **kw):
    assets = [Asset(uri=f"crm://x/{i}", title=f"X {i}", system_type="crm", format="table", changed=False,
                    meta={"owner": "Ben Muster", "update_frequency": "P1W", "sensitivity_tier": "internal",
                          "tier_state": "confirmed", **kw}) for i in range(n)]
    return inv.apply_assets(cat, sc_(cat), {"source_id": "manual-crm"}, assets, "ben", origin="manual")


def test_group_owner_keeps_record_incomplete(pilot, cat):
    _manual(cat, 1)
    r = inv.set_fields(cat, sc_(cat), "crm://x/0", {"owner": "support-team@example.org"}, "ben")
    assert r["status"] == "incomplete"
    assert json.loads(r["missing_fields"]) == [{"field": "owner", "reason": "owner must be a named individual"}]
    [a] = audits(cat, "inventory.asset.set", "crm://x/0")
    assert a["d"]["previous"] == {"owner": "Ben Muster"} and a["d"]["new"] == {"owner": "support-team@example.org"}


@pytest.mark.parametrize("owner,ok", [("ben", True), ("Ben Muster", True), ("ben.muster@example.org", True),
                                      ("info@example.org", False), ("Ben, Clara", False), ("IT Team", False)])
def test_owner_must_be_single_named_individual(owner, ok):
    assert inv.is_named_individual(owner, {"ben": {}}) is ok


def test_all_fields_set_record_complete(pilot, cat):
    _manual(cat, 1)
    r = rec(cat, "crm://x/0")
    assert r["status"] == "complete" and json.loads(r["missing_fields"]) == []


def test_handoff_blocked_by_gaps(pilot, cli, cat):
    _manual(cat, 40)
    for i in (3, 17):
        inv.set_fields(cat, sc_(cat), f"crm://x/{i}", {"update_frequency": "unknown"}, "ben")
    code, out, err = cli("inventory", "report", "--gate")
    assert code == 1
    assert "incomplete: crm://x/3 (update_frequency: update_frequency is not set)" in err
    assert "crm://x/17" in err and err.count("incomplete: ") == 2


def test_waiver_allows_handoff(pilot, cli, cat):
    _manual(cat, 2)
    inv.set_fields(cat, sc_(cat), "crm://x/1", {"owner": "unknown"}, "ben")
    assert cli("inventory", "report", "--gate")[0] == 1
    assert cli("inventory", "waive", "crm://x/1", "--reason", "owner named at next board meeting", "--as", "finn")[0] == 1
    assert cli("inventory", "waive", "crm://x/1", "--reason", "owner named at next board meeting", "--as", "ben")[0] == 0
    code, out, _ = cli("inventory", "report", "--gate")
    assert code == 0 and "inventory gate passed" in out
    [a] = audits(cat, "inventory.waive", "crm://x/1")
    assert a["actor"] == "ben" and a["d"]["reason"] == "owner named at next board meeting"


# ---------------------------------------------------------------- sensitivity hints

def test_german_id_detected_without_storing_values(pilot, cli, cat, capsys):
    ids = [valid_tax_id(i) for i in range(12)]
    (pilot["share"] / "Mitglieder").mkdir()
    (pilot["share"] / "Mitglieder" / "steuer.csv").write_text(
        "name;steuer_id\n" + "".join(f"Mitglied {i};{t}\n" for i, t in enumerate(ids)), encoding="utf-8")
    add_source(cli, "fileshare", str(pilot["share"]))
    code, out, err = cli("inventory", "scan", "--as", "dirk")
    assert code == 0, err
    uri = f"file://{pilot['share']}/Mitglieder"
    hints = {h["entity_type"]: h for h in cat.query("SELECT * FROM sensitivity_hint WHERE asset_uri = ?", (uri,))}
    assert hints["DE_TAX_ID"]["hit_count"] == 12 and hints["DE_TAX_ID"]["sample_size"] == 200
    assert rec(cat, uri)["sensitivity_tier"] == "personal"
    dump = dump_catalog(cat) + out + err + pilot["lineage"].read_text()
    cat.close()
    dump += "".join(p.read_bytes().decode("latin-1") for p in pilot["tmp"].glob("catalog.duckdb*"))
    assert not [t for t in ids if t in dump]


def test_no_outbound_call(pilot, cli, cat, monkeypatch):
    def blocked(*a, **k):
        raise OSError("network egress blocked by test")

    (pilot["share"] / "Kontakte").mkdir()
    (pilot["share"] / "Kontakte" / "liste.txt").write_text(f"IBAN {valid_iban(1)}, Mail max@example.org")
    add_source(cli, "fileshare", str(pilot["share"]))
    sensitivity._analyzer.cache_clear()
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    scan(cat, "office-share")
    hints = {h["entity_type"] for h in cat.query("SELECT entity_type FROM sensitivity_hint")}
    assert {"IBAN_CODE", "EMAIL_ADDRESS"} <= hints


def test_missing_model_is_refused_without_download(monkeypatch):
    with pytest.raises(Refused, match="not installed"):
        sensitivity.analyze(["x"], model="de_core_news_does_not_exist")


def test_postal_code_counts_only_with_address_context():
    assert "DE_PLZ" not in sensitivity.analyze(["Im Jahr 14542 kostete es 10115 Euro."])
    assert sensitivity.analyze(["Anschrift: Musterstraße 5, 14542 Werder"])["DE_PLZ"]["count"] == 1


def test_hints_below_threshold_discarded():
    text = [f"Steuer {valid_tax_id(3)}"]
    assert "PHONE_NUMBER" not in sensitivity.analyze(text)  # Presidio scores it 0.4
    assert "PHONE_NUMBER" in sensitivity.analyze(text, threshold=0.3)


def _hint_asset(cat, text, uri="file://x/a"):
    asset = Asset(uri=uri, title="a", system_type="fileshare", format="text/plain", samples=lambda n: [text])
    inv.apply_assets(cat, sc_(cat), {"source_id": "office-share"}, [asset], "dirk")
    return rec(cat, uri)


def test_proposal_awaits_confirmation(pilot, cat):
    r = _hint_asset(cat, f"Versichertennummer {valid_kvnr(5)}")
    assert (r["sensitivity_tier"], r["tier_state"]) == ("special-category", "proposed")
    assert r["status"] == "incomplete" and any(m["field"] == "sensitivity_tier" for m in json.loads(r["missing_fields"]))
    assert json.loads(r["personal_data_categories"]) == ["health"]
    with pytest.raises(Refused, match="neither the owner"):
        inv.set_tier(cat, sc_(cat), r["asset_uri"], "finn")
    r = inv.set_tier(cat, sc_(cat), r["asset_uri"], "eva")  # compliance confirms
    assert r["tier_state"] == "confirmed"
    assert not any(m["field"] == "sensitivity_tier" for m in json.loads(r["missing_fields"]))
    assert len(audits(cat, "inventory.tier.confirm", r["asset_uri"])) == 1


def test_owner_override_is_audited(pilot, cat):
    r = _hint_asset(cat, "Kontakt: max.muster@example.org")
    assert r["sensitivity_tier"] == "personal"
    inv.set_fields(cat, sc_(cat), r["asset_uri"], {"owner": "ben"}, "ben")
    with pytest.raises(Refused, match="needs a reason"):
        inv.set_tier(cat, sc_(cat), r["asset_uri"], "ben", "internal")
    r = inv.set_tier(cat, sc_(cat), r["asset_uri"], "ben", "internal", "test data, synthetic")
    assert (r["sensitivity_tier"], r["tier_state"]) == ("internal", "confirmed")
    [a] = audits(cat, "inventory.tier.override", r["asset_uri"])
    assert a["actor"] == "ben" and a["d"]["reason"] == "test data, synthetic"
    assert a["d"]["previous"] == {"tier": "personal", "state": "proposed"}
    assert a["d"]["new"] == {"tier": "internal", "state": "confirmed"}


def test_proposal_never_below_hints(pilot, cat):
    r = _hint_asset(cat, "Nur Trainingszeiten")
    assert (r["sensitivity_tier"], r["tier_state"]) == ("internal", "proposed")
    inv.set_tier(cat, sc_(cat), r["asset_uri"], "eva")
    r = _hint_asset(cat, f"Neu: Steuer-ID {valid_tax_id(9)}")
    assert (r["sensitivity_tier"], r["tier_state"]) == ("personal", "proposed")


def test_missing_ropa_surfaced(pilot, cli, cat):
    _manual(cat, 2)
    inv.set_fields(cat, sc_(cat), "crm://x/0", {"ropa_missing": True}, "eva")
    cat.execute("UPDATE source_asset SET sensitivity_tier = 'personal'")
    inv.set_fields(cat, sc_(cat), "crm://x/1", {"ropa_ref": "VVT-07"}, "eva")
    out = cli("inventory", "report")[1]
    section = out.split("## RoPA gaps for compliance")[1].split("##")[0]
    assert "crm://x/0 (X 0), tier personal, owner: Ben Muster (flagged ropa_missing)" in section
    assert "crm://x/1" not in section


def test_german_pii_fixture_precision_recall(capsys):
    """Labelled synthetic German fixture; precision/recall are printed as the regression baseline."""
    cases = []
    for i in range(10):
        cases += [(f"Steuer-ID des Mitglieds: {valid_tax_id(i)}", {"DE_TAX_ID": 1}),
                  (f"Bitte auf {valid_iban(i)} überweisen.", {"IBAN_CODE": 1}),
                  (f"Versichertennummer {valid_kvnr(i)} liegt vor.", {"DE_HEALTH_INSURANCE": 1}),
                  (f"Rückfragen an person{i}@example.org", {"EMAIL_ADDRESS": 1}),
                  (f"Anschrift: Lindenstraße {i + 1}, 1454{i} Werder", {"DE_PLZ": 1}),
                  (f"Training am {i + 1}. März ab 18 Uhr, Beitrag 1454{i} Cent im Jahr.", {})]
    tp = fp = fn = 0
    for text, want in cases:
        got = {k: v["count"] for k, v in sensitivity.analyze([text]).items()}
        for et in set(want) | set(got):
            tp += min(want.get(et, 0), got.get(et, 0))
            fp += max(0, got.get(et, 0) - want.get(et, 0))
            fn += max(0, want.get(et, 0) - got.get(et, 0))
    precision, recall = tp / (tp + fp), tp / (tp + fn)
    print(f"PII baseline (blank de pipeline, threshold 0.5): precision {precision:.2f}, recall {recall:.2f}, "
          f"n={len(cases)}")
    assert precision >= 0.95 and recall >= 0.95


# ---------------------------------------------------------------- report, DCAT

def test_report_totals_are_consistent(pilot, cli, cat, tmp_path):
    _manual(cat, 40)
    for i in range(4):
        inv.set_fields(cat, sc_(cat), f"crm://x/{i}", {"owner": "unknown"}, "ben")
    code, out, _ = cli("inventory", "report", "--csv", tmp_path / "inv.csv")
    assert code == 0 and "Completeness: 90 %" in out and "complete 36, incomplete 4, missing 0" in out
    assert "| internal | 40 |" in out
    with open(tmp_path / "inv.csv", encoding="utf-8") as f:
        assert len(list(csv.DictReader(f))) == 40


def test_dcat_export_contains_fields(pilot, cli, cat, tmp_path):
    _manual(cat, 1, steward="Clara Probe", access_rights_note="office volunteers, read only")
    cat.execute("UPDATE source_asset SET last_modified = TIMESTAMP '2026-01-05 10:00:00'")
    code, _, _ = cli("inventory", "export", "-o", tmp_path / "dcat.jsonld")
    doc = json.loads((tmp_path / "dcat.jsonld").read_text())
    [d] = doc["@graph"]
    assert code == 0 and doc["@context"]["dcat"] == "http://www.w3.org/ns/dcat#"
    assert d["dcterms:accrualPeriodicity"] == "P1W" and d["dcterms:modified"] == "2026-01-05T10:00:00"
    assert d["dcterms:format"] == "table" and d["dcat:contactPoint"] == "Clara Probe"
    assert d["dcterms:accessRights"] == "office volunteers, read only" and d["dcterms:publisher"] == "Ben Muster"


def test_csv_import_template_is_valid():
    tpl = Path(__file__).parent.parent / "examples" / "verein-faq-assistant" / "exports" / "fee-rules.csv"
    assets, errors = inv.parse_import(tpl)
    assert errors == [] and len(assets) == 2
