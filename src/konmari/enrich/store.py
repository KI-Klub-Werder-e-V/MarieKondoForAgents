"""`konmari enrich`: metadata envelope per chunk and agent-exposed table, derived from upstream decisions,
completeness gate, ACL sync, freshness, context header, lineage (catalog + OpenLineage), audited rollback."""

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from konmari import __version__
from konmari.canonical import canonical_json, load_yaml
from konmari.catalog import Catalog, now
from konmari.categories import store as cats
from konmari.charter.store import Scope
from konmari.chunking import chunker as ck
from konmari.chunking import store as cst
from konmari.discard import corpus
from konmari.enrich.providers import AclUnavailable, manifest_acl, model_refusal, normalize_acl
from konmari.inventory.lineage import OWNERSHIP, PRODUCER, SCHEMA, default_transport
from konmari.scorecard.probes import record_evidence
from konmari.standardize import PERSONAL_TIERS, metric
from konmari.versions import Refused

MANDATORY = ("source_id", "source_uri", "source_system", "content_type", "owner", "created_at", "modified_at",
             "freshness_class", "authority", "lineage", "acl_tags", "sensitivity", "language", "enriched_at")
OPTIONAL = ("glossary_terms", "valid_from", "valid_until", "version", "generated_context")
DATES = ("created_at", "modified_at", "enriched_at", "valid_from", "valid_until")
FRESHNESS = {"live": timedelta(hours=1), "daily": timedelta(days=1), "weekly": timedelta(days=7),
             "monthly": timedelta(days=30), "static": None}
SENSITIVITY = ("public", "internal", "confidential", "personal", "special-category")
OVERRIDABLE = set(MANDATORY + OPTIONAL) - {"lineage", "enriched_at", "generated_context", "glossary_terms"}
HEADER_TOKENS = 100
RUN_EVENT = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent"
DOCUMENTATION = "https://openlineage.io/spec/facets/1-0-1/DocumentationDatasetFacet.json"
FACET_URL = f"{PRODUCER}/blob/v{__version__}/src/konmari/schemas/chunk-metadata-facet.schema.json"
NUMERIC = re.compile(r"(?i)^(int|integer|bigint|smallint|decimal|numeric|number|float|double|real|money)")


# ---------------------------------------------------------------- envelope model

def iso(v) -> str | None:
    """ISO 8601 with time zone; naive values are UTC, date-only values are midnight UTC."""
    if v in (None, ""):
        return None
    d = v if isinstance(v, datetime) else datetime.combine(v, datetime.min.time()) if isinstance(v, date) else \
        datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).isoformat()


def envelope_errors(env: dict) -> list[str]:
    errs = [f"missing {f}" for f in MANDATORY if env.get(f) in (None, "", [], {})
            or (f == "acl_tags" and not (env[f].get("allow") or env[f].get("deny")))]
    for f in DATES:
        if env.get(f):
            try:
                if datetime.fromisoformat(str(env[f])).tzinfo is None:
                    errs.append(f"{f} has no time zone")
            except ValueError:
                errs.append(f"{f} is not ISO 8601")
    if env.get("freshness_class") and env["freshness_class"] not in FRESHNESS:
        errs.append(f"freshness_class must be one of {', '.join(FRESHNESS)}")
    if env.get("authority") and env["authority"] not in ("primary", "secondary"):
        errs.append("authority must be primary or secondary")
    if env.get("sensitivity") and env["sensitivity"] not in SENSITIVITY:
        errs.append(f"sensitivity must be one of {', '.join(SENSITIVITY)}")
    errs += [f"unknown field {f}" for f in env if f not in MANDATORY + OPTIONAL]
    return errs


def freshness_class(freq: str | None) -> str:
    """From the inventory update frequency (ISO 8601 duration | continuous | irregular | unknown)."""
    if freq == "continuous":
        return "live"
    m = re.fullmatch(r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?",
                     freq or "")
    if not m or not any(m.groups()):
        return "static"
    y, mo, w, d, h, mi, s = (int(x or 0) for x in m.groups())
    td = timedelta(days=365 * y + 30 * mo + 7 * w + d, hours=h, minutes=mi, seconds=s)
    for cls in ("live", "daily", "weekly", "monthly"):
        if td <= FRESHNESS[cls]:
            return cls
    return "static"


def authority(cat: Catalog, source_system: str | None, source_id: str | None) -> str:
    """From the active source-of-truth policies (c05): primary when the source ranks first for any field,
    secondary when it is only ranked lower; a source no policy ranks has no competitor and counts as primary."""
    names = {source_system, source_id} - {None}
    ranks = [r["sources"] for p in cat.query("SELECT content FROM policy_version WHERE active")
             for r in json.loads(p["content"])["rules"].values() if r.get("sources")]
    if any(s[0] in names for s in ranks):
        return "primary"
    return "secondary" if any(names & set(s) for s in ranks) else "primary"


_DE = {"der", "die", "das", "und", "ist", "nicht", "mit", "für", "ein", "eine", "zu", "von", "im", "den", "auf",
       "sind", "bei", "wird", "werden", "oder"}
_EN = {"the", "and", "is", "of", "to", "for", "with", "a", "in", "are", "on", "not", "be", "by", "this", "or"}


def language(text: str) -> str | None:
    words = re.findall(r"\w+", text.lower())
    de, en = sum(w in _DE for w in words), sum(w in _EN for w in words)
    return None if de == en == 0 else "de" if de >= en else "en"


def context_header(title: str, path: list[str], source_system: str | None, modified_at: str | None) -> str:
    """Deterministic header (title, heading path, source, date) of at most 100 tokens; no model involved."""
    trail = [title] + [p for p in path if p != title]
    tail = " | ".join(x for x in [source_system, (modified_at or "")[:10]] if x)
    tok = ck.RegexTokenizer()
    while True:
        h = " > ".join(trail) + (f" | {tail}" if tail else "")
        if tok.count(h) <= HEADER_TOKENS or len(trail) <= 1:
            break
        trail = trail[:1] + trail[2:]  # drop middle headings first, keep the title and the nearest heading
    while tok.count(h) > HEADER_TOKENS:
        h = " ".join(h.split()[:-1])
    return h


# ---------------------------------------------------------------- overrides (decisions in audit_event)

def overrides(cat: Catalog) -> dict[str, dict]:
    out = {}
    for r in cat.query("SELECT details FROM audit_event WHERE action = 'enrich.override' AND result = 'recorded'"
                       " ORDER BY seq"):
        d = json.loads(r["details"])
        out.setdefault(d["subject"], {})[d["field"]] = d["new"]
    return out


def _parse_value(field: str, value):
    if field == "acl_tags" and isinstance(value, str):
        value = json.loads(value) if value.strip().startswith("{") else {"allow": value.split(",")}
    if field == "acl_tags":
        return normalize_acl(value.get("allow", []), value.get("deny", []))
    return iso(value) if field in DATES else value


def override(cat: Catalog, sc: Scope, subject: str, field: str, value, actor: str, reason: str) -> dict:
    """A manual value for one envelope field of a chunk, an item (all its chunks) or a table; later runs keep it."""
    if field not in OVERRIDABLE:
        raise Refused(f"field '{field}' cannot be overridden; valid: {', '.join(sorted(OVERRIDABLE))}")
    if not (reason or "").strip():
        raise Refused("an override needs a reason (--reason)")
    row = cat.one("SELECT envelope FROM chunk_metadata WHERE subject_id = ? OR item_id = ? ORDER BY subject_id"
                  " LIMIT 1", (subject, subject))
    known = row or cat.one("SELECT 1 AS x FROM chunk WHERE chunk_id = ? OR item_id = ?", (subject, subject)) or \
        cat.one("SELECT 1 AS x FROM column_annotation WHERE table_id = ?", (subject,))
    if not known:
        raise Refused(f"unknown chunk, item or table '{subject}'")
    value = _parse_value(field, value)
    prev = json.loads(row["envelope"]).get(field) if row else None
    cst.log(cat, sc, "enrich.override", f"{subject}#{field}", prev, value, actor, reason, subject=subject,
            field=field)
    return {"subject": subject, "field": field, "previous": prev, "new": value}


# ---------------------------------------------------------------- runs

def _asset(cat: Catalog, uri: str) -> dict:
    return cat.one("SELECT * FROM source_asset WHERE asset_uri = ?", (uri,)) or {}


def _connector(cat: Catalog, source_id: str | None) -> str | None:
    r = cat.one("SELECT connector FROM inventory_source WHERE source_id = ?", (source_id,)) if source_id else None
    return r and r["connector"]


def _row(cat: Catalog, subject: str) -> dict | None:
    """The stored row as is (JSON columns as text)."""
    return cat.one("SELECT * FROM chunk_metadata WHERE subject_id = ?", (subject,))


def _prev_acl(prev: dict | None):
    return json.loads(prev["acl_tags"]) if prev and prev["acl_tags"] else None


COLS = ("subject_id", "subject_kind", "item_id", "run_id", "envelope", "source_id", "content_type", "owner",
        "sensitivity", "authority", "freshness_class", "acl_tags", "acl_stale", "expired", "stale_after",
        "glossary_terms", "context_header", "generated_context", "complete", "status", "reasons", "updated_at")


def _write(cat: Catalog, row: dict) -> None:
    cat.execute("DELETE FROM chunk_metadata WHERE subject_id = ?", (row["subject_id"],))
    cat.execute(f"INSERT INTO chunk_metadata ({', '.join(COLS)}) VALUES ({', '.join('?' * len(COLS))})",
                tuple(row[c] for c in COLS))


def _signature(row: dict) -> str:
    env = {k: v for k, v in json.loads(row["envelope"]).items() if k not in ("enriched_at", "lineage")}
    return canonical_json({**{c: row[c] for c in COLS if c not in ("run_id", "updated_at", "envelope")}, "env": env})


def _lineage(cat: Catalog, chunk: dict, item: dict, run_id: str) -> list[dict]:
    cset = cat.one("SELECT s.run_id, r.config FROM chunk_set s JOIN chunk_run r ON r.run_id = s.run_id WHERE"
                   " s.item_id = ? AND r.status = 'done' ORDER BY s.created_at DESC LIMIT 1", (item["item_id"],))
    norm = cat.one("SELECT run_id FROM normalize_run ORDER BY started_at DESC LIMIT 1")
    cfg = json.loads(cset["config"]) if cset else {}
    return [{"step": "source", "uri": item["content_ref"], "item_id": item["item_id"],
             "version": chunk["source_version"]},
            {"step": "normalize", "run": norm and norm["run_id"],
             "category_step": cats.step_status(cat, item["charter_id"], item.get("category"), "standardize")["status"],
             "tool": f"konmari {__version__}"},
            {"step": "chunk", "run": cset and cset["run_id"], "profile": cfg.get("profiles", {}).get(
                item.get("category") or ""), "tools": cfg.get("tools"), "method": chunk["method"]},
            {"step": "enrich", "run": run_id, "tool": f"konmari {__version__}"}]


class _Run:
    def __init__(self, cat, sc, run_id, acl, model, today, terms):
        self.cat, self.sc, self.run_id, self.acl, self.model, self.today = cat, sc, run_id, acl, model, today
        self.terms, self.ovr, self.acl_cache = terms, overrides(cat), {}
        self.rep = {"run_id": run_id, "subjects": 0, "ready": 0, "needs_review": [], "missing": {},
                    "acl_stale": [], "acl_changes": 0, "expired": [], "completeness": {}, "schema": {},
                    "llm": {"called": 0, "skipped_personal": 0, "refused": None}}
        self.complete_by_cat = {}

    def acl_for(self, key, item, asset):
        """One ACL lookup per item or table per run; (tags | None, stale)."""
        if key not in self.acl_cache and item and item.get("acl_status") in ("known", "policy"):
            # c07 captured this item's ACL: acl_tags are filled from it (one capture implementation)
            self.acl_cache[key] = ({"allow": list(item["allowed_principals"]),
                                    "deny": list(item["denied_principals"] or [])}, False)
        if key not in self.acl_cache:
            fn = self.acl.get(_connector(self.cat, asset.get("source_id")), self.acl.get("*", manifest_acl))
            try:
                self.acl_cache[key] = (fn(item, asset), False)
            except AclUnavailable as e:
                self.acl_cache[key] = (None, str(e))
        return self.acl_cache[key]

    def finish(self, subject, kind, item_id, env, extra_reasons=(), header="", generated=None, prev=None, stale=False,
               category=None):
        errs = envelope_errors(env)
        complete = not [e for e in errs if e.startswith("missing")]
        reasons = errs + list(extra_reasons)
        status = "ready-for-agents" if not reasons else "needs-review"
        expired = bool(env.get("valid_until")) and datetime.fromisoformat(env["valid_until"]).date() < self.today
        interval = FRESHNESS.get(env.get("freshness_class"))
        stale_after = (datetime.fromisoformat(env["modified_at"]) + interval).isoformat() \
            if interval and env.get("modified_at") else None
        row = {"subject_id": subject, "subject_kind": kind, "item_id": item_id, "run_id": self.run_id,
               "envelope": canonical_json(env), "source_id": env.get("source_id"),
               "content_type": env.get("content_type"), "owner": env.get("owner"),
               "sensitivity": env.get("sensitivity"), "authority": env.get("authority"),
               "freshness_class": env.get("freshness_class"),
               "acl_tags": canonical_json(env["acl_tags"]) if env.get("acl_tags") else None,
               "acl_stale": bool(stale), "expired": expired, "stale_after": stale_after,
               "glossary_terms": json.dumps(env.get("glossary_terms") or []), "context_header": header,
               "generated_context": json.dumps(generated) if generated else None, "complete": complete,
               "status": status, "reasons": json.dumps(reasons), "updated_at": now()}
        self.rep["subjects"] += 1
        self.rep["ready"] += status == "ready-for-agents"
        if reasons:
            self.rep["needs_review"].append({"subject": subject, "reasons": reasons})
        for e in errs:
            if e.startswith("missing "):
                self.rep["missing"].setdefault(e[8:], []).append(subject)
        if stale:
            self.rep["acl_stale"].append(subject)
        if expired:
            self.rep["expired"].append(subject)
        c = self.complete_by_cat.setdefault(category or env.get("content_type") or "(none)", [0, 0])
        c[0] += complete
        c[1] += 1
        if prev is None or _signature(prev) != _signature(row):
            self.cat.execute("INSERT INTO chunk_metadata_history VALUES (?, ?, ?)",
                             (self.run_id, subject, json.dumps(prev, default=str) if prev else None))
            _write(self.cat, row)
        return row


def _apply_acl(r: _Run, subject: str, env: dict, key: str, item, asset, prev, ovr: dict):
    """acl_tags: manual decision > source permissions; on connector failure keep the previous tags, flag stale."""
    if "acl_tags" in ovr:
        env["acl_tags"] = ovr["acl_tags"]
        return False
    tags, stale = r.acl_for(key, item, asset)
    if stale:
        env["acl_tags"] = _prev_acl(prev)
        return stale
    env["acl_tags"] = tags
    return False


def run(cat: Catalog, sc: Scope, actor: str, *, acl: dict | None = None, model=None, today: date | None = None,
        transport=None, emit_lineage: bool = True) -> dict:
    """Enrich every active chunk and every agent-exposed table. `acl` maps connector -> provider(item, asset)
    ('*' = default, the manifest export); `model` is an opt-in ContextModel for LLM context."""
    from konmari.glossary import store as gl

    run_id, t = new_run_id(), now()
    today = today or date.today()
    r = _Run(cat, sc, run_id, acl or {}, model, today, gl.approved_labels(cat))
    enriched_at = iso(t)
    events, acl_events = {}, {}
    items = {i["item_id"]: i for i in cat.query("SELECT * FROM corpus_item WHERE charter_id = ?", (sc.charter_id,))}
    chunks = [c for c in cst.active_chunks(cat) if c["item_id"] in items]
    texts = {}
    for c in chunks:
        texts.setdefault(c["item_id"], []).append(c["content"])
    for c in chunks:
        it = dict(items[c["item_id"]])
        it["meta"] = json.loads(it["meta"]) if isinstance(it["meta"], str) else it["meta"]
        it["category"] = corpus.category(cat, it)
        asset, meta, prof = _asset(cat, it["asset_uri"]), it["meta"], cst.profile(cat, sc.charter_id, it["category"])
        ovr = {**r.ovr.get(it["item_id"], {}), **r.ovr.get(c["chunk_id"], {})}
        prev = _row(cat, c["chunk_id"])
        source_system = meta.get("source_system") or asset.get("system_type")
        env = {"source_id": asset.get("source_id"), "source_uri": meta.get("source_uri") or it["item_id"],
               "source_system": source_system, "content_type": it["category"], "owner": asset.get("owner"),
               "created_at": iso(meta.get("created_at")),
               "modified_at": iso(meta.get("modified_at") or asset.get("last_modified")),
               "freshness_class": freshness_class(asset.get("update_frequency")),
               "authority": authority(cat, source_system, asset.get("source_id")),
               "lineage": _lineage(cat, c, it, run_id), "sensitivity": meta.get("sensitivity")
               or asset.get("sensitivity_tier"), "language": meta.get("language")
               or language(" ".join(texts[c["item_id"]])), "enriched_at": enriched_at,
               "glossary_terms": gl.match_terms(r.terms, c["content"]), "version": c["source_version"]}
        for k in ("valid_from", "valid_until"):
            if meta.get(k):
                env[k] = iso(meta[k])
        stale = _apply_acl(r, c["chunk_id"], env, it["item_id"], it, asset, prev, ovr)
        env.update({k: v for k, v in ovr.items() if k != "acl_tags"})
        if prev and not stale and _prev_acl(prev) != env["acl_tags"] and "acl_tags" not in ovr:
            acl_events.setdefault(it["item_id"], {"previous": _prev_acl(prev), "new": env["acl_tags"], "chunks": []})[
                "chunks"].append(c["chunk_id"])
        header = context_header(it["title"], c["heading_path"], env["source_system"], env["modified_at"])
        generated = _llm_context(r, prof, c, it, env, prev)
        if generated:
            env["generated_context"] = generated
        row = r.finish(c["chunk_id"], "chunk", it["item_id"], env, header=header, generated=generated, prev=prev,
                       stale=stale, category=it["category"])
        events.setdefault(it["item_id"], []).append(row)
    for item_id, ev in acl_events.items():
        r.rep["acl_changes"] += 1
        cst.log(cat, sc, "enrich.acl_change", item_id, ev["previous"], ev["new"], actor, chunks=ev["chunks"])
    for table in exposed_tables(cat):
        events[table] = [_enrich_table(r, table, enriched_at, run_id)]
    for c, (ok, total) in sorted(r.complete_by_cat.items()):
        r.rep["completeness"][c] = round(ok / total, 4)
        metric(cat, "metadata_completeness", "completeness", ok / total, run_id, entity=c)
    cat.execute("INSERT INTO enrich_run VALUES (?, ?, ?, ?, ?, ?, 'done')",
                (run_id, sc.charter_id, actor, t, json.dumps({"model": getattr(model, "name", None),
                                                              "acl": sorted((acl or {}).keys())}),
                 json.dumps(r.rep, default=str)))
    cst.log(cat, sc, "enrich.run", f"enrich_run:{run_id}", None, {"subjects": r.rep["subjects"],
                                                                   "ready": r.rep["ready"]}, actor,
            needs_review=[n["subject"] for n in r.rep["needs_review"]], acl_stale=r.rep["acl_stale"])
    if emit_lineage and events:
        r.rep["lineage_event"] = lineage_event(cat, run_id, events, transport)
    return r.rep


def new_run_id() -> str:
    return uuid.uuid4().hex


def _llm_context(r: _Run, prof: dict, c: dict, it: dict, env: dict, prev: dict | None):
    if not prof["llm_context"] or r.model is None:
        return None
    if why := model_refusal(r.model, prof["approved_processors"]):
        r.rep["llm"]["refused"] = why
        return None
    if env.get("sensitivity") in PERSONAL_TIERS and not it.get("masked_ref"):
        r.rep["llm"]["skipped_personal"] += 1  # never sent unmasked personal data (DSGVO), c07 masks first
        return None
    if prev and prev["generated_context"] and json.loads(prev["generated_context"])["model"] == r.model.name:
        return json.loads(prev["generated_context"])
    text = r.model.complete(f"Document: {it['title']}\nPassage:\n{c['content']}\n\nWrite one or two sentences that "
                            "situate this passage within the document, to improve search retrieval.")
    r.rep["llm"]["called"] += 1
    return {"text": text, "model": r.model.name, "label": f"generated by {r.model.name}"}


# ---------------------------------------------------------------- tables and column annotations

def load_schema(cat: Catalog, sc: Scope, path: str | Path, actor: str) -> list[str]:
    """Declare agent-exposed tables and their columns: {tables: [{table: <asset_uri>, created_at, modified_at, language,
    acl, owner (table-level values the source cannot report, stored as audited overrides),
    columns: [{name, description, type, unit, nullable, allowed_values, format, term}]}]}."""
    doc = load_yaml(Path(path))
    if not isinstance(doc, dict) or not isinstance(doc.get("tables"), list):
        raise Refused(f"{path}: a schema file needs a 'tables' list")
    changed = []
    for tdoc in doc["tables"]:
        table = tdoc.get("table")
        if not table or not _asset(cat, table):
            raise Refused(f"{path}: table '{table}' is not an inventoried asset")
        for col in tdoc.get("columns") or []:
            if not col.get("name"):
                raise Refused(f"{path}: {table}: every column needs a name")
            new = {"exposed": col.get("exposed", True), "description": col.get("description"),
                   "data_type": col.get("type"), "unit": col.get("unit"),
                   "allowed_values": json.dumps(col["allowed_values"]) if col.get("allowed_values") else None,
                   "format": col.get("format"), "nullable": col.get("nullable"), "term": col.get("term")}
            old = cat.one("SELECT * FROM column_annotation WHERE table_id = ? AND column_name = ?",
                          (table, col["name"]))
            if old and all(old[k] == v for k, v in new.items()):
                continue
            cat.execute("DELETE FROM column_annotation WHERE table_id = ? AND column_name = ?", (table, col["name"]))
            cat.execute("INSERT INTO column_annotation VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (table, col["name"], *new.values(), actor, now()))
            cst.log(cat, sc, "enrich.column", f"{table}#{col['name']}",
                    {k: old[k] for k in new} if old else None, new, actor)
            changed.append(f"{table}#{col['name']}")
        for f in ("created_at", "modified_at", "language", "acl", "owner"):
            if tdoc.get(f) is not None:
                fld = "acl_tags" if f == "acl" else f
                cur = overrides(cat).get(table, {}).get(fld)
                if cur != _parse_value(fld, tdoc[f]):
                    override(cat, sc, table, fld, tdoc[f], actor, f"schema declaration {Path(path).name}")
    return changed


def exposed_tables(cat: Catalog) -> list[str]:
    return [r["table_id"] for r in cat.query("SELECT DISTINCT table_id FROM column_annotation WHERE exposed"
                                             " ORDER BY table_id")]


def columns(cat: Catalog, table: str) -> list[dict]:
    from konmari.glossary import store as gl

    out = []
    for c in cat.query("SELECT * FROM column_annotation WHERE table_id = ? AND exposed ORDER BY column_name",
                       (table,)):
        term = gl.resolve_approved(cat, c["term"]) if c["term"] else None
        gaps = []
        if not (c["description"] or "").strip() and not term:
            gaps.append("no description or glossary link")
        if not c["data_type"]:
            gaps.append("no data type")
        elif NUMERIC.match(c["data_type"]) and not c["unit"]:
            gaps.append("numeric without unit")
        if c["nullable"] is None:
            gaps.append("nullability not declared")
        out.append({**c, "allowed_values": json.loads(c["allowed_values"]) if c["allowed_values"] else None,
                    "term_id": term and term["term_id"], "definition": term and term["content"]["definition"],
                    "gaps": gaps})
    return out


def schema_coverage(cat: Catalog, table: str) -> dict:
    cols = columns(cat, table)
    documented = [c for c in cols if "no description or glossary link" not in c["gaps"]]
    return {"columns": len(cols), "documented": len(documented), "linked": sum(bool(c["term_id"]) for c in cols),
            "coverage": round(len(documented) / len(cols), 4) if cols else 1.0,
            "gaps": {c["column_name"]: c["gaps"] for c in cols if c["gaps"]}}


def schema_view(cat: Catalog, table: str) -> list[dict]:
    """What agents get with a table: per column the description and, for linked terms, the approved definition."""
    return [{k: c[k] for k in ("column_name", "description", "data_type", "unit", "allowed_values", "format",
                               "nullable", "term_id", "definition")} for c in columns(cat, table)]


def _enrich_table(r: _Run, table: str, enriched_at: str, run_id: str) -> dict:
    cat = r.cat
    asset = _asset(cat, table)
    prim = cats.primary(cat, table)
    category = prim["category_id"] if prim and prim["state"] == "accepted" else None
    cols = columns(cat, table)
    ovr = r.ovr.get(table, {})
    prev = _row(cat, table)
    env = {"source_id": asset.get("source_id"), "source_uri": table, "source_system": asset.get("system_type"),
           "content_type": category, "owner": asset.get("owner"), "created_at": None,
           "modified_at": iso(asset.get("last_modified")), "freshness_class": freshness_class(
               asset.get("update_frequency")), "authority": authority(cat, asset.get("system_type"),
                                                                       asset.get("source_id")),
           "lineage": [{"step": "source", "uri": table, "version": asset.get("schema_hash") or asset.get(
               "content_hash")}, {"step": "enrich", "run": run_id, "tool": f"konmari {__version__}"}],
           "sensitivity": asset.get("sensitivity_tier"), "enriched_at": enriched_at,
           "language": language(" ".join(c["description"] or "" for c in cols)),
           "glossary_terms": sorted({c["term_id"] for c in cols if c["term_id"]})}
    stale = _apply_acl(r, table, env, table, None, asset, prev, ovr)
    env.update({k: v for k, v in ovr.items() if k != "acl_tags"})
    cov = schema_coverage(cat, table)
    r.rep["schema"][table] = cov
    metric(cat, "schema_coverage", "coverage", cov["coverage"], run_id, entity=table)
    record_evidence(cat, table, "glossary_coverage", {"described": not cov["gaps"], "mapped": cov["linked"],
                                                      "total": cov["columns"],
                                                      "contract_status": "complete" if not cov["gaps"]
                                                      else "incomplete"}, f"enrich-run:{run_id}", "konmari-enrich")
    gaps = [f"column {c}: {', '.join(g)}" for c, g in cov["gaps"].items()]
    header = context_header(asset.get("title") or table, [], env["source_system"], env["modified_at"])
    return r.finish(table, "table", None, env, gaps, header, prev=prev, stale=stale, category=category)


def record_envelope(cat: Catalog, table: str, record_modified_at) -> dict:
    """The envelope an agent-exposed record carries: the table's envelope plus the record's own modified_at."""
    row = metadata(cat, table)
    if row is None:
        raise Refused(f"table '{table}' has not been enriched")
    return {**row["envelope"], "modified_at": iso(record_modified_at), "status": row["status"]}


# ---------------------------------------------------------------- lineage, rollback

def lineage(cat: Catalog, chunk_id: str) -> list[dict]:
    row = metadata(cat, chunk_id)
    if row is None:
        raise Refused(f"chunk '{chunk_id}' has no metadata yet; run 'konmari enrich run'")
    return row["envelope"]["lineage"]


def lineage_event(cat: Catalog, run_id: str, events: dict[str, list[dict]], transport=None) -> dict:
    """OpenLineage COMPLETE run event: one output dataset per enriched item or table, with the ownership, schema and
    documentation facets plus the custom konmari_chunkMetadata facet (versioned _schemaURL)."""
    outputs, inputs = [], []
    for name, rows in sorted(events.items()):
        env = json.loads(rows[0]["envelope"])
        kind = rows[0]["subject_kind"]
        inputs.append({"namespace": f"konmari://{env.get('source_id') or 'unknown'}", "name": name})
        fields = ([{"name": c["column_name"], "type": c["data_type"] or "unknown",
                    **({"description": c["description"]} if c["description"] else {})}
                   for c in columns(cat, name)] if kind == "table" else
                  [{"name": f, "type": "string"} for f in MANDATORY + OPTIONAL])
        counts = {}
        for x in rows:
            counts[x["status"]] = counts.get(x["status"], 0) + 1
        outputs.append({"namespace": "konmari://catalog", "name": f"chunk_metadata/{name}", "facets": {
            "ownership": {"_producer": PRODUCER, "_schemaURL": OWNERSHIP,
                          "owners": [{"name": env["owner"], "type": "owner"}] if env.get("owner") else []},
            "schema": {"_producer": PRODUCER, "_schemaURL": SCHEMA, "fields": fields},
            "documentation": {"_producer": PRODUCER, "_schemaURL": DOCUMENTATION,
                              "description": rows[0]["context_header"]},
            "konmari_chunkMetadata": {"_producer": PRODUCER, "_schemaURL": FACET_URL, "subject_kind": kind,
                                      "subjects": len(rows), "content_type": env.get("content_type"),
                                      "sensitivity": env.get("sensitivity"), "authority": env.get("authority"),
                                      "freshness_class": env.get("freshness_class"), "status_counts": counts,
                                      "acl_stale": sum(bool(x["acl_stale"]) for x in rows),
                                      "glossary_terms": sorted({t for x in rows
                                                                for t in json.loads(x["glossary_terms"])})}}})
    event = {"eventType": "COMPLETE", "eventTime": now().isoformat() + "Z", "producer": PRODUCER,
             "schemaURL": RUN_EVENT, "run": {"runId": str(uuid.UUID(run_id))},
             "job": {"namespace": "konmari", "name": "enrich"}, "inputs": inputs, "outputs": outputs}
    (transport or default_transport()).emit(event)
    return event


def get_run(cat: Catalog, run_id: str) -> dict:
    row = cat.one("SELECT * FROM enrich_run WHERE run_id = ?", (run_id,))
    if row is None:
        raise Refused(f"unknown enrichment run '{run_id}'")
    return row


def rollback(cat: Catalog, sc: Scope, run_id: str, actor: str, reason: str | None = None) -> dict:
    """Restore every metadata row the run changed to its previous value (rows it created are removed)."""
    row = get_run(cat, run_id)
    if row["status"] == "rolled_back":
        raise Refused(f"run {run_id} is already rolled back")
    hist = cat.query("SELECT * FROM chunk_metadata_history WHERE run_id = ?", (run_id,))
    later = cat.query("SELECT DISTINCT h.run_id FROM chunk_metadata_history h JOIN enrich_run e ON e.run_id ="
                      " h.run_id WHERE e.status = 'done' AND e.started_at > ? AND h.subject_id IN (SELECT subject_id"
                      " FROM chunk_metadata_history WHERE run_id = ?)", (row["started_at"], run_id))
    if later:
        raise Refused(f"later run(s) {', '.join(r['run_id'] for r in later)} changed the same metadata; roll them "
                      "back first")
    for h in hist:
        cat.execute("DELETE FROM chunk_metadata WHERE subject_id = ?", (h["subject_id"],))
        if h["previous"]:
            _write(cat, json.loads(h["previous"]))
    cat.execute("UPDATE enrich_run SET status = 'rolled_back' WHERE run_id = ?", (run_id,))
    cst.log(cat, sc, "enrich.rollback", f"enrich_run:{run_id}", "done", "rolled_back", actor, reason,
            subjects=[h["subject_id"] for h in hist])
    return {"run_id": run_id, "restored": len(hist)}


def metadata(cat: Catalog, subject: str) -> dict | None:
    """One envelope row with its JSON columns parsed."""
    r = _row(cat, subject)
    if r:
        for k in ("envelope", "acl_tags", "glossary_terms", "generated_context", "reasons"):
            r[k] = json.loads(r[k]) if r[k] else None
    return r


def render_report(rep: dict) -> str:
    lines = [f"# Enrichment report run {rep['run_id']}", "",
             f"- subjects: {rep['subjects']}, ready-for-agents: {rep['ready']}, needs-review: "
             f"{len(rep['needs_review'])}"]
    for field, subs in sorted(rep["missing"].items()):
        lines.append(f"- missing {field}: {len(subs)}")
        lines += [f"  - {s}" for s in subs[:20]]
    lines.append(f"- acl_stale (source unreachable, previous tags kept): {len(rep['acl_stale'])}")
    lines += [f"  - {s}" for s in rep["acl_stale"][:20]]
    lines.append(f"- ACL changes recorded: {rep['acl_changes']}")
    lines.append(f"- expired (valid_until passed): {len(rep['expired'])}")
    lines += [f"  - {s}" for s in rep["expired"][:20]]
    llm = rep["llm"]
    lines.append(f"- LLM context: {llm['called']} call(s), {llm['skipped_personal']} skipped (personal data)"
                 + (f", refused: {llm['refused']}" if llm["refused"] else ""))
    lines.append("- completeness per category: " + ", ".join(f"{c} {v:.0%}" for c, v in rep["completeness"].items()))
    for t, cov in rep["schema"].items():
        lines.append(f"- schema coverage {t}: {cov['coverage']:.0%} ({cov['documented']}/{cov['columns']} columns)")
        lines += [f"  - {c}: {', '.join(g)}" for c, g in cov["gaps"].items()]
    return "\n".join(lines) + "\n"
