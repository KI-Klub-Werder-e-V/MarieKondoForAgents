"""`konmari monitor run` (design D6-D8): the monitored asset registry from the c02 source inventory and the c08 data
products, freshness with warn/error tiers at 1x/2x the declared update interval, quality checks, drift, OpenLineage
`dataQualityAssertions` facets, optional dbt/Soda imports, and the feedback to scorecard and discard pass."""

import hashlib
import json
import re
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from konmari.canonical import load_yaml
from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.evaluate.golden import event, mask
from konmari.inventory import lineage as ilin
from konmari.monitor import incidents as inc
from konmari.scorecard import probes as sp
from konmari.versions import Refused

DEFAULTS = {"volume_tolerance": 0.2, "metadata_tolerance": 0.05, "duplicate_tolerance": 0.05,
            "drift_threshold": 0.2, "checks": ["freshness", "volume", "metadata_completeness", "duplicate_share",
                                               "schema_change"]}
FACET = "https://openlineage.io/spec/facets/1-0-1/DataQualityAssertionsDatasetFacet.json"
SEVERITY = {"ok": 0, "warn": 1, "error": 2}


def load_config(path=None) -> dict:
    p = Path(path or "monitor.yaml")
    doc = (load_yaml(p) or {}) if p.is_file() else {}
    cfg = {**DEFAULTS, **(doc.get("defaults") or {})}
    cfg["assets"], cfg["notify"], cfg["topics"] = doc.get("assets") or {}, doc.get("notify") or {}, \
        doc.get("topics") or {}
    return cfg


# ---------------------------------------------------------------- registry

def _days(td: timedelta | None) -> float | None:
    return td.total_seconds() / 86400 if td else None


def registry(cat: Catalog, sc: Scope) -> list[dict]:
    """Every in-scope source asset and data product with owner, interval and newest change timestamp."""
    from konmari.connect import descriptor as dsc
    from konmari.connect import products as prd

    out = []
    sources = {s["id"] for s in sc.sources}
    for a in cat.query("SELECT * FROM source_asset WHERE charter_id = ? AND status != 'missing' ORDER BY asset_uri",
                       (sc.charter_id,)):
        if sources and a["source_id"] not in sources:
            continue
        freq = a["update_frequency"]
        out.append({"asset_id": a["asset_uri"], "kind": "source", "namespace": ilin.namespace(a["asset_uri"]),
                    "name": a["title"], "owner": a["owner"], "irregular": freq == "irregular",
                    "interval_days": sp.frequency_days(freq), "last_change": a["last_modified"],
                    "schema": a["schema_hash"]})
    for p in cat.query("SELECT * FROM data_product WHERE charter_id = ? ORDER BY product_id", (sc.charter_id,)):
        cur = prd.current(cat, p["product_id"])
        if cur is None:
            continue
        fields = [[f["name"], f["type"]] for f in dsc.fields(cur["desc"])]
        out.append({"asset_id": f"product:{p['product_id']}", "kind": "product", "namespace": "konmari://products",
                    "name": p["product_id"], "owner": p["owner"], "irregular": False,
                    "interval_days": _days(dsc.sla(cur["desc"])), "last_change": prd.as_of(cat, p["product_id"]),
                    "schema": hashlib.sha256(json.dumps(fields).encode()).hexdigest()[:16], "desc": cur["desc"]})
    for a in out:
        a["reason"] = "missing_owner" if not (a["owner"] or "").strip() or a["owner"] == "unknown" else \
            "missing_update_interval" if a["interval_days"] is None and not a["irregular"] else None
    return out


def _stored(cat: Catalog, asset_id: str) -> dict | None:
    return cat.one("SELECT * FROM monitored_asset WHERE asset_id = ?", (asset_id,))


def set_threshold(cat: Catalog, sc: Scope, asset_id: str, actor: str, warn_after: float | None = None,
                  error_after: float | None = None, reason: str | None = None) -> dict:
    """Only the asset's owner overrides its freshness tiers; audited with old and new values."""
    a = _stored(cat, asset_id)
    if a is None:
        raise Refused(f"'{asset_id}' is not a monitored asset; run 'konmari monitor run' first")
    if actor != a["owner"]:
        raise Refused(f"only the owner of {asset_id} ({a['owner']}) may change its thresholds")
    old = {"warn_after_days": a["warn_after_days"], "error_after_days": a["error_after_days"]}
    new = {"warn_after_days": warn_after if warn_after is not None else a["warn_after_days"],
           "error_after_days": error_after if error_after is not None else a["error_after_days"]}
    cat.execute("UPDATE monitored_asset SET warn_after_days = ?, error_after_days = ? WHERE asset_id = ?",
                (new["warn_after_days"], new["error_after_days"], asset_id))
    event(cat, "monitor.threshold.override", actor, sc, asset=asset_id, owner=actor, previous=old, new=new,
          reason=reason)
    return new


# ---------------------------------------------------------------- checks

def _result(run_id, a, check, status, observed=None, expected=None, details=None, at=None, origin="konmari"):
    return {"result_id": new_id(), "run_id": run_id, "asset_id": a["asset_id"], "dataset": a["name"],
            "namespace": a["namespace"], "check_name": check, "status": status, "observed": observed,
            "expected": expected, "details": details or {}, "origin": origin, "checked_at": at}


def freshness(a: dict, stored: dict | None, run_id: str, at: datetime) -> dict:
    warn = (stored or {}).get("warn_after_days") or a["interval_days"]
    err = (stored or {}).get("error_after_days") or 2 * a["interval_days"]
    exp = {"warn_after_days": round(warn, 3), "error_after_days": round(err, 3)}
    if a["last_change"] is None:
        return _result(run_id, a, "freshness", "warn", None, exp, {"reason": "no_change_timestamp"}, at)
    age = (at - a["last_change"]).total_seconds() / 86400
    status = "error" if age > err else "warn" if age > warn else "ok"
    return _result(run_id, a, "freshness", status, {"age_days": round(age, 2)}, exp, {}, at)


def _previous(cat: Catalog, asset_id: str, check: str):
    r = cat.one("SELECT observed FROM check_result WHERE asset_id = ? AND check_name = ? AND origin = 'konmari'"
                " ORDER BY checked_at DESC LIMIT 1", (asset_id, check))
    return json.loads(r["observed"]) if r and r["observed"] else None


def _items(cat: Catalog, asset_id: str) -> list[str]:
    return [r["item_id"] for r in cat.query("SELECT item_id FROM corpus_item WHERE asset_uri = ? AND state = 'active'"
                                            " ORDER BY item_id", (asset_id,))]


def _rows(cat: Catalog, a: dict) -> list[dict]:
    return [json.loads(r["data"]) for r in cat.query("SELECT data FROM product_row WHERE product_id = ?"
                                                     " ORDER BY row_key", (a["name"],))]


def quality(cat: Catalog, a: dict, run_id: str, cfg: dict, at: datetime) -> list[dict]:
    declared = (cfg["assets"].get(a["asset_id"]) or {}).get("checks") or cfg["checks"]
    tol = {**cfg, **(cfg["assets"].get(a["asset_id"]) or {})}
    personal = inc.personal(cat, a["asset_id"])
    items = _items(cat, a["asset_id"]) if a["kind"] == "source" else None
    rows = _rows(cat, a) if a["kind"] == "product" else None
    out = []
    if "volume" in declared:
        count = len(rows) if rows is not None else len(items)
        prev = _previous(cat, a["asset_id"], "volume")
        if prev is None:
            out.append(_result(run_id, a, "volume", "ok", count, None, {"first_run": True}, at))
        else:
            t = tol["volume_tolerance"]
            lo, hi = round(prev * (1 - t)), round(prev * (1 + t))
            out.append(_result(run_id, a, "volume", "ok" if lo <= count <= hi else "error", count,
                               {"min": lo, "max": hi}, {"previous": prev}, at))
    if "metadata_completeness" in declared:
        if rows is not None:
            from konmari.connect import descriptor as dsc

            req = [f["name"] for f in dsc.fields(a["desc"]) if f["required"]]
            bad_keys = [str(r.get(dsc.key_field(a["desc"]))) for r in rows if any(r.get(f) in (None, "") for f in req)]
            total = len(rows)
        else:
            meta = cat.query("SELECT subject_id, complete FROM chunk_metadata WHERE subject_kind = 'chunk' AND"
                             f" item_id IN ({', '.join('?' * len(items)) or 'NULL'})", tuple(items))
            bad_keys, total = sorted(m["subject_id"] for m in meta if not m["complete"]), len(meta)
        if total:
            share = round(len(bad_keys) / total, 4)
            st = "error" if share > tol["metadata_tolerance"] else "ok"
            out.append(_result(run_id, a, "metadata_completeness", st, share, {"max": tol["metadata_tolerance"]},
                               {"incomplete": len(bad_keys), "total": total,
                                **({"samples": bad_keys[:5]} if bad_keys and not personal else {})}, at))
    if "duplicate_share" in declared:
        if rows is not None:
            from konmari.connect import descriptor as dsc

            key = dsc.key_field(a["desc"])
            seen = Counter(json.dumps({k: v for k, v in r.items() if k != key}, sort_keys=True) for r in rows)
            dups, total = sum(c for c in seen.values() if c > 1), len(rows)
        else:
            flagged = {r["item_id"] for r in cat.query("SELECT item_id FROM flag WHERE flag_type = 'duplicate'")}
            dups, total = len(flagged & set(items)), len(items)
        if total:
            share = round(dups / total, 4)
            out.append(_result(run_id, a, "duplicate_share", "error" if share > tol["duplicate_tolerance"] else "ok",
                               share, {"max": tol["duplicate_tolerance"]}, {"duplicates": dups, "total": total}, at))
    if "schema_change" in declared and a["schema"]:
        prev = _previous(cat, a["asset_id"], "schema_change")
        st = "warn" if prev is not None and prev != a["schema"] else "ok"
        out.append(_result(run_id, a, "schema_change", st, a["schema"], prev, {}, at))
    return out


# ---------------------------------------------------------------- drift

def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"\w+", text.casefold()) if len(w) >= 5 and not w.isdigit()]


def topic_shares(texts: list[str], topics: dict | None = None) -> dict[str, float]:
    """Topic per text from the configured keyword map ('other' if none matches), or word shares without a map."""
    if topics:
        c = Counter(next((t for t, kws in topics.items() if any(k.casefold() in x.casefold() for k in kws)), "other")
                    for x in texts)
    else:
        c = Counter(w for x in texts for w in set(_tokens(x)))
    total = sum(c.values()) or 1
    return {k: round(n / total, 4) for k, n in sorted(c.items())}


def topic_drift(reference: list[str], current: list[str], topics: dict | None = None) -> dict:
    """Total variation distance between the topic shares of the two windows (0 = same, 1 = disjoint)."""
    p, q = topic_shares(reference, topics), topic_shares(current, topics)
    score = round(0.5 * sum(abs(p.get(k, 0) - q.get(k, 0)) for k in set(p) | set(q)), 4)
    new = sorted(k for k in q if k not in p)
    return {"method": "topic_share_tvd", "score": score, "new_topics": new[:20]}


class EvidentlyDrift:
    """Optional: Evidently text drift on the two windows. Install it yourself (`uv pip install evidently==0.7.23
    "pandas<3"`): Evidently 0.7 does not run on pandas 3, which the kit uses, so it is not a declared extra."""
    method = "evidently_text_drift"

    def __call__(self, reference: list[str], current: list[str], topics=None) -> dict:
        import pandas as pd
        from evidently import DataDefinition, Dataset, Report
        from evidently.presets import DataDriftPreset

        dd = DataDefinition(text_columns=["text"])
        ref = Dataset.from_pandas(pd.DataFrame({"text": reference}), data_definition=dd)
        cur = Dataset.from_pandas(pd.DataFrame({"text": current}), data_definition=dd)
        res = Report([DataDriftPreset()]).run(cur, ref).dict()
        score = next((m["value"] for m in res["metrics"] if isinstance(m.get("value"), (int, float))), 0.0)
        return {"method": self.method, "score": round(float(score), 4), "new_topics": []}


def read_log(path: str | Path) -> list[dict]:
    """JSONL with {"ts": ISO timestamp, "text": question or retrieved content}."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out.append({"ts": datetime.fromisoformat(str(r["ts"]).replace("Z", "")), "text": str(r["text"])})
    return out


def drift(cat: Catalog, sc: Scope, logs: dict[str, list[dict]], cfg: dict, actor: str, send=None,
          at: datetime | None = None, window_days: int = 7, method=None) -> list[dict]:
    """Compare the current window with the reference window before it, per subject (questions, content). Texts
    are masked (c07) before anything is counted; drift only warns and asks for golden items, it never blocks."""
    at = at or now()
    cur_from, ref_from = at - timedelta(days=window_days), at - timedelta(days=2 * window_days)
    method = method or topic_drift
    run_id, out = new_id()[:12], []
    for subject, rows in logs.items():
        ref = [mask(r["text"]) for r in rows if ref_from <= r["ts"] < cur_from]
        cur = [mask(r["text"]) for r in rows if cur_from <= r["ts"] <= at]
        if not ref or not cur:
            continue
        d = method(ref, cur, cfg.get("topics") or None)
        thr = cfg["drift_threshold"]
        status = "warn" if d["score"] > thr else "ok"
        row = {"result_id": new_id(), "run_id": run_id, "charter_id": sc.charter_id, "subject": subject,
               "reference_window": f"{ref_from.isoformat()}/{cur_from.isoformat()}",
               "current_window": f"{cur_from.isoformat()}/{at.isoformat()}", "method": d["method"],
               "score": d["score"], "threshold": thr, "status": status,
               "details": json.dumps({"new_topics": d["new_topics"], "reference": len(ref), "current": len(cur)}),
               "checked_at": at}
        cat.execute(f"INSERT INTO drift_result VALUES ({', '.join('?' * len(row))})", tuple(row.values()))
        if status == "warn" and send is not None:
            for o in inc.use_case_owners(sc):
                send.send(inc.contact(sc, o), f"[konmari] {subject} drift: review adding golden items",
                          {"kind": "drift", "check": f"{subject}_drift", "status": "warn", "observed": d["score"],
                           "expected": {"max": thr}, "run_id": run_id,
                           "message": f"new topics {', '.join(d['new_topics']) or '-'}: please review adding golden "
                                      "items for them"})
        event(cat, "monitor.drift", actor, sc, result=status, run_id=run_id, subject=subject, method=d["method"],
              score=d["score"], threshold=thr)
        out.append(row | {"details": json.loads(row["details"])})
    return out


# ---------------------------------------------------------------- OpenLineage

def lineage_event(run_id: str, results: list[dict], at: datetime) -> dict:
    """One RunEvent; every checked dataset carries a `dataQualityAssertions` input facet."""
    by = {}
    for r in results:
        by.setdefault((r["namespace"], r["dataset"]), []).append(
            {"assertion": r["check_name"], "success": r["status"] == "ok"})
    return {"eventType": "COMPLETE", "eventTime": at.isoformat() + "Z", "producer": ilin.PRODUCER,
            "schemaURL": "https://openlineage.io/spec/2-0-2/OpenLineage.json#/$defs/RunEvent",
            "run": {"runId": str(uuid.uuid5(uuid.NAMESPACE_URL, run_id))},
            "job": {"namespace": "konmari", "name": "monitor.run"},
            "inputs": [{"namespace": ns, "name": name, "inputFacets": {"dataQualityAssertions": {
                "_producer": ilin.PRODUCER, "_schemaURL": FACET, "assertions": a}}} for (ns, name), a in by.items()],
            "outputs": []}


# ---------------------------------------------------------------- persistence and the run

def store_results(cat: Catalog, results: list[dict]) -> None:
    for r in results:
        cat.execute("INSERT INTO check_result VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["result_id"], r["run_id"], r["asset_id"], r["check_name"], r["status"],
                     json.dumps(r["observed"]), json.dumps(r["expected"]), json.dumps(r["details"]), r["origin"],
                     r["checked_at"]))


def _upsert(cat: Catalog, sc: Scope, a: dict, fresh: dict | None, at: datetime) -> None:
    old = _stored(cat, a["asset_id"])
    status = fresh and fresh["status"]
    since = (old or {}).get("error_since") if status == "error" and (old or {}).get("freshness_status") == "error" \
        else at if status == "error" else None
    cat.execute("DELETE FROM monitored_asset WHERE asset_id = ?", (a["asset_id"],))
    cat.execute("INSERT INTO monitored_asset VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (a["asset_id"], sc.charter_id, a["kind"], a["namespace"], a["name"], a["owner"], a["interval_days"],
                 (old or {}).get("warn_after_days"), (old or {}).get("error_after_days"), a["reason"] is None,
                 a["reason"], status, a["last_change"], since, at))


def run(cat: Catalog, sc: Scope, cfg: dict, actor: str = "konmari-monitor", send=None, at: datetime | None = None,
        dry_run: bool = False, transport=None) -> dict:
    at = at or now()
    send = send if send is not None else inc.notifier(cfg)
    run_id = new_id()[:12]
    assets = registry(cat, sc)
    results, unmonitorable = [], []
    for a in assets:
        if a["reason"]:
            known = (_stored(cat, a["asset_id"]) or {}).get("reason") == a["reason"]
            unmonitorable.append({"asset": a["asset_id"], "reason": a["reason"], "new": not known})
            if not dry_run:
                _upsert(cat, sc, a, None, at)
            continue
        stored = _stored(cat, a["asset_id"])
        rs = [freshness(a, stored, run_id, at)] if a["interval_days"] else []
        rs += quality(cat, a, run_id, cfg, at)
        results += rs
        if not dry_run:
            _upsert(cat, sc, a, rs[0] if rs and rs[0]["check_name"] == "freshness" else None, at)
    report = {"run_id": run_id, "dry_run": dry_run, "checked_at": at.isoformat(), "assets": len(assets),
              "unmonitorable": unmonitorable,
              "results": [{k: r[k] for k in ("asset_id", "check_name", "status", "observed", "expected")}
                          for r in results]}
    if dry_run:
        return report
    for u in unmonitorable:
        if u.pop("new"):  # reported once, not on every run
            for o in inc.use_case_owners(sc):
                send.send(inc.contact(sc, o), f"[konmari] unmonitorable: {u['asset']} ({u['reason']})",
                          {"asset": u["asset"], "status": "unmonitorable", "kind": u["reason"], "run_id": run_id})
            event(cat, "monitor.unmonitorable", actor, sc, asset=u["asset"], reason=u["reason"])
    store_results(cat, results)
    owners = {a["asset_id"]: a["owner"] for a in assets}
    report["incidents"] = inc.process(cat, sc, run_id, results, send, owners, at)
    report["escalated"] = inc.escalate(cat, sc, send, at)
    report["discard_candidates"] = inc.propose_discard(cat, sc, at)
    ev = lineage_event(run_id, results, at)
    cat.execute("INSERT INTO lineage_event VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), ev["run"]["runId"], "monitor.run", "COMPLETE", at, json.dumps(ev, sort_keys=True)))
    (transport or ilin.default_transport()).emit(ev)
    for a in assets:  # c03 scorecard: currentness criterion reads the latest freshness_alert evidence
        f = next((r for r in results if r["asset_id"] == a["asset_id"] and r["check_name"] == "freshness"), None)
        if a["kind"] == "source" and f:
            sp.record_evidence(cat, a["asset_id"], "freshness_alert", {"status": f["status"]},
                               f"monitor-run:{run_id}", actor, at)
    event(cat, "monitor.run", actor, sc, run_id=run_id, assets=len(assets), results=len(results),
          failing=sum(r["status"] != "ok" for r in results), unmonitorable=len(unmonitorable))
    return report


# ---------------------------------------------------------------- imports (dbt source freshness, Soda)

DBT_STATUS = {"pass": "ok", "warn": "warn", "error": "error", "runtime error": "error"}
SODA_STATUS = {"pass": "ok", "warn": "warn", "fail": "error"}


def import_results(cat: Catalog, sc: Scope, path: str | Path, kind: str, actor: str, send=None,
                   at: datetime | None = None) -> dict:
    """dbt `sources.json` (`dbt source freshness`) or Soda scan results JSON into check_result, matched to
    monitored assets by dataset name (`<source>.<table>` / table name) or asset ID."""
    at = at or now()
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    assets = {a["asset_id"]: a for a in cat.query("SELECT * FROM monitored_asset WHERE charter_id = ?",
                                                    (sc.charter_id,))}
    by_name = {a["name"]: a for a in assets.values()}
    run_id, results, skipped = new_id()[:12], [], []
    if kind == "dbt":
        entries = [(".".join(r["unique_id"].split(".")[-2:]), "freshness", DBT_STATUS.get(r.get("status")),
                    {"age_seconds": r.get("max_loaded_at_time_ago_in_s")}, r.get("criteria")) for r in doc["results"]]
    elif kind == "soda":
        entries = [(c.get("table"), f"soda:{c.get('name')}", SODA_STATUS.get(c.get("outcome")), None,
                    c.get("definition")) for c in doc.get("checks") or []]
    else:
        raise Refused(f"unknown import kind '{kind}' (dbt, soda)")
    for name, check, status, observed, expected in entries:
        a = by_name.get(name) or assets.get(name)
        if a is None or status is None:
            skipped.append(name)
            continue
        results.append(_result(run_id, a, check, status, observed, expected, {}, at, kind))
    store_results(cat, results)
    owners = {a["asset_id"]: a["owner"] for a in assets.values()}
    out = inc.process(cat, sc, run_id, results, send if send is not None else inc.LogNotifier(), owners, at)
    event(cat, "monitor.import", actor, sc, run_id=run_id, kind=kind, results=len(results), skipped=len(skipped))
    return {"run_id": run_id, "imported": len(results), "skipped": skipped, "incidents": out}


def schedule(catalog_url: str | None = None, every_hours: int = 24) -> dict:
    """Cron line and systemd timer for `konmari monitor run`."""
    cmd = f"konmari{' --catalog ' + catalog_url if catalog_url else ''} monitor run"
    cron = "0 6 * * *" if every_hours >= 24 else f"0 */{every_hours} * * *"
    timer = (f"[Unit]\nDescription=KonMari freshness, quality and drift monitoring\n\n[Timer]\nOnBootSec=10min\n"
             f"OnUnitActiveSec={every_hours}h\nUnit=konmari-monitor.service\n\n[Install]\nWantedBy=timers.target\n")
    service = f"[Unit]\nDescription=konmari monitor run\n\n[Service]\nType=oneshot\nExecStart={cmd}\n"
    return {"cron": f"{cron} {cmd}", "timer": timer, "service": service}
