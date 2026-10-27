## What changes

<!-- One or two sentences. For behaviour changes: which OpenSpec change? -->

OpenSpec change: `openspec/changes/<name>` (or: bug fix, no requirement changed)

## Checklist

- [ ] `uv run pytest -q` passes
- [ ] `openspec validate --all --strict` passes
- [ ] Every new or changed scenario has a test that asserts its THEN clause
- [ ] No real personal or confidential data
- [ ] New thresholds have a source or are marked as a default in `research/`
- [ ] Guides (`docs/*-guide.*.md`) and `docs/cli.md` updated if commands changed
