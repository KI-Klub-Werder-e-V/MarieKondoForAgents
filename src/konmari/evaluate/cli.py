"""`konmari eval ...` and `konmari monitor ...` (c09)."""

import csv
import json
from pathlib import Path

from konmari.canonical import load_yaml
from konmari.catalog import connect
from konmari.evaluate import gate as eg
from konmari.evaluate import golden as gd
from konmari.evaluate import judge as jd
from konmari.evaluate import run as er
from konmari.monitor import checks as mc
from konmari.monitor import incidents as mi
from konmari.versions import Refused


def _ctx(a, cat_url):
    from konmari.cli import _scope

    cat = connect(cat_url)
    sc = _scope(a, cat)
    if getattr(a, "pilot", None) and a.pilot != sc.charter_id:
        raise Refused(f"--pilot {a.pilot} does not match the charter '{sc.charter_id}'")
    return cat, sc


def _out(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def eval_cmd(a, cat_url):
    cat, sc = _ctx(a, cat_url)
    op = a.op
    if op == "import":
        res = gd.import_items(cat, sc, a.file, a.as_)
        for s in res["stored"]:
            print(f"stored {s} (draft: needs approval by a domain expert or source owner)")
        print(f"{len(res['stored'])} stored, {len(res['unchanged'])} unchanged, {len(res['errors'])} error(s)")
        if res["errors"]:
            raise Refused(res["errors"])
        return 0
    if op == "approve":
        r = gd.approve(cat, sc, a.item, a.as_, a.version)
        print(f"approved {a.item} v{r['version']} by {a.as_}")
        return 0
    if op == "list":
        _out([{k: i[k] for k in ("item_id", "version", "status", "origin", "category_id", "expected_behaviour",
                                 "approved_by", "retention_until")} for i in gd.items(cat, sc.charter_id)])
        return 0
    if op == "retention":
        gone = gd.retention(cat, sc, a.as_)
        print(f"deleted {len(gone)} expired user-derived item(s){': ' + ', '.join(gone) if gone else ''}")
        return 0
    if op == "activate":
        r = gd.activate(cat, sc, a.as_)
        print(f"golden set v{r['version']} active: {r['approved']} approved of {r['items']} item(s); minimums "
              f"from {r['minimums']['source']}")
        return 0
    if op == "run":
        cfg = load_yaml(Path(a.config)) or {}
        principal = cfg.get("principal") or {}
        judge = jd.make(cfg.get("judge"))
        r = er.run(cat, sc, er.agent_from_config(cfg, principal), principal,
                   str((cfg.get("agent") or {}).get("version") or "unversioned"), a.as_, judge)
        agg = (r["aggregates"] or {}).get("run") or {}
        print(f"run {r['run_id']} {r['status']}: {agg.get('items', 0)} executed, {len(r['excluded'])} excluded; "
              f"judge {r['judge_model']} {r['judge_version']} ({r['judge_status']})")
        for x in r["excluded"]:
            print(f"excluded {x['item_id']} v{x['version']}: {x['reason']}")
        if r["error"]:
            print(f"error: {r['error']}")
        if r["status"] == "completed":
            fb = eg.feedback(cat, sc, r["run_id"], a.as_)
            for c in fb["fix"]:
                print(f"fix candidate: {c}")
            if p := eg.propose_next(cat, sc, r["run_id"], a.as_):
                print(f"proposed next category {p['category']} (proposal {p['proposal_id']}); confirm with "
                      f"'konmari eval next-category --confirm {p['proposal_id']} --as <use_case_owner>'")
        return 0 if r["status"] == "completed" else 1
    if op == "report":
        r = er.get(cat, a.run) if a.run else er.latest(cat, sc.charter_id)
        if r is None:
            raise Refused("no evaluation run yet")
        _out({**r, "results": er.results(cat, r["run_id"])})
        return 0
    if op == "gate":
        res = eg.evaluate(cat, sc, a.run, a.as_ or "konmari-eval")
        if a.json:
            _out(res)
        for f in res["failures"]:
            print(f"gate failed: {f}")
        if res["passed"]:
            print(f"gate passed for run {res['run_id']}")
        return 0 if res["passed"] else 1
    if op == "baseline":
        if a.action == "approve" and not a.run:
            raise Refused("give the run ID to approve as baseline")
        r = eg.approve_baseline(cat, sc, a.run, a.as_) if a.action == "approve" else eg.revert_baseline(cat, sc,
                                                                                                         a.as_)
        print(f"baseline {r['baseline']} (previous {r['previous'] or '-'})")
        return 0
    if op == "calibrate":
        with open(a.labels, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        tuning = set(Path(a.tuning).read_text().split()) if a.tuning else set()
        r = jd.calibrate(cat, sc, rows, a.judge_model, a.judge_version, a.as_, tuning)
        print(f"kappa {r['kappa']} on {r['items']} item(s): judge {r['status']}")
        _out(r["confusion"])
        return 0
    if op == "next-category":
        if a.confirm:
            if not a.as_:
                raise Refused("--as is required to confirm")
            r = eg.confirm_next(cat, sc, a.confirm, a.as_)
            print(f"active category {r['category']} (previous {r['previous'] or '-'})")
        else:
            print(f"active category: {eg.active_category(cat, sc.charter_id) or '-'}")
            for p in cat.query("SELECT * FROM category_focus WHERE charter_id = ? AND status = 'proposed'",
                               (sc.charter_id,)):
                print(f"proposed {p['category_id']} (proposal {p['proposal_id']}, run {p['run_id']})")
        return 0
    raise Refused(f"unknown eval command {op}")


def monitor_cmd(a, cat_url):
    cat, sc = _ctx(a, cat_url)
    cfg = mc.load_config(getattr(a, "config", None))
    actor = a.as_ or "konmari-monitor"
    op = a.op
    if op == "run":
        rep = mc.run(cat, sc, cfg, actor, dry_run=a.dry_run)
        for u in rep["unmonitorable"]:
            print(f"unmonitorable: {u['asset']} ({u['reason']})")
        for r in rep["results"]:
            if r["status"] != "ok" or a.dry_run:
                print(f"{r['status']}: {r['check_name']} {r['asset_id']} observed {json.dumps(r['observed'])} "
                      f"expected {json.dumps(r['expected'])}")
        print(f"monitor run {rep['run_id']}{' (dry run, nothing written)' if a.dry_run else ''}: {rep['assets']} "
              f"asset(s), {len(rep['results'])} check(s)")
        return 0
    if op == "threshold":
        if not a.as_:
            raise Refused("--as is required (the asset owner)")
        r = mc.set_threshold(cat, sc, a.asset, a.as_, a.warn_after_days, a.error_after_days, a.reason)
        print(f"{a.asset}: warn after {r['warn_after_days'] or 'interval'} d, error after "
              f"{r['error_after_days'] or '2x interval'} d")
        return 0
    if op == "incidents":
        _out(cat.query("SELECT * FROM incident" + (" WHERE status = ?" if a.status else "") + " ORDER BY opened_at",
                       (a.status,) if a.status else ()))
        return 0
    if op == "incident":
        if not a.as_:
            raise Refused("--as is required")
        r = mi.decide(cat, sc, a.incident, a.action, a.as_, a.reason)
        print(f"incident {a.incident}: {r['status']}")
        return 0
    if op == "drift":
        logs = {"questions": mc.read_log(a.questions)} | ({"content": mc.read_log(a.content)} if a.content else {})
        rows = mc.drift(cat, sc, logs, cfg, actor, mi.notifier(cfg), window_days=a.window_days,
                        method=mc.EvidentlyDrift() if a.evidently else None)
        for r in rows:
            print(f"{r['status']}: {r['subject']} drift {r['score']} ({r['method']}, threshold {r['threshold']}); "
                  f"reference {r['reference_window']}, current {r['current_window']}")
        return 0
    if op == "import":
        r = mc.import_results(cat, sc, a.file, a.kind, actor, mi.notifier(cfg))
        print(f"imported {r['imported']} result(s), skipped {len(r['skipped'])}")
        return 0
    if op == "schedule":
        s = mc.schedule(a.catalog, a.every_hours)
        print(s["cron"] if a.format == "cron" else f"# konmari-monitor.service\n{s['service']}\n"
                                                   f"# konmari-monitor.timer\n{s['timer']}")
        return 0
    raise Refused(f"unknown monitor command {op}")


def register(groups, ccmd) -> None:
    ev = groups.add_parser("eval", help="agent evaluation: golden set, runs through MCP, judge calibration, gate")
    evs = ev.add_subparsers(dest="cmd", required=True)

    def ecmd(sub, name, help, actor="required", op=None):
        q = ccmd(sub, name, eval_cmd, help, actor)
        q.set_defaults(op=op or name)
        q.add_argument("--pilot", help="pilot (charter ID); default: the charter's")
        return q

    it = evs.add_parser("items", help="golden items: import, approve, list, retention").add_subparsers(dest="sub",
                                                                                                  required=True)
    q = ecmd(it, "import", "import items (CSV or YAML) as drafts; user_log questions are masked first")
    q.add_argument("file")
    q = ecmd(it, "approve", "approve an item version (domain expert or source owner)")
    q.add_argument("item")
    q.add_argument("--version", type=int)
    ecmd(it, "list", "latest version of every item", actor=None)
    ecmd(it, "retention", "delete user-derived items past their retention date")
    st = evs.add_parser("set", help="golden set versions").add_subparsers(dest="sub", required=True)
    ecmd(st, "activate", "activate a new set version (>= 50 items, >= 20 % negative, <= 50 % synthetic)")
    q = ecmd(evs, "run", "run the active set against the agent's MCP interface")
    q.add_argument("--config", default="eval.yaml", help="agent, test principal and judge (default: eval.yaml)")
    q = ecmd(evs, "report", "a run with per-item results as JSON", actor=None)
    q.add_argument("--run")
    q = ecmd(evs, "gate", "thresholds and baseline regression (exit 1 on failure)", actor="optional")
    q.add_argument("--run")
    q.add_argument("--json", action="store_true")
    q = ecmd(evs, "baseline", "approve a run as baseline, or revert to the previous one")
    q.add_argument("action", choices=["approve", "revert"])
    q.add_argument("run", nargs="?")
    q = ecmd(evs, "calibrate", "Cohen's kappa of the judge against human labels (CSV item_id,human,judge)")
    q.add_argument("labels")
    q.add_argument("--judge-model", dest="judge_model", required=True)
    q.add_argument("--judge-version", dest="judge_version", required=True)
    q.add_argument("--tuning", help="file with the item IDs used to tune the judge prompt (must not overlap)")
    q = ecmd(evs, "next-category", "show proposals, or --confirm one", actor="optional")
    q.add_argument("--confirm", metavar="PROPOSAL")

    mo = groups.add_parser("monitor", help="freshness, quality and drift monitoring with owner-routed incidents")
    mos = mo.add_subparsers(dest="cmd", required=True)

    def mcmd(name, help, op=None):
        q = ccmd(mos, name, monitor_cmd, help, "optional")
        q.set_defaults(op=op or name)
        q.add_argument("--config", default="monitor.yaml", help="monitor settings (default: monitor.yaml)")
        return q

    q = mcmd("run", "check every in-scope source and data product; alerts, incidents, scorecard, lineage")
    q.add_argument("--dry-run", dest="dry_run", action="store_true", help="compute and print, write nothing")
    q = mcmd("threshold", "owner override of an asset's freshness tiers (days)")
    q.add_argument("asset")
    q.add_argument("--warn-after-days", dest="warn_after_days", type=float)
    q.add_argument("--error-after-days", dest="error_after_days", type=float)
    q.add_argument("--reason")
    q = mcmd("incidents", "list incidents")
    q.add_argument("--status")
    q = mcmd("incident", "acknowledge, resolve or accept the risk of an incident (named human, reason)")
    q.add_argument("action", choices=list(mi.TRANSITIONS))
    q.add_argument("incident")
    q.add_argument("--reason", required=True)
    q = mcmd("drift", "question and content drift against the previous window (JSONL ts,text)")
    q.add_argument("--questions", required=True)
    q.add_argument("--content")
    q.add_argument("--window-days", dest="window_days", type=int, default=7)
    q.add_argument("--evidently", action="store_true", help="use Evidently (installed separately, needs pandas<3)")
    q = mcmd("import", "import dbt source freshness or Soda scan results")
    q.add_argument("kind", choices=["dbt", "soda"])
    q.add_argument("file")
    q = mcmd("schedule", "cron line or systemd timer for 'konmari monitor run'")
    q.add_argument("--format", choices=["cron", "systemd"], default="cron")
    q.add_argument("--every-hours", dest="every_hours", type=int, default=24)
