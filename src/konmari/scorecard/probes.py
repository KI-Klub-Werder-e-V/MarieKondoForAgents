"""Evidence probes: each pre-fills one joy criterion from catalog evidence, or answers `unknown`.

Evidence is referenced, never copied (design decision 5): `asset_evidence` payloads hold only flags,
counts, hashes and IDs, and `record_evidence` refuses anything else.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from konmari.canonical import load_yaml
from konmari.catalog import Catalog, audit_change, new_id, now
from konmari.inventory.store import ISO_DURATION
from konmari.versions import Refused

_ID = re.compile(r"^[\w.:/@#+%=~-]{1,300}$")  # identifiers and hashes: no whitespace, no prose
_NUM = (int, float)
# kind -> {payload key: allowed type}; "ref" values must be existing asset URIs
KINDS = {
    "acl_snapshot": {"broad_grants": list, "principal_count": int, "principals_hash": str, "anonymous_links": int},
    "owner_status": {"active": bool},
    "glossary_coverage": {"described": bool, "mapped": int, "total": int, "contract_status": str},
    "stable_id": {"present": bool},
    "retrieval_eval": {"hit_rate": _NUM, "mrr": _NUM, "k": int, "questions": int},
    "dedup_check": {"open_clusters": int, "winner": "ref", "cluster_ids": list},
    "superseded": {"superseded_by": "ref"},
    "relevance": {"relevant": bool, "marked_by": str},
    "freshness_alert": {"status": str},
    "agent_eval": {"pass_rate": _NUM, "faithfulness": _NUM, "citation_precision": _NUM, "citation_recall": _NUM,
                   "citation_check": _NUM, "abstention_rate": _NUM, "status": str, "items": int},
    "soda_check": {"check_type": str, "outcome": str, "check_hash": str},
}
BROAD = {"everyone": "everyone", "everyone except external users": "eeeu", "eeeu": "eeeu",
         "all users": "everyone", "anyone": "anonymous_link", "anonymous": "anonymous_link"}


def _naive(ts) -> datetime | None:
    if ts is None or isinstance(ts, datetime) and ts.tzinfo is None:
        return ts
    ts = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return ts.astimezone(timezone.utc).replace(tzinfo=None) if ts.tzinfo else ts


def short_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def acl_payload(principals: list[str], anonymous_links: int = 0) -> dict:
    """Reduce a raw ACL to the broad-grant tokens and counts; principal names only survive as one hash."""
    broad = sorted({BROAD[p.strip().lower()] for p in principals if p.strip().lower() in BROAD})
    return {"broad_grants": broad, "principal_count": len(principals),
            "principals_hash": short_hash("\n".join(sorted(principals))), "anonymous_links": anonymous_links}


def _check_payload(cat: Catalog, kind: str, payload: dict) -> None:
    spec = KINDS.get(kind)
    if spec is None:
        raise Refused(f"unknown evidence kind '{kind}'; valid: {', '.join(KINDS)}")
    errors = []
    for k, v in payload.items():
        t = spec.get(k)
        if t is None:
            errors.append(f"{kind}: field '{k}' is not allowed (allowed: {', '.join(spec)})")
        elif v is None:
            continue
        elif t == "ref":
            if not cat.one("SELECT 1 AS x FROM source_asset WHERE asset_uri = ?", (v,)):
                errors.append(f"{kind}.{k}: '{v}' is not an inventoried asset")
        elif t is list:
            if not isinstance(v, list) or not all(isinstance(x, str) and _ID.match(x) for x in v):
                errors.append(f"{kind}.{k}: must be a list of identifiers")
        elif t is str:
            if not (isinstance(v, str) and _ID.match(v)):
                errors.append(f"{kind}.{k}: must be an identifier or hash, not free text")
        elif not isinstance(v, t) or (t is int and isinstance(v, bool)):
            errors.append(f"{kind}.{k}: wrong type")
    if errors:
        raise Refused(errors)


def record_evidence(cat: Catalog, uri: str, kind: str, payload: dict, ref: str, actor: str,
                    captured_at=None) -> dict:
    """Store one evidence fact for an asset (references, hashes, counts and flags only)."""
    _check_payload(cat, kind, payload)
    if not _ID.match(ref or ""):
        raise Refused(f"evidence reference '{ref}' must be an identifier (no spaces)")
    row = {"evidence_id": new_id(), "asset_uri": uri, "kind": kind, "payload": json.dumps(payload, sort_keys=True),
           "evidence_ref": ref, "captured_at": _naive(captured_at) or now(), "recorded_at": now(),
           "recorded_by": actor}
    cat.execute("INSERT INTO asset_evidence VALUES (?, ?, ?, ?, ?, ?, ?, ?)", tuple(row.values()))
    return row


# ---------------------------------------------------------------- probe interface

@dataclass
class Result:
    level: int | None                 # 0-3, None = unknown
    evidence_ref: str | None
    evidence_at: datetime | None
    evidence: dict = field(default_factory=dict)
    probe: str = ""
    stale: bool = False


class EvidenceSource(Protocol):
    """Read-only provider that can re-capture one evidence kind (e.g. an ACL snapshot from c07)."""

    kind: str

    def capture(self, asset: dict) -> tuple[dict, str, datetime]: ...   # (payload, evidence_ref, captured_at)


class Context:
    def __init__(self, cat: Catalog, cfg: dict, stakeholders: dict | None = None,
                 sources: list[EvidenceSource] | None = None, at: datetime | None = None):
        self.cat, self.cfg, self.stakeholders = cat, cfg, stakeholders or {}
        self.sources = {s.kind: s for s in sources or []}
        self.now = at or now()

    def latest(self, uri: str, kind: str, where=None) -> dict | None:
        rows = self.cat.query("SELECT * FROM asset_evidence WHERE asset_uri = ? AND kind = ?"
                              " ORDER BY captured_at DESC, recorded_at DESC", (uri, kind))
        for r in rows:
            r["payload"] = json.loads(r["payload"])
            if where is None or where(r["payload"]):
                return r
        return None

    def refresh(self, asset: dict, kinds) -> bool:
        """Re-capture `kinds` from registered sources; True if anything was captured."""
        done = False
        for k in kinds:
            if src := self.sources.get(k):
                payload, ref, at = src.capture(asset)
                record_evidence(self.cat, asset["asset_uri"], k, payload, ref, "konmari-probe", at)
                done = True
        return done


class Probe(Protocol):
    name: str
    criterion: str
    kinds: tuple[str, ...]            # evidence kinds it reads (re-captured when stale)

    def run(self, asset: dict, ctx: Context) -> Result: ...


def _ev_ref(row: dict) -> str:
    return f"evidence:{row['evidence_id']}"


def frequency_days(freq: str) -> float | None:
    if freq == "continuous":
        return 1.0
    m = ISO_DURATION.match(freq or "")
    if not m:
        return None
    y, mo, w, d, _, h, mi, s = (int(g[:-1]) if g else 0 for g in (*m.groups()[:4], None, *m.groups()[5:]))
    days = y * 365 + mo * 30 + w * 7 + d + h / 24 + mi / 1440 + s / 86400  # shortcut: month = 30 days
    return days or None


def _soda(ctx: Context, uri: str, check_type: str) -> str | None:
    row = ctx.latest(uri, "soda_check", lambda p: p.get("check_type") == check_type)
    return row["payload"]["outcome"] if row else None


class AuthoritativeCurrent:
    name, criterion, kinds = "freshness_vs_expected_frequency", "authoritative_current", ("superseded",)

    def run(self, asset, ctx):
        uri = asset["asset_uri"]
        if sup := ctx.latest(uri, "superseded"):
            return Result(0, _ev_ref(sup), sup["captured_at"], {"superseded_by": sup["payload"].get("superseded_by")})
        freq, lm = asset.get("update_frequency"), asset.get("last_modified")
        ev = {"expected_frequency": freq, "last_modified": lm.isoformat() if lm else None}
        ref, at = f"source_asset:{uri}", asset.get("last_seen")
        if freq == "irregular":
            level = 2  # no fixed cadence (e.g. a static policy) cannot be late
        else:
            days = frequency_days(freq)
            if days is None or lm is None:
                return Result(None, ref, at, ev)
            age = (ctx.now - lm).total_seconds() / 86400
            ratio = round(age / days, 3)
            ev |= {"age_days": round(age, 1), "stale_ratio": ratio}
            level = 0 if ratio > ctx.cfg["stale_multiple"] else 1 if ratio > 1 else 2
        alert = ctx.latest(uri, "freshness_alert")
        if alert and alert["payload"]["status"] in ("warn", "error") and (lm is None or alert["captured_at"] > lm):
            ev["freshness_alert"] = alert["payload"]["status"]
            level = min(level, 0 if alert["payload"]["status"] == "error" else 1)
        soda = _soda(ctx, uri, "freshness")
        if soda:
            ev["soda_freshness"] = soda
            level = min(level, 1) if soda == "fail" else (3 if soda == "pass" and level == 2 else level)
        return Result(level, ref, at, ev)


class OwnedMaintained:
    name, criterion, kinds = "owner_present_and_active", "owned_maintained", ("owner_status",)

    def run(self, asset, ctx):
        uri = asset["asset_uri"]
        owner = (asset.get("owner") or "").strip()
        ref, at = f"source_asset:{uri}", asset.get("last_seen")
        if owner in ("", "unknown"):
            return Result(0, ref, at, {"owner_present": False, "owner_active": False})
        st = ctx.latest(uri, "owner_status")
        active = st["payload"]["active"] if st else owner in ctx.stakeholders
        if st:
            ref, at = _ev_ref(st), st["captured_at"]
        steward = bool((asset.get("steward") or "").strip())
        level = (3 if steward else 2) if active else 1
        return Result(level, ref, at, {"owner_present": True, "owner_active": active, "steward_present": steward})


class MeaningClear:
    name, criterion, kinds = "description_and_glossary_coverage", "meaning_clear", ("glossary_coverage",)

    def run(self, asset, ctx):
        g = ctx.latest(asset["asset_uri"], "glossary_coverage")
        if g is None:
            return Result(None, None, None, {})
        p = g["payload"]
        share = round(p.get("mapped", 0) / p["total"], 3) if p.get("total") else 0.0
        ev = {"described": p.get("described", False), "mapped": p.get("mapped", 0), "total": p.get("total", 0),
              "coverage": share}
        level = 0 if not ev["described"] else 3 if share >= 1 else 2 if share >= 0.5 else 1
        return Result(level, _ev_ref(g), g["captured_at"], ev)


class RetrievablePrecise:
    name, criterion, kinds = "retrieval_metrics_or_stable_id", "retrievable_precise", ("retrieval_eval", "stable_id")

    def run(self, asset, ctx):
        uri = asset["asset_uri"]
        if r := ctx.latest(uri, "retrieval_eval"):  # 09 agent-evaluation, preferred over the proxy
            hit, mrr = r["payload"]["hit_rate"], r["payload"].get("mrr", 0)
            level = 3 if hit >= 0.8 and mrr >= 0.5 else 2 if hit >= 0.5 else 1 if hit > 0 else 0
            return Result(level, _ev_ref(r), r["captured_at"], {"source": "retrieval_eval", **r["payload"]})
        if s := ctx.latest(uri, "stable_id"):
            return Result(2 if s["payload"]["present"] else 0, _ev_ref(s), s["captured_at"],
                          {"source": "stable_id_proxy", "present": s["payload"]["present"]})
        return Result(None, None, None, {})


class ConflictsResolved:
    name, criterion, kinds = "open_duplicate_clusters", "conflicts_resolved", ("dedup_check",)

    def run(self, asset, ctx):
        uri = asset["asset_uri"]
        d = ctx.latest(uri, "dedup_check")
        if d is None:
            return Result(None, None, None, {})
        p = d["payload"]
        ev = {"open_clusters": p.get("open_clusters", 0), "winner": p.get("winner"),
              "cluster_ids": p.get("cluster_ids", [])}
        level = (1 if p.get("winner") else 0) if ev["open_clusters"] else 2
        soda = _soda(ctx, uri, "duplicate_count")
        if soda:
            ev["soda_duplicates"] = soda
            level = min(level, 1) if soda == "fail" else (3 if soda == "pass" and level == 2 else level)
        return Result(level, _ev_ref(d), d["captured_at"], ev)


class PermissionsExplicit:
    name, criterion, kinds = "broad_acl_grants", "permissions_explicit", ("acl_snapshot",)

    def run(self, asset, ctx):
        a = ctx.latest(asset["asset_uri"], "acl_snapshot")
        if a is None:
            return Result(None, None, None, {})
        p = a["payload"]
        ev = {"broad_grants": p.get("broad_grants", []), "anonymous_links": p.get("anonymous_links", 0),
              "principal_count": p.get("principal_count"), "acl_snapshot_ref": a["evidence_ref"],
              "captured_at": a["captured_at"].isoformat()}
        if ev["broad_grants"] or ev["anonymous_links"]:
            level = 0
        elif asset.get("sensitivity_tier") in ("personal", "special-category") and not asset.get("ropa_ref"):
            level = 1
        else:
            level = 3 if asset.get("tier_state") == "confirmed" else 2
        return Result(level, a["evidence_ref"], a["captured_at"], ev)


PROBES: list[Probe] = [AuthoritativeCurrent(), OwnedMaintained(), MeaningClear(), RetrievablePrecise(),
                       ConflictsResolved(), PermissionsExplicit()]


def run_probe(probe: Probe, asset: dict, ctx: Context) -> tuple[Result, bool]:
    """Run a probe; expired evidence is re-captured and the probe re-run once. Returns (result, stale)."""
    max_age = timedelta(days=ctx.cfg["evidence_max_age_days"])

    def expired(r):
        return r.evidence_at is not None and ctx.now - r.evidence_at > max_age

    res = probe.run(asset, ctx)
    if expired(res):
        if ctx.refresh(asset, probe.kinds):
            res = probe.run(asset, ctx)
        if expired(res):
            return Result(None, res.evidence_ref, res.evidence_at, res.evidence | {"stale_evidence": True}), True
    return res, False


# ---------------------------------------------------------------- optional evidence adapters

def _match_asset(cat: Catalog, charter_id: str, table: str | None) -> str | None:
    if not table:
        return None
    for r in cat.query("SELECT asset_uri, title FROM source_asset WHERE charter_id = ? ORDER BY asset_uri",
                       (charter_id,)):
        if r["title"] == table or r["title"].endswith("." + table) or r["asset_uri"] == table:
            return r["asset_uri"]
    return None


def ingest_soda(cat: Catalog, charter_id: str, path: str | Path, actor: str) -> tuple[list[dict], list[str]]:
    """Soda Core scan results (`soda scan -srf results.json`): freshness and duplicate checks become evidence."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    at = doc.get("scanEndTimestamp") or doc.get("dataTimestamp")
    rows, skipped = [], []
    for chk in doc.get("checks", []):
        text = f"{chk.get('name', '')} {chk.get('definition', '')}".lower()
        ctype = "freshness" if "freshness" in text else "duplicate_count" if "duplicate" in text else "other"
        uri = _match_asset(cat, charter_id, chk.get("table"))
        if uri is None or chk.get("outcome") not in ("pass", "fail", "warn"):
            skipped.append(f"check on table '{chk.get('table')}': no matching inventoried asset or no outcome")
            continue
        h = short_hash(str(chk.get("identity") or chk.get("definition") or chk.get("name")))
        rows.append(record_evidence(cat, uri, "soda_check", {"check_type": ctype, "outcome": chk["outcome"],
                                                             "check_hash": h}, f"soda:{h}", actor, at))
    audit_change(cat, "scorecard.evidence.ingest", str(path), None, {"rows": len(rows), "skipped": len(skipped)},
                 actor=actor, charter_id=charter_id, step="score", adapter="soda")
    return rows, skipped


def ingest_odcs(cat: Catalog, charter_id: str, path: str | Path, actor: str) -> tuple[list[dict], list[str]]:
    """ODCS v3 data contract: per schema object, description and glossary coverage of its properties."""
    doc = load_yaml(Path(path))
    if not isinstance(doc, dict) or doc.get("kind") != "DataContract":
        raise Refused(f"{path}: not an ODCS data contract (kind: DataContract)")
    ref = f"odcs:{doc.get('id')}@{doc.get('version')}"
    status = str(doc.get("status") or "unknown")
    rows, skipped = [], []
    for obj in doc.get("schema") or []:
        uri = _match_asset(cat, charter_id, obj.get("physicalName") or obj.get("name"))
        if uri is None:
            skipped.append(f"schema object '{obj.get('name')}': no matching inventoried asset")
            continue
        props = obj.get("properties") or []
        mapped = sum(1 for p in props if p.get("businessName") or p.get("authoritativeDefinitions"))
        described = bool(obj.get("description") or doc.get("description"))
        rows.append(record_evidence(cat, uri, "glossary_coverage", {
            "described": described, "mapped": mapped, "total": len(props),
            "contract_status": status if _ID.match(status) else "unknown"}, ref, actor))
    audit_change(cat, "scorecard.evidence.ingest", str(path), None, {"rows": len(rows), "skipped": len(skipped)},
                 actor=actor, charter_id=charter_id, step="score", adapter="odcs")
    return rows, skipped
