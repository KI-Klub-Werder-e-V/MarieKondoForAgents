"""`konmari` command line. Exit codes: 0 ok, 1 validation failed / refused / gate failed, 2 usage error."""

import argparse
import json
import sys
from importlib.resources import files
from pathlib import Path

import yaml

from konmari import versions as v
from konmari.canonical import dump_yaml, load_yaml
from konmari.catalog import connect
from konmari.charter import store as cs
from konmari.charter.gate import gate
from konmari.charter.validation import STEPS, roles_by_id, validate_charter
from konmari.categories import store as cats
from konmari.inventory import lineage
from konmari.inventory import sensitivity as sens
from konmari.inventory import store as inv
from konmari.metrics import store as ms
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
    return p


def main(argv=None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.fn(a, a.catalog)
    except Refused as e:
        return _fail(e.reasons)

    except (OSError, ValueError, yaml.YAMLError) as e:
        return _fail(str(e))


if __name__ == "__main__":
    sys.exit(main())
