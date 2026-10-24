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
