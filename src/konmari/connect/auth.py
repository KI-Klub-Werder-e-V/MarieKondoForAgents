"""OAuth 2.1 resource server in front of the MCP endpoint (design D9, MCP authorization spec): RFC 9728 metadata,
401 with `resource_metadata`, signature/expiry/issuer via JWKS or RFC 7662 introspection, audience = the server's
canonical URI, no tokens in the query string, 403 `insufficient_scope` per product. Tokens are never passed on."""

import json
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

import jwt

from konmari.governance import access
from konmari.governance.access import AccessDenied

LOCALHOST = {"127.0.0.1", "localhost", "::1"}


class JwksValidator:
    """JWT validation against the authorization server's JWKS (RS256/ES256): signature, expiry and issuer.
    The audience is checked by `access.from_token` against the canonical resource URI."""

    def __init__(self, issuer: str, jwks: dict | None = None, jwks_url: str | None = None, urlopen_=urlopen,
                 cache_seconds: int = 300):
        self.issuer, self.url, self.urlopen, self.ttl = issuer, jwks_url, urlopen_, cache_seconds
        self._jwks, self._fetched = jwks, time.time() if jwks else 0.0

    def _keys(self) -> jwt.PyJWKSet:
        if self.url and time.time() - self._fetched > self.ttl:
            with self.urlopen(self.url, timeout=10) as r:
                self._jwks, self._fetched = json.loads(r.read()), time.time()
        return jwt.PyJWKSet.from_dict(self._jwks or {"keys": []})

    def __call__(self, token: str) -> dict:
        try:
            kid = jwt.get_unverified_header(token).get("kid")
            keys = self._keys()
            key = next((k for k in keys.keys if k.key_id == kid), None) if kid else (keys.keys or [None])[0]
            if key is None:
                raise jwt.InvalidTokenError("unknown signing key")
            return jwt.decode(token, key.key, algorithms=["RS256", "ES256", "PS256"], issuer=self.issuer,
                              options={"verify_aud": False, "require": ["exp", "iss"]})
        except jwt.ExpiredSignatureError:
            raise AccessDenied(401, "token_expired") from None
        except jwt.InvalidTokenError:
            raise AccessDenied(401, "invalid_token") from None


def check_bind(host: str, tls: bool) -> None:
    """Plain HTTP only on localhost; anything else needs TLS."""
    if not tls and host not in LOCALHOST:
        raise ValueError(f"refusing to serve plain HTTP on {host}: bind to 127.0.0.1 or give --tls-cert/--tls-key")


def metadata(resource: str, authorization_servers: list[str], scopes: list[str]) -> dict:
    return {"resource": resource, "authorization_servers": authorization_servers, "scopes_supported": scopes,
            "bearer_methods_supported": ["header"]}


def metadata_url(resource: str) -> str:
    u = urlsplit(resource)
    return f"{u.scheme}://{u.netloc}/.well-known/oauth-protected-resource{u.path.rstrip('/')}"


class AuthGate:
    """ASGI wrapper: authenticates every MCP request, checks the product scope of tools/call and resources/read,
    and hands the c07 identity to the MCP handlers in `request.state.konmari`."""

    def __init__(self, app, core, resource: str, authorization_servers: list[str], validator):
        self.app, self.core, self.resource, self.validator = app, core, resource.rstrip("/"), validator
        self.as_list = authorization_servers
        self.meta_path = urlsplit(metadata_url(self.resource)).path

    async def _send(self, send, status: int, body: dict, headers: dict | None = None) -> None:
        raw = json.dumps(body).encode()
        h = [(b"content-type", b"application/json"), (b"content-length", str(len(raw)).encode())]
        h += [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
        await send({"type": "http.response.start", "status": status, "headers": h})
        await send({"type": "http.response.body", "body": raw})

    async def _deny(self, send, status: int, error: str, description: str, scope: str | None = None) -> None:
        www = f'Bearer resource_metadata="{metadata_url(self.resource)}"'
        if error:
            www += f', error="{error}", error_description="{description}"'
        if scope:
            www += f', scope="{scope}"'
        await self._send(send, status, {"error": error or "unauthorized", "error_description": description},
                         {"www-authenticate": www})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"].startswith("/.well-known/oauth-protected-resource"):
            scopes = sorted({f"product:{p['product_id']}:read" for p in self.core.cat.query(
                "SELECT product_id FROM data_product")})
            return await self._send(send, 200, metadata(self.resource, self.as_list, scopes))
        if "access_token" in parse_qs(scope.get("query_string", b"").decode()):
            return await self._deny(send, 400, "invalid_request", "access tokens in the query string are refused")
        auth = dict(scope.get("headers") or []).get(b"authorization", b"").decode()
        if not auth.lower().startswith("bearer "):
            return await self._deny(send, 401, "", "a bearer token is required")
        token, claims = auth[7:].strip(), {}

        def validate(t):
            claims.update(self.validator(t))
            return claims
        try:
            ctx = access.from_token(self.core.cat, token, validate, audience=self.resource)
        except AccessDenied as e:
            return await self._deny(send, 401 if e.status == 401 else 403, "invalid_token", e.reason)
        granted = (claims.get("scope") or "").split()
        body = b""
        while True:  # read the JSON-RPC body once to find the product, then replay it to the MCP app
            msg = await receive()
            body += msg.get("body", b"")
            if not msg.get("more_body"):
                break
        try:
            req = json.loads(body) if body else {}
        except ValueError:
            req = {}
        for r in req if isinstance(req, list) else [req]:
            params = (r or {}).get("params") or {}
            target = params.get("name") if r.get("method") == "tools/call" else \
                params.get("uri") if r.get("method") == "resources/read" else None
            pid = target and self.core.product_of(target)
            if pid and f"product:{pid}:read" not in granted:
                need = f"product:{pid}:read"
                return await self._deny(send, 403, "insufficient_scope", f"scope {need} is required", need)
        from konmari.connect.server import Identity

        scope.setdefault("state", {})["konmari"] = Identity(ctx, granted, claims.get("azp") or claims.get("client_id"))
        sent = False

        async def replay():
            nonlocal sent
            if sent:
                return await receive()
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await self.app(scope, replay, send)
