"""Owner-routed alerts and the incident lifecycle (design D9, D10): one incident per asset and check, one
notification per incident, no personal-data values in alerts, human acknowledgement / resolution / risk
acceptance with a reason, auto-resolve on recovery, escalation after 2 business days, discard candidates after
30 days in freshness error (never executed without a human)."""

import json
import os
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.request import Request, urlopen

from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.evaluate.golden import event
from konmari.inventory.sensitivity import TIERS
from konmari.versions import Refused

ESCALATION_BUSINESS_DAYS = 2   # assumption (design: no source)
DISCARD_AFTER_DAYS = 30        # assumption (design: no source)
OPEN = ("open", "acknowledged", "accepted_risk")  # an incident in one of these absorbs repeated failures
PERSONAL = TIERS.index("personal")
ALERT_FIELDS = ("asset", "dataset", "check", "status", "observed", "expected", "run_id", "incident_id", "kind",
                "message", "samples")


# ---------------------------------------------------------------- notification channels

class MemoryNotifier:
    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to: str, subject: str, payload: dict) -> None:
        self.sent.append({"to": to, "subject": subject, "payload": payload})


class LogNotifier:
    """Default channel: one JSON line per alert in .konmari/notifications.jsonl."""

    def __init__(self, path: str | Path = ".konmari/notifications.jsonl"):
        self.path = Path(path)

    def send(self, to, subject, payload):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"to": to, "subject": subject, "payload": payload}, default=str) + "\n")


class WebhookNotifier:
    def __init__(self, url: str, urlopen_=urlopen):
        self.url, self.urlopen = url, urlopen_

    def send(self, to, subject, payload):
        body = json.dumps({"to": to, "subject": subject, **payload}, default=str).encode()
        with self.urlopen(Request(self.url, data=body, headers={"Content-Type": "application/json"}), timeout=10):
            pass


class EmailNotifier:
    """SMTP; the password comes from the environment variable named in monitor.yaml (never the file itself)."""

    def __init__(self, host: str, port: int, sender: str, user: str | None = None, password_env: str | None = None,
                 smtp=smtplib.SMTP):
        self.host, self.port, self.sender, self.user, self.password_env, self.smtp = host, port, sender, user, \
            password_env, smtp

    def send(self, to, subject, payload):
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        with self.smtp(self.host, self.port) as s:
            if self.user:
                s.starttls()
                s.login(self.user, os.environ.get(self.password_env or "", ""))
            s.send_message(msg)


def notifier(cfg: dict):
    n = cfg.get("notify") or {}
    ch = n.get("channel", "log")
    if ch == "webhook":
        return WebhookNotifier(n["url"])
    if ch == "email":
        return EmailNotifier(n["smtp_host"], int(n.get("smtp_port", 587)), n["from"], n.get("user"),
                             n.get("password_env"))
    if ch == "log":
        return LogNotifier(n.get("path", ".konmari/notifications.jsonl"))
    raise Refused(f"monitor.yaml notify.channel: '{ch}' is not email, webhook or log")


def contact(sc: Scope, who: str | None) -> str | None:
    s = sc.stakeholders.get(who or "")
    return s["contact"] if s and s.get("contact") else who


def use_case_owners(sc: Scope) -> list[str]:
    return [sid for sid, s in sc.stakeholders.items() if "use_case_owner" in (s.get("roles") or [])]


# ---------------------------------------------------------------- personal data stays out of alerts

def personal(cat: Catalog, asset_id: str) -> bool:
    """c07/c02 classification: the asset or one of the product's fields is personal or special-category."""
    if asset_id.startswith("product:"):
        from konmari.connect import descriptor as dsc
        from konmari.connect import products as prd

        cur = prd.current(cat, asset_id.removeprefix("product:"))
        return bool(cur) and any(f["pii_entity"] or (f["classification"] in TIERS and
                                                     TIERS.index(f["classification"]) >= PERSONAL)
                                 for f in dsc.fields(cur["desc"]))
    a = cat.one("SELECT sensitivity_tier, personal_data_categories FROM source_asset WHERE asset_uri = ?",
                (asset_id,))
    tier = (a or {}).get("sensitivity_tier")
    return bool(a) and (tier in TIERS and TIERS.index(tier) >= PERSONAL or
                        bool(json.loads(a["personal_data_categories"] or "[]")))


def alert_payload(cat: Catalog, result: dict, incident_id: str | None) -> dict:
    """Names the dataset, check and observed value; sample IDs only for assets without personal data."""
    d = json.loads(result["details"]) if isinstance(result["details"], str) else result["details"]
    p = {"asset": result["asset_id"], "dataset": result.get("dataset"), "check": result["check_name"],
         "status": result["status"], "observed": result["observed"], "expected": result["expected"],
         "run_id": result["run_id"], "incident_id": incident_id}
    if d.get("samples") and not personal(cat, result["asset_id"]):
        p["samples"] = d["samples"]
    return {k: v for k, v in p.items() if k in ALERT_FIELDS and v is not None}


# ---------------------------------------------------------------- incidents

def open_incident(cat: Catalog, asset_id: str, check: str) -> dict | None:
    return cat.one(f"SELECT * FROM incident WHERE asset_id = ? AND check_name = ? AND status IN {OPEN}"
                   " ORDER BY opened_at DESC LIMIT 1", (asset_id, check))


def process(cat: Catalog, sc: Scope, run_id: str, results: list[dict], send, owners: dict, at: datetime) -> dict:
    """Update incidents from check results; notify the owner once per new incident."""
    out = {"opened": [], "updated": [], "resolved": []}
    for r in results:
        inc = open_incident(cat, r["asset_id"], r["check_name"])
        if r["status"] in ("warn", "error"):
            if inc:
                sev = "error" if "error" in (inc["severity"], r["status"]) else "warn"
                cat.execute("UPDATE incident SET failure_count = failure_count + 1, last_failed = ?, severity = ?"
                            " WHERE incident_id = ?", (at, sev, inc["incident_id"]))
                out["updated"].append(inc["incident_id"])
                continue
            iid = new_id()[:12]
            cat.execute("INSERT INTO incident VALUES (?, ?, ?, ?, 'open', 1, ?, ?, ?, NULL, NULL, NULL, NULL, NULL,"
                        " NULL, NULL)", (iid, r["asset_id"], r["check_name"], r["status"], at, at, run_id))
            to = contact(sc, owners.get(r["asset_id"]))
            if to:
                send.send(to, f"[konmari] {r['status']}: {r['check_name']} on {r.get('dataset') or r['asset_id']}",
                          alert_payload(cat, r, iid))
                cat.execute("UPDATE incident SET notified_at = ? WHERE incident_id = ?", (at, iid))
            event(cat, "monitor.incident.open", "konmari-monitor", sc, incident_id=iid, asset=r["asset_id"],
                  check=r["check_name"], status=r["status"], run_id=run_id, notified=bool(to))
            out["opened"].append(iid)
        elif inc and inc["status"] in ("open", "acknowledged"):
            cat.execute("UPDATE incident SET status = 'resolved', decided_by = 'system', decided_at = ?,"
                        " resolving_run = ?, reason = 'check passed' WHERE incident_id = ?",
                        (at, run_id, inc["incident_id"]))
            event(cat, "monitor.incident.resolve", "system", sc, incident_id=inc["incident_id"], previous=inc["status"],
                  new="resolved", run_id=run_id, reason="check passed")
            out["resolved"].append(inc["incident_id"])
    return out


TRANSITIONS = {"acknowledge": ("acknowledged", ("open",)), "resolve": ("resolved", ("open", "acknowledged")),
               "accept-risk": ("accepted_risk", ("open", "acknowledged"))}


def decide(cat: Catalog, sc: Scope, incident_id: str, action: str, actor: str, reason: str) -> dict:
    if not (reason or "").strip():
        raise Refused("a reason is required")
    new, allowed = TRANSITIONS[action]
    inc = cat.one("SELECT * FROM incident WHERE incident_id = ?", (incident_id,))
    if inc is None:
        raise Refused(f"unknown incident '{incident_id}'")
    if inc["status"] not in allowed:
        raise Refused(f"incident {incident_id} is {inc['status']}; '{action}' needs status {' or '.join(allowed)}")
    cat.execute("UPDATE incident SET status = ?, decided_by = ?, decided_at = ?, reason = ? WHERE incident_id = ?",
                (new, actor, now(), reason, incident_id))
    event(cat, f"monitor.incident.{action}", actor, sc, incident_id=incident_id, previous=inc["status"], new=new,
          reason=reason)
    return {**inc, "status": new, "decided_by": actor, "reason": reason}


def business_days(start: datetime, end: datetime) -> int:
    """Whole business days (Mon-Fri) elapsed after `start`. shortcut: public holidays are not excluded."""
    n, d = 0, start
    while d + timedelta(days=1) <= end:
        d += timedelta(days=1)
        n += d.weekday() < 5
    return n


def escalate(cat: Catalog, sc: Scope, send, at: datetime) -> list[str]:
    out = []
    for inc in cat.query("SELECT * FROM incident WHERE status = 'open' AND severity = 'error' AND escalated_at IS NULL"):
        if business_days(inc["opened_at"], at) > ESCALATION_BUSINESS_DAYS:
            owners = use_case_owners(sc)
            for o in owners:
                send.send(contact(sc, o), f"[konmari] escalation: {inc['check_name']} on {inc['asset_id']} "
                                          f"unacknowledged for more than {ESCALATION_BUSINESS_DAYS} business days",
                          {"asset": inc["asset_id"], "check": inc["check_name"], "status": "error",
                           "incident_id": inc["incident_id"], "kind": "escalation"})
            cat.execute("UPDATE incident SET escalated_at = ? WHERE incident_id = ?", (at, inc["incident_id"]))
            event(cat, "monitor.incident.escalate", "konmari-monitor", sc, incident_id=inc["incident_id"],
                  to=owners, business_days=business_days(inc["opened_at"], at))
            out.append(inc["incident_id"])
    return out


def propose_discard(cat: Catalog, sc: Scope, at: datetime) -> list[dict]:
    """Assets in freshness error for more than 30 consecutive days become c04 discard proposals (pending: the
    discard pass changes nothing until an authorized person approves)."""
    from konmari.discard import decisions as dd

    out = []
    for a in cat.query("SELECT * FROM monitored_asset WHERE charter_id = ? AND freshness_status = 'error'"
                       " AND error_since IS NOT NULL", (sc.charter_id,)):
        days = (at - a["error_since"]).days
        inc = open_incident(cat, a["asset_id"], "freshness")
        if days <= DISCARD_AFTER_DAYS or inc is None or inc["discard_proposals"] is not None:
            continue
        reason = (f"freshness error for {days} consecutive days (asset {a['asset_id']}, incident "
                  f"{inc['incident_id']})")
        ids = []
        for item in cat.query("SELECT * FROM corpus_item WHERE asset_uri = ? AND state = 'active' ORDER BY item_id",
                              (a["asset_id"],)):
            if dd.open_proposal(cat, item["item_id"]) is None:
                ids.append(dd.create_proposal(cat, sc, item, "archive", "freshness-monitor", [], "konmari-monitor",
                                              reason)["proposal_id"])
        cat.execute("UPDATE incident SET discard_proposals = ? WHERE incident_id = ?",
                    (json.dumps(ids), inc["incident_id"]))
        event(cat, "monitor.discard.propose", "konmari-monitor", sc, asset=a["asset_id"],
              incident_id=inc["incident_id"], days=days, proposals=ids)
        out.append({"asset": a["asset_id"], "incident_id": inc["incident_id"], "days": days, "proposals": ids})
    return out
