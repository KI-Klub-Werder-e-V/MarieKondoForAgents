"""Synthetic c09 set-up: the c07 governed catalog with four documents (one with a superseded chunk, one readable only
by the board), a registered evaluation agent, golden items, and an in-process stub agent speaking MCP. No real
data."""

import json
from datetime import timedelta

import anyio

from gov_fixture import KB, SUPPORT, add_doc, agent, approve_all, govern, scope
from konmari.canonical import dump_yaml
from konmari.catalog import now
from konmari.evaluate import golden as gd

PRINCIPAL = {"agent": "eval-bot", "user": "test-eval", "groups": ["support-team"]}
FEES = "Der Jahresbeitrag für Erwachsene beträgt 120 Euro. Der Beitrag ist am 31. Januar fällig."
HALL = "Die Halle ist im Winter ab 18 Uhr geöffnet."


def governed(cat) -> None:
    sc = scope()
    add_doc(cat, "fee-rules", KB, [FEES], allow=["group:support-team"])
    add_doc(cat, "hall-times", SUPPORT, [HALL], allow=["group:support-team"])
    add_doc(cat, "board-minutes", SUPPORT, ["Der Vorstand beschließt den Haushalt 2027."], allow=["group:board"])
    t = now()
    # an earlier version of the fee rules: superseded by the source-of-truth version (c05/c06)
    cat.execute("INSERT INTO chunk (chunk_id, item_id, source_version, first_run, heading_path, ordinal, kind, method,"
                " content, content_hash, tokens, location, position, status, superseded_by, created_at) VALUES"
                " ('fee-rules#2019', 'fee-rules', 'v0', 'run', '[]', 0, 'text', 'structure',"
                " 'Der Jahresbeitrag für Erwachsene beträgt 100 Euro.', 'h2019', 7, '{}', 9, 'superseded', 'v1', ?)",
                (t,))
    approve_all(cat, sc)
    govern(cat, sc)
    agent(cat, sc, "eval-bot")


def item(i: str, behaviour="answer", origin="domain_expert", category=KB, question=None, sources=None, ref=None):
    return {"id": i, "question": question or f"Frage {i}?", "origin": origin, "category": category,
            "expected_behaviour": behaviour,
            "reference_answer": ref if ref is not None else ("120 Euro" if behaviour == "answer" else None),
            "expected_sources": sources if sources is not None else (["fee-rules"] if behaviour != "abstain" else [])}


def write_items(path, items) -> str:
    path.write_text(dump_yaml({"items": items}), encoding="utf-8")
    return str(path)


def golden_set(cat, sc, tmp, answer=40, abstain=10, conflict=0, approve=True, activate=True, extra=()):
    """Import, approve (clara) and activate a set; question text = ID so the stub agent can route on it."""
    items = [item(f"a{n:02d}", question=f"a{n:02d}") for n in range(answer)]
    items += [item(f"n{n:02d}", "abstain", question=f"n{n:02d}") for n in range(abstain)]
    items += [item(f"c{n:02d}", "flag_conflict", question=f"c{n:02d}", sources=["fee-rules", "hall-times"])
              for n in range(conflict)]
    items += list(extra)
    res = gd.import_items(cat, sc, write_items(tmp / "items.yaml", items), "clara")
    assert not res["errors"], res["errors"]
    if approve:
        for it in items:
            gd.approve(cat, sc, it["id"], "clara")
    if activate:
        gd.activate(cat, sc, "ben")
    return items


GOOD = {"answer": FEES, "behaviour": "answer", "retrieved": ["fee-rules#0"],
        "statements": [{"text": "Der Jahresbeitrag für Erwachsene beträgt 120 Euro.", "citations": ["fee-rules#0"]},
                       {"text": "Der Beitrag ist am 31. Januar fällig.", "citations": ["fee-rules#0"]}]}
ABSTAIN = {"answer": "Dazu habe ich keine belastbaren Informationen.", "behaviour": "abstain", "retrieved": [],
           "statements": []}
CONFLICT = {"answer": "Die Quellen widersprechen sich.", "behaviour": "flag_conflict",
            "retrieved": ["fee-rules#0", "hall-times#0"],
            "statements": [{"text": "Die Quellen widersprechen sich.", "citations": ["fee-rules#0", "hall-times#0"]}]}


def responder(overrides: dict | None = None):
    """Question -> response: answer items get GOOD, n* get ABSTAIN, c* get CONFLICT unless overridden."""
    overrides = overrides or {}

    def fn(q):
        if q in overrides:
            return overrides[q]
        return {"n": ABSTAIN, "c": CONFLICT}.get(q[:1], GOOD)
    return fn


def stub_agent(respond, delay: float = 0.0, hang_after: int | None = None):
    """An in-process MCP agent with one tool `ask`. Records the questions and the request meta."""
    import mcp_types as types
    from mcp.server import Server

    calls = []

    async def list_tools(ctx, params):
        return types.ListToolsResult(tools=[types.Tool.model_validate(
            {"name": "ask", "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}},
                                            "required": ["question"]}})])

    async def call_tool(ctx, params):
        calls.append({"question": params.arguments["question"], "meta": params.meta})
        if hang_after is not None and len(calls) > hang_after:
            await anyio.sleep(30)
        if delay:
            await anyio.sleep(delay)
        r = respond(params.arguments["question"])
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(r))],
                                    structured_content=r)
    return Server("stub-agent", on_list_tools=list_tools, on_call_tool=call_tool), calls


def events(cat, action: str) -> list[dict]:
    return [r | {"details": json.loads(r["details"])} for r in
            cat.query("SELECT * FROM audit_event WHERE action = ? ORDER BY seq", (action,))]


def days_ago(n: float):
    return now() - timedelta(days=n)


__all__ = ["KB", "SUPPORT", "scope"]
