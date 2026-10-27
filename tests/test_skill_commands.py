"""c10 agent-skill: the skill, its references, docs/cli.md and docs/cli.de.md only use commands and flags the CLI has."""

import argparse
import json
import re
import shlex
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

from konmari import guided
from konmari.cli import build_parser

ROOT = Path(__file__).parent.parent
DOCS = sorted((ROOT / "skills").rglob("*.md")) + [ROOT / "docs" / "cli.md", ROOT / "docs" / "cli.de.md"]
PLACEHOLDER = re.compile(r"<[^<>\n]*>")


def code_commands(path: Path) -> list[str]:
    """Every `konmari ...` line inside a fenced code block, continuation lines joined."""
    out, block, buf = [], False, ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("```"):
            block = not block
            continue
        if not block:
            continue
        line = buf + line.strip()
        if line.endswith("\\"):
            buf = line[:-1] + " "
            continue
        buf = ""
        line = line.removeprefix("uv run ")
        if line.startswith("konmari "):
            out.append(line)
    return out


def guided_commands() -> list[str]:
    """Command templates `konmari next` can print (string literals in guided.py)."""
    src = re.sub(r"\{[^{}]*\}", "X", (ROOT / "src" / "konmari" / "guided.py").read_text(encoding="utf-8"))
    return [m.group(1) for m in re.finditer(r"""["'](konmari [^"'\n]*)""", src)]


def _subparsers(p: argparse.ArgumentParser):
    return next((a for a in p._actions if isinstance(a, argparse._SubParsersAction)), None)


def problems(cmd: str) -> list[str]:
    tokens = shlex.split(PLACEHOLDER.sub("PLACEHOLDER", cmd), comments=True)[1:]
    p, path, errs = build_parser(), ["konmari"], []
    for t in tokens:
        sub = _subparsers(p)
        if t.startswith("-") and t != "-":
            flag = t.split("=", 1)[0]
            if flag not in p._option_string_actions:
                errs.append(f"{' '.join(path)}: unknown flag {flag}")
        elif sub and t in sub.choices:
            p = sub.choices[t]
            path.append(t)
    if (sub := _subparsers(p)) and sub.required and not {"-h", "--help"} & set(tokens):
        errs.append(f"{' '.join(path)}: incomplete, needs one of {', '.join(sub.choices)}")
    if len(path) == 1 and not {"-h", "--help"} & set(tokens):
        errs.append(f"unknown command group in: {cmd}")
    return errs


ALL = [(f"{p.relative_to(ROOT)}", c) for p in DOCS for c in code_commands(p)] + \
      [("src/konmari/guided.py", c) for c in guided_commands()]


def test_every_documented_command_exists_in_the_cli():
    assert len(ALL) > 150  # skill, references, cli.md, cli.de.md and the next templates are all read
    bad = [f"{where}: {cmd}\n    " + "; ".join(errs) for where, cmd in ALL if (errs := problems(cmd))]
    assert not bad, "\n".join(bad)


def test_guard_rejects_unknown_commands_and_flags():
    assert problems("konmari charter approve --as anna") == []
    assert problems("konmari charter approve --as anna --bogus")
    assert problems("konmari nonsense run")
    assert problems("konmari charter")


def test_plugin_manifests_and_frontmatter_are_valid():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "konmari" and plugin["version"] == "0.1.0"
    assert plugin["author"]["name"] == "KI Klub Werder e.V."
    assert "mcpServers" not in plugin and not (ROOT / ".mcp.json").exists()
    assert market["name"] and market["owner"]["name"]
    assert [(e["name"], e["source"]) for e in market["plugins"]] == [("konmari", "./")]
    assert "mcpServers" not in market["plugins"][0]
    text = (ROOT / "skills" / "konmari" / "SKILL.md").read_text(encoding="utf-8")
    front = yaml.safe_load(text.split("---")[1])
    assert front["name"] == "konmari"
    assert "Daten für Agenten aufräumen" in front["description"] and "KonMari" in front["description"]
    assert len(text.splitlines()) <= 250


def test_skill_states_the_approval_protocol():
    text = (ROOT / "skills" / "konmari" / "SKILL.md").read_text(encoding="utf-8")
    for phrase in ["## Approval protocol (hard rule)", "explicitly confirmed", "Never infer consent",
                   "Never batch approvals silently", "the charter does not contain", "handoff note",
                   "## Never invent values", "## Data stays local"]:
        assert phrase in text, phrase
    for ref in re.findall(r"`(references/[^`<]+\.md)`", text):
        assert (ROOT / "skills" / "konmari" / ref).is_file(), ref


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not installed")
def test_wheel_contains_the_cli_and_its_data_files(tmp_path):
    subprocess.run(["uv", "build", "--wheel", "--out-dir", str(tmp_path), str(ROOT)], check=True,
                   capture_output=True, timeout=300)
    whl = next(tmp_path.glob("konmari-*.whl"))
    names = zipfile.ZipFile(whl).namelist()
    assert "konmari/cli.py" in names and "konmari/guided.py" in names
    assert any(n.startswith("konmari/migrations/") and n.endswith(".sql") for n in names)
    assert any(n.startswith("konmari/schemas/") and n.endswith(".json") for n in names)
    assert any(n.startswith("konmari/charter/templates/") for n in names)
    entry = next(n for n in names if n.endswith("entry_points.txt"))
    assert "konmari = konmari.cli:main" in zipfile.ZipFile(whl).read(entry).decode()


def test_step_command_templates_cover_every_later_step():
    from konmari.charter.validation import STEPS

    assert set(guided.STEP_COMMANDS) == set(STEPS[2:])
