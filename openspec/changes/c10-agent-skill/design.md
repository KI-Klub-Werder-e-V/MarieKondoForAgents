## Context

The CLI is complete for all nine steps and every decision is gated, audited and attributed to a stakeholder. What is missing is an operator: someone has to know which of about a hundred commands comes next, with which `--as`. The owner wants Claude Code to be that operator, through a skill, while the CLI stays the only thing that decides anything. The sibling repo `kommentarwache` already ships a Claude Code plugin from its repository root (`.claude-plugin/` plus `skills/`), so we follow its layout.

## Goals / Non-Goals

**Goals:**
- One deterministic primitive (`konmari next`) that tells the agent where the pilot stands and what the next command is, so the skill can loop on it instead of re-deriving the state from guides.
- A skill that does the legwork (drafting, scanning, reading reports, explaining) and never makes a decision that belongs to a stakeholder.
- One-command install for Claude Code users, and `uvx` use without a clone.

**Non-Goals:**
- A planner that knows every sub-command state of steps 4–9. `next` reports the per-category step state and the step's command sequence; the reference files explain how to read each step's own reports.
- An MCP server in the plugin. `konmari serve` only works in a pilot folder after products are published; `references/connect-agent.md` explains how to add it per pilot.
- Any automation of approvals.

## Decisions

1. **`next` is computed, not stored.** It reads the charter file, the catalog and the c02 step table and keeps no state of its own. *Alternative:* a `progress` table written by each step. Rejected because it would be a second source of truth next to `category_step` and the versions tables.
2. **Pure gate check.** `gate()` audits every call by contract (gate-contract.md), so `next` cannot call it. We split it into `check()` (pure) and `gate()` = `check()` + audit. The audit event and the `GateResult` stay identical.
3. **No catalog is created by `next`.** For a DuckDB URL whose file does not exist, `next` treats the catalog as empty instead of calling `connect()`, which would create and migrate it. Existing catalogs are opened with `connect()` as every other command does (migrations are idempotent).
4. **Order of the first unfinished item.** Charter → metric set → baselines → inventory (sources, scan/import, record completeness, category assignment, order, handoff) → steps `score` … `evaluate`, step-major: the earliest step not complete for some category with accepted assets, then the first such category in the approved order. Because `check_step` makes later categories wait for earlier ones within a step, this order never points at an item that `check_step` would refuse for order reasons, and most step commands run pilot-wide anyway.
5. **Approvers come from the charter only.** Charter approval: `raci.charter_approval.A`; metrics: `raci.metric_approval.A`; baselines: the metric's `owner`; category decisions, order approval and step completion: the category's `approver` from `data_scope.categories`; score reviews: the category's first reviewer. Legwork commands get `--as <you>`; the skill asks the user once which stakeholder they are.
6. **Step completion is an approval.** Except for `score` (completed by the review) and `inventory` (completed by the handoff), the CLI does not mark a category step complete by itself. `next` therefore lists the step's commands followed by `konmari categories step <c> <step> --status complete --as <approver>` and reports it as `approval_needed`.
7. **The skill is small and points at references.** SKILL.md holds the loop and the hard rules (under 250 lines); one reference per step condenses the step guide. A guard test checks every command in them against the argparse tree, so the skill cannot drift from the CLI.

## Risks / Trade-offs

- [The agent runs an approval without real consent] → The approval protocol is a hard rule in SKILL.md; `next` names the approver so the agent cannot pick one; the CLI still refuses anyone without the role, and every approval is audited with identity and OS user.
- [Step 4–9 guidance is coarse] → `next` gives the command sequence per step; the references explain the step's own reports and blockers. A finer planner can be added later without changing the JSON shape.
- [Documented commands drift from the CLI] → guard test over skills, `docs/cli.de.md` and the `next` command templates.
- [Data leaves the machine through the agent] → The skill forbids sending file contents to external services unless the charter allows a processor; scans and PII detection stay local.

## Migration Plan

Additive. `gate()` keeps its signature and audit behaviour. Roll back by removing the plugin files and the `next` command.

## Open Questions

- Should `next` later read sub-step state for steps 4–9 (open discard proposals, quarantine, open conflicts) to point at one concrete command?
