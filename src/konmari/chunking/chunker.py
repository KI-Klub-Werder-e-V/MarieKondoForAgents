"""Structure-aware, token-bounded chunking with deterministic chunk IDs.

Rules (design D1/D2): never cross a heading path, never split a list item or table row, tables are their own chunks
(split on row boundaries with header and caption repeated), oversized paragraphs split on sentence boundaries,
fragments under `min_tokens` merge with their same-path neighbour, plain-text fallback only without structure.
"""

import hashlib
import json
import re
from functools import cache

from konmari.chunking.parse import has_structure

DEFAULT_PROFILE = {
    "max_tokens": 400,        # Chroma: 200-400 tokens, no overlap (research c06 §1.1); 400 is our assumption
    "min_tokens": 50,         # merge successive fragments below this with the same heading path
    "overlap_fallback": 0.15,  # overlap only for the plain-text fallback splitter
    "model_max_tokens": 512,  # embedding model maximum input; no chunk may exceed it
    "flag_tokens": 800,       # chunks above this are flagged in the report
    "tokenizer": "regex-v1",  # or hf:<tokenizer.json path or Hugging Face model name> of the embedding model
    "parser": "lite",         # lite | docling
    "llm_context": False,     # opt-in, see konmari.enrich (design D6)
    "approved_processors": [],
}
INT_KEYS = ("max_tokens", "min_tokens", "model_max_tokens", "flag_tokens")


def profile_errors(p: dict) -> list[str]:
    errs = [f"unknown profile key '{k}'" for k in p if k not in DEFAULT_PROFILE]
    errs += [f"{k} must be a positive integer" for k in INT_KEYS if k in p and not (isinstance(p[k], int) and p[k] > 0)]
    if not (isinstance(p.get("overlap_fallback", 0), (int, float)) and 0 <= p.get("overlap_fallback", 0) < 0.5):
        errs.append("overlap_fallback must be a fraction between 0 and 0.5")
    if p.get("parser", "lite") not in ("lite", "docling"):
        errs.append("parser must be lite or docling")
    full = DEFAULT_PROFILE | p
    if not errs and full["max_tokens"] > full["model_max_tokens"]:
        errs.append(f"max_tokens {full['max_tokens']} exceeds the embedding model's maximum input "
                    f"{full['model_max_tokens']}")
    return errs


# ---------------------------------------------------------------- tokenizers

class RegexTokenizer:
    """Offline approximation: words and punctuation marks. Configure the embedding model's tokenizer for real counts."""
    name = "regex-v1"
    _rx = re.compile(r"\w+|[^\w\s]")

    def count(self, text: str) -> int:
        return len(self._rx.findall(text))


class HFTokenizer:
    """Hugging Face `tokenizers`: a tokenizer.json path, or a model name (downloaded once by the operator)."""

    def __init__(self, ref: str):
        from pathlib import Path

        from tokenizers import Tokenizer

        self.name = f"hf:{ref}"
        self._tok = Tokenizer.from_file(ref) if Path(ref).is_file() else Tokenizer.from_pretrained(ref)

    def count(self, text: str) -> int:
        return len(self._tok.encode(text, add_special_tokens=False).ids)


@cache
def tokenizer(ref: str):
    return HFTokenizer(ref[3:]) if ref.startswith("hf:") else RegexTokenizer()


# ---------------------------------------------------------------- chunk building

_SENT = re.compile(r"(?<=[.!?;:])\s+")


class ChunkError(Exception):
    pass


def _split_long(text: str, tok, limit: int) -> list[str]:
    """Greedy packing of sentences (then words, for run-on sentences) into pieces of at most `limit` tokens."""
    out, cur = [], ""
    for sent in _SENT.split(text):
        parts = [sent] if tok.count(sent) <= limit else sent.split()
        for s in parts:
            cand = f"{cur} {s}".strip()
            if cur and tok.count(cand) > limit:
                out.append(cur)
                cur = s
            else:
                cur = cand
    return out + ([cur] if cur else [])


def _cell(v: str) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ")


def table_markdown(header: list[list[str]], rows: list[list[str]], caption: str | None) -> str:
    """Row-preserving serialization: caption line, header row(s), separator, one line per row."""
    lines = [caption] if caption else []
    width = max(len(r) for r in header + rows)
    for h in header:
        lines.append("| " + " | ".join(_cell(c) for c in h) + " |")
    lines.append("|" + "---|" * width)
    lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(lines)


def table_rows(md: str) -> tuple[list[list[str]], list[list[str]]]:
    """Inverse of table_markdown (for round-trip checks and consumers): (header rows, data rows)."""
    lines = [ln for ln in md.splitlines() if ln.startswith("|")]
    sep = next(i for i, ln in enumerate(lines) if re.fullmatch(r"\|(---\|)+", ln))
    split = lambda ln: [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", ln.strip()[1:-1])]  # noqa: E731
    return [split(ln) for ln in lines[:sep]], [split(ln) for ln in lines[sep + 1:]]


def _table_chunks(el: dict, tok, prof: dict) -> list[dict]:
    h = el.get("header_rows", 1)
    header, rows, caption = el["rows"][:h], el["rows"][h:], el.get("caption")
    out, cur = [], []

    def emit():
        out.append({"kind": "table", "content": table_markdown(header, cur, caption), "loc": el["loc"]})

    for r in rows:
        if cur and tok.count(table_markdown(header, cur + [r], caption)) > prof["max_tokens"]:
            emit()
            cur = []
        cur.append(r)
    if cur or not rows:
        emit()
    for c in out:
        if tok.count(c["content"]) > prof["model_max_tokens"]:
            raise ChunkError("a single table row with its header exceeds the embedding model's maximum input")
    return out


def _merge_loc(a: dict, b: dict) -> dict:
    if "pages" in a or "pages" in b:
        return {"pages": sorted(set(a.get("pages", [])) | set(b.get("pages", [])))}
    ea, eb = a.get("elements", [0, 0]), b.get("elements", [0, 0])
    return {"elements": [min(ea[0], eb[0]), max(ea[1], eb[1])]}


def build(elements: list[dict], tok, prof: dict) -> tuple[list[dict], str]:
    """Return (chunks, method). Chunks: {heading_path, kind, content, tokens, loc}."""
    prof = DEFAULT_PROFILE | prof
    if not has_structure(elements):
        return _fallback(elements, tok, prof), "fallback"
    stack, out, cur = [], [], None

    def flush():
        nonlocal cur
        if cur:
            out.append(cur)
        cur = None

    for el in elements:
        if el["kind"] == "heading":
            flush()
            stack = [s for s in stack if s[0] < el["level"]] + [(el["level"], el["text"])]
            continue
        path = [t for _, t in stack]
        if el["kind"] == "table":
            flush()  # tables are never merged with prose
            out += [dict(c, heading_path=path) for c in _table_chunks(el, tok, prof)]
            continue
        n = tok.count(el["text"])
        limit = prof["max_tokens"]
        if el["kind"] == "list_item" and n <= prof["model_max_tokens"]:
            pieces = [el["text"]]  # list items stay whole unless the embedding model could not take them
        else:
            pieces = [el["text"]] if n <= limit else _split_long(el["text"], tok, limit)
        for p in pieces:
            pt = tok.count(p)
            if cur and cur["heading_path"] == path:
                joined = f"{cur['content']}\n\n{p}"
                if (cur["tokens"] < prof["min_tokens"] or pt < prof["min_tokens"]) and tok.count(joined) <= limit:
                    cur.update(content=joined, tokens=tok.count(joined), loc=_merge_loc(cur["loc"], el["loc"]))
                    continue
            flush()
            cur = {"heading_path": path, "kind": "text", "content": p, "tokens": pt, "loc": dict(el["loc"])}
    flush()
    for c in out:
        c["tokens"] = tok.count(c["content"])
    return out, "structure"


def _fallback(elements: list[dict], tok, prof: dict) -> list[dict]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    text = "\n\n".join(e["text"] for e in elements if e["text"])
    splitter = RecursiveCharacterTextSplitter(chunk_size=prof["max_tokens"],
                                              chunk_overlap=int(prof["max_tokens"] * prof["overlap_fallback"]),
                                              length_function=tok.count)
    pieces = []
    for p in splitter.split_text(text):  # the splitter may leave a run-on piece above the limit
        pieces += [p] if tok.count(p) <= prof["max_tokens"] else _split_long(p, tok, prof["max_tokens"])
    return [{"heading_path": [], "kind": "text", "content": p, "tokens": tok.count(p), "loc": {}} for p in pieces]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def assign_ids(item_id: str, chunks: list[dict]) -> list[dict]:
    """chunk_id = sha256(item, heading path, content hash, ordinal). The ordinal counts identical content under the
    same heading path, so an edit elsewhere in the document never changes the ID of an unchanged chunk."""
    seen = {}
    for c in chunks:
        c["content_hash"] = sha(c["content"])
        key = (json.dumps(c["heading_path"], ensure_ascii=False), c["content_hash"])
        c["ordinal"] = seen[key] = seen.get(key, -1) + 1
        c["chunk_id"] = sha("\x1f".join([item_id, key[0], c["content_hash"], str(c["ordinal"])]))[:32]
    return chunks
