"""Permission leak test suite and the computed governance gate (design D10). Read API for change 08:
`exposable(cat, sc)` and the `governance_status` view (latest `konmari govern check`)."""

import json
import re
from datetime import timedelta

from konmari.canonical import load_yaml
from konmari.catalog import Catalog, new_id, now, verify_chain
from konmari.charter.store import Scope
from konmari.governance import access
from konmari.governance import acl as gacl
from konmari.governance import pii
from konmari.governance import policy as pol
from konmari.governance.audit import heartbeat, write_event
from konmari.inventory.sensitivity import rank

LEAK_AGENT = "konmari-leak-suite"
MAX_PROBES = 50  # shortcut: adversarial probes per principal are capped; raise for large pilots
HEARTBEAT_MAX_AGE = timedelta(days=1)


def load_suite(path) -> dict:
    doc = load_yaml(path) if not isinstance(path, dict) else path
    if not (doc or {}).get("principals"):
        raise ValueError(f"{path}: a leak suite needs at least one test principal")
    return doc


def run_leak_suite(cat: Catalog, sc: Scope, suite: dict, actor: str, retrieve=access.retrieve,
                   prefilter=None) -> dict:
    """For each test principal: probe with queries built from every chunk it must not read (plus the suite's own
    queries) through the real retrieval path; any returned chunk it may not read is a leak."""
    if access.agent_client(cat, LEAK_AGENT) is None:
        access.register_agent(cat, sc, LEAK_AGENT, [f"read:{c}" for c in sc.categories], actor)
    rows = {r["chunk_id"]: r for r in cat.query("SELECT chunk_id, item_id, category_id, content, allowed_principals,"
                                                " denied_principals FROM agent_index")}
    failures, n = [], 0
    for p in suite["principals"]:
        ctx = access.context(cat, p.get("agent") or LEAK_AGENT, p.get("user"), p.get("groups") or [])
        restricted = [r for r in rows.values() if not access.permitted(r, ctx)]
        probes = list(suite.get("queries") or []) + [" ".join(re.findall(r"\w+", r["content"])[:12])
                                                     for r in restricted[:MAX_PROBES]]
        with access.invoke(ctx):
            for q in probes:
                n += 1
                for hit in retrieve(cat, ctx, q, k=10, tool="leak-suite", prefilter=prefilter):
                    if not access.permitted(rows.get(hit["chunk_id"], hit), ctx):
                        failures.append({"principal": p["id"], "chunk_id": hit["chunk_id"],
                                         "category_id": hit["category_id"]})
    failures = [dict(t) for t in {tuple(sorted(f.items())) for f in failures}]
    versions = pol.active_versions(cat)
    run = {"run_id": new_id(), "started_at": now(), "actor": actor, "passed": not failures, "queries": n,
           "failures": json.dumps(sorted(failures, key=lambda f: (f["principal"], f["chunk_id"]))),
           "policy_versions": json.dumps(versions, sort_keys=True)}
    cat.execute("INSERT INTO leak_run VALUES (?, ?, ?, ?, ?, ?, ?)", tuple(run.values()))
    write_event(cat, "govern.leak_suite", actor=actor, result="passed" if not failures else "failed",
                charter_id=sc.charter_id, run=run["run_id"], queries=n, failures=failures, policy_versions=versions)
    return {**run, "failures": json.loads(run["failures"])}


def _audit_active(cat: Catalog, at) -> bool:
    last = cat.one("SELECT ts FROM audit_event WHERE action = 'audit.heartbeat' ORDER BY ts DESC LIMIT 1")
    return bool(last) and at - last["ts"] <= HEARTBEAT_MAX_AGE and verify_chain(cat) is None


def status(cat: Catalog, sc: Scope, at=None) -> list[dict]:
    """Computed per source (category): approved only with 100 % ACL coverage, an approved `allowed` policy, a passed
    PII recall gate where personal data is present, a passed leak suite for the current policy versions and active
    audit logging. `acl_stale` when the ACL sync is overdue."""
    at = at or now()
    audit_ok = _audit_active(cat, at)
    leak = cat.one("SELECT * FROM leak_run ORDER BY started_at DESC LIMIT 1")
    dv = pii.detector_version()
    out = []
    for cid in sc.categories:
        p, reasons = pol.active(cat, cid), []
        if p is None:
            reasons.append("policy_missing")
        elif p["doc"]["exposure"] != "allowed":
            reasons.append("policy_blocked")
        chunks = pii.pilot_chunks(cat, sc, cid)
        covered = [c for c in chunks if c["acl_status"] in ("known", "policy") and c["allowed_principals"]]
        coverage = len(covered) / len(chunks) if chunks else 0.0
        if not chunks:
            reasons.append("no_chunks")
        elif coverage < 1:
            reasons.append("acl_coverage")
        scans = {r["chunk_id"]: r for r in cat.query("SELECT chunk_id, content_hash, detector_version FROM pii_scan")}
        if any((s := scans.get(c["chunk_id"])) is None or s["content_hash"] != c["content_hash"]
               or s["detector_version"] != dv for c in chunks):
            reasons.append("pii_unscanned")
        personal = p and rank(p["doc"]["sensitivity"]) >= rank("personal") or any(
            pii.findings(cat, c["chunk_id"]) for c in chunks)
        if personal and pii.gate_state(cat, cid) != "passed":
            reasons.append("pii_recall")
        if not (leak and leak["passed"] and p and json.loads(leak["policy_versions"]).get(cid) == p["version"]):
            reasons.append("leak_suite")
        if not audit_ok:
            reasons.append("audit_inactive")
        stale = gacl.stale(cat, cid, at)
        out.append({"category_id": cid, "status": "acl_stale" if stale else "blocked" if reasons else "approved",
                    "reasons": (["acl_stale"] if stale else []) + reasons, "policy_version": p and p["version"],
                    "acl_coverage": round(coverage, 4)})
    return out


def check(cat: Catalog, sc: Scope, actor: str, suite: dict | None = None, prefilter=None) -> dict:
    """`konmari govern check`: audit heartbeat, leak suite (when given), then the recorded gate status."""
    heartbeat(cat, actor)
    leak = run_leak_suite(cat, sc, suite, actor, prefilter=prefilter) if suite else None
    prev = {r["category_id"]: r["status"] for r in cat.query("SELECT * FROM governance_status")}
    check_id, t, rows = new_id(), now(), status(cat, sc)
    for r in rows:
        cat.execute("INSERT INTO governance_check VALUES (?, ?, ?, ?, ?, ?)",
                    (check_id, r["category_id"], r["status"], json.dumps(r["reasons"]), r["policy_version"], t))
        if prev.get(r["category_id"]) != r["status"]:
            write_event(cat, "govern.gate", actor=actor, charter_id=sc.charter_id, policy=r["category_id"],
                        previous=prev.get(r["category_id"]), new=r["status"], reasons=r["reasons"],
                        policy_version=r["policy_version"])
    return {"check_id": check_id, "sources": rows, "leak_suite": leak}


def exposable(cat: Catalog, sc: Scope) -> list[dict]:
    """The contract for change 08: sources it may expose right now (live computation, never a stored flag)."""
    return [r for r in status(cat, sc) if r["status"] == "approved"]
