"""Source-of-truth policy: versioned per-entity YAML, validation, pending -> owner approval -> active, rollback,
and coverage of agent-exposed fields."""

import json
from pathlib import Path

from konmari import versions as v
from konmari.canonical import canonical_hash, canonical_json, load_yaml
from konmari.catalog import Catalog, now
from konmari.charter.store import Scope
from konmari.standardize import log, metric
from konmari.versions import Refused

STRATEGIES = ("source_priority", "most_recent", "most_complete", "owner_decision")
DUE_DAYS = 30  # our assumption, not a legal deadline (design decision 7)


def policy_errors(doc, sc: Scope | None = None) -> list[str]:
    if not isinstance(doc, dict) or not doc.get("entity") or not isinstance(doc.get("rules"), dict):
        return ["a policy file needs 'entity' and a 'rules' mapping (field -> rule)"]
    errs = []
    for f, r in doc["rules"].items():
        name = f"{doc['entity']}.{f}"
        if not isinstance(r, dict):
            errs.append(f"{name}: rule must be a mapping")
            continue
        for k in ("strategy", "owner", "tie_breaker", "rationale"):
            if not r.get(k):
                errs.append(f"{name}: '{k}' is required")
        for k in ("strategy", "tie_breaker"):
            if r.get(k) and r[k] not in STRATEGIES:
                errs.append(f"{name}: {k} must be one of {', '.join(STRATEGIES)}")
        used = {r.get("strategy"), r.get("tie_breaker")}
        if "source_priority" in used and not (isinstance(r.get("sources"), list) and r["sources"]):
            errs.append(f"{name}: source_priority needs an ordered 'sources' list")
        if "most_recent" in used and not r.get("timestamp_field"):
            errs.append(f"{name}: most_recent needs a 'timestamp_field' (a declared source timestamp)")
        if sc is not None and r.get("owner") and r["owner"] not in sc.stakeholders:
            errs.append(f"{name}: owner '{r['owner']}' is not in the charter's stakeholder register")
    return errs


def read_policy(path: str | Path, sc: Scope | None = None) -> dict:
    doc = load_yaml(Path(path))
    if errs := policy_errors(doc, sc):
        raise Refused([f"{path}: {e}" for e in errs])
    return doc


def _row(r):
    if r:
        r["doc"] = json.loads(r["content"])
    return r


def active_policy(cat: Catalog, entity: str) -> dict | None:
    return _row(cat.one("SELECT * FROM policy_version WHERE entity = ? AND active", (entity,)))


def get_version(cat: Catalog, entity: str, version: int) -> dict:
    r = _row(cat.one("SELECT * FROM policy_version WHERE entity = ? AND version = ?", (entity, version)))
    if r is None:
        raise Refused(f"policy version {version} for '{entity}' not found")
    return r


def versions(cat: Catalog, entity: str) -> list[dict]:
    return [_row(r) for r in cat.query("SELECT * FROM policy_version WHERE entity = ? ORDER BY version", (entity,))]


def load(cat: Catalog, sc: Scope | None, path: str | Path, author: str) -> dict:
    """Store an edited policy as a new `pending` version with diff and dry-run impact; the active version stays."""
    from konmari.policy import golden

    doc = read_policy(path, sc)
    entity = doc["entity"]
    rows = versions(cat, entity)
    if rows and rows[-1]["content_hash"] == canonical_hash(doc):
        return rows[-1]
    active = active_policy(cat, entity)
    diff = v.diff(active["doc"] if active else {}, doc)
    impact = golden.impact(cat, entity, doc, (rows[-1]["version"] if rows else 0) + 1)
    ver = (rows[-1]["version"] if rows else 0) + 1
    cat.execute("INSERT INTO policy_version VALUES (?, ?, 'pending', FALSE, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (entity, ver, canonical_json(doc), canonical_hash(doc), author, now(), json.dumps(diff),
                 json.dumps(impact, default=str)))
    for f, r in doc["rules"].items():
        cat.execute("INSERT INTO policy_rule VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (entity, ver, f, r["strategy"], json.dumps(r.get("sources")), r.get("timestamp_field"),
                     r["tie_breaker"], r["owner"], r["rationale"]))
    log(cat, sc, "policy.load", f"policy:{entity}", active and active["version"], {"version": ver, "status": "pending"},
        author, diff=diff, impact_count=len(impact))
    return get_version(cat, entity, ver)


def _owners_needed(old: dict | None, new: dict) -> set[str]:
    o, n = (old or {}).get("rules", {}), new["rules"]
    return {r["owner"] for f, r in n.items() if o.get(f) != r} | {r["owner"] for f, r in o.items() if f not in n}


def _check_owner(sc: Scope | None, actor: str, needed: set[str], what: str) -> None:
    # shortcut: one approver per version; a change that touches rules of several owners is split into versions
    if needed and needed != {actor}:
        raise Refused(f"{what} needs the field owner(s) {', '.join(sorted(needed))}; '{actor}' cannot approve it "
                      "alone (split the change per owner)")
    if sc is not None and "data_owner" not in sc.stakeholders.get(actor, {}).get("roles", []):
        raise Refused(f"'{actor}' is not named as a data owner in the pilot charter")


def _activate(cat: Catalog, entity: str, version: int) -> None:
    cat.execute("UPDATE policy_version SET active = FALSE WHERE entity = ?", (entity,))
    cat.execute("UPDATE policy_version SET active = TRUE WHERE entity = ? AND version = ?", (entity, version))


def approve(cat: Catalog, sc: Scope | None, entity: str, version: int, actor: str) -> dict:
    from konmari.policy import golden

    row = get_version(cat, entity, version)
    if row["status"] != "pending":
        raise Refused(f"policy version {version} for '{entity}' is {row['status']}, not pending")
    active = active_policy(cat, entity)
    _check_owner(sc, actor, _owners_needed(active and active["doc"], row["doc"]), f"policy version {version}")
    cat.execute("UPDATE policy_version SET status = 'approved', approver = ?, approved_at = ? WHERE entity = ?"
                " AND version = ?", (actor, now(), entity, version))
    _activate(cat, entity, version)
    log(cat, sc, "policy.approve", f"policy:{entity}", active and active["version"], version, actor,
        impact_count=len(json.loads(row["impact"])))
    golden.rebuild_if_ready(cat, sc, entity, actor)
    return get_version(cat, entity, version)


def rollback(cat: Catalog, sc: Scope | None, entity: str, to_version: int, actor: str, reason: str) -> dict:
    from konmari.policy import golden

    row = get_version(cat, entity, to_version)
    if row["status"] != "approved":
        raise Refused(f"policy version {to_version} was never approved; rollback only goes to approved versions")
    active = active_policy(cat, entity)
    if active and active["version"] == to_version:
        raise Refused(f"policy version {to_version} is already active")
    _check_owner(sc, actor, _owners_needed(active and active["doc"], row["doc"]), "the rollback")
    _activate(cat, entity, to_version)
    log(cat, sc, "policy.rollback", f"policy:{entity}", active and active["version"], to_version, actor, reason)
    golden.rebuild_if_ready(cat, sc, entity, actor)
    return get_version(cat, entity, to_version)


def read_exposure(path: str | Path) -> dict[str, list[str]]:
    """Agent-exposed fields as declared by data-products / mcp-exposure (c08): {exposed: {entity: [fields]}}."""
    doc = load_yaml(Path(path))
    if not isinstance(doc, dict) or not isinstance(doc.get("exposed"), dict):
        raise Refused(f"{path}: an exposure declaration needs an 'exposed' mapping (entity -> fields)")
    return doc["exposed"]


def coverage(cat: Catalog, exposed: dict[str, list[str]]) -> dict[str, list[str]]:
    """entity -> exposed fields without a rule in the active policy. No implicit default ever fills the gap."""
    out = {}
    for entity, fields in sorted(exposed.items()):
        active = active_policy(cat, entity)
        rules = active["doc"]["rules"] if active else {}
        out[entity] = [f"{entity}.{f}" for f in fields if f not in rules]
        metric(cat, "policy_coverage", "coverage", (len(fields) - len(out[entity])) / len(fields) if fields else 1.0,
               entity=entity)
    return out
