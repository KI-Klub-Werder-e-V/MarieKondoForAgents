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


def approve_pilot(cli, sources, categories=None):
    """Point the engagement charter at `sources`, put all five default categories in scope, approve
    charter and metrics, and record baselines so the inventory gate passes."""
    cats = categories or ["kb-and-sops", "customer-records", "policies-and-contracts", "financial-ops-metrics",
                          "support-history"]

    def fn(d):
        d["data_scope"]["sources"] = sources
        d["data_scope"]["categories"] = [{"id": c, "approver": "ben", "reviewers": ["clara"]} for c in cats]
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
