## 1. Pure gate check

- [ ] 1.1 Split `charter.gate.gate()` into `check()` (pure) and `gate()` (check + audit); test: existing gate tests stay green

## 2. konmari next

- [ ] 2.1 Implement `konmari.guided.report()` for charter, metric set and baselines with approvers from the charter; tests: No charter yet, Invalid charter lists every error, Charter waiting for approval, Metric set waiting for approval, Missing baselines flagged as input
- [ ] 2.2 Add the inventory part (sources, scan/import, record completeness, assignment, order, handoff); tests: Unregistered charter sources come first, Category order approval names the category approver
- [ ] 2.3 Add the per-category step report for score … evaluate; tests: Categories in the approved order, Step completion names the category approver
- [ ] 2.4 Add `konmari next [--json]` to the CLI, read-only, no catalog creation, exit 0; tests: No audit events or state written, Exit code zero while blocked, JSON shape; one Postgres variant

## 3. Skill and plugin

- [ ] 3.1 Write `skills/konmari/SKILL.md` (setup, loop, approval protocol, no invented values, data stays local, stop and resume)
- [ ] 3.2 Write `skills/konmari/references/01-charter.md` … `09-evaluate.md` and `connect-agent.md`
- [ ] 3.3 Add `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` (no MCP server)
- [ ] 3.4 Guard test `tests/test_skill_commands.py`; tests: Every documented command exists in the CLI, Plugin manifests and frontmatter are valid, Skill states the approval protocol, Wheel contains the CLI and its data files

## 4. Install path and docs

- [ ] 4.1 Verify `uv build` and `uvx --from <path> konmari --help`; fix pyproject if needed
- [ ] 4.2 README.md and README.en.md: "Mit Claude Code (empfohlen)" before the CLI quick start; add `docs/agents.md`; update AGENTS.md layout and the ROADMAP
