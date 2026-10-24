"""Scorecard reports (Markdown, CSV, JSON) and run comparison."""

import csv
import io
import json

from konmari.catalog import Catalog, now
from konmari.charter.store import Scope
from konmari.schema import schema_errors
from konmari.scorecard import rubric as rb
from konmari.scorecard.engine import ASPECTS, aggregation_rule, current_levels, rollup, split_scope
from konmari.scorecard.review import status
from konmari.versions import Refused

SHORT = {"authoritative_current": "AC", "owned_maintained": "OM", "meaning_clear": "MC", "retrievable_precise": "RP",
         "conflicts_resolved": "CR", "permissions_explicit": "PE"}


def _iso(ts):
    return ts.isoformat() if ts else None


def build(cat: Catalog, sc: Scope) -> dict:
    run = cat.one("SELECT * FROM scorecard_run WHERE charter_id = ? ORDER BY started_at DESC LIMIT 1",
                  (sc.charter_id,))
    if run is None:
        raise Refused(f"no scorecard run for charter '{sc.charter_id}'; run 'konmari score' first")
    cfg = json.loads(run["config"])
    groups, _ = split_scope(cat, sc)
    assets, categories = [], []
    for cid in sorted(groups):
        rows, levels = [], []
        counts = {"keep": 0, "fix": 0, "discard": 0, "unreviewed": 0}
        for a in groups[cid]:
            st = status(cat, a["asset_uri"])
            v = st["verdict"]
            if v is None:
                continue
            crit = {r["criterion"]: {"level": "unknown" if r["level"] is None else r["level"], "probe": r["probe"],
                                     "evidence_ref": r["evidence_ref"], "evidence_at": _iso(r["evidence_at"]),
                                     "stale_evidence": bool(r["stale_evidence"])}
                    for r in cat.query("SELECT * FROM criterion_score WHERE run_id = ? AND asset_uri = ?",
                                       (v["run_id"], a["asset_uri"]))}
            levels.append(current_levels(cat, a["asset_uri"]))
            counts["unreviewed" if st["status"] == "proposed" else st["effective"]] += 1
            rows.append({"asset_uri": a["asset_uri"], "title": a["title"], "category": cid,
                         "criteria": {c: crit[c] for c in rb.CRITERIA},
                         "verdict": {"proposed": v["proposed"], "reason": v["reason"], "reason_ref": v["reason_ref"],
                                     "status": st["status"], "effective": st["effective"],
                                     "fix_items": v["fix_items"]},
                         "rubric_version": v["rubric_version"], "run_id": v["run_id"]})
        assets += rows
        categories.append({"category": cid, "assets": len(rows), "aspects": rollup(levels, cfg),
                           "verdicts": counts})
    return {"charter_id": sc.charter_id, "charter_version": sc.charter_version, "run_id": run["run_id"],
            "rubric_version": run["rubric_version"], "generated_at": now().isoformat(), "config": cfg,
            "aggregation_rule": aggregation_rule(cfg), "aspect_criteria": {k: sorted(v) for k, v in ASPECTS.items()},
            "assets": assets, "categories": categories}


def validate(rep: dict) -> list[str]:
    return schema_errors(rep, "scorecard-report")


def to_json(rep: dict) -> str:
    return json.dumps(rep, indent=2, ensure_ascii=False, sort_keys=True)


def _fix(items) -> str:
    return "; ".join(f"{i['criterion']} ({i['level']}) -> {i['capability']}" for i in items)


def _oldest(a) -> str:
    ts = [c["evidence_at"] for c in a["criteria"].values() if c["evidence_at"]]
    return min(ts)[:10] if ts else "-"


def render_md(rep: dict) -> str:
    out = [f"# Joy test scorecard: {rep['charter_id']}", "",
           f"Charter version {rep['charter_version']}, rubric `{rep['rubric_version']}`, run `{rep['run_id']}`, "
           f"generated {rep['generated_at'][:16]}.", "",
           "Criteria: " + ", ".join(f"{s} = {c}" for c, s in SHORT.items()) + ". Levels 0-3, `unknown` = no usable "
           "evidence.", "", "## Assets", "",
           "| Asset | Category | " + " | ".join(SHORT.values()) + " | Min | Verdict | Status | Fix items | "
           "Oldest evidence |",
           "|---|---|" + "---|" * len(SHORT) + "---|---|---|---|---|"]
    for a in rep["assets"]:
        lv = [a["criteria"][c]["level"] for c in SHORT]
        known = [x for x in lv if x != "unknown"]
        agg = "unknown" if len(known) < len(lv) else min(known)
        v = a["verdict"]
        verdict = v["proposed"] + (f" ({v['reason']})" if v["reason"] else "")
        st = v["status"] + (f", approved {v['effective']}" if v["effective"] and v["status"] == "proposed" else
                            f" {v['effective']}" if v["status"] == "approved" else "")
        out.append(f"| {a['asset_uri']} | {a['category']} | " + " | ".join(map(str, lv)) +
                   f" | {agg} | {verdict} | {st} | {_fix(v['fix_items']) or '-'} | {_oldest(a)} |")
    out += ["", "## Category maturity", "", f"Aggregation rule: {rep['aggregation_rule']}", ""]
    for c in rep["categories"]:
        n = c["verdicts"]
        out += [f"### {c['category']} ({c['assets']} assets)", "",
                f"Verdicts: keep {n['keep']}, fix {n['fix']}, discard {n['discard']}, unreviewed {n['unreviewed']}.",
                "", "| Aspect | Criteria | Share | State |", "|---|---|---|---|"]
        for m in c["aspects"]:
            crit = ", ".join(f"{k} >= {b}" for k, b in m["criteria"].items())
            out.append(f"| {m['aspect']} | {crit} | {m['share']:.0%} ({m['meeting']}/{m['assets']}) | {m['state']} |")
        out.append("")
    return "\n".join(out)


def render_csv(rep: dict, level: str = "asset") -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    if level == "category":
        w.writerow(["charter_id", "rubric_version", "category", "aspect", "share", "meeting", "assets", "state",
                    "keep", "fix", "discard", "unreviewed"])
        for c in rep["categories"]:
            for m in c["aspects"]:
                w.writerow([rep["charter_id"], rep["rubric_version"], c["category"], m["aspect"], m["share"],
                            m["meeting"], m["assets"], m["state"], *c["verdicts"].values()])
        return buf.getvalue()
    w.writerow(["charter_id", "rubric_version", "run_id", "asset_uri", "category", *SHORT, "proposed", "reason",
                "status", "effective", "fix_items", *[f"{c}_evidence_at" for c in SHORT]])
    for a in rep["assets"]:
        v = a["verdict"]
        w.writerow([rep["charter_id"], a["rubric_version"], a["run_id"], a["asset_uri"], a["category"],
                    *[a["criteria"][c]["level"] for c in SHORT], v["proposed"], v["reason"], v["status"],
                    v["effective"], _fix(v["fix_items"]), *[a["criteria"][c]["evidence_at"] for c in SHORT]])
    return buf.getvalue()


def compare(cat: Catalog, run_a: str, run_b: str, allow_rubric_mismatch: bool = False) -> list[str]:
    """Criterion levels that differ between two runs. Runs on different rubric versions are not comparable
    unless explicitly allowed."""
    runs = {r["run_id"]: r for r in cat.query("SELECT * FROM scorecard_run WHERE run_id IN (?, ?)", (run_a, run_b))}
    for r in (run_a, run_b):
        if r not in runs:
            raise Refused(f"scorecard run '{r}' not found")
    va, vb = runs[run_a]["rubric_version"], runs[run_b]["rubric_version"]
    if va != vb and not allow_rubric_mismatch:
        raise Refused(f"runs use different rubric versions ({va} vs {vb}); scores are not comparable. "
                      "Pass --allow-rubric-mismatch to compare anyway")

    def levels(run):
        return {(r["asset_uri"], r["criterion"]): r["level"] for r in cat.query(
            "SELECT asset_uri, criterion, level FROM criterion_score WHERE run_id = ?", (run,))}

    a, b = levels(run_a), levels(run_b)
    fmt = lambda x: "unknown" if x is None else x  # noqa: E731
    out = [f"{u} {c}: {fmt(a.get((u, c)))} -> {fmt(b.get((u, c)))}" for u, c in sorted(set(a) | set(b))
           if a.get((u, c), "absent") != b.get((u, c), "absent")]
    return ([f"WARNING: rubric versions differ ({va} vs {vb})"] if va != vb else []) + out
