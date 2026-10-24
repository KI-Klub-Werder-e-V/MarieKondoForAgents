"""Retention rules per category (data, approved by compliance), legal holds, expiry dates and the scheduled
expiry check. The kit ships no authoritative periods (design D8)."""

import calendar
import re
from datetime import date, datetime, timedelta

from konmari.catalog import Catalog, new_id, now
from konmari.categories import store as cats
from konmari.charter.store import Scope
from konmari.discard import corpus
from konmari.discard.events import log
from konmari.versions import Refused

START_EVENTS = ("last-entry", "end-of-calendar-year-of-last-entry")
EXPIRY_ACTIONS = ("erase", "review")
WILDCARDS = {"*", "all", "alle", "any", "default"}
_PERIOD = re.compile(r"^P(?!$)(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)D)?$")


def parse_period(p: str) -> tuple[int, int, int]:
    m = _PERIOD.match(p or "")
    if not m:
        raise Refused(f"period '{p}' must be an ISO 8601 duration in years, months and days, e.g. P8Y or P6M")
    return tuple(int(x or 0) for x in m.groups())


def add_period(d: date, period: str) -> date:
    y, m, days = parse_period(period)
    months = d.month - 1 + m
    year, month = d.year + y + months // 12, months % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1])) + timedelta(days=days)


def compliance_ids(sc: Scope) -> set[str]:
    return {sid for sid, s in sc.stakeholders.items() if "compliance" in s.get("roles", [])}


def add_rule(cat: Catalog, sc: Scope, category: str, purpose: str, legal_basis: str, source: str, start_event: str,
             period: str, expiry_action: str, actor: str) -> dict:
    if actor not in sc.stakeholders:
        raise Refused(f"'{actor}' is not in the stakeholder register")
    if not category or category.lower() in WILDCARDS or "*" in category:
        raise Refused("one retention period for all categories is not allowed; register one rule per category")
    if category not in {c["category_id"] for c in cats.categories(cat, sc)}:
        raise Refused(f"category '{category}' is not in the category map of charter '{sc.charter_id}'")
    if start_event not in START_EVENTS:
        raise Refused(f"start event must be one of: {', '.join(START_EVENTS)}")
    if expiry_action not in EXPIRY_ACTIONS:
        raise Refused(f"action at expiry must be one of: {', '.join(EXPIRY_ACTIONS)}")
    parse_period(period)
    for k, v in {"purpose": purpose, "legal basis": legal_basis, "source": source}.items():
        if not (v or "").strip():
            raise Refused(f"a retention rule needs a {k}")
    row = {"rule_id": new_id()[:12], "charter_id": sc.charter_id, "category_id": category, "purpose": purpose,
           "legal_basis": legal_basis, "source": source, "start_event": start_event, "period": period,
           "expiry_action": expiry_action, "status": "proposed", "created_by": actor, "created_at": now(),
           "approver": None, "approved_at": None}
    cat.execute("INSERT INTO retention_rule VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row.values()))
    log(cat, sc, "retention.rule.add", None, None, "proposed", actor, rule_id=row["rule_id"], category=category,
        source=source, period=period)
    return row


def approve_rule(cat: Catalog, sc: Scope, rule_id: str, actor: str) -> dict:
    rule = cat.one("SELECT * FROM retention_rule WHERE rule_id = ? AND charter_id = ?", (rule_id, sc.charter_id))
    if rule is None:
        raise Refused(f"unknown retention rule '{rule_id}'")
    if actor not in compliance_ids(sc):
        log(cat, sc, "retention.rule.approve", None, None, None, actor, result="denied", rule_id=rule_id)
        raise Refused(f"'{actor}' does not hold the compliance role; data protection or compliance approves "
                      "retention rules (the attempt was logged)")
    cat.execute("UPDATE retention_rule SET status = 'superseded' WHERE charter_id = ? AND category_id = ? AND"
                " status = 'approved'", (sc.charter_id, rule["category_id"]))
    cat.execute("UPDATE retention_rule SET status = 'approved', approver = ?, approved_at = ? WHERE rule_id = ?",
                (actor, now(), rule_id))
    log(cat, sc, "retention.rule.approve", None, "proposed", "approved", actor, rule_id=rule_id,
        category=rule["category_id"])
    return cat.one("SELECT * FROM retention_rule WHERE rule_id = ?", (rule_id,))


def rules(cat: Catalog, charter_id: str) -> list[dict]:
    return cat.query("SELECT * FROM retention_rule WHERE charter_id = ? ORDER BY category_id, created_at",
                     (charter_id,))


def approved_rule(cat: Catalog, charter_id: str, category: str | None) -> dict | None:
    return cat.one("SELECT * FROM retention_rule WHERE charter_id = ? AND category_id = ? AND status = 'approved'",
                   (charter_id, category)) if category else None


def expiry_date(rule: dict, item: dict) -> date | None:
    """Retention end from the rule's start event and the item's last-entry date (source metadata)."""
    last = corpus.meta(item).get("last_entry")
    if not last:
        return None
    d = date.fromisoformat(str(last)[:10])
    start = date(d.year, 12, 31) if rule["start_event"] == "end-of-calendar-year-of-last-entry" else d
    return add_period(start, rule["period"])


def add_hold(cat: Catalog, sc: Scope, reason: str, owner: str, review_date: str, actor: str, item_id=None,
             category=None) -> dict:
    if bool(item_id) == bool(category):
        raise Refused("a legal hold covers either one item or one category")
    if not (reason or "").strip():
        raise Refused("a legal hold needs a reason")
    if owner not in sc.stakeholders or actor not in sc.stakeholders:
        raise Refused("hold owner and actor must be in the stakeholder register")
    if item_id:
        corpus.require(cat, item_id)
    row = {"hold_id": new_id()[:12], "charter_id": sc.charter_id, "item_id": item_id, "category_id": category,
           "reason": reason, "owner": owner, "review_date": date.fromisoformat(review_date), "created_at": now(),
           "released_at": None}
    cat.execute("INSERT INTO legal_hold VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(row.values()))
    log(cat, sc, "retention.hold.add", item_id, None, None, actor, hold_id=row["hold_id"], category=category,
        reason=reason, owner=owner, review_date=review_date)
    return row


def release_hold(cat: Catalog, sc: Scope, hold_id: str, actor: str) -> None:
    h = cat.one("SELECT * FROM legal_hold WHERE hold_id = ? AND released_at IS NULL", (hold_id,))
    if h is None:
        raise Refused(f"no active legal hold '{hold_id}'")
    if actor not in {h["owner"]} | compliance_ids(sc):
        raise Refused("only the hold owner or compliance may release a legal hold")
    cat.execute("UPDATE legal_hold SET released_at = ? WHERE hold_id = ?", (now(), hold_id))
    log(cat, sc, "retention.hold.release", h["item_id"], None, None, actor, hold_id=hold_id)


def active_holds(cat: Catalog, charter_id: str, item_id: str, category: str | None) -> list[dict]:
    return cat.query("SELECT * FROM legal_hold WHERE charter_id = ? AND released_at IS NULL AND (item_id = ? OR"
                     " category_id = ?)", (charter_id, item_id, category))


def check(cat: Catalog, sc: Scope, actor: str, window_days: int = 30, schedule: str = "P7D",
          today: date | None = None) -> list[dict]:
    """Proposals for active, archived and restricted items whose retention ends within the window."""
    from konmari.discard.decisions import create_proposal, open_proposal

    parse_period(schedule)
    today = today or date.today()
    run_id, started = new_id(), now()
    cat.execute("INSERT INTO retention_check_run VALUES (?, ?, ?, NULL, ?, ?, NULL)",
                (run_id, sc.charter_id, started, window_days, schedule))
    out = []
    for it in cat.query("SELECT * FROM corpus_item WHERE charter_id = ? AND state IN ('active', 'archived',"
                        " 'restricted') ORDER BY item_id", (sc.charter_id,)):
        it["meta"] = corpus.meta(it)
        rule = approved_rule(cat, sc.charter_id, corpus.category(cat, it))
        end = expiry_date(rule, it) if rule else None
        if end is None or end > today + timedelta(days=window_days) or open_proposal(cat, it["item_id"]):
            continue
        action = "erase" if rule["expiry_action"] == "erase" else "keep"
        what = "erase" if action == "erase" else "review (keep or change the action)"
        out.append(create_proposal(cat, sc, it, action, "retention", [], actor,
                                   f"retention period of rule {rule['rule_id']} ({rule['source']}) ends {end}: {what}"))
    cat.execute("UPDATE retention_check_run SET finished_at = ?, proposals = ? WHERE run_id = ?",
                (now(), len(out), run_id))
    log(cat, sc, "retention.check", None, None, None, actor, run_id=run_id, proposals=len(out),
        window_days=window_days, schedule=schedule)
    return out


def overdue(cat: Catalog, charter_id: str, at: datetime | None = None) -> str | None:
    last = cat.one("SELECT * FROM retention_check_run WHERE charter_id = ? AND finished_at IS NOT NULL"
                   " ORDER BY finished_at DESC LIMIT 1", (charter_id,))
    if last is None:
        return "retention check has never run; schedule 'konmari retention check'" if cat.one(
            "SELECT 1 AS x FROM retention_rule WHERE charter_id = ? AND status = 'approved'", (charter_id,)) else None
    due = datetime.combine(add_period(last["finished_at"].date(), last["schedule"]), last["finished_at"].time())
    if (at or now()) > due:
        return f"retention check is overdue: last run {last['finished_at']:%Y-%m-%d %H:%M}, schedule {last['schedule']}"
    return None
