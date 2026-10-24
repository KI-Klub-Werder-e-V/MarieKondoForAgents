"""c08 end to end (task 8.1): fixture source -> lint -> refresh -> test -> approve -> publish -> tool review, all
through the CLI; then an MCP client lists and calls the tools over STDIO (subprocess `konmari serve --stdio`) and
over Streamable HTTP with a test OIDC issuer, and gets fresh, audited, masked results. Same run on Postgres.
Task 8.2: the official MCP Inspector against both pinned protocol revisions (opt-in, needs npx and network)."""

import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time

import anyio
import httpx2
import pytest
from mcp import Client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

import c08_fixture as fx
from konmari.canonical import dump_yaml
from konmari.catalog import connect, verify_chain
from konmari.connect import auth, mcp_app
from konmari.connect import descriptor as dsc
from konmari.connect import server as srv

STDIO_ENV = {"KONMARI_AGENT": "faq-bot", "KONMARI_ON_BEHALF_OF": "finn", "KONMARI_GROUPS": "support-team",
             "KONMARI_SCOPES": "product:support-faq:read", "KONMARI_CLIENT_ID": "stdio-host"}


def connect_step(c, engagement, cli):
    def ok(*args):
        code, out, err = cli(*args)
        assert code == 0, (args, err)
        return out
    ok("charter", "approve", "--as", "anna")
    dsc.write(fx.faq_desc(c["loc"]), engagement / "products" / "support-faq")
    ok("product", "lint", "products/support-faq", "--as", "ben")
    assert "support-faq: ok (+12 ~0 -0" in ok("product", "refresh", "support-faq", "--as", "ben")
    ok("product", "test", "support-faq")
    ok("product", "approve", "support-faq", "--version", "1.0.0", "--as", "ben")
    ok("product", "publish", "support-faq", "--version", "1.0.0", "--as", "ben")
    assert cli("serve", "--check-pins")[0] == 1  # new tools wait for review
    for t in ("support_faq.v1.get", "support_faq.v1.search"):
        ok("serve", "--approve-tool", t, "--as", "clara")
    ok("serve", "--check-pins")
    listed = json.loads(ok("product", "list", "--json"))
    assert listed[0]["callable_versions"] == ["1.0.0"] and listed[0]["freshness"] == "fresh"


async def _exercise(client):
    tools = [t.name for t in (await client.list_tools()).tools]
    res = await client.call_tool("support_faq.v1.search", {"keywords": "Mitgliedsbeitrag", "topic": "fees",
                                                           "limit": 3})
    cat_body = (await client.read_resource("konmari://catalog")).contents[0].text
    return client.protocol_version, tools, res, json.loads(cat_body)


def _assert_result(tools, res, catalog):
    assert tools == ["support_faq.v1.get", "support_faq.v1.search"]
    s = res.structured_content
    assert not res.is_error and s["freshness"] == "fresh" and s["version"] == "1.0.0" and len(s["records"]) == 3
    assert all(r["author_email"].startswith("<EMAIL_ADDRESS_") for r in s["records"])
    assert "@example.org" not in res.content[0].text
    assert catalog[0]["id"] == "support-faq"


def _stdio(engagement, catalog_url, mode):
    params = StdioServerParameters(command=sys.executable, args=["-m", "konmari.cli", "serve", "--stdio"],
                                   env={**os.environ, "KONMARI_CATALOG": catalog_url, **STDIO_ENV},
                                   cwd=str(engagement))

    async def main():
        async with Client(params, mode=mode) as c:
            return await _exercise(c)
    return anyio.run(main)


class HttpServer:
    """uvicorn in a thread on a free localhost port, serving the gated MCP app."""

    def __init__(self, core, issuer):
        import uvicorn

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        self.resource = f"http://127.0.0.1:{self.port}/mcp"
        app = mcp_app.http_app(core, auth.JwksValidator(fx.ISSUER, jwks=issuer.jwks), self.resource, [fx.ISSUER])
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(200):
            if self.server.started:
                return self
            time.sleep(0.05)
        raise RuntimeError("HTTP server did not start")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(10)


def _http(core, mode):
    issuer = fx.Issuer()
    with HttpServer(core, issuer) as h:
        tok = issuer.token(h.resource)

        async def main():
            async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {tok}"}, timeout=30) as hc:
                async with Client(streamable_http_client(h.resource, http_client=hc), mode=mode) as c:
                    return await _exercise(c)
        return anyio.run(main)


def _run_end_to_end(c, engagement, cli, url):
    connect_step(c, engagement, cli)
    c["cat"].close()  # DuckDB: the STDIO server process needs the file lock
    for mode, want in (("2026-07-28", "2026-07-28"), ("legacy", "2025-11-25")):
        version, tools, res, catalog = _stdio(engagement, url, mode)
        assert version == want
        _assert_result(tools, res, catalog)
    cat = connect(url)
    core = srv.ProductServer(cat, fx.scope())
    for mode, want in (("2026-07-28", "2026-07-28"), ("legacy", "2025-11-25")):
        version, tools, res, catalog = _http(core, mode)
        assert version == want
        _assert_result(tools, res, catalog)
    calls = fx.events(cat, "agent.tool_call")
    searches = [e["details"] for e in calls if e["details"]["tool"] == "support_faq.v1.search"]
    assert len(searches) == 4 and all(e["records"] == 3 and e["on_behalf_of"] == "finn" for e in searches)
    assert {e["client_id"] for e in searches} == {"stdio-host", "test-client"}
    assert all("Mitgliedsbeitrag" not in json.dumps(e) for e in searches)
    assert verify_chain(cat) is None
    return cat


def test_connect_step_end_to_end(c08, engagement, cli):
    cat = _run_end_to_end(c08, engagement, cli, c08["url"])
    cat.close()


@pytest.mark.postgres
def test_connect_step_on_postgres(pg_url, engagement, cli, tmp_path, monkeypatch):
    monkeypatch.setenv("KONMARI_CATALOG", pg_url)
    monkeypatch.setenv("KONMARI_LINEAGE_FILE", str(tmp_path / "lineage.jsonl"))
    cat = connect(pg_url)
    fx.governed(cat, fx.scope())
    c = {"cat": cat, "loc": fx.source_db(tmp_path / "source.db")}
    cat = _run_end_to_end(c, engagement, cli, pg_url)
    assert cat.one("SELECT COUNT(*) AS n FROM product_row")["n"] == 12
    cat.close()


# ---------------------------------------------------------------- MCP Inspector (task 8.2)

INSPECTOR = "@modelcontextprotocol/inspector@2.12.0"


@pytest.mark.skipif(not os.environ.get("KONMARI_TEST_INSPECTOR") or not shutil.which("npx"),
                    reason="opt-in: KONMARI_TEST_INSPECTOR=1 and npx (downloads the official MCP Inspector)")
@pytest.mark.parametrize("pinned", ["2026-07-28", "2025-11-25"])
def test_mcp_inspector_conformance(c08, engagement, cli, pinned):
    connect_step(c08, engagement, cli)
    c08["cat"].close()
    # each run serves exactly one revision, so the Inspector must speak the pinned one
    (engagement / "connect.yaml").write_text(dump_yaml({"protocol_version": pinned,
                                                        "fallback_protocol_version": None}))
    # the PII detector version (part of the c07 gate) depends on the NLP model settings of the test session
    nlp = {k: os.environ[k] for k in ("KONMARI_NLP_MODEL", "KONMARI_NLP_MODEL_EN")}
    env = [x for k, v in {**STDIO_ENV, **nlp, "KONMARI_CATALOG": c08["url"]}.items() for x in ("-e", f"{k}={v}")]
    server = [sys.executable, "-m", "konmari.cli", "serve", "--stdio"]
    out = {}
    for method, extra in (("tools/list", []), ("resources/list", []),
                          ("tools/call", ["--tool-name", "support_faq.v1.get", "--tool-arg", "id=faq-001"])):
        era = "modern" if pinned == "2026-07-28" else "legacy"
        p = subprocess.run(["npx", "-y", INSPECTOR, "--cli", "--protocol-era", era, *env, "--method", method, *extra,
                            "--", *server],
                           cwd=engagement, capture_output=True, text=True, timeout=180)
        assert p.returncode == 0, (method, p.stdout[-2000:], p.stderr[-2000:])
        out[method] = json.loads(p.stdout)
    assert [t["name"] for t in out["tools/list"]["tools"]] == ["support_faq.v1.get", "support_faq.v1.search"]
    assert out["tools/call"]["structuredContent"]["records"][0]["id"] == "faq-001"
    assert any(r["uri"] == "konmari://catalog" for r in out["resources/list"]["resources"])
