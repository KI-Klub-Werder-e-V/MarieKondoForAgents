"""The governed MCP server core (`konmari serve`, design D6-D11): only callable, gate-approved, pinned products;
c07 access control and masking on every call and read; input validation, record caps with pagination, per-identity
rate limit and timeout; freshness in every result; one fail-closed audit event per call (c07 `agent.tool_call`)."""

import base64
import json
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from jsonschema import Draft202012Validator

from konmari.catalog import Catalog
from konmari.charter.store import Scope
from konmari.connect import descriptor as dsc
from konmari.connect import products as prd
from konmari.connect import tools as tls
from konmari.governance import access
from konmari.governance import pii
from konmari.governance import policy as pol
from konmari.governance.access import AccessDenied

PROTOCOL, FALLBACK = "2026-07-28", "2025-11-25"
DEFAULTS = {"protocol_version": PROTOCOL, "fallback_protocol_version": FALLBACK,
            "limits": {"default_records": tls.DEFAULT_CAP, "max_records": tls.MAX_CAP, "calls_per_minute": 60,
                       "timeout_seconds": 10}}
URI = re.compile(r"^konmari://(catalog|products/([a-z0-9][a-z0-9-]*)/(\d+\.\d+\.\d+)(/docs)?)$")


class UnknownTool(LookupError):
    """Unknown tool name: a JSON-RPC protocol error, not a data access."""


class ResourceNotFound(LookupError):
    pass


class ToolError(AccessDenied):
    """A refused call: `reason` goes to the audit trail (no values), `message` to the client."""

    def __init__(self, status: int, reason: str, message: str):
        super().__init__(status, reason)
        self.message = message


@dataclass
class Identity:
    ctx: access.Context
    scopes: list[str] = field(default_factory=list)   # product:<id>:read
    client_id: str | None = None

    @property
    def key(self) -> str:
        return f"{self.ctx.agent}|{self.ctx.on_behalf_of or 'none'}"


def load_config(path=None) -> dict:
    from pathlib import Path

    from konmari.canonical import load_yaml

    cfg = json.loads(json.dumps(DEFAULTS))
    p = Path(path or "connect.yaml")
    doc = (load_yaml(p) or {}) if p.is_file() else {}
    for k, v in doc.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    lim = cfg["limits"]
    if not 1 <= lim["default_records"] <= lim["max_records"] <= tls.MAX_CAP:
        raise ValueError(f"connect.yaml limits: need 1 <= default_records <= max_records <= {tls.MAX_CAP}")
    return cfg


def _cursor(offset: int, version: str) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset, "v": version}).encode()).decode()


def _offset(cursor: str | None, version: str) -> int:
    if not cursor:
        return 0
    try:
        c = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        if c["v"] == version and isinstance(c["o"], int) and c["o"] >= 0:
            return c["o"]
    except (ValueError, KeyError, TypeError):
        pass
    raise ToolError(400, "invalid_cursor", "cursor is not a next_cursor of this tool; start without a cursor")


class ProductServer:
    def __init__(self, cat: Catalog, sc: Scope, cfg: dict | None = None, clock=time.monotonic, exposable=None,
                 log=None, gate_ttl: float = 30.0):
        from konmari.governance import gate

        self.cat, self.sc, self.cfg, self.clock = cat, sc, cfg or load_config("does-not-exist.yaml"), clock
        self._gate = exposable or (lambda: gate.exposable(cat, sc))
        self._gate_cache, self.gate_ttl = None, gate_ttl
        self.calls: dict[str, deque] = defaultdict(deque)
        self.log = log or (lambda msg: None)
        self.lim = self.cfg["limits"]

    # ------------------------------------------------------------ what is exposed

    def exposable(self) -> list[dict]:
        """c07 `gate.exposable`, computed live; cached for `gate_ttl` seconds because it re-verifies the audit chain.
        A blocked source therefore disappears from the server within that time."""
        if self._gate_cache is None or self.clock() - self._gate_cache[0] >= self.gate_ttl:
            self._gate_cache = (self.clock(), self._gate())
        return self._gate_cache[1]

    def _generated(self) -> list[tuple[dict, dict]]:
        return tls.generated(self.cat, self.lim["default_records"], self.lim["max_records"])

    def exposed(self) -> list[tuple[dict, dict]]:
        """Callable versions of gate-approved categories whose tool definition matches its approved pin."""
        ok_categories = {r["category_id"] for r in self.exposable()}
        pins, out = tls.pins(self.cat), []
        for d, v in self._generated():
            if dsc.info(v["desc"])["category"] not in ok_categories:
                continue
            pin = pins.get(d["name"])
            if pin is None or pin["def_hash"] != tls.def_hash(d):
                self.log(f"tool pin {'missing' if pin is None else 'mismatch'}: {d['name']} is not exposed until "
                         f"a reviewer runs 'konmari serve --approve-tool {d['name']}'")
                continue
            out.append((d, v))
        return out

    def list_tools(self) -> list[dict]:
        return [d for d, _ in self.exposed()]

    def product_of(self, tool_or_uri: str) -> str | None:
        """For the HTTP scope check: the product a tool name or resource URI belongs to."""
        if m := URI.match(tool_or_uri or ""):
            return m[2]
        base = (tool_or_uri or "").rsplit(".", 2)[0]
        return next((p["product_id"] for p in self.cat.query("SELECT product_id FROM data_product")
                     if p["product_id"].replace("-", "_") == base), None)

    # ------------------------------------------------------------ calls

    def _rate(self, ident: Identity) -> None:
        q, t = self.calls[ident.key], self.clock()
        while q and t - q[0] >= 60:
            q.popleft()
        if len(q) >= self.lim["calls_per_minute"]:
            wait = int(60 - (t - q[0])) + 1
            raise ToolError(429, "rate_limited", f"rate limit of {self.lim['calls_per_minute']} calls per minute "
                                                 f"reached; retry after {wait} seconds")
        q.append(t)

    def _deadline(self, start: float) -> None:
        if self.clock() - start > self.lim["timeout_seconds"]:
            raise ToolError(504, "timeout", f"the call took longer than {self.lim['timeout_seconds']} s; narrow "
                                            "the search or retry later")

    def _not_callable(self, name: str) -> str | None:
        """A known tool of a withdrawn or sunset version: say so instead of pretending it never existed."""
        for v in prd.versions_in(self.cat, ("withdrawn", "sunset")):
            if any(d["name"] == name for d in tls.definitions(v)):
                live = [x for x in prd.callable_versions(self.cat) if x["product_id"] == v["product_id"]
                        and dsc.major(x["version"]) == dsc.major(v["version"])]
                if not live:
                    return f"version {v['version']} of data product {v['product_id']} is {v['state']}"
        return None

    def _rows(self, ident: Identity, v: dict) -> list[dict]:
        rows = []
        p = set(ident.ctx.principals)
        for r in self.cat.query("SELECT row_key, data, acl FROM product_row WHERE product_id = ? ORDER BY row_key",
                                (v["product_id"],)):
            a = json.loads(r["acl"])
            if p & set(a.get("allow") or []) and not p & set(a.get("deny") or []):  # c07: ACL travels with the row
                rows.append({"key": r["row_key"], **json.loads(r["data"])})
        return rows

    def _project(self, desc: dict, rows: list[dict]) -> tuple[list[dict], dict]:
        """Contract output fields only; personal fields masked per the approved source policy (c07)."""
        i = dsc.info(desc)
        p = pol.active(self.cat, i["masking_policy"])
        rules = (p or {}).get("doc", {}).get("masking") or {}
        flds = dsc.fields(desc)
        masking = {f["name"]: rules.get(f["pii_entity"], "replace-with-type") for f in flds if tls.masked(desc, f)}
        k = pii.key(self.cat) if masking else b""
        out = []
        for r in rows:
            rec = {}
            for f in flds:
                val = r.get(f["name"])
                if f["name"] in masking and val is not None:
                    s, ent = str(val), f["pii_entity"] or "PII"
                    val = pii.apply_mask(s, [{"start_pos": 0, "end_pos": len(s), "entity_type": ent}],
                                         {ent: masking[f["name"]]}, k)
                rec[f["name"]] = val
            out.append(rec)
        return out, {"masking": masking, "policy": p and f"{p['policy_id']}:v{p['version']}", "row_filter": "acl"}

    def call(self, ident: Identity, name: str, args: dict | None) -> dict:
        args = args or {}
        found = next(((d, v) for d, v in self.exposed() if d["name"] == name), None)
        if found is None:
            if msg := self._not_callable(name):
                return self._error(ident, name, None, args, ToolError(410, "version_not_callable", msg))
            raise UnknownTool(f"unknown tool '{name}'")
        d, v = found
        pid, start, extra = v["product_id"], self.clock(), {}
        extra.update(client_id=ident.client_id, product=pid, version=v["version"], argument_names=sorted(args),
                     records=0, outcome="error")

        def run():
            self._rate(ident)
            i = dsc.info(v["desc"])
            if f"product:{pid}:read" not in ident.scopes:
                raise ToolError(403, "insufficient_scope", f"scope product:{pid}:read is required")
            if i["category"] not in ident.ctx.categories:
                raise ToolError(403, "category_not_granted",
                                f"agent {ident.ctx.agent} has no read:{i['category']} grant")
            errs = sorted(Draft202012Validator(d["inputSchema"]).iter_errors(args), key=lambda e: e.path)
            if errs:
                bad = [e.message for e in errs]
                raise ToolError(400, "invalid_arguments", "invalid arguments: " + "; ".join(bad))
            rows = self._rows(ident, v)
            self._deadline(start)
            if name.endswith(".get"):
                rows = [r for r in rows if str(r.get(i["key"])) == str(args[i["key"]])]
                offset, limit = 0, 1
            else:
                text_fields = [f["name"] for f in dsc.fields(v["desc"]) if f["type"] == "string"
                               and not tls.masked(v["desc"], f)]
                for f, want in args.items():
                    if f not in ("keywords", "limit", "cursor"):
                        rows = [r for r in rows if r.get(f) == want]
                words = re.findall(r"\w+", (args.get("keywords") or "").casefold())
                rows = [r for r in rows if all(w in " ".join(str(r.get(t) or "") for t in text_fields).casefold()
                                               for w in words)]
                offset, limit = _offset(args.get("cursor"), v["version"]), args.get("limit",
                                                                                  self.lim["default_records"])
            page = rows[offset:offset + limit]
            more = offset + limit < len(rows)
            records, applied = self._project(v["desc"], page)
            self._deadline(start)
            ts = prd.as_of(self.cat, pid)
            result = {"product": pid, "version": v["version"], "as_of": ts and ts.isoformat() + "Z",
                      "freshness": prd.freshness(ts, i["sla"]), "records": records,
                      "next_cursor": _cursor(offset + limit, v["version"]) if more else None}
            if v["state"] == "deprecated":
                result["deprecation"] = {"successor": v["successor"],
                                         "sunset": v["sunset_at"] and v["sunset_at"].isoformat() + "Z"}
            extra.update(records=len(records), applied_policies=applied, outcome="ok",
                         duration_ms=int((self.clock() - start) * 1000))
            return result, [r["key"] for r in page]

        try:
            with access.invoke(ident.ctx):
                result = access.tool_call(self.cat, ident.ctx, name, "read", run, extra=extra)
        except ToolError as e:
            return {"isError": True, "text": e.message, "structured": None}
        return {"isError": False, "structured": result, "text": render(result, dsc.info(v["desc"])["sla"])}

    def _error(self, ident: Identity, name: str, pid, args: dict, err: ToolError) -> dict:
        def run():
            raise err
        try:
            access.tool_call(self.cat, ident.ctx, name, "read", run, extra={
                "client_id": ident.client_id, "product": pid, "argument_names": sorted(args), "records": 0,
                "outcome": "error"})
        except ToolError:
            pass
        return {"isError": True, "text": err.message, "structured": None}

    # ------------------------------------------------------------ resources

    def _resources(self, ident: Identity) -> dict[str, dict]:
        out = {"konmari://catalog": {"uri": "konmari://catalog", "name": "catalog", "title": "Data product catalog",
                                     "description": "Callable data products with version, purpose, owner, "
                                                    "freshness SLA and as_of", "mimeType": "application/json"}}
        newest = None
        for v in self._visible_versions(ident):
            i, ts = dsc.info(v["desc"]), prd.as_of(self.cat, v["product_id"])
            newest = max(filter(None, [newest, ts]), default=None)
            mod = {"lastModified": ts.isoformat() + "Z"} if ts else {}
            base = f"konmari://products/{v['product_id']}/{v['version']}"
            out[base] = {"uri": base, "name": f"{v['product_id']}@{v['version']}", "title": f"{i['name']} descriptor",
                         "description": f"ODPS product and ODCS contract of {i['id']} {v['version']}",
                         "mimeType": "application/json", "annotations": mod}
            out[base + "/docs"] = {"uri": base + "/docs", "name": f"{v['product_id']}@{v['version']}-docs",
                                   "title": f"{i['name']} documentation",
                                   "description": f"Purpose, fields and limits of {i['id']} {v['version']}",
                                   "mimeType": "text/markdown", "annotations": mod}
        if newest:
            out["konmari://catalog"]["annotations"] = {"lastModified": newest.isoformat() + "Z"}
        return out

    def _visible_versions(self, ident: Identity) -> list[dict]:
        ok_categories = {r["category_id"] for r in self.exposable()}
        return [v for v in prd.callable_versions(self.cat)
                if dsc.info(v["desc"])["category"] in ok_categories & set(ident.ctx.categories)
                and f"product:{v['product_id']}:read" in ident.scopes]

    def list_resources(self, ident: Identity) -> list[dict]:
        return list(self._resources(ident).values())

    def read_resource(self, ident: Identity, uri: str) -> tuple[str, str]:
        """(mimeType, text) of a listed resource; anything not in the published set is not found, unread."""
        res = self._resources(ident)
        if not URI.match(uri or "") or uri not in res:
            raise ResourceNotFound(f"resource not found: {uri}")

        def run():
            if uri == "konmari://catalog":
                body = json.dumps(self._catalog(ident), indent=2, ensure_ascii=False)
            else:
                m = URI.match(uri)
                v = prd.get(self.cat, m[2], m[3])
                body = docs(v["desc"]) if m[4] else json.dumps(v["desc"], indent=2, ensure_ascii=False)
            return body, [uri]

        with access.invoke(ident.ctx):
            body = access.tool_call(self.cat, ident.ctx, "resources/read", "read", run,
                                    extra={"client_id": ident.client_id, "resource": uri, "outcome": "ok"})
        return res[uri]["mimeType"], body

    def _catalog(self, ident: Identity) -> list[dict]:
        out = []
        for v in self._visible_versions(ident):
            i, ts = dsc.info(v["desc"]), prd.as_of(self.cat, v["product_id"])
            out.append({"id": i["id"], "version": v["version"], "state": v["state"], "purpose": i["purpose"],
                        "owner": i["owner"], "freshness_sla": i["sla"] and prd._iso_duration(i["sla"]),
                        "as_of": ts and ts.isoformat() + "Z", "freshness": prd.freshness(ts, i["sla"]),
                        "tools": [d["name"] for d in tls.definitions(v)]})
        return out


def render(result: dict, sla) -> str:
    """The text block: a summary line, a staleness warning, then the records as JSON data (never as prose)."""
    head = (f"{result['product']} {result['version']}: {len(result['records'])} record(s), data as of "
            f"{result['as_of'] or 'unknown'} ({result['freshness']}).")
    if result["freshness"] == "stale":
        head += (f" Warning: the data is older than its freshness SLA ({prd._iso_duration(sla) if sla else 'none'})"
                 " and may be outdated; say so when you use it.")
    if result.get("deprecation"):
        head += f" This major version is deprecated (successor {result['deprecation']['successor']})."
    return head + "\n" + json.dumps(result, ensure_ascii=False, sort_keys=True)


def docs(desc: dict) -> str:
    i = dsc.info(desc)
    lines = [f"# {i['name']} ({i['id']} {i['version']})", "", f"Purpose: {i['purpose']}", "",
             f"Usage: {i['usage'] or '-'}", "", f"Limitations: {i['limitations'] or '-'}", "",
             f"Owner: {i['owner']}. Freshness SLA: {prd._iso_duration(i['sla']) if i['sla'] else '-'}.", "",
             "| field | type | classification | definition |", "|---|---|---|---|"]
    lines += [f"| {f['name']} | {f['type']} | {f['classification']} | {f['description']} |" for f in dsc.fields(desc)]
    return "\n".join(lines) + "\n"
