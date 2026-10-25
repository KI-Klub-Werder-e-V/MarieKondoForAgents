## Why

The nine steps are complete, but running them means reading nine guides and typing about a hundred commands in the right order with the right `--as`. The people we built the kit for (club offices, small companies, public bodies) mostly will not do that. Many of them already use Claude Code. If an agent can operate the deterministic CLI for them, the kit becomes usable without a volunteer who knows every command, while every decision stays with the person the charter names.

## What Changes

- A read-only `konmari next [--json]` command: charter and metric-set state, then every in-scope category in the approved order with its per-category step state, and for the first unfinished item its status (`ready`, `blocked`, `needs_approval`, `needs_input`, `done`), every unmet reason and the exact next command(s). Approval commands carry `--as` with the stakeholder the charter names for that decision. Values only a human can supply are flagged `needs_input`, never filled in.
- A pure gate check (`konmari.charter.gate.check`) that `gate()` and `next` share; `gate()` keeps auditing every call.
- A Claude Code skill `skills/konmari/` that drives the CLI end to end in a loop around `konmari next`, with a hard approval protocol, plus one reference file per step and one for connecting an agent.
- Plugin packaging (`.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`) so the skill installs with `/plugin marketplace add` and `/plugin install`.
- A guard test that every `konmari …` command in the skill and in `docs/cli.de.md` exists in the real argument parser.
- README (de/en) leads with the Claude Code path; `docs/agents.md` explains how the skill and the CLI divide the work.

## Capabilities

### New Capabilities
- `guided-operation`: the read-only `konmari next` status and next-step report the agent loops on.
- `agent-skill`: the Claude Code skill and plugin packaging, the approval protocol it must follow, and the checks that keep its commands in sync with the CLI.

### Modified Capabilities
- (none; `gate()` behaviour and its audit event are unchanged)

## Impact

- Code: new module `src/konmari/guided.py`, new CLI command `konmari next`, `gate()` split into `check()` plus audit.
- New files: `skills/konmari/SKILL.md`, `skills/konmari/references/*.md`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`, `docs/agents.md`, `tests/test_guided_operation.py`, `tests/test_skill_commands.py`.
- Upstream: reads c01 charter/metrics state, c02 category order and step order (`category_step`, `check_step`), c03 reviewers.
- No new dependencies. No MCP server is shipped in the plugin; `konmari serve` only works inside a pilot folder with published products.
