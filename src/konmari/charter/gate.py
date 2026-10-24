"""Readiness gate that every later pipeline step calls before reading source data (contract: docs/gate-contract.md)."""

from dataclasses import dataclass, field
from pathlib import Path

from konmari import versions as v
from konmari.canonical import canonical_hash, load_yaml
from konmari.catalog import Catalog, audit, connect
from konmari.charter.validation import STEPS

PLANNED_BASELINE_STEPS = {"inventory"}  # inventory only reads metadata (design decision 9)


@dataclass
class GateResult:
    passed: bool
    step: str
    charter_id: str | None
    charter_version: int | None
    metric_set_version: int | None = None
    unmet: list[str] = field(default_factory=list)


def gate(step: str, charter_path="charter.yaml", catalog: Catalog | None = None, actor: str | None = None) -> GateResult:
    if step not in STEPS:
        raise ValueError(f"unknown step '{step}'; valid steps: {', '.join(STEPS)}")
    cat = catalog or connect()
    unmet, cid, ver, msv = [], None, None, None
    p = Path(charter_path)
    doc = load_yaml(p) if p.is_file() else None
    cid = doc.get("charter_id") if isinstance(doc, dict) else None
    row = v.latest(cat, "charter_version", cid) if cid else None
    if row is None:
        unmet.append(f"charter: no registered charter for '{charter_path}'")
    else:
        ver = row["version"]
        if row["status"] != "approved":
            unmet.append(f"charter: latest version {ver} is '{row['status']}', not approved")
        elif canonical_hash(doc) != row["content_hash"]:
            unmet.append(f"charter: '{charter_path}' does not match approved version {ver} (content hash)")
        ms = v.latest(cat, "metric_set_version", cid)
        if ms is None:
            unmet.append("metrics: no metric set registered")
        else:
            msv = ms["version"]
            if ms["status"] != "approved":
                unmet.append(f"metrics: metric set version {msv} is '{ms['status']}', not approved")
            for m in ms["doc"]["metrics"]:
                if m["tier"] != "must_have":
                    continue
                base = cat.query("SELECT value, plan_date FROM metric_measurement WHERE charter_id = ? AND metric_id = ?"
                                 " AND kind = 'baseline' ORDER BY recorded_at DESC", (cid, m["id"]))
                if any(b["value"] is not None for b in base):
                    continue
                if base and base[0]["plan_date"] and step in PLANNED_BASELINE_STEPS:
                    continue
                unmet.append(f"metrics: must_have metric '{m['id']}' has no baseline"
                             + (" value (planned baselines only allow the inventory step)" if base else
                                " or measurement plan"))
    res = GateResult(not unmet, step, cid, ver, msv, unmet)
    audit(cat, "charter.gate", actor=actor, charter_id=cid, charter_version=ver, step=step,
          result="passed" if res.passed else "failed", details={"unmet": unmet, "metric_set_version": msv})
    return res
