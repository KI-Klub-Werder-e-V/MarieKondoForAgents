# Working in this repo

The KonMari-Kit makes one pilot use case's data agent-ready: charter → inventory → joy test →
discard → normalize → enrich → govern → connect (MCP) → evaluate. Read `README.md` for what it
does and `openspec/ROADMAP.md` for how the nine steps fit together.

## Commands

```bash
uv sync                          # install (extras: postgres, smb, semantic, s3, docling)
uv run konmari --help            # the CLI
uv run pytest -q                 # tests; Postgres variants use KONMARI_TEST_PG_URL or a local PG16
openspec validate --all --strict # specs
```

The catalog defaults to `.konmari/catalog.duckdb`; set `KONMARI_CATALOG=postgresql://...` or
`--catalog` for Postgres. Migrations must run unchanged on both.

## Rules that are easy to break

- Behaviour changes start as an OpenSpec change in `openspec/changes/`. Every `#### Scenario`
  has a test named after it that asserts the THEN clause.
- The kit proposes, humans decide. Anything that discards, masks, erases, publishes or widens
  access needs an approval by the role the charter assigns, recorded with identity and time.
- Decisions are reversible and append-only. Never update or delete audit or decision rows;
  record a new decision that supersedes the old one.
- Steps are gated. A command for step N calls the gate for step N−1 (`docs/gate-contract.md`);
  do not bypass it.
- No secrets in the catalog, logs or locators. Credentials come in as secret references
  (`--secret-ref env:NAME`); the kit refuses URLs with embedded passwords.
- PII detection runs locally. No real personal data in tests, fixtures or examples.
- External systems (SharePoint, CRMs, models) sit behind a small interface with a fake adapter;
  tests never call real services.
- Every `konmari …` command in `skills/**/*.md` and `docs/cli.md` and `docs/cli.de.md` must exist in the parser
  (`tests/test_skill_commands.py`). `konmari next` stays read-only: no audit events, no writes.
- Thresholds are named, versioned config values with a source or an "assumption" note in
  `research/`.

## Layout

```
src/konmari/     CLI and one package per step (charter, inventory, categories, scorecard, discard, …);
                 guided.py is `konmari next`, the read-only "where are we, what's next" report
skills/konmari/  Claude Code skill that operates the CLI (SKILL.md plus references/ per step)
.claude-plugin/  plugin.json and marketplace.json: the repo root is the plugin
tests/           pytest, one file per capability, tests named after spec scenarios
docs/            guides per step (de/en) and contracts between steps
examples/        fictional pilot (verein-faq-assistant)
openspec/        config, ROADMAP, the nine step changes and c10-agent-skill
research/        source brief and research notes with sources
```
