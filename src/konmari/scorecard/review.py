"""Human review of proposed verdicts: accept, override, reverse, bulk accept. Append-only and audited;
nothing here touches source data."""

from konmari.catalog import Catalog, audit
from konmari.categories import store as cats
from konmari.charter.store import Scope
from konmari.scorecard.engine import STEP, add_review, latest_verdict, reviews, split_scope
from konmari.versions import Refused

VERDICTS = ("keep", "fix", "discard")


def status(cat: Catalog, uri: str) -> dict:
    """{proposed, status (proposed|approved|None), effective (latest approved verdict or None), verdict}."""
    v = latest_verdict(cat, uri)
    hist = reviews(cat, uri)
    approved = [r for r in hist if r["action"] != "propose"]
    return {"verdict": v, "proposed": v["proposed"] if v else None,
            "status": None if not hist else "proposed" if hist[-1]["action"] == "propose" else "approved",
            "effective": approved[-1]["to_verdict"] if approved else None}


def effective_verdict(cat: Catalog, uri: str) -> str | None:
    """What downstream consumers (c04 retention-decisions, c08 data-products) act on: the latest approved verdict."""
    return status(cat, uri)["effective"]


def _authorise(cat: Catalog, sc: Scope, uri: str, actor: str, action: str) -> dict:
    st = status(cat, uri)
    if st["verdict"] is None:
        raise Refused(f"'{uri}' has no proposed verdict; run 'konmari score' first")
    category = st["verdict"]["category_id"]
    allowed = set(sc.reviewers(category)) | {sc.approver(category)} if category in sc.categories else set()
    if actor not in allowed:
        audit(cat, f"scorecard.review.{action}", actor=actor, charter_id=sc.charter_id,
              charter_version=sc.charter_version, step=STEP, result="denied",
              details={"entity": uri, "reason": f"not a reviewer of category '{category}' in the pilot charter"})
        raise Refused(f"'{actor}' is not a reviewer of category '{category}' in charter '{sc.charter_id}' "
                      f"(data_scope.categories); the attempt was logged")
    return st


def _record(cat, sc, uri, st, action, actor, to, justification=None) -> dict:
    v = st["verdict"]
    frm = st["proposed"] if st["status"] == "proposed" else st["effective"]
    row = add_review(cat, uri, v["verdict_id"], action, actor, frm, to, justification, v["rubric_version"])
    audit(cat, f"scorecard.review.{action}", actor=actor, charter_id=sc.charter_id, charter_version=sc.charter_version,
          step=STEP, result="approved",
          details={"entity": uri, "actor": actor, "from": frm, "to": to, "justification": justification,
                   "rubric_version": v["rubric_version"], "verdict_id": v["verdict_id"]})
    _maybe_complete(cat, sc, v["category_id"], actor)
    return row


def _maybe_complete(cat: Catalog, sc: Scope, category: str, actor: str) -> None:
    """The `score` step of a category is complete once every in-scope asset has an approved verdict."""
    assets = split_scope(cat, sc)[0].get(category, [])
    if assets and all(status(cat, a["asset_uri"])["status"] == "approved" for a in assets) and \
            cats.step_status(cat, sc.charter_id, category, STEP)["status"] != "complete":
        cats.set_step(cat, sc, category, STEP, "complete", actor)


def accept(cat: Catalog, sc: Scope, uri: str, actor: str) -> dict:
    st = _authorise(cat, sc, uri, actor, "accept")
    if st["status"] != "proposed":
        raise Refused(f"'{uri}' has no open proposal (status {st['status']}); use 'review reverse' to change it")
    return _record(cat, sc, uri, st, "accept", actor, st["proposed"])


def _justified(justification) -> str:
    if not (justification or "").strip():
        raise Refused("a justification is required (--justification)")
    return justification.strip()


def override(cat: Catalog, sc: Scope, uri: str, to: str, justification: str | None, actor: str) -> dict:
    st = _authorise(cat, sc, uri, actor, "override")
    if to not in VERDICTS:
        raise Refused(f"invalid verdict '{to}'; valid: {', '.join(VERDICTS)}")
    if st["status"] != "proposed":
        raise Refused(f"'{uri}' has no open proposal (status {st['status']}); use 'review reverse' to change it")
    if to == st["proposed"]:
        raise Refused(f"'{to}' is the proposed verdict; use 'review accept'")
    return _record(cat, sc, uri, st, "override", actor, to, _justified(justification))


def reverse(cat: Catalog, sc: Scope, uri: str, to: str, justification: str | None, actor: str) -> dict:
    """Replace an approved verdict by a new review entry; earlier entries stay in the history."""
    st = _authorise(cat, sc, uri, actor, "reverse")
    if to not in VERDICTS:
        raise Refused(f"invalid verdict '{to}'; valid: {', '.join(VERDICTS)}")
    if st["status"] != "approved":
        raise Refused(f"'{uri}' has no approved verdict to reverse; accept or override the proposal")
    if to == st["effective"]:
        raise Refused(f"'{to}' is already the approved verdict")
    return _record(cat, sc, uri, st, "reverse", actor, to, _justified(justification))


def bulk_accept(cat: Catalog, sc: Scope, actor: str, category: str | None = None) -> tuple[list[str], list[str]]:
    """Accept every open `keep` proposal without unknown criteria; anything else needs a one-by-one review."""
    done, skipped = [], []
    for cid, assets in sorted(split_scope(cat, sc)[0].items()):
        for a in assets if category in (None, cid) else []:
            st = status(cat, a["asset_uri"])
            if st["status"] != "proposed":
                continue
            if st["proposed"] == "keep" and not st["verdict"]["has_unknown"]:
                accept(cat, sc, a["asset_uri"], actor)
                done.append(a["asset_uri"])
            else:
                skipped.append(a["asset_uri"])
    return done, skipped
