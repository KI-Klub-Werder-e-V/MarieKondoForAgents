"""Business glossary: SKOS-aligned terms, steward approval (four eyes), versions, harvest, links, conflicts, export."""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from konmari.canonical import canonical_json, load_yaml
from konmari.catalog import Catalog, now
from konmari.charter.store import Scope
from konmari.chunking.store import log
from konmari.enrich.providers import model_refusal
from konmari.standardize import PERSONAL_TIERS
from konmari.versions import Refused

LANGS = ("de", "en")
LABEL_KINDS = ("pref", "alt", "hidden")
METRIC_FIELDS = ("formula", "grain", "unit", "owner")
NS = "urn:konmari:glossary:"


# ---------------------------------------------------------------- model and validation

def labels(content: dict, kinds=LABEL_KINDS) -> list[tuple[str, str, str]]:
    """[(kind, lang, label)]; pref is one label per language, alt and hidden are lists."""
    out, lab = [], content.get("labels") or {}
    for k in kinds:
        for lang, v in (lab.get(k) or {}).items():
            out += [(k, lang, x) for x in ([v] if isinstance(v, str) else v or [])]
    return out


def term_errors(content: dict) -> list[str]:
    lab = content.get("labels") or {}
    pref = lab.get("pref") or {}
    errs = []
    if not any(pref.get(lang) for lang in LANGS):
        errs.append("a term needs a preferred label in German or English (labels.pref.de / labels.pref.en)")
    errs += [f"labels.pref.{lang}: exactly one preferred label per language" for lang, v in pref.items()
             if not isinstance(v, str)]
    seen = {}
    for kind, lang, x in labels(content):
        key = (lang, x.strip().casefold())
        if key in seen:
            errs.append(f"label-disjointness: '{x}'@{lang} is used as {seen[key]} and {kind} label")
        seen[key] = kind
    return errs


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe")
                              .replace("ü", "ue").replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue"))
    return re.sub(r"[^a-z0-9]+", "-", t.encode("ascii", "ignore").decode().lower()).strip("-") or "term"


# ---------------------------------------------------------------- glossary and stewards

def glossary(cat: Catalog, sc: Scope) -> dict | None:
    row = cat.one("SELECT * FROM glossary WHERE glossary_id = ?", (sc.charter_id,))
    if row:
        row["stewards"] = json.loads(row["stewards"])
    return row


def set_stewards(cat: Catalog, sc: Scope, stewards: list[str], actor: str) -> dict:
    unknown = [s for s in stewards if s not in sc.stakeholders]
    if unknown or not stewards:
        raise Refused(f"stewards must be named stakeholders of the charter: {', '.join(unknown) or '(none given)'}")
    old = glossary(cat, sc)
    if old:
        cat.execute("UPDATE glossary SET stewards = ? WHERE glossary_id = ?", (json.dumps(stewards), sc.charter_id))
    else:
        cat.execute("INSERT INTO glossary VALUES (?, ?, ?, ?)", (sc.charter_id, json.dumps(stewards), actor, now()))
    log(cat, sc, "glossary.stewards", f"glossary:{sc.charter_id}", old and old["stewards"], stewards, actor)
    return glossary(cat, sc)


def _gid(cat: Catalog, sc: Scope) -> str:
    if not glossary(cat, sc):
        cat.execute("INSERT INTO glossary VALUES (?, '[]', 'konmari', ?)", (sc.charter_id, now()))
    return sc.charter_id


def _require_steward(cat: Catalog, sc: Scope, actor: str | None = None) -> list[str]:
    g = glossary(cat, sc)
    if not g or not g["stewards"]:
        raise Refused("the glossary has no steward; terms cannot leave draft until one is named "
                      "(konmari glossary stewards --steward <id> --as <id>)")
    if actor is not None and actor not in g["stewards"]:
        raise Refused(f"'{actor}' is not a steward of this glossary (stewards: {', '.join(g['stewards'])})")
    return g["stewards"]


# ---------------------------------------------------------------- terms and versions

def _version(cat: Catalog, term_id: str, version: int | None = None) -> dict | None:
    q = "SELECT * FROM glossary_term_version WHERE term_id = ?" + (" AND version = ?" if version else
                                                                   " ORDER BY version DESC LIMIT 1")
    r = cat.one(q, (term_id, version) if version else (term_id,))
    if r:
        r["content"], r["authors"] = json.loads(r["content"]), json.loads(r["authors"])
        r["ai_drafted"] = json.loads(r["ai_drafted"]) if r["ai_drafted"] else None
    return r


def term(cat: Catalog, term_id: str) -> dict:
    t = cat.one("SELECT * FROM glossary_term WHERE term_id = ?", (term_id,))
    if t is None:
        raise Refused(f"unknown term '{term_id}'")
    t["latest"] = _version(cat, term_id)
    t["exposed"] = _version(cat, term_id, t["exposed_version"]) if t["exposed_version"] else None
    t["versions"] = [r["version"] for r in cat.query("SELECT version FROM glossary_term_version WHERE term_id = ?"
                                                     " ORDER BY version", (term_id,))]
    return t


def status(t: dict) -> str:
    if t["deprecated"]:
        return "deprecated"
    return "approved" if t["exposed_version"] else t["latest"]["status"]


def terms(cat: Catalog, glossary_id: str) -> list[dict]:
    return [term(cat, r["term_id"]) for r in cat.query("SELECT term_id FROM glossary_term WHERE glossary_id = ?"
                                                       " ORDER BY term_id", (glossary_id,))]


def _label_owner(cat: Catalog, gid: str, lang: str, label: str, skip: str | None, kinds=("pref",)) -> str | None:
    """A live term (not rejected, not deprecated) other than `skip` that uses `label` as one of `kinds`."""
    for t in terms(cat, gid):
        if t["term_id"] == skip or status(t) in ("rejected", "deprecated"):
            continue
        for v in {t["latest"]["version"]: t["latest"], **({t["exposed_version"]: t["exposed"]} if t["exposed"]
                                                          else {})}.values():
            if any(lg == lang and x.casefold() == label.casefold() for _, lg, x in labels(v["content"], kinds)):
                return t["term_id"]
    return None


def _check(cat: Catalog, gid: str, content: dict, term_id: str | None) -> None:
    if errs := term_errors(content):
        raise Refused(errs)
    for _, lang, x in labels(content, ("pref",)):
        if other := _label_owner(cat, gid, lang, x, term_id):
            raise Refused(f"preferred label '{x}'@{lang} already exists: term '{other}' (konmari glossary show "
                          f"{other})")


def add(cat: Catalog, sc: Scope, content: dict, actor: str, ai_drafted: dict | None = None,
        term_id: str | None = None) -> dict:
    gid = _gid(cat, sc)
    _check(cat, gid, content, None)
    pref = content["labels"]["pref"]
    base = term_id or slug(pref.get("de") or pref.get("en"))
    tid, n = base, 1
    while cat.one("SELECT term_id FROM glossary_term WHERE term_id = ?", (tid,)):
        n += 1
        tid = f"{base}-{n}"
    cat.execute("INSERT INTO glossary_term VALUES (?, ?, NULL, FALSE, NULL, ?, ?)", (tid, gid, actor, now()))
    _insert_version(cat, tid, 1, "draft", content, [actor], ai_drafted)
    log(cat, sc, "glossary.add", f"term:{tid}", None, {"version": 1, "status": "draft"}, actor,
        ai_drafted=ai_drafted)
    return term(cat, tid)


def _insert_version(cat, tid, version, st, content, authors, ai_drafted=None):
    cat.execute("INSERT INTO glossary_term_version (term_id, version, status, content, authors, ai_drafted,"
                " created_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (tid, version, st, canonical_json(content),
                                                             json.dumps(sorted(set(authors))),
                                                             json.dumps(ai_drafted) if ai_drafted else None, now()))


def edit(cat: Catalog, sc: Scope, term_id: str, changes: dict, actor: str, ai_drafted: dict | None = None) -> dict:
    """Draft or in-review: changed in place. Approved: a new version goes to review while the approved one stays
    exposed. Rejected: a new draft version."""
    t = term(cat, term_id)
    if t["deprecated"]:
        raise Refused(f"term '{term_id}' is deprecated")
    cur = t["latest"]
    content = {**cur["content"], **changes}
    _check(cat, t["glossary_id"], content, term_id)
    if cur["status"] in ("draft", "in_review"):
        cat.execute("UPDATE glossary_term_version SET content = ?, authors = ?, ai_drafted = ? WHERE term_id = ? AND"
                    " version = ?", (canonical_json(content), json.dumps(sorted(set(cur["authors"]) | {actor})),
                                     json.dumps(ai_drafted or cur["ai_drafted"]) if (ai_drafted or cur["ai_drafted"])
                                     else None, term_id, cur["version"]))
        ver, st = cur["version"], cur["status"]
    else:
        ver = cur["version"] + 1
        st = "draft" if cur["status"] == "rejected" or ai_drafted else "in_review"
        if st == "in_review":
            _require_steward(cat, sc)
        _insert_version(cat, term_id, ver, st, content, [actor], ai_drafted)
    log(cat, sc, "glossary.edit", f"term:{term_id}", {"version": cur["version"], "content": cur["content"]},
        {"version": ver, "status": st, "content": content}, actor, ai_drafted=ai_drafted)
    return term(cat, term_id)


def submit(cat: Catalog, sc: Scope, term_id: str, actor: str) -> dict:
    t = term(cat, term_id)
    cur = t["latest"]
    if cur["status"] != "draft":
        raise Refused(f"term '{term_id}' version {cur['version']} is {cur['status']}, not draft")
    _require_steward(cat, sc)
    if not (cur["content"].get("definition") or "").strip():
        raise Refused(f"term '{term_id}' needs a definition before review")
    _set_status(cat, term_id, cur["version"], "in_review")
    log(cat, sc, "glossary.submit", f"term:{term_id}", "draft", "in_review", actor, version=cur["version"])
    return term(cat, term_id)


def _set_status(cat, tid, ver, st, actor=None, comment=None):
    cat.execute("UPDATE glossary_term_version SET status = ? WHERE term_id = ? AND version = ?", (st, tid, ver))
    if actor:
        cat.execute("UPDATE glossary_term_version SET decided_by = ?, decided_at = ?, comment = ? WHERE term_id = ?"
                    " AND version = ?", (actor, now(), comment, tid, ver))


def decide(cat: Catalog, sc: Scope, term_id: str, actor: str, approve: bool, comment: str | None = None) -> dict:
    """Four eyes: only a steward who is not an author of the version may approve or reject it."""
    _require_steward(cat, sc, actor)
    t = term(cat, term_id)
    cur = t["latest"]
    what = "approve" if approve else "reject"
    if cur["status"] != "in_review":
        raise Refused(f"term '{term_id}' version {cur['version']} is {cur['status']}; only in_review terms can be "
                      f"{what}d (konmari glossary submit)")
    if actor in cur["authors"]:
        log(cat, sc, f"glossary.{what}", f"term:{term_id}", "in_review", "in_review", actor, comment,
            result="refused", version=cur["version"])
        raise Refused(f"'{actor}' wrote version {cur['version']} of '{term_id}' and cannot {what} it (four eyes); "
                      "another steward must decide")
    if approve and (m := cur["content"].get("metric")) is not None:
        if missing := [f for f in METRIC_FIELDS if not (m or {}).get(f)]:
            raise Refused([f"metric definition: missing field '{f}'" for f in missing])
    _set_status(cat, term_id, cur["version"], "approved" if approve else "rejected", actor, comment)
    if approve:
        cat.execute("UPDATE glossary_term SET exposed_version = ? WHERE term_id = ?", (cur["version"], term_id))
        if m := cur["content"].get("metric"):
            cat.execute("INSERT INTO glossary_metric VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (term_id, cur["version"], m["formula"], m["grain"], m["unit"], m["owner"], now()))
    log(cat, sc, f"glossary.{what}", f"term:{term_id}", {"status": "in_review", "exposed": t["exposed_version"]},
        {"status": "approved" if approve else "rejected", "version": cur["version"]}, actor, comment)
    refresh_links(cat, sc.charter_id)
    return term(cat, term_id)


def deprecate(cat: Catalog, sc: Scope, term_id: str, actor: str, replaced_by: str | None, comment: str) -> dict:
    _require_steward(cat, sc, actor)
    t = term(cat, term_id)
    if not t["exposed_version"] or t["deprecated"]:
        raise Refused(f"only approved terms can be deprecated; '{term_id}' is {status(t)}")
    if replaced_by and status(term(cat, replaced_by)) != "approved":
        raise Refused(f"replacement '{replaced_by}' is not an approved term")
    cat.execute("UPDATE glossary_term SET deprecated = TRUE, replaced_by = ? WHERE term_id = ?", (replaced_by, term_id))
    _set_status(cat, term_id, t["exposed_version"], "deprecated", actor, comment)
    log(cat, sc, "glossary.deprecate", f"term:{term_id}", "approved", {"status": "deprecated",
                                                                       "replaced_by": replaced_by}, actor, comment)
    refresh_links(cat, sc.charter_id)
    return term(cat, term_id)


def revert(cat: Catalog, sc: Scope, term_id: str, to: int, actor: str, reason: str | None = None) -> dict:
    """Expose an earlier approved version again (all versions are kept)."""
    _require_steward(cat, sc, actor)
    t = term(cat, term_id)
    v = _version(cat, term_id, to)
    if v is None or not v["decided_at"] or v["status"] not in ("approved", "deprecated"):
        raise Refused(f"version {to} of '{term_id}' was never approved")
    if t["exposed_version"] == to:
        raise Refused(f"version {to} is already the exposed version")
    cat.execute("UPDATE glossary_term SET exposed_version = ? WHERE term_id = ?", (to, term_id))
    log(cat, sc, "glossary.revert", f"term:{term_id}", t["exposed_version"], to, actor, reason)
    refresh_links(cat, sc.charter_id)
    return term(cat, term_id)


def resolve_approved(cat: Catalog, ref: str) -> dict | None:
    """An approved (exposed, not deprecated) term by ID or preferred label."""
    for r in cat.query("SELECT * FROM approved_glossary_term WHERE NOT deprecated"):
        c = json.loads(r["content"])
        if r["term_id"] == ref or ref.casefold() in {x.casefold() for _, _, x in labels(c, ("pref",))}:
            return {**r, "content": c}
    return None


def exposed(cat: Catalog, glossary_id: str | None = None) -> list[dict]:
    """What downstream interfaces see: approved terms and deprecated ones (flagged), never drafts or reviews."""
    q = "SELECT * FROM approved_glossary_term" + (" WHERE glossary_id = ?" if glossary_id else "") + " ORDER BY term_id"
    return [{**r, "content": json.loads(r["content"])} for r in cat.query(q, (glossary_id,) if glossary_id else ())]


# ---------------------------------------------------------------- harvesting

_ACRONYM = re.compile(r"\b[A-ZÄÖÜ][A-ZÄÖÜ0-9]{1,5}\b")
_CAPWORD = re.compile(r"(?<=[a-zäöüß,;:]\s)([A-ZÄÖÜ][a-zäöüß]+(?:-[A-ZÄÖÜ][a-zäöüß]+)*)")


def harvest(cat: Catalog, sc: Scope, actor: str, questions: list[str] = (), top: int = 20,
            min_count: int = 2, lang: str = "de") -> list[dict]:
    """Draft candidates ranked by frequency across pilot chunks, exposed column names and pilot questions."""
    from konmari.chunking.store import active_chunks

    gid = _gid(cat, sc)
    hits: dict[str, Counter] = {}
    examples: dict[str, list[str]] = {}

    def hit(tok, where, ref):
        hits.setdefault(tok, Counter())[where] += 1
        if len(examples.setdefault(tok, [])) < 5:
            examples[tok].append(ref)

    for c in active_chunks(cat):
        for tok in set(_ACRONYM.findall(c["content"])) | set(_CAPWORD.findall(c["content"])):
            hit(tok, "chunks", f"chunk:{c['chunk_id']}")
    cols = cat.query("SELECT table_id, column_name FROM column_annotation WHERE exposed")
    for tok in list(hits):
        for col in cols:
            if tok.casefold() in re.split(r"[_\W]+", col["column_name"].casefold()):
                hit(tok, "columns", f"column:{col['table_id']}#{col['column_name']}")
    for q in questions:
        for tok in set(_ACRONYM.findall(q)) | set(_CAPWORD.findall(q)):
            hit(tok, "questions", f"question:{q[:60]}")
    known = {x.casefold() for t in terms(cat, gid) for _, _, x in labels(t["latest"]["content"])}
    ranked = sorted(((sum(c.values()), tok) for tok, c in hits.items() if tok.casefold() not in known
                     and sum(c.values()) >= min_count), key=lambda x: (-x[0], x[1]))[:top]
    out = []
    for count, tok in ranked:
        t = add(cat, sc, {"labels": {"pref": {lang: tok}}, "definition": "", "sources": [{
            "harvest": {"count": count, **hits[tok], "examples": examples[tok]}}]}, actor)
        out.append({"term_id": t["term_id"], "label": tok, "count": count, **hits[tok], "examples": examples[tok]})
    return out


# ---------------------------------------------------------------- AI-drafted definitions (opt-in)

def ai_draft(cat: Catalog, sc: Scope, term_id: str, model, actor: str, approved_processors: list[str]) -> dict:
    """Ask a self-hosted or approved model for a definition proposal; it enters as a draft flagged ai_drafted."""
    if why := model_refusal(model, approved_processors):
        raise Refused(why)
    t = term(cat, term_id)
    labs = [x for _, _, x in labels(t["latest"]["content"], ("pref", "alt"))]
    ctx = []
    for r in cat.query("SELECT c.content, m.sensitivity, i.masked_ref FROM agent_chunk c JOIN corpus_item i ON"
                       " i.item_id = c.item_id JOIN chunk_metadata m ON m.subject_id = c.chunk_id"):
        if r["sensitivity"] in PERSONAL_TIERS and not r["masked_ref"]:
            continue  # personal or special-category data never goes to a model unmasked
        if any(re.search(rf"(?<!\w){re.escape(x)}(?!\w)", r["content"]) for x in labs) and len(ctx) < 3:
            ctx.append(r["content"])
    text = model.complete(f"Define the business term '{labs[0]}' in one or two sentences for a glossary. Context:\n"
                          + "\n---\n".join(ctx))
    return edit(cat, sc, term_id, {"definition": text}, actor, ai_drafted={"model": model.name})


# ---------------------------------------------------------------- links and conflicts

def approved_labels(cat: Catalog) -> list[tuple[str, re.Pattern]]:
    out = []
    for r in exposed(cat):
        if r["deprecated"]:
            continue
        for _, _, x in labels(r["content"], ("pref", "alt")):
            flags = 0 if x.isupper() else re.I  # acronyms match case-sensitively
            out.append((r["term_id"], re.compile(rf"(?<!\w){re.escape(x)}(?!\w)", flags)))
    return out


def match_terms(label_rx: list[tuple[str, re.Pattern]], text: str) -> list[str]:
    """Whole-word matches of approved preferred or alternative labels."""
    return sorted({tid for tid, rx in label_rx if rx.search(text)})


def conflicts(cat: Catalog, glossary_id: str) -> list[dict]:
    """Label clashes between live terms and type/unit mismatches between columns linked to one term."""
    out, by_label = [], {}
    for t in terms(cat, glossary_id):
        if status(t) in ("rejected", "deprecated"):
            continue
        c = (t["exposed"] or t["latest"])["content"]
        for _, lang, x in labels(c, ("pref", "alt")):
            by_label.setdefault((lang, x.casefold()), {})[t["term_id"]] = c.get("category")
    for (lang, x), owners in sorted(by_label.items()):
        if len(owners) > 1:
            out.append({"kind": "label", "label": f"{x}@{lang}", "terms": sorted(owners),
                        "categories": sorted({v or "-" for v in owners.values()}), "targets": []})
    cols = {}
    for a in cat.query("SELECT * FROM column_annotation WHERE exposed AND term IS NOT NULL ORDER BY table_id,"
                       " column_name"):
        if t := resolve_approved(cat, a["term"]):
            cols.setdefault(t["term_id"], []).append(a)
    for tid, cs in sorted(cols.items()):
        for kind, key in (("type", "data_type"), ("unit", "unit")):
            if len({(c[key] or "").casefold() for c in cs}) > 1:
                out.append({"kind": kind, "terms": [tid], "targets": [f"{c['table_id']}#{c['column_name']}"
                                                                       for c in cs],
                            "values": {f"{c['table_id']}#{c['column_name']}": c[key] for c in cs}})
    decided = {json.loads(r["details"])["entity"] for r in cat.query(
        "SELECT details FROM audit_event WHERE action = 'glossary.conflict.resolve' AND result = 'recorded'")}
    for c in out:
        c["conflict_id"] = "gc-" + hashlib.sha256(canonical_json([c["kind"], c["terms"], c["targets"],
                                                                  c.get("label")]).encode()).hexdigest()[:10]
        c["status"] = "resolved" if c["conflict_id"] in decided else "open"
    return out


def resolve_conflict(cat: Catalog, sc: Scope, conflict_id: str, actor: str, decision: str) -> dict:
    """A steward decides; nothing is resolved automatically."""
    _require_steward(cat, sc, actor)
    c = next((c for c in conflicts(cat, sc.charter_id) if c["conflict_id"] == conflict_id), None)
    if c is None or c["status"] != "open":
        raise Refused(f"no open glossary conflict '{conflict_id}'")
    if not (decision or "").strip():
        raise Refused("a conflict decision needs a text (--decision)")
    log(cat, sc, "glossary.conflict.resolve", conflict_id, c, decision, actor, decision)
    refresh_links(cat, sc.charter_id)
    return {**c, "status": "resolved"}


def refresh_links(cat: Catalog, glossary_id: str) -> None:
    """Recompute column and chunk links of approved terms; links in open conflicts stay flagged."""
    t = now()
    flagged = {(tid, tgt) for c in conflicts(cat, glossary_id) if c["status"] == "open"
               for tid in c["terms"] for tgt in c["targets"]}
    cat.execute("DELETE FROM glossary_link WHERE term_id IN (SELECT term_id FROM glossary_term WHERE glossary_id = ?)",
                (glossary_id,))
    rows = []
    for a in cat.query("SELECT * FROM column_annotation WHERE exposed AND term IS NOT NULL"):
        if term_ := resolve_approved(cat, a["term"]):
            tgt = f"{a['table_id']}#{a['column_name']}"
            rows.append((term_["term_id"], "column", tgt, (term_["term_id"], tgt) in flagged, t))
    rx = approved_labels(cat)
    for m in cat.query("SELECT subject_id, envelope FROM chunk_metadata WHERE subject_kind = 'chunk'"):
        c = cat.one("SELECT content FROM chunk WHERE chunk_id = ?", (m["subject_id"],))
        found = match_terms(rx, c["content"]) if c else []
        env = json.loads(m["envelope"])
        if env.get("glossary_terms", []) != found:
            env["glossary_terms"] = found
            cat.execute("UPDATE chunk_metadata SET glossary_terms = ?, envelope = ? WHERE subject_id = ?",
                        (json.dumps(found), canonical_json(env), m["subject_id"]))
        rows += [(tid, "chunk", m["subject_id"], False, t) for tid in found]
    for r in cat.query("SELECT DISTINCT term_id FROM glossary_metric"):
        rows.append((r["term_id"], "metric", r["term_id"], False, t))
    if rows:
        cat.many("INSERT INTO glossary_link VALUES (?, ?, ?, ?, ?)", sorted(set(rows)))


def links(cat: Catalog, term_id: str) -> list[dict]:
    return cat.query("SELECT * FROM glossary_link WHERE term_id = ? ORDER BY kind, target", (term_id,))


# ---------------------------------------------------------------- export and report

def export_json(cat: Catalog, glossary_id: str) -> list[dict]:
    out = []
    for r in exposed(cat, glossary_id):
        c = r["content"]
        lab = c.get("labels") or {}
        m = cat.one("SELECT * FROM glossary_metric WHERE term_id = ? AND version = ?", (r["term_id"], r["version"]))
        out.append({"term_id": r["term_id"], "version": r["version"],
                    "status": "deprecated" if r["deprecated"] else "approved", "replaced_by": r["replaced_by"],
                    "pref_labels": lab.get("pref") or {}, "alt_labels": lab.get("alt") or {},
                    "hidden_labels": lab.get("hidden") or {}, "definition": c.get("definition"),
                    "scope_note": c.get("scope_note"), "examples": c.get("examples") or [], "owner": c.get("owner"),
                    "category": c.get("category"), "broader": c.get("broader") or [],
                    "related": c.get("related") or [],
                    "metric": {k: m[k] for k in METRIC_FIELDS} if m else None})
    return out


def export_skos(cat: Catalog, glossary_id: str) -> str:
    from rdflib import RDF, Graph, Literal, Namespace, URIRef
    from rdflib.namespace import DCTERMS, OWL, SKOS

    g, base = Graph(), Namespace(f"{NS}{glossary_id}:")
    g.bind("skos", SKOS)
    g.bind("dcterms", DCTERMS)
    scheme = URIRef(f"{NS}{glossary_id}")
    g.add((scheme, RDF.type, SKOS.ConceptScheme))
    for t in export_json(cat, glossary_id):
        s = base[t["term_id"]]
        g.add((s, RDF.type, SKOS.Concept))
        g.add((s, SKOS.inScheme, scheme))
        for prop, key in ((SKOS.prefLabel, "pref_labels"), (SKOS.altLabel, "alt_labels"),
                          (SKOS.hiddenLabel, "hidden_labels")):
            for lang, v in t[key].items():
                for x in [v] if isinstance(v, str) else v:
                    g.add((s, prop, Literal(x, lang=lang)))
        if t["definition"]:
            g.add((s, SKOS.definition, Literal(t["definition"])))
        if t["scope_note"]:
            g.add((s, SKOS.scopeNote, Literal(t["scope_note"])))
        for b in t["broader"]:
            g.add((s, SKOS.broader, base[b]))
        for r in t["related"]:
            g.add((s, SKOS.related, base[r]))
        if t["status"] == "deprecated":
            g.add((s, OWL.deprecated, Literal(True)))
            if t["replaced_by"]:
                g.add((s, DCTERMS.isReplacedBy, base[t["replaced_by"]]))
    return g.serialize(format="turtle")


def report(cat: Catalog, glossary_id: str) -> dict:
    from konmari.enrich.store import exposed_tables, schema_coverage

    counts = Counter(status(t) for t in terms(cat, glossary_id))
    cols = [schema_coverage(cat, tb) for tb in exposed_tables(cat)]
    total, documented = sum(c["columns"] for c in cols), sum(c["documented"] for c in cols)
    return {"coverage": round(documented / total, 4) if total else 1.0, "columns": total, "documented": documented,
            "linked": sum(c["linked"] for c in cols), "status_counts": dict(sorted(counts.items())),
            "conflicts": [c for c in conflicts(cat, glossary_id) if c["status"] == "open"]}


def read_term_file(path: str | Path) -> dict:
    doc = load_yaml(Path(path))
    if not isinstance(doc, dict):
        raise Refused(f"{path}: a term file is a mapping with labels, definition, owner, ...")
    return doc
