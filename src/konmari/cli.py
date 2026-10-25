"""`konmari` command line. Exit codes: 0 ok, 1 validation failed / refused / gate failed, 2 usage error."""

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

import yaml

from konmari import guided
from konmari import versions as v
from konmari.canonical import dump_yaml, load_yaml
from konmari.catalog import audit_change, connect
from konmari.charter import store as cs
from konmari.charter.gate import gate
from konmari.charter.validation import STEPS, roles_by_id, validate_charter
from konmari.chunking import store as cst
from konmari.enrich import store as est
from konmari.enrich.providers import OpenAICompatible
from konmari.glossary import store as gls
from konmari.governance import access as gaccess
from konmari.governance import acl as gacl
from konmari.governance import audit as gaudit
from konmari.governance import config as gcfg
from konmari.governance import gate as ggate
from konmari.governance import pii as gpii
from konmari.governance import policy as gpol
from konmari.governance import records as grec
from konmari.categories import store as cats
from konmari.catalog import verify_chain
from konmari.discard import calibrate as dcal
from konmari.discard import corpus as dcorp
from konmari.discard import decisions as dd
from konmari.discard import detect as ddet
from konmari.discard import events as dev
from konmari.discard import report as drep
from konmari.discard import retention as dret
from konmari.discard.archive import JsonIndex, archive_for
from konmari.inventory import lineage
from konmari.inventory import sensitivity as sens
from konmari.inventory import store as inv
from konmari.metrics import store as ms
from konmari.normalize import store as nst
from konmari.policy import conflicts as pcf
from konmari.policy import golden as pgold
from konmari.policy import store as pst
from konmari.resolve import linkage as erl
from konmari.scorecard import engine as se
from konmari.scorecard import probes as sp
from konmari.scorecard import report as sr
from konmari.scorecard import review as sv
from konmari.versions import Refused


def _fail(lines) -> int:
    for line in lines if isinstance(lines, list) else [lines]:
        print(f"error: {line}", file=sys.stderr)
    return 1


def _member(doc: dict, sid: str) -> str:
    if sid not in roles_by_id(doc):
        raise Refused(f"'{sid}' is not in the stakeholder register")
    return sid


# ---------------------------------------------------------------- charter

def charter_init(a, cat_url):
    out = Path(a.output)
    if out.exists() and not a.force:
        return _fail(f"{out} exists; use --force to overwrite")
    text = (files("konmari") / "charter" / "templates" / f"charter.{a.template}.{a.lang}.yaml").read_text()
    out.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({a.template} template, {a.lang}); fill every <FILL ...> placeholder, then run "
          "'konmari charter validate'")
    return 0


def charter_validate(a, cat_url):
    doc = load_yaml(Path(a.file))
    errors = validate_charter(doc)
    if errors:
        return _fail([f"{a.file}: {e}" for e in errors])
    row = cs.register(connect(cat_url), doc, "konmari-validate")
    print(f"{a.file}: charter '{doc['charter_id']}' is valid; catalog version {row['version']} ({row['status']})")
    return 0


def charter_approve(a, cat_url):
    doc = cs.load_charter(a.file)
    row = cs.approve(connect(cat_url), doc, a.as_, a.compliance_signoff)
    if a.compliance_signoff:
        print(f"compliance sign-off for AI Act classification 'high' recorded on version {row['version']}")
    else:
        print(f"charter '{doc['charter_id']}' version {row['version']} approved by {a.as_} "
              f"(sha256 {row['content_hash']})")
    return 0


def charter_amend(a, cat_url):
    cat = connect(cat_url)
    doc = cs.load_charter(a.file)
    _member(doc, a.as_)
    row = cs.amend(cat, None if a.from_version else doc, a.reason, a.as_, doc["charter_id"], a.from_version)
    if a.from_version:
        Path(a.file).write_text(dump_yaml(row["doc"]), encoding="utf-8")
        print(f"{a.file} reset to the content of version {a.from_version}")
    print(f"version {row['version']} created in status draft (reason: {a.reason}); re-approval required")
    return 0


def charter_show(a, cat_url):
    cat = connect(cat_url)
    cid = cs.load_charter(a.file)["charter_id"]
    if a.log:
        rows = cs.decision_log(cat, cid)
        if not rows:
            print("decision log is empty")
        for r in rows:
            print(f"- {r['meeting_date']} {r['meeting_type']} (v{r['charter_version']}, by {r['author']}, "
                  f"logged {r['logged_at']:%Y-%m-%d %H:%M})")
            print(f"  attendees: {', '.join(r['attendees'])}")
            for d in r["decisions"]:
                print(f"  decision: {d}")
            for act in r["actions"]:
                print(f"  action: {act['action']} (owner: {act['owner']})")
        return 0
    row = v.get(cat, cs.T, cid, a.version) if a.version else v.latest(cat, cs.T, cid)
    if row is None:
        return _fail(f"charter '{cid}' version {a.version or 'latest'} not found")
    print(f"# charter {cid} version {row['version']} ({row['status']}), sha256 {row['content_hash']}")
    print(f"# created {row['created_at']:%Y-%m-%d %H:%M} by {row['created_by']}; reason: {row['reason']}")
    for ap in cs.approvals(cat, cid, row["version"]):
        print(f"# approval: {ap['subject']} by {ap['approver_id']} ({ap['role']}, os user {ap['os_user']}) "
              f"at {ap['approved_at']:%Y-%m-%d %H:%M}")
    print(dump_yaml(row["doc"]), end="")
    return 0


def charter_diff(a, cat_url):
    cat = connect(cat_url)
    cid = cs.load_charter(a.file)["charter_id"]
    old, new = v.get(cat, cs.T, cid, a.old), v.get(cat, cs.T, cid, a.new)
    if not old or not new:
        return _fail("both versions must exist")
    changes = v.diff(old["doc"], new["doc"])
    print("\n".join(changes) if changes else "no differences")
    return 0


def charter_log(a, cat_url):
    doc = cs.load_charter(a.file)
    _member(doc, a.as_)
    actions = []
    for item in a.action or []:
        owner, _, text = item.partition(":")
        if not text:
            return _fail(f"--action '{item}': use OWNER:TEXT")
        actions.append({"owner": _member(doc, owner.strip()), "action": text.strip()})
    attendees = [s.strip() for s in ",".join(a.attendees).split(",") if s.strip()]
    entry = cs.log_decision(connect(cat_url), doc["charter_id"], a.as_, a.meeting, a.date, attendees,
                            a.decision, actions)
    print(f"decision-log entry stored for {entry['meeting_date']} ({entry['meeting_type']}), "
          f"charter version {entry['charter_version']}")
    return 0


def charter_status(a, cat_url):
    st = cs.status(connect(cat_url), cs.load_charter(a.file)["charter_id"])
    print(f"charter {st['charter_id']}: version {st['version']} ({st['status']}); metric set: {st['metric_set']}")
    print(f"last steering meeting: {st['last_steering'] or 'none logged'}")
    if st["cadence_breach"]:
        print(f"WARNING cadence breach: {st['cadence_breach']}")
    return 0


def charter_gate(a, cat_url):
    res = gate(a.step, a.file, connect(cat_url))
    if a.json:
        print(json.dumps(res.__dict__))
    elif res.passed:
        print(f"gate passed for step '{a.step}': charter {res.charter_id} version {res.charter_version}")
    for u in [] if a.json else res.unmet:
        print(f"unmet: {u}", file=sys.stderr)
    return 0 if res.passed else 1


def next_step(a, cat_url):
    rep = guided.report(cat_url)
    print(json.dumps(rep, indent=2, ensure_ascii=False) if a.json else guided.render(rep), end="\n" if a.json else "")
    return 0


# ---------------------------------------------------------------- metrics

def _metrics_ctx(a):
    charter = cs.load_charter(a.charter)
    metrics = load_yaml(Path(a.file))
    if not isinstance(metrics, dict):
        raise Refused(f"{a.file}: not a metrics file")
    return charter, metrics


def metrics_validate(a, cat_url):
    charter, metrics = _metrics_ctx(a)
    errors = ms.check(metrics, charter, a.file)
    if errors:
        return _fail([f"{a.file}: {e}" for e in errors])
    row = ms.register(connect(cat_url), metrics, charter, a.file, "konmari-validate")
    print(f"{a.file}: valid; stored as metric set version {row['version']} ({row['status']}) "
          f"for charter {charter['charter_id']}")
    return 0


def metrics_approve(a, cat_url):
    charter, metrics = _metrics_ctx(a)
    row = ms.approve(connect(cat_url), metrics, charter, a.file, a.as_)
    print(f"metric set version {row['version']} approved by {a.as_}")
    return 0


def metrics_amend(a, cat_url):
    charter, metrics = _metrics_ctx(a)
    _member(charter, a.as_)
    row, lowered = ms.amend(connect(cat_url), metrics, charter, a.file, a.reason, a.as_)
    print(f"metric set version {row['version']} created in status draft; re-approval required")
    for m in lowered:
        print(f"FLAG target_lowered_after_measurement: {m}")
    return 0


def metrics_baseline(a, cat_url):
    charter, metrics = _metrics_ctx(a)
    if (a.value is None) == (not a.not_yet_measured):
        return _fail("give either --value (with --method) or --not-yet-measured with --plan-date")
    ms.record(connect(cat_url), charter["charter_id"], a.metric, "baseline", a.as_, a.value, a.method, a.unit,
              a.date, a.plan_date, golden_set_hash=ms.golden_hash(metrics, a.file))
    print(f"baseline for {a.metric} recorded" + (" as not_yet_measured" if a.value is None else f": {a.value}"))
    return 0


def metrics_measure(a, cat_url):
    charter, metrics = _metrics_ctx(a)
    ms.record(connect(cat_url), charter["charter_id"], a.metric, a.kind, a.as_, a.value, a.method, a.unit, a.date,
              step=a.step, golden_set_hash=ms.golden_hash(metrics, a.file))
    print(f"{a.kind} measurement for {a.metric} at step {a.step} recorded: {a.value}")
    return 0


def metrics_report(a, cat_url):
    rep = ms.report(connect(cat_url), cs.load_charter(a.charter)["charter_id"])
    print(ms.to_json(rep) if a.format == "json" else ms.render_md(rep), end="\n" if a.format == "json" else "")
    return 0


# ---------------------------------------------------------------- inventory

def _scope(a, cat):
    try:
        sc = cs.scope(cs.load_charter(a.charter)["charter_id"], cat)
    except LookupError as e:
        raise Refused(f"{e}; approve the charter first") from None
    if getattr(a, "as_", None):
        _member({"stakeholders": list(sc.stakeholders.values())}, a.as_)
    return sc


def inventory_source_add(a, cat_url):
    cat = connect(cat_url)
    row = inv.register_source(cat, _scope(a, cat), a.connector, a.locator, a.as_, a.secret_ref, a.title)
    print(f"source '{row['source_id']}' ({row['connector']} {row['locator']}) registered for charter "
          f"{row['charter_id']} v{row['charter_version']}")
    return 0


def inventory_scan(a, cat_url):
    cat = connect(cat_url)
    res = gate("inventory", a.charter, cat, a.as_)
    if not res.passed:
        return _fail([f"gate: {u}" for u in res.unmet])
    out = inv.scan(cat, _scope(a, cat), a.source, actor=a.as_ or "konmari-scan", sample_size=a.sample_size,
                   threshold=a.threshold, transport=lineage.default_transport())
    if not out:
        return _fail("no scannable source registered; run 'konmari inventory source add' first")
    for sid, c in out.items():
        print(f"{sid}: " + ", ".join(f"{k} {v}" for k, v in c.items()))
    return 0


def inventory_import(a, cat_url):
    cat = connect(cat_url)
    counts, errors = inv.import_file(cat, _scope(a, cat), a.file, a.as_, lineage.default_transport())
    print(f"{a.file}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    for e in errors:
        print(f"error: {a.file} {e}", file=sys.stderr)
    return 1 if errors else 0


def inventory_set(a, cat_url):
    cat = connect(cat_url)
    pdc = [p.strip() for p in a.personal_data_categories.split(",") if p.strip()] \
        if a.personal_data_categories is not None else None
    rec = inv.set_fields(cat, _scope(a, cat), a.asset, {
        "owner": a.owner, "steward": a.steward, "update_frequency": a.update_frequency, "format": a.format,
        "ropa_ref": a.ropa_ref, "ropa_missing": True if a.ropa_missing else None,
        "access_rights_note": a.access_rights_note, "personal_data_categories": pdc}, a.as_, a.reason,
        lineage.default_transport())
    print(f"{a.asset}: {rec['status']}")
    return 0


def inventory_tier(a, cat_url):
    if bool(a.confirm) == bool(a.set):
        return _fail("give either --confirm or --set TIER --reason ...")
    cat = connect(cat_url)
    rec = inv.set_tier(cat, _scope(a, cat), a.asset, a.as_, a.set, a.reason)
    print(f"{a.asset}: tier {rec['sensitivity_tier']} confirmed by {a.as_}; record {rec['status']}")
    return 0


def inventory_waive(a, cat_url):
    cat = connect(cat_url)
    inv.waive(cat, _scope(a, cat), a.asset, a.as_, a.reason)
    print(f"{a.asset}: waiver recorded by {a.as_}")
    return 0


def inventory_report(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    s = inv.summary(cat, sc.charter_id)
    if a.csv:
        inv.write_csv(a.csv, s["records"])
    if not a.gate:
        print(inv.render_md(sc.charter_id, s), end="")
        return 0
    blockers = inv.gate_blockers(cat, sc.charter_id)
    for r in blockers:
        miss = "; ".join(f"{m['field']}: {m['reason']}" for m in json.loads(r["missing_fields"]))
        print(f"incomplete: {r['asset_uri']} ({miss})", file=sys.stderr)
    if blockers:
        return _fail(f"inventory gate failed: {len(blockers)} incomplete record(s) without waiver")
    print(f"inventory gate passed: {len(s['records'])} record(s), completeness {s['completeness_pct']} %")
    return 0


def inventory_export(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    recs = inv.records(cat, sc.charter_id)
    prim = {r["asset_uri"]: p["category_id"] for r in recs if (p := cats.primary(cat, r["asset_uri"]))
            and p["state"] == "accepted"}
    text = json.dumps(inv.dcat_jsonld(recs, prim), indent=2, ensure_ascii=False)
    if a.output:
        Path(a.output).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.output} ({len(recs)} datasets, DCAT 3 JSON-LD)")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------- categories

def _cat_ctx(a, cat_url):
    cat = connect(cat_url)
    return cat, _scope(a, cat)


def categories_suggest(a, cat_url):
    cat, sc = _cat_ctx(a, cat_url)
    rows = cats.suggest(cat, sc, a.as_)
    for r in rows:
        print(f"suggested {r['category']} for {r['asset_uri']} (rule {r['rule_id']}, matched '{r['matched']}')")
    print(f"{len(rows)} suggestion(s); accept, change or reject each one")
    return 0


def categories_decide(a, cat_url):
    cat, sc = _cat_ctx(a, cat_url)
    if a.cmd == "accept":
        cats.accept(cat, sc, a.asset, a.as_, a.reason)
    elif a.cmd == "reject":
        cats.reject(cat, sc, a.asset, a.as_, a.reason)
    elif a.cmd == "demote":
        cats.demote(cat, sc, a.asset, a.as_, a.reason)
    elif a.cmd == "revert":
        print(f"{a.asset}: primary category reverted to {cats.revert(cat, sc, a.asset, a.as_, a.to, a.reason)}")
        return 0
    else:
        cats.assign(cat, sc, a.asset, a.category, a.as_, not a.secondary, a.reason)
    print(f"{a.asset}: {a.cmd} recorded")
    return 0


def categories_show(a, cat_url):
    cat, sc = _cat_ctx(a, cat_url)
    if not a.category:
        for c in cats.categories(cat, sc, active_only=False):
            n = len(cats.members(cat, sc, c["category_id"]))
            print(f"{c['category_id']} ({c['status']}, baseline {c['baseline_tier']}, {n} asset(s)): {c['name']}"
                  f" - {c['description']}")
        return 0
    v = cats.view(cat, sc, a.category)
    print(f"# {a.category}")
    for system, assets in v["by_system"].items():
        print(f"{system}:")
        for x in assets:
            role = "primary" if x["is_primary"] else "secondary"
            print(f"  - {x['asset_uri']} ({x['title']}) [{role}, {x['state']}]")
    for h in v["overlaps"]:
        print(f"overlap hint ({h['kind']}): {' <-> '.join(h['assets'])}")
    return 0


def categories_manage(a, cat_url):
    cat, sc = _cat_ctx(a, cat_url)
    if a.cmd == "add":
        cats.add(cat, sc, a.id, a.name, a.description, a.baseline_tier, a.as_)
    elif a.cmd == "rename":
        cats.rename(cat, sc, a.id, a.name, a.as_)
    else:
        cats.retire(cat, sc, a.id, a.as_, a.reason)
    print(f"category {a.id}: {a.cmd} recorded")
    return 0


def _print_version(r):
    by = f", approved by {r['approved_by']} at {r['approved_at']:%Y-%m-%d %H:%M}" if r["approved_by"] else ""
    print(f"version {r['version']} ({r['status']}{by}); reason: {r['reason']}")
    print("weights (our assumptions, configurable): " + ", ".join(f"{k}={v}" for k, v in r["weights"].items()))
    for i, c in enumerate(r["categories"], 1):
        s = r["scores"][c]
        inputs = ", ".join(f"{k}={v}" for k, v in s["inputs"].items())
        print(f"{i}. {c}: score {s['score']} ({inputs})" + (" special-category: last" if s["special_category"] else ""))


def categories_order(a, cat_url):
    cat, sc = _cat_ctx(a, cat_url)
    if a.propose or a.set:
        if not a.as_:
            return _fail("--as is required to propose or change the order")
        if a.set:
            row = cats.set_order(cat, sc, [c.strip() for c in a.set.split(",")], a.as_, a.reason)
        else:
            weights = {}
            for item in a.weight or []:
                k, _, val = item.partition("=")
                weights[k.strip()] = float(val)
            row = cats.propose(cat, sc, a.as_, weights)
        _print_version(row)
        print("approve with: konmari categories approve --as <approver>")
        return 0
    rows = cats.order_versions(cat, sc.charter_id)
    for r in rows if a.version is None else [r for r in rows if r["version"] == a.version]:
        _print_version(r)
    return 0 if rows else _fail("no category order yet; run 'konmari categories order --propose --as <id>'")


def categories_approve(a, cat_url):
    cat, sc = _cat_ctx(a, cat_url)
    row = cats.approve(cat, sc, a.as_, a.version)
    print(f"category order version {row['version']} approved by {a.as_}: {', '.join(row['categories'])}")
    return 0


def categories_handoff(a, cat_url):
    cat, sc = _cat_ctx(a, cat_url)
    order, reasons = cats.handoff(cat, sc, a.as_)
    if order is None:
        return _fail(reasons)
    print("category sequence: " + ", ".join(order))
    return 0


def categories_step(a, cat_url):
    cat, sc = _cat_ctx(a, cat_url)
    if a.waive:
        cats.waive_step(cat, sc, a.category, a.step, a.as_, a.reason)
        print(f"waiver recorded for {a.category}/{a.step}")
    elif a.status:
        cats.set_step(cat, sc, a.category, a.step, a.status, a.as_)
        print(f"{a.category}/{a.step}: {a.status}")
    else:
        reasons = cats.check_step(cat, sc, a.category, a.step)
        st = cats.step_status(cat, sc.charter_id, a.category, a.step)["status"]
        print(f"{a.category}/{a.step}: {st}; " + ("may start" if not reasons else "refused"))
        return _fail(reasons) if reasons else 0
    return 0


# ---------------------------------------------------------------- scorecard

def _print_run(s) -> None:
    print(f"scorecard run {s['run_id']} (rubric {s['rubric_version']}, charter {s['charter_id']} "
          f"v{s['charter_version']}, trigger {s['trigger']})")
    for v in s["scored"]:
        why = f" ({v['reason']}{': ' + v['reason_ref'] if v['reason_ref'] else ''})" if v["reason"] else ""
        fixes = f"; fix: {', '.join(i['criterion'] for i in v['fix_items'])}" if v["fix_items"] else ""
        print(f"proposed {v['proposed']}{why}: {v['asset_uri']}{fixes}")
    for o in s["out_of_scope"]:
        print(f"out of scope: {o['asset_uri']} ({o['reason']})")
    for c, reasons in s["blocked"].items():
        print(f"not scored, category {c}: " + "; ".join(reasons))
    print(f"{len(s['scored'])} scored, {len(s['out_of_scope'])} out of scope; review with 'konmari review list'")


def score_run(a, cat_url):
    cat = connect(cat_url)
    cfg = se.load_config(a.config)
    only = None
    if a.changed:
        only = se.changed_assets(cat, _scope(a, cat))
        if not only:
            print("no inventory changes since the last run")
            return 0
    _print_run(se.score(cat, a.charter, a.as_ or "konmari-score", cfg, a.rubric, only=only,
                        trigger="inventory_change" if a.changed else "manual"))
    return 0


def _actor_in_register(a, cat):
    sc = _scope(a, cat)
    _member({"stakeholders": list(sc.stakeholders.values())}, a.as_)
    return sc


def score_evidence(a, cat_url):
    cat = connect(cat_url)
    _actor_in_register(a, cat)
    inv.get(cat, a.asset)
    payload = json.loads(a.data)
    if a.kind == "acl_snapshot" and "principals" in payload:  # raw ACL: keep only tokens, counts and a hash
        payload = sp.acl_payload(payload["principals"], payload.get("anonymous_links", 0))
    row = sp.record_evidence(cat, a.asset, a.kind, payload, a.ref, a.as_, a.captured_at)
    print(f"{a.asset}: {a.kind} evidence recorded ({row['evidence_ref']}, captured {row['captured_at']:%Y-%m-%d %H:%M})")
    return 0


def score_ingest(a, cat_url):
    cat = connect(cat_url)
    sc = _actor_in_register(a, cat)
    rows, skipped = (sp.ingest_soda if a.soda else sp.ingest_odcs)(cat, sc.charter_id, a.soda or a.odcs, a.as_)
    for r in rows:
        print(f"{r['asset_uri']}: {r['kind']} {r['payload']}")
    for s in skipped:
        print(f"skipped: {s}", file=sys.stderr)
    print(f"{len(rows)} evidence row(s) recorded")
    return 0


def score_irrelevant(a, cat_url):
    cat = connect(cat_url)
    sc = _actor_in_register(a, cat)
    inv.get(cat, a.asset)
    sp.record_evidence(cat, a.asset, "relevance", {"relevant": False, "marked_by": a.as_}, f"expert:{a.as_}", a.as_)
    audit_change(cat, "scorecard.mark_irrelevant", a.asset, None, {"relevant": False}, actor=a.as_, reason=a.reason,
                 charter_id=sc.charter_id, step="score")
    print(f"{a.asset}: marked as not needed for the pilot by {a.as_}; the next 'konmari score' proposes discard")
    return 0


def score_fix_done(a, cat_url):
    cat = connect(cat_url)
    _actor_in_register(a, cat)
    _print_run(se.complete_fix(cat, a.asset, a.criterion, a.as_, a.charter, cfg=se.load_config(a.config)))
    return 0


def score_alert(a, cat_url):
    cat = connect(cat_url)
    _actor_in_register(a, cat)
    sp.record_evidence(cat, a.asset, "freshness_alert", {"status": a.status}, a.ref, a.as_)
    _print_run(se.rescore(cat, a.asset, "freshness_alert", a.as_, a.charter, {"status": a.status},
                          cfg=se.load_config(a.config)))
    return 0


def review_list(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.asset:
        for r in se.reviews(cat, a.asset):
            print(f"{r['reviewed_at']:%Y-%m-%d %H:%M} {r['action']} by {r['actor']}: {r['from_verdict'] or '-'} -> "
                  f"{r['to_verdict']} (rubric {r['rubric_version']})" +
                  (f"; justification: {r['justification']}" if r["justification"] else ""))
        st = sv.status(cat, a.asset)
        print(f"status {st['status']}, effective verdict {st['effective'] or 'none'}")
        return 0
    for cid, assets in sorted(se.split_scope(cat, sc)[0].items()):
        for x in assets:
            st = sv.status(cat, x["asset_uri"])
            if st["verdict"] is None or (a.status and st["status"] != a.status):
                continue
            unknown = " [unknown criteria]" if st["verdict"]["has_unknown"] else ""
            print(f"{x['asset_uri']} ({cid}): proposed {st['proposed']}, status {st['status']}, effective "
                  f"{st['effective'] or 'none'}{unknown}")
    return 0


def review_act(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.cmd == "accept":
        if a.all_keep:
            done, skipped = sv.bulk_accept(cat, sc, a.as_, a.category)
            print(f"accepted {len(done)} keep proposal(s); {len(skipped)} need a one-by-one review")
            return 0
        if not a.asset:
            return _fail("give an asset or --all-keep")
        sv.accept(cat, sc, a.asset, a.as_)
    elif a.cmd == "override":
        sv.override(cat, sc, a.asset, a.to, a.justification, a.as_)
    else:
        sv.reverse(cat, sc, a.asset, a.to, a.justification, a.as_)
    st = sv.status(cat, a.asset)
    print(f"{a.asset}: {a.cmd} recorded by {a.as_}; verdict {st['effective']} ({st['status']})")
    return 0


def scorecard_report(a, cat_url):
    cat = connect(cat_url)
    rep = sr.build(cat, _scope(a, cat))
    out = sr.to_json(rep) + "\n" if a.format == "json" else sr.render_csv(rep, a.level) if a.format == "csv" \
        else sr.render_md(rep)
    if a.output:
        Path(a.output).write_text(out, encoding="utf-8")
        print(f"wrote {a.output}")
    else:
        print(out, end="")
    return 0


def scorecard_compare(a, cat_url):
    lines = sr.compare(connect(cat_url), a.run_a, a.run_b, a.allow_rubric_mismatch)
    print("\n".join(lines) if lines else "no differences")
    return 0


# ---------------------------------------------------------------- discard

def discard_detect(a, cat_url):
    cat = connect(cat_url)
    rep = ddet.detect(cat, a.charter, a.as_ or "konmari-discard", ddet.load_config(a.config), a.items)
    warn = [w for w in [dret.overdue(cat, rep["charter_id"])] if w]
    md, js = drep.write(rep, a.report_dir, warn)
    print(drep.render_md(rep, warn), end="")
    print(f"report written to {md} and {js}")
    return 0


def discard_calibrate(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.sample:
        pairs = dcal.sample(cat, sc.charter_id, a.detector, a.sample)
        out = Path(a.output)
        dcal.write_sample(cat, pairs, out, out.with_suffix(".html"))
        print(f"{len(pairs)} pair(s) written to {out} (fill the label column: {', '.join(dcal.LABELS)}); "
              f"side-by-side view: {out.with_suffix('.html')}")
        return 0
    if a.labels:
        print(f"{dcal.record_labels(cat, sc, a.labels, a.as_)} label(s) stored for {a.detector}")
    if a.override_reason:
        dcal.override(cat, sc, a.detector, a.threshold, a.override_reason, a.as_)
        print(f"override recorded for {a.detector} by {a.as_}: {a.override_reason}")
    elif a.threshold is not None:
        row = dcal.select(cat, sc, a.detector, a.threshold, a.as_)
        print(f"threshold {a.threshold} selected for {a.detector} by {a.as_} ({row['labels']} labels)")
    n, m = dcal.metrics(cat, a.detector, (*dcal.REPORT_THRESHOLDS, *([a.threshold] if a.threshold else [])))
    print(f"{a.detector}: {n} labeled pair(s)")
    for t, pr in m.items():
        print(f"  threshold {t}: precision {pr['precision']}, recall {pr['recall']} ({pr['predicted']} predicted)")
    return 0


def _print_proposal(p):
    act = p["decided_action"] or p["action"]
    changed = f" (proposed {p['action']})" if act != p["action"] else ""
    print(f"{p['proposal_id']} {p['status']} {act}{changed} {p['item_id']} [{p['category_id']}, {p['method']}]: "
          f"{p['reason']}")


def discard_propose(a, cat_url):
    cat = connect(cat_url)
    res = dd.propose(cat, _scope(a, cat), a.as_)
    for p in res["created"]:
        _print_proposal(p)
    for d, n in res["blocked"].items():
        print(f"blocked: {n} {d} flag(s) create no proposals; calibration missing for detector '{d}' "
              f"(konmari discard calibrate --detector {d} ...)")
    for c, why in res["waiting"].items():
        print(f"waiting: category {c} gets no proposals yet: {'; '.join(why)}")
    print(f"{len(res['created'])} proposal(s) created; nothing changes until they are approved and executed")
    return 0


def discard_list(a, cat_url):
    cat = connect(cat_url)
    for p in dd.list_proposals(cat, _scope(a, cat).charter_id, a.status):
        _print_proposal(p)
    return 0


def discard_decide(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    p = dd.decide(cat, sc, a.proposal, a.as_, "reject" if a.cmd == "reject" else "approve",
                  getattr(a, "action", None), a.comment, getattr(a, "compliance", False), ddet.load_config(a.config))
    _print_proposal(p)
    return 0


def discard_bulk(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    ids = dd.bulk_approve(cat, sc, a.action, a.category, a.method, a.as_, a.yes, a.comment)
    for i in ids:
        print(i)
    print(f"{len(ids)} proposal(s) " + ("approved" if a.yes else "would be approved; repeat with --yes to confirm"))
    return 0


def discard_cancel(a, cat_url):
    cat = connect(cat_url)
    _print_proposal(dd.cancel(cat, _scope(a, cat), a.proposal, a.as_, a.comment))
    return 0


def discard_request(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    _print_proposal(dd.create_proposal(cat, sc, dcorp.require(cat, a.item), a.action, "request", [], a.as_, a.reason))
    return 0


def discard_off_scope(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    dcorp.require(cat, a.item)
    cat.execute("INSERT INTO flag VALUES (?, NULL, 'off-scope', 'reviewer', ?, '[]', NULL, ?, ?, NULL, ?, ?)",
                (dd.new_id(), a.item, json.dumps({"category": a.reason}), json.dumps({"reason": "reviewer"}),
                 dd.now(), a.as_))
    dev.log(cat, sc, "mark_off_scope", a.item, None, None, a.as_, reason=a.reason)
    print(f"{a.item}: flagged off-scope by {a.as_}; the next 'konmari discard propose' proposes exclude")
    return 0


def discard_execute(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    res = dd.execute(cat, sc, a.as_, archive_for(a.archive), a.proposal, lineage.default_transport(),
                     JsonIndex(a.index) if a.index else None)
    for r in res:
        print(f"{r['proposal_id']} {r['action']} {r['item_id']}: {r['result']}"
              + (f" ({r['message']})" if r.get("message") else ""))
    return 1 if any(r["result"] in ("failed", "refused") for r in res) else 0


def discard_restore(a, cat_url):
    cat = connect(cat_url)
    dd.restore(cat, _scope(a, cat), a.item, a.as_, archive_for(a.archive))
    print(f"{a.item}: restored to active (checksum verified)")
    return 0


def discard_history(a, cat_url):
    for r in dev.history(connect(cat_url), a.item):
        d = r["details"]
        print(f"{r['ts']:%Y-%m-%d %H:%M:%S} {r['action']} by {r['actor']} ({r['result']}): "
              f"{d.get('prior_state')} -> {d.get('new_state')}"
              + (f", action {d['action']}" if d.get("action") else ""))
    return 0


def discard_log(a, cat_url):
    cat = connect(cat_url)
    if a.op == "verify":
        bad = verify_chain(cat)
        if bad:
            return _fail(f"decision log chain broken at event {bad['seq']} (id {bad['id']}, {bad['action']})")
        n = cat.one("SELECT COUNT(*) AS n FROM audit_event WHERE seq IS NOT NULL")["n"]
        print(f"decision log verified: {n} chained event(s)")
        return 0
    rows = cat.query("SELECT * FROM audit_event WHERE seq IS NOT NULL ORDER BY seq")
    text = "".join(json.dumps(r, default=str, sort_keys=True) + "\n" for r in rows)
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"wrote {a.output} ({len(rows)} events)")
    else:
        print(text, end="")
    return 0


def discard_propagation(a, cat_url):
    cat = connect(cat_url)
    left = dd.check_propagation(cat, _scope(a, cat).charter_id, JsonIndex(a.index) if a.index else None)
    for iid, ids in left.items():
        print(f"{iid}: {', '.join(ids)}")
    if left:
        return _fail(f"{sum(map(len, left.values()))} derived artifact(s) of discarded items are still present")
    print("propagation check passed: no derived artifacts of discarded items remain")
    return 0


def discard_report(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    rep = drep.latest(cat, sc.charter_id, a.run)
    if rep is None:
        return _fail("no detection run yet; run 'konmari discard detect' first")
    warn = [w for w in [dret.overdue(cat, sc.charter_id)] if w]
    print(json.dumps(rep | {"warnings": warn}, indent=2, sort_keys=True) if a.format == "json"
          else drep.render_md(rep, warn), end="\n" if a.format == "json" else "")
    return 0


# ---------------------------------------------------------------- retention

def retention_rule(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.op == "add":
        r = dret.add_rule(cat, sc, a.category, a.purpose, a.legal_basis, a.source, a.start_event, a.period,
                          a.expiry_action, a.as_)
        print(f"rule {r['rule_id']} for {a.category} registered (proposed); compliance approves with "
              f"'konmari retention rule approve {r['rule_id']} --as <id>'")
    elif a.op == "approve":
        r = dret.approve_rule(cat, sc, a.rule, a.as_)
        print(f"rule {r['rule_id']} for {r['category_id']} approved by {a.as_} at {r['approved_at']:%Y-%m-%d}")
    else:
        for r in dret.rules(cat, sc.charter_id):
            by = f", by {r['approver']}" if r["approver"] else ""
            print(f"{r['rule_id']} {r['category_id']} ({r['status']}{by}):"
                  f" {r['period']} from {r['start_event']}, then {r['expiry_action']}; source {r['source']}; "
                  f"basis {r['legal_basis']}; purpose {r['purpose']}")
    return 0


def retention_hold(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.op == "add":
        h = dret.add_hold(cat, sc, a.reason, a.owner, a.review_date, a.as_, a.item, a.category)
        print(f"legal hold {h['hold_id']} recorded on {a.item or a.category}")
    elif a.op == "release":
        dret.release_hold(cat, sc, a.hold, a.as_)
        print(f"legal hold {a.hold} released")
    else:
        for h in cat.query("SELECT * FROM legal_hold WHERE charter_id = ? AND released_at IS NULL", (sc.charter_id,)):
            print(f"{h['hold_id']} {h['item_id'] or h['category_id']}: {h['reason']} (owner {h['owner']}, "
                  f"review {h['review_date']})")
    return 0


def retention_check(a, cat_url):
    cat = connect(cat_url)
    for p in dret.check(cat, _scope(a, cat), a.as_, a.window_days, a.schedule):
        _print_proposal(p)
    print("retention check recorded")
    return 0


# ---------------------------------------------------------------- standardize (c05)

def _json(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def normalize_run(a, cat_url):
    if not a.records:
        return _fail("--records is required (JSONL: source, object, record_id, values)")
    cat = connect(cat_url)
    _scope(a, cat)
    rep = nst.run(cat, a.records, a.specs, a.as_ or "konmari-normalize")
    if rep["skipped"]:
        print(f"run {rep['run_id']} already done for this input and spec version {rep['spec_version']}; "
              "nothing written")
    else:
        print(f"run {rep['run_id']} (spec version {rep['spec_version']}): {rep['canonical']} canonical, "
              f"{rep['quarantined']} quarantined")
    bad = [sf for sf, c in rep["conformance"].items() if not c["conformant"]]
    for sf, c in rep["conformance"].items():
        print(f"  {sf}: canonical {c['canonical_rate']:.1%}, quarantined {c['quarantine_rate']:.1%} "
              f"{c['reasons'] or ''}")
    return _fail([f"{sf}: {rep['conformance'][sf]['unaccounted']} unaccounted value(s)" for sf in bad]) if bad else 0


def normalize_conformance(a, cat_url):
    conf = nst.conformance(connect(cat_url), a.run)
    _json(conf)
    bad = [f"{sf}: {c['unaccounted']} unaccounted value(s)" for sf, c in conf.items() if not c["conformant"]]
    return _fail(bad) if bad else 0


def normalize_quarantine(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.op == "list":
        for q in nst.quarantine_list(cat, sc, a.as_, None if a.status == "all" else a.status):
            print(f"{q['qid']} {q['status']} {q['source_field']} {q['record_id']}: {q['raw']!r} -> {q['reason']} "
                  f"({q['detail']}; owner {q['owner']})")
        return 0
    if a.op == "resolve":
        q = nst.resolve_quarantine(cat, sc, a.qid, a.as_, a.value, a.unfixable, a.spec_change, a.reason)
    else:
        q = nst.revert_quarantine(cat, sc, a.qid, a.as_, a.reason)
    print(f"{q['qid']} is {q['status']}")
    return 0


def normalize_contract(a, cat_url):
    cat = connect(cat_url)
    if a.op == "export":
        spec = nst.latest_spec(cat) or nst.load_specs(cat, a.specs, "konmari-contract")
        text = dump_yaml(nst.odcs_contract(spec))
        if a.output:
            Path(a.output).write_text(text, encoding="utf-8")
            print(f"wrote {a.output}")
        else:
            print(text, end="")
        return 0
    failures = nst.check_contract(cat, load_yaml(Path(a.contract)), a.run or nst.latest_run_id(cat))
    if failures:
        return _fail(failures)
    print("all data-contract quality rules pass")
    return 0


def normalize_rollback(a, cat_url):
    cat = connect(cat_url)
    _scope(a, cat)
    rep = nst.rollback(cat, a.run, a.as_, a.reason)
    print(f"run {rep['run_id']} rolled back: {rep['values_removed']} canonical value(s) removed; raw data untouched")
    return 0


def resolve_run(a, cat_url):
    cat = connect(cat_url)
    rep = erl.run(cat, _scope(a, cat), a.entity, erl.load_config(a.config), a.as_)
    print(f"run {rep['run_id']}: {rep['records']} records, {rep['auto']} auto-linked pair(s), {rep['review']} in the "
          f"review queue, {rep['clusters']} cluster(s) (thresholds {rep['upper']} / {rep['lower']})")
    return 0


def resolve_review(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.op == "list":
        for r in erl.queue(cat, a.entity):
            print(f"{r['probability']:.4f} {r['rec_a']} {r['rec_b']}")
    elif a.op == "export":
        print(f"{erl.export_review(cat, a.entity, a.output, a.sample)} pair(s) written to {a.output}; fill the label "
              "column with match or non_match")
    elif a.op == "import":
        print(f"{erl.import_review(cat, sc, a.file, a.as_)} label(s) stored")
    else:
        lab = "match" if a.op == "accept" else "non_match"
        erl.label_pairs(cat, sc, a.entity, [(a.rec_a, a.rec_b, lab)], a.as_, a.reason)
        print(f"{a.rec_a} / {a.rec_b}: {lab}")
    return 0


def resolve_publish(a, cat_url):
    cat = connect(cat_url)
    rep = erl.publish(cat, _scope(a, cat), a.entity, a.as_, erl.read_known(a.known) if a.known else None,
                      a.override_recall_reason)
    print(f"published {a.entity}: precision {rep['precision']}, recall {rep['recall']} "
          f"(floors {rep['floors']['precision']} / {rep['floors']['recall']})")
    return 0


def resolve_split(a, cat_url):
    cat = connect(cat_url)
    rep = erl.split(cat, _scope(a, cat), a.entity, a.cluster, a.records.split(","), a.as_, a.reason)
    print(f"split {rep['split_id']}: {', '.join(rep['clusters'])} ({rep['links_cut']} link(s) cut)")
    return 0


def resolve_restore(a, cat_url):
    cat = connect(cat_url)
    rep = erl.restore(cat, _scope(a, cat), a.split, a.as_, a.reason)
    print(f"restored {', '.join(rep['clusters'])}")
    return 0


def _exposure(path):
    return pst.read_exposure(path) if path and Path(path).exists() else None


def policy_load(a, cat_url):
    cat = connect(cat_url)
    row = pst.load(cat, _scope(a, cat), a.file, a.as_)
    impact = json.loads(row["impact"])
    print(f"policy {row['entity']} version {row['version']} is {row['status']}"
          f"{' (active)' if row['active'] else ''}; dry run: {len(impact)} golden value(s) would change")
    for i in impact[:a.show]:
        print(f"  {i['cluster_id']} {i['field']}: {i['from']!r} -> {i['to']!r} ({i['status']})")
    if len(impact) > a.show:
        print(f"  ... {len(impact) - a.show} more (konmari policy load --show N)")
    return 0


def policy_approve(a, cat_url):
    cat = connect(cat_url)
    row = pst.approve(cat, _scope(a, cat), a.entity, a.version, a.as_)
    print(f"policy {a.entity} version {row['version']} approved by {a.as_} and active")
    return 0


def policy_rollback(a, cat_url):
    cat = connect(cat_url)
    pst.rollback(cat, _scope(a, cat), a.entity, a.to, a.as_, a.reason)
    print(f"policy {a.entity} rolled back to version {a.to}; golden records recomputed")
    return 0


def policy_check(a, cat_url):
    cat = connect(cat_url)
    exposed = _exposure(a.exposure)
    if exposed is None:
        return _fail(f"{a.exposure}: no exposure declaration (data-products / mcp-exposure fields)")
    missing = [m for ms in pst.coverage(cat, exposed).values() for m in ms]
    if missing:
        return _fail([f"{m} is exposed to agents but has no precedence rule" for m in missing])
    print("every agent-exposed field has a precedence rule")
    return 0


def policy_build(a, cat_url):
    cat = connect(cat_url)
    rep = pgold.build(cat, _scope(a, cat), a.entity, a.as_ or "konmari-golden", _exposure(a.exposure))
    print(f"golden records for {a.entity} (policy version {rep['policy_version']}): {rep['clusters']} cluster(s), "
          f"{rep['unresolved']} unresolved attribute(s)")
    return 0


def policy_golden(a, cat_url):
    cat = connect(cat_url)
    fields = [a.field] if a.field else [g["field"] for g in pgold.record(cat, a.entity, a.cluster)]
    _json([pgold.serve(cat, a.entity, a.cluster, f) for f in fields])
    return 0


def conflicts_list(a, cat_url):
    cat = connect(cat_url)
    for c in pcf.listing(cat, _scope(a, cat), a.as_, a.status):
        cands = "; ".join(f"{x['source']}={x['value']!r}" for x in c["candidates"])
        print(f"{c['conflict_id']} {c['status']} {c['entity']}.{c['field']} {c['cluster_id']} [{cands}] owner "
              f"{c['owner']}, due {c['due_date']}{', personal data' if c['personal_data'] else ''}")
    return 0


def conflicts_decide(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.cmd == "revert":
        c = pcf.revert(cat, sc, a.conflict, a.as_, a.reason)
    else:
        c = pcf.decide(cat, sc, a.conflict, a.as_, a.source, a.value, getattr(a, "ticket", None),
                       a.reason if a.cmd == "accept" else None)
    print(f"{c['conflict_id']} is {c['status']}")
    return 0


def conflicts_report(a, cat_url):
    cat = connect(cat_url)
    rows = pcf.overdue(cat) if a.overdue else pcf.entries(cat)
    for c in rows:
        print(f"{c['conflict_id']} {c['status']} {c['entity']}.{c['field']} owner {c['owner']} due {c['due_date']}"
              + (f", {c['age_days']} days old" if a.overdue else "")
              + (", personal data (DSGVO Art. 5(1)(d), Art. 16)" if c["personal_data"] else ""))
    if not rows:
        print("no overdue conflicts" if a.overdue else "conflict register is empty")
    return 0


def conflicts_export(a, cat_url):
    print(f"{pcf.export_tickets(connect(cat_url), a.output)} write-back ticket(s) written to {a.output}")
    return 0


# ---------------------------------------------------------------- structure and enrich (c06)

def _enrich_scope(a, cat):
    """c06 steps pass the readiness gate first and act as a named stakeholder."""
    res = gate("enrich", a.charter, cat, getattr(a, "as_", None))
    if not res.passed:
        raise Refused(res.unmet)
    return _scope(a, cat)


def chunk_run(a, cat_url):
    cat = connect(cat_url)
    rep = cst.run(cat, _enrich_scope(a, cat), a.as_ or "konmari-chunk", a.dry_run)
    if a.json:
        _json(rep)
    else:
        print(cst.render_report(rep), end="")
    return 0


def chunk_profile(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    if a.op == "show":
        _json({c: cst.profile(cat, sc.charter_id, c) for c in ([a.category] if a.category else sc.categories)})
        return 0
    changes = {k: v for k, v in {"max_tokens": a.max_tokens, "min_tokens": a.min_tokens,
                                 "overlap_fallback": a.overlap_fallback, "model_max_tokens": a.model_max_tokens,
                                 "flag_tokens": a.flag_tokens, "tokenizer": a.tokenizer, "parser": a.parser,
                                 "llm_context": a.llm_context}.items() if v is not None}
    if a.approved_processor:
        changes["approved_processors"] = a.approved_processor
    if not changes:
        return _fail("nothing to change; give e.g. --max-tokens 600")
    _json(cst.set_profile(cat, sc, a.category, changes, a.as_, a.reason))
    return 0


def chunk_rollback(a, cat_url):
    cat = connect(cat_url)
    rep = cst.rollback(cat, _scope(a, cat), a.run, a.as_, a.reason)
    print(f"run {a.run} rolled back; previous chunk sets active again for {len(rep['items'])} item(s)")
    return 0


def _model(a):
    if not getattr(a, "llm_url", None):
        return None
    return OpenAICompatible(a.llm_url, a.llm_model, a.processor)


def enrich_run(a, cat_url):
    cat = connect(cat_url)
    rep = est.run(cat, _enrich_scope(a, cat), a.as_ or "konmari-enrich", model=_model(a),
                  emit_lineage=not a.no_lineage)
    rep.pop("lineage_event", None)
    if a.json:
        _json(rep)
    else:
        print(est.render_report(rep), end="")
    return 0


def enrich_override(a, cat_url):
    cat = connect(cat_url)
    o = est.override(cat, _scope(a, cat), a.subject, a.field, a.value, a.as_, a.reason)
    print(f"{o['subject']} {o['field']}: {o['previous']!r} -> {o['new']!r} (kept by later runs)")
    return 0


def enrich_lineage(a, cat_url):
    _json(est.lineage(connect(cat_url), a.chunk))
    return 0


def enrich_rollback(a, cat_url):
    cat = connect(cat_url)
    rep = est.rollback(cat, _scope(a, cat), a.run, a.as_, a.reason)
    print(f"run {a.run} rolled back; {rep['restored']} metadata row(s) restored")
    return 0


def enrich_schema(a, cat_url):
    cat = connect(cat_url)
    if a.op == "load":
        changed = est.load_schema(cat, _scope(a, cat), a.file, a.as_)
        print(f"{len(changed)} column annotation(s) changed")
        return 0
    cov = {t: est.schema_coverage(cat, t) for t in est.exposed_tables(cat)}
    _json(cov)
    gaps = [f"{t} column {c}: {', '.join(g)}" for t, v in cov.items() for c, g in v["gaps"].items()]
    return _fail(gaps) if gaps else 0


def glossary_cmd(a, cat_url):
    cat = connect(cat_url)
    sc = _scope(a, cat)
    op, actor = a.op, getattr(a, "as_", None)
    if op == "stewards":
        print(f"stewards: {', '.join(gls.set_stewards(cat, sc, a.steward, actor)['stewards'])}")
        return 0
    if op == "add":
        t = gls.add(cat, sc, gls.read_term_file(a.file), actor)
    elif op == "edit":
        changes = gls.read_term_file(a.file) if a.file else {}
        if a.definition:
            changes["definition"] = a.definition
        t = gls.edit(cat, sc, a.term, changes, actor)
    elif op == "submit":
        t = gls.submit(cat, sc, a.term, actor)
    elif op in ("approve", "reject"):
        t = gls.decide(cat, sc, a.term, actor, op == "approve", a.comment)
    elif op == "deprecate":
        t = gls.deprecate(cat, sc, a.term, actor, a.replaced_by, a.comment)
    elif op == "revert":
        t = gls.revert(cat, sc, a.term, a.to, actor, a.reason)
    elif op == "draft-definition":
        t = gls.ai_draft(cat, sc, a.term, _model(a), actor, a.approved_processor or [])
    elif op == "harvest":
        qs = [q["question"] for q in (load_yaml(Path(a.questions)) or {}).get("questions", [])] \
            if a.questions and Path(a.questions).exists() else []
        for c in gls.harvest(cat, sc, actor, qs, a.top, lang=a.lang):
            print(f"{c['count']:>4} {c['label']} -> draft {c['term_id']} (chunks {c.get('chunks', 0)}, columns "
                  f"{c.get('columns', 0)}, questions {c.get('questions', 0)})")
        return 0
    elif op == "conflict":
        c = gls.resolve_conflict(cat, sc, a.conflict, actor, a.decision)
        print(f"{c['conflict_id']} resolved")
        return 0
    elif op == "report":
        _json(gls.report(cat, sc.charter_id))
        return 0
    elif op == "export":
        text = gls.export_skos(cat, sc.charter_id) if a.format == "skos" else \
            json.dumps(gls.export_json(cat, sc.charter_id), indent=2, ensure_ascii=False)
        if a.output:
            Path(a.output).write_text(text, encoding="utf-8")
            print(f"wrote {a.output}")
        else:
            print(text)
        return 0
    else:  # show
        t = gls.term(cat, a.term)
        _json({**t, "status": gls.status(t), "links": gls.links(cat, a.term)})
        return 0
    print(f"{t['term_id']}: {gls.status(t)} (latest version {t['latest']['version']} {t['latest']['status']}, "
          f"exposed version {t['exposed_version'] or '-'})")
    return 0


# ---------------------------------------------------------------- govern and audit (c07)

def _gov_scope(a, cat):
    """c07 steps that change the catalog pass the readiness gate first, like c06."""
    res = gate("govern", a.charter, cat, getattr(a, "as_", None))
    if not res.passed:
        raise Refused(res.unmet)
    return _scope(a, cat)


def _charter_doc(a):
    p = Path(getattr(a, "charter", "charter.yaml"))
    return load_yaml(p) if p.is_file() else None


def _gov_cfg(a):
    cfg = gcfg.load(a.config)
    if errs := gcfg.validate(cfg, _charter_doc(a)):
        raise Refused(errs)
    return cfg


def audit_cmd(a, cat_url):
    cat = connect(cat_url)
    if a.op == "verify":
        bad = gaudit.verify(cat)
        if bad:
            return _fail(f"audit chain broken at event {bad['seq'] or '(unchained)'} (id {bad['id']}, "
                         f"{bad['action']})")
        n = cat.one("SELECT COUNT(*) AS n FROM audit_event")["n"]
        print(f"audit trail verified: one chain, {n} event(s) from all steps")
        return 0
    if a.op == "purge":
        _scope(a, cat)
        rep = gaudit.purge(cat, gcfg.load(a.config), a.as_, _charter_doc(a))
        print(f"purged {rep['deleted']} event(s) older than {rep['cutoff']}"
              + (f"; checkpoint written (last purged seq {rep['to_seq']})" if rep["deleted"] else ""))
        return 0
    from datetime import datetime

    sc = _scope(a, cat)
    if a.subject and "compliance" not in sc.stakeholders[a.as_].get("roles", []):
        return _fail(f"'{a.as_}' does not hold the compliance role")
    text, n = gaudit.export(cat, a.as_, a.subject, a.from_ and datetime.fromisoformat(a.from_),
                            a.to and datetime.fromisoformat(a.to), a.format)
    Path(a.output).write_text(text, encoding="utf-8")
    print(f"wrote {a.output} ({n} events, {a.format}); the export is recorded in the audit trail")
    return 0


def _policy_changes(a) -> dict:
    ch = dict(load_yaml(Path(a.file)) or {}) if a.file else {}
    for k in ("exposure", "sensitivity", "acl_sync_cadence", "owner", "legal_basis"):
        if getattr(a, k, None) is not None:
            ch[k] = getattr(a, k)
    if a.tool:
        ch["tools"] = a.tool
    for m in a.mask or []:
        ent, _, op = m.partition("=")
        ch.setdefault("masking", {})[ent] = op
    if a.allow:
        ch["principals"] = {"allow": a.allow, "deny": a.deny or []}
    if a.service_principal:
        ch["service_principals"] = a.service_principal
    return ch


def govern_policy(a, cat_url):
    cat = connect(cat_url)
    if a.op == "show":
        sc = _scope(a, cat)
        rows = [r for p in ([a.policy] if a.policy else sc.categories) for r in gpol.versions(cat, p)]
        _json([{k: r[k] for k in ("policy_id", "version", "status", "doc", "created_by", "approved_by",
                                  "reverted_from")} for r in rows])
        return 0
    sc = _gov_scope(a, cat)
    if a.op == "seed":
        rows = gpol.seed(cat, sc, a.as_)
        print(f"{len(rows)} draft polic{'y' if len(rows) == 1 else 'ies'} created (pending): "
              + ", ".join(r["policy_id"] for r in rows))
        return 0
    if a.op == "edit":
        r = gpol.edit(cat, sc, a.policy, _policy_changes(a), a.as_, a.reason)
    elif a.op == "approve":
        r = gpol.approve(cat, sc, a.policy, a.version, a.as_, a.reason)
    else:
        r = gpol.revert(cat, sc, a.policy, a.to, a.as_, a.reason)
    print(f"policy {r['policy_id']} version {r['version']}: {r['status']}")
    return 0


def govern_grant(a, cat_url):
    cat = connect(cat_url)
    g = gpol.grant(cat, _gov_scope(a, cat), a.agent, a.category, a.tool, a.as_, a.days,
                   gcfg.load(a.config)["grants"]["max_days"])
    print(f"write grant {g['grant_id']}: {a.agent} may use {a.tool} on {a.category} until {g['expires_at']:%Y-%m-%d}")
    return 0


def govern_agent(a, cat_url):
    cat = connect(cat_url)
    r = gaccess.register_agent(cat, _gov_scope(a, cat), a.agent, a.scope, a.as_, a.service_principal)
    print(f"agent {r['agent_id']} registered with scopes {', '.join(r['scopes'])}"
          + (" (service principal)" if r["service_principal"] else ""))
    return 0


def govern_acl_sync(a, cat_url):
    cat = connect(cat_url)
    sc = _gov_scope(a, cat)
    srcs = cat.query("SELECT * FROM inventory_source WHERE charter_id = ?", (sc.charter_id,))
    providers = {s["connector"]: gacl.provider_for(s) for s in srcs}
    _json(gacl.sync(cat, sc, a.as_, providers, a.force, only=a.category))
    return 0


def govern_scan(a, cat_url):
    cat = connect(cat_url)
    _json(gpii.scan(cat, _gov_scope(a, cat), a.as_, _gov_cfg(a)))
    return 0


def govern_mask(a, cat_url):
    cat = connect(cat_url)
    rep = gpii.build_index(cat, _gov_scope(a, cat), a.as_)
    _json(rep)
    return _fail([f"{f['chunk_id']} ({f['item_id']}): {f['reason']}" for f in rep["failed"]]) if rep["failed"] else 0


def govern_review(a, cat_url):
    cat = connect(cat_url)
    if a.op == "list":
        for f in gpii.queue(cat, _scope(a, cat)):
            print(f"{f['finding_id']} {f['entity_type']} {f['score']:.2f} {f['chunk_id']}: …{f['context']}…")
        return 0
    f = gpii.review(cat, _gov_scope(a, cat), a.finding, a.op, a.as_, a.reason)
    print(f"finding {f['finding_id']}: {f['review_state']} (applied on the next 'konmari govern mask')")
    return 0


def govern_label(a, cat_url):
    cat = connect(cat_url)
    sc = _gov_scope(a, cat)
    if a.op == "export":
        r = gpii.export_sample(cat, sc, a.category, a.output, a.as_, a.size)
        print(f"wrote {a.output}: sample {r['sample_id']} with {r['size']} item(s) to label")
        return 0
    r = gpii.import_labels(cat, sc, a.file, a.as_, _gov_cfg(a))
    _json({k: r[k] for k in ("sample_id", "policy_id", "sample_size", "recall", "passed", "reasons", "missed")})
    return 0 if r["passed"] else 1


def govern_reidentify(a, cat_url):
    cat = connect(cat_url)
    try:
        values = gpii.reidentify(cat, _scope(a, cat), a.token, a.as_, a.purpose)
    except gaccess.AccessDenied as e:
        return _fail(f"refused ({e.reason}); the attempt is recorded in the audit trail")
    for v in values:
        print(v)
    return 0 if values else _fail("no value matches this token")


def govern_leak_test(a, cat_url):
    cat = connect(cat_url)
    r = ggate.run_leak_suite(cat, _gov_scope(a, cat), ggate.load_suite(Path(a.suite)), a.as_,
                             prefilter=gaccess.openfga_prefilter(gcfg.load(a.config)))
    for f in r["failures"]:
        print(f"LEAK principal {f['principal']} got chunk {f['chunk_id']} (source {f['category_id']})")
    print(f"leak suite {'passed' if r['passed'] else 'FAILED'}: {r['queries']} queries")
    return 0 if r["passed"] else 1


def _print_status(rows) -> None:
    for r in rows:
        extra = f" (policy v{r['policy_version']})" if r["status"] == "approved" else \
            f": {', '.join(r['reasons'])}"
        print(f"{r['category_id']}: {r['status']}{extra}" + (" -> exposable" if r["status"] == "approved" else ""))


def govern_status(a, cat_url):
    cat = connect(cat_url)
    rows = ggate.status(cat, _scope(a, cat))
    _json(rows) if a.json else _print_status(rows)
    return 0


def govern_check(a, cat_url):
    cat = connect(cat_url)
    suite = ggate.load_suite(Path(a.suite)) if a.suite and Path(a.suite).exists() else None
    r = ggate.check(cat, _gov_scope(a, cat), a.as_, suite, gaccess.openfga_prefilter(gcfg.load(a.config)))
    if r["leak_suite"]:
        for f in r["leak_suite"]["failures"]:
            print(f"LEAK principal {f['principal']} got chunk {f['chunk_id']} (source {f['category_id']})")
    _print_status(r["sources"])
    return 0 if all(s["status"] == "approved" for s in r["sources"]) else 1


def govern_ropa(a, cat_url):
    cat = connect(cat_url)
    doc = grec.ropa_entry(cat, _scope(a, cat), _charter_doc(a) or {}, gcfg.load(a.config))
    Path(a.output).write_text(dump_yaml(doc), encoding="utf-8")
    print(f"wrote {a.output}; fill the <FILL> fields and file it with your Art. 30 record")
    return 0


def govern_config(a, cat_url):
    errs = gcfg.validate(gcfg.load(a.config), _charter_doc(a))
    if errs:
        return _fail(errs)
    print(f"{a.config}: governance configuration is valid")
    return 0


# ---------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="konmari", description="KonMari-Kit: make one pilot use case's data agent-ready")
    p.add_argument("--catalog", help="catalog URL: duckdb:///path.duckdb or postgresql://... "
                                     "(default: $KONMARI_CATALOG or duckdb:///.konmari/catalog.duckdb)")
    groups = p.add_subparsers(dest="group", required=True)

    ch = groups.add_parser("charter", help="pilot charter: scope, stakeholders, compliance, approval, gate")
    chs = ch.add_subparsers(dest="cmd", required=True)

    def cmd(sub, name, fn, help, file=True):
        q = sub.add_parser(name, help=help)
        if file:
            q.add_argument("--file", default="charter.yaml", help="charter file (default: charter.yaml)")
        q.set_defaults(fn=fn)
        return q

    q = cmd(chs, "init", charter_init, "write a charter template", file=False)
    q.add_argument("--template", choices=["full", "minimal"], default="full")
    q.add_argument("--lang", choices=["en", "de"], default="en")
    q.add_argument("-o", "--output", default="charter.yaml")
    q.add_argument("--force", action="store_true")
    cmd(chs, "validate", charter_validate, "validate the charter (schema, RACI, compliance, scope, hash)")
    q = cmd(chs, "approve", charter_approve, "approve the charter as the Accountable stakeholder")
    q.add_argument("--as", dest="as_", required=True, metavar="STAKEHOLDER_ID")
    q.add_argument("--compliance-signoff", action="store_true",
                   help="record the extra compliance approval needed for AI Act classification 'high'")
    q = cmd(chs, "amend", charter_amend, "create a new draft version from the edited file (or an earlier version)")
    q.add_argument("--reason", required=True)
    q.add_argument("--as", dest="as_", required=True, metavar="STAKEHOLDER_ID")
    q.add_argument("--from-version", type=int, help="revert: amend to the content of this earlier version")
    q = cmd(chs, "show", charter_show, "show a charter version with its approvals, or the decision log")
    q.add_argument("--version", type=int)
    q.add_argument("--log", action="store_true", help="show the decision log instead")
    q = cmd(chs, "diff", charter_diff, "list changed fields between two versions")
    q.add_argument("old", type=int)
    q.add_argument("new", type=int)
    q = cmd(chs, "log", charter_log, "append a decision-log entry")
    q.add_argument("--meeting", required=True,
                   choices=["kickoff", "working", "steering", "phase_gate", "post_pilot", "other"])
    q.add_argument("--date", required=True, help="meeting date YYYY-MM-DD")
    q.add_argument("--attendees", action="append", required=True, help="comma-separated stakeholder IDs")
    q.add_argument("--decision", action="append", required=True)
    q.add_argument("--action", action="append", help="OWNER:TEXT, repeatable")
    q.add_argument("--as", dest="as_", required=True, metavar="STAKEHOLDER_ID")
    cmd(chs, "status", charter_status, "show charter and metric status and cadence breaches")
    q = cmd(chs, "gate", charter_gate, "readiness gate for a KonMari step (exit 0 = pass)")
    q.add_argument("--step", required=True, choices=STEPS)
    q.add_argument("--json", action="store_true")

    q = groups.add_parser("next", help="where the pilot stands and the next command(s); read-only")
    q.add_argument("--json", action="store_true")
    q.set_defaults(fn=next_step)

    me = groups.add_parser("metrics", help="success metrics: definitions, baselines, measurements, report")
    mes = me.add_subparsers(dest="cmd", required=True)

    def mcmd(name, fn, help):
        q = mes.add_parser(name, help=help)
        q.add_argument("--file", default="metrics.yaml", help="metrics file (default: metrics.yaml)")
        q.add_argument("--charter", default="charter.yaml", help="charter file (default: charter.yaml)")
        q.set_defaults(fn=fn)
        return q

    mcmd("validate", metrics_validate, "validate metrics and golden set; store them as a draft metric set")
    mcmd("approve", metrics_approve, "approve the metric set").add_argument(
        "--as", dest="as_", required=True, metavar="STAKEHOLDER_ID")
    q = mcmd("amend", metrics_amend, "create a new metric-set version from the edited file")
    q.add_argument("--reason", required=True)
    q.add_argument("--as", dest="as_", required=True, metavar="STAKEHOLDER_ID")
    for name, fn, help in [("baseline", metrics_baseline, "record a baseline (as-is process, without the agent)"),
                           ("measure", metrics_measure, "record a gate or final measurement")]:
        q = mcmd(name, fn, help)
        q.add_argument("metric")
        q.add_argument("--value", type=float)
        q.add_argument("--method")
        q.add_argument("--unit")
        q.add_argument("--date", help="measurement date YYYY-MM-DD (default: today)")
        q.add_argument("--as", dest="as_", required=True, metavar="STAKEHOLDER_ID")
        if name == "baseline":
            q.add_argument("--not-yet-measured", action="store_true")
            q.add_argument("--plan-date", help="date the baseline will be measured")
        else:
            q.add_argument("--kind", required=True, choices=["gate", "final"])
            q.add_argument("--step", required=True, choices=STEPS)
    mcmd("report", metrics_report, "per-metric status and pilot verdict").add_argument(
        "--format", choices=["md", "json"], default="md")

    def ccmd(sub, name, fn, help, actor="required"):
        q = sub.add_parser(name, help=help)
        q.add_argument("--charter", default="charter.yaml", help="charter file (default: charter.yaml)")
        if actor:
            q.add_argument("--as", dest="as_", required=actor == "required", metavar="STAKEHOLDER_ID")
        q.set_defaults(fn=fn)
        return q

    iv = groups.add_parser("inventory", help="source inventory: register, scan, import, records, gate, report")
    ivs = iv.add_subparsers(dest="cmd", required=True)
    src = ivs.add_parser("source", help="register charter sources").add_subparsers(dest="sub", required=True)
    q = ccmd(src, "add", inventory_source_add, "register a source listed in the approved charter")
    q.add_argument("--connector", required=True, choices=inv.CONNECTORS)
    q.add_argument("--locator", required=True, help="site, connection URL (no password), share path or file")
    q.add_argument("--secret-ref", help="env:NAME or file:PATH holding a read-only credential")
    q.add_argument("--title")
    q = ccmd(ivs, "scan", inventory_scan, "scan registered sources read-only (runs the inventory gate first)",
             actor="optional")
    q.add_argument("--source", help="only this source ID")
    q.add_argument("--sample-size", type=int, default=sens.DEFAULT_SAMPLE, help="items or rows per asset (200)")
    q.add_argument("--threshold", type=float, default=sens.DEFAULT_THRESHOLD, help="hint score threshold (0.5)")
    q = ccmd(ivs, "import", inventory_import, "import CSV/YAML records for systems without a connector")
    q.add_argument("file")
    q = ccmd(ivs, "set", inventory_set, "set owner, steward, update frequency and other record fields")
    q.add_argument("asset", help="asset_uri")
    for f in ("owner", "steward", "update-frequency", "format", "ropa-ref", "access-rights-note"):
        q.add_argument(f"--{f}")
    q.add_argument("--personal-data-categories", help="comma-separated list")
    q.add_argument("--ropa-missing", action="store_true", help="flag: no RoPA entry exists yet")
    q.add_argument("--reason")
    q = ccmd(ivs, "tier", inventory_tier, "confirm or override the sensitivity tier (owner or compliance)")
    q.add_argument("asset")
    q.add_argument("--confirm", action="store_true")
    q.add_argument("--set", choices=sens.TIERS)
    q.add_argument("--reason")
    q = ccmd(ivs, "waive", inventory_waive, "waive an incomplete record for the handoff (approver)")
    q.add_argument("asset")
    q.add_argument("--reason", required=True)
    q = ccmd(ivs, "report", inventory_report, "Markdown report (and CSV); --gate checks completeness", actor=None)
    q.add_argument("--gate", action="store_true", help="exit 1 while incomplete records have no waiver")
    q.add_argument("--csv", help="also write all records to this CSV file")
    q = ccmd(ivs, "export", inventory_export, "export the inventory as DCAT 3 JSON-LD", actor=None)
    q.add_argument("-o", "--output")

    ca = groups.add_parser("categories", help="category map: suggest, assign, show, order, approve, step order")
    cas = ca.add_subparsers(dest="cmd", required=True)
    ccmd(cas, "suggest", categories_suggest, "rule-based primary category suggestions")
    q = ccmd(cas, "assign", categories_decide, "assign a primary (or --secondary) category")
    q.add_argument("asset")
    q.add_argument("category")
    q.add_argument("--secondary", action="store_true")
    q.add_argument("--reason")
    for name, help in [("accept", "accept the suggested primary category"),
                       ("reject", "reject the suggested primary category"),
                       ("demote", "make the accepted primary a secondary category"),
                       ("revert", "revert to an earlier primary category")]:
        q = ccmd(cas, name, categories_decide, help)
        q.add_argument("asset")
        q.add_argument("--reason", required=name == "reject")
        if name == "revert":
            q.add_argument("--to", help="earlier primary category (default: the previous one)")
    q = ccmd(cas, "show", categories_show, "list categories, or one category's assets across systems", actor=None)
    q.add_argument("category", nargs="?")
    q = ccmd(cas, "add", categories_manage, "add a category (approver)")
    q.add_argument("id")
    q.add_argument("--name", required=True)
    q.add_argument("--description", required=True)
    q.add_argument("--baseline-tier", default="internal", choices=sens.TIERS)
    q = ccmd(cas, "rename", categories_manage, "rename a category (approver)")
    q.add_argument("id")
    q.add_argument("--name", required=True)
    q = ccmd(cas, "retire", categories_manage, "retire an empty category (approver)")
    q.add_argument("id")
    q.add_argument("--reason")
    q = ccmd(cas, "order", categories_order, "propose (--propose) or change (--set) the category order; "
                                              "without flags list versions", actor="optional")
    q.add_argument("--propose", action="store_true")
    q.add_argument("--weight", action="append", metavar="NAME=VALUE",
                   help="override a risk weight: max_tier, personal_share, systems, overlaps")
    q.add_argument("--set", metavar="CAT1,CAT2,...")
    q.add_argument("--reason")
    q.add_argument("--version", type=int)
    q = ccmd(cas, "approve", categories_approve, "approve the proposed category order (charter approver)")
    q.add_argument("--version", type=int)
    ccmd(cas, "handoff", categories_handoff, "check the handoff to step 03 and print the category sequence",
         actor="optional")
    q = ccmd(cas, "step", categories_step, "show, set or waive a KonMari step for one category", actor="optional")
    q.add_argument("category")
    q.add_argument("step", choices=STEPS)
    q.add_argument("--status", choices=["in_progress", "complete"])
    q.add_argument("--waive", action="store_true", help="allow starting before earlier categories finish")
    q.add_argument("--reason")

    sco = groups.add_parser("score", help="joy test: score in-scope assets, propose keep/fix/discard (no subcommand)")
    sco.add_argument("--charter", default="charter.yaml", help="charter file (default: charter.yaml)")
    sco.add_argument("--as", dest="as_", metavar="STAKEHOLDER_ID")
    sco.add_argument("--config", help="pilot thresholds (default: scorecard.yaml if present)")
    sco.add_argument("--rubric", help="custom rubric YAML (default: the shipped rubric)")
    sco.add_argument("--changed", action="store_true", help="only assets whose inventory entry changed")
    sco.set_defaults(fn=score_run)
    scs = sco.add_subparsers(dest="cmd")
    q = ccmd(scs, "evidence", score_evidence, "record one evidence fact (references, counts, hashes only)")
    q.add_argument("asset")
    q.add_argument("--kind", required=True, choices=list(sp.KINDS))
    q.add_argument("--data", required=True, help="JSON payload; acl_snapshot also takes {\"principals\": [...]}")
    q.add_argument("--ref", required=True, help="evidence reference, e.g. acl:snapshot-2026-10-01")
    q.add_argument("--captured-at", help="capture timestamp (ISO 8601, default now)")
    q = ccmd(scs, "ingest", score_ingest, "ingest Soda Core scan results or an ODCS contract as evidence")
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--soda", help="soda scan results JSON (soda scan -srf)")
    g.add_argument("--odcs", help="ODCS v3 data contract YAML")
    q = ccmd(scs, "irrelevant", score_irrelevant, "mark an asset as not needed for any pilot question")
    q.add_argument("asset")
    q.add_argument("--reason", required=True)
    q = ccmd(scs, "fix-done", score_fix_done, "mark a fix item complete and re-score the asset")
    q.add_argument("asset")
    q.add_argument("criterion", choices=list(se.FIX_ROUTES))
    q.add_argument("--config")
    q = ccmd(scs, "alert", score_alert, "record a freshness alert and re-score the asset")
    q.add_argument("asset")
    q.add_argument("--status", required=True, choices=["warn", "error"])
    q.add_argument("--ref", required=True, help="alert reference, e.g. monitor:incident-17")
    q.add_argument("--config")

    rv = groups.add_parser("review", help="review proposed verdicts: list, accept, override, reverse")
    rvs = rv.add_subparsers(dest="cmd", required=True)
    q = ccmd(rvs, "list", review_list, "list verdicts, or one asset's review history", actor=None)
    q.add_argument("asset", nargs="?")
    q.add_argument("--status", choices=["proposed", "approved"])
    q = ccmd(rvs, "accept", review_act, "accept a proposal (or --all-keep: every keep without unknown)")
    q.add_argument("asset", nargs="?")
    q.add_argument("--all-keep", action="store_true")
    q.add_argument("--category")
    for name, help in [("override", "replace the proposal by another verdict (justification required)"),
                       ("reverse", "change an approved verdict (justification required)")]:
        q = ccmd(rvs, name, review_act, help)
        q.add_argument("asset")
        q.add_argument("--to", required=True, choices=list(sv.VERDICTS))
        q.add_argument("--justification")

    sca = groups.add_parser("scorecard", help="scorecard reports and run comparison")
    scas = sca.add_subparsers(dest="cmd", required=True)
    q = ccmd(scas, "report", scorecard_report, "asset and category report (md, csv, json)", actor=None)
    q.add_argument("--format", choices=["md", "csv", "json"], default="md")
    q.add_argument("--level", choices=["asset", "category"], default="asset", help="CSV only")
    q.add_argument("-o", "--output")
    q = scas.add_parser("compare", help="criterion levels that changed between two runs")
    q.add_argument("run_a")
    q.add_argument("run_b")
    q.add_argument("--allow-rubric-mismatch", action="store_true")
    q.set_defaults(fn=scorecard_compare)

    di = groups.add_parser("discard", help="discard pass: detect duplicates and clutter, propose, approve, execute, "
                                           "restore, decision log")
    dis = di.add_subparsers(dest="cmd", required=True)
    q = ccmd(dis, "detect", discard_detect, "fingerprint the pilot's inventoried items and flag clutter (changes "
                                            "no data)", actor="optional")
    q.add_argument("--items", help="item manifest (JSONL or YAML) for assets whose items were exported to files")
    q.add_argument("--config", help="detector settings (default: discard.yaml if present)")
    q.add_argument("--report-dir", default=".konmari/reports")
    q = ccmd(dis, "calibrate", discard_calibrate, "sample pairs, store expert labels, report precision/recall, "
                                                  "select a threshold or record an override", actor="optional")
    q.add_argument("--detector", required=True, choices=ddet.DETECTORS)
    q.add_argument("--sample", type=int, help="write this many pairs, spread over the score range")
    q.add_argument("-o", "--output", default="calibration-pairs.csv")
    q.add_argument("--labels", help="the filled-in CSV")
    q.add_argument("--threshold", type=float, help="select this threshold (needs 50 labels)")
    q.add_argument("--override-reason", help="use the detector without 50 labels (approver only)")
    ccmd(dis, "propose", discard_propose, "turn flags and approved scorecard 'discard' verdicts into proposals")
    q = ccmd(dis, "list", discard_list, "list proposals", actor=None)
    q.add_argument("--status", choices=["pending", "approved", "rejected", "scheduled", "executed", "cancelled"])
    for name, help in [("approve", "approve a proposal (optionally with another --action)"),
                       ("reject", "reject a proposal")]:
        q = ccmd(dis, name, discard_decide, help)
        q.add_argument("proposal")
        q.add_argument("--comment")
        q.add_argument("--config")
        if name == "approve":
            q.add_argument("--action", choices=dd.ACTIONS, help="change the proposed action")
            q.add_argument("--compliance", action="store_true", help="the second (compliance) approval of an erasure")
    q = ccmd(dis, "bulk-approve", discard_bulk, "approve all pending proposals with one action, category and method")
    q.add_argument("--action", required=True, choices=dd.ACTIONS)
    q.add_argument("--category", required=True)
    q.add_argument("--method", required=True)
    q.add_argument("--yes", action="store_true", help="confirm after checking the listed item IDs")
    q.add_argument("--comment")
    q = ccmd(dis, "cancel", discard_cancel, "cancel an open proposal, e.g. an erasure during its waiting period")
    q.add_argument("proposal")
    q.add_argument("--comment")
    q = ccmd(dis, "request", discard_request, "propose an action for one item by hand (e.g. an erasure request)")
    q.add_argument("item")
    q.add_argument("--action", required=True, choices=dd.ACTIONS)
    q.add_argument("--reason", required=True)
    q = ccmd(dis, "mark-off-scope", discard_off_scope, "flag an item as not needed for the pilot (reviewer)")
    q.add_argument("item")
    q.add_argument("--reason", required=True)
    q = ccmd(dis, "execute", discard_execute, "execute approved decisions and due erasures")
    q.add_argument("--proposal")
    q.add_argument("--archive", help="filesystem path or s3://bucket/prefix (default: $KONMARI_ARCHIVE or "
                                     ".konmari/archive)")
    q.add_argument("--index", help="JSON list of artifact IDs present in the downstream index (stub adapter)")
    q = ccmd(dis, "restore", discard_restore, "restore an archived or excluded item (checksum verified)")
    q.add_argument("item")
    q.add_argument("--archive")
    q = dis.add_parser("history", help="the decision history of one item")
    q.add_argument("item")
    q.set_defaults(fn=discard_history)
    q = dis.add_parser("log", help="verify or export the hash-chained decision log")
    q.add_argument("op", choices=["verify", "export"])
    q.add_argument("-o", "--output")
    q.set_defaults(fn=discard_log)
    q = ccmd(dis, "check-propagation", discard_propagation, "fail while derived artifacts of discarded items remain",
             actor=None)
    q.add_argument("--index", help="JSON list of artifact IDs present in the downstream index (stub adapter)")
    q = ccmd(dis, "report", discard_report, "latest run report with duplicate rate and warnings", actor=None)
    q.add_argument("--run")
    q.add_argument("--format", choices=["md", "json"], default="md")

    re_ = groups.add_parser("retention", help="retention rules per category, legal holds, expiry check")
    res = re_.add_subparsers(dest="cmd", required=True)
    rule = res.add_parser("rule", help="register, list and approve retention rules").add_subparsers(dest="op",
                                                                                                   required=True)
    q = ccmd(rule, "add", retention_rule, "register a rule for one category (no wildcard)")
    q.add_argument("--category", required=True)
    q.add_argument("--purpose", required=True)
    q.add_argument("--legal-basis", required=True, help="e.g. 'Art. 6(1)(c) DSGVO'")
    q.add_argument("--source", required=True, help="statute and paragraph, contract or internal policy")
    q.add_argument("--start-event", required=True, choices=dret.START_EVENTS)
    q.add_argument("--period", required=True, help="ISO 8601 duration, e.g. P8Y")
    q.add_argument("--expiry-action", required=True, choices=dret.EXPIRY_ACTIONS)
    ccmd(rule, "list", retention_rule, "list rules", actor=None)
    q = ccmd(rule, "approve", retention_rule, "approve a rule (compliance)")
    q.add_argument("rule")
    hold = res.add_parser("hold", help="legal holds on items or categories").add_subparsers(dest="op", required=True)
    q = ccmd(hold, "add", retention_hold, "put an item or a category on legal hold")
    g = q.add_mutually_exclusive_group(required=True)
    g.add_argument("--item")
    g.add_argument("--category")
    q.add_argument("--reason", required=True)
    q.add_argument("--owner", required=True)
    q.add_argument("--review-date", required=True, help="YYYY-MM-DD")
    q = ccmd(hold, "release", retention_hold, "release a legal hold (owner or compliance)")
    q.add_argument("hold")
    ccmd(hold, "list", retention_hold, "list active legal holds", actor=None)
    q = ccmd(res, "check", retention_check, "propose actions for items whose retention ends soon (incl. archive)")
    q.add_argument("--window-days", type=int, default=ddet.DEFAULTS["retention_window_days"])
    q.add_argument("--schedule", default=ddet.DEFAULTS["retention_schedule"], help="interval between checks (P7D)")

    no = groups.add_parser("normalize", help="normalize in-scope fields by spec (raw kept), quarantine, conformance")
    no.add_argument("--charter", default="charter.yaml", help="charter file (default: charter.yaml)")
    no.add_argument("--as", dest="as_", metavar="STAKEHOLDER_ID")
    no.add_argument("--records", help="JSONL records: {source, object, record_id, values}")
    no.add_argument("--specs", default="normalize", help="spec directory or file (default: normalize/)")
    no.set_defaults(fn=normalize_run)
    nos = no.add_subparsers(dest="cmd")
    qu = nos.add_parser("quarantine", help="values that could not be normalized").add_subparsers(dest="op",
                                                                                                 required=True)
    q = ccmd(qu, "list", normalize_quarantine, "list quarantined values (personal data masked)", actor="optional")
    q.add_argument("--status", default="open", choices=["open", "resolved", "unfixable", "spec_change_requested",
                                                        "all"])
    q = ccmd(qu, "resolve", normalize_quarantine, "resolve as field owner: --value, --unfixable or --spec-change")
    q.add_argument("qid")
    q.add_argument("--value")
    q.add_argument("--unfixable", action="store_true")
    q.add_argument("--spec-change", action="store_true")
    q.add_argument("--reason")
    q = ccmd(qu, "revert", normalize_quarantine, "reopen a resolved entry and withdraw its manual value")
    q.add_argument("qid")
    q.add_argument("--reason")
    q = nos.add_parser("conformance", help="per-field conformance of a run (exit 1 if values are unaccounted)")
    q.add_argument("run")
    q.set_defaults(fn=normalize_conformance)
    ct = nos.add_parser("contract", help="ODCS data contract from the specs").add_subparsers(dest="op", required=True)
    q = ct.add_parser("export", help="write the specs as an ODCS v3 contract with quality rules")
    q.add_argument("--specs", default="normalize")
    q.add_argument("-o", "--output")
    q.set_defaults(fn=normalize_contract)
    q = ct.add_parser("check", help="run the contract's quality rules against a run (CI)")
    q.add_argument("contract")
    q.add_argument("--run", help="default: the latest run")
    q.set_defaults(fn=normalize_contract)
    q = ccmd(nos, "rollback", normalize_rollback, "drop one run's canonical values (raw data untouched)")
    q.add_argument("run")
    q.add_argument("--reason", required=True)

    rs = groups.add_parser("resolve", help="cross-source entity resolution, clerical review, publish, split")
    rss = rs.add_subparsers(dest="cmd", required=True)
    q = ccmd(rss, "run", resolve_run, "score cross-source pairs (Splink) and route them by the two thresholds")
    q.add_argument("--entity", required=True)
    q.add_argument("--config", default="resolve.yaml")
    rv2 = rss.add_parser("review", help="clerical review of borderline pairs").add_subparsers(dest="op", required=True)
    q = ccmd(rv2, "list", resolve_review, "the review queue, highest probability first", actor=None)
    q.add_argument("--entity", required=True)
    q = ccmd(rv2, "export", resolve_review, "CSV with the queue and a precision sample", actor=None)
    q.add_argument("--entity", required=True)
    q.add_argument("-o", "--output", default="review-pairs.csv")
    q.add_argument("--sample", type=int, default=erl.DEFAULTS["sample"])
    q = ccmd(rv2, "import", resolve_review, "store the labels of a filled-in CSV")
    q.add_argument("file")
    for name in ("accept", "reject"):
        q = ccmd(rv2, name, resolve_review, f"{name} one pair")
        q.add_argument("rec_a", help="source:record_id")
        q.add_argument("rec_b")
        q.add_argument("--entity", required=True)
        q.add_argument("--reason")
    q = ccmd(rss, "publish", resolve_publish, "release clusters to golden records if precision/recall meet floors")
    q.add_argument("--entity", required=True)
    q.add_argument("--known", help="CSV rec_a,rec_b of the owner's known duplicates (recall)")
    q.add_argument("--override-recall-reason", help="publish without a known-duplicate list (data owner, audited)")
    q = ccmd(rss, "split", resolve_split, "split a wrongly merged cluster")
    q.add_argument("cluster")
    q.add_argument("--entity", required=True)
    q.add_argument("--records", required=True, help="comma-separated source:record_id of one part")
    q.add_argument("--reason", required=True)
    q = ccmd(rss, "restore", resolve_restore, "undo a split")
    q.add_argument("split")
    q.add_argument("--reason")

    po = groups.add_parser("policy", help="source-of-truth policy: load, approve, rollback, coverage, golden records")
    pos = po.add_subparsers(dest="cmd", required=True)
    q = ccmd(pos, "load", policy_load, "store an edited policy as a pending version with a dry-run impact report")
    q.add_argument("--file", required=True)
    q.add_argument("--show", type=int, default=20, help="impact lines to print (default 20)")
    q = ccmd(pos, "approve", policy_approve, "approve a pending version (field owner) and activate it")
    q.add_argument("entity")
    q.add_argument("--version", type=int, required=True)
    q = ccmd(pos, "rollback", policy_rollback, "re-activate an earlier approved version")
    q.add_argument("entity")
    q.add_argument("--to", type=int, required=True)
    q.add_argument("--reason", required=True)
    q = ccmd(pos, "check", policy_check, "every agent-exposed field needs a rule (exit 1 lists the gaps)", actor=None)
    q.add_argument("--exposure", default="exposure.yaml")
    q = ccmd(pos, "build", policy_build, "build golden records with provenance and detect conflicts",
             actor="optional")
    q.add_argument("entity")
    q.add_argument("--exposure", default="exposure.yaml")
    q = pos.add_parser("golden", help="golden values with provenance and status (the agent-facing contract)")
    q.add_argument("entity")
    q.add_argument("cluster")
    q.add_argument("--field")
    q.set_defaults(fn=policy_golden)

    co = groups.add_parser("conflicts", help="conflict register: list, resolve, accept, revert, overdue report")
    cos = co.add_subparsers(dest="cmd", required=True)
    q = ccmd(cos, "list", conflicts_list, "list conflicts (personal values masked without access)", actor="optional")
    q.add_argument("--status", choices=["open", "resolved", "accepted_deviation", "reopened", "overridden"])
    for name, help in [("resolve", "decide a conflict and name the write-back ticket"),
                       ("accept", "accept the deviation at the source (reason required)")]:
        q = ccmd(cos, name, conflicts_decide, help)
        q.add_argument("conflict")
        g = q.add_mutually_exclusive_group(required=True)
        g.add_argument("--source", help="take this source's candidate value")
        g.add_argument("--value", help="enter a corrected value")
        if name == "resolve":
            q.add_argument("--ticket", required=True, help="write-back ticket, e.g. IT-4711")
        else:
            q.add_argument("--reason", required=True)
    q = ccmd(cos, "revert", conflicts_decide, "withdraw a decision and reopen the conflict")
    q.add_argument("conflict")
    q.add_argument("--reason")
    q = cos.add_parser("report", help="conflict report; --overdue lists entries past their due date")
    q.add_argument("--overdue", action="store_true")
    q.set_defaults(fn=conflicts_report)
    q = cos.add_parser("export-tickets", help="CSV of write-back tickets for the source owners")
    q.add_argument("-o", "--output", default="write-back-tickets.csv")
    q.set_defaults(fn=conflicts_export)

    ck_ = groups.add_parser("chunk", help="structure-aware chunking of kept, normalized pilot documents")
    cks = ck_.add_subparsers(dest="cmd", required=True)
    q = ccmd(cks, "run", chunk_run, "chunk eligible items and print the chunking report", actor="optional")
    q.add_argument("--dry-run", action="store_true", help="compute and report, write nothing")
    q.add_argument("--json", action="store_true")
    prof = cks.add_parser("profile", help="chunking profiles per category").add_subparsers(dest="op", required=True)
    q = ccmd(prof, "show", chunk_profile, "effective profile per category", actor=None)
    q.add_argument("category", nargs="?")
    q = ccmd(prof, "set", chunk_profile, "override profile values for one category (audited)")
    q.add_argument("category")
    for opt, typ in (("--max-tokens", int), ("--min-tokens", int), ("--overlap-fallback", float),
                     ("--model-max-tokens", int), ("--flag-tokens", int), ("--tokenizer", str),
                     ("--parser", str)):
        q.add_argument(opt, type=typ)
    q.add_argument("--llm-context", type=lambda v: v.lower() in ("1", "true", "yes", "on"), metavar="on|off")
    q.add_argument("--approved-processor", action="append", help="processor allowed to receive content (repeatable)")
    q.add_argument("--reason", required=True)
    q = ccmd(cks, "rollback", chunk_rollback, "make the previous chunk sets of a run's items active again")
    q.add_argument("run")
    q.add_argument("--reason")

    en = groups.add_parser("enrich", help="metadata envelope, completeness gate, schemas, lineage")
    ens = en.add_subparsers(dest="cmd", required=True)
    q = ccmd(ens, "run", enrich_run, "enrich active chunks and agent-exposed tables; print the report",
             actor="optional")
    q.add_argument("--llm-url", help="opt-in LLM context: OpenAI-compatible server (self-hosted by default)")
    q.add_argument("--llm-model")
    q.add_argument("--processor", help="name of the external processor running --llm-url (must be approved)")
    q.add_argument("--no-lineage", action="store_true", help="do not emit the OpenLineage run event")
    q.add_argument("--json", action="store_true")
    q = ccmd(ens, "override", enrich_override, "set one envelope field of a chunk, item or table (audited)")
    q.add_argument("subject", help="chunk ID, item ID or table asset URI")
    q.add_argument("--field", required=True)
    q.add_argument("--value", required=True)
    q.add_argument("--reason", required=True)
    q = ens.add_parser("lineage", help="source, normalization, chunking and enrichment steps of one chunk")
    q.add_argument("chunk")
    q.set_defaults(fn=enrich_lineage)
    q = ccmd(ens, "rollback", enrich_rollback, "restore the metadata values a run changed")
    q.add_argument("run")
    q.add_argument("--reason")
    sch = ens.add_parser("schema", help="column annotations of agent-exposed tables").add_subparsers(dest="op",
                                                                                                  required=True)
    q = ccmd(sch, "load", enrich_schema, "load table and column descriptions from YAML")
    q.add_argument("file")
    ccmd(sch, "report", enrich_schema, "schema coverage (exit 1 lists undocumented columns)", actor=None)

    gl = groups.add_parser("glossary", help="business glossary: terms, steward approval, links, export")
    gls_ = gl.add_subparsers(dest="op", required=True)
    q = ccmd(gls_, "stewards", glossary_cmd, "name the glossary stewards")
    q.add_argument("--steward", action="append", required=True)
    q = ccmd(gls_, "add", glossary_cmd, "add a draft term from a YAML file")
    q.add_argument("--file", required=True)
    q = ccmd(gls_, "edit", glossary_cmd, "edit a term (approved terms get a new version in review)")
    q.add_argument("term")
    q.add_argument("--file")
    q.add_argument("--definition")
    for name, help in (("submit", "send a draft to review"), ("approve", "approve (steward, not an author)"),
                       ("reject", "reject (steward, not an author)")):
        q = ccmd(gls_, name, glossary_cmd, help)
        q.add_argument("term")
        q.add_argument("--comment", required=name == "reject")
    q = ccmd(gls_, "deprecate", glossary_cmd, "deprecate an approved term")
    q.add_argument("term")
    q.add_argument("--replaced-by")
    q.add_argument("--comment", required=True)
    q = ccmd(gls_, "revert", glossary_cmd, "expose an earlier approved version again")
    q.add_argument("term")
    q.add_argument("--to", type=int, required=True)
    q.add_argument("--reason")
    q = ccmd(gls_, "harvest", glossary_cmd, "propose draft terms from chunks, exposed columns and pilot questions")
    q.add_argument("--questions", default="golden-set.yaml")
    q.add_argument("--top", type=int, default=20)
    q.add_argument("--lang", default="de", choices=["de", "en"])
    q = ccmd(gls_, "draft-definition", glossary_cmd, "let a self-hosted or approved model draft a definition")
    q.add_argument("term")
    q.add_argument("--llm-url", required=True)
    q.add_argument("--llm-model", required=True)
    q.add_argument("--processor")
    q.add_argument("--approved-processor", action="append")
    q = ccmd(gls_, "conflict", glossary_cmd, "resolve a glossary conflict (steward decision)")
    q.add_argument("conflict")
    q.add_argument("--decision", required=True)
    ccmd(gls_, "report", glossary_cmd, "coverage, terms per status and open conflicts", actor=None)
    q = ccmd(gls_, "export", glossary_cmd, "approved and deprecated terms as SKOS (Turtle) or JSON", actor=None)
    q.add_argument("--format", choices=["skos", "json"], default="skos")
    q.add_argument("-o", "--output")
    q = ccmd(gls_, "show", glossary_cmd, "one term with versions, status and links", actor=None)
    q.add_argument("term")

    def gcmd(sub, name, fn, help, actor="required"):
        q = ccmd(sub, name, fn, help, actor)
        q.add_argument("--config", default="governance.yaml", help="governance settings (default: governance.yaml)")
        return q

    au = groups.add_parser("audit", help="the one hash-chained audit trail: verify, export, retention purge")
    aus = au.add_subparsers(dest="op", required=True)
    gcmd(aus, "verify", audit_cmd, "recompute the chain over all steps; exit 1 names the first broken event",
         actor=None)
    q = gcmd(aus, "export", audit_cmd, "events of a data subject (Art. 15) or a time range, as JSONL or CSV")
    q.add_argument("--subject", help="user identity or pseudonym token (compliance role)")
    q.add_argument("--from", dest="from_", help="ISO date/time")
    q.add_argument("--to", help="ISO date/time")
    q.add_argument("--format", choices=["jsonl", "csv"], default="jsonl")
    q.add_argument("-o", "--output", required=True)
    gcmd(aus, "purge", audit_cmd, "delete events past retention (retention role) and write a checkpoint")

    gv = groups.add_parser("govern", help="governance: source policies, ACLs, PII masking, leak suite, gate")
    gvs = gv.add_subparsers(dest="cmd", required=True)
    pol_ = gvs.add_parser("policy", help="versioned source policies per category").add_subparsers(dest="op",
                                                                                                 required=True)
    gcmd(pol_, "seed", govern_policy, "one pending draft per in-scope category (inventory sensitivity, owner)")
    q = gcmd(pol_, "edit", govern_policy, "new pending version; the approved one stays enforced")
    q.add_argument("policy", help="category ID")
    q.add_argument("--file", help="YAML with policy fields")
    q.add_argument("--exposure", choices=gpol.EXPOSURE)
    q.add_argument("--sensitivity")
    q.add_argument("--acl-sync-cadence", dest="acl_sync_cadence", help="ISO 8601 duration, e.g. PT24H")
    q.add_argument("--owner")
    q.add_argument("--legal-basis", dest="legal_basis", help="Art. 9(2) basis for special-category data")
    q.add_argument("--tool", action="append", help="permitted tool (repeatable)")
    q.add_argument("--mask", action="append", metavar="ENTITY=OPERATOR", help="e.g. PERSON=pseudonymize")
    q.add_argument("--allow", action="append", help="explicit principal for sources without readable ACLs")
    q.add_argument("--deny", action="append")
    q.add_argument("--service-principal", action="append", help="autonomous agent granted this source")
    q.add_argument("--reason")
    q = gcmd(pol_, "approve", govern_policy, "approve a pending version (data_owner or compliance)")
    q.add_argument("policy")
    q.add_argument("--version", type=int, required=True)
    q.add_argument("--reason")
    q = gcmd(pol_, "revert", govern_policy, "make an earlier version active again (new approved version)")
    q.add_argument("policy")
    q.add_argument("--to", type=int, required=True)
    q.add_argument("--reason")
    q = gcmd(pol_, "show", govern_policy, "all versions of one or every policy", actor=None)
    q.add_argument("policy", nargs="?")
    q = gcmd(gvs, "grant", govern_grant, "owner-approved write grant for one agent, category and tool")
    q.add_argument("--agent", required=True)
    q.add_argument("--category", required=True)
    q.add_argument("--tool", required=True)
    q.add_argument("--days", type=int, default=90)
    q = gcmd(gvs, "agent", govern_agent, "register an agent client with least-privilege scopes")
    q.add_argument("agent")
    q.add_argument("--scope", action="append", required=True, help="read:<category> or write:<category>")
    q.add_argument("--service-principal", action="store_true", help="autonomous agent without a user")
    q = gcmd(gvs, "acl-sync", govern_acl_sync, "re-read source ACLs when due; apply revocations everywhere")
    q.add_argument("--force", action="store_true", help="sync now, regardless of the cadence")
    q.add_argument("--category")
    gcmd(gvs, "scan", govern_scan, "PII scan of every pilot chunk (findings without values)")
    gcmd(gvs, "mask", govern_mask, "build the masked agent index from the approved policies")
    rv_ = gvs.add_parser("review", help="human review of uncertain PII findings").add_subparsers(dest="op",
                                                                                                required=True)
    gcmd(rv_, "list", govern_review, "pending findings with context", actor=None)
    for name in ("confirm", "reject", "revert"):
        q = gcmd(rv_, name, govern_review, f"{name} a finding (reviewer roles)")
        q.add_argument("finding")
        q.add_argument("--reason")
    lb = gvs.add_parser("label", help="labelled sample for the PII recall gate").add_subparsers(dest="op",
                                                                                               required=True)
    q = gcmd(lb, "export", govern_label, "CSV sample to label by hand")
    q.add_argument("--category", required=True)
    q.add_argument("--size", type=int, default=200)
    q.add_argument("-o", "--output", default="pii-labels.csv")
    q = gcmd(lb, "import", govern_label, "import labels and evaluate recall (exit 1 if the gate fails)")
    q.add_argument("file")
    q = gcmd(gvs, "reidentify", govern_reidentify, "resolve a pseudonym token (compliance; audited)")
    q.add_argument("token")
    q.add_argument("--purpose", required=True)
    q = gcmd(gvs, "leak-test", govern_leak_test, "permission leak suite (exit 1 on any unauthorized chunk)")
    q.add_argument("--suite", default="leak-suite.yaml")
    q = gcmd(gvs, "status", govern_status, "computed governance status per source", actor=None)
    q.add_argument("--json", action="store_true")
    q = gcmd(gvs, "check", govern_check, "heartbeat, leak suite, record the gate status (exit 1 if any blocked)")
    q.add_argument("--suite", default="leak-suite.yaml")
    q = gcmd(gvs, "ropa", govern_ropa, "prefilled DSGVO Art. 30 record entry for the pilot agent", actor=None)
    q.add_argument("-o", "--output", default="ropa-entry.yaml")
    gcmd(gvs, "config", govern_config, "validate governance.yaml (retention, thresholds)", actor=None)

    from konmari.connect import cli as connect_cli

    connect_cli.register(groups, ccmd)
    from konmari.evaluate import cli as eval_cli

    eval_cli.register(groups, ccmd)
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a, a.catalog)
    except Refused as e:
        return _fail(e.reasons)
    except gaccess.AccessDenied as e:
        return _fail(f"refused with HTTP {e.status}: {e.reason}")

    except (OSError, ValueError, yaml.YAMLError) as e:
        return _fail(str(e))


if __name__ == "__main__":
    sys.exit(main())
