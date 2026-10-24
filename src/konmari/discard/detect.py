"""Detection cascade over the pilot corpus: exact -> lexical (MinHash LSH, containment) -> semantic (optional),
plus superseded versions, off-scope items and sensitive-review signals. Evidence holds IDs, scores, parameters
and signals only, never text excerpts."""

import json
import re
from collections import defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Protocol

import yaml
from datasketch import MinHash, MinHashLSH

from konmari.canonical import canonical_hash
from konmari.catalog import Catalog, new_id, now
from konmari.categories import store as cats
from konmari.charter.gate import gate
from konmari.charter.store import Scope, scope
from konmari.discard import corpus
from konmari.discard import report as rp
from konmari.discard.events import log
from konmari.versions import Refused

STEP = "discard"
DETECTORS = ["minhash", "containment", "semantic"]  # need calibration before they may create proposals
# Starting values are our assumptions (design D2); the calibration decides per pilot.
DEFAULTS = {"shingle_words": 5, "num_perm": 128, "jaccard": 0.8, "containment": 0.9, "candidate_floor": 0.5,
            "semantic": False, "cosine": 0.93, "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "identifier_types": ["EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN_CODE"], "erase_wait_days": 14,
            "retention_window_days": 30, "retention_schedule": "P7D"}
SENSITIVE_TIERS = {"personal", "special-category", "confidential"}
IDENTIFIERS = {  # configured identifier detectors: only entity types and counts are kept
    "EMAIL_ADDRESS": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "PHONE_NUMBER": re.compile(r"(?<![\w])(?:\+49|0)\s?\d{2,5}[\s/-]?\d{3,}(?:[\s-]?\d+)*"),
    "IBAN_CODE": re.compile(r"\b[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){3,7}(?:\s?[A-Z0-9]{1,3})?\b"),
}
VALID_UNTIL = re.compile(r"(gültig bis|valid until)\s*:?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})", re.I)
REPLACED = re.compile(r"(ersetzt durch|abgelöst durch|replaced by|superseded by)\s*:?\s*(.+)", re.I)
FILENAME_TOKENS = re.compile(r"([_\- ]?(v\d+(\.\d+)*|final|neu|alt|new|old|copy|kopie|\d{4}-\d{2}(-\d{2})?|\d{8}))+$",
                             re.I)


class Embedder(Protocol):
    model_id: str  # model name and version, recorded with every semantic flag

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Self-hosted multilingual model (extra: pip install konmari[semantic])."""

    def __init__(self, model: str):
        import sentence_transformers
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model)
        self.model_id = f"{model}@sentence-transformers-{sentence_transformers.__version__}"

    def embed(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path or "discard.yaml")
    if path is None and not p.is_file():
        return dict(DEFAULTS)
    doc = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    unknown = set(doc) - set(DEFAULTS)
    if unknown:
        raise Refused(f"{p}: unknown setting(s): {', '.join(sorted(unknown))}")
    return DEFAULTS | doc


def version_key(v) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v))) if v not in (None, "") else ()


def canonical(members: list[dict]) -> tuple[str, str]:
    """Source-of-truth > effective date > source version > completeness (design D4); mtime is never used."""
    cand = members
    for reason, key in [("source-of-truth", lambda m: 1 if corpus.meta(m).get("source_of_truth") else None),
                        ("effective-date", lambda m: str(corpus.meta(m).get("effective_date") or "") or None),
                        ("source-version", lambda m: version_key(corpus.meta(m).get("version")) or None)]:
        vals = [key(m) for m in cand]
        if all(v is None for v in vals):
            continue
        best = max(v for v in vals if v is not None)
        top = [m for m, v in zip(cand, vals) if v == best]
        if len(top) == 1:
            return top[0]["item_id"], reason
        cand = top
    top = sorted(cand, key=lambda m: (-m["_length"], m["item_id"]))
    return top[0]["item_id"], "completeness"


def shingles(text: str, k: int) -> set[str]:
    words = text.lower().split()
    if len(words) <= k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i:i + k]) for i in range(len(words) - k + 1)}


def components(pairs) -> list[list[str]]:
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in pairs:
        parent[find(a)] = find(b)
    groups = defaultdict(list)
    for x in parent:
        groups[find(x)].append(x)
    return [sorted(g) for g in groups.values()]


def identifiers(text: str, types: list[str]) -> dict[str, int]:
    return {t: n for t in types if t in IDENTIFIERS and (n := len(IDENTIFIERS[t].findall(text)))}


def _stem(title: str) -> str:
    return FILENAME_TOKENS.sub("", Path(title).stem).lower()


def inventory_snapshot(cat: Catalog, charter_id: str) -> str:
    rows = cat.query("SELECT asset_uri, content_hash, schema_hash, status, last_seen FROM source_asset"
                     " WHERE charter_id = ? ORDER BY asset_uri", (charter_id,))
    return "inv-" + canonical_hash([[str(v) for v in r.values()] for r in rows])[:16]


class _Run:
    def __init__(self, cat, run_id, actor):
        self.cat, self.run_id, self.actor, self.flags = cat, run_id, actor, []

    def flag(self, flag_type, method, item_id, related, score=None, params=None, signals=None):
        f = {"flag_id": new_id(), "run_id": self.run_id, "flag_type": flag_type, "method": method, "item_id": item_id,
             "related_ids": json.dumps(sorted(related)), "score": None if score is None else round(float(score), 4),
             "params": json.dumps(params or {}, sort_keys=True), "signals": json.dumps(signals or {}, sort_keys=True),
             "cluster_id": None, "created_at": now(), "created_by": self.actor}
        self.flags.append(f)
        return f

    def candidate(self, detector, a, b, score):
        self.cat.execute("INSERT INTO candidate_pair VALUES (?, ?, ?, ?, ?)", (self.run_id, detector, a, b,
                                                                           round(float(score), 4)))


def category_waiting(cat: Catalog, sc: Scope, category: str | None) -> list[str]:
    """Reasons why a category may not run the discard step yet (empty: it may)."""
    if category not in sc.categories or cats.step_status(cat, sc.charter_id, category, STEP)["status"] != \
            "not_started":
        return []
    return cats.check_step(cat, sc, category, STEP)


def _guard(cat: Catalog, charter_path, actor) -> Scope:
    res = gate(STEP, charter_path, cat, actor)
    if not res.passed:
        raise Refused([f"no approved pilot charter: {u}" if u.startswith("charter") else u for u in res.unmet])
    return scope(res.charter_id, cat)


def detect(cat: Catalog, charter_path="charter.yaml", actor="konmari-discard", cfg: dict | None = None,
           manifest=None, embedder: Embedder | None = None, today: date | None = None) -> dict:
    cfg = DEFAULTS | (cfg or {})
    today = today or date.today()
    sc = _guard(cat, charter_path, actor)
    found, outside = corpus.collect(cat, sc, manifest)
    # detection only reads; the category order (c02) decides which categories may get proposals
    waiting = {}
    for c in sorted({corpus.category(cat, i) for i in found} & set(sc.categories)):
        if why := category_waiting(cat, sc, c):
            waiting[c] = why
        elif cats.step_status(cat, sc.charter_id, c, STEP)["status"] == "not_started":
            cats.set_step(cat, sc, c, STEP, "in_progress", actor)
    items = [i for i in corpus.sync(cat, sc, found) if i["state"] == "active"]
    run = _Run(cat, new_id(), actor)
    semantic_on = bool(cfg["semantic"])
    if semantic_on and embedder is None:
        embedder = SentenceTransformerEmbedder(cfg["model"])
    config = {k: cfg[k] for k in ("shingle_words", "num_perm", "jaccard", "containment", "candidate_floor",
                                  "semantic", "cosine", "identifier_types")}
    config |= {"norm_version": corpus.NORM_VERSION, "model": embedder.model_id if semantic_on else None}
    cat.execute("INSERT INTO discard_run (run_id, charter_id, charter_version, inventory_snapshot, config, actor,"
                " started_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (run.run_id, sc.charter_id, sc.charter_version,
                                                             inventory_snapshot(cat, sc.charter_id),
                                                             json.dumps(config, sort_keys=True), actor, now()))
    by_id, texts, raw_texts = {}, {}, {}
    for it in items:
        raw, raw_texts[it["item_id"]] = corpus.read_bytes(it), corpus.read_text(it)
        text = corpus.normalize(raw_texts[it["item_id"]])
        it["_text_sha"], it["_length"] = corpus.sha256(text.encode()), len(text)
        by_id[it["item_id"]], texts[it["item_id"]] = it, text
        cat.execute("INSERT INTO fingerprint VALUES (?, ?, ?, ?, ?, ?)", (run.run_id, it["item_id"], it["_text_sha"],
                                                                      corpus.sha256(raw), corpus.NORM_VERSION,
                                                                      it["_length"]))
    stats = {"items_scanned": len(items), "outside_inventory": outside, "rejected_candidates": 0}
    pairs = {"exact": [], "lexical": [], "semantic": []}

    # exact: identical normalized text
    groups = defaultdict(list)
    for it in items:
        groups[it["_text_sha"]].append(it["item_id"])
    reps = []
    for ids in groups.values():
        ids.sort()
        reps.append(ids[0])
        for other in ids[1:]:
            run.flag("duplicate", "exact", other, [ids[0]], 1.0, {"norm_version": corpus.NORM_VERSION})
            pairs["exact"].append((ids[0], other))

    # lexical: MinHash LSH candidates, every candidate verified by exact Jaccard
    k, perm = cfg["shingle_words"], cfg["num_perm"]
    sh = {i: shingles(texts[i], k) for i in reps}
    mh = {}
    lsh = MinHashLSH(threshold=min(cfg["candidate_floor"], cfg["jaccard"]), num_perm=perm)
    for i in reps:
        m = MinHash(num_perm=perm, seed=1)
        for s in sh[i]:
            m.update(s.encode())
        mh[i] = m
        lsh.insert(i, m)
    jac = {}
    lex_params = {"shingle_words": k, "num_perm": perm, "jaccard_threshold": cfg["jaccard"]}
    for a in reps:
        for b in lsh.query(mh[a]):
            if b <= a:
                continue
            j = len(sh[a] & sh[b]) / len(sh[a] | sh[b]) if sh[a] | sh[b] else 0.0
            jac[(a, b)] = j
            run.candidate("minhash", a, b, j)
            if j >= cfg["jaccard"]:
                run.flag("duplicate", "minhash", b, [a], j, lex_params)
                pairs["lexical"].append((a, b))
            else:
                stats["rejected_candidates"] += 1

    # containment C(A,B) = |S(A) & S(B)| / |S(A)|, candidates from a shingle index
    # shortcut: exact inverted index, fine for pilot corpora (thousands of items); use LSH Ensemble beyond that
    index = defaultdict(list)
    for i in reps:
        for s in sh[i]:
            index[s].append(i)
    shared = defaultdict(int)
    for ids in index.values():
        for a, b in combinations(sorted(ids), 2):
            shared[(a, b)] += 1
    for (a, b), n in shared.items():
        ca, cb = n / len(sh[a]), n / len(sh[b])
        small, big, c = (a, b, ca) if ca >= cb else (b, a, cb)
        if c < cfg["candidate_floor"]:
            continue
        run.candidate("containment", small, big, c)
        j = jac.get((a, b), n / len(sh[a] | sh[b]))
        if c >= cfg["containment"] and j < cfg["jaccard"] and len(sh[small]) < len(sh[big]):
            run.flag("duplicate", "containment", small, [big], c,
                     {"shingle_words": k, "containment_threshold": cfg["containment"], "jaccard": round(j, 4)},
                     {"contained_item": small, "container": big})
            pairs["lexical"].append((small, big))

    # semantic (optional): cosine over embeddings; never stronger than archive (design D3)
    if semantic_on and len(reps) > 1:
        import numpy as np

        vec = np.array(embedder.embed([texts[i] for i in reps]), dtype=float)
        vec /= np.linalg.norm(vec, axis=1, keepdims=True)
        sims = vec @ vec.T
        linked = {tuple(sorted(p)) for p in pairs["lexical"]}
        floor = min(cfg["candidate_floor"], cfg["cosine"])
        for x, y in combinations(range(len(reps)), 2):
            a, b = sorted((reps[x], reps[y]))
            s = float(sims[x, y])
            if s < floor:
                continue
            run.candidate("semantic", a, b, s)
            if s >= cfg["cosine"] and (a, b) not in linked:
                run.flag("duplicate", "semantic", b, [a], s, {"cosine_threshold": cfg["cosine"],
                                                              "model": embedder.model_id})
                pairs["semantic"].append((a, b))

    # clusters per method family, with a canonical candidate
    clusters, member_of = [], defaultdict(list)
    for family, ps in pairs.items():
        for members in components(ps):
            cid, (canon, reason) = new_id(), canonical([by_id[m] for m in members])
            clusters.append({"cluster_id": cid, "family": family, "members": members, "canonical": canon,
                             "reason": reason})
            for m in members:
                member_of[m].append(clusters[-1])
            cat.execute("INSERT INTO cluster VALUES (?, ?, ?, ?, ?, ?)", (cid, run.run_id, family, json.dumps(members),
                                                                       canon, reason))
    fam = {"exact": "exact", "minhash": "lexical", "containment": "lexical", "semantic": "semantic"}
    for f in run.flags:
        c = next((c for c in member_of[f["item_id"]] if c["family"] == fam[f["method"]]), None)
        f["cluster_id"] = c and c["cluster_id"]

    # superseded versions: version history, passed valid-until with a newer cluster member, named replacement
    signals = defaultdict(lambda: {"signals": [], "related": set()})
    logical = defaultdict(list)
    for it in items:
        if (lid := corpus.meta(it).get("logical_id")) and corpus.meta(it).get("version") not in (None, ""):
            logical[lid].append(it)
    for lid, its in logical.items():
        newest = max(its, key=lambda m: version_key(corpus.meta(m)["version"]))
        for it in its:
            if version_key(corpus.meta(it)["version"]) < version_key(corpus.meta(newest)["version"]):
                signals[it["item_id"]]["signals"].append("version-history")
                signals[it["item_id"]]["related"].add(newest["item_id"])

    def newer(a, b):  # is b newer than a by source metadata
        ma, mb = corpus.meta(a), corpus.meta(b)
        if ma.get("effective_date") and mb.get("effective_date"):
            return str(mb["effective_date"]) > str(ma["effective_date"])
        return version_key(mb.get("version")) > version_key(ma.get("version")) > ()

    def valid_until(it) -> str | None:  # metadata first, else a "gültig bis 31.12.2025" line in the text
        if vu := corpus.meta(it).get("valid_until"):
            return str(vu)
        if m := VALID_UNTIL.search(raw_texts[it["item_id"]]):
            d = m.group(2)
            return d if "-" in d else "{2}-{1:0>2}-{0:0>2}".format(*d.split("."))
        return None

    for it in items:
        vu = valid_until(it)
        if vu and str(vu) < today.isoformat():
            mates = {m for c in member_of[it["item_id"]] for m in c["members"]} - {it["item_id"]}
            if repl := [m for m in sorted(mates) if newer(it, by_id[m])]:
                signals[it["item_id"]]["signals"].append("valid-until-passed")
                signals[it["item_id"]]["related"].update(repl)
    titles = {}
    for it in items:
        titles.setdefault(it["title"].lower(), it["item_id"])
        titles.setdefault(Path(it["title"]).stem.lower(), it["item_id"])
    for it in items:
        for m in REPLACED.finditer(raw_texts[it["item_id"]]):
            hit = [i for t, i in titles.items() if len(t) > 3 and t in m.group(2).lower() and i != it["item_id"]]
            if hit:
                signals[it["item_id"]]["signals"].append("named-replacement")
                signals[it["item_id"]]["related"].update(hit)
    for iid, s in signals.items():
        if any(_stem(by_id[iid]["title"]) == _stem(by_id[r]["title"]) and by_id[iid]["title"] != by_id[r]["title"]
               for r in s["related"]):
            s["signals"].append("filename-pattern")  # filename evidence only adds to another signal
        run.flag("superseded", "superseded", iid, s["related"], None, {}, {"signals": sorted(set(s["signals"]))})
    linked = {frozenset(p) for ps in pairs.values() for p in ps} | {frozenset((i, r)) for i, s in signals.items()
                                                                    for r in s["related"]}
    stems = defaultdict(list)
    for it in items:
        stems[_stem(it["title"])].append(it)
    for its in stems.values():
        for a, b in combinations(sorted(its, key=lambda m: m["item_id"]), 2):
            if a["title"] != b["title"] and frozenset((a["item_id"], b["item_id"])) not in linked:
                run.flag("review", "filename", a["item_id"], [b["item_id"]], None, {},
                         {"signals": ["filename-only"]})

    # off-scope and sensitive-review
    mismatches = []
    for it in items:
        c = corpus.category(cat, it)
        if c and c not in sc.categories:
            run.flag("off-scope", "category", it["item_id"], [], None, {"category": c},
                     {"reason": "category-not-in-charter"})
        tier = (cat.one("SELECT sensitivity_tier FROM source_asset WHERE asset_uri = ?", (it["asset_uri"],))
                or {}).get("sensitivity_tier")
        found_ids = identifiers(texts[it["item_id"]], cfg["identifier_types"])
        if tier in SENSITIVE_TIERS or found_ids:
            run.flag("sensitive-review", "identifier-detector" if found_ids else "inventory-tier", it["item_id"], [],
                     None, {"identifier_types": cfg["identifier_types"]}, {"tier": tier, "entities": found_ids})
            if found_ids and tier not in SENSITIVE_TIERS:
                mismatches.append({"item_id": it["item_id"], "asset_uri": it["asset_uri"], "inventory_tier": tier,
                                   "entities": found_ids})

    for f in run.flags:
        cat.execute("INSERT INTO flag VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(f.values()))
    rep = rp.build(cat, sc, run.run_id, stats, clusters, run.flags, mismatches) | {"categories_waiting": waiting}
    cat.execute("UPDATE discard_run SET finished_at = ?, stats = ?, report = ? WHERE run_id = ?",
                (now(), json.dumps(stats), json.dumps(rep, sort_keys=True, default=str), run.run_id))
    log(cat, sc, "detect", None, None, None, actor, run_id=run.run_id, items=len(items),
        duplicate_rate=rep["duplicate_rate"])
    return rep
