"""`konmari serve`: the ProductServer core on the official MCP Python SDK, over STDIO or Streamable HTTP, with the
protocol revision pinned in connect.yaml (default 2026-07-28, fallback 2025-11-25 switchable)."""

import os
import sys

import mcp_types as types
from mcp.server import Server
from mcp.shared.exceptions import MCPError

from konmari.connect.server import Identity, ProductServer, ResourceNotFound, UnknownTool
from konmari.governance import access
from konmari.governance.audit import AuditUnavailable

RESOURCE_NOT_FOUND = -32002


def allowed_versions(cfg: dict) -> set[str]:
    return {v for v in (cfg.get("protocol_version"), cfg.get("fallback_protocol_version")) if v}


def build(core: ProductServer, identity) -> Server:
    """`identity(ctx) -> Identity`: from the environment (STDIO) or the validated token (HTTP)."""
    served = allowed_versions(core.cfg)

    async def pin_protocol(ctx, call_next):
        v = (ctx.params or {}).get("protocolVersion") if ctx.method == "initialize" else ctx.protocol_version
        if ctx.request_id is not None and v and v not in served:
            raise MCPError(types.INVALID_REQUEST, f"protocol version {v} is not served; this server is pinned to "
                                                  f"{', '.join(sorted(served))}")
        return await call_next(ctx)

    async def list_tools(ctx, params):
        identity(ctx)
        return types.ListToolsResult(tools=[types.Tool.model_validate(d) for d in core.list_tools()])

    async def call_tool(ctx, params):
        # shortcut: the catalog connection is synchronous; calls are served one at a time (pilot load)
        try:
            r = core.call(identity(ctx), params.name, params.arguments or {})
        except UnknownTool as e:
            raise MCPError(types.INVALID_PARAMS, str(e)) from None
        except AuditUnavailable:
            r = {"isError": True, "structured": None, "text": "the audit trail is unavailable; the call was refused"}
        return types.CallToolResult(content=[types.TextContent(type="text", text=r["text"])],
                                    structured_content=r["structured"], is_error=r["isError"])

    async def list_resources(ctx, params):
        return types.ListResourcesResult(resources=[types.Resource.model_validate(r)
                                                    for r in core.list_resources(identity(ctx))])

    async def read_resource(ctx, params):
        try:
            mime, text = core.read_resource(identity(ctx), str(params.uri))
        except ResourceNotFound as e:
            raise MCPError(RESOURCE_NOT_FOUND, str(e)) from None
        except AuditUnavailable:
            raise MCPError(types.INTERNAL_ERROR, "the audit trail is unavailable; the read was refused") from None
        return types.ReadResourceResult(contents=[types.TextResourceContents(uri=str(params.uri), mime_type=mime,
                                                                             text=text)])

    srv = Server("konmari", version="0.1.0", title="KonMari-Kit data products",
                 instructions="Read-only data products of one pilot. Every result states product, version, as_of "
                              "and freshness; say 'I don't know' when the data is stale or missing.",
                 on_list_tools=list_tools, on_call_tool=call_tool, on_list_resources=list_resources,
                 on_read_resource=read_resource)
    srv.middleware.append(pin_protocol)
    return srv


def env_identity(cat) -> Identity:
    """STDIO: the caller identity comes from the environment (MCP spec), never from an OAuth flow."""
    agent = os.environ.get("KONMARI_AGENT")
    if not agent:
        raise ValueError("STDIO mode needs a caller identity: set KONMARI_AGENT (registered with 'konmari govern "
                         "agent'), optionally KONMARI_ON_BEHALF_OF, KONMARI_GROUPS and KONMARI_SCOPES")
    groups = [g for g in os.environ.get("KONMARI_GROUPS", "").replace(",", " ").split() if g]
    scopes = [s for s in os.environ.get("KONMARI_SCOPES", "").replace(",", " ").split() if s]
    ctx = access.context(cat, agent, os.environ.get("KONMARI_ON_BEHALF_OF") or None, groups)
    return Identity(ctx, scopes, os.environ.get("KONMARI_CLIENT_ID") or "stdio")


def serve_stdio(core: ProductServer) -> None:
    import anyio
    from mcp.server.stdio import stdio_server

    ident = env_identity(core.cat)
    srv = build(core, lambda ctx: ident)

    async def main():
        async with stdio_server() as (r, w):
            await srv.run(r, w, srv.create_initialization_options())
    anyio.run(main)


def http_app(core: ProductServer, validator, resource: str, authorization_servers: list[str],
             host: str = "127.0.0.1"):
    """The ASGI app: AuthGate (OAuth resource server) around the SDK's Streamable HTTP app at /mcp."""
    from konmari.connect.auth import AuthGate

    def identity(ctx):
        st = getattr(getattr(ctx.request, "state", None), "konmari", None)
        if st is None:  # never reached behind AuthGate; fail closed anyway
            raise MCPError(types.INVALID_REQUEST, "unauthenticated")
        return st
    srv = build(core, identity)
    inner = srv.streamable_http_app(streamable_http_path="/mcp", json_response=True, stateless_http=True, host=host)
    return AuthGate(inner, core, resource, authorization_servers, validator)


def serve_http(core: ProductServer, validator, resource: str, authorization_servers: list[str], host: str,
               port: int, tls_cert: str | None = None, tls_key: str | None = None) -> None:
    import uvicorn

    from konmari.connect.auth import check_bind

    check_bind(host, bool(tls_cert and tls_key))
    uvicorn.run(http_app(core, validator, resource, authorization_servers, host), host=host, port=port,
                ssl_certfile=tls_cert, ssl_keyfile=tls_key, log_level="warning")


def log_stderr(msg: str) -> None:
    print(msg, file=sys.stderr)
