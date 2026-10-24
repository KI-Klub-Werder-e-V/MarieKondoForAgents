import copy
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from konmari.canonical import dump_yaml, load_yaml
from konmari.catalog import connect
from konmari.cli import main

EXAMPLE = Path(__file__).parent.parent / "examples" / "verein-faq-assistant"


@pytest.fixture
def charter_doc():
    return copy.deepcopy(load_yaml(EXAMPLE / "charter.yaml"))


@pytest.fixture
def engagement(tmp_path, monkeypatch):
    """A copy of the example engagement as cwd, with a fresh DuckDB catalog."""
    d = tmp_path / "engagement"
    shutil.copytree(EXAMPLE, d)
    monkeypatch.chdir(d)
    monkeypatch.setenv("KONMARI_CATALOG", f"duckdb:///{tmp_path}/catalog.duckdb")
    return d


@pytest.fixture
def cli(capsys):
    def run(*args):
        code = main([str(a) for a in args])
        out = capsys.readouterr()
        return code, out.out, out.err
    return run


@pytest.fixture
def cat(engagement):
    c = connect()
    yield c
    c.close()


def edit_yaml(path, fn):
    doc = load_yaml(Path(path))
    fn(doc)
    Path(path).write_text(dump_yaml(doc), encoding="utf-8")


def _pg_bin():
    for d in [os.environ.get("KONMARI_PG_BIN"), *sorted(Path("/usr/lib/postgresql").glob("*/bin"), reverse=True)]:
        if d and (Path(d) / "initdb").exists():
            return Path(d)
    found = shutil.which("initdb")
    return Path(found).parent if found else None


@pytest.fixture(scope="session")
def pg_server(tmp_path_factory):
    """Postgres from $KONMARI_TEST_PG_URL (CI service) or a throwaway local cluster; skipped if neither exists."""
    if url := os.environ.get("KONMARI_TEST_PG_URL"):
        yield url
        return
    pytest.importorskip("psycopg")
    bindir = _pg_bin()
    if bindir is None:
        pytest.skip("no Postgres available (set KONMARI_TEST_PG_URL or install postgresql)")
    data = tmp_path_factory.mktemp("pgdata")
    sock = Path(tmp_path_factory.mktemp("pgsock"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    subprocess.run([bindir / "initdb", "-D", data, "-A", "trust", "-U", "konmari"], check=True, capture_output=True)
    subprocess.run([bindir / "pg_ctl", "-D", data, "-w", "-l", data / "log", "-o",
                    f"-p {port} -k {sock} -c listen_addresses=''", "start"], check=True, capture_output=True)
    try:
        yield f"postgresql://konmari@/postgres?host={sock}&port={port}"
    finally:
        subprocess.run([bindir / "pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True)


@pytest.fixture
def pg_url(pg_server, request):
    """A fresh database per test."""
    import psycopg

    name = "t_" + request.node.name.lower().replace("[", "_").replace("]", "").replace("-", "_")[:50]
    with psycopg.connect(pg_server, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        c.execute(f'CREATE DATABASE "{name}"')
    base, _, query = pg_server.partition("?")
    return f"{base.rsplit('/', 1)[0]}/{name}" + (f"?{query}" if query else "")


# ---------------------------------------------------------------- c02 fixtures

@pytest.fixture(scope="session", autouse=True)
def blank_nlp_model(tmp_path_factory):
    """A blank German spaCy pipeline: the Presidio pattern recognizers run offline without the 500 MB model."""
    import spacy

    d = tmp_path_factory.mktemp("nlp") / "de_blank"
    spacy.blank("de").to_disk(d)
    old = os.environ.get("KONMARI_NLP_MODEL")
    os.environ["KONMARI_NLP_MODEL"] = str(d)
    yield str(d)
    if old is None:
        os.environ.pop("KONMARI_NLP_MODEL", None)
    else:
        os.environ["KONMARI_NLP_MODEL"] = old


SP_SITE = "contoso.sharepoint.com/sites/support"


def approve_pilot(cli, sources, categories=None, approvers=None):
    """Point the engagement charter at `sources`, put all five default categories in scope, approve
    charter and metrics, and record baselines so the inventory gate passes."""
    cats = categories or ["kb-and-sops", "customer-records", "policies-and-contracts", "financial-ops-metrics",
                          "support-history"]

    def fn(d):
        d["data_scope"]["sources"] = sources
        d["data_scope"]["categories"] = [{"id": c, "approver": (approvers or {}).get(c, "ben"), "reviewers": ["clara"]}
                                         for c in cats]
    edit_yaml("charter.yaml", fn)
    for args in [("charter", "approve", "--as", "anna"), ("metrics", "approve", "--as", "ben"),
                 ("metrics", "baseline", "answer_accuracy", "--value", 0.4, "--method", "m", "--as", "clara"),
                 ("metrics", "baseline", "abstention_rate", "--value", 0.2, "--method", "m", "--as", "clara"),
                 ("metrics", "baseline", "human_approved_decisions", "--value", 100, "--method", "m", "--as", "clara"),
                 ("metrics", "baseline", "time_to_answer", "--not-yet-measured", "--plan-date", "2026-03-01",
                  "--as", "ben")]:
        code, out, err = cli(*args)
        assert code == 0, (args, err)


@pytest.fixture
def pilot(engagement, cli, tmp_path, monkeypatch, request):
    """Approved fixture pilot with a local file share, a SQLite CRM, a SharePoint site and a CSV import.
    Tests that also request `pg_url` get a Postgres catalog instead of DuckDB."""
    if "pg_url" in request.fixturenames:
        monkeypatch.setenv("KONMARI_CATALOG", request.getfixturevalue("pg_url"))
    share, csvf = tmp_path / "share", tmp_path / "manual.csv"
    share.mkdir()
    monkeypatch.setenv("KONMARI_LINEAGE_FILE", str(tmp_path / "lineage.jsonl"))
    sources = [
        {"id": "office-share", "connector": "fileshare", "locator": str(share),
         "categories": ["kb-and-sops", "policies-and-contracts"]},
        {"id": "crm-db", "connector": "sql", "locator": f"sqlite:///{tmp_path}/crm.db",
         "categories": ["customer-records", "support-history"]},
        {"id": "support-site", "connector": "sharepoint", "locator": SP_SITE,
         "categories": ["support-history", "policies-and-contracts"]},
        {"id": "manual-crm", "connector": "csv-import", "locator": str(csvf), "categories": ["customer-records"]},
    ]
    approve_pilot(cli, sources)
    return {"share": share, "csv": csvf, "db": tmp_path / "crm.db", "sqlite": f"sqlite:///{tmp_path}/crm.db",
            "lineage": tmp_path / "lineage.jsonl", "dir": engagement, "tmp": tmp_path}


def valid_tax_id(seed: int) -> str:
    """Synthetic Steueridentifikationsnummer with a valid ISO 7064 MOD 11,10 check digit."""
    import random
    from collections import Counter

    rnd = random.Random(seed)
    while True:
        d = [rnd.randint(1, 9)] + [rnd.randint(0, 9) for _ in range(9)]
        if max(Counter(d).values()) <= 3:
            break
    product = 10
    for x in d:
        total = (x + product) % 10 or 10
        product = (total * 2) % 11
    check = 11 - product
    return "".join(map(str, d)) + str(0 if check == 10 else check)


def valid_kvnr(seed: int) -> str:
    """Synthetic Krankenversichertennummer (letter + 8 digits + check digit)."""
    import random

    rnd = random.Random(seed)
    letter = chr(ord("A") + rnd.randint(0, 25))
    body = "".join(str(rnd.randint(0, 9)) for _ in range(8))
    eff = str(ord(letter) - ord("A") + 1).zfill(2) + body
    total = sum((p // 10 + p % 10) for p in (int(c) * f for c, f in zip(eff, [1, 2] * 5)))
    return letter + body + str(total % 10)


def valid_iban(seed: int) -> str:
    import random

    rnd = random.Random(seed)
    bban = "".join(str(rnd.randint(0, 9)) for _ in range(18))
    check = 98 - int(bban + "131400") % 97  # D=13, E=14
    return f"DE{check:02d}{bban}"


def fake_token(*roles) -> str:
    import base64
    import json

    enc = lambda o: base64.urlsafe_b64encode(json.dumps(o).encode()).decode().rstrip("=")  # noqa: E731
    return f"{enc({'alg': 'none'})}.{enc({'roles': list(roles)})}.sig"


def dump_catalog(cat) -> str:
    """Every value in every catalog table, as one string (for 'never stored' checks)."""
    out = []
    for t in cat.query("SELECT table_name FROM information_schema.tables WHERE table_schema IN ('main', 'public')"):
        out += [repr(r) for r in cat.query(f"SELECT * FROM {t['table_name']}")]
    return "\n".join(out)


# ---------------------------------------------------------------- c04 fixtures

DP_ASSETS = {  # asset_uri: (category, sensitivity tier)
    "manual://kb/sops": ("kb-and-sops", "internal"),
    "manual://kb/faq-export": ("kb-and-sops", "public"),
    "manual://support/tickets": ("support-history", "internal"),
    "manual://policies/contracts": ("policies-and-contracts", "confidential"),
    "manual://finance/payroll": ("financial-ops-metrics", "personal"),
    "manual://crm/invoices": ("customer-records", "personal"),
    "manual://hr/applications": ("customer-records", "personal"),
}
DP_CATEGORIES = ["kb-and-sops", "support-history", "policies-and-contracts", "customer-records"]


@pytest.fixture
def dp(engagement, cli, tmp_path, monkeypatch, request):
    """Approved support pilot (financial-ops-metrics out of scope) with csv-import assets handed off through c02 and
    scored (c03 step complete); `items([...])` writes synthetic item files plus a manifest."""
    import csv
    import json

    from konmari.categories import store as cats
    from konmari.charter.store import scope

    if "pg_url" in request.fixturenames:
        monkeypatch.setenv("KONMARI_CATALOG", request.getfixturevalue("pg_url"))
    monkeypatch.setenv("KONMARI_LINEAGE_FILE", str(tmp_path / "lineage.jsonl"))
    monkeypatch.setenv("KONMARI_ARCHIVE", str(tmp_path / "archive"))
    csvf = tmp_path / "assets.csv"
    approve_pilot(cli, [{"id": "manual-assets", "connector": "csv-import", "locator": str(csvf),
                         "categories": DP_CATEGORIES}], DP_CATEGORIES, {"policies-and-contracts": "clara"})

    def ok(*args):
        code, out, err = cli(*args)
        assert code == 0, (args, err)
        return out

    ok("metrics", "baseline", "time_to_answer", "--value", 120, "--method", "stopwatch", "--as", "ben")
    cols = ["asset_uri", "title", "system_type", "owner", "steward", "update_frequency", "format", "sensitivity_tier",
            "ropa_ref"]
    with csvf.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        for uri, (_, tier) in DP_ASSETS.items():
            w.writerow({"asset_uri": uri, "title": uri.rsplit("/", 1)[-1], "system_type": "manual", "owner": "ben",
                        "steward": "clara", "update_frequency": "P1M", "format": "text/plain", "sensitivity_tier": tier,
                        "ropa_ref": "ropa-7"})
    ok("inventory", "source", "add", "--connector", "csv-import", "--locator", csvf, "--as", "dirk")
    ok("inventory", "import", csvf, "--as", "dirk")
    for uri, (c, _) in DP_ASSETS.items():
        ok("categories", "assign", uri, c, "--as", "ben")
    ok("categories", "order", "--propose", "--as", "ben")
    ok("categories", "approve", "--as", "ben")
    ok("categories", "handoff")
    cat = connect()
    sc = scope("verein-faq-assistant", cat)
    for c in cats.approved_order(cat, sc.charter_id)["categories"]:
        if c in sc.categories:
            cats.set_step(cat, sc, c, "score", "complete", "ben")
            cats.waive_step(cat, sc, c, "discard", "ben", "fixture: all categories in one discard pass")
    files = tmp_path / "items"
    files.mkdir()
    manifest = tmp_path / "items.jsonl"

    def items(entries, extra_lines=()):
        """entries: dicts with asset_uri, name, text and optional metadata keys."""
        with manifest.open("w", encoding="utf-8") as f:
            for e in entries:
                e = dict(e)
                p = files / e.pop("name")
                data = e.pop("text")
                p.write_bytes(data if isinstance(data, bytes) else data.encode())
                f.write(json.dumps({"path": str(p), **e}) + "\n")
            for line in extra_lines:
                f.write(json.dumps(line) + "\n")
        return manifest

    yield {"cat": cat, "ok": ok, "items": items, "manifest": manifest, "files": files, "tmp": tmp_path,
           "lineage": tmp_path / "lineage.jsonl", "archive": tmp_path / "archive"}
    cat.close()


# ---------------------------------------------------------------- c05 fixtures

def example_scope():
    """Scope with the example charter's stakeholders (ben, clara: data owners; eva: compliance; finn: agent user)."""
    from konmari.charter.store import Scope

    doc = load_yaml(EXAMPLE / "charter.yaml")
    return Scope(doc["charter_id"], 1, [], {}, {s["id"]: s for s in doc["stakeholders"]})


@pytest.fixture(scope="session")
def sot_pipeline(tmp_path_factory):
    """The synthetic c05 pilot run once: normalize, resolve, 120 labels, publish, policy v1 approved, golden built.
    Tests get their own copy through `sot`."""
    import sot_fixture as fx
    from konmari.inventory.lineage import MemoryTransport
    from konmari.normalize import store as nst
    from konmari.policy import store as pst
    from konmari.resolve import linkage as erl

    d = tmp_path_factory.mktemp("sot")
    f = fx.write(d)
    db = d / "catalog.duckdb"
    cat, sc = connect(f"duckdb:///{db}"), example_scope()
    t = MemoryTransport()
    norm = nst.run(cat, f["records"], f["specs"], "ben", t)
    er = erl.run(cat, sc, "customer", erl.load_config(f["resolve"]), "ben")
    known = set(fx.known_duplicates())
    auto = cat.query("SELECT rec_a, rec_b FROM er_pair WHERE origin = 'auto' ORDER BY rec_a, rec_b LIMIT 120")
    erl.label_pairs(cat, sc, "customer", [(r["rec_a"], r["rec_b"], "match" if (r["rec_a"], r["rec_b"]) in known
                                           else "non_match") for r in auto], "clara")
    erl.publish(cat, sc, "customer", "ben", sorted(known))
    pst.load(cat, sc, f["policy"], "ben")
    pst.approve(cat, sc, "customer", 1, "ben")
    cat.close()
    return {"db": db, "files": f, "norm": norm, "er": er, "events": t.events}


@pytest.fixture
def sot(sot_pipeline, tmp_path, monkeypatch):
    from konmari.inventory.lineage import MemoryTransport

    db = tmp_path / "catalog.duckdb"
    shutil.copy(sot_pipeline["db"], db)
    monkeypatch.setenv("KONMARI_CATALOG", f"duckdb:///{db}")
    monkeypatch.setenv("KONMARI_LINEAGE_FILE", str(tmp_path / "lineage.jsonl"))
    monkeypatch.setenv("KONMARI_NOTIFY_FILE", str(tmp_path / "notify.jsonl"))
    c = connect(f"duckdb:///{db}")
    yield {**sot_pipeline, "cat": c, "sc": example_scope(), "tmp": tmp_path, "transport": MemoryTransport(),
           "notify": tmp_path / "notify.jsonl"}
    c.close()


def write_mini(d: Path, records: list[dict], fields: list[dict]) -> tuple[Path, Path]:
    """A small records file plus one spec file for focused normalization tests."""
    import json

    d.mkdir(parents=True, exist_ok=True)
    (d / "normalize").mkdir(exist_ok=True)
    (d / "normalize" / "spec.yaml").write_text(dump_yaml({"fields": fields}), encoding="utf-8")
    rec = d / "records.jsonl"
    rec.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    return rec, d / "normalize"


@pytest.fixture
def sot_cli(engagement, sot, cli):
    """`sot` plus the example engagement as cwd with an approved charter, for CLI commands that need the scope."""
    code, _, err = cli("charter", "approve", "--as", "anna")
    assert code == 0, err
    return sot
