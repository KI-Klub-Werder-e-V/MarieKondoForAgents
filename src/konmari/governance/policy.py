"""Source policies (design D9) and write grants (D4): versioned, owner/compliance-approved, revertible, audited.
A "source" in the governance step is one in-scope category of the pilot (spec: "per in-scope source or category")."""

import json
import re
from collections import Counter
from datetime import timedelta

from konmari.catalog import Catalog, new_id, now
from konmari.categories import store as cats
from konmari.charter.store import Scope
from konmari.governance.audit import write_event
from konmari.inventory.sensitivity import TIERS, rank
from konmari.versions import Refused

EXPOSURE = ("allowed", "blocked")
OPERATORS = ("redact", "mask", "replace-with-type", "pseudonymize")
WILDCARDS = {"*", "all", "any"}
DEFAULT = {"exposure": "blocked", "sensitivity": "internal", "masking": {"default": "replace-with-type"},
           "acl_sync_cadence": "PT24H", "tools": ["search"], "owner": None, "principals": None,
           "service_principals": [], "legal_basis": None, "entities": []}
_DUR = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?$")


def duration(s: str) -> timedelta:
    m = _DUR.match(s or "")
    if not m or not any(m.groups()):
        raise Refused(f"'{s}' is not an ISO 8601 duration like PT24H, PT1H or P1D")
    d, h, mi = (int(x or 0) for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi)


def wildcard(scope: str) -> bool:
    return scope.strip().lower() in WILDCARDS or scope.split(":", 1)[-1].strip().lower() in WILDCARDS


def validate(doc: dict) -> list[str]:
    errs = []
    if doc.get("exposure") not in EXPOSURE:
        errs.append(f"exposure must be one of {', '.join(EXPOSURE)}")
    if doc.get("sensitivity") not in TIERS:
        errs.append(f"sensitivity must be one of {', '.join(TIERS)}")
    for ent, op in (doc.get("masking") or {}).items():
        if op not in OPERATORS:
            errs.append(f"masking {ent}: operator '{op}' is not one of {', '.join(OPERATORS)}")
    try:
        duration(doc.get("acl_sync_cadence"))
    except Refused as e:
        errs += e.reasons
    errs += [f"tool scope '{t}' is a wildcard; name each tool" for t in doc.get("tools") or [] if wildcard(t)]
    p = doc.get("principals")
    if p is not None and not (isinstance(p, dict) and p.get("allow")):
        errs.append("principals needs {allow: [...], deny: [...]} with at least one allowed principal")
    return errs


def _row(r: dict | None) -> dict | None:
    if r:
        r["doc"] = json.loads(r["content"])
    return r


def versions(cat: Catalog, pid: str) -> list[dict]:
    return [_row(r) for r in cat.query("SELECT * FROM source_policy WHERE policy_id = ? ORDER BY version", (pid,))]


def latest(cat: Catalog, pid: str) -> dict | None:
    return _row(cat.one("SELECT * FROM source_policy WHERE policy_id = ? ORDER BY version DESC LIMIT 1", (pid,)))


def active(cat: Catalog, pid: str) -> dict | None:
    """The approved version: the only one that is enforced."""
    return _row(cat.one("SELECT * FROM source_policy WHERE policy_id = ? AND status = 'approved'", (pid,)))


def active_versions(cat: Catalog) -> dict[str, int]:
    return {r["policy_id"]: r["version"] for r in cat.query("SELECT policy_id, version FROM source_policy"
                                                             " WHERE status = 'approved'")}


def _insert(cat, sc, pid, doc, actor, reason, status="pending", approved_by=None, reverted_from=None) -> dict:
    if errs := validate(doc):
        raise Refused(errs)
    cur = latest(cat, pid)
    version = cur["version"] + 1 if cur else 1
    t = now()
    cat.execute("INSERT INTO source_policy VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, version, status, json.dumps(doc, sort_keys=True), actor, t, reason, approved_by,
                 t if approved_by else None, reverted_from))
    return latest(cat, pid)


def _member(sc: Scope, sid: str) -> set[str]:
    if sid not in sc.stakeholders:
        raise Refused(f"'{sid}' is not in the stakeholder register")
    return set(sc.stakeholders[sid].get("roles", []))


def _in_scope(sc: Scope, pid: str) -> None:
    if pid not in sc.categories:
        raise Refused(f"'{pid}' is not an in-scope category of charter {sc.charter_id}")


def seed(cat: Catalog, sc: Scope, actor: str) -> list[dict]:
    """One pending draft per in-scope category, in the approved category order; sensitivity and owner from the
    inventory (highest tier and most common owner of the category's assets)."""
    _member(sc, actor)
    order = (cats.approved_order(cat, sc.charter_id) or {}).get("categories") or []
    out = []
    for pid in [c for c in order if c in sc.categories] + [c for c in sc.categories if c not in order]:
        if latest(cat, pid):
            continue
        assets = [a for a in cat.query("SELECT * FROM source_asset WHERE charter_id = ?", (sc.charter_id,))
                  if (cats.primary(cat, a["asset_uri"]) or {}).get("category_id") == pid]
        tier = max((a["sensitivity_tier"] for a in assets if a["sensitivity_tier"] in TIERS), key=rank,
                   default="internal")
        owners = Counter(a["owner"] for a in assets if a["owner"])
        doc = {**DEFAULT, "sensitivity": tier, "owner": owners.most_common(1)[0][0] if owners else sc.approver(pid)}
        if rank(tier) >= rank("personal"):
            doc["masking"] = {"default": "replace-with-type", "PERSON": "pseudonymize"}
        row = _insert(cat, sc, pid, doc, actor, "seeded from source-inventory and category-map")
        write_event(cat, "govern.policy.create", actor=actor, charter_id=sc.charter_id, policy=pid,
                    previous=None, new=row["version"], reason="seed")
        out.append(row)
    return out


def edit(cat: Catalog, sc: Scope, pid: str, changes: dict, actor: str, reason: str | None = None) -> dict:
    """A new pending version; enforcement keeps using the approved one until approval."""
    _member(sc, actor)
    _in_scope(sc, pid)
    cur = latest(cat, pid)
    row = _insert(cat, sc, pid, {**(cur["doc"] if cur else DEFAULT), **changes}, actor, reason)
    write_event(cat, "govern.policy.edit", actor=actor, charter_id=sc.charter_id, policy=pid,
                previous=cur and cur["version"], new=row["version"], fields=sorted(changes), reason=reason)
    return row


def _check_approver(sc: Scope, pid: str, approver: str, doc: dict) -> None:
    roles = _member(sc, approver)
    if not roles & {"data_owner", "compliance"}:
        raise Refused(f"'{approver}' holds neither the data_owner nor the compliance role")
    if doc.get("legal_basis") and "compliance" not in roles:
        raise Refused("a policy recording a legal basis for special-category data needs approval by compliance")


def _supersede(cat: Catalog, pid: str) -> int | None:
    cur = active(cat, pid)
    if cur:
        cat.execute("UPDATE source_policy SET status = 'superseded' WHERE policy_id = ? AND version = ?",
                    (pid, cur["version"]))
    return cur and cur["version"]


def approve(cat: Catalog, sc: Scope, pid: str, version: int, approver: str, reason: str | None = None) -> dict:
    row = next((r for r in versions(cat, pid) if r["version"] == version), None)
    if row is None or row["status"] != "pending":
        raise Refused(f"policy {pid} has no pending version {version}")
    _check_approver(sc, pid, approver, row["doc"])
    prev = _supersede(cat, pid)
    cat.execute("UPDATE source_policy SET status = 'approved', approved_by = ?, approved_at = ? WHERE policy_id = ?"
                " AND version = ?", (approver, now(), pid, version))
    write_event(cat, "govern.policy.approve", actor=approver, charter_id=sc.charter_id, policy=pid, previous=prev,
                new=version, reason=reason)
    return active(cat, pid)


def revert(cat: Catalog, sc: Scope, pid: str, to_version: int, approver: str, reason: str | None = None) -> dict:
    """Reactivate an earlier version's content as a new approved version (prior versions stay retrievable)."""
    old = next((r for r in versions(cat, pid) if r["version"] == to_version), None)
    if old is None:
        raise Refused(f"policy {pid} has no version {to_version}")
    _check_approver(sc, pid, approver, old["doc"])
    prev = _supersede(cat, pid)
    row = _insert(cat, sc, pid, old["doc"], approver, reason or f"revert to version {to_version}", "approved",
                  approver, to_version)
    write_event(cat, "govern.policy.revert", actor=approver, charter_id=sc.charter_id, policy=pid, previous=prev,
                new=row["version"], reverted_to=to_version, reason=reason)
    return row


# ---------------------------------------------------------------- write grants

def grant(cat: Catalog, sc: Scope, agent: str, category: str, tool: str, approver: str, days: int = 90,
          max_days: int = 90) -> dict:
    """Owner-approved write grant for one agent, category and tool, with an expiry."""
    _in_scope(sc, category)
    if wildcard(tool) or wildcard(category):
        raise Refused(f"wildcard scope '{tool if wildcard(tool) else category}' refused; name one tool and category")
    if approver != sc.approver(category):
        raise Refused(f"'{approver}' is not the data owner (approver) of category {category}")
    if not 0 < days <= max_days:
        raise Refused(f"grant expiry must be 1 to {max_days} days")
    t = now()
    g = {"grant_id": new_id(), "agent_id": agent, "category_id": category, "tool": tool, "approved_by": approver,
         "approved_at": t, "expires_at": t + timedelta(days=days)}
    cat.execute("INSERT INTO write_grant VALUES (?, ?, ?, ?, ?, ?, ?, NULL)", tuple(g.values()))
    write_event(cat, "govern.grant.approve", actor=approver, charter_id=sc.charter_id, grant=g["grant_id"],
                agent=agent, category=category, tool=tool, expires_at=g["expires_at"].isoformat())
    return g


def active_grant(cat: Catalog, agent: str, category: str, tool: str, at=None) -> dict | None:
    return cat.one("SELECT * FROM write_grant WHERE agent_id = ? AND category_id = ? AND tool = ? AND revoked_at IS"
                   " NULL AND expires_at > ? ORDER BY expires_at DESC LIMIT 1", (agent, category, tool, at or now()))
