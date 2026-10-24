"""access-control at request time (design D1-D4): token audience check, agent + on-behalf-of identity, agent scopes
intersected with user data permissions, retrieval with the permission filter inside the query (Postgres RLS on a
transaction-scoped principal context; the same predicate as SQL on DuckDB), fail-closed audited tool calls."""

import base64
import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from konmari.catalog import Catalog, now
from konmari.governance import acl as gacl
from konmari.governance import policy as pol
from konmari.governance.audit import AuditUnavailable, span, write_event
from konmari.versions import Refused

_SCOPE = re.compile(r"^(read|write):([a-z0-9][a-z0-9-]*)$")


class AccessDenied(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(f"HTTP {status}: {reason}")
        self.status, self.reason = status, reason


@dataclass
class Context:
    agent: str
    on_behalf_of: str | None           # None = autonomous agent (service principal)
    principals: list[str]
    categories: list[str]              # read scopes of the agent
    write_categories: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- agents and scopes

def validate_scopes(scopes: list[str], categories) -> list[str]:
    """Least privilege: one operation and one category per scope; wildcards and omnibus scopes are refused."""
    errs = []
    for s in scopes:
        if pol.wildcard(s):
            errs.append(f"scope '{s}' is a wildcard or omnibus scope; request read:<category> or write:<category>")
        elif not _SCOPE.match(s):
            errs.append(f"scope '{s}' is not read:<category> or write:<category>")
        elif s.split(":", 1)[1] not in categories:
            errs.append(f"scope '{s}' names a category outside the pilot scope")
    return errs


def register_agent(cat: Catalog, sc, agent: str, scopes: list[str], actor: str, service_principal=False) -> dict:
    if errs := validate_scopes(scopes, sc.categories):
        raise Refused(errs)
    cat.execute("DELETE FROM agent_client WHERE agent_id = ?", (agent,))
    cat.execute("INSERT INTO agent_client VALUES (?, ?, ?, ?, ?)",
                (agent, json.dumps(sorted(scopes)), service_principal, actor, now()))
    write_event(cat, "govern.agent.register", actor=actor, charter_id=sc.charter_id, agent=agent,
                scopes=sorted(scopes), service_principal=service_principal)
    return agent_client(cat, agent)


def agent_client(cat: Catalog, agent: str) -> dict | None:
    r = cat.one("SELECT * FROM agent_client WHERE agent_id = ?", (agent,))
    if r:
        r["scopes"] = json.loads(r["scopes"])
    return r


def context(cat: Catalog, agent: str, user: str | None, groups=()) -> Context:
    """Effective access = agent scopes ∩ user data permissions; a service principal sees explicit grants only."""
    client = agent_client(cat, agent)
    if client is None:
        raise AccessDenied(403, "unknown_agent")
    reads = [s.split(":", 1)[1] for s in client["scopes"] if s.startswith("read:")]
    writes = [s.split(":", 1)[1] for s in client["scopes"] if s.startswith("write:")]
    if user is None:
        if not client["service_principal"]:
            raise AccessDenied(401, "missing_on_behalf_of")
        principals = [f"service:{agent}"]
    else:
        principals = gacl.normalize_acl([f"user:{user}", *[f"group:{g}" for g in groups], "group:everyone"])["allow"]
    if any("," in p for p in principals):
        raise AccessDenied(400, "invalid_principal")
    return Context(agent, user, principals, reads, writes)


# ---------------------------------------------------------------- tokens (OAuth 2.1, RFC 8693 `act`)

def _b64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class HmacValidator:
    """HS256 JWT check for local runs and tests. shortcut: production uses IntrospectionValidator (RFC 7662) against
    the organization's OIDC server (Keycloak, Authentik, Entra ID), which also covers RS256 signing keys."""

    def __init__(self, secret: str):
        self.secret = secret.encode()

    def __call__(self, token: str) -> dict:
        try:
            head, body, sig = token.split(".")
            want = hmac.new(self.secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
            if json.loads(_b64(head)).get("alg") != "HS256" or not hmac.compare_digest(want, _b64(sig)):
                raise ValueError
            claims = json.loads(_b64(body))
        except ValueError:
            raise AccessDenied(401, "invalid_token") from None
        if claims.get("exp") and claims["exp"] < time.time():
            raise AccessDenied(401, "token_expired")
        return claims


class IntrospectionValidator:
    """RFC 7662 token introspection: POST the token to the authorization server, read the claims."""

    def __init__(self, url: str, client_id: str, client_secret: str, urlopen_=urlopen):
        self.url, self.auth, self.urlopen = url, base64.b64encode(f"{client_id}:{client_secret}".encode()), urlopen_

    def __call__(self, token: str) -> dict:
        req = Request(self.url, data=urlencode({"token": token}).encode(),
                      headers={"Authorization": f"Basic {self.auth.decode()}",
                               "Content-Type": "application/x-www-form-urlencoded"})
        with self.urlopen(req, timeout=10) as r:
            claims = json.loads(r.read())
        if not claims.get("active"):
            raise AccessDenied(401, "invalid_token")
        return claims


def sign_hs256(claims: dict, secret: str) -> str:
    enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    head, body = enc({"alg": "HS256", "typ": "JWT"}), enc(claims)
    sig = hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{base64.urlsafe_b64encode(sig).decode().rstrip('=')}"


def from_token(cat: Catalog, token: str, validator, audience: str) -> Context:
    """Only tokens issued for the KonMari interface are accepted (RFC 8707 audience); they are never passed on.
    Delegation: `sub` = user, `act.sub` = agent. Without `act`, `sub` must be a service-principal agent."""
    agent = user = None
    try:
        claims = validator(token)
        aud = claims.get("aud")
        if audience not in (aud if isinstance(aud, list) else [aud]):
            raise AccessDenied(401, "invalid_audience")
        if act := claims.get("act"):
            agent, user = act.get("sub"), claims.get("sub")
        else:
            agent = claims.get("sub")
        ctx = context(cat, agent, user, claims.get("groups") or [])
    except AccessDenied as e:
        write_event(cat, "agent.request", actor=agent or "unknown", result="deny", agent=agent,
                    on_behalf_of=user or "none", decision="deny", reason=e.reason)
        raise
    return ctx


# ---------------------------------------------------------------- audited tool calls

def invoke(ctx: Context):
    """The `invoke_agent` span around one agent request; tool calls inside it become its `execute_tool` children."""
    return span("invoke_agent", ctx.agent, **{"gen_ai.agent.id": ctx.agent})


def _event(cat, ctx: Context, tool, operation, ids, decision, reason, versions, trace_id):
    write_event(cat, "agent.tool_call", actor=ctx.agent, result=decision, trace_id=trace_id, agent=ctx.agent,
                on_behalf_of=ctx.on_behalf_of or "none", tool=tool, operation=operation, resource_ids=ids,
                decision=decision, reason=reason, policy_versions=versions)


def tool_call(cat: Catalog, ctx: Context, tool: str, operation: str, fn):
    """Run `fn() -> (data, resource_ids)` and audit it; no data is returned unless the audit event is persisted."""
    versions = {c: v for c, v in pol.active_versions(cat).items() if c in ctx.categories + ctx.write_categories}
    with span("execute_tool", tool, **{"gen_ai.tool.name": tool, "gen_ai.agent.id": ctx.agent}) as s:
        trace_id = f"{s.get_span_context().trace_id:032x}" if s.get_span_context().is_valid else None
        try:
            data, ids = fn()
        except AccessDenied as e:
            s.set_attribute("error.type", e.reason)
            try:
                _event(cat, ctx, tool, operation, [], "deny", e.reason, versions, trace_id)
            except Exception as err:
                raise AuditUnavailable(f"audit write failed: {err}") from err
            raise
        try:
            _event(cat, ctx, tool, operation, ids, "allow", None, versions, trace_id)
        except Exception as err:
            s.set_attribute("error.type", "audit_unavailable")
            raise AuditUnavailable(f"audit write failed; tool call refused: {err}") from err
        return data


def _rows(cat: Catalog, ctx: Context, categories: list[str], item_ids: list[str] | None) -> list[dict]:
    cols = "chunk_id, item_id, category_id, content, sensitivity, allowed_principals, denied_principals"
    extra = f" AND item_id IN ({', '.join('?' * len(item_ids))})" if item_ids else ""
    if item_ids is not None and not item_ids:
        return []
    if cat.dialect == "postgres":  # the filter is the RLS policy; no WHERE on principals here
        with cat.transaction():
            cat.execute("SET LOCAL ROLE konmari_agent")
            cat.execute("SELECT set_config('app.principals', ?, true), set_config('app.categories', ?, true)",
                        (",".join(ctx.principals), ",".join(categories)))
            return cat.query(f"SELECT {cols} FROM agent_index WHERE true{extra}", tuple(item_ids or ()))
    return cat.query(f"SELECT {cols} FROM agent_index WHERE list_has_any(allowed_principals, ?) AND NOT"
                     f" list_has_any(denied_principals, ?) AND list_contains(?, category_id){extra}",
                     (ctx.principals, ctx.principals, categories, *(item_ids or ())))


def _score(query: str, text: str) -> int:
    words = set(re.findall(r"\w+", query.lower()))
    return len(words & set(re.findall(r"\w+", text.lower())))


def retrieve(cat: Catalog, ctx: Context, query: str, k: int = 5, tool: str = "search", prefilter=None) -> list[dict]:
    """Best matching chunks the context may read. Sources with an overdue ACL sync are blocked.
    shortcut: lexical overlap ranking; c08/c09 plug in the embedding search on the same filtered rows."""
    def run():
        cats = [c for c in ctx.categories if not gacl.stale(cat, c)]
        ids = prefilter(ctx) if prefilter else None
        rows = sorted(_rows(cat, ctx, cats, ids), key=lambda r: (-_score(query, r["content"]), r["chunk_id"]))
        hits = [r for r in rows if _score(query, r["content"]) > 0][:k]
        return hits, [r["chunk_id"] for r in hits]
    return tool_call(cat, ctx, tool, "read", run)


def write(cat: Catalog, ctx: Context, tool: str, category: str, fn):
    """A write tool: needs a write scope and an active, owner-approved, unexpired grant; else 403 + deny event."""
    def run():
        if category not in ctx.write_categories:
            raise AccessDenied(403, "no_write_scope")
        if pol.active_grant(cat, ctx.agent, category, tool) is None:
            expired = cat.one("SELECT 1 AS x FROM write_grant WHERE agent_id = ? AND category_id = ? AND tool = ?",
                              (ctx.agent, category, tool))
            raise AccessDenied(403, "grant_expired" if expired else "no_write_grant")
        return fn()
    return tool_call(cat, ctx, tool, "write", run)


def permitted(row: dict, ctx: Context) -> bool:
    """Independent post-retrieval check (test assertion only, design D1)."""
    p = set(ctx.principals)
    return bool(p & set(row["allowed_principals"])) and not p & set(row["denied_principals"] or []) \
        and row["category_id"] in ctx.categories


# ---------------------------------------------------------------- OpenFGA (optional ReBAC pre-filter)

def openfga_prefilter(cfg: dict):
    """The OpenFGA pre-filter when `openfga.enabled` is set in governance.yaml, else None."""
    f = cfg.get("openfga") or {}
    return OpenFga(f["url"], f["store_id"], f.get("model_id")) if f.get("enabled") else None


class OpenFga:
    """`ListObjects` -> authorized document IDs as an extra filter (config `openfga.enabled`)."""

    def __init__(self, url: str, store_id: str, model_id: str | None = None, urlopen_=urlopen):
        self.url, self.store, self.model, self.urlopen = url.rstrip("/"), store_id, model_id, urlopen_

    def __call__(self, ctx: Context) -> list[str]:
        body = {"type": "document", "relation": "viewer", "user": f"user:{ctx.on_behalf_of or ctx.agent}"}
        if self.model:
            body["authorization_model_id"] = self.model
        req = Request(f"{self.url}/stores/{self.store}/list-objects", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
        with self.urlopen(req, timeout=10) as r:
            return [o.split(":", 1)[1] for o in json.loads(r.read()).get("objects", [])]
