"""Governance settings: `governance.yaml` in the engagement directory overrides these defaults (all our assumptions,
see research/c07-governance.md; calibrate after the first pilot)."""

import copy
from pathlib import Path

from konmari.canonical import load_yaml

DEFAULTS = {
    "audit": {"retention_months": 6, "max_retention_months": 24, "ai_act_high_risk": None},
    "pii": {"low": 0.4, "high": 0.85, "recall_threshold": 0.95, "min_sample": 200},
    "token": {"audience": "konmari"},
    "openfga": {"enabled": False, "url": None, "store_id": None, "model_id": None},
    "grants": {"max_days": 90},
}


def load(path: str | Path = "governance.yaml") -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    p = Path(path)
    for section, values in ((load_yaml(p) or {}) if p.is_file() else {}).items():
        cfg.setdefault(section, {}).update(values or {})
    return cfg


def high_risk(cfg: dict, charter: dict | None) -> bool:
    """Explicit flag first, else the charter's AI Act classification (c01)."""
    flag = cfg["audit"].get("ai_act_high_risk")
    if flag is not None:
        return bool(flag)
    return ((charter or {}).get("compliance", {}).get("ai_act", {}) or {}).get("classification") == "high"


def validate(cfg: dict, charter: dict | None = None) -> list[str]:
    a, errs = cfg["audit"], []
    months, ceiling = a["retention_months"], a["max_retention_months"]
    if high_risk(cfg, charter) and months < 6:
        errs.append(f"audit.retention_months = {months} is below 6 months; EU AI Act Art. 26(6) requires deployers "
                    "of high-risk systems to keep logs for at least six months")
    if months > ceiling:
        errs.append(f"audit.retention_months = {months} exceeds the maximum of {ceiling} months documented in the "
                    "DSGVO Art. 30 record (audit.max_retention_months)")
    if months < 1:
        errs.append("audit.retention_months must be at least 1")
    p = cfg["pii"]
    if not 0 <= p["low"] <= p["high"] <= 1:
        errs.append("pii thresholds need 0 <= low <= high <= 1")
    return errs
