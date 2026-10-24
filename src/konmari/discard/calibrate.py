"""Threshold calibration: stratified pair sampling, expert labels, precision/recall per threshold, selection or
named override. Detectors without either may not create proposals (design D2)."""

import csv
import difflib
import html
import json
from pathlib import Path

from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.discard import corpus
from konmari.discard.detect import DETECTORS
from konmari.discard.events import log
from konmari.inventory.store import approvers
from konmari.versions import Refused

LABELS = ("duplicate", "related-but-distinct", "unrelated")
MIN_LABELS = 50  # our assumption (design D2)
REPORT_THRESHOLDS = (0.7, 0.8, 0.9)


def _detector(d: str) -> str:
    if d not in DETECTORS:
        raise Refused(f"unknown detector '{d}'; calibrate one of: {', '.join(DETECTORS)}")
    return d


def latest_run(cat: Catalog, charter_id: str) -> dict | None:
    return cat.one("SELECT * FROM discard_run WHERE charter_id = ? AND finished_at IS NOT NULL"
                   " ORDER BY started_at DESC LIMIT 1", (charter_id,))


def sample(cat: Catalog, charter_id: str, detector: str, n: int = 60, bins: int = 5) -> list[dict]:
    """Candidate pairs of the latest run, spread evenly over the score range (round-robin over score bins)."""
    run = latest_run(cat, charter_id)
    if run is None:
        raise Refused("no detection run yet; run 'konmari discard detect' first")
    rows = cat.query("SELECT * FROM candidate_pair WHERE run_id = ? AND detector = ? ORDER BY score, item_a, item_b",
                     (run["run_id"], _detector(detector)))
    if not rows:
        return []
    lo, hi = rows[0]["score"], rows[-1]["score"]
    width = (hi - lo) / bins or 1.0
    buckets = [[] for _ in range(bins)]
    for r in rows:
        buckets[min(int((r["score"] - lo) / width), bins - 1)].append(r)
    out = []
    while len(out) < n and any(buckets):
        for b in buckets:
            if b and len(out) < n:
                out.append(b.pop(len(b) // 2))  # the middle of each bin first
    return sorted(out, key=lambda r: r["score"])


def write_sample(cat: Catalog, pairs: list[dict], csv_path: str | Path, html_path: str | Path) -> None:
    """A CSV to fill in (label column) and a side-by-side diff page for the expert."""
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["detector", "item_a", "item_b", "score", "label"])
        w.writeheader()
        for p in pairs:
            w.writerow({"detector": p["detector"], "item_a": p["item_a"], "item_b": p["item_b"], "score": p["score"],
                        "label": ""})
    d = difflib.HtmlDiff(wrapcolumn=70)
    parts = ["<!doctype html><meta charset='utf-8'><title>Calibration pairs</title>"]
    for p in pairs:
        a, b = corpus.require(cat, p["item_a"]), corpus.require(cat, p["item_b"])
        parts.append(f"<h2>{html.escape(p['item_a'])} vs {html.escape(p['item_b'])} (score {p['score']})</h2>")
        parts.append(d.make_table(corpus.read_text(a).splitlines(), corpus.read_text(b).splitlines(),
                                  html.escape(a["title"]), html.escape(b["title"])))
    Path(html_path).write_text("\n".join(parts), encoding="utf-8")


def record_labels(cat: Catalog, sc: Scope, csv_path: str | Path, actor: str) -> int:
    if actor not in sc.stakeholders:
        raise Refused(f"'{actor}' is not in the stakeholder register")
    n = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("label") or "").strip()]
    for i, r in enumerate(rows, 2):
        if r["label"].strip() not in LABELS:
            raise Refused(f"{csv_path} row {i}: label must be one of {', '.join(LABELS)}")
    for r in rows:
        cat.execute("INSERT INTO calibration_label VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), _detector(r["detector"]), r["item_a"], r["item_b"], float(r["score"]),
                     r["label"].strip(), actor, now()))
        n += 1
    log(cat, sc, "calibrate.label", None, None, None, actor, labels=n)
    return n


def metrics(cat: Catalog, detector: str, thresholds) -> tuple[int, dict]:
    rows = cat.query("SELECT score, label FROM calibration_label WHERE detector = ?", (detector,))
    dup = sum(r["label"] == "duplicate" for r in rows)
    out = {}
    for t in sorted(set(thresholds)):
        pred = [r for r in rows if r["score"] >= t]
        tp = sum(r["label"] == "duplicate" for r in pred)
        out[f"{t:.2f}"] = {"precision": round(tp / len(pred), 3) if pred else None,
                           "recall": round(tp / dup, 3) if dup else None, "predicted": len(pred)}
    return len(rows), out


def select(cat: Catalog, sc: Scope, detector: str, threshold: float, actor: str) -> dict:
    n, m = metrics(cat, _detector(detector), (*REPORT_THRESHOLDS, threshold))
    if n < MIN_LABELS:
        raise Refused(f"detector '{detector}' has {n} labeled pairs; at least {MIN_LABELS} are needed "
                      "(or record an override with --override-reason)")
    return _store(cat, sc, detector, threshold, n, m, None, actor)


def override(cat: Catalog, sc: Scope, detector: str, threshold: float | None, reason: str, actor: str) -> dict:
    if not (reason or "").strip():
        raise Refused("an override needs a reason")
    if actor not in approvers(sc):
        raise Refused(f"'{actor}' is not an approver named in the charter; only an approver may override calibration")
    n, m = metrics(cat, _detector(detector), REPORT_THRESHOLDS)
    return _store(cat, sc, detector, threshold, n, m, reason, actor)


def _store(cat, sc, detector, threshold, n, m, reason, actor) -> dict:
    row = {"calibration_id": new_id(), "charter_id": sc.charter_id, "detector": detector, "threshold": threshold,
           "labels": n, "metrics": json.dumps(m), "override_reason": reason, "decided_by": actor, "decided_at": now()}
    cat.execute("INSERT INTO calibration VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row.values()))
    log(cat, sc, "calibrate.override" if reason else "calibrate.select", None, None, None, actor, detector=detector,
        threshold=threshold, labels=n, reason=reason)
    return row | {"metrics": m}


def status(cat: Catalog, charter_id: str, detector: str) -> dict | None:
    return cat.one("SELECT * FROM calibration WHERE charter_id = ? AND detector = ? ORDER BY decided_at DESC LIMIT 1",
                   (charter_id, detector))


def describe(cat: Catalog, charter_id: str) -> dict[str, str]:
    out = {}
    for d in DETECTORS:
        s = status(cat, charter_id, d)
        n = cat.one("SELECT COUNT(*) AS n FROM calibration_label WHERE detector = ?", (d,))["n"]
        out[d] = (f"override by {s['decided_by']}: {s['override_reason']}" if s and s["override_reason"] else
                  f"calibrated: threshold {s['threshold']} by {s['decided_by']} ({s['labels']} labels)" if s else
                  f"uncalibrated ({n} labels, {MIN_LABELS} needed)")
    return out
