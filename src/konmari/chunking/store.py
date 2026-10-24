"""`konmari chunk`: pilot-scoped input selection, chunking runs with reports, audited profiles, rollback."""

import json
import statistics
from importlib.metadata import version as pkg_version
from pathlib import Path

from konmari import __version__
from konmari.catalog import Catalog, audit_change, new_id, now
from konmari.categories import store as cats
from konmari.charter.store import Scope
from konmari.chunking import chunker as ck
from konmari.chunking.parse import ParseError, parse
from konmari.discard import corpus
from konmari.governance import acl
from konmari.scorecard.probes import record_evidence
from konmari.versions import Refused

STEP = "enrich"
FULL_CONTEXT_TOKENS = 200_000  # below this a long-context model can read the whole pilot corpus


def log(cat: Catalog, sc: Scope | None, action: str, entity: str, previous, new, actor: str, reason=None,
        result="recorded", **extra) -> None:
    """Every c06 decision (profile override, rollback, metadata override, term transition) goes to audit_event."""
    audit_change(cat, action, entity, previous, new, actor=actor, reason=reason,
                 charter_id=sc.charter_id if sc else None, step=STEP, result=result, **extra)


# ---------------------------------------------------------------- profiles

def overrides(cat: Catalog, charter_id: str, category: str | None) -> dict:
    row = cat.one("SELECT settings FROM chunking_profile WHERE charter_id = ? AND category_id = ?",
                  (charter_id, category or ""))
    return json.loads(row["settings"]) if row else {}


def profile(cat: Catalog, charter_id: str, category: str | None) -> dict:
    return ck.DEFAULT_PROFILE | overrides(cat, charter_id, category)


def set_profile(cat: Catalog, sc: Scope, category: str, changes: dict, actor: str, reason: str) -> dict:
    """Override profile values for one category; the decision keeps old value, new value, actor and reason."""
    if not (reason or "").strip():
        raise Refused("a profile override needs a reason (--reason)")
    if category not in sc.categories:
        raise Refused(f"category '{category}' is not in the pilot scope")
    old = overrides(cat, sc.charter_id, category)
    new = old | changes
    if errs := ck.profile_errors(new):
        raise Refused(errs)
    cat.execute("DELETE FROM chunking_profile WHERE charter_id = ? AND category_id = ?", (sc.charter_id, category))
    cat.execute("INSERT INTO chunking_profile VALUES (?, ?, ?, ?, ?)",
                (sc.charter_id, category, json.dumps(new, sort_keys=True), now(), actor))
    full_old = ck.DEFAULT_PROFILE | old
    log(cat, sc, "chunk.profile", f"chunking_profile:{category}", {k: full_old[k] for k in changes},
        changes, actor, reason)
    return ck.DEFAULT_PROFILE | new


# ---------------------------------------------------------------- input selection

def select(cat: Catalog, sc: Scope) -> tuple[list[dict], list[dict]]:
    """(eligible items, refused [{item_id, reason, detail}]). Eligible: source in the charter, category in scope,
    active after the discard pass (c04) and the category's earlier steps, standardize included, complete (c02/c05)."""
    sources = {s["id"] for s in sc.sources}
    ok, refused = [], []
    for it in cat.query("SELECT i.*, a.source_id FROM corpus_item i LEFT JOIN source_asset a ON a.asset_uri ="
                        " i.asset_uri WHERE i.charter_id = ? ORDER BY i.item_id", (sc.charter_id,)):
        it["meta"] = json.loads(it["meta"])
        cid = corpus.category(cat, it)
        it["category"] = cid
        if it["source_id"] not in sources or cid not in sc.categories:
            refused.append({"item_id": it["item_id"], "reason": "out-of-pilot-scope",
                            "detail": f"source {it['source_id']}, category {cid}"})
        elif it["state"] != "active":
            refused.append({"item_id": it["item_id"], "reason": "not-kept", "detail": f"state {it['state']}"})
        elif why := cats.check_step(cat, sc, cid, STEP):
            std = cats.step_status(cat, sc.charter_id, cid, "standardize")["status"] == "complete"
            refused.append({"item_id": it["item_id"], "reason": "step-order" if std else "not-normalized",
                            "detail": "; ".join(why)})
        else:
            ok.append(it)
    return ok, refused


# ---------------------------------------------------------------- runs

def source_version(item: dict) -> str:
    return str(item["meta"].get("version") or corpus.sha256(corpus.read_bytes(item))[:16])


def tool_versions(parser: str) -> dict:
    out = {"konmari": __version__, "langchain-text-splitters": pkg_version("langchain-text-splitters")}
    out[parser] = pkg_version("docling" if parser == "docling" else "pdfplumber")
    return out


def active_set(cat: Catalog, item_id: str) -> dict | None:
    row = cat.one("SELECT s.* FROM chunk_set s JOIN chunk_run r ON r.run_id = s.run_id WHERE s.item_id = ?"
                  " AND r.status = 'done' ORDER BY s.created_at DESC LIMIT 1", (item_id,))
    if row:
        row["chunk_ids"] = json.loads(row["chunk_ids"])
    return row


def _apply(cat: Catalog, item_id: str, dropped: str) -> None:
    """Make the item's latest non-rolled-back chunk set the active one; other chunks become `dropped`."""
    s = active_set(cat, item_id)
    ids = s["chunk_ids"] if s else []
    marks = ", ".join("?" * len(ids))
    keep = f" AND chunk_id NOT IN ({marks})" if ids else ""
    by = s["source_version"] if s and dropped == "superseded" else None
    cat.execute(f"UPDATE chunk SET status = ?, superseded_by = ? WHERE item_id = ? AND status = 'active'{keep}",
                (dropped, by, item_id, *ids))
    if ids:
        cat.execute(f"UPDATE chunk SET status = 'active', superseded_by = NULL WHERE chunk_id IN ({marks})", ids)


def chunk_item(it: dict, prof: dict) -> tuple[list[dict], str]:
    tok = ck.tokenizer(prof["tokenizer"])
    path = Path(it["content_ref"])
    if it.get("text_ref") and path.suffix.lower() not in (".pdf", ".docx", ".html", ".htm"):
        path = Path(it["text_ref"])
    chunks, method = ck.build(parse(path, prof["parser"]), tok, prof)
    return ck.assign_ids(it["item_id"], chunks), method


def run(cat: Catalog, sc: Scope, actor: str, dry_run: bool = False) -> dict:
    """Chunk every eligible item. Dry run: compute and report, write nothing."""
    items, refused = select(cat, sc)
    run_id, t = new_id(), now()
    rep = {"run_id": run_id, "dry_run": dry_run, "documents": 0, "chunks": 0, "new_chunks": 0, "unchanged": 0,
           "superseded": 0, "table_chunks": 0, "over_flag": [], "fallback": [], "parse_errors": [], "refused": refused,
           "corpus_tokens": 0}
    sizes, profiles, inputs, sets = [], {}, {}, []
    for it in items:
        prof = profiles.setdefault(it["category"] or "", profile(cat, sc.charter_id, it["category"]))
        ver = inputs[it["item_id"]] = source_version(it)
        rep["documents"] += 1
        try:
            chunks, method = chunk_item(it, prof)
        except (ParseError, ck.ChunkError, OSError) as e:
            rep["parse_errors"].append({"item_id": it["item_id"], "error": str(e)})
            sets.append((it, ver, "needs-review", str(e), []))
            continue
        if method == "fallback":
            rep["fallback"].append(it["item_id"])
        prev = active_set(cat, it["item_id"])
        ids = [c["chunk_id"] for c in chunks]
        rep["chunks"] += len(chunks)
        rep["table_chunks"] += sum(c["kind"] == "table" for c in chunks)
        rep["over_flag"] += [c["chunk_id"] for c in chunks if c["tokens"] > prof["flag_tokens"]]
        rep["corpus_tokens"] += sum(c["tokens"] for c in chunks)
        sizes += [c["tokens"] for c in chunks]
        old = set(prev["chunk_ids"]) if prev and prev["status"] == "chunked" else set()
        rep["unchanged"] += len(old & set(ids))
        rep["new_chunks"] += len(set(ids) - old)
        rep["superseded"] += len(old - set(ids))
        if prev is None or prev["chunk_ids"] != ids or prev["status"] != "chunked":
            for c in chunks:
                c["method"] = method
            sets.append((it, ver, "chunked", None, chunks))
    for r in refused:  # discarded or descoped items lose their active chunks
        if (s := active_set(cat, r["item_id"])) and s["chunk_ids"]:
            sets.append(({"item_id": r["item_id"]}, s["source_version"], "refused", r["reason"], []))
    sizes.sort()
    rep["token_sizes"] = {"min": sizes[0], "median": statistics.median(sizes), "max": sizes[-1],
                          "p95": sizes[min(len(sizes) - 1, int(round(0.95 * (len(sizes) - 1))))]} if sizes else {}
    rep["full_context_feasible"] = rep["corpus_tokens"] < FULL_CONTEXT_TOKENS
    rep["needs_review"] = [e["item_id"] for e in rep["parse_errors"]]
    if dry_run:
        return rep
    config = {"profiles": profiles, "tools": tool_versions(next(iter(profiles.values()))["parser"] if profiles
                                                            else "lite")}
    cat.execute("INSERT INTO chunk_run VALUES (?, ?, ?, ?, ?, ?, ?, 'done')",
                (run_id, sc.charter_id, actor, t, json.dumps(config, sort_keys=True), json.dumps(inputs),
                 json.dumps(rep, default=str)))
    for it, ver, status, err, chunks in sets:
        for pos, c in enumerate(chunks):
            if cat.one("SELECT chunk_id FROM chunk WHERE chunk_id = ?", (c["chunk_id"],)) is None:
                cat.execute("INSERT INTO chunk (chunk_id, item_id, source_version, first_run, heading_path, ordinal,"
                            " kind, method, content, content_hash, tokens, location, position, status, superseded_by,"
                            " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'inactive', NULL, ?)",
                            (c["chunk_id"], it["item_id"], ver, run_id,
                             json.dumps(c["heading_path"], ensure_ascii=False), c["ordinal"], c["kind"], c["method"],
                             c["content"], c["content_hash"], c["tokens"], json.dumps(c["loc"]), pos, t))
                corpus.register_derived(cat, it["item_id"], c["chunk_id"], "chunk")
        if chunks:
            acl.propagate(cat, it["item_id"])  # c07: new chunks carry the parent item's ACL
        cat.execute("INSERT INTO chunk_set VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (run_id, it["item_id"], ver, status, err, json.dumps([c["chunk_id"] for c in chunks]), t))
        _apply(cat, it["item_id"], "superseded")
    for uri in sorted({it["asset_uri"] for it, _, status, _, _ in sets if status == "chunked"}):
        record_evidence(cat, uri, "stable_id", {"present": True}, f"chunk-run:{run_id}", actor)
    log(cat, sc, "chunk.run", f"chunk_run:{run_id}", None, {k: rep[k] for k in ("documents", "chunks", "new_chunks",
                                                                                 "unchanged", "superseded")},
        actor, refused=len(refused), needs_review=rep["needs_review"])
    return rep


def get_run(cat: Catalog, run_id: str) -> dict:
    row = cat.one("SELECT * FROM chunk_run WHERE run_id = ?", (run_id,))
    if row is None:
        raise Refused(f"unknown chunking run '{run_id}'")
    return row


def rollback(cat: Catalog, sc: Scope, run_id: str, actor: str, reason: str | None = None) -> dict:
    """Chunks of the run become inactive and the previous chunk set of each affected item is active again."""
    row = get_run(cat, run_id)
    if row["status"] == "rolled_back":
        raise Refused(f"run {run_id} is already rolled back")
    cat.execute("UPDATE chunk_run SET status = 'rolled_back' WHERE run_id = ?", (run_id,))
    items = [r["item_id"] for r in cat.query("SELECT item_id FROM chunk_set WHERE run_id = ?", (run_id,))]
    for i in items:
        _apply(cat, i, "inactive")
    log(cat, sc, "chunk.rollback", f"chunk_run:{run_id}", "done", "rolled_back", actor, reason, items=items)
    return {"run_id": run_id, "items": items}


def doc_status(cat: Catalog, item_id: str) -> str | None:
    s = active_set(cat, item_id)
    return s and s["status"]


def active_chunks(cat: Catalog, item_id: str | None = None) -> list[dict]:
    q = "SELECT * FROM chunk WHERE status = 'active'" + (" AND item_id = ?" if item_id else "")
    rows = cat.query(q + " ORDER BY item_id, position", (item_id,) if item_id else ())
    for r in rows:
        r["heading_path"], r["location"] = json.loads(r["heading_path"]), json.loads(r["location"])
    return rows


def render_report(rep: dict) -> str:
    s = rep.get("token_sizes") or {}
    lines = [f"# Chunking report {'(dry run, nothing written) ' if rep['dry_run'] else ''}run {rep['run_id']}", "",
             f"- documents processed: {rep['documents']}",
             f"- chunks: {rep['chunks']} ({rep['new_chunks']} new, {rep['unchanged']} unchanged, "
             f"{rep['superseded']} superseded)",
             f"- token sizes: min {s.get('min', '-')}, median {s.get('median', '-')}, p95 {s.get('p95', '-')}, "
             f"max {s.get('max', '-')}",
             f"- table chunks: {rep['table_chunks']}",
             f"- chunks above the flag size: {len(rep['over_flag'])} {rep['over_flag'] or ''}",
             f"- plain-text fallback: {len(rep['fallback'])} {rep['fallback'] or ''}",
             f"- parse errors (status needs-review): {len(rep['parse_errors'])}",
             *[f"  - {e['item_id']}: {e['error']}" for e in rep["parse_errors"]],
             f"- refused: {len(rep['refused'])}",
             *[f"  - {r['item_id']}: {r['reason']} ({r['detail']})" for r in rep["refused"]],
             f"- pilot corpus tokens: {rep['corpus_tokens']}"]
    if rep["full_context_feasible"]:
        lines.append(f"- full-context feasible: the pilot corpus is below {FULL_CONTEXT_TOKENS:,} tokens, so a "
                     "long-context model could read it whole instead of using retrieval")
    return "\n".join(lines) + "\n"
