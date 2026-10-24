"""Charter lifecycle in the catalog: register, approve, amend, decision log, status, scope lookup."""

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from konmari import versions as v
from konmari.canonical import canonical_hash, load_yaml
from konmari.catalog import Catalog, audit, connect, new_id, now, os_user
from konmari.charter.validation import roles_by_id, validate_charter
from konmari.versions import Refused

T = "charter_version"
CADENCE_GRACE_DAYS = 7  # our assumption (design decision 10)


def load_charter(path="charter.yaml") -> dict:
    doc = load_yaml(Path(path))
    if not isinstance(doc, dict) or "charter_id" not in doc:
        raise Refused(f"{path}: not a charter (no charter_id)")
    return doc


def _write_register(cat: Catalog, doc: dict, version: int) -> None:
    cid = doc["charter_id"]
    cat.execute("DELETE FROM stakeholder WHERE charter_id = ? AND version = ?", (cid, version))
    cat.execute("DELETE FROM raci_assignment WHERE charter_id = ? AND version = ?", (cid, version))
    for s in doc["stakeholders"]:
        cat.execute("INSERT INTO stakeholder VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (cid, version, s["id"], s["name"], s["contact"], s["org_unit"], ",".join(s["roles"])))
    for act, row in doc["raci"].items():
        for letter in "RACI":
            for sid in dict.fromkeys(row.get(letter, [])):
                cat.execute("INSERT INTO raci_assignment VALUES (?, ?, ?, ?, ?)", (cid, version, act, sid, letter))


def _require_valid(doc) -> None:
    errors = validate_charter(doc)
    if errors:
        raise Refused(errors)


def register(cat: Catalog, doc: dict, actor: str) -> dict:
    _require_valid(doc)
    row, changed = v.sync_draft(cat, T, doc["charter_id"], doc, actor, "charter")
    if changed:
        _write_register(cat, doc, row["version"])
    return row


def approve(cat: Catalog, doc: dict, approver: str, compliance_signoff: bool = False) -> dict:
    cid = doc.get("charter_id")
    _require_valid(doc)
    roles = roles_by_id(doc)
    if approver not in roles:
        raise Refused(f"'{approver}' is not in the stakeholder register")
    row = register(cat, doc, approver)
    ver, h = row["version"], row["content_hash"]
    ai = doc["compliance"]["ai_act"]

    def record(subject, note=None):
        cat.execute("INSERT INTO approval VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id(), subject, cid, ver, approver, ",".join(sorted(roles[approver])), os_user(), now(), h,
                     note))

    if compliance_signoff:
        reasons = []
        if "compliance" not in roles[approver]:
            reasons.append(f"'{approver}' does not hold the compliance role")
        if ai["classification"] != "high":
            reasons.append(f"a compliance sign-off is only needed for AI Act classification 'high' "
                           f"(current: {ai['classification']})")
        if reasons:
            audit(cat, "charter.compliance_signoff", actor=approver, charter_id=cid, charter_version=ver,
                  result="refused", details={"reasons": reasons})
            raise Refused(reasons)
        record("ai_act_high", f"AI Act classification high: {ai['reason']}")
        audit(cat, "charter.compliance_signoff", actor=approver, charter_id=cid, charter_version=ver,
              result="recorded", details={"classification": "high", "content_hash": h})
        return v.latest(cat, T, cid)

    if row["status"] == "approved":
        raise Refused(f"version {ver} is already approved")
    reasons = []
    acc = doc["raci"]["charter_approval"]["A"][0]
    if approver != acc:
        reasons.append(f"'{approver}' is not Accountable for charter approval (Accountable: '{acc}')")
    if doc["compliance"]["dpia_screening"]["result"] == "pending":
        reasons.append("DPIA screening is 'pending'; the compliance stakeholder must decide it first")
    if ai["classification"] == "pending":
        reasons.append("EU AI Act classification is 'pending'")
    if ai["classification"] == "prohibited":
        reasons.append("EU AI Act classification is 'prohibited'; this charter cannot be approved")
    if ai["classification"] == "high" and not cat.one(
            "SELECT id FROM approval WHERE subject = 'ai_act_high' AND charter_id = ? AND version = ?"
            " AND content_hash = ?", (cid, ver, h)):
        reasons.append("EU AI Act classification is 'high'; a compliance stakeholder must first run "
                       "'konmari charter approve --as <id> --compliance-signoff'")
    if reasons:
        audit(cat, "charter.approve", actor=approver, charter_id=cid, charter_version=ver, result="refused",
              details={"reasons": reasons})
        raise Refused(reasons)
    record("charter")
    v.set_status(cat, T, cid, ver, "approved")
    audit(cat, "charter.approve", actor=approver, charter_id=cid, charter_version=ver, result="approved",
          details={"content_hash": h, "os_user": os_user()})
    return v.latest(cat, T, cid)


def amend(cat: Catalog, doc: dict | None, reason: str, actor: str, cid: str, from_version: int | None = None) -> dict:
    if not reason or not reason.strip():
        raise Refused("an amendment needs a reason (--reason)")
    row = v.latest(cat, T, cid)
    if row is None:
        raise Refused("no charter version in the catalog yet; run 'konmari charter approve' first")
    if from_version is not None:
        src = v.get(cat, T, cid, from_version)
        if src is None:
            raise Refused(f"version {from_version} does not exist")
        doc = src["doc"]
    _require_valid(doc)
    if doc["charter_id"] != cid:
        raise Refused("charter_id cannot be changed by an amendment; start a new charter instead")
    if canonical_hash(doc) == row["content_hash"]:
        raise Refused(f"no changes compared with version {row['version']}")
    new = row["version"] + 1
    v.insert(cat, T, cid, new, doc, actor, reason)
    _write_register(cat, doc, new)
    audit(cat, "charter.amend", actor=actor, charter_id=cid, charter_version=new, result="draft",
          details={"reason": reason, "previous_version": row["version"], "from_version": from_version})
    return v.latest(cat, T, cid)


def approvals(cat: Catalog, cid: str, version: int | None = None) -> list[dict]:
    if version is None:
        return cat.query("SELECT * FROM approval WHERE charter_id = ? AND subject <> 'metrics' ORDER BY approved_at",
                         (cid,))
    return cat.query("SELECT * FROM approval WHERE charter_id = ? AND version = ? AND subject <> 'metrics'"
                     " ORDER BY approved_at", (cid, version))


def log_decision(cat: Catalog, cid: str, author: str, meeting_type: str, meeting_date: str, attendees: list,
                 decisions: list, actions: list[dict]) -> dict:
    row = v.latest(cat, T, cid)
    if row is None:
        raise Refused("no charter version in the catalog yet; run 'konmari charter approve' first")
    if not decisions:
        raise Refused("a decision-log entry needs at least one decision")
    date.fromisoformat(meeting_date)
    entry = {"id": new_id(), "charter_id": cid, "charter_version": row["version"], "logged_at": now(),
             "author": author, "meeting_type": meeting_type, "meeting_date": meeting_date,
             "attendees": json.dumps(attendees), "decisions": json.dumps(decisions), "actions": json.dumps(actions)}
    cat.execute("INSERT INTO decision_log VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", tuple(entry.values()))
    audit(cat, "charter.log", actor=author, charter_id=cid, charter_version=row["version"], result="logged",
          details={"meeting_type": meeting_type, "meeting_date": meeting_date})
    return entry


def decision_log(cat: Catalog, cid: str) -> list[dict]:
    rows = cat.query("SELECT * FROM decision_log WHERE charter_id = ? ORDER BY meeting_date, logged_at", (cid,))
    for r in rows:
        for k in ("attendees", "decisions", "actions"):
            r[k] = json.loads(r[k])
    return rows


def status(cat: Catalog, cid: str, today: date | None = None) -> dict:
    today = today or date.today()
    row = v.latest(cat, T, cid)
    if row is None:
        raise Refused(f"charter '{cid}' is not in the catalog yet")
    doc = row["doc"]
    ms = v.latest(cat, "metric_set_version", cid)
    last = cat.one("SELECT MAX(meeting_date) AS d FROM decision_log WHERE charter_id = ? AND meeting_type = 'steering'",
                   (cid,))["d"]
    last = date.fromisoformat(str(last)) if last else None
    interval = doc["cadence"]["steering"]["interval_days"]
    since = last or date.fromisoformat(doc["cadence"]["kickoff"])
    age = (today - since).days
    out = {"charter_id": cid, "version": row["version"], "status": row["status"],
           "metric_set": f"version {ms['version']} ({ms['status']})" if ms else "not registered",
           "last_steering": str(last) if last else None, "cadence_breach": None}
    if age > interval + CADENCE_GRACE_DAYS:
        sponsors = [f"{s['name']} ({s['id']})" for s in doc["stakeholders"] if "sponsor" in s["roles"]]
        what = f"last steering meeting {age} days ago" if last else f"no steering meeting {age} days after kickoff"
        out["cadence_breach"] = (f"{what} (interval {interval} days + {CADENCE_GRACE_DAYS} days grace); "
                                 f"sponsor: {', '.join(sponsors)}")
    return out


@dataclass
class Scope:
    """The pilot data scope of the latest approved charter version. Later steps read sources,
    categories, approvers and reviewers from here and nowhere else."""

    charter_id: str
    charter_version: int
    sources: list[dict]
    categories: dict[str, dict] = field(default_factory=dict)  # id -> {"approver": id, "reviewers": [ids]}
    stakeholders: dict[str, dict] = field(default_factory=dict)

    def approver(self, category: str) -> str:
        if category not in self.categories:
            raise KeyError(f"category '{category}' is not in the scope of charter {self.charter_id} "
                           f"v{self.charter_version}")
        return self.categories[category]["approver"]

    def reviewers(self, category: str) -> list[str]:
        self.approver(category)
        return list(self.categories[category]["reviewers"])


def scope(charter_id: str, catalog: Catalog | None = None) -> Scope:
    cat = catalog or connect()
    row = v.latest_approved(cat, T, charter_id)
    if row is None:
        raise LookupError(f"charter '{charter_id}' has no approved version")
    ds = row["doc"]["data_scope"]
    return Scope(charter_id, row["version"], ds["sources"],
                 {c["id"]: {"approver": c["approver"], "reviewers": c["reviewers"]} for c in ds["categories"]},
                 {s["id"]: s for s in row["doc"]["stakeholders"]})
