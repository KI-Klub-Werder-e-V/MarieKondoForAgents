"""MCP tool definitions generated from contracts (design D7) and their review pins (D8). Definitions are built from
the descriptor only, never from record content. A tool is exposed only while its SHA-256 matches the approved pin."""

import hashlib
import re

from konmari.canonical import canonical_json
from konmari.catalog import Catalog, now
from konmari.charter.store import Scope
from konmari.connect import descriptor as dsc
from konmari.connect import products as prd
from konmari.inventory.sensitivity import TIERS, rank
from konmari.versions import Refused

NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
JSON_TYPE = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean", "date": "string",
             "timestamp": "string"}
DEFAULT_CAP, MAX_CAP = 100, 1000
ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
DIALECT = "https://json-schema.org/draft/2020-12/schema"


def base_name(pid: str, version: str) -> str:
    return f"{pid.replace('-', '_')}.v{dsc.major(version)}"


def masked(desc: dict, f: dict) -> bool:
    """Personal fields are masked unless the purpose statement justifies them (lint guarantees one or the other)."""
    personal = f["classification"] in TIERS and rank(f["classification"]) >= rank("personal")
    return personal and not dsc.info(desc)["justified"].get(f["name"])


def record_schema(desc: dict) -> dict:
    props = {}
    for f in dsc.fields(desc):
        t = "string" if masked(desc, f) else JSON_TYPE[f["type"]]
        props[f["name"]] = {"type": [t, "null"], "description": f["description"]}
    return {"type": "object", "properties": props, "additionalProperties": False}


def output_schema(desc: dict) -> dict:
    return {"$schema": DIALECT, "type": "object", "additionalProperties": False,
            "required": ["product", "version", "as_of", "freshness", "records", "next_cursor"],
            "properties": {
                "product": {"type": "string", "description": "data product id"},
                "version": {"type": "string", "description": "data product version that answered"},
                "as_of": {"type": ["string", "null"], "description": "the data is current as of this time (UTC)"},
                "freshness": {"enum": ["fresh", "stale"], "description": "as_of against the freshness SLA"},
                "records": {"type": "array", "items": record_schema(desc)},
                "next_cursor": {"type": ["string", "null"], "description": "pass as `cursor` for the next page"},
                "deprecation": {"type": "object", "description": "set while this major version is deprecated",
                                "properties": {"successor": {"type": ["string", "null"]},
                                               "sunset": {"type": ["string", "null"]}}}}}


def _param(f: dict) -> dict:
    return {"type": JSON_TYPE[f["type"]], "description": f["description"]}


def _description(desc: dict, kind: str) -> str:
    i = dsc.info(desc)
    names = ", ".join(f["name"] for f in dsc.fields(desc))
    sla = prd._iso_duration(i["sla"]) if i["sla"] else "none"
    what = (f"Look up one {i['table']} record by {i['key']}." if kind == "get" else
            f"Search {i['table']} records by keywords and filters.")
    return (f"{what} Purpose: {i['purpose']} Returns: records with {names}, plus product, version, as_of and "
            f"freshness. Owner: {i['owner']}. Freshness SLA: {sla}. When not to use: {i['limitations'] or 'outside'}"
            f" the stated purpose. Read-only data product {i['id']}, major version {dsc.major(i['version'])}.")


def definitions(version_row: dict, cap: int = DEFAULT_CAP, max_cap: int = MAX_CAP) -> list[dict]:
    """The `search` and `get` tools of one product version; deterministic and free of record content."""
    desc = version_row["desc"]
    i, flds = dsc.info(desc), dsc.fields(desc)
    base = base_name(i["id"], i["version"])
    filters = {f["name"]: _param(f) for f in flds if f["filterable"] and not masked(desc, f)}
    search = {"$schema": DIALECT, "type": "object", "additionalProperties": False, "properties": {
        "keywords": {"type": "string", "maxLength": 200,
                     "description": "words that must all appear in the record's text fields (case-insensitive)"},
        **filters,
        "limit": {"type": "integer", "minimum": 1, "maximum": max_cap, "default": cap,
                  "description": f"records per page (default {cap}, at most {max_cap})"},
        "cursor": {"type": "string", "maxLength": 200, "description": "next_cursor of the previous page"}}}
        # undeclared filters are rejected
    key = next(f for f in flds if f["primary_key"])
    get = {"$schema": DIALECT, "type": "object", "additionalProperties": False, "required": [key["name"]],
           "properties": {key["name"]: _param(key)}}
    out = []
    for kind, schema in (("search", search), ("get", get)):
        name = f"{base}.{kind}"
        assert NAME.match(name), name
        out.append({"name": name, "title": f"{i['name']} (v{dsc.major(i['version'])}): {kind}",
                    "description": _description(desc, kind), "inputSchema": schema,
                    "outputSchema": output_schema(desc), "annotations": {"title": f"{i['name']} {kind}",
                                                                          **ANNOTATIONS}})
    return out


def def_hash(d: dict) -> str:
    return hashlib.sha256(canonical_json(d).encode()).hexdigest()


def generated(cat: Catalog, cap: int = DEFAULT_CAP, max_cap: int = MAX_CAP) -> list[tuple[dict, dict]]:
    """(definition, version row) for every callable version, ordered by tool name."""
    pairs = [(d, v) for v in prd.callable_versions(cat) for d in definitions(v, cap, max_cap)]
    return sorted(pairs, key=lambda p: p[0]["name"])


def pins(cat: Catalog) -> dict[str, dict]:
    return {r["tool_name"]: r for r in cat.query("SELECT * FROM tool_pin")}


def check(cat: Catalog, cap: int = DEFAULT_CAP, max_cap: int = MAX_CAP) -> list[dict]:
    """Pin state of every generated tool: ok, unpinned or mismatch."""
    p = pins(cat)
    out = []
    for d, _ in generated(cat, cap, max_cap):
        h = def_hash(d)
        st = "ok" if p.get(d["name"], {}).get("def_hash") == h else "mismatch" if d["name"] in p else "unpinned"
        out.append({"tool": d["name"], "status": st, "hash": h})
    return out


def approve(cat: Catalog, sc: Scope, name: str, actor: str, cap: int = DEFAULT_CAP, max_cap: int = MAX_CAP
            ) -> dict:
    """A reviewer approves the current definition of one tool (audited)."""
    d = next((d for d, _ in generated(cat, cap, max_cap) if d["name"] == name), None)
    if d is None:
        raise Refused(f"no callable tool named '{name}' (see konmari serve --print-tools)")
    h, prev = def_hash(d), pins(cat).get(name)
    cat.execute("DELETE FROM tool_pin WHERE tool_name = ?", (name,))
    cat.execute("INSERT INTO tool_pin VALUES (?, ?, ?, ?)", (name, h, actor, now()))
    prd.event(cat, "tool.approve", actor, sc, tool=name, previous=prev and prev["def_hash"], new=h)
    return {"tool": name, "hash": h, "approved_by": actor}
