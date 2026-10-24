"""Charter schema validation plus cross-field rules (RACI, roles, compliance, cadence, data scope)."""

from konmari.schema import placeholder_errors, schema_errors

ACTIVITIES = ["charter_approval", "metric_approval", "source_inclusion", "retention_discard_approval",
              "sot_conflict_resolution", "dpia_decision", "go_live_approval"]
REQUIRED_ROLES = ["sponsor", "use_case_owner", "data_owner", "compliance", "agent_user"]
DEFAULT_CATEGORIES = ["kb-and-sops", "customer-records", "policies-and-contracts", "financial-ops-metrics",
                      "support-history"]
STEPS = ["inventory", "score", "discard", "standardize", "enrich", "govern", "connect", "evaluate"]
REVIEWER_ROLES = {"data_owner", "data_steward", "agent_user"}

CUSTOM = {
    ("use_cases", "maxItems"): "only one use case per charter; a second use case needs its own charter",
    ("cadence.post_pilot_review", "required"):
        "missing; the engagement must not be a one-off, define a post-pilot review interval and owner",
}


def roles_by_id(charter: dict) -> dict[str, set[str]]:
    return {s["id"]: set(s.get("roles", [])) for s in charter.get("stakeholders", []) if isinstance(s, dict)}


def _name(charter: dict, sid: str) -> str:
    for s in charter.get("stakeholders", []):
        if s.get("id") == sid:
            return f"{s.get('name', sid)} ({sid})"
    return sid


def validate_charter(charter) -> list[str]:
    if not isinstance(charter, dict):
        return ["(root): charter must be a YAML mapping"]
    errors = schema_errors(charter, "charter", CUSTOM) + placeholder_errors(charter)
    try:
        errors += _cross_field(charter)
    except (TypeError, AttributeError, KeyError):
        pass  # shape errors are already reported by the schema
    return errors


def _cross_field(c: dict) -> list[str]:
    errs = []
    roles = roles_by_id(c)
    org = {sid for sid, r in roles.items() if r - {"facilitator"}}  # the organization's own staff

    def known(sid, where):
        if sid not in roles:
            errs.append(f"{where}: '{sid}' is not in the stakeholder register")
            return False
        return True

    present = set().union(*roles.values()) if roles else set()
    missing = [r for r in REQUIRED_ROLES if r not in present]
    if missing:
        errs.append(f"stakeholders: missing required role(s): {', '.join(missing)}")

    for act, row in (c.get("raci") or {}).items():
        for letter in "RACI":
            for sid in row.get(letter, []):
                known(sid, f"raci.{act}.{letter}")
        acc = row.get("A", [])
        if len(acc) != 1:
            errs.append(f"raci.{act}.A: exactly one Accountable required, found {len(acc)}"
                        + (f": {', '.join(_name(c, s) for s in acc)}" if acc else ""))
        if not row.get("R"):
            errs.append(f"raci.{act}.R: at least one Responsible required")
        for sid in acc:
            if sid in roles and sid not in org:
                errs.append(f"raci.{act}.A: {_name(c, sid)} holds only the facilitator role; "
                            "the organization's own staff must be Accountable")
        if act == "dpia_decision" and len(acc) == 1 and "compliance" not in roles.get(acc[0], set()):
            errs.append(f"raci.dpia_decision.A: {_name(c, acc[0])} must hold the compliance role")

    comp = c.get("compliance") or {}
    by = (comp.get("dpia_screening") or {}).get("decided_by")
    if by and known(by, "compliance.dpia_screening.decided_by") and "compliance" not in roles[by]:
        errs.append(f"compliance.dpia_screening.decided_by: {_name(c, by)} must hold the compliance role")

    cad = c.get("cadence") or {}
    att = (cad.get("steering") or {}).get("attendees", [])
    for sid in att:
        known(sid, "cadence.steering.attendees")
    for role in ("sponsor", "compliance"):
        if att and not any(role in roles.get(s, set()) for s in att):
            errs.append(f"cadence.steering.attendees: must include a {role}")
    owner = (cad.get("post_pilot_review") or {}).get("owner")
    if owner and known(owner, "cadence.post_pilot_review.owner") and owner not in org:
        errs.append("cadence.post_pilot_review.owner: must be an organization stakeholder, not the facilitator")
    if cad.get("kickoff") and cad.get("pilot_close") and cad["pilot_close"] < cad["kickoff"]:
        errs.append("cadence.pilot_close: must not be before the kickoff")

    scope = c.get("data_scope") or {}
    valid = DEFAULT_CATEGORIES + [e["id"] for e in scope.get("category_extensions", [])]
    for cat in scope.get("categories", []):
        cid = cat.get("id")
        if cid not in valid:
            errs.append(f"data_scope.categories: unknown category '{cid}'; valid IDs: {', '.join(valid)}")
        appr = cat.get("approver")
        if not appr or appr not in roles or "data_owner" not in roles[appr]:
            errs.append(f"data_scope.categories.{cid}: needs one approver holding the data_owner role"
                        + (f" ('{appr}' is not a data_owner)" if appr else ""))
        revs = cat.get("reviewers", [])
        if not revs:
            errs.append(f"data_scope.categories.{cid}: needs at least one reviewer")
        for r in revs:
            if known(r, f"data_scope.categories.{cid}.reviewers") and not roles[r] & REVIEWER_ROLES:
                errs.append(f"data_scope.categories.{cid}.reviewers: {_name(c, r)} must hold "
                            "data_owner, data_steward or agent_user")
    in_scope = {cat.get("id") for cat in scope.get("categories", [])}
    for src in scope.get("sources", []):
        for cid in src.get("categories", []):
            if cid not in in_scope:
                errs.append(f"data_scope.sources.{src.get('id')}: category '{cid}' is not in scope")
    return errs
