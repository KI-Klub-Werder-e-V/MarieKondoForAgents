"""`konmari eval run` (design D1-D3): every approved item of the active golden set goes to the agent through its
MCP interface as a declared test principal; retrieval, grounding, citation and abstention are scored per item and
aggregated per run and category. An unreachable agent fails the run and keeps the partial results.

Agent contract: an MCP tool (default `ask`) taking {"question"} and returning structured content
{"answer", "behaviour": answer|abstain|flag_conflict, "retrieved": [chunk IDs],
 "statements": [{"text", "citations": [source or chunk IDs]}]}. The test principal travels in the request `_meta`
(`konmari/test_principal`) and, for STDIO agents, in KONMARI_AGENT / KONMARI_ON_BEHALF_OF / KONMARI_GROUPS."""

import json
import os
import sys
import time
from contextlib import asynccontextmanager

import anyio

from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.evaluate import golden as gd
from konmari.evaluate import judge as jd
from konmari.evaluate.golden import NEGATIVE, event
from konmari.governance import access
from konmari.versions import Refused

PASS_FAITHFULNESS = 0.80  # item-level pass bar, the default faithfulness gate
METRICS = ("faithfulness", "context_precision", "context_recall", "citation_precision", "citation_recall")


class AgentError(RuntimeError):
    pass


class McpAgent:
    """`target`: an in-process MCP server, `mcp.StdioServerParameters`, or a Streamable HTTP URL."""

    def __init__(self, target, tool: str = "ask", timeout: float = 30.0):
        self.target, self.tool, self.timeout = target, tool, timeout

    @asynccontextmanager
    async def session(self, principal: dict):
        from mcp import Client, StdioServerParameters
        from mcp.client.stdio import stdio_client

        target = self.target
        if isinstance(target, StdioServerParameters):  # agent stderr to the real stderr (sys.stderr may be captured)
            target = stdio_client(target, errlog=sys.__stderr__ or sys.stderr)
        async with Client(target) as c:
            async def ask(question: str) -> dict:
                with anyio.fail_after(self.timeout):
                    r = await c.call_tool(self.tool, {"question": question},
                                          meta={"konmari/test_principal": principal})
                if r.is_error:
                    raise AgentError(r.content[0].text if r.content else "agent returned an error")
                return r.structured_content or json.loads(r.content[0].text)
            yield ask


def agent_from_config(cfg: dict, principal: dict) -> McpAgent:
    a = cfg.get("agent") or {}
    if a.get("url"):
        target = a["url"]
    elif a.get("command"):
        from mcp import StdioServerParameters

        cmd = a["command"] if isinstance(a["command"], list) else str(a["command"]).split()
        env = {**os.environ, "KONMARI_AGENT": principal["agent"], "KONMARI_ON_BEHALF_OF": principal.get("user") or "",
               "KONMARI_GROUPS": " ".join(principal.get("groups") or [])}
        target = StdioServerParameters(command=cmd[0], args=cmd[1:], env=env)
    else:
        raise Refused("eval config: agent.command (STDIO) or agent.url (Streamable HTTP) is required")
    return McpAgent(target, a.get("tool", "ask"), float(a.get("timeout_seconds", 30)))


# ---------------------------------------------------------------- per-item scoring

def _chunks(cat: Catalog, source_id: str) -> list[dict]:
    """A cited or expected ID is a chunk ID or a source item ID."""
    return cat.query("SELECT c.chunk_id, c.item_id, c.status, c.content, i.state FROM chunk c LEFT JOIN corpus_item i"
                     " ON i.item_id = c.item_id WHERE c.chunk_id = ? OR c.item_id = ? ORDER BY c.position",
                     (source_id, source_id))


def _matches(chunk: dict, wanted: list[str]) -> bool:
    return chunk["chunk_id"] in wanted or chunk["item_id"] in wanted


def _superseded_member(cat: Catalog, item_id: str) -> bool:
    """c04/c05 source-of-truth ranking: a cluster member that is not the canonical item is not current."""
    return any(item_id in json.loads(r["members"]) and r["canonical_item"] != item_id
               for r in cat.query("SELECT members, canonical_item FROM cluster"))


def check_citation(cat: Catalog, ctx: access.Context, source_id: str) -> str:
    """Deterministic: exists, within the test principal's permitted scope (c07), current version (c05/c06)."""
    rows = _chunks(cat, source_id)
    if not rows:
        return "missing"
    item = rows[0]["item_id"]
    idx = cat.query("SELECT category_id, allowed_principals, denied_principals FROM agent_index WHERE item_id = ?",
                    (item,))
    if not any(access.permitted(r, ctx) for r in idx):
        return "out_of_scope"
    exact = [r for r in rows if r["chunk_id"] == source_id] or rows
    if all(r["status"] != "active" for r in exact) or (rows[0]["state"] or "active") != "active" \
            or _superseded_member(cat, item):
        return "stale_source"
    return "ok"


def _passages(cat: Catalog, ids: list[str]) -> list[str]:
    return [c["content"] for i in ids for c in _chunks(cat, i)]


def score_item(cat: Catalog, ctx: access.Context, judge, item: dict, resp: dict) -> dict:
    behaviour = resp.get("behaviour")
    answer = resp.get("answer") or ""
    retrieved = [str(x) for x in resp.get("retrieved") or []]
    statements = [s for s in resp.get("statements") or [] if str(s.get("text") or "").strip()]
    cited = sorted({str(c) for s in statements for c in s.get("citations") or []}
                   | {str(c) for c in resp.get("citations") or []})
    expected = item["expected_sources"]
    out = {"behaviour": behaviour, "answer": answer, "retrieved": retrieved, "cited": cited, "reasons": [],
           "details": {}, **{m: None for m in METRICS}}

    # retrieval: ID-based context precision and recall against the expected sources
    if item["expected_behaviour"] == "answer" and expected:
        rchunks = [c for r in retrieved for c in _chunks(cat, r)[:1]] if retrieved else []
        hits = [c for c in rchunks if _matches(c, expected)]
        out["context_precision"] = round(len(hits) / len(retrieved), 4) if retrieved else 0.0
        found = [e for e in expected if any(_matches(c, [e]) for c in rchunks)]
        out["context_recall"] = round(len(found) / len(expected), 4)
        if missing := [e for e in expected if e not in found]:
            out["details"]["missing_sources"] = missing
        rank = next((n for n, c in enumerate(rchunks, 1) if _matches(c, expected)), None)
        out["details"]["reciprocal_rank"] = round(1 / rank, 4) if rank else 0.0

    # citations: deterministic checks first (design D2)
    checks = {c: check_citation(cat, ctx, c) for c in cited}
    cited_ids = set(cited) | {ch["item_id"] for c in cited for ch in _chunks(cat, c)}
    out["citation_checks"] = checks
    bad = sorted({s for s in checks.values() if s != "ok"})
    out["citation_ok"] = not bad
    for c, s in checks.items():
        if s == "out_of_scope":
            event(cat, "eval.access_finding", "konmari-eval", source_id=c, agent=ctx.agent,
                  on_behalf_of=ctx.on_behalf_of or "none", finding="citation outside the permitted scope")
    out["reasons"] += [s if s != "missing" else "missing_source" for s in bad]

    if item["expected_behaviour"] == "answer":
        if behaviour != "answer":
            out["reasons"].append("over_abstention" if behaviour == "abstain" else "unexpected_behaviour")
        else:
            passages = _passages(cat, retrieved)
            cl = jd.claims(answer)
            unsupported = [c for c in cl if not judge.supports(c, passages)]
            out["faithfulness"] = round((len(cl) - len(unsupported)) / len(cl), 4) if cl else None
            if unsupported:
                out["details"]["unsupported_claims"] = unsupported
            out.update(citation_scores(cat, judge, statements))
            if out["faithfulness"] is not None and out["faithfulness"] < PASS_FAITHFULNESS:
                out["reasons"].append("unfaithful")
    elif item["expected_behaviour"] == "abstain":
        if behaviour != "abstain" or statements:
            out["reasons"].append("hallucinated_answer")
    else:  # flag_conflict: name the conflict and cite the conflicting sources or the winner (listed first)
        if behaviour != "flag_conflict":
            out["reasons"].append("conflict_not_flagged")
        elif not (set(expected) <= cited_ids or expected[0] in cited_ids):
            out["reasons"].append("conflict_sources_missing")
    out["passed"] = not out["reasons"]
    return out


def citation_scores(cat: Catalog, judge, statements: list[dict]) -> dict:
    """ALCE-style: recall = statements fully supported by their citations; precision = citations that support
    their statement alone or are needed for it."""
    if not statements:
        return {"citation_precision": None, "citation_recall": None}
    supported, relevant, total = 0, 0, 0
    for s in statements:
        cits = [str(c) for c in s.get("citations") or []]
        texts = {c: _passages(cat, [c]) for c in cits}
        full = bool(cits) and bool(judge.supports(s["text"], [t for c in cits for t in texts[c]]))
        supported += full
        for c in cits:
            total += 1
            rest = [t for o in cits if o != c for t in texts[o]]
            relevant += bool(judge.supports(s["text"], texts[c]) or (full and not judge.supports(s["text"], rest)))
    return {"citation_recall": round(supported / len(statements), 4),
            "citation_precision": round(relevant / total, 4) if total else 0.0}


# ---------------------------------------------------------------- aggregation

def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def aggregate(results: list[dict]) -> dict:
    answerable = [r for r in results if r["expected_behaviour"] == "answer"]
    negative = [r for r in results if r["expected_behaviour"] in NEGATIVE]
    out = {m: _mean(r[m] for r in results) for m in METRICS}
    cited = [r for r in results if json.loads(r["cited"])]
    out.update(items=len(results),
               pass_rate=round(sum(bool(r["passed"]) for r in results) / len(results), 4) if results else None,
               citation_check=round(sum(bool(r["citation_ok"]) for r in cited) / len(cited), 4) if cited else None,
               abstention_rate=round(sum(bool(r["passed"]) for r in negative) / len(negative), 4) if negative
               else None,
               answer_rate=round(sum(r["behaviour"] == "answer" for r in answerable) / len(answerable), 4)
               if answerable else None)
    return out


def aggregates(cat: Catalog, run_id: str) -> dict:
    rows = cat.query("SELECT * FROM eval_result WHERE run_id = ? ORDER BY item_id", (run_id,))
    by_cat = {}
    for r in rows:
        by_cat.setdefault(r["category_id"], []).append(r)
    return {"run": aggregate(rows), "categories": {c: aggregate(rs) for c, rs in sorted(by_cat.items())}}


# ---------------------------------------------------------------- the run

def _store(cat: Catalog, run_id: str, item: dict, res: dict, latency_ms: int) -> None:
    cat.execute("INSERT INTO eval_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, item["item_id"], item["version"], item["category_id"], item["expected_behaviour"],
                 res["behaviour"], res["answer"], json.dumps(res["retrieved"]), json.dumps(res["cited"]), latency_ms,
                 *(res[m] for m in METRICS), res["citation_ok"], res["passed"], json.dumps(res["reasons"]),
                 json.dumps(res["details"], ensure_ascii=False)))
    for src, st in res["citation_checks"].items():
        cat.execute("INSERT INTO citation_check VALUES (?, ?, ?, ?)", (run_id, item["item_id"], src, st))


def run(cat: Catalog, sc: Scope, agent, principal: dict, agent_version: str, actor: str, judge=None) -> dict:
    judge = judge or jd.LexicalJudge()
    gs = gd.active_set(cat, sc.charter_id)
    if gs is None:
        raise Refused("no active golden set; run 'konmari eval set activate' first")
    if not principal.get("agent"):
        raise Refused("eval config: principal.agent is required (a registered test agent)")
    ctx = access.context(cat, principal["agent"], principal.get("user"), principal.get("groups") or [])
    pins = gd.pinned(cat, gs)
    todo = [p for p in pins if p["status"] == "approved"]
    excluded = [{"item_id": p["item_id"], "version": p["version"],
                 "reason": "not_approved" if p["status"] == "draft" else p["status"]} for p in pins
                if p["status"] != "approved"]
    run_id = new_id()[:12]
    jstatus = jd.status(cat, sc.charter_id, judge.model, judge.version)
    cat.execute("INSERT INTO eval_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running', NULL, ?, NULL, ?, NULL, ?)",
                (run_id, sc.charter_id, gs["version"], agent_version, json.dumps(principal), judge.model,
                 judge.version, jstatus, json.dumps(excluded), now(), actor))
    event(cat, "eval.run.start", actor, sc, run_id=run_id, set_version=gs["version"], agent_version=agent_version,
          judge_model=judge.model, judge_version=judge.version, judge_status=jstatus, principal=principal["agent"])
    error = None

    async def go():
        async with agent.session(principal) as ask:
            for it in todo:
                t0 = time.monotonic()
                resp = await ask(it["question"])
                ms = int((time.monotonic() - t0) * 1000)
                _store(cat, run_id, it, score_item(cat, ctx, judge, it, resp), ms)
    try:
        anyio.run(go)
    except Exception as e:  # unreachable agent, timeout, protocol error: the run fails, partial results stay
        while isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
            e = e.exceptions[0]
        error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
    agg = aggregates(cat, run_id)
    status = "failed" if error else "completed"
    cat.execute("UPDATE eval_run SET status = ?, error = ?, aggregates = ?, ended_at = ? WHERE run_id = ?",
                (status, error, json.dumps(agg), now(), run_id))
    event(cat, "eval.run.finish", actor, sc, result=status, run_id=run_id, executed=agg["run"]["items"],
          excluded=len(excluded), error=error)
    return get(cat, run_id)


def get(cat: Catalog, run_id: str) -> dict:
    r = cat.one("SELECT * FROM eval_run WHERE run_id = ?", (run_id,))
    if r is None:
        raise Refused(f"unknown evaluation run '{run_id}'")
    for k in ("principal", "excluded", "aggregates"):
        r[k] = json.loads(r[k]) if r[k] else None
    return r


def latest(cat: Catalog, charter_id: str) -> dict | None:
    r = cat.one("SELECT run_id FROM eval_run WHERE charter_id = ? ORDER BY started_at DESC LIMIT 1", (charter_id,))
    return get(cat, r["run_id"]) if r else None


def results(cat: Catalog, run_id: str) -> list[dict]:
    rows = cat.query("SELECT * FROM eval_result WHERE run_id = ? ORDER BY item_id", (run_id,))
    for r in rows:
        for k in ("retrieved", "cited", "reasons", "details"):
            r[k] = json.loads(r[k])
    return rows
