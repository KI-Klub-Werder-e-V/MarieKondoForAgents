"""Metric-set lifecycle and measurements in the catalog, plus the status report."""

import json
from datetime import date
from pathlib import Path

from konmari import versions as v
from konmari.canonical import canonical_hash, load_yaml
from konmari.catalog import Catalog, audit, new_id, now, os_user
from konmari.charter.validation import STEPS, roles_by_id
from konmari.metrics.validation import validate_golden_set, validate_metrics
from konmari.versions import Refused

T = "metric_set_version"


def golden_path(metrics: dict, metrics_path) -> Path:
    return Path(metrics_path).parent / metrics["golden_set"]["file"]


def golden_hash(metrics: dict, metrics_path) -> str | None:
    p = golden_path(metrics, metrics_path)
    return canonical_hash(load_yaml(p)) if p.is_file() else None


def check(metrics, charter: dict, metrics_path) -> list[str]:
    errs = validate_metrics(metrics, charter)
    gs = (metrics or {}).get("golden_set") if isinstance(metrics, dict) else None
    if isinstance(gs, dict) and gs.get("file"):
        p = golden_path(metrics, metrics_path)
        if not p.is_file():
            errs.append(f"golden_set.file: '{p}' not found")
        else:
            errs += validate_golden_set(load_yaml(p), charter, gs.get("min_questions", 30),
                                        gs.get("min_abstention", 5))
    return errs


def _require_valid(metrics, charter, metrics_path):
    errs = check(metrics, charter, metrics_path)
    if errs:
        raise Refused(errs)


def _write_definitions(cat: Catalog, metrics: dict, version: int) -> None:
    cid = metrics["charter_id"]
    cat.execute("DELETE FROM metric_definition WHERE charter_id = ? AND metric_set_version = ?", (cid, version))
    for m in metrics["metrics"]:
        cat.execute("INSERT INTO metric_definition VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (cid, version, m["id"], m["name"], m["family"], m["unit"], m["direction"], float(m["target"]),
                     m["tier"], m["owner"], m["cadence"]))


def register(cat: Catalog, metrics: dict, charter: dict, metrics_path, actor: str) -> dict:
    _require_valid(metrics, charter, metrics_path)
    row, changed = v.sync_draft(cat, T, metrics["charter_id"], metrics, actor, "metrics")
    if changed:
        _write_definitions(cat, metrics, row["version"])
        audit(cat, "metrics.register", actor=actor, charter_id=metrics["charter_id"], result="draft",
              details={"metric_set_version": row["version"]})
    return row


def approve(cat: Catalog, metrics: dict, charter: dict, metrics_path, approver: str) -> dict:
    roles = roles_by_id(charter)
    if approver not in roles:
        raise Refused(f"'{approver}' is not in the stakeholder register")
    row = register(cat, metrics, charter, metrics_path, approver)
    cid, ver = metrics["charter_id"], row["version"]
    if row["status"] == "approved":
        raise Refused(f"metric set version {ver} is already approved")
    acc = charter["raci"]["metric_approval"]["A"][0]
    if approver != acc:
        audit(cat, "metrics.approve", actor=approver, charter_id=cid, result="refused",
              details={"metric_set_version": ver, "accountable": acc})
        raise Refused(f"'{approver}' is not Accountable for metric approval (Accountable: '{acc}')")
    cat.execute("INSERT INTO approval VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), "metrics", cid, ver, approver, ",".join(sorted(roles[approver])), os_user(), now(),
                 row["content_hash"], None))
    v.set_status(cat, T, cid, ver, "approved")
    audit(cat, "metrics.approve", actor=approver, charter_id=cid, result="approved",
          details={"metric_set_version": ver, "content_hash": row["content_hash"]})
    return v.latest(cat, T, cid)


def _lowered(old: dict, new: dict) -> list[str]:
    prev = {m["id"]: m for m in old["metrics"]}
    out = []
    for m in new["metrics"]:
        p = prev.get(m["id"])
        if p and p["direction"] == m["direction"] and (
                m["target"] < p["target"] if m["direction"] == ">=" else m["target"] > p["target"]):
            out.append(m["id"])
    return out


def amend(cat: Catalog, metrics: dict, charter: dict, metrics_path, reason: str, actor: str) -> tuple[dict, list]:
    if not reason or not reason.strip():
        raise Refused("an amendment needs a reason (--reason)")
    cid = metrics["charter_id"]
    row = v.latest(cat, T, cid)
    if row is None:
        raise Refused("no metric set in the catalog yet; run 'konmari metrics validate' first")
    _require_valid(metrics, charter, metrics_path)
    if canonical_hash(metrics) == row["content_hash"]:
        raise Refused(f"no changes compared with metric set version {row['version']}")
    measured = {r["metric_id"] for r in cat.query(
        "SELECT DISTINCT metric_id FROM metric_measurement WHERE charter_id = ? AND value IS NOT NULL", (cid,))}
    lowered = [m for m in _lowered(row["doc"], metrics) if m in measured]
    new = row["version"] + 1
    v.insert(cat, T, cid, new, metrics, actor, reason)
    _write_definitions(cat, metrics, new)
    if lowered:
        cat.execute("UPDATE metric_set_version SET flags = ? WHERE charter_id = ? AND version = ?",
                    ("target_lowered_after_measurement:" + ",".join(lowered), cid, new))
        audit(cat, "target_lowered_after_measurement", actor=actor, charter_id=cid, result="flagged",
              details={"metric_set_version": new, "metrics": lowered, "reason": reason})
    audit(cat, "metrics.amend", actor=actor, charter_id=cid, result="draft",
          details={"metric_set_version": new, "previous_version": row["version"], "reason": reason})
    return v.latest(cat, T, cid), lowered


def record(cat: Catalog, cid: str, metric_id: str, kind: str, author: str, value: float | None = None,
           method: str | None = None, unit: str | None = None, measured_on: str | None = None,
           plan_date: str | None = None, step: str | None = None, golden_set_hash: str | None = None) -> dict:
    ms = v.latest(cat, T, cid)
    ch = v.latest(cat, "charter_version", cid)
    if ms is None or ch is None:
        raise Refused("charter and metric set must be registered first ('konmari charter approve', "
                      "'konmari metrics validate')")
    m = next((m for m in ms["doc"]["metrics"] if m["id"] == metric_id), None)
    if m is None:
        raise Refused(f"metric '{metric_id}' is not in metric set version {ms['version']}")
    if kind not in ("baseline", "gate", "final"):
        raise Refused(f"unknown measurement kind '{kind}'")
    if kind != "baseline" and step not in STEPS:
        raise Refused(f"--step must be one of: {', '.join(STEPS)}")
    if value is None:
        if kind != "baseline":
            raise Refused("gate and final measurements need a --value")
        close = ch["doc"]["cadence"]["pilot_close"]
        if not plan_date or date.fromisoformat(plan_date) > date.fromisoformat(close):
            raise Refused(f"a not_yet_measured baseline needs a --plan-date no later than the pilot close ({close})")
    elif not method:
        raise Refused("a measurement needs a --method")
    if author not in roles_by_id(ch["doc"]):
        raise Refused(f"'{author}' is not in the stakeholder register")
    measured_on = measured_on or (date.today().isoformat() if value is not None else None)
    row = {"id": new_id(), "charter_id": cid, "metric_id": metric_id, "kind": kind, "value": value,
           "unit": unit or m["unit"], "measured_on": measured_on, "plan_date": plan_date, "method": method,
           "author": author, "step": step, "charter_version": ch["version"], "metric_set_version": ms["version"],
           "golden_set_hash": golden_set_hash, "recorded_at": now()}
    cat.execute(f"INSERT INTO metric_measurement ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                tuple(row.values()))
    audit(cat, f"metrics.{kind}", actor=author, charter_id=cid, charter_version=ch["version"], step=step,
          result="not_yet_measured" if value is None else "recorded",
          details={"metric_id": metric_id, "value": value, "metric_set_version": ms["version"]})
    return row


def _met(m: dict, value: float) -> bool:
    return value >= m["target"] if m["direction"] == ">=" else value <= m["target"]


def report(cat: Catalog, cid: str) -> dict:
    ms = v.latest(cat, T, cid)
    if ms is None:
        raise Refused("no metric set in the catalog yet")
    rows = cat.query("SELECT * FROM metric_measurement WHERE charter_id = ? ORDER BY recorded_at", (cid,))
    closed = any(r["kind"] == "final" for r in rows)
    out, failing = [], []
    for m in ms["doc"]["metrics"]:
        mine = [r for r in rows if r["metric_id"] == m["id"]]
        base = next((r for r in reversed(mine) if r["kind"] == "baseline"), None)
        latest = next((r for r in reversed(mine) if r["kind"] in ("gate", "final")), None)
        final = next((r for r in reversed(mine) if r["kind"] == "final"), None)
        status = "not_measured" if latest is None else ("met" if _met(m, latest["value"]) else "not_met")
        final_status = "not_measured" if final is None else ("met" if _met(m, final["value"]) else "not_met")
        item = {"id": m["id"], "name": m["name"], "family": m["family"], "tier": m["tier"], "unit": m["unit"],
                "direction": m["direction"], "target": m["target"],
                "baseline": None if base is None else (base["value"] if base["value"] is not None
                                                       else f"not_yet_measured (plan {base['plan_date']})"),
                "latest": None if latest is None else latest["value"],
                "final": None if final is None else final["value"], "status": status}
        out.append(item)
        if closed and m["tier"] == "must_have" and final_status != "met":
            failing.append(m["id"])
    verdict = "in_progress" if not closed else ("not_successful" if failing else "successful")
    return {"charter_id": cid, "metric_set_version": ms["version"], "metric_set_status": ms["status"],
            "flags": ms["flags"], "verdict": verdict, "failing_must_have": failing, "metrics": out}


def render_md(rep: dict) -> str:
    lines = [f"# Success metrics: {rep['charter_id']}", "",
             f"Metric set version {rep['metric_set_version']} ({rep['metric_set_status']})"
             + (f", flags: {rep['flags']}" if rep["flags"] else ""), "",
             f"**Pilot verdict: {rep['verdict']}**"
             + (f" (must-have not met at close: {', '.join(rep['failing_must_have'])})" if rep["failing_must_have"]
                else ""), "",
             "| Metric | Tier | Baseline | Latest | Target | Status |", "|---|---|---|---|---|---|"]
    for m in rep["metrics"]:
        fmt = lambda x: "–" if x is None else str(x)  # noqa: E731
        lines.append(f"| {m['id']} | {m['tier']} | {fmt(m['baseline'])} | {fmt(m['latest'])} | "
                     f"{m['direction']} {m['target']} {m['unit']} | {m['status']} |")
    return "\n".join(lines) + "\n"


def to_json(rep: dict) -> str:
    return json.dumps(rep, indent=2, ensure_ascii=False, default=str)
