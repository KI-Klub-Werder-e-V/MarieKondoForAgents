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
