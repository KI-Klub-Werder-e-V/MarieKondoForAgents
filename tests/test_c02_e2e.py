"""c02 end to end (task 7.4): SharePoint, SQL and file-share sources run scan -> confirm -> assign -> approve
order, and the gates pass. SharePoint answers from the recorded Graph fixture through the real GET transport."""

import json
import sqlite3
from pathlib import Path

import pytest

from konmari.categories import require_step
from konmari.catalog import connect
from konmari.inventory import connectors as cn
from konmari.inventory import store as inv

from conftest import SP_SITE, fake_token

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "graph" / "support-site.json").read_text())["scan1"]


class _Resp:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def read(self):
        return self.body


def _flow(pilot, cli, monkeypatch):
    def urlopen(req, timeout=None):
        assert req.get_method() == "GET"
        body = FIXTURE[req.full_url]
        return _Resp((body if isinstance(body, str) else json.dumps(body)).encode())

    monkeypatch.setattr(cn, "urlopen", urlopen)
    monkeypatch.setenv("SP_TOKEN", fake_token("Sites.Selected"))
    for rel, text in {"Vertraege/Mietvertrag.txt": "Mietvertrag Halle", "Handbuch/faq.md": "# FAQ"}.items():
        (pilot["share"] / rel).parent.mkdir(parents=True, exist_ok=True)
        (pilot["share"] / rel).write_text(text)
    with sqlite3.connect(pilot["db"]) as c:
        c.execute("CREATE TABLE tickets (id INTEGER, body TEXT)")
        c.execute("CREATE TABLE contacts (id INTEGER, name TEXT)")

    def ok(*args):
        code, out, err = cli(*args)
        assert code == 0, (args, err)
        return out

    ok("inventory", "source", "add", "--connector", "sharepoint", "--locator", SP_SITE, "--secret-ref",
       "env:SP_TOKEN", "--as", "dirk")
    ok("inventory", "source", "add", "--connector", "sql", "--locator", pilot["sqlite"], "--as", "dirk")
    ok("inventory", "source", "add", "--connector", "fileshare", "--locator", pilot["share"], "--as", "dirk")
    ok("inventory", "scan", "--as", "dirk")
    cat = connect()
    uris = [r["asset_uri"] for r in inv.records(cat, "verein-faq-assistant")]
    assert {u.split(":")[0] for u in uris} == {"sharepoint", "sql", "file"} and len(uris) == 6
    assert cli("inventory", "report", "--gate")[0] == 1  # owners and tiers still open
    assert cli("categories", "handoff")[0] == 1
    for u in uris:
        ok("inventory", "set", u, "--owner", "ben", "--update-frequency", "P1M", "--as", "ben")
        ok("inventory", "tier", u, "--confirm", "--as", "ben")
    ok("inventory", "report", "--gate")
    assert "6 suggestion(s)" in ok("categories", "suggest", "--as", "ben")
    for u in uris:
        ok("categories", "accept", u, "--as", "ben")
    print(ok("categories", "order", "--propose", "--as", "ben"))
    ok("categories", "approve", "--as", "ben")
    seq = ok("categories", "handoff")
    assert seq.startswith("category sequence: ") and "kb-and-sops" in seq
    require_step("kb-and-sops", "score", "charter.yaml", cat)
    report = ok("inventory", "report")
    assert "Completeness: 100 %" in report and "## RoPA gaps for compliance" in report
    cat.close()


def test_fixture_pilot_end_to_end_duckdb(pilot, cli, monkeypatch):
    _flow(pilot, cli, monkeypatch)


@pytest.mark.postgres
def test_fixture_pilot_end_to_end_postgres(pg_url, pilot, cli, monkeypatch):
    _flow(pilot, cli, monkeypatch)
