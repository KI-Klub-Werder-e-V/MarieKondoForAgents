"""Metric-set and golden-set validation against the charter's stakeholder register."""

from konmari.charter.validation import roles_by_id
from konmari.schema import placeholder_errors, schema_errors

MUST_HAVE_FAMILIES = ["answer_quality", "abstention", "business_outcome"]
ANY_FAMILIES = ["data_readiness", "governance"]
HUMAN_APPROVAL_METRIC = "human_approved_decisions"
GOLDEN_MIN_QUESTIONS, GOLDEN_MIN_ABSTENTION = 30, 5
GOLDEN_AUTHOR_ROLES = {"agent_user", "data_steward", "data_owner"}  # agent users and domain experts


def validate_metrics(metrics, charter: dict) -> list[str]:
    if not isinstance(metrics, dict):
        return ["(root): metrics file must be a YAML mapping"]
    errs = schema_errors(metrics, "metrics") + placeholder_errors(metrics)
    if metrics.get("charter_id") != charter.get("charter_id"):
        errs.append(f"charter_id: '{metrics.get('charter_id')}' does not match the charter "
                    f"'{charter.get('charter_id')}'")
    items = [m for m in metrics.get("metrics") or [] if isinstance(m, dict)]
    roles = roles_by_id(charter)
    seen = set()
    for m in items:
        mid = m.get("id", "?")
        if mid in seen:
            errs.append(f"metrics.{mid}: duplicate metric ID")
        seen.add(mid)
        owner = m.get("owner")
        if owner and owner not in roles:
            errs.append(f"metrics.{mid}.owner: '{owner}' is not in the stakeholder register")
    for fam in MUST_HAVE_FAMILIES:
        if not any(m.get("family") == fam and m.get("tier") == "must_have" for m in items):
            msg = f"metrics: no must_have metric in family '{fam}'"
            if fam == "business_outcome":
                msg += "; technical metrics alone do not show business value"
            errs.append(msg)
    for fam in ANY_FAMILIES:
        if not any(m.get("family") == fam for m in items):
            errs.append(f"metrics: no metric in family '{fam}'")
    ha = next((m for m in items if m.get("id") == HUMAN_APPROVAL_METRIC), None)
    full = 100 if ha and ha.get("unit") == "%" else 1.0
    if not ha or ha.get("family") != "governance":
        errs.append(f"metrics: governance metric '{HUMAN_APPROVAL_METRIC}' (share of human-approved discard and "
                    "retention decisions) is required")
    elif ha.get("direction") != ">=" or not isinstance(ha.get("target"), (int, float)) or ha["target"] < full:
        errs.append(f"metrics.{HUMAN_APPROVAL_METRIC}.target: must be 100% ({full} {ha.get('unit')}) with "
                    "direction '>='")
    return errs


def validate_golden_set(golden, charter: dict, min_questions=GOLDEN_MIN_QUESTIONS,
                        min_abstention=GOLDEN_MIN_ABSTENTION) -> list[str]:
    if not isinstance(golden, dict):
        return ["golden_set: file must be a YAML mapping"]
    errs = [f"golden_set.{e}" for e in schema_errors(golden, "golden-set") + placeholder_errors(golden)]
    qs = [q for q in golden.get("questions") or [] if isinstance(q, dict)]
    min_questions, min_abstention = max(min_questions, GOLDEN_MIN_QUESTIONS), max(min_abstention, GOLDEN_MIN_ABSTENTION)
    if len(qs) < min_questions:
        errs.append(f"golden_set: {len(qs)} questions, at least {min_questions} are required")
    neg = sum(q.get("expected_behaviour") in ("abstain", "flag_conflict") for q in qs)
    if neg < min_abstention:
        errs.append(f"golden_set: {neg} abstention questions (abstain or flag_conflict), at least {min_abstention} "
                    "abstention questions are required")
    roles = roles_by_id(charter)
    bad = [q.get("id", "?") for q in qs if not roles.get(q.get("author"), set()) & GOLDEN_AUTHOR_ROLES]
    if bad and len(bad) == len(qs):
        errs.append("golden_set: questions must come from agent users or domain experts named in the stakeholder "
                    "register; none of the authors qualifies")
    elif bad:
        errs.append(f"golden_set: questions {', '.join(bad)}: author is not an agent user or domain expert in the "
                    "stakeholder register")
    # shortcut: "DPIA covers it" is read as dpia_screening.result == required; refine if pilots need finer rules
    dpia = ((charter.get("compliance") or {}).get("dpia_screening") or {}).get("result")
    pd = [q.get("id", "?") for q in qs if q.get("contains_personal_data")]
    if pd and dpia != "required":
        errs.append(f"golden_set: questions {', '.join(pd)} contain personal data but the DPIA screening does not "
                    "cover it; paraphrase them without personal data")
    return errs
