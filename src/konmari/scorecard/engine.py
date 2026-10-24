"""Scoring runs: pilot scope, probes, keep/fix/discard proposals with gates, re-scoring, category maturity."""

import json
import uuid
from pathlib import Path

from konmari.canonical import canonical_hash, load_yaml
from konmari.catalog import Catalog, audit, new_id, now
from konmari.categories import store as cats
from konmari.charter.gate import gate
from konmari.charter.store import Scope, load_charter, scope
from konmari.inventory import lineage
from konmari.inventory import store as inv
from konmari.scorecard import rubric as rb
from konmari.scorecard.probes import PROBES, Context, run_probe
from konmari.versions import Refused

STEP = "score"
# Pilot thresholds, all our assumptions (design "Risks"); override per pilot in scorecard.yaml.
DEFAULT_CONFIG = {"keep_level": 2, "stale_multiple": 2.0, "agent_ready_share": 0.8, "in_progress_share": 0.4,
                  "evidence_max_age_days": 7}
# Downstream capability expected to close a fix item, per criterion.
FIX_ROUTES = {"authoritative_current": "source-of-truth-policy", "owned_maintained": "source-inventory",
              "meaning_clear": "business-glossary", "retrievable_precise": "document-chunking",
              "conflicts_resolved": "dedup-detection", "permissions_explicit": "access-control"}
# Brief table aspect -> {criterion: minimum level}; None = the keep level (design decision 7).
ASPECTS = {"Format": {"retrievable_precise": None},
           "Metadata": {"meaning_clear": None, "owned_maintained": None},
           "Access": {"permissions_explicit": None},
           "Consistency": {"conflicts_resolved": None},
           "Update mechanism": {"authoritative_current": 3},
           "Discoverability": {"retrievable_precise": None, "meaning_clear": None}}
INVENTORY_FIELDS = ["owner", "steward", "update_frequency", "last_modified", "format", "sensitivity_tier", "tier_state",
                    "ropa_ref", "status", "content_hash", "schema_hash"]


def aggregation_rule(cfg: dict) -> str:
    return (f"An aspect's share counts the category's in-scope assets whose mapped criteria all reach the bar "
            f"(level >= {cfg['keep_level']}; Update mechanism needs authoritative_current = 3); unknown counts as "
            f"not meeting it. agent_ready at share >= {cfg['agent_ready_share']:.0%}, in_progress at share >= "
            f"{cfg['in_progress_share']:.0%}, otherwise poorly_prepared.")


def load_config(path: str | Path | None = None) -> dict:
    """Thresholds from `path` (default scorecard.yaml if present) over the defaults."""
    p = Path(path or "scorecard.yaml")
    if not p.is_file():
        if path:
            raise Refused(f"config file '{path}' not found")
        return dict(DEFAULT_CONFIG)
    doc = load_yaml(p) or {}
    if not isinstance(doc, dict):
        raise Refused(f"{p}: expected a mapping of thresholds")
    errors = [f"{p}: unknown setting '{k}' (valid: {', '.join(DEFAULT_CONFIG)})" for k in doc if k not in DEFAULT_CONFIG]
    errors += [f"{p}: '{k}' must be a number" for k, v in doc.items()
               if k in DEFAULT_CONFIG and (isinstance(v, bool) or not isinstance(v, (int, float)))]
    if errors:
        raise Refused(errors)
    return DEFAULT_CONFIG | doc


# ---------------------------------------------------------------- scope

def split_scope(cat: Catalog, sc: Scope) -> tuple[dict[str, list[dict]], list[dict]]:
    """({category: [assets]}, [out-of-scope {asset_uri, reason}]) for the charter's inventoried assets."""
    groups: dict[str, list[dict]] = {}
    out = []
    for a in inv.records(cat, sc.charter_id):
        if a["status"] == "missing":
            continue
        p = cats.primary(cat, a["asset_uri"])
        if not p or p["state"] != "accepted":
            out.append({"asset_uri": a["asset_uri"], "reason": "no accepted primary category"})
        elif p["category_id"] not in sc.categories:
            out.append({"asset_uri": a["asset_uri"],
                        "reason": f"category '{p['category_id']}' is not in the pilot charter scope"})
        else:
            groups.setdefault(p["category_id"], []).append(a)
    return groups, out


def inventory_hash(asset: dict) -> str:
    return canonical_hash({k: str(asset.get(k)) for k in INVENTORY_FIELDS})


# ---------------------------------------------------------------- verdict

def decide(levels: dict, gates: set, cfg: dict, *, irrelevant_ref=None, winner=None, stale_ratio=None,
           owner_active=True) -> dict:
    """The one proposed verdict for an asset (spec "Verdict proposal with gates")."""
    discard = None
    if irrelevant_ref:
        discard = ("irrelevant", irrelevant_ref)
    elif winner:
        discard = ("redundant", winner)
    elif stale_ratio is not None and stale_ratio > cfg["stale_multiple"] and not owner_active:
        discard = ("stale_unowned", None)
    if discard:
        return {"proposed": "discard", "reason": discard[0], "reason_ref": discard[1], "fix_items": []}
    below = [c for c in rb.CRITERIA if levels.get(c) is None or levels[c] < cfg["keep_level"]
             or (c in gates and levels[c] == 0)]
    items = [{"criterion": c, "level": "unknown" if levels.get(c) is None else levels[c],
              "capability": FIX_ROUTES[c]} for c in below]
    return {"proposed": "fix" if below else "keep", "reason": None, "reason_ref": None, "fix_items": items}


def _decide_asset(asset: dict, results: dict, ctx: Context, gates: set) -> dict:
    uri = asset["asset_uri"]
    rel = ctx.latest(uri, "relevance")
    dd = ctx.latest(uri, "dedup_check")
    sup = ctx.latest(uri, "superseded")
    winner = (dd["payload"].get("winner") if dd else None) or (sup["payload"].get("superseded_by") if sup else None)
    return decide({c: r.level for c, r in results.items()}, gates, ctx.cfg,
                  irrelevant_ref=f"evidence:{rel['evidence_id']}" if rel and rel["payload"]["relevant"] is False
                  else None,
                  winner=winner if winner and winner != uri else None,
                  stale_ratio=results["authoritative_current"].evidence.get("stale_ratio"),
                  owner_active=results["owned_maintained"].evidence.get("owner_active", True))


def latest_verdict(cat: Catalog, uri: str) -> dict | None:
    row = cat.one("SELECT * FROM verdict WHERE asset_uri = ? ORDER BY created_at DESC LIMIT 1", (uri,))
    if row:
        row["fix_items"] = json.loads(row["fix_items"])
    return row


def reviews(cat: Catalog, uri: str) -> list[dict]:
    return cat.query("SELECT * FROM verdict_review WHERE asset_uri = ? ORDER BY reviewed_at, review_id", (uri,))


def add_review(cat: Catalog, uri: str, verdict_id: str, action: str, actor: str, frm, to: str, justification,
               rubric_version: str) -> dict:
    row = {"review_id": new_id(), "asset_uri": uri, "verdict_id": verdict_id, "action": action, "actor": actor,
           "from_verdict": frm, "to_verdict": to, "justification": justification, "rubric_version": rubric_version,
           "reviewed_at": now()}
    cat.execute("INSERT INTO verdict_review VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row.values()))
    return row


def _store(cat, run_id, sc, category, asset, results, verdict, rubric_version, actor) -> dict:
    uri = asset["asset_uri"]
    for c, r in results.items():
        cat.execute("INSERT INTO criterion_score VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (run_id, uri, c, r.level, r.probe, r.evidence_ref, r.evidence_at,
                     json.dumps(r.evidence, sort_keys=True, default=str), r.stale, rubric_version))
    prev = latest_verdict(cat, uri)
    vid = new_id()
    cat.execute("INSERT INTO verdict VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (vid, run_id, uri, sc.charter_id, category, rubric_version, verdict["proposed"], verdict["reason"],
                 verdict["reason_ref"], json.dumps(verdict["fix_items"]),
                 any(r.level is None for r in results.values()), inventory_hash(asset), now()))
    if prev is None or prev["proposed"] != verdict["proposed"]:
        hist = reviews(cat, uri)
        add_review(cat, uri, vid, "propose", actor, hist[-1]["to_verdict"] if hist else None, verdict["proposed"],
                   None, rubric_version)
        audit(cat, "scorecard.propose", actor=actor, charter_id=sc.charter_id, charter_version=sc.charter_version,
              step=STEP, result="proposed",
              details={"entity": uri, "from": prev["proposed"] if prev else None, "to": verdict["proposed"],
                       "reason": verdict["reason"], "rubric_version": rubric_version, "run_id": run_id})
    return {"asset_uri": uri, "category": category, **verdict}


# ---------------------------------------------------------------- category maturity

def rollup(asset_levels: list[dict], cfg: dict) -> list[dict]:
    """Per aspect: share of assets whose mapped criteria all reach the bar (unknown = not met) and the state."""
    out = []
    n = len(asset_levels)
    for aspect, crit in ASPECTS.items():
        bar = {c: lv or cfg["keep_level"] for c, lv in crit.items()}
        meeting = sum(1 for lv in asset_levels if all(lv.get(c) is not None and lv[c] >= b for c, b in bar.items()))
        share = round(meeting / n, 4) if n else 0.0
        state = "agent_ready" if n and share >= cfg["agent_ready_share"] else \
            "in_progress" if n and share >= cfg["in_progress_share"] else "poorly_prepared"
        out.append({"aspect": aspect, "criteria": bar, "assets": n, "meeting": meeting, "share": share,
                    "state": state})
    return out


def current_levels(cat: Catalog, uri: str) -> dict | None:
    v = latest_verdict(cat, uri)
    if v is None:
        return None
    return {r["criterion"]: r["level"] for r in cat.query(
        "SELECT criterion, level FROM criterion_score WHERE run_id = ? AND asset_uri = ?", (v["run_id"], uri))}


# ---------------------------------------------------------------- runs

def _run_event(event_type: str, run_id: str, sc: Scope, uris: list[str], rubric_version: str) -> dict:
    return {"eventType": event_type, "eventTime": now().isoformat() + "Z", "producer": lineage.PRODUCER,
            "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/definitions/RunEvent",
            "run": {"runId": str(uuid.UUID(run_id)),
                    "facets": {"konmari": {"charter_id": sc.charter_id, "charter_version": sc.charter_version,
                                           "rubric_version": rubric_version}}},
            "job": {"namespace": "konmari", "name": "scorecard.score"},
            "inputs": [{"namespace": lineage.namespace(u), "name": u} for u in uris], "outputs": []}


def score(cat: Catalog, charter_path="charter.yaml", actor: str = "konmari-score", cfg: dict | None = None,
          rubric_path=None, sources=None, only: set | None = None, trigger: str = "manual", transport=None,
          at=None) -> dict:
    """One scoring run over the pilot scope (or only the assets in `only`). Returns the run summary."""
    res = gate(STEP, charter_path, cat, actor)
    if not res.passed:
        raise Refused([f"no approved pilot charter for '{charter_path}': {u}" if u.startswith("charter:") else u
                       for u in res.unmet])
    sc = scope(load_charter(charter_path)["charter_id"], cat)
    cfg = cfg or load_config()
    rubric = rb.load(rubric_path)
    ver = rb.publish(cat, rubric, actor)["version"]
    gates = rb.gates(rubric)
    groups, out = split_scope(cat, sc)
    run_id = new_id()
    cat.execute("INSERT INTO scorecard_run (run_id, charter_id, charter_version, rubric_version, config, run_trigger,"
                " actor, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, sc.charter_id, sc.charter_version, ver, json.dumps(cfg, sort_keys=True), trigger, actor,
                 now()))
    transport = transport or lineage.default_transport()
    todo = [a["asset_uri"] for g in groups.values() for a in g if only is None or a["asset_uri"] in only]
    transport.emit(_run_event("START", run_id, sc, todo, ver))
    ctx = Context(cat, cfg, sc.stakeholders, sources, at)
    scored, blocked = [], {}
    order = cats.approved_order(cat, sc.charter_id)
    for category in order["categories"] if order else sorted(groups):
        assets = [a for a in groups.get(category, []) if only is None or a["asset_uri"] in only]
        if not assets:
            continue
        if reasons := cats.check_step(cat, sc, category, STEP):
            blocked[category] = reasons
            continue
        for a in assets:
            results = {}
            for p in PROBES:
                r, stale = run_probe(p, a, ctx)
                r.probe, r.stale = p.name, stale
                results[p.criterion] = r
            scored.append(_store(cat, run_id, sc, category, a, results, _decide_asset(a, results, ctx, gates), ver,
                                 actor))
        if cats.step_status(cat, sc.charter_id, category, STEP)["status"] == "not_started":
            cats.set_step(cat, sc, category, STEP, "in_progress", actor)
        levels = [lv for a in groups[category] if (lv := current_levels(cat, a["asset_uri"])) is not None]
        for m in rollup(levels, cfg):
            cat.execute("INSERT INTO category_maturity VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, category, m["aspect"], json.dumps(m["criteria"]), m["assets"], m["meeting"],
                         m["share"], m["state"]))
    summary = {"run_id": run_id, "rubric_version": ver, "charter_id": sc.charter_id,
               "charter_version": sc.charter_version, "trigger": trigger, "config": cfg,
               "scored": scored, "out_of_scope": out, "blocked": blocked}
    cat.execute("UPDATE scorecard_run SET finished_at = ?, summary = ? WHERE run_id = ?",
                (now(), json.dumps({k: summary[k] for k in ("scored", "out_of_scope", "blocked")}, default=str),
                 run_id))
    audit(cat, "scorecard.run", actor=actor, charter_id=sc.charter_id, charter_version=sc.charter_version, step=STEP,
          result="completed", details={"run_id": run_id, "rubric_version": ver, "trigger": trigger, "config": cfg,
                                       "scored": len(scored), "out_of_scope": len(out), "blocked": sorted(blocked)})
    transport.emit(_run_event("COMPLETE", run_id, sc, [s["asset_uri"] for s in scored], ver))
    return summary


def changed_assets(cat: Catalog, sc: Scope) -> set[str]:
    """In-scope assets whose inventory entry changed since they were last scored (or never scored)."""
    groups, _ = split_scope(cat, sc)
    return {a["asset_uri"] for g in groups.values() for a in g
            if (v := latest_verdict(cat, a["asset_uri"])) is None or v["inventory_hash"] != inventory_hash(a)}


def rescore(cat: Catalog, uri: str, trigger: str, actor: str, charter_path="charter.yaml", detail=None,
            **kw) -> dict:
    """Re-score one asset after a fix item completed or a freshness alert; audited with the trigger."""
    sc = scope(load_charter(charter_path)["charter_id"], cat)
    audit(cat, f"scorecard.trigger.{trigger}", actor=actor, charter_id=sc.charter_id, step=STEP, result="recorded",
          details={"entity": uri, **(detail or {})})
    return score(cat, charter_path, actor, only={uri}, trigger=trigger, **kw)


def complete_fix(cat: Catalog, uri: str, criterion: str, actor: str, charter_path="charter.yaml", **kw) -> dict:
    v = latest_verdict(cat, uri)
    if v is None or criterion not in {i["criterion"] for i in v["fix_items"]}:
        raise Refused(f"'{uri}' has no open fix item for '{criterion}'")
    return rescore(cat, uri, "fix_completed", actor, charter_path, {"criterion": criterion}, **kw)
