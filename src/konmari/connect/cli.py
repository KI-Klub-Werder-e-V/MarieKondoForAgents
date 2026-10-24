"""`konmari product ...` and `konmari serve` (c08)."""

import json
import os
import sys
from pathlib import Path

from konmari import versions as v
from konmari.catalog import connect, os_user
from konmari.connect import descriptor as dsc
from konmari.connect import products as prd
from konmari.connect import refresh as rf
from konmari.connect import server as srv
from konmari.connect import tools as tls
from konmari.versions import Refused


def _ctx(a, cat):
    from konmari.cli import _scope

    sc = _scope(a, cat)
    row = v.latest_approved(cat, "charter_version", sc.charter_id)
    return sc, row["doc"]


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def product_cmd(a, cat_url):
    cat = connect(cat_url)
    op = a.op
    if op == "init":
        d = Path(a.dir or f"products/{a.product}")
        if (d / dsc.PRODUCT_FILE).exists():
            raise Refused(f"{d / dsc.PRODUCT_FILE} exists; edit it instead")
        dsc.write(dsc.skeleton(a.product, a.name or a.product, a.category, a.owner, a.use_case), d)
        print(f"wrote {d / dsc.PRODUCT_FILE} and {d / dsc.CONTRACT_FILE}; fill in every TODO, then "
              f"'konmari product lint {d}'")
        return 0
    if op == "list":
        rows = prd.listing(cat)
        if a.json:
            _out(rows)
        else:
            for r in rows:
                callable_, tools = ", ".join(r["callable_versions"]) or "-", ", ".join(r["tools"]) or "-"
                print(f"{r['id']}: current {r['current_version'] or '-'}, callable {callable_}, owner {r['owner']}, "
                      f"SLA {r['freshness_sla']}, as_of {r['as_of'] or '-'} ({r['freshness']}), last refresh "
                      f"{r['last_refresh'] or '-'}, tools {tools}")
        return 0
    if op == "schedule":
        cur = prd.current(cat, a.product) or (prd.versions(cat, a.product) or [None])[-1]
        if cur is None:
            raise Refused(f"unknown data product '{a.product}'")
        s = rf.schedule(cur["desc"], a.catalog)
        print(s["cron"] if a.format == "cron" else f"# {s['unit']}.service\n{s['service']}\n# {s['unit']}.timer\n"
                                                   f"{s['timer']}")
        return 0
    if op == "diff":
        res = prd.diff(cat, dsc.info(dsc.load(a.dir))["id"], dsc.load(a.dir))
        _out(res)
        return 0 if res["ok"] else 1
    sc, charter = _ctx(a, cat)
    actor = getattr(a, "as_", None) or os_user()
    if op == "lint":
        desc = dsc.load(a.dir)
        errs = dsc.lint(desc, cat, sc, charter)
        if errs:
            raise Refused(errs)
        row = prd.register(cat, sc, desc, actor)
        print(f"{row['product_id']} {row['version']}: descriptor valid ({row['state']}, hash {row['content_hash'][:12]})")
        return 0
    if op == "test":
        version = a.version or _latest(cat, a.product)
        res = prd.run_tests(cat, a.product, version, actor=actor)
        for f in res["failures"]:
            print(f"FAILED {f['rule']} on {f['field'] or 'table'}: {f['violations']} violation(s)", file=sys.stderr)
        print(f"{a.product} {version}: {'passed' if res['passed'] else 'failed'} on {res['row_count']} row(s)"
              f" (test {res['test_id']})")
        return 0 if res["passed"] else 1
    if op == "approve":
        row = prd.approve(cat, sc, a.product, a.version, a.as_)
        print(f"{a.product} {a.version}: approved by {a.as_}")
        return 0 if row else 1
    if op == "publish":
        if a.rollback:
            row = prd.rollback(cat, sc, a.product, a.as_, a.reason)
            print(f"{a.product}: rolled back, {row['version']} is current again")
            return 0
        if not a.version:
            raise Refused("--version is required (or --rollback)")
        row = prd.publish(cat, sc, a.product, a.version, a.as_, days=_cfg(a)["deprecation_days"])
        print(f"{a.product} {row['version']}: published; review the tools with 'konmari serve --check-pins'")
        return 0
    if op == "deprecate":
        row = prd.deprecate(cat, sc, a.product, a.version, a.as_, a.successor, a.days)
        print(f"{a.product} {a.version}: deprecated, sunset {row['sunset_at']}")
        return 0
    if op == "refresh":
        ids = [p["product_id"] for p in cat.query("SELECT product_id FROM data_product ORDER BY product_id")] \
            if a.product == "all" else [a.product]
        bad = 0
        for pid in ids:
            r = rf.refresh(cat, pid, actor)
            bad += r["status"] != "ok"
            print(f"{pid}: {r['status']} (+{r['inserted']} ~{r['updated']} -{r['deleted']}, as_of {r['as_of']})"
                  + (f": {r['error']}" if r["error"] else ""))
        return 1 if bad else 0
    if op == "export":
        token = os.environ.get("KONMARI_OPENMETADATA_TOKEN")
        if not token:
            raise Refused("set KONMARI_OPENMETADATA_TOKEN (an OpenMetadata bot token) for the export")
        _out(prd.export_openmetadata(cat, a.product, a.openmetadata, token))
        return 0
    raise Refused(f"unknown operation {op}")


def _latest(cat, pid: str) -> str:
    vs = prd.versions(cat, pid)
    if not vs:
        raise Refused(f"unknown data product '{pid}'")
    return vs[-1]["version"]


def _cfg(a) -> dict:
    cfg = srv.load_config(getattr(a, "config", None))
    cfg.setdefault("deprecation_days", prd.DEPRECATION_DAYS)
    return cfg


def _validator(http: dict):
    from konmari.connect.auth import JwksValidator
    from konmari.governance.access import IntrospectionValidator

    if http.get("jwks_url"):
        return JwksValidator(http["issuer"], jwks_url=http["jwks_url"])
    if intro := http.get("introspection"):
        secret = os.environ.get(intro.get("secret_env", "KONMARI_INTROSPECTION_SECRET"), "")
        return IntrospectionValidator(intro["url"], intro["client_id"], secret)
    raise Refused("connect.yaml http: set jwks_url (+ issuer) or introspection {url, client_id, secret_env}")


def serve_cmd(a, cat_url):
    from konmari.connect import mcp_app

    cat = connect(cat_url)
    from konmari.cli import _scope

    sc, cfg = _scope(a, cat), _cfg(a)
    lim = cfg["limits"]
    if a.print_tools:
        _out([d for d, _ in tls.generated(cat, lim["default_records"], lim["max_records"])])
        return 0
    if a.check_pins:
        rows = tls.check(cat, lim["default_records"], lim["max_records"])
        for r in rows:
            print(f"{r['tool']}: {r['status']}" + ("" if r["status"] == "ok" else
                                                    f" (approve with --approve-tool {r['tool']})"))
        return 0 if all(r["status"] == "ok" for r in rows) else 1
    if a.approve_tool:
        if not a.as_:
            raise Refused("--approve-tool needs --as <reviewer>")
        r = tls.approve(cat, sc, a.approve_tool, a.as_, lim["default_records"], lim["max_records"])
        print(f"{r['tool']}: approved by {r['approved_by']} (sha256 {r['hash'][:16]}...)")
        return 0
    core = srv.ProductServer(cat, sc, cfg, log=mcp_app.log_stderr)
    core.list_tools()  # logs pin mismatches at start-up
    if a.http:
        http = cfg.get("http") or {}
        if not http.get("resource") or not http.get("authorization_servers"):
            raise Refused("connect.yaml http: resource (canonical URI) and authorization_servers are required")
        mcp_app.serve_http(core, _validator(http), http["resource"], http["authorization_servers"], a.host, a.port,
                           a.tls_cert, a.tls_key)
        return 0
    mcp_app.serve_stdio(core)
    return 0


def register(groups, ccmd) -> None:
    pr = groups.add_parser("product", help="data products: descriptor, tests, versions, publish, refresh, catalog")
    ps = pr.add_subparsers(dest="cmd", required=True)

    def pcmd(name, help, actor="optional"):
        q = ccmd(ps, name, product_cmd, help, actor)
        q.set_defaults(op=name)
        return q

    q = pcmd("init", "write an ODPS product + ODCS contract skeleton", actor=None)
    q.add_argument("product")
    q.add_argument("--name")
    q.add_argument("--category", required=True)
    q.add_argument("--owner", required=True, help="accountable owner (stakeholder ID)")
    q.add_argument("--use-case", dest="use_case", required=True, help="charter use case name")
    q.add_argument("--dir", help="default: products/<id>")
    q = pcmd("lint", "validate schemas, mandatory elements and upstream steps; register the draft")
    q.add_argument("dir", help="product directory with product.yaml and contract.yaml")
    q = pcmd("test", "contract tests on the materialised data (exit 1 on a failed rule)")
    q.add_argument("product")
    q.add_argument("--version")
    q = pcmd("diff", "compare with the published version: breaking? required semver bump", actor=None)
    q.add_argument("dir")
    q = pcmd("approve", "owner approval of a draft version", actor="required")
    q.add_argument("product")
    q.add_argument("--version", required=True)
    q = pcmd("publish", "publish an approved, tested version (or --rollback)", actor="required")
    q.add_argument("product")
    q.add_argument("--version")
    q.add_argument("--rollback", action="store_true", help="make the previous version current again")
    q.add_argument("--reason")
    q.add_argument("--config", default="connect.yaml")
    q = pcmd("deprecate", "deprecate a version with successor and sunset date", actor="required")
    q.add_argument("product")
    q.add_argument("--version", required=True)
    q.add_argument("--successor")
    q.add_argument("--days", type=int, default=prd.DEPRECATION_DAYS)
    q = pcmd("list", "the product catalog", actor=None)
    q.add_argument("--json", action="store_true")
    q = pcmd("refresh", "incremental, contract-tested refresh (product ID or 'all')")
    q.add_argument("product")
    q = pcmd("schedule", "cron line or systemd timer derived from the freshness SLA", actor=None)
    q.add_argument("product")
    q.add_argument("--format", choices=["cron", "systemd"], default="cron")
    q = pcmd("export", "optional: send the current descriptor to OpenMetadata", actor=None)
    q.add_argument("product")
    q.add_argument("--openmetadata", required=True, metavar="URL")

    q = ccmd(groups, "serve", serve_cmd, "MCP server: published data products as read-only tools and resources",
             actor="optional")
    mode = q.add_mutually_exclusive_group()
    mode.add_argument("--stdio", action="store_true", help="local agent host (default); identity from KONMARI_*")
    mode.add_argument("--http", action="store_true", help="Streamable HTTP with OAuth 2.1 (connect.yaml http)")
    q.add_argument("--host", default="127.0.0.1")
    q.add_argument("--port", type=int, default=8765)
    q.add_argument("--tls-cert", dest="tls_cert")
    q.add_argument("--tls-key", dest="tls_key")
    q.add_argument("--config", default="connect.yaml", help="connect settings (default: connect.yaml)")
    q.add_argument("--print-tools", action="store_true", help="print the generated tool definitions")
    q.add_argument("--check-pins", action="store_true", help="exit 1 if a tool definition is not approved")
    q.add_argument("--approve-tool", metavar="NAME", help="approve the current definition of one tool")
