"""Cross-source entity resolution: Splink (Fellegi-Sunter) scoring, two-threshold routing, clerical review,
quality gate, crosswalk, and reversible splits."""

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from konmari import versions as v
from konmari.canonical import canonical_hash, load_yaml
from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.normalize.store import current_values
from konmari.standardize import log, metric
from konmari.versions import Refused

DEFAULTS = {"upper": 0.99, "lower": 0.50, "precision": 0.98, "recall": 0.95, "sample": 100}
METRIC_IDS = {"upper": "er_upper_threshold", "lower": "er_lower_threshold", "precision": "er_precision",
              "recall": "er_recall"}  # success-metrics (c01) IDs that override the defaults


def load_config(path: str | Path) -> dict:
    doc = load_yaml(Path(path))
    if not isinstance(doc, dict) or not isinstance(doc.get("entities"), dict):
        raise Refused(f"{path}: an entity-resolution config needs an 'entities' mapping")
    return doc


def success_targets(cat: Catalog, sc: Scope | None) -> dict:
    """Thresholds and floors: success-metrics (c01) targets where defined, else the defaults (our assumptions)."""
    out = dict(DEFAULTS)
    ms = v.latest_approved(cat, "metric_set_version", sc.charter_id) if sc else None
    if ms:
        for key, mid in METRIC_IDS.items():
            r = cat.one("SELECT target FROM metric_definition WHERE charter_id = ? AND metric_set_version = ?"
                        " AND metric_id = ?", (sc.charter_id, ms["version"], mid))
            if r:
                out[key] = r["target"]
    return out


def _settings(ecfg: dict):
    import splink.comparison_library as cl
    from splink import SettingsCreator, block_on

    comps = []
    for c in ecfg["comparisons"]:
        col, method = c["attribute"], c.get("method", "exact")
        comp = cl.ExactMatch(col) if method == "exact" else \
            cl.JaroWinklerAtThresholds(col, c["thresholds"]) if method == "jaro_winkler" else \
            cl.LevenshteinAtThresholds(col, c["thresholds"])
        comps.append(comp.configure(m_probabilities=c["m"], u_probabilities=c["u"]))
    return SettingsCreator(link_type="link_only", unique_id_column_name="unique_id", comparisons=comps,
                           blocking_rules_to_generate_predictions=[block_on(*b) for b in ecfg["blocking"]],
                           probability_two_random_records_match=ecfg.get("prior", 0.001))


def dedup_edges(cat: Catalog, keys: set[str]) -> list[tuple[str, str]]:
    """Within-source duplicates decided by dedup-detection (c04): member -> canonical item of its cluster."""
    by_id = defaultdict(list)
    for k in keys:
        by_id[k.split(":", 1)[1]].append(k)
        by_id[k].append(k)
    edges = []
    for c in cat.query("SELECT members, canonical_item FROM cluster"):
        for canon in by_id.get(c["canonical_item"], []):
            for m in json.loads(c["members"]):
                edges += [(canon, k) for k in by_id.get(m, []) if k != canon and k.split(":")[0] == canon.split(":")[0]]
    return edges


def score(values: dict[str, dict], ecfg: dict, lower: float, skip: set[str] = frozenset()) -> list[tuple]:
    """(rec_a, rec_b, match probability) for cross-source pairs at or above `lower` (match keys for names)."""
    import duckdb
    import pyarrow as pa
    from splink import DuckDBAPI, Linker

    cols = [c["attribute"] for c in ecfg["comparisons"]]
    by_source = defaultdict(list)
    for key, attrs in sorted(values.items()):
        if key in skip:
            continue
        src, rid = key.split(":", 1)
        by_source[src].append({"unique_id": rid, **{c: (attrs[c]["match_key"] or attrs[c]["canonical"])
                                                    if c in attrs else None for c in cols}})
    if len(by_source) < 2:
        return []
    db = DuckDBAPI(duckdb.connect())
    tables = [db.register(pa.table({c: pa.array([r[c] for r in rows], pa.string()) for c in ["unique_id", *cols]}),
                          dataset_display_name=src) for src, rows in sorted(by_source.items())]
    linker = Linker(tables, _settings(ecfg), log_level="WARNING")
    out = []
    for r in linker.inference.predict(threshold_match_probability=lower).as_record_list():
        a, b = sorted([f"{r['source_dataset_l']}:{r['unique_id_l']}", f"{r['source_dataset_r']}:{r['unique_id_r']}"])
        out.append((a, b, float(r["match_probability"])))
    return out


def route(p: float, upper: float, lower: float) -> str | None:
    """auto-link at or above upper, clerical review in between, not linked below lower."""
    return "auto" if p >= upper else "review" if p >= lower else None


def run(cat: Catalog, sc: Scope | None, entity: str, cfg: dict, actor: str) -> dict:
    import splink

    if entity not in cfg["entities"]:
        raise Refused(f"no entity-resolution config for '{entity}'")
    ecfg = cfg["entities"][entity]
    t = success_targets(cat, sc)
    upper, lower = ecfg.get("upper", t["upper"]), ecfg.get("lower", t["lower"])
    values = current_values(cat, entity)
    if not values:
        raise Refused(f"no normalized values for entity '{entity}'; run 'konmari normalize' first")
    dups = dedup_edges(cat, set(values))
    pairs = score(values, ecfg, lower, skip={b for _, b in dups})
    run_id = new_id()
    old = {(r["rec_a"], r["rec_b"]): r for r in cat.query("SELECT * FROM er_pair WHERE entity = ?", (entity,))}
    for a, b, p in pairs:
        origin = route(p, upper, lower)
        if prev := old.get((a, b)):  # labels (incl. negative ones) survive later runs
            cat.execute("UPDATE er_pair SET probability = ?, origin = ?, run_id = ? WHERE entity = ? AND rec_a = ?"
                        " AND rec_b = ?", (p, origin, run_id, entity, a, b))
        else:
            cat.execute("INSERT INTO er_pair VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, ?)",
                        (entity, a, b, p, origin, run_id))
    scored = {(a, b) for a, b, _ in pairs}
    for key, r in old.items():  # unlabelled pairs that no longer score above the lower threshold drop out
        if key not in scored and r["label"] is None:
            cat.execute("DELETE FROM er_pair WHERE entity = ? AND rec_a = ? AND rec_b = ?", (entity, *key))
    clusters = recluster(cat, entity, run_id)
    stats = {"records": len(values), "pairs": len(pairs), "auto": sum(p >= upper for *_, p in pairs),
             "review": sum(p < upper for *_, p in pairs), "dedup_links": len(dups), "clusters": len(clusters)}
    cat.execute("INSERT INTO er_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)",
                (run_id, entity, f"splink-{splink.__version__}:{canonical_hash(ecfg)[:12]}", upper, lower,
                 json.dumps(stats), actor, now()))
    log(cat, sc, "resolve.run", entity, None, stats, actor, run_id=run_id, upper=upper, lower=lower)
    return {"run_id": run_id, "upper": upper, "lower": lower, **stats}


def _linked(r: dict) -> bool:
    return r["label"] == "match" or (r["origin"] == "auto" and r["label"] is None)


def _components(nodes: set[str], edges: list[tuple[str, str]]) -> list[list[str]]:
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for a, b in edges:
        if a in parent and b in parent:
            parent[find(a)] = find(b)
    groups = defaultdict(list)
    for n in nodes:
        groups[find(n)].append(n)
    return sorted(sorted(g) for g in groups.values())


def cluster_id(members: list[str]) -> str:
    return "C-" + hashlib.sha256(min(members).encode()).hexdigest()[:8]


def _edges(cat: Catalog, entity: str, nodes: set[str]) -> tuple[list[tuple], dict[str, str]]:
    pairs = [r for r in cat.query("SELECT * FROM er_pair WHERE entity = ?", (entity,)) if _linked(r)]
    dups = dedup_edges(cat, nodes)
    origin = {}
    for kind, edges in (("dedup", dups), ("review", [(r["rec_a"], r["rec_b"]) for r in pairs
                                                     if r["origin"] == "review"]),
                        ("auto", [(r["rec_a"], r["rec_b"]) for r in pairs if r["origin"] == "auto"])):
        for a, b in edges:
            origin[a] = origin[b] = kind  # later kinds win: auto > review > dedup
    return [(r["rec_a"], r["rec_b"]) for r in pairs] + dups, origin


def recluster(cat: Catalog, entity: str, run_id: str) -> list[list[str]]:
    """Connected components of the current links; changed clusters are retired (history), the crosswalk replaced."""
    nodes = set(current_values(cat, entity))
    edges, origin = _edges(cat, entity, nodes)
    comps = _components(nodes, edges)
    ts = now()
    active = {(r["cluster_id"], r["members"]): r for r in cat.query(
        "SELECT * FROM er_cluster WHERE entity = ? AND retired_at IS NULL", (entity,))}
    keep = set()
    for m in comps:
        key = (cluster_id(m), json.dumps(m))
        if key in active:
            keep.add(key)
        else:
            cat.execute("INSERT INTO er_cluster VALUES (?, ?, ?, ?, ?, ?, NULL)", (new_id(), key[0], entity, key[1],
                                                                                  run_id, ts))
    for key, r in active.items():
        if key not in keep:
            cat.execute("UPDATE er_cluster SET retired_at = ? WHERE row_id = ?", (ts, r["row_id"]))
    cat.execute("UPDATE crosswalk SET active = FALSE WHERE entity = ? AND active", (entity,))
    cat.many("INSERT INTO crosswalk VALUES (?, ?, ?, ?, ?, ?, ?, TRUE)",
             [(entity, *k.split(":", 1), cluster_id(m), origin.get(k, "single"), run_id, ts) for m in comps for k in m])
    return comps


def crosswalk(cat: Catalog, entity: str) -> dict[str, list[dict]]:
    out = defaultdict(list)
    for r in cat.query("SELECT * FROM crosswalk WHERE entity = ? AND active ORDER BY source, record_id", (entity,)):
        out[r["cluster_id"]].append(r)
    return dict(out)


# ---------------------------------------------------------------- clerical review

def queue(cat: Catalog, entity: str) -> list[dict]:
    return cat.query("SELECT * FROM er_pair WHERE entity = ? AND origin = 'review' AND label IS NULL"
                     " ORDER BY probability DESC, rec_a, rec_b", (entity,))


def _reviewer(sc: Scope | None, actor: str) -> None:
    if sc is not None and actor not in sc.stakeholders:
        raise Refused(f"'{actor}' is not in the stakeholder register and cannot review pairs")


def label_pairs(cat: Catalog, sc: Scope | None, entity: str, labels: list[tuple[str, str, str]], actor: str,
                reason: str | None = None) -> int:
    """labels: (rec_a, rec_b, match|non_match). A non_match is a negative label: never auto-linked again."""
    _reviewer(sc, actor)
    for a, b, lab in labels:
        if lab not in ("match", "non_match"):
            raise Refused(f"label for {a} / {b} must be match or non_match, not '{lab}'")
        a, b = sorted([a, b])
        r = cat.one("SELECT * FROM er_pair WHERE entity = ? AND rec_a = ? AND rec_b = ?", (entity, a, b))
        if r is None:
            raise Refused(f"no scored pair {a} / {b} for entity '{entity}'")
        cat.execute("UPDATE er_pair SET label = ?, reviewer = ?, reviewed_at = ? WHERE entity = ? AND rec_a = ?"
                    " AND rec_b = ?", (lab, actor, now(), entity, a, b))
        log(cat, sc, "resolve.review", f"{entity}:{a}|{b}", r["label"], lab, actor, reason,
            probability=r["probability"])
    if labels:
        recluster(cat, entity, _last_run(cat, entity)["run_id"])
    return len(labels)


def _last_run(cat: Catalog, entity: str) -> dict:
    r = cat.one("SELECT * FROM er_run WHERE entity = ? ORDER BY created_at DESC LIMIT 1", (entity,))
    if r is None:
        raise Refused(f"no entity-resolution run for '{entity}'; run 'konmari resolve run' first")
    return r


def export_review(cat: Catalog, entity: str, path: str | Path, sample: int = DEFAULTS["sample"]) -> int:
    """CSV for reviewers: the review queue plus a precision sample of unlabelled auto-linked pairs."""
    vals = current_values(cat, entity)
    show = lambda k: json.dumps({a: r["canonical"] for a, r in sorted(vals.get(k, {}).items())},  # noqa: E731
                                ensure_ascii=False)
    auto = [r for r in cat.query("SELECT * FROM er_pair WHERE entity = ? AND origin = 'auto' AND label IS NULL",
                                 (entity,))]
    auto = sorted(auto, key=lambda r: hashlib.sha256(f"{r['rec_a']}|{r['rec_b']}".encode()).hexdigest())[:sample]
    rows = [("review", r) for r in queue(cat, entity)] + [("precision_sample", r) for r in auto]
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["kind", "entity", "rec_a", "rec_b", "probability", "values_a", "values_b", "label"])
        for kind, r in rows:
            w.writerow([kind, entity, r["rec_a"], r["rec_b"], f"{r['probability']:.4f}", show(r["rec_a"]),
                        show(r["rec_b"]), ""])
    return len(rows)


def import_review(cat: Catalog, sc: Scope | None, path: str | Path, actor: str) -> int:
    by_entity = defaultdict(list)
    with Path(path).open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("label") or "").strip():
                by_entity[row["entity"]].append((row["rec_a"], row["rec_b"], row["label"].strip()))
    return sum(label_pairs(cat, sc, e, labels, actor, reason=f"imported from {Path(path).name}")
               for e, labels in by_entity.items())


# ---------------------------------------------------------------- quality gate and publication

def read_known(path: str | Path) -> list[tuple[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        return [(r["rec_a"], r["rec_b"]) for r in csv.DictReader(f)]


def quality(cat: Catalog, sc: Scope | None, entity: str, known: list[tuple[str, str]] | None) -> dict:
    t = success_targets(cat, sc)
    auto = cat.query("SELECT label FROM er_pair WHERE entity = ? AND origin = 'auto'", (entity,))
    labelled = [r["label"] for r in auto if r["label"]]
    need = min(t["sample"], len(auto))
    rep = {"floors": {"precision": t["precision"], "recall": t["recall"]}, "linked_pairs": len(auto),
           "labelled": len(labelled), "sample_needed": need, "failures": []}
    if not auto:
        rep["precision"] = "no linked pairs"
    elif len(labelled) < need:
        rep["precision"] = "unmeasured"
        rep["failures"].append(f"precision unmeasured: {len(labelled)} of {need} required linked pairs are labelled"
                               " (konmari resolve review --export)")
    else:
        rep["precision"] = round(labelled.count("match") / len(labelled), 4)
        if rep["precision"] < t["precision"]:
            rep["failures"].append(f"precision {rep['precision']} is below the floor {t['precision']} "
                                   f"(success-metrics {METRIC_IDS['precision']})")
    if not known:
        rep["recall"] = "unmeasured"
    else:
        where = {r["source"] + ":" + r["record_id"]: r["cluster_id"]
                 for rows in crosswalk(cat, entity).values() for r in rows}
        found = sum(1 for a, b in known if a in where and where.get(a) == where.get(b))
        rep["recall"] = round(found / len(known), 4)
        if rep["recall"] < t["recall"]:
            rep["failures"].append(f"recall {rep['recall']} is below the floor {t['recall']} "
                                   f"(success-metrics {METRIC_IDS['recall']})")
    return rep


def publish(cat: Catalog, sc: Scope | None, entity: str, actor: str, known=None, override_reason=None) -> dict:
    """Release the current clusters to golden records, only when precision and recall meet the floors."""
    run_ = _last_run(cat, entity)
    rep = quality(cat, sc, entity, known)
    if rep["recall"] == "unmeasured":
        if not override_reason:
            rep["failures"].append("recall unmeasured: supply the owner's known-duplicate list (--known) or an "
                                   "owner override (--override-recall-reason)")
        elif sc is not None and "data_owner" not in sc.stakeholders.get(actor, {}).get("roles", []):
            rep["failures"].append(f"'{actor}' is not a data owner and cannot override unmeasured recall")
    run_metrics = json.loads(run_["metrics"]) | {"quality": rep}
    cat.execute("UPDATE er_run SET metrics = ? WHERE run_id = ?", (json.dumps(run_metrics), run_["run_id"]))
    for name in ("precision", "recall"):
        if isinstance(rep[name], float):
            metric(cat, "er", name, rep[name], run_["run_id"], entity)
    if rep["failures"]:
        log(cat, sc, "resolve.publish", entity, None, None, actor, "; ".join(rep["failures"]), result="refused",
            run_id=run_["run_id"])
        raise Refused(rep["failures"])
    cat.execute("UPDATE er_run SET published_at = ?, published_by = ?, override_reason = ? WHERE run_id = ?",
                (now(), actor, override_reason if rep["recall"] == "unmeasured" else None, run_["run_id"]))
    log(cat, sc, "resolve.publish", entity, None, {"run_id": run_["run_id"], "precision": rep["precision"],
                                                   "recall": rep["recall"]}, actor, override_reason,
        result="published", override=bool(override_reason and rep["recall"] == "unmeasured"))
    return rep


def published(cat: Catalog, entity: str) -> bool:
    r = cat.one("SELECT published_at FROM er_run WHERE entity = ? ORDER BY created_at DESC LIMIT 1", (entity,))
    return bool(r and r["published_at"])


# ---------------------------------------------------------------- split and restore

def _rebuild(cat: Catalog, sc: Scope | None, entity: str, actor: str) -> None:
    from konmari.policy import golden

    if golden.active_policy(cat, entity):
        golden.build(cat, sc, entity, actor)


def split(cat: Catalog, sc: Scope | None, entity: str, cid: str, records: list[str], actor: str, reason: str) -> dict:
    _reviewer(sc, actor)
    members = [r["source"] + ":" + r["record_id"] for r in crosswalk(cat, entity).get(cid, [])]
    part = set(records)
    if not members:
        raise Refused(f"cluster '{cid}' not found for entity '{entity}'")
    if not part < set(members) or not part:
        raise Refused(f"--records must be a non-empty proper subset of the members of {cid}: {', '.join(members)}")
    cut = [r for r in cat.query("SELECT * FROM er_pair WHERE entity = ?", (entity,))
           if _linked(r) and (r["rec_a"] in part) != (r["rec_b"] in part)
           and {r["rec_a"], r["rec_b"]} <= set(members)]
    remaining = [e for e in _edges(cat, entity, set(members))[0] if e not in {(r["rec_a"], r["rec_b"]) for r in cut}]
    if len(_components(set(members), remaining)) < 2:
        raise Refused("the two groups stay connected through within-source duplicates (c04); revert those first")
    split_id = new_id()
    for r in cut:
        cat.execute("UPDATE er_pair SET prev_label = label, label = 'non_match', split_id = ?, reviewer = ?,"
                    " reviewed_at = ? WHERE entity = ? AND rec_a = ? AND rec_b = ?",
                    (split_id, actor, now(), entity, r["rec_a"], r["rec_b"]))
    comps = recluster(cat, entity, _last_run(cat, entity)["run_id"])
    new = [cluster_id(m) for m in comps if set(m) & set(members)]
    log(cat, sc, "resolve.split", f"{entity}:{cid}", members, new, actor, reason, split_id=split_id,
        links_cut=len(cut))
    _rebuild(cat, sc, entity, actor)
    return {"split_id": split_id, "clusters": new, "links_cut": len(cut)}


def restore(cat: Catalog, sc: Scope | None, split_id: str, actor: str, reason: str | None = None) -> dict:
    _reviewer(sc, actor)
    rows = cat.query("SELECT * FROM er_pair WHERE split_id = ?", (split_id,))
    if not rows:
        raise Refused(f"split '{split_id}' not found or already restored")
    entity = rows[0]["entity"]
    cat.execute("UPDATE er_pair SET label = prev_label, prev_label = NULL, split_id = NULL WHERE split_id = ?",
                (split_id,))
    comps = recluster(cat, entity, _last_run(cat, entity)["run_id"])
    keys = {k for r in rows for k in (r["rec_a"], r["rec_b"])}
    restored = sorted({cluster_id(m) for m in comps if set(m) & keys})
    log(cat, sc, "resolve.restore", f"{entity}:{split_id}", "split", restored, actor, reason)
    _rebuild(cat, sc, entity, actor)
    return {"entity": entity, "clusters": restored}
