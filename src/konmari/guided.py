"""`konmari next`: where the pilot stands and what to do next. Read-only: no audit events, no state changes
(c10 guided-operation). Commands assume the pilot folder defaults charter.yaml and metrics.yaml."""

import json
import os
import shlex
from pathlib import Path

from konmari import versions as v
from konmari.canonical import canonical_hash, load_yaml
from konmari.catalog import DEFAULT_URL, Catalog, _duckdb_path, connect
from konmari.categories import store as cats
from konmari.charter import store as cs
from konmari.charter.gate import check
from konmari.charter.validation import STEPS, validate_charter
from konmari.inventory import store as inv
from konmari.metrics import store as ms

YOU = "<you>"  # the stakeholder ID of the person operating the kit
CHARTER, METRICS = "charter.yaml", "metrics.yaml"

# Command sequence per step for one category; {c} = category, {a} = its approver. Placeholders in <...>.
STEP_COMMANDS = {
    "discard": ["konmari discard detect --items <items.jsonl> --as <you>", "konmari discard propose --as <you>",
                "konmari discard list", "konmari discard approve <proposal> --as {a}",
                "konmari discard execute --as {a}"],
    "standardize": ["konmari normalize --records <records.jsonl> --specs <normalize/> --as <you>",
                    "konmari normalize quarantine list --as <you>",
                    "konmari policy load --file <policy/entity.yaml> --as <you>",
                    "konmari policy approve <entity> --version <n> --as {a}", "konmari conflicts list --as <you>"],
    "enrich": ["konmari chunk run --dry-run --as <you>", "konmari chunk run --as <you>",
               "konmari enrich run --as <you>", "konmari glossary report"],
    "govern": ["konmari govern policy seed --as <you>", "konmari govern acl-sync --as <you>",
               "konmari govern scan --as <you>", "konmari govern mask --as <you>", "konmari govern check --as <you>"],
    "connect": ['konmari product init <product> --category {c} --owner {a} --use-case "<use case>"',
                "konmari product lint products/<product> --as <you>", "konmari product refresh <product> --as <you>",
                "konmari product test <product>", "konmari product approve <product> --version 1.0.0 --as {a}",
                "konmari product publish <product> --version 1.0.0 --as {a}"],
    "evaluate": ["konmari eval items import <questions.yaml> --as <you>", "konmari eval items approve <item> --as {a}",
                 "konmari eval set activate --as <you>", "konmari eval run --as <you>", "konmari eval gate",
                 "konmari monitor run --as <you>"],
}


def _item(step, status, reasons=(), commands=(), category=None, approval=None, inputs=()) -> dict:
    return {"step": step, "category": category, "status": status, "reasons": list(reasons),
            "commands": list(commands), "approval_needed": approval, "input_needed": list(inputs)}


def _approval(who, role, what) -> dict:
    return {"who": who, "role": role, "what": what}


def _open(cat_url: str | None) -> Catalog | None:
    """The catalog, or None when its DuckDB file does not exist yet (connect() would create it)."""
    url = cat_url or os.environ.get("KONMARI_CATALOG") or DEFAULT_URL
    if not url.startswith(("postgres://", "postgresql://")):
        path = _duckdb_path(url)
        if path != ":memory:" and not Path(path).exists():
            return None
    return connect(url)


# ---------------------------------------------------------------- charter and metric set

def _charter(cat) -> tuple[dict, dict | None, dict | None]:
    """(state, charter doc or None, next item or None)."""
    p = Path(CHARTER)
    if not p.is_file():
        return {"file": CHARTER, "state": "missing"}, None, _item(
            "charter", "needs_input", [f"no {CHARTER} in this folder"],
            ["konmari charter init --template minimal --lang <de|en>", "konmari charter validate"],
            inputs=[{"who": "user", "what": "use case, stakeholders with roles, RACI, data protection screening "
                                            "and data scope: interview the user and fill every <FILL ...>"}])
    doc = load_yaml(p)
    errors = validate_charter(doc) if isinstance(doc, dict) else [f"{CHARTER} is not a YAML mapping"]
    if errors:
        return {"file": CHARTER, "state": "invalid"}, None, _item(
            "charter", "needs_input", errors, ["konmari charter validate"],
            inputs=[{"who": "user", "what": "fix each listed field in charter.yaml"}])
    cid, h = doc["charter_id"], canonical_hash(doc)
    row = v.latest(cat, cs.T, cid) if cat else None
    acc = doc["raci"]["charter_approval"]["A"][0]
    state = {"file": CHARTER, "charter_id": cid, "version": row["version"] if row else None,
             "state": row["status"] if row else "unregistered"}
    if row and row["status"] == "approved":
        if row["content_hash"] == h:
            return state, doc, None
        state["state"] = "changed"
        return state, doc, _item(
            "charter", "needs_approval", [f"{CHARTER} differs from approved version {row['version']}"],
            [f'konmari charter amend --reason "<reason>" --as {YOU}', f"konmari charter approve --as {acc}"],
            approval=_approval(acc, "Accountable for charter_approval",
                               f"approve the amended charter (version {row['version'] + 1})"))
    comp = doc["compliance"]
    ai = comp["ai_act"]["classification"]
    compliance = next((s["id"] for s in doc["stakeholders"] if "compliance" in s["roles"]), None)
    if comp["dpia_screening"]["result"] == "pending" or ai == "pending":
        what = [x for x, pending in [("DPIA screening", comp["dpia_screening"]["result"] == "pending"),
                                     ("EU AI Act classification", ai == "pending")] if pending]
        return state, doc, _item("charter", "needs_input", [f"{w} is 'pending'" for w in what],
                                 ["konmari charter validate"],
                                 inputs=[{"who": compliance, "what": f"decide {w} in charter.yaml"} for w in what])
    if ai == "prohibited":
        return state, doc, _item("charter", "blocked", ["EU AI Act classification is 'prohibited'; this charter "
                                                        "cannot be approved"])
    version = row["version"] if row else 1
    if ai == "high" and not (cat and row and cat.one(
            "SELECT id FROM approval WHERE subject = 'ai_act_high' AND charter_id = ? AND version = ?"
            " AND content_hash = ?", (cid, row["version"], h))):
        return state, doc, _item(
            "charter", "needs_approval", ["EU AI Act classification is 'high'; compliance must sign off first"],
            ["konmari charter validate", f"konmari charter approve --as {compliance} --compliance-signoff"],
            approval=_approval(compliance, "compliance", f"sign off AI Act 'high' for charter version {version}"))
    return state, doc, _item(
        "charter", "needs_approval", [f"charter version {version} is not approved"],
        ["konmari charter validate", f"konmari charter approve --as {acc}"],
        approval=_approval(acc, "Accountable for charter_approval", f"approve charter version {version}"))


def _baselines(cat, cid, metrics: dict) -> list[dict]:
    out = []
    for m in metrics["metrics"]:
        if m["tier"] != "must_have":
            continue
        rows = cat.query("SELECT value, plan_date FROM metric_measurement WHERE charter_id = ? AND metric_id = ?"
                         " AND kind = 'baseline' ORDER BY recorded_at DESC", (cid, m["id"])) if cat else []
        st = "measured" if any(r["value"] is not None for r in rows) else ("planned" if rows and rows[0]["plan_date"]
                                                                           else "missing")
        out.append({"metric": m["id"], "owner": m["owner"], "state": st, "name": m["name"], "unit": m["unit"],
                    "method": m.get("method")})
    return out


def _baseline_item(step, base: list[dict], reasons, category=None) -> dict:
    todo = [b for b in base if b["state"] != "measured"]
    cmds = [f'konmari metrics baseline {b["metric"]} --value <value> --method "<method>" --as {b["owner"]}'
            for b in todo]
    if step == "metrics":  # a measurement plan is enough for the inventory step only
        cmds += [f"konmari metrics baseline {b['metric']} --not-yet-measured --plan-date <YYYY-MM-DD> --as "
                 f"{b['owner']}" for b in todo if b["state"] == "missing"]
    return _item(step, "needs_input", reasons, cmds, category, inputs=[
        {"who": b["owner"], "metric": b["metric"],
         "what": f"as-is baseline for '{b['name']}' ({b['unit']}), measured without the agent: {b['method']}"}
        for b in todo])


def _metrics(cat, charter: dict) -> tuple[dict, list[dict], dict | None]:
    p = Path(METRICS)
    if not p.is_file():
        return {"file": METRICS, "state": "missing"}, [], _item(
            "metrics", "needs_input", [f"no {METRICS} in this folder"], ["konmari metrics validate"],
            inputs=[{"who": "user", "what": "success metrics with target, owner and tier, plus a golden question "
                                            "set (see examples/verein-faq-assistant/metrics.yaml)"}])
    doc = load_yaml(p)
    errors = ms.check(doc, charter, p) if isinstance(doc, dict) else [f"{METRICS} is not a YAML mapping"]
    if errors:
        return {"file": METRICS, "state": "invalid"}, [], _item(
            "metrics", "needs_input", errors, ["konmari metrics validate"],
            inputs=[{"who": "user", "what": "fix each listed field in metrics.yaml or the golden set"}])
    row = v.latest(cat, ms.T, charter["charter_id"]) if cat else None
    acc = charter["raci"]["metric_approval"]["A"][0]
    state = {"file": METRICS, "version": row["version"] if row else None,
             "state": row["status"] if row else "unregistered"}
    base = _baselines(cat, charter["charter_id"], doc)
    state["baselines"] = [{k: b[k] for k in ("metric", "owner", "state")} for b in base]
    if row and row["status"] == "approved" and row["content_hash"] != canonical_hash(doc):
        state["state"] = "changed"
        return state, base, _item(
            "metrics", "needs_approval", [f"{METRICS} differs from approved metric set version {row['version']}"],
            [f'konmari metrics amend --reason "<reason>" --as {YOU}', f"konmari metrics approve --as {acc}"],
            approval=_approval(acc, "Accountable for metric_approval",
                               f"approve the amended metric set (version {row['version'] + 1})"))
    if not row or row["status"] != "approved":
        version = row["version"] if row else 1
        return state, base, _item(
            "metrics", "needs_approval", [f"metric set version {version} is not approved"],
            ["konmari metrics validate", f"konmari metrics approve --as {acc}"],
            approval=_approval(acc, "Accountable for metric_approval", f"approve metric set version {version}"))
    if missing := [b for b in base if b["state"] == "missing"]:
        return state, base, _baseline_item(
            "metrics", base, [f"must_have metric '{b['metric']}' has no baseline or measurement plan" for b in missing])
    return state, base, None


# ---------------------------------------------------------------- inventory (pilot-wide until the handoff)

def _q(s: str) -> str:
    return shlex.quote(str(s))


def _inventory(cat, sc) -> dict | None:
    registered = {s["source_id"] for s in inv.sources(cat, sc.charter_id)}
    if todo := [s for s in sc.sources if s["id"] not in registered]:
        return _item("inventory", "ready", [f"charter source '{s['id']}' is not registered" for s in todo]
                     + ["add --secret-ref env:NAME when a source needs a read-only credential (never the value)"],
                     [f"konmari inventory source add --connector {s['connector']} --locator {_q(s['locator'])} "
                      f"--as {YOU}" for s in todo])
    counts = {r["source_id"]: r["n"] for r in cat.query(
        "SELECT source_id, COUNT(*) AS n FROM source_asset WHERE charter_id = ? GROUP BY source_id", (sc.charter_id,))}
    # shortcut: a source whose scan found nothing keeps asking for a scan; empty sources are rare in a pilot
    if todo := [s for s in sc.sources if not counts.get(s["id"])]:
        return _item("inventory", "ready", [f"source '{s['id']}' has no inventory records yet" for s in todo],
                     [f"konmari inventory import {_q(s['locator'])} --as {YOU}" if s["connector"] == "csv-import"
                      else f"konmari inventory scan --source {s['id']} --as {YOU}" for s in todo])
    if blockers := inv.gate_blockers(cat, sc.charter_id):
        compliance = next((sid for sid, s in sc.stakeholders.items() if "compliance" in s["roles"]), None)
        flags = {"owner": "--owner <owner>", "update_frequency": "--update-frequency <P1M>",
                 "format": "--format <media-type>"}
        reasons, cmds, inputs, approval = [], [], [], None
        for r in blockers:
            miss = [m["field"] for m in json.loads(r["missing_fields"])]
            reasons.append(f"incomplete: {r['asset_uri']} (missing: {', '.join(miss)})")
            if f := [flags[m] for m in miss if m in flags]:
                cmds.append(f"konmari inventory set {_q(r['asset_uri'])} {' '.join(f)} --as {YOU}")
                inputs.append({"who": "user", "what": f"{', '.join(m for m in miss if m in flags)} of "
                                                      f"{r['asset_uri']}"})
            if "sensitivity_tier" in miss:
                who = r["owner"] if r["owner"] in sc.stakeholders else compliance
                if r["sensitivity_tier"] in (None, "", "unknown"):
                    cmds.append(f'konmari inventory tier {_q(r["asset_uri"])} --set <tier> --reason "<reason>" '
                                f"--as {who}")
                else:
                    cmds.append(f"konmari inventory tier {_q(r['asset_uri'])} --confirm --as {who}")
                approval = approval or _approval(who, "owner of the asset or compliance",
                                                 f"confirm the sensitivity tier of {r['asset_uri']}")
        return _item("inventory", "needs_input" if inputs else "needs_approval", reasons, cmds, approval=approval,
                     inputs=inputs)
    recs = inv.records(cat, sc.charter_id)
    prim = {r["asset_uri"]: cats.primary(cat, r["asset_uri"]) for r in recs if r["status"] != "missing"}
    if unassigned := {u: p for u, p in prim.items() if not p or p["state"] != "accepted"}:
        if not cat.one("SELECT 1 AS x FROM asset_category WHERE asset_uri IN (SELECT asset_uri FROM source_asset"
                       " WHERE charter_id = ?) LIMIT 1", (sc.charter_id,)):
            return _item("inventory", "ready", [f"{len(unassigned)} asset(s) without a category"],
                         [f"konmari categories suggest --as {YOU}"])
        first = next(iter(sc.categories))
        cmds, approval = [], None
        for u, p in unassigned.items():
            c = p["category_id"] if p else None
            who = sc.approver(c) if c in sc.categories else sc.approver(first)
            cmds.append(f"konmari categories accept {_q(u)} --as {who}" if p else
                        f"konmari categories assign {_q(u)} <category> --as {who}")
            approval = approval or _approval(who, "category approver (data_scope.categories)",
                                             "accept or change each suggested primary category")
        return _item("inventory", "needs_approval",
                     [f"unassigned: {u}" + (f" (suggested: {p['category_id']})" if p else "")
                      for u, p in unassigned.items()], cmds, approval=approval)
    approver = sc.approver(next(iter(sc.categories)))
    if cats.approved_order(cat, sc.charter_id) is None:
        rows = cats.order_versions(cat, sc.charter_id)
        if not rows or rows[-1]["status"] != "proposed":
            return _item("inventory", "ready", ["no category order proposed"],
                         [f"konmari categories order --propose --as {YOU}"])
        return _item("inventory", "needs_approval", [f"category order version {rows[-1]['version']} is proposed, "
                                                     f"not approved: {', '.join(rows[-1]['categories'])}"],
                     ["konmari categories order", f"konmari categories approve --as {approver}"],
                     approval=_approval(approver, "category approver (data_scope.categories)",
                                        f"approve category order version {rows[-1]['version']}"))
    if [c for c in sc.categories if cats.step_status(cat, sc.charter_id, c, "inventory")["status"] != "complete"]:
        return _item("inventory", "ready", ["the handoff to step 3 has not run"],
                     [f"konmari categories handoff --as {YOU}"])
    return None


# ---------------------------------------------------------------- steps 3-9 per category

def _categories(cat, sc) -> tuple[str, list[dict]]:
    order = cats.approved_order(cat, sc.charter_id)
    seq = [c for c in order["categories"] if c in sc.categories] if order else list(sc.categories)
    rows = []
    for c in seq:
        rows.append({"category": c, "assets": len(cats.members(cat, sc, c, accepted_only=True)),
                     "steps": {s: cats.step_status(cat, sc.charter_id, c, s)["status"] for s in STEPS}})
    return (f"approved v{order['version']}" if order else "charter (not approved)"), rows


def _step(cat, sc, base, step, c, status) -> dict:
    a = sc.approver(c)
    if unmet := check(step, CHARTER, cat).unmet:
        return _baseline_item(step, base, unmet, c)
    if why := cats.check_step(cat, sc, c, step):
        return _item(step, "blocked", why, [f'konmari categories step {c} {step} --waive --reason "<reason>" '
                                            f"--as {a}"], c,
                     _approval(a, f"approver of category {c}", f"waive the category order for {step}"))
    if step == "score":
        if status == "not_started":
            return _item(step, "ready", [f"category {c} has not been scored"], [f"konmari score --as {YOU}"], c)
        r = sc.reviewers(c)[0] if sc.reviewers(c) else a
        return _item(step, "needs_approval", [f"proposed verdicts in {c} wait for review"],
                     ["konmari review list --status proposed", f"konmari review accept <asset> --as {r}",
                      f"konmari review accept --all-keep --category {c} --as {r}"], c,
                     _approval(r, f"reviewer of category {c}", "accept, override or reverse each proposed verdict"))
    cmds = [t.format(c=c, a=a) for t in STEP_COMMANDS[step]]
    return _item(step, "ready", [f"step '{step}' is {status} for category {c}"],
                 cmds + [f"konmari categories step {c} {step} --status complete --as {a}"], c,
                 _approval(a, f"approver of category {c}", f"mark step '{step}' complete for {c} once its "
                                                           "results are reviewed"))


def _done(base) -> dict:
    return _item("evaluate", "done", ["every step is complete for every category with assets"],
                 [f'konmari metrics measure {b["metric"]} --kind final --step evaluate --value <value> '
                  f'--method "<method>" --as {b["owner"]}' for b in base]
                 + ["konmari metrics report", f"konmari monitor run --as {YOU}"])


# ---------------------------------------------------------------- report

def report(cat_url: str | None = None) -> dict:
    cat = _open(cat_url)
    try:
        out = {"charter": None, "metrics": None, "category_order": None, "categories": [], "next": None}
        out["charter"], doc, nxt = _charter(cat)
        if nxt is None:
            out["metrics"], base, nxt = _metrics(cat, doc)
        if nxt is None:
            sc = cs.scope(doc["charter_id"], cat)
            out["category_order"], out["categories"] = _categories(cat, sc)
            nxt = _inventory(cat, sc)
            for step in STEPS[1:] if nxt is None else []:
                c = next((r for r in out["categories"] if r["assets"] and r["steps"][step] != "complete"), None)
                if c:
                    nxt = _step(cat, sc, base, step, c["category"], c["steps"][step])
                    break
            nxt = nxt or _done(base)
        out["next"] = nxt
        return out
    finally:
        if cat:
            cat.close()


def render(rep: dict) -> str:
    ch, me, n = rep["charter"], rep["metrics"], rep["next"]
    lines = [f"charter {ch.get('charter_id') or ch['file']}: {ch['state']}"
             + (f" (version {ch['version']})" if ch.get("version") else "")]
    if me:
        lines.append(f"metric set: {me['state']}" + (f" (version {me['version']})" if me.get("version") else ""))
        for b in me.get("baselines", []):
            lines.append(f"  baseline {b['metric']} (owner {b['owner']}): {b['state']}")
    if rep["categories"]:
        lines.append(f"categories (order: {rep['category_order']}):")
        for i, c in enumerate(rep["categories"], 1):
            lines.append(f"  {i}. {c['category']} ({c['assets']} asset(s)): "
                         + ", ".join(f"{s} {st}" for s, st in c["steps"].items()))
    lines.append(f"next: {n['step']}" + (f" for {n['category']}" if n["category"] else "") + f": {n['status']}")
    lines += [f"  reason: {r}" for r in n["reasons"]]
    lines += [f"  run: {c}" for c in n["commands"]]
    if a := n["approval_needed"]:
        lines.append(f"  approval needed: {a['who']} ({a['role']}): {a['what']}")
    lines += [f"  input needed from {i['who']}: {i['what']}" for i in n["input_needed"]]
    return "\n".join(lines) + "\n"
