"""Regression gates, baselines and feedback (design D3, D5, D9): thresholds from the charter's success metrics over
the documented defaults, the 0.05 regression rule against the approved baseline, failed runs and uncalibrated
judge metrics never pass; per-category results go to the readiness scorecard, the next category needs a human."""

import json

from konmari import versions as v
from konmari.catalog import Catalog, new_id, now
from konmari.categories import store as cats
from konmari.charter.store import Scope
from konmari.evaluate import run as er
from konmari.evaluate.golden import event
from konmari.scorecard import probes as sp
from konmari.versions import Refused

# Defaults (design D5): 0.80 grounding follows the promptfoo example; the others are our assumptions (R§2).
DEFAULTS = {"faithfulness": 0.80, "citation_precision": 0.80, "citation_recall": 0.80, "citation_check": 1.0,
            "abstention_rate": 0.90, "answer_rate": 0.85}
REGRESSION = 0.05            # assumption: allowed drop of any aggregate against the baseline
JUDGE_METRICS = ("faithfulness", "citation_precision", "citation_recall")
BASELINE_METRICS = ("faithfulness", "context_precision", "context_recall", "citation_precision", "citation_recall",
                    "citation_check", "abstention_rate", "answer_rate", "pass_rate")
CONFIRM_ROLES = {"use_case_owner", "sponsor"}


def thresholds(cat: Catalog, charter_id: str) -> tuple[dict, dict]:
    """(thresholds, origin per metric): an approved success metric named like a gate metric, or of family
    `abstention`, sets that gate's threshold."""
    out, origin = dict(DEFAULTS), {k: "default" for k in DEFAULTS}
    ms = v.latest_approved(cat, "metric_set_version", charter_id)
    for m in (ms or {}).get("doc", {}).get("metrics") or []:
        key = m["id"] if m["id"] in DEFAULTS else "abstention_rate" if m.get("family") == "abstention" else None
        if key and m.get("direction") == ">=" and isinstance(m.get("target"), (int, float)):
            out[key] = m["target"] / 100 if m.get("unit") == "%" else m["target"]
            origin[key] = f"charter metric {m['id']} (metric set v{ms['version']})"
    return out, origin


def baseline(cat: Catalog, charter_id: str) -> dict | None:
    return cat.one("SELECT * FROM baseline WHERE charter_id = ? ORDER BY seq DESC LIMIT 1", (charter_id,))


def _failing(r: dict, agg: dict, thr: dict) -> list[str]:
    """Metrics of one aggregate that fail: below threshold, judge-based while uncalibrated, or a failed run."""
    out = [k for k, t in thr.items() if agg.get(k) is not None and (agg[k] < t - 1e-9 or (
        k in JUDGE_METRICS and r["judge_status"] != "calibrated"))]
    return out + (["run_failed"] if r["status"] != "completed" else [])


def evaluate(cat: Catalog, sc: Scope, run_id: str | None = None, actor: str = "konmari-eval") -> dict:
    """`konmari eval gate`: {passed, run_id, failures: [...]}; audited."""
    r = er.get(cat, run_id) if run_id else er.latest(cat, sc.charter_id)
    if r is None:
        raise Refused("no evaluation run; run 'konmari eval run' first")
    thr, origin = thresholds(cat, sc.charter_id)
    fails = []
    if r["status"] != "completed":
        fails.append(f"run {r['run_id']} is {r['status']}" + (f": {r['error']}" if r["error"] else "")
                     + "; no gate passes on a failed run")
    agg = (r["aggregates"] or {}).get("run") or {}
    uncal = r["judge_status"] != "calibrated"
    for k, t in thr.items():
        if uncal and k in JUDGE_METRICS and agg.get(k) is not None:
            fails.append(f"{k}: judge {r['judge_model']} {r['judge_version']} is uncalibrated; judge-based metrics "
                         "do not pass")
        elif agg.get(k) is not None and agg[k] < t - 1e-9:
            fails.append(f"{k}: {agg[k]} is below the threshold {t} ({origin[k]})")
    base = baseline(cat, sc.charter_id)
    if base and base["run_id"] != r["run_id"]:
        bagg = (er.get(cat, base["run_id"])["aggregates"] or {}).get("run") or {}
        for k in BASELINE_METRICS:
            if agg.get(k) is not None and bagg.get(k) is not None and agg[k] < bagg[k] - REGRESSION - 1e-9:
                fails.append(f"{k} regressed: baseline {bagg[k]} (run {base['run_id']}), new {agg[k]}, allowed drop "
                             f"{REGRESSION}")
    passed = not fails
    event(cat, "eval.gate", actor, sc, result="passed" if passed else "failed", run_id=r["run_id"],
          baseline=base and base["run_id"], failures=fails, thresholds=thr)
    return {"passed": passed, "run_id": r["run_id"], "failures": fails, "thresholds": thr,
            "baseline": base and base["run_id"]}


# ---------------------------------------------------------------- baseline

def _insert_baseline(cat, sc, run_id, previous, kind, actor):
    last = cat.one("SELECT MAX(seq) AS s FROM baseline WHERE charter_id = ?", (sc.charter_id,))
    cat.execute("INSERT INTO baseline VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sc.charter_id, (last["s"] or 0) + 1, run_id, previous, kind, actor, now()))


def approve_baseline(cat: Catalog, sc: Scope, run_id: str, actor: str) -> dict:
    r = er.get(cat, run_id)
    if r["charter_id"] != sc.charter_id or r["status"] != "completed":
        raise Refused(f"run {run_id} is {r['status']}; only a completed run of this pilot can become the baseline")
    cur = baseline(cat, sc.charter_id)
    prev = cur and cur["run_id"]
    _insert_baseline(cat, sc, run_id, prev, "approve", actor)
    event(cat, "eval.baseline.approve", actor, sc, previous=prev, new=run_id)
    return {"previous": prev, "baseline": run_id}


def revert_baseline(cat: Catalog, sc: Scope, actor: str) -> dict:
    cur = baseline(cat, sc.charter_id)
    if cur is None or cur["previous_run"] is None:
        raise Refused("there is no previous baseline to revert to")
    target = cur["previous_run"]
    # the baseline that was active before `target` became active, so repeated reverts walk back in history
    before = cat.one("SELECT previous_run FROM baseline WHERE charter_id = ? AND run_id = ? AND seq < ?"
                     " ORDER BY seq DESC LIMIT 1", (sc.charter_id, target, cur["seq"]))
    _insert_baseline(cat, sc, target, before and before["previous_run"], "revert", actor)
    event(cat, "eval.baseline.revert", actor, sc, previous=cur["run_id"], new=target)
    return {"previous": cur["run_id"], "baseline": target}


# ---------------------------------------------------------------- feedback

def feedback(cat: Catalog, sc: Scope, run_id: str, actor: str = "konmari-eval") -> dict:
    """Per-category aggregates to the readiness scorecard (evidence `agent_eval` and `retrieval_eval` on every
    accepted asset of the category); categories failing a threshold are `fix` candidates."""
    r = er.get(cat, run_id)
    thr, _ = thresholds(cat, sc.charter_id)
    out = {"run_id": run_id, "fix": [], "categories": {}}
    res = er.results(cat, run_id)
    for cid, agg in ((r["aggregates"] or {}).get("categories") or {}).items():
        failing = _failing(r, agg, thr)
        status = "fix" if failing else "pass"
        if status == "fix":
            out["fix"].append(cid)
        answers = [x for x in res if x["category_id"] == cid and x["expected_behaviour"] == "answer"]
        payload = {k: agg[k] for k in ("pass_rate", "faithfulness", "citation_precision", "citation_recall",
                                       "citation_check", "abstention_rate") if agg.get(k) is not None}
        payload |= {"status": status, "items": agg["items"]}
        assets = cats.members(cat, sc, cid, accepted_only=True)
        for a in assets:
            sp.record_evidence(cat, a["asset_uri"], "agent_eval", payload, f"eval-run:{run_id}", actor)
            if answers:
                hit = sum((x["context_recall"] or 0) > 0 for x in answers) / len(answers)
                mrr = sum(x["details"].get("reciprocal_rank", 0) for x in answers) / len(answers)
                sp.record_evidence(cat, a["asset_uri"], "retrieval_eval",
                                   {"hit_rate": round(hit, 4), "mrr": round(mrr, 4), "k": 0, "questions": len(answers)},
                                   f"eval-run:{run_id}", actor)
        out["categories"][cid] = {"status": status, "failing": failing, "assets": [a["asset_uri"] for a in assets]}
    event(cat, "eval.feedback", actor, sc, run_id=run_id, fix=out["fix"])
    return out


def active_category(cat: Catalog, charter_id: str) -> str | None:
    r = cat.one("SELECT category_id FROM category_focus WHERE charter_id = ? AND status = 'confirmed'"
                " ORDER BY confirmed_at DESC LIMIT 1", (charter_id,))
    return r and r["category_id"]


def propose_next(cat: Catalog, sc: Scope, run_id: str, actor: str = "konmari-eval") -> dict | None:
    """When every category of the run passes, propose the next category of the approved order (c02 category map);
    nothing changes until a human confirms."""
    fb = feedback_status(cat, sc, run_id)
    if not fb["categories"] or fb["fix"]:
        return None
    order = (cats.approved_order(cat, sc.charter_id) or {}).get("categories") or \
        [c["category_id"] for c in cats.categories(cat, sc)]
    order = json.loads(order) if isinstance(order, str) else order
    done = set(fb["categories"]) | {r["category_id"] for r in cat.query(
        "SELECT category_id FROM category_focus WHERE charter_id = ? AND status = 'confirmed'", (sc.charter_id,))}
    nxt = next((c for c in order if c not in done), None)
    if nxt is None:
        return None
    pid = new_id()[:12]
    cat.execute("INSERT INTO category_focus VALUES (?, ?, ?, ?, 'proposed', ?, NULL, NULL)",
                (pid, sc.charter_id, nxt, run_id, now()))
    event(cat, "eval.next_category.propose", actor, sc, proposal_id=pid, category=nxt, run_id=run_id)
    return {"proposal_id": pid, "category": nxt, "status": "proposed"}


def feedback_status(cat: Catalog, sc: Scope, run_id: str) -> dict:
    """Which categories of a run pass their thresholds (no writes)."""
    r = er.get(cat, run_id)
    thr, _ = thresholds(cat, sc.charter_id)
    cats_ = (r["aggregates"] or {}).get("categories") or {}
    fix = [c for c, a in cats_.items() if _failing(r, a, thr)]
    return {"categories": list(cats_), "fix": fix}


def confirm_next(cat: Catalog, sc: Scope, proposal_id: str, actor: str) -> dict:
    p = cat.one("SELECT * FROM category_focus WHERE proposal_id = ? AND charter_id = ?", (proposal_id, sc.charter_id))
    if p is None or p["status"] != "proposed":
        raise Refused(f"no open next-category proposal '{proposal_id}'")
    roles = set((sc.stakeholders.get(actor) or {}).get("roles") or [])
    if not roles & CONFIRM_ROLES:
        raise Refused(f"'{actor}' cannot confirm the next category (roles {', '.join(sorted(CONFIRM_ROLES))})")
    prev = active_category(cat, sc.charter_id)
    cat.execute("UPDATE category_focus SET status = 'confirmed', confirmed_by = ?, confirmed_at = ? WHERE"
                " proposal_id = ?", (actor, now(), proposal_id))
    event(cat, "eval.next_category.confirm", actor, sc, proposal_id=proposal_id, previous=prev,
          new=p["category_id"])
    return {"category": p["category_id"], "previous": prev}
