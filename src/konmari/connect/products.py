"""Data products in the catalog (design D2, D3): registration, the version state machine, contract tests,
semver gate via `datacontract breaking`, owner approval, publish, rollback, deprecation window, catalog listing.
Every transition and refusal is written to the one audit trail (step 'connect')."""

import json
from collections import Counter
from datetime import datetime, timedelta

from konmari.canonical import canonical_hash, canonical_json, dump_yaml
from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.connect import descriptor as dsc
from konmari.governance.audit import write_event
from konmari.inventory.sensitivity import TIERS
from konmari.versions import Refused

STEP = "connect"
DEPRECATION_DAYS = 90  # our assumption (design D2), configurable per call / connect.yaml
TRANSITIONS = {
    "draft": {"approved"},
    "approved": {"published", "draft"},
    "published": {"superseded", "deprecated", "withdrawn"},
    "superseded": {"published", "deprecated"},
    "deprecated": {"published", "sunset", "withdrawn"},
    "sunset": set(),
    "withdrawn": set(),
}
CALLABLE = ("published", "deprecated")
BUMPS = (None, "patch", "minor", "major")
# changelog paths that only describe a field; changing them is a metadata (patch) change
METADATA = ("description", "businessName", "tags", "customProperties", "authoritativeDefinitions", "examples")


def event(cat: Catalog, action: str, actor: str, sc: Scope | None = None, result: str = "recorded", **details):
    return write_event(cat, action, actor=actor, step=STEP, result=result,
                       charter_id=sc.charter_id if sc else None, **details)


# ---------------------------------------------------------------- versions and the state machine

def _row(r: dict | None) -> dict | None:
    if r:
        r["desc"] = json.loads(r["descriptor"])
    return r


def get(cat: Catalog, pid: str, version: str) -> dict | None:
    return _row(cat.one("SELECT * FROM data_product_version WHERE product_id = ? AND version = ?", (pid, version)))


def versions(cat: Catalog, pid: str) -> list[dict]:
    rows = [_row(r) for r in cat.query("SELECT * FROM data_product_version WHERE product_id = ?", (pid,))]
    return sorted(rows, key=lambda r: dsc.parse_version(r["version"]))


def versions_in(cat: Catalog, states: tuple[str, ...]) -> list[dict]:
    return [_row(r) for r in cat.query(f"SELECT * FROM data_product_version WHERE state IN "
                                       f"({', '.join('?' * len(states))}) ORDER BY product_id, version", states)]


def product(cat: Catalog, pid: str) -> dict:
    p = cat.one("SELECT * FROM data_product WHERE product_id = ?", (pid,))
    if p is None:
        raise Refused(f"unknown data product '{pid}'; run 'konmari product lint' on its descriptor first")
    return p


def current(cat: Catalog, pid: str) -> dict | None:
    return _row(cat.one("SELECT * FROM data_product_version WHERE product_id = ? AND state = 'published'", (pid,)))


def transition(cat: Catalog, pid: str, version: str, to: str, actor: str, sc: Scope | None = None, **details
               ) -> dict:
    row = get(cat, pid, version)
    if row is None:
        raise Refused(f"{pid} has no version {version}")
    if to not in TRANSITIONS[row["state"]]:
        raise Refused(f"{pid} {version}: transition {row['state']} -> {to} is not allowed")
    cat.execute("UPDATE data_product_version SET state = ? WHERE product_id = ? AND version = ?", (to, pid, version))
    event(cat, "product.transition", actor, sc, product=pid, version=version, previous=row["state"], new=to,
          **details)
    return get(cat, pid, version)


def register(cat: Catalog, sc: Scope, desc: dict, actor: str) -> dict:
    """A valid descriptor becomes the draft of its version. Approved content is frozen: a changed descriptor for an
    approved version goes back to draft; for a published version it needs a new version number."""
    i, h, t = dsc.info(desc), canonical_hash(desc), now()
    if cat.one("SELECT 1 AS x FROM data_product WHERE product_id = ?", (i["id"],)) is None:
        cat.execute("INSERT INTO data_product VALUES (?, ?, ?, ?, ?, ?)",
                    (i["id"], i["name"], i["category"], i["owner"], sc.charter_id, t))
        event(cat, "product.register", actor, sc, product=i["id"], category=i["category"], owner=i["owner"])
    else:
        cat.execute("UPDATE data_product SET name = ?, category_id = ?, owner = ? WHERE product_id = ?",
                    (i["name"], i["category"], i["owner"], i["id"]))
    row = get(cat, i["id"], i["version"])
    if row is None:
        cat.execute("INSERT INTO data_product_version (product_id, version, state, descriptor, content_hash,"
                    " created_at, created_by) VALUES (?, ?, 'draft', ?, ?, ?, ?)",
                    (i["id"], i["version"], canonical_json(desc), h, t, actor))
        event(cat, "product.draft", actor, sc, product=i["id"], version=i["version"], content_hash=h)
    elif row["content_hash"] != h:
        if row["state"] not in ("draft", "approved"):
            raise Refused(f"{i['id']} {i['version']} is {row['state']} and frozen; raise the version to change it")
        cat.execute("UPDATE data_product_version SET descriptor = ?, content_hash = ? WHERE product_id = ?"
                    " AND version = ?", (canonical_json(desc), h, i["id"], i["version"]))
        if row["state"] == "approved":
            transition(cat, i["id"], i["version"], "draft", actor, sc, reason="descriptor changed after approval")
        else:
            event(cat, "product.draft", actor, sc, product=i["id"], version=i["version"], content_hash=h)
    return get(cat, i["id"], i["version"])


def expire(cat: Catalog, at: datetime | None = None) -> None:
    """Deprecated versions past their sunset date become `sunset` (no longer callable)."""
    at = at or now()
    for r in cat.query("SELECT product_id, version FROM data_product_version WHERE state = 'deprecated'"
                       " AND sunset_at IS NOT NULL AND sunset_at <= ?", (at,)):
        transition(cat, r["product_id"], r["version"], "sunset", "konmari-connect", reason="sunset date reached")


def callable_versions(cat: Catalog, at: datetime | None = None) -> list[dict]:
    """What the MCP server may expose right now: published and deprecated-but-not-sunset versions."""
    expire(cat, at)
    rows = [_row(r) for r in cat.query("SELECT * FROM data_product_version WHERE state IN ('published',"
                                       " 'deprecated') ORDER BY product_id")]
    return sorted(rows, key=lambda r: (r["product_id"], dsc.parse_version(r["version"])))


# ---------------------------------------------------------------- contract tests

def _type_ok(v, t: str) -> bool:
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if t == "boolean":
        return isinstance(v, bool)
    if t in ("date", "timestamp"):
        try:
            datetime.fromisoformat(str(v))
            return True
        except ValueError:
            return False
    return isinstance(v, str)


def _compare(value, rule: dict) -> bool:
    ops = {"mustBe": lambda x, y: x == y, "mustNotBe": lambda x, y: x != y,
           "mustBeGreaterThan": lambda x, y: x > y, "mustBeGreaterOrEqualTo": lambda x, y: x >= y,
           "mustBeLessThan": lambda x, y: x < y, "mustBeLessOrEqualTo": lambda x, y: x <= y,
           "mustBeBetween": lambda x, y: y[0] <= x <= y[1], "mustNotBeBetween": lambda x, y: not y[0] <= x <= y[1]}
    return all(fn(value, rule[op]) for op, fn in ops.items() if op in rule)


def evaluate(desc: dict, rows: list[dict]) -> list[dict]:
    """The contract's schema and quality rules on materialised rows: required (not null), unique / primaryKey,
    logicalType, and ODCS library metrics nullValues, missingValues, invalidValues, duplicateValues, rowCount.
    shortcut: `datacontract test` needs duckdb<1.6 (we pin duckdb 2.0), so the ODCS library rules are evaluated
    here; custom SQL rules are not run."""
    failures = []

    def metric(name: str, field: str | None, args: dict) -> int:
        vals = [r.get(field) for r in rows] if field else []
        if name == "nullValues":
            return sum(v is None for v in vals)
        if name == "missingValues":
            missing = args.get("missingValues", [None, ""])
            return sum(v in missing for v in vals)
        if name == "invalidValues":
            valid = args.get("validValues")
            return sum(v is not None and valid is not None and v not in valid for v in vals)
        if name == "duplicateValues":
            return sum(n - 1 for v, n in Counter(v for v in vals if v is not None).items() if n > 1)
        return len(rows)  # rowCount

    for f in dsc.fields(desc):
        checks = []
        if f["required"] or f["primary_key"]:
            checks.append(("required", "nullValues", {"mustBe": 0}))
        if f["unique"] or f["primary_key"]:
            checks.append(("unique", "duplicateValues", {"mustBe": 0}))
        for q in f["quality"]:
            if q.get("metric"):
                checks.append((q.get("name") or q["metric"], q["metric"], q))
        for rule, m, q in checks:
            n = metric(m, f["name"], q.get("arguments") or {})
            if not _compare(n, q):
                failures.append({"rule": rule, "field": f["name"], "violations": n})
        bad = sum(r.get(f["name"]) is not None and not _type_ok(r[f["name"]], f["type"]) for r in rows)
        if bad:
            failures.append({"rule": f"logicalType {f['type']}", "field": f["name"], "violations": bad})
    for q in dsc.table(desc).get("quality") or []:
        if q.get("metric") == "rowCount" and not _compare(len(rows), q):
            failures.append({"rule": q.get("name") or "rowCount", "field": None, "violations": len(rows)})
    return failures


def materialised(cat: Catalog, pid: str) -> list[dict]:
    return [json.loads(r["data"]) for r in cat.query("SELECT data FROM product_row WHERE product_id = ?"
                                                     " ORDER BY row_key", (pid,))]


def run_tests(cat: Catalog, pid: str, version: str, rows: list[dict] | None = None, origin: str = "test",
              actor: str = "konmari-connect") -> dict:
    """Contract tests against the materialised product data (or the staged rows of a refresh); stored."""
    row = get(cat, pid, version)
    if row is None:
        raise Refused(f"{pid} has no version {version}")
    rows = materialised(cat, pid) if rows is None else rows
    failures = evaluate(row["desc"], rows)
    res = {"test_id": new_id(), "product_id": pid, "version": version, "content_hash": row["content_hash"],
           "run_at": now(), "passed": not failures, "row_count": len(rows), "failures": failures,
           "origin": origin}
    cat.execute("INSERT INTO product_test VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*[res[k] for k in ("test_id", "product_id", "version", "content_hash", "run_at", "passed",
                                    "row_count")], json.dumps(failures), origin))
    event(cat, "product.test", actor, result="passed" if not failures else "failed", product=pid, version=version,
          test=res["test_id"], row_count=len(rows), failures=failures)
    return res


def latest_test(cat: Catalog, pid: str, version: str, content_hash: str) -> dict | None:
    r = cat.one("SELECT * FROM product_test WHERE product_id = ? AND version = ? AND content_hash = ?"
                " ORDER BY run_at DESC LIMIT 1", (pid, version, content_hash))
    if r:
        r["failures"] = json.loads(r["failures"])
    return r


# ---------------------------------------------------------------- semver gate

def _bump(old: str, new: str) -> str | None:
    o, n = dsc.parse_version(old), dsc.parse_version(new)
    if n <= o:
        return None
    return "major" if n[0] > o[0] else "minor" if n[1] > o[1] else "patch"


def _required(old: str, level: str | None) -> str:
    o = dsc.parse_version(old)
    return {"major": f"{o[0] + 1}.0.0", "minor": f"{o[0]}.{o[1] + 1}.0", "patch": f"{o[0]}.{o[1]}.{o[2] + 1}",
            None: old}[level]


def compare(old_contract: dict, new_contract: dict) -> dict:
    """`datacontract breaking` (consumer perspective) and its changelog, reduced to the semver level."""
    from datacontract.data_contract import DataContract

    a, b = DataContract(data_contract_str=dump_yaml(old_contract)), \
        DataContract(data_contract_str=dump_yaml(new_contract))
    res = a.breaking(b)
    changes = [{"path": e.path, "change": e.change_type.value, "level": e.level.value, "message": e.message}
               for e in res.entries if e.path != "version"]
    if res.is_breaking:
        level = "major"
    elif any(c["change"] in ("added", "removed") and c["path"].startswith("schema.")
             and not c["path"].rsplit(".", 1)[-1] in METADATA for c in changes):
        level = "minor"
    else:
        level = "patch" if changes else None
    return {"breaking": res.is_breaking, "required": level, "changes": changes}


def diff(cat: Catalog, pid: str, desc: dict) -> dict:
    """Against the current published version: the required bump, the actual bump, and whether it is enough."""
    version = dsc.info(desc)["version"]
    base = current(cat, pid)
    if base is None:
        return {"product": pid, "version": version, "base": None, "breaking": False, "required": None,
                "bump": "initial", "ok": True, "changes": []}
    c = compare(base["desc"]["contract"], desc["contract"])
    bump = _bump(base["version"], version)
    ok = bump is not None and BUMPS.index(bump) >= BUMPS.index(c["required"])
    need = _required(base["version"], c["required"] or "patch")
    msg = None if ok else (f"the change is {'breaking' if c['breaking'] else c['required'] or 'unchanged'}"
                           f" against {base['version']}: requires version {need}")
    return {"product": pid, "version": version, "base": base["version"], **c, "bump": bump, "ok": ok,
            "minimum": need, "message": msg}


# ---------------------------------------------------------------- approval, publish, rollback, deprecation

def _owner_only(cat: Catalog, pid: str, actor: str) -> dict:
    p = product(cat, pid)
    if actor != p["owner"]:
        raise Refused(f"'{actor}' is not the owner of {pid} (owner: {p['owner']})")
    return p


def approve(cat: Catalog, sc: Scope, pid: str, version: str, actor: str) -> dict:
    """The product owner approves a draft: approver, timestamp, version, diff summary and the test result."""
    _owner_only(cat, pid, actor)
    row = get(cat, pid, version)
    if row is None or row["state"] != "draft":
        raise Refused(f"{pid} {version} is {row['state'] if row else 'unknown'}; only a draft can be approved")
    d = diff(cat, pid, row["desc"])
    if not d["ok"]:
        raise Refused(d["message"])
    test = latest_test(cat, pid, version, row["content_hash"])
    summary = {k: d[k] for k in ("base", "bump", "required", "breaking")} | {"changes": len(d["changes"])}
    cat.execute("INSERT INTO product_approval VALUES (?, ?, ?, 'publish', ?, ?, ?, ?, ?, NULL)",
                (new_id(), pid, version, actor, now(), json.dumps(summary), test and test["test_id"],
                 row["content_hash"]))
    return transition(cat, pid, version, "approved", actor, sc, diff_summary=summary,
                      test=test and test["test_id"], test_passed=test and test["passed"])


def _refuse(cat, sc, actor, pid, version, reasons):
    event(cat, "product.publish", actor, sc, result="refused", product=pid, version=version, reasons=reasons)
    raise Refused(reasons)


def publish(cat: Catalog, sc: Scope, pid: str, version: str, actor: str, days: int = DEPRECATION_DAYS,
            exposable=None, transport=None, at: datetime | None = None) -> dict:
    """Publish an approved, tested version; the previous one becomes superseded (same major) or deprecated with a
    successor and sunset date (new major). Refusals are audited and change nothing."""
    from konmari.connect import lineage
    from konmari.governance import gate

    at = at or now()
    row, reasons = get(cat, pid, version), []
    if row is None:
        _refuse(cat, sc, actor, pid, version, [f"{pid} has no version {version}"])
    appr = cat.one("SELECT * FROM product_approval WHERE product_id = ? AND version = ? AND kind = 'publish'"
                   " AND content_hash = ? ORDER BY approved_at DESC LIMIT 1", (pid, version, row["content_hash"]))
    if row["state"] != "approved" or appr is None:
        reasons.append(f"{pid} {version} has no recorded owner approval (state {row['state']}); run "
                       f"'konmari product approve {pid} --version {version}'")
    test = latest_test(cat, pid, version, row["content_hash"])
    if test is None or not test["passed"]:
        reasons.append(f"{pid} {version}: contract tests " + ("have not run" if test is None else
                       "failed: " + ", ".join(f"{f['rule']} {f['field']} ({f['violations']})"
                                              for f in test["failures"])))
    d = diff(cat, pid, row["desc"])
    if not d["ok"]:
        reasons.append(d["message"])
    category = dsc.info(row["desc"])["category"]
    ok_sources = {r["category_id"] for r in (exposable or (lambda: gate.exposable(cat, sc)))()}
    if category not in ok_sources:
        reasons.append(f"category '{category}' is not exposable per the governance gate (konmari govern check)")
    if reasons:
        _refuse(cat, sc, actor, pid, version, reasons)
    run = lineage.start(cat, f"product.publish.{pid}", [], [f"{pid}@{version}"], transport)
    prev = current(cat, pid)
    if prev:
        if dsc.major(version) > dsc.major(prev["version"]):
            transition(cat, pid, prev["version"], "deprecated", actor, sc, successor=version)
            cat.execute("UPDATE data_product_version SET deprecated_at = ?, sunset_at = ?, successor = ?"
                        " WHERE product_id = ? AND version = ?",
                        (at, at + timedelta(days=days), version, pid, prev["version"]))
        else:
            transition(cat, pid, prev["version"], "superseded", actor, sc, successor=version)
    cat.execute("UPDATE data_product_version SET published_at = ? WHERE product_id = ? AND version = ?",
                (at, pid, version))
    out = transition(cat, pid, version, "published", actor, sc, approver=appr["approver"],
                     diff_summary=json.loads(appr["diff_summary"]), test=test["test_id"],
                     previous_version=prev and prev["version"])
    lineage.finish(cat, run, "COMPLETE", transport)
    return out


def rollback(cat: Catalog, sc: Scope, pid: str, actor: str, reason: str) -> dict:
    """One approved, audited command: the current version is withdrawn, the previous one is current again."""
    _owner_only(cat, pid, actor)
    if not (reason or "").strip():
        raise Refused("a rollback needs --reason")
    cur = current(cat, pid)
    if cur is None:
        raise Refused(f"{pid} has no published version to roll back")
    prev = [r for r in versions(cat, pid) if r["state"] in ("superseded", "deprecated") and r["published_at"]]
    if not prev:
        raise Refused(f"{pid} {cur['version']} has no previous version to roll back to")
    back = max(prev, key=lambda r: r["published_at"])
    cat.execute("INSERT INTO product_approval VALUES (?, ?, ?, 'rollback', ?, ?, ?, NULL, ?, ?)",
                (new_id(), pid, back["version"], actor, now(),
                 json.dumps({"from": cur["version"], "to": back["version"]}), back["content_hash"], reason))
    transition(cat, pid, cur["version"], "withdrawn", actor, sc, reason=reason, rollback_to=back["version"])
    cat.execute("UPDATE data_product_version SET deprecated_at = NULL, sunset_at = NULL, successor = NULL"
                " WHERE product_id = ? AND version = ?", (pid, back["version"]))
    out = transition(cat, pid, back["version"], "published", actor, sc, reason=reason,
                     rollback_from=cur["version"])
    event(cat, "product.rollback", actor, sc, product=pid, previous=cur["version"], new=back["version"],
          reason=reason)
    return out


def deprecate(cat: Catalog, sc: Scope, pid: str, version: str, actor: str, successor: str | None,
              days: int = DEPRECATION_DAYS, at: datetime | None = None) -> dict:
    _owner_only(cat, pid, actor)
    at = at or now()
    out = transition(cat, pid, version, "deprecated", actor, sc, successor=successor, days=days)
    cat.execute("UPDATE data_product_version SET deprecated_at = ?, sunset_at = ?, successor = ? WHERE"
                " product_id = ? AND version = ?", (at, at + timedelta(days=days), successor, pid, version))
    return get(cat, pid, version) or out


# ---------------------------------------------------------------- catalog

def freshness(as_of: datetime | None, sla: timedelta | None, at: datetime | None = None) -> str:
    return "fresh" if as_of and sla and (at or now()) - as_of <= sla else "stale"


def last_refresh(cat: Catalog, pid: str) -> dict | None:
    return cat.one("SELECT * FROM refresh_run WHERE product_id = ? AND status != 'running'"
                   " ORDER BY started_at DESC LIMIT 1", (pid,))


def as_of(cat: Catalog, pid: str) -> datetime | None:
    r = cat.one("SELECT as_of FROM refresh_run WHERE product_id = ? AND status = 'ok' ORDER BY started_at DESC"
                " LIMIT 1", (pid,))
    return r and r["as_of"]


def listing(cat: Catalog, at: datetime | None = None) -> list[dict]:
    """`konmari product list`: one entry per product; only published and deprecated versions are callable."""
    from konmari.connect import tools

    callable_ = callable_versions(cat, at)
    out = []
    for p in cat.query("SELECT * FROM data_product ORDER BY product_id"):
        pid, vs = p["product_id"], versions(cat, p["product_id"])
        cur = next((v for v in vs if v["state"] == "published"), None)
        latest = cur or (vs[-1] if vs else None)
        i = dsc.info(latest["desc"]) if latest else {}
        mine = [v for v in callable_ if v["product_id"] == pid]
        last, ts = last_refresh(cat, pid), as_of(cat, pid)
        out.append({
            "id": pid, "name": p["name"], "purpose": i.get("purpose"), "owner": p["owner"],
            "category": p["category_id"], "current_version": cur and cur["version"],
            "callable_versions": [v["version"] for v in mine],
            "deprecated_versions": [{"version": v["version"], "successor": v["successor"],
                                     "sunset": v["sunset_at"] and v["sunset_at"].isoformat()}
                                    for v in mine if v["state"] == "deprecated"],
            "versions": {v["version"]: v["state"] for v in vs},
            "freshness_sla": i.get("sla") and _iso_duration(i["sla"]), "as_of": ts and ts.isoformat(),
            "freshness": freshness(ts, i.get("sla"), at), "last_refresh": last and last["status"],
            "classification": dict(sorted(Counter(f["classification"] for f in dsc.fields(latest["desc"])
                                                  if f["classification"] in TIERS).items())) if latest else {},
            "tools": sorted({t["name"] for v in mine for t in tools.definitions(v)}),
            "resources": [f"konmari://products/{pid}/{v['version']}" for v in mine],
        })
    return out


def _iso_duration(td: timedelta) -> str:
    s = int(td.total_seconds())
    if s % 86400 == 0:
        return f"P{s // 86400}D"
    return f"PT{s // 3600}H" if s % 3600 == 0 else f"PT{s // 60}M"


def openmetadata_payload(desc: dict) -> dict:
    """Optional export (task 5.2): the descriptor as an OpenMetadata CreateDataProduct request body."""
    i = dsc.info(desc)
    return {"name": i["id"], "displayName": i["name"], "description": i["purpose"],
            "domains": [i["category"]], "owners": [{"type": "user", "name": i["owner"]}],
            "extension": {"konmariVersion": i["version"], "freshnessSla": i["sla"] and _iso_duration(i["sla"])}}


def export_openmetadata(cat: Catalog, pid: str, base_url: str, token: str, urlopen_=None) -> dict:
    """PUT the current version to OpenMetadata (`/api/v1/dataProducts`). Not part of the default path."""
    from urllib.request import Request, urlopen

    cur = current(cat, pid)
    if cur is None:
        raise Refused(f"{pid} has no published version to export")
    req = Request(base_url.rstrip("/") + "/api/v1/dataProducts", method="PUT",
                  data=json.dumps(openmetadata_payload(cur["desc"])).encode(),
                  headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with (urlopen_ or urlopen)(req, timeout=30) as r:
        return json.loads(r.read())
