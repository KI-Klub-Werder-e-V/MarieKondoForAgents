"""Category map: cross-system categories, audited assignments, overlap hints, risk-ordered approved sequence,
and the per-category step order that steps 03-09 must respect."""

import json
import re
from pathlib import Path

from konmari.catalog import Catalog, audit_change, connect, now
from konmari.charter.store import Scope, load_charter, scope
from konmari.charter.validation import STEPS
from konmari.inventory import sensitivity
from konmari.inventory.store import approvers, gate_blockers
from konmari.versions import Refused

STEP = "inventory"
DEFAULTS = [
    ("kb-and-sops", "Knowledge base and SOPs", "Handbooks, FAQs, how-tos and standard operating procedures",
     "internal"),
    ("customer-records", "Customer records", "Customer, member and contact records, e.g. CRM objects and member "
     "lists", "personal"),
    ("policies-and-contracts", "Policies and contracts", "Policies, guidelines, statutes, terms and contracts",
     "confidential"),
    ("financial-ops-metrics", "Financial and operational metrics", "Invoices, budgets, KPIs and operational "
     "reports", "confidential"),
    ("support-history", "Support history", "Tickets, member or customer requests and the answers given", "personal"),
]
DEFAULT_ORDER = ["kb-and-sops", "support-history", "policies-and-contracts", "customer-records",
                 "financial-ops-metrics"]  # tie-break order from the source brief
DEFAULT_WEIGHTS = {"max_tier": 1.0, "personal_share": 2.0, "systems": 0.5, "overlaps": 0.25}  # our assumptions
STEP_STATUS = ["not_started", "in_progress", "complete"]

# Transparent suggestion rules (design decision 9): (rule ID, category, pattern on title/path or table name).
KEYWORDS = [
    ("01", "policies-and-contracts", r"vertr(ae|ä)ge?|vertrag|contracts?|richtlinie|polic(y|ies)|satzung|\bagb\b"),
    ("02", "kb-and-sops", r"handbuch|anleitung|\bsops?\b|\bfaq|wiki|knowledge|how-?to|leitfaden|manual"),
    ("03", "customer-records", r"kunden?|customers?|mitglied|members?|\bcrm\b|kontakte|contacts"),
    ("04", "financial-ops-metrics", r"finanz|rechnung|invoices?|buchhaltung|budget|\bkpis?\b|metrics|umsatz|revenue"),
    ("05", "support-history", r"tickets?|support|anfragen|helpdesk"),
]
MEDIA_TYPES = [
    ("R-MT-01", "kb-and-sops", ("text/markdown", "text/html", "text/plain")),
    ("R-MT-02", "financial-ops-metrics", ("application/vnd.ms-excel",
                                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
]


class StepRefused(Refused):
    """A KonMari step may not process this category yet; args[0] lists the reasons."""


def _scope(charter_path, cat) -> Scope:
    return scope(load_charter(charter_path)["charter_id"], cat)


# ---------------------------------------------------------------- categories

def ensure_defaults(cat: Catalog, sc: Scope) -> None:
    if cat.one("SELECT 1 AS x FROM category WHERE charter_id = ? LIMIT 1", (sc.charter_id,)):
        return
    rows = DEFAULTS + [(c, c, "category extension from the pilot charter", "internal")
                       for c in sc.categories if c not in DEFAULT_ORDER]
    for cid, name, desc, tier in rows:
        cat.execute("INSERT INTO category VALUES (?, ?, ?, ?, ?, 'active')", (sc.charter_id, cid, name, desc, tier))


def categories(cat: Catalog, sc: Scope, active_only=True) -> list[dict]:
    ensure_defaults(cat, sc)
    return cat.query("SELECT * FROM category WHERE charter_id = ?" + (" AND status = 'active'" if active_only else "")
                     + " ORDER BY category_id", (sc.charter_id,))


def _category(cat: Catalog, sc: Scope, cid: str) -> dict:
    row = next((c for c in categories(cat, sc, active_only=False) if c["category_id"] == cid), None)
    if row is None or row["status"] != "active":
        raise Refused(f"category '{cid}' is not an active category of charter '{sc.charter_id}'")
    return row


def _approver(sc: Scope, actor: str) -> None:
    if actor not in approvers(sc):
        raise Refused(f"'{actor}' is not an approver named in charter '{sc.charter_id}' (data_scope.categories)")


def add(cat: Catalog, sc: Scope, cid: str, name: str, description: str, baseline: str, actor: str) -> None:
    _approver(sc, actor)
    if not re.match(r"^[a-z0-9][a-z0-9-]*$", cid):
        raise Refused(f"category ID '{cid}' must be lowercase letters, digits and dashes")
    if baseline not in sensitivity.TIERS:
        raise Refused(f"invalid baseline tier '{baseline}'; valid: {', '.join(sensitivity.TIERS)}")
    if any(c["category_id"] == cid for c in categories(cat, sc, active_only=False)):
        raise Refused(f"category '{cid}' already exists")
    cat.execute("INSERT INTO category VALUES (?, ?, ?, ?, ?, 'active')", (sc.charter_id, cid, name, description, baseline))
    audit_change(cat, "categories.add", cid, None, {"name": name, "description": description, "baseline": baseline},
                 actor=actor, charter_id=sc.charter_id, step=STEP)


def rename(cat: Catalog, sc: Scope, cid: str, name: str, actor: str) -> None:
    _approver(sc, actor)
    old = _category(cat, sc, cid)
    cat.execute("UPDATE category SET name = ? WHERE charter_id = ? AND category_id = ?", (name, sc.charter_id, cid))
    audit_change(cat, "categories.rename", cid, old["name"], name, actor=actor, charter_id=sc.charter_id, step=STEP)


def retire(cat: Catalog, sc: Scope, cid: str, actor: str, reason: str | None = None) -> None:
    _approver(sc, actor)
    _category(cat, sc, cid)
    used = [r["asset_uri"] for r in cat.query("SELECT asset_uri FROM asset_category WHERE category_id = ? AND state <>"
                                              " 'rejected' AND asset_uri IN (SELECT asset_uri FROM source_asset WHERE"
                                              " charter_id = ?) ORDER BY asset_uri", (cid, sc.charter_id))]
    if used:
        raise Refused([f"category '{cid}' still has {len(used)} assigned asset(s); reassign them first:", *used])
    cat.execute("UPDATE category SET status = 'retired' WHERE charter_id = ? AND category_id = ?", (sc.charter_id, cid))
    audit_change(cat, "categories.retire", cid, "active", "retired", actor=actor, reason=reason,
                 charter_id=sc.charter_id, step=STEP)


# ---------------------------------------------------------------- assignments

def _assets(cat: Catalog, charter_id: str) -> list[dict]:
    return cat.query("SELECT * FROM source_asset WHERE charter_id = ? AND status <> 'missing' ORDER BY asset_uri",
                     (charter_id,))


def assignments(cat: Catalog, uri: str) -> list[dict]:
    return cat.query("SELECT * FROM asset_category WHERE asset_uri = ? ORDER BY is_primary DESC, category_id", (uri,))


def primary(cat: Catalog, uri: str) -> dict | None:
    return next((a for a in assignments(cat, uri) if a["is_primary"] and a["state"] != "rejected"), None)


def _upsert(cat: Catalog, uri, cid, is_primary, state, actor, rule_id=None, matched=None) -> None:
    cat.execute("DELETE FROM asset_category WHERE asset_uri = ? AND category_id = ?", (uri, cid))
    cat.execute("INSERT INTO asset_category VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (uri, cid, is_primary, state, rule_id, matched, now(), actor))


def rule_for(asset: dict, hints: list[str], source_categories: list[str]) -> tuple[str, str, str] | None:
    """(rule_id, category, matched) of the first rule that fires, or None."""
    text = f"{asset['title']} {asset['path'] or ''}"
    table = asset["title"].split(".")[-1] if asset["system_type"] == "sql" else None
    for prefix, target in (("R-TBL", table), ("R-KW", text)):  # the table name is the most specific signal
        for num, cid, rx in KEYWORDS:
            if target and (m := re.search(rx, target, re.I)):
                return f"{prefix}-{num}", cid, m.group(0)
    for rid, cid, types in MEDIA_TYPES:
        if asset["format"] in types:
            return rid, cid, asset["format"]
    if found := [h for h in hints if h != "DE_PLZ"]:
        return "R-HINT-01", "customer-records", ", ".join(sorted(found))
    if len(source_categories) == 1:
        return "R-SRC-01", source_categories[0], f"charter source lists only {source_categories[0]}"
    return None


def suggest(cat: Catalog, sc: Scope, actor: str) -> list[dict]:
    active = {c["category_id"] for c in categories(cat, sc)}
    src_cats = {s["id"]: s.get("categories", []) for s in sc.sources}
    out = []
    for a in _assets(cat, sc.charter_id):
        rows = assignments(cat, a["asset_uri"])
        if any(r["is_primary"] and r["state"] != "rejected" for r in rows):
            continue
        hints = [h["entity_type"] for h in cat.query("SELECT entity_type FROM sensitivity_hint WHERE asset_uri = ?",
                                                     (a["asset_uri"],))]
        hit = rule_for(a, hints, src_cats.get(a["source_id"], []))
        if not hit or hit[1] not in active or any(r["category_id"] == hit[1] and r["state"] == "rejected" for r in rows):
            continue
        rid, cid, matched = hit
        _upsert(cat, a["asset_uri"], cid, True, "suggested", actor, rid, matched)
        audit_change(cat, "categories.suggest", a["asset_uri"], None, {"primary": cid, "state": "suggested"},
                     actor=actor, reason=f"rule {rid} matched '{matched}'", charter_id=sc.charter_id, step=STEP,
                     rule_id=rid)
        out.append({"asset_uri": a["asset_uri"], "category": cid, "rule_id": rid, "matched": matched})
    return out


def accept(cat: Catalog, sc: Scope, uri: str, actor: str, reason: str | None = None) -> None:
    p = primary(cat, uri)
    if not p or p["state"] != "suggested":
        raise Refused(f"'{uri}' has no suggested primary category to accept")
    _upsert(cat, uri, p["category_id"], True, "accepted", actor, p["rule_id"], p["matched"])
    audit_change(cat, "categories.accept", uri, {"primary": p["category_id"], "state": "suggested"},
                 {"primary": p["category_id"], "state": "accepted"}, actor=actor, reason=reason,
                 charter_id=sc.charter_id, step=STEP)


def reject(cat: Catalog, sc: Scope, uri: str, actor: str, reason: str) -> None:
    p = primary(cat, uri)
    if not p or p["state"] != "suggested":
        raise Refused(f"'{uri}' has no suggested primary category to reject")
    _upsert(cat, uri, p["category_id"], True, "rejected", actor, p["rule_id"], p["matched"])
    audit_change(cat, "categories.reject", uri, {"primary": p["category_id"], "state": "suggested"},
                 {"primary": None}, actor=actor, reason=reason, charter_id=sc.charter_id, step=STEP)


def assign(cat: Catalog, sc: Scope, uri: str, cid: str, actor: str, is_primary=True, reason: str | None = None) -> None:
    _category(cat, sc, cid)
    if not cat.one("SELECT asset_uri FROM source_asset WHERE asset_uri = ?", (uri,)):
        raise Refused(f"no inventory record for '{uri}'")
    p = primary(cat, uri)
    if not is_primary:
        if p and p["category_id"] == cid:
            raise Refused(f"'{cid}' is already the primary category of '{uri}'")
        _upsert(cat, uri, cid, False, "accepted", actor)
        audit_change(cat, "categories.assign", uri, None, {"secondary": cid}, actor=actor, reason=reason,
                     charter_id=sc.charter_id, step=STEP)
        return
    if p and p["state"] == "accepted":
        raise Refused(f"'{uri}' already has the accepted primary category '{p['category_id']}'; demote it first "
                      f"with 'konmari categories demote {uri}'")
    if p:  # changing a suggestion: the suggestion counts as rejected so it is not suggested again
        _upsert(cat, uri, p["category_id"], True, "rejected", actor, p["rule_id"], p["matched"])
    _upsert(cat, uri, cid, True, "accepted", actor)
    audit_change(cat, "categories.assign", uri, {"primary": p["category_id"] if p else None},
                 {"primary": cid, "state": "accepted"}, actor=actor, reason=reason, charter_id=sc.charter_id, step=STEP)


def demote(cat: Catalog, sc: Scope, uri: str, actor: str, reason: str | None = None) -> None:
    p = primary(cat, uri)
    if not p or p["state"] != "accepted":
        raise Refused(f"'{uri}' has no accepted primary category to demote")
    _upsert(cat, uri, p["category_id"], False, "accepted", actor)
    audit_change(cat, "categories.demote", uri, {"primary": p["category_id"]},
                 {"primary": None, "secondary": p["category_id"]}, actor=actor, reason=reason,
                 charter_id=sc.charter_id, step=STEP)


def primary_history(cat: Catalog, uri: str) -> list[str]:
    """Primary categories the asset has had, oldest first, from the audit trail."""
    out = []
    for r in cat.query("SELECT details FROM audit_event WHERE action IN ('categories.accept', 'categories.assign',"
                       " 'categories.revert') ORDER BY ts"):
        d = json.loads(r["details"])
        if d.get("entity") == uri and (d.get("new") or {}).get("primary"):
            out.append(d["new"]["primary"])
    return out


def revert(cat: Catalog, sc: Scope, uri: str, actor: str, to: str | None = None, reason: str | None = None) -> str:
    p = primary(cat, uri)
    current = p["category_id"] if p else None
    earlier = [c for c in primary_history(cat, uri) if c != current]
    if to is None:
        if not earlier:
            raise Refused(f"'{uri}' has no earlier primary category to revert to")
        to = earlier[-1]
    elif to not in earlier:
        raise Refused(f"'{to}' was never an earlier primary category of '{uri}'"
                      + (f"; earlier: {', '.join(dict.fromkeys(earlier))}" if earlier else ""))
    _category(cat, sc, to)
    if p:
        cat.execute("DELETE FROM asset_category WHERE asset_uri = ? AND category_id = ?", (uri, current))
    _upsert(cat, uri, to, True, "accepted", actor)
    audit_change(cat, "categories.revert", uri, {"primary": current}, {"primary": to, "state": "accepted"},
                 actor=actor, reason=reason, charter_id=sc.charter_id, step=STEP)
    return to


# ---------------------------------------------------------------- category view and overlap hints

def members(cat: Catalog, sc: Scope, cid: str, accepted_only=False) -> list[dict]:
    states = ("accepted",) if accepted_only else ("accepted", "suggested")
    return [a | {"is_primary": r["is_primary"], "state": r["state"]}
            for a in _assets(cat, sc.charter_id) for r in assignments(cat, a["asset_uri"])
            if r["category_id"] == cid and r["state"] in states]


def _norm_title(a: dict) -> str:
    t = a["title"].split(".")[-1] if a["system_type"] == "sql" else re.sub(r"\.[A-Za-z0-9]{1,5}$", "", a["title"])
    return re.sub(r"[^a-z0-9äöüß]", "", t.lower())


def overlap_hints(cat: Catalog, sc: Scope, cid: str) -> list[dict]:
    """Likely cross-system overlaps inside one category. Hints only: input for c04 dedup-detection,
    no status changes."""
    assets = members(cat, sc, cid)
    keys = {"title": _norm_title, "content_hash": lambda a: a["content_hash"],
            "columns": lambda a: ",".join(sorted(f["name"] for f in json.loads(a["schema_fields"])))
            if a["schema_fields"] else None}
    out = []
    for kind, fn in keys.items():
        groups: dict[str, list] = {}
        for a in assets:
            if k := fn(a):
                groups.setdefault(k, []).append(a)
        for k, grp in sorted(groups.items()):
            if len({a["source_id"] for a in grp}) > 1:
                out.append({"kind": kind, "key": k, "assets": sorted(a["asset_uri"] for a in grp)})
    return out


def view(cat: Catalog, sc: Scope, cid: str) -> dict:
    _category(cat, sc, cid)
    by: dict[str, list] = {}
    for a in members(cat, sc, cid):
        by.setdefault(a["system_type"], []).append(a)
    return {"category": cid, "by_system": dict(sorted(by.items())), "overlaps": overlap_hints(cat, sc, cid)}


# ---------------------------------------------------------------- risk score and approved order

def risk(cat: Catalog, sc: Scope, weights: dict | None = None) -> dict[str, dict]:
    w = DEFAULT_WEIGHTS | (weights or {})
    out = {}
    for cid in sc.categories:
        assets = members(cat, sc, cid, accepted_only=True)
        confirmed = [a["sensitivity_tier"] for a in assets if a["tier_state"] == "confirmed"]
        personal = [a for a in assets if a["sensitivity_tier"] in ("personal", "special-category")
                    or json.loads(a["personal_data_categories"] or "[]")]
        inputs = {"max_tier": max((sensitivity.rank(t) for t in confirmed), default=0),
                  "personal_share": round(len(personal) / len(assets), 3) if assets else 0.0,
                  "systems": len({a["system_type"] for a in assets}),
                  "overlaps": len(overlap_hints(cat, sc, cid))}
        out[cid] = {"score": round(sum(w[k] * v for k, v in inputs.items()), 3), "inputs": inputs,
                    "special_category": any(a["sensitivity_tier"] == "special-category" for a in assets)}
    return out


def _tiebreak(cid: str) -> int:
    return DEFAULT_ORDER.index(cid) if cid in DEFAULT_ORDER else len(DEFAULT_ORDER)


def special_not_last(cat: Catalog, sc: Scope, order: list[str]) -> str | None:
    """Reason why `order` breaks the special-category-last rule for the current tiers, or None."""
    scores = risk(cat, sc)
    special = [c for c in order if scores.get(c, {}).get("special_category")]
    if special and order[-len(special):] != special:
        return f"categories with special-category data ({', '.join(special)}) must come after all others"
    return None


def proposed_order(scores: dict) -> list[str]:
    # special-category data always goes last ("sensitive later"), then by score, then the brief's default order
    return sorted(scores, key=lambda c: (scores[c]["special_category"], scores[c]["score"], _tiebreak(c), c))


def order_versions(cat: Catalog, charter_id: str) -> list[dict]:
    rows = cat.query("SELECT * FROM category_order_version WHERE charter_id = ? ORDER BY version", (charter_id,))
    for r in rows:
        for k in ("categories", "scores", "weights"):
            r[k] = json.loads(r[k])
    return rows


def approved_order(cat: Catalog, charter_id: str) -> dict | None:
    rows = [r for r in order_versions(cat, charter_id) if r["status"] == "approved"]
    return rows[-1] if rows else None


def _insert_version(cat, sc, order, scores, weights, actor, reason) -> dict:
    version = len(order_versions(cat, sc.charter_id)) + 1
    cat.execute("INSERT INTO category_order_version (charter_id, version, status, categories, scores, weights, reason,"
                " created_at, created_by) VALUES (?, ?, 'proposed', ?, ?, ?, ?, ?, ?)",
                (sc.charter_id, version, json.dumps(order), json.dumps(scores), json.dumps(weights), reason, now(),
                 actor))
    audit_change(cat, "categories.order.propose", f"category_order v{version}", None, order, actor=actor,
                 reason=reason, charter_id=sc.charter_id, step=STEP, weights=weights)
    return order_versions(cat, sc.charter_id)[-1]


def propose(cat: Catalog, sc: Scope, actor: str, weights: dict | None = None) -> dict:
    unknown = set(weights or {}) - set(DEFAULT_WEIGHTS)
    if unknown:
        raise Refused(f"unknown weight(s) {', '.join(sorted(unknown))}; valid: {', '.join(DEFAULT_WEIGHTS)}")
    w = DEFAULT_WEIGHTS | (weights or {})
    scores = risk(cat, sc, w)
    return _insert_version(cat, sc, proposed_order(scores), scores, w, actor, "risk-score proposal")


def set_order(cat: Catalog, sc: Scope, order: list[str], actor: str, reason: str) -> dict:
    _approver(sc, actor)
    if not (reason or "").strip():
        raise Refused("an order change needs a reason (--reason)")
    if sorted(order) != sorted(sc.categories):
        raise Refused(f"the order must list each in-scope category exactly once: {', '.join(sorted(sc.categories))}")
    base = approved_order(cat, sc.charter_id)
    w = base["weights"] if base else DEFAULT_WEIGHTS
    scores = risk(cat, sc, w)
    if why := special_not_last(cat, sc, order):
        raise Refused(why)
    return _insert_version(cat, sc, order, scores, w, actor, reason)


def approve(cat: Catalog, sc: Scope, actor: str, version: int | None = None) -> dict:
    _approver(sc, actor)
    rows = order_versions(cat, sc.charter_id)
    row = next((r for r in rows if r["version"] == version), None) if version else (rows[-1] if rows else None)
    if row is None or row["status"] != "proposed":
        raise Refused("no proposed category order to approve; run 'konmari categories order --propose' first")
    if why := special_not_last(cat, sc, row["categories"]):
        raise Refused(f"{why}; propose a new order")
    prev = approved_order(cat, sc.charter_id)
    cat.execute("UPDATE category_order_version SET status = 'approved', approved_by = ?, approved_at = ?"
                " WHERE charter_id = ? AND version = ?", (actor, now(), sc.charter_id, row["version"]))
    audit_change(cat, "categories.order.approve", f"category_order v{row['version']}",
                 prev["categories"] if prev else None, row["categories"],
                 actor=actor, charter_id=sc.charter_id, step=STEP)
    return approved_order(cat, sc.charter_id)


def handoff(cat: Catalog, sc: Scope, actor: str | None = None) -> tuple[list[str] | None, list[str]]:
    """The category sequence for readiness-scorecard (c03) and later steps, or None with the reasons.
    Passing completes the `inventory` step for every in-scope category."""
    reasons = []
    order = approved_order(cat, sc.charter_id)
    if order is None:
        reasons.append("category order not approved")
    elif why := special_not_last(cat, sc, order["categories"]):  # a tier confirmed after approval
        reasons.append(f"approved order v{order['version']} is out of date: {why}; propose and approve a new order")
    for a in _assets(cat, sc.charter_id):
        p = primary(cat, a["asset_uri"])
        if not p or p["state"] != "accepted":
            reasons.append(f"unassigned: {a['asset_uri']} has no accepted primary category"
                           + (f" (only suggested: {p['category_id']})" if p else ""))
    for r in gate_blockers(cat, sc.charter_id):
        miss = ", ".join(m["field"] for m in json.loads(r["missing_fields"]))
        reasons.append(f"incomplete: {r['asset_uri']} (missing: {miss})")
    if reasons:
        return None, reasons
    for cid in sc.categories:
        if step_status(cat, sc.charter_id, cid, "inventory")["status"] != "complete":
            _write_step(cat, sc, cid, "inventory", "complete", actor or "konmari-handoff")
    return order["categories"], []


# ---------------------------------------------------------------- step order per category

def step_status(cat: Catalog, charter_id: str, cid: str, step: str) -> dict:
    return cat.one("SELECT * FROM category_step WHERE charter_id = ? AND category_id = ? AND step = ?",
                   (charter_id, cid, step)) or {"status": "not_started", "waiver": None}


def _write_step(cat, sc, cid, step, status, actor, waiver=None) -> None:
    old = step_status(cat, sc.charter_id, cid, step)
    cat.execute("DELETE FROM category_step WHERE charter_id = ? AND category_id = ? AND step = ?",
                (sc.charter_id, cid, step))
    cat.execute("INSERT INTO category_step VALUES (?, ?, ?, ?, ?, ?, ?)",
                (sc.charter_id, cid, step, status, now(), actor, waiver or old["waiver"]))
    if waiver:
        audit_change(cat, "categories.step.waive", f"{cid}/{step}", json.loads(old["waiver"]) if old["waiver"] else None,
                     json.loads(waiver), actor=actor, reason=json.loads(waiver)["reason"], charter_id=sc.charter_id,
                     step=step)
    else:
        audit_change(cat, "categories.step", f"{cid}/{step}", old["status"], status, actor=actor,
                     charter_id=sc.charter_id, step=step)


def check_step(cat: Catalog, sc: Scope, cid: str, step: str) -> list[str]:
    if step not in STEPS:
        raise ValueError(f"unknown step '{step}'; valid steps: {', '.join(STEPS)}")
    if cid not in sc.categories:
        return [f"category '{cid}' is not in the scope of charter '{sc.charter_id}'"]
    i = STEPS.index(step)
    if i == 0:
        return []
    reasons = []
    prev = STEPS[i - 1]
    if step_status(cat, sc.charter_id, cid, prev)["status"] != "complete":
        reasons.append(f"step '{prev}' is not complete for category '{cid}'; finish it before '{step}'")
    order = approved_order(cat, sc.charter_id)
    if order is None:
        return reasons + ["category order not approved"]
    seq = order["categories"]
    # categories without accepted assets have nothing to process, so they never hold up later ones
    earlier = [c for c in seq[:seq.index(cid)] if step_status(cat, sc.charter_id, c, step)["status"] != "complete"
               and members(cat, sc, c, accepted_only=True)]
    if earlier and not step_status(cat, sc.charter_id, cid, step)["waiver"]:
        reasons.append(f"earlier categories in the approved order have not finished '{step}': {', '.join(earlier)}; "
                       f"an approver must record a waiver: konmari categories step {cid} {step} --waive --reason ...")
    return reasons


def set_step(cat: Catalog, sc: Scope, cid: str, step: str, status: str, actor: str) -> None:
    if status not in STEP_STATUS[1:]:
        raise Refused(f"invalid step status '{status}'; valid: in_progress, complete")
    reasons = check_step(cat, sc, cid, step)
    if reasons:
        raise StepRefused(reasons)
    _write_step(cat, sc, cid, step, status, actor)


def waive_step(cat: Catalog, sc: Scope, cid: str, step: str, actor: str, reason: str) -> None:
    _approver(sc, actor)
    if not (reason or "").strip():
        raise Refused("a waiver needs a reason (--reason)")
    if cid not in sc.categories or step not in STEPS:
        raise Refused(f"unknown category '{cid}' or step '{step}'")
    _write_step(cat, sc, cid, step, step_status(cat, sc.charter_id, cid, step)["status"], actor,
                json.dumps({"by": actor, "reason": reason, "at": now().isoformat()}))


def require_step(category: str, step: str, charter_path: str | Path = "charter.yaml",
                 catalog: Catalog | None = None) -> None:
    """Steps 03-09 call this before processing a category; raises StepRefused with every unmet condition."""
    cat = catalog or connect()
    reasons = check_step(cat, _scope(charter_path, cat), category, step)
    if reasons:
        raise StepRefused(reasons)
