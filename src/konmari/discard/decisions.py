"""Discard proposals, human approval, execution (archive, exclude, route-to-masking, erase), restore and
propagation checks. Nothing changes before an authorized person approves; source systems are never modified."""

import json
from datetime import date, datetime, timedelta

from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.discard import calibrate, corpus, retention
from konmari.discard.archive import Archive
from konmari.discard.detect import DEFAULTS, DETECTORS, category_waiting
from konmari.discard.events import lineage_event, log
from konmari.inventory.store import approvers
from konmari.scorecard import engine as se
from konmari.scorecard.review import effective_verdict
from konmari.versions import Refused

ACTIONS = ("keep", "archive", "exclude", "route-to-masking", "erase")
STRENGTH = {"keep": 0, "route-to-masking": 1, "archive": 2, "exclude": 3, "erase": 4}
NEW_STATE = {"archive": "archived", "exclude": "excluded", "route-to-masking": "awaiting-masking"}
OPEN = ("pending", "approved", "scheduled")


def cap(action: str, methods) -> str:
    """Semantic evidence alone never yields exclude or erase (design D3)."""
    methods = set(methods)
    return "archive" if methods and methods <= {"semantic"} and STRENGTH[action] > STRENGTH["archive"] else action


def open_proposal(cat: Catalog, item_id: str) -> dict | None:
    return cat.one(f"SELECT * FROM proposal WHERE item_id = ? AND status IN {OPEN} ORDER BY created_at DESC LIMIT 1",
                   (item_id,))


def get(cat: Catalog, proposal_id: str) -> dict:
    p = cat.one("SELECT * FROM proposal WHERE proposal_id = ?", (proposal_id,))
    if p is None:
        raise Refused(f"unknown proposal '{proposal_id}'")
    return p


def create_proposal(cat: Catalog, sc: Scope, item: dict, action: str, method: str, flag_ids: list[str], actor: str,
                    reason: str, run_id: str | None = None) -> dict:
    if action not in ACTIONS:
        raise Refused(f"unknown action '{action}'; valid: {', '.join(ACTIONS)}")
    row = {"proposal_id": new_id()[:12], "charter_id": sc.charter_id, "run_id": run_id, "item_id": item["item_id"],
           "category_id": corpus.category(cat, item), "action": action, "method": method,
           "flag_ids": json.dumps(flag_ids), "reason": reason, "proposed_by": actor, "created_at": now(),
           "status": "pending"}
    cols = ", ".join(row)
    cat.execute(f"INSERT INTO proposal ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
    log(cat, sc, "propose", item["item_id"], item["state"], item["state"], actor, proposal_id=row["proposal_id"],
        action=action, method=method, flag_ids=flag_ids)
    return get(cat, row["proposal_id"])


def propose(cat: Catalog, sc: Scope, actor: str) -> dict:
    """Proposals from the latest run's flags, reviewer flags and approved scorecard 'discard' verdicts."""
    run = calibrate.latest_run(cat, sc.charter_id)
    if run is None:
        raise Refused("no detection run yet; run 'konmari discard detect' first")
    flags = cat.query("SELECT * FROM flag WHERE run_id = ? OR (run_id IS NULL AND item_id IN (SELECT item_id FROM"
                      " corpus_item WHERE charter_id = ?)) ORDER BY created_at, flag_id",
                      (run["run_id"], sc.charter_id))
    clusters = {c["cluster_id"]: c for c in cat.query("SELECT * FROM cluster WHERE run_id = ?", (run["run_id"],))}
    calib = {d: calibrate.status(cat, sc.charter_id, d) for d in DETECTORS}
    blocked = {d: 0 for d in DETECTORS if calib[d] is None}
    items = {i["item_id"]: corpus.get(cat, i["item_id"]) for i in cat.query(
        "SELECT item_id FROM corpus_item WHERE charter_id = ? AND state = 'active'", (sc.charter_id,))}
    cand = {}  # item_id -> [(action, method, flag_id, reason)]

    def add(iid, action, method, fid, reason):
        if iid in items:
            cand.setdefault(iid, []).append((action, method, fid, reason))

    title = lambda iid: (items.get(iid) or corpus.get(cat, iid) or {"title": iid})["title"]  # noqa: E731
    for f in flags:
        iid, m = f["item_id"], f["method"]
        if m in DETECTORS:
            if calib[m] is None:
                blocked[m] += 1
                continue
            if calib[m]["threshold"] is not None and (f["score"] or 0) < calib[m]["threshold"]:
                continue
        if f["flag_type"] == "duplicate":
            c = clusters.get(f["cluster_id"])
            canon = c["canonical_item"] if c else json.loads(f["related_ids"])[0]
            others = [iid, *json.loads(f["related_ids"])]
            for x in others:  # the non-canonical member(s) of the pair
                if x == canon:
                    continue
                what = {"exact": "exact duplicate of", "minhash": f"near-duplicate (Jaccard {f['score']:.2f}) of",
                        "containment": f"contained (containment {f['score']:.2f}) in",
                        "semantic": f"semantically similar (cosine {f['score']:.2f}) to"}[m]
                why = f"{what} {title(canon)}" + (f"; canonical by {c['canonical_reason']}" if c else "")
                add(x, "archive", m, f["flag_id"], why)
        elif f["flag_type"] == "superseded":
            sig = json.loads(f["signals"])["signals"]
            newer = ", ".join(title(r) for r in json.loads(f["related_ids"]))
            add(iid, "archive", "superseded", f["flag_id"], f"superseded by {newer} ({', '.join(sig)})")
        elif f["flag_type"] == "off-scope":
            why = json.loads(f["params"]).get("category") or "marked by a reviewer"
            add(iid, "exclude", "off-scope", f["flag_id"], f"not needed for the pilot: {why} is not in the charter"
                if f["method"] == "category" else f"marked off-scope by {f['created_by']}: {why}")
        elif f["flag_type"] == "sensitive-review":
            s = json.loads(f["signals"])
            found = ", ".join(f"{k} x{v}" for k, v in s["entities"].items())
            add(iid, "route-to-masking", f["method"], f["flag_id"],
                f"personal or confidential content ({found or 'inventory tier ' + str(s['tier'])}); "
                "mask before agents may read it")
    for iid, it in items.items():
        if effective_verdict(cat, it["asset_uri"]) == "discard":
            v = se.latest_verdict(cat, it["asset_uri"])
            add(iid, "exclude" if v and v["reason"] == "irrelevant" else "archive", "scorecard", None,
                f"scorecard verdict discard for asset {it['asset_uri']} ({v['reason'] if v else 'approved'})")
    created, waiting = [], {}
    for iid, cs in sorted(cand.items()):
        if open_proposal(cat, iid):
            continue
        c = corpus.category(cat, items[iid])
        if why := category_waiting(cat, sc, c):
            waiting[c] = why
            continue
        best = max(cs, key=lambda c: STRENGTH[c[0]])
        support = [c for c in cs if c[0] == best[0]]
        action = cap(best[0], {c[1] for c in support})
        created.append(create_proposal(cat, sc, items[iid], action, best[1], [c[2] for c in support if c[2]], actor,
                                       "; ".join(dict.fromkeys(c[3] for c in support)), run["run_id"]))
    return {"created": created, "blocked": {d: n for d, n in blocked.items() if n}, "waiting": waiting}


def list_proposals(cat: Catalog, charter_id: str, status: str | None = None) -> list[dict]:
    return cat.query("SELECT * FROM proposal WHERE charter_id = ?" + (" AND status = ?" if status else "")
                     + " ORDER BY created_at, proposal_id", (charter_id, status) if status else (charter_id,))


def _approvers_for(sc: Scope, category: str | None) -> set[str]:
    # off-scope items have no category approver in the charter: any named category approver decides
    return {sc.approver(category)} if category in sc.categories else approvers(sc)


def _authorise(cat: Catalog, sc: Scope, p: dict, actor: str, what: str) -> None:
    if actor == p["proposed_by"]:
        log(cat, sc, what, p["item_id"], None, None, actor, result="denied", proposal_id=p["proposal_id"],
            reason="proposer may not approve")
        raise Refused(f"'{actor}' created proposal {p['proposal_id']} and may not decide it; another approver must")
    if actor not in _approvers_for(sc, p["category_id"]):
        log(cat, sc, what, p["item_id"], None, None, actor, result="denied", proposal_id=p["proposal_id"],
            reason=f"not the approver for category {p['category_id']}")
        raise Refused(f"authorization error: '{actor}' is not the approver for category '{p['category_id']}' in "
                      f"charter '{sc.charter_id}'; the attempt was logged")


def decide(cat: Catalog, sc: Scope, proposal_id: str, actor: str, verdict: str = "approve", action: str | None = None,
           comment: str | None = None, compliance: bool = False, cfg: dict | None = None) -> dict:
    """Approve (optionally with a changed action) or reject. Erase needs the category approver and, separately,
    compliance; the second approval schedules it after the waiting period."""
    cfg = DEFAULTS | (cfg or {})
    p = get(cat, proposal_id)
    item = corpus.get(cat, p["item_id"])
    state = item["state"] if item else None
    if compliance:
        final = p["decided_action"] or p["action"]
        if actor not in retention.compliance_ids(sc):
            log(cat, sc, "approve.compliance", p["item_id"], state, state, actor, result="denied",
                proposal_id=proposal_id)
            raise Refused(f"authorization error: '{actor}' does not hold the compliance role; the attempt was logged")
        if final != "erase" or p["status"] not in ("pending", "approved"):
            raise Refused("a compliance approval is only taken for an open erase proposal")
        if actor in (p["proposed_by"], p["decided_by"]):
            raise Refused("erasure needs two different people: the category approver and compliance")
        cat.execute("UPDATE proposal SET compliance_by = ?, compliance_at = ? WHERE proposal_id = ?",
                    (actor, now(), proposal_id))
        log(cat, sc, "approve.compliance", p["item_id"], state, state, actor, proposal_id=proposal_id,
            comment=comment)
        return _maybe_schedule(cat, sc, get(cat, proposal_id), actor, cfg)
    if p["status"] != "pending":
        raise Refused(f"proposal {proposal_id} is {p['status']}, not pending")
    _authorise(cat, sc, p, actor, "approve" if verdict == "approve" else "reject")
    if action and action not in ACTIONS:
        raise Refused(f"unknown action '{action}'; valid: {', '.join(ACTIONS)}")
    final = action or p["action"]
    if verdict == "reject":
        cat.execute("UPDATE proposal SET status = 'rejected', decided_by = ?, decided_at = ?, comment = ? WHERE"
                    " proposal_id = ?", (actor, now(), comment, proposal_id))
        log(cat, sc, "reject", p["item_id"], state, state, actor, proposal_id=proposal_id, action=p["action"],
            comment=comment)
        return get(cat, proposal_id)
    if final == "erase" and p["compliance_by"] == actor:
        raise Refused("erasure needs two different people: the category approver and compliance")
    cat.execute("UPDATE proposal SET status = 'approved', decided_action = ?, decided_by = ?, decided_at = ?,"
                " comment = ? WHERE proposal_id = ?", (final, actor, now(), comment, proposal_id))
    log(cat, sc, "approve", p["item_id"], state, state, actor, proposal_id=proposal_id, proposed_action=p["action"],
        action=final, comment=comment)
    return _maybe_schedule(cat, sc, get(cat, proposal_id), actor, cfg)


def _maybe_schedule(cat, sc, p, actor, cfg) -> dict:
    if (p["decided_action"] or p["action"]) != "erase" or not (p["decided_by"] and p["compliance_by"]):
        return p
    after = now() + timedelta(days=cfg["erase_wait_days"])
    cat.execute("UPDATE proposal SET status = 'scheduled', execute_after = ? WHERE proposal_id = ?",
                (after, p["proposal_id"]))
    log(cat, sc, "schedule", p["item_id"], None, None, actor, proposal_id=p["proposal_id"],
        execute_after=after.isoformat(), wait_days=cfg["erase_wait_days"])
    return get(cat, p["proposal_id"])


def cancel(cat: Catalog, sc: Scope, proposal_id: str, actor: str, comment: str | None = None) -> dict:
    p = get(cat, proposal_id)
    if p["status"] not in OPEN:
        raise Refused(f"proposal {proposal_id} is {p['status']}; only open proposals can be cancelled")
    if actor not in _approvers_for(sc, p["category_id"]) | retention.compliance_ids(sc):
        raise Refused(f"'{actor}' may not cancel proposal {proposal_id}")
    cat.execute("UPDATE proposal SET status = 'cancelled', comment = ? WHERE proposal_id = ?", (comment, proposal_id))
    log(cat, sc, "cancel", p["item_id"], None, None, actor, proposal_id=proposal_id, comment=comment)
    return get(cat, proposal_id)


def bulk_preview(cat: Catalog, sc: Scope, action: str, category: str, method: str) -> list[dict]:
    if action == "erase":
        raise Refused("erase proposals are approved one by one")
    return [p for p in list_proposals(cat, sc.charter_id, "pending")
            if (p["action"], p["category_id"], p["method"]) == (action, category, method)]


def bulk_approve(cat: Catalog, sc: Scope, action: str, category: str, method: str, actor: str,
                 confirm: bool = False, comment: str | None = None) -> list[str]:
    """Without confirm only lists the affected item IDs; with confirm records one decision per proposal."""
    ps = bulk_preview(cat, sc, action, category, method)
    if confirm:
        for p in ps:
            decide(cat, sc, p["proposal_id"], actor, comment=comment or f"bulk approval {action}/{category}/{method}")
    return [p["item_id"] for p in ps]


# ---------------------------------------------------------------- execution

def _leave_active(cat, sc, item, new_state, p, actor, transport, index, **extra) -> None:
    arts = [a["artifact_id"] for a in cat.query("SELECT artifact_id FROM derived_artifact WHERE item_id = ? AND"
                                                " removed_at IS NULL", (item["item_id"],))]
    cat.execute("UPDATE derived_artifact SET marked_for_removal_at = ? WHERE item_id = ? AND removed_at IS NULL",
                (now(), item["item_id"]))
    if index is not None and arts:
        index.delete(arts)
    if new_state != "erased":
        cat.execute("UPDATE corpus_item SET state = ?, state_changed_at = ?, restricted_until = ? WHERE item_id = ?",
                    (new_state, now(), extra.get("restricted_until"), item["item_id"]))
    if transport is not None:
        transport.emit(lineage_event(item, new_state, p and p["proposal_id"], arts))
    log(cat, sc, "execute", item["item_id"], item["state"], new_state, actor, proposal_id=p and p["proposal_id"],
        action=p and (p["decided_action"] or p["action"]), derived_artifacts_marked=arts,
        **{k: str(v) for k, v in extra.items()})


def _done(cat, p) -> None:
    cat.execute("UPDATE proposal SET status = 'executed', executed_at = ? WHERE proposal_id = ?",
                (now(), p["proposal_id"]))


def execute(cat: Catalog, sc: Scope, actor: str, archive: Archive, proposal_id: str | None = None, transport=None,
            index=None, today: date | None = None, at: datetime | None = None) -> list[dict]:
    """Run approved decisions (and erasures whose waiting period is over). Returns one result per proposal."""
    at, today = at or now(), today or date.today()
    ps = [get(cat, proposal_id)] if proposal_id else [
        p for p in list_proposals(cat, sc.charter_id) if p["status"] in ("approved", "scheduled")]
    out = []
    for p in ps:
        action = p["decided_action"] or p["action"]
        item = corpus.get(cat, p["item_id"])
        res = {"proposal_id": p["proposal_id"], "item_id": p["item_id"], "action": action}
        if action == "erase":
            out.append(res | _erase(cat, sc, p, item, actor, archive, transport, index, today, at))
            continue
        if p["status"] != "approved":
            out.append(res | {"result": "refused", "message": f"proposal is {p['status']}, not approved"})
            continue
        if item is None or item["state"] != "active":
            out.append(res | {"result": "refused", "message": f"item is {item['state'] if item else 'erased'}"})
            continue
        if action == "keep":
            log(cat, sc, "execute", item["item_id"], "active", "active", actor, proposal_id=p["proposal_id"],
                action="keep")
        elif action == "archive":
            data = corpus.read_bytes(item)
            key, digest = corpus.sha256(item["item_id"].encode()), corpus.sha256(data)
            archive.put(key, data)
            if corpus.sha256(archive.get(key)) != digest:
                archive.delete(key)
                log(cat, sc, "execute", item["item_id"], "active", "active", actor, result="failed",
                    proposal_id=p["proposal_id"], action="archive", reason="archive checksum mismatch")
                out.append(res | {"result": "failed", "message": "archive checksum mismatch; item stays active"})
                continue
            rule = retention.approved_rule(cat, sc.charter_id, p["category_id"])
            cat.execute("INSERT INTO archive_manifest VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (item["item_id"], p["proposal_id"], item["content_ref"], key, digest,
                         rule and rule["rule_id"], now()))
            _leave_active(cat, sc, item, "archived", p, actor, transport, index, sha256=digest)
        else:
            _leave_active(cat, sc, item, NEW_STATE[action], p, actor, transport, index)
        _done(cat, p)
        out.append(res | {"result": "executed"})
    return out


def _erase(cat, sc, p, item, actor, archive, transport, index, today, at) -> dict:
    def refuse(msg, **kw):
        log(cat, sc, "execute", p["item_id"], item and item["state"], item and item["state"], actor, result="refused",
            proposal_id=p["proposal_id"], action="erase", reason=msg, **kw)
        return {"result": "refused", "message": msg}

    rule = retention.approved_rule(cat, sc.charter_id, p["category_id"])
    if rule is None:
        return refuse(f"retention rule missing for category '{p['category_id']}'; erasure needs an approved rule")
    if p["status"] != "scheduled":
        return {"result": "refused", "message": "erasure needs the category approver and compliance; not scheduled"}
    if at < p["execute_after"]:
        return {"result": "waiting", "message": f"waiting period runs until {p['execute_after']:%Y-%m-%d %H:%M}"}
    if item is None:
        return refuse("item already erased")
    if holds := retention.active_holds(cat, sc.charter_id, item["item_id"], p["category_id"]):
        return refuse("legal hold: " + "; ".join(f"{h['reason']} (owner {h['owner']}, review {h['review_date']})"
                                                 for h in holds), holds=[h["hold_id"] for h in holds])
    end = retention.expiry_date(rule, item)
    if end and end > today:  # Art. 17(3)(b) DSGVO, §35(3) BDSG: restrict instead of erasing
        if item["state"] != "restricted":
            _leave_active(cat, sc, item, "restricted", p, actor, transport, index, restricted_until=end,
                          rule_id=rule["rule_id"])
        _done(cat, p)
        return {"result": "restricted", "message": f"retention duty under rule {rule['rule_id']} until {end}"}
    m = cat.one("SELECT * FROM archive_manifest WHERE item_id = ?", (item["item_id"],))
    if m:
        archive.delete(m["object_key"])
    _leave_active(cat, sc, item, "erased", p, actor, transport, index, rule_id=rule["rule_id"])
    owner = (cat.one("SELECT owner FROM source_asset WHERE asset_uri = ?", (item["asset_uri"],)) or {}).get("owner")
    iid = item["item_id"]
    for sql in ("DELETE FROM archive_manifest WHERE item_id = ?", "DELETE FROM fingerprint WHERE item_id = ?",
                "DELETE FROM flag WHERE item_id = ?", "DELETE FROM candidate_pair WHERE item_a = ? OR item_b = ?",
                "DELETE FROM calibration_label WHERE item_a = ? OR item_b = ?",
                "DELETE FROM corpus_item WHERE item_id = ?"):
        cat.execute(sql, (iid,) * sql.count("?"))
    cat.execute("INSERT INTO erasure_record VALUES (?, ?, ?, ?, ?, ?, ?)",
                (iid, sc.charter_id, p["category_id"], rule["rule_id"],
                 json.dumps([p["decided_by"], p["compliance_by"]]),
                 now(), json.dumps([{"owner": owner, "action": f"delete at the source: {item['content_ref']}"}])))
    _done(cat, p)
    return {"result": "erased", "message": "erasure record written; source location listed as open action"}


def restore(cat: Catalog, sc: Scope, item_id: str, actor: str, archive: Archive) -> dict:
    er = cat.one("SELECT * FROM erasure_record WHERE item_id = ?", (item_id,))
    if er:
        log(cat, sc, "restore", item_id, "erased", "erased", actor, result="refused", reason="erased")
        raise Refused(f"item '{item_id}' was erased on {er['erased_at']:%Y-%m-%d}; it cannot be restored")
    item = corpus.require(cat, item_id)
    category = corpus.category(cat, item)
    if actor not in _approvers_for(sc, category):
        log(cat, sc, "restore", item_id, item["state"], item["state"], actor, result="denied")
        raise Refused(f"authorization error: '{actor}' is not the approver for category '{category}'")
    last = cat.one("SELECT * FROM proposal WHERE item_id = ? AND status = 'executed' ORDER BY executed_at DESC LIMIT 1",
                   (item_id,))
    if item["state"] == "archived":
        m = cat.one("SELECT * FROM archive_manifest WHERE item_id = ?", (item_id,))
        if corpus.sha256(archive.get(m["object_key"])) != m["sha256"]:
            log(cat, sc, "restore", item_id, "archived", "archived", actor, result="failed", reason="checksum mismatch")
            raise Refused(f"archived copy of '{item_id}' does not match the manifest checksum; not restored")
        archive.delete(m["object_key"])
        cat.execute("DELETE FROM archive_manifest WHERE item_id = ?", (item_id,))
        ref = m["proposal_id"]
    elif item["state"] == "excluded":
        fp = cat.one("SELECT raw_sha256 FROM fingerprint WHERE item_id = ? ORDER BY run_id DESC LIMIT 1", (item_id,))
        if fp and corpus.sha256(corpus.read_bytes(item)) != fp["raw_sha256"]:
            raise Refused(f"the source of '{item_id}' changed since it was excluded; re-run detection instead")
        ref = last and last["proposal_id"]
    else:
        raise Refused(f"item '{item_id}' is {item['state']}; only archived or excluded items can be restored")
    cat.execute("UPDATE corpus_item SET state = 'active', state_changed_at = ? WHERE item_id = ?", (now(), item_id))
    log(cat, sc, "restore", item_id, item["state"], "active", actor, decision_ref=ref, checksum_verified=True)
    return corpus.get(cat, item_id)


def register_masked(cat: Catalog, sc: Scope, item_id: str, masked_ref: str, actor: str) -> dict:
    """Hand-off back from sensitive-data-masking (c07): the masked version re-enters the corpus."""
    item = corpus.require(cat, item_id)
    if item["state"] != "awaiting-masking":
        raise Refused(f"item '{item_id}' is {item['state']}, not awaiting-masking")
    cat.execute("UPDATE corpus_item SET state = 'active', masked_ref = ?, state_changed_at = ? WHERE item_id = ?",
                (masked_ref, now(), item_id))
    log(cat, sc, "masked", item_id, "awaiting-masking", "active", actor, masked_ref=masked_ref)
    return corpus.get(cat, item_id)


def awaiting_masking(cat: Catalog, charter_id: str) -> list[dict]:
    return cat.query("SELECT * FROM corpus_item WHERE charter_id = ? AND state = 'awaiting-masking' ORDER BY item_id",
                     (charter_id,))


def check_propagation(cat: Catalog, charter_id: str, index=None) -> dict[str, list[str]]:
    """Derived artifacts of items outside the active corpus that are still present (unknown counts as present)."""
    rows = cat.query("SELECT d.* FROM derived_artifact d WHERE d.removed_at IS NULL AND d.item_id NOT IN"
                     " (SELECT item_id FROM corpus_item WHERE state = 'active') AND (d.item_id IN (SELECT item_id FROM"
                     " corpus_item WHERE charter_id = ?) OR d.item_id IN (SELECT item_id FROM erasure_record WHERE"
                     " charter_id = ?)) ORDER BY d.item_id, d.artifact_id", (charter_id, charter_id))
    present = index.present([r["artifact_id"] for r in rows]) if index is not None else None
    out = {}
    for r in rows:
        if present is not None and r["artifact_id"] not in present:
            cat.execute("UPDATE derived_artifact SET removed_at = ? WHERE artifact_id = ?", (now(), r["artifact_id"]))
            continue
        out.setdefault(r["item_id"], []).append(r["artifact_id"])
    return out
