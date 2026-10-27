## ADDED Requirements

### Requirement: Installable Claude Code skill
The repository SHALL ship a Claude Code plugin with one skill `konmari` at `skills/konmari/SKILL.md` whose frontmatter has a `name` and a trigger-rich `description` (English, with German trigger phrases), plus `.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json` with the plugin at the repository root (`source: "./"`). The plugin MUST NOT declare an MCP server.

#### Scenario: Plugin manifests and frontmatter are valid
- **WHEN** the test suite runs
- **THEN** both plugin JSON files parse, name the plugin `konmari`, declare no MCP server, and SKILL.md has a `name` and a `description`

### Requirement: Documented commands exist
Every `konmari …` command in a code block of `skills/**/*.md` and `docs/cli.de.md`, and every command template `konmari next` can print, SHALL resolve to a real subcommand path in the CLI argument parser, and every `--flag` it uses SHALL exist on that subcommand. Placeholders in angle brackets are allowed.

#### Scenario: Every documented command exists in the CLI
- **WHEN** the guard test extracts the commands
- **THEN** each subcommand path and each flag is found in the argparse tree

### Requirement: Approval protocol
The skill SHALL state as a hard rule that a command whose `--as` records an approval or decision runs only after the agent has shown what is being approved and the user has confirmed in the conversation that they are that person or authorised by them; it MUST NOT infer consent, batch approvals silently or use an approver the charter does not name, and it SHALL write a handoff note instead when the approver is someone else. It SHALL forbid inventing baselines, measurements, labels or golden-set answers.

#### Scenario: Skill states the approval protocol
- **WHEN** the skill text is read
- **THEN** it contains the approval protocol section, the handoff note rule and the rule against invented values

### Requirement: Installable from git
The package SHALL build as a wheel that contains the CLI, its migrations, schemas and charter templates, so `uvx --from git+https://github.com/KI-Klub-Werder-e-V/MarieKondoForAgents konmari` works outside a clone.

#### Scenario: Wheel contains the CLI and its data files
- **WHEN** the wheel is built with `uv build`
- **THEN** it contains `konmari/cli.py`, the SQL migrations, the JSON schemas and the charter templates, and declares the `konmari` console script
