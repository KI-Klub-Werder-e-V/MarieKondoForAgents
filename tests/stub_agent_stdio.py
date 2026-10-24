"""A synthetic agent over MCP STDIO for the CI example test: answers a* questions from the fee rules, abstains on n*.
It refuses to start without the declared test principal in KONMARI_AGENT."""

import json
import os
import sys

import anyio
import mcp_types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

sys.path.insert(0, os.path.dirname(__file__))
import c09_fixture as fx  # noqa: E402

if os.environ.get("KONMARI_AGENT") != fx.PRINCIPAL["agent"]:
    sys.exit("KONMARI_AGENT must name the declared test principal")


async def list_tools(ctx, params):
    return types.ListToolsResult(tools=[types.Tool.model_validate(
        {"name": "ask", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}}})])


async def call_tool(ctx, params):
    r = fx.responder()(params.arguments["question"])
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(r))], structured_content=r)


async def main():
    srv = Server("stub-agent", on_list_tools=list_tools, on_call_tool=call_tool)
    async with stdio_server() as (r, w):
        await srv.run(r, w, srv.create_initialization_options())

anyio.run(main)
