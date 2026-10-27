# Working with Claude Code

The KonMari-Kit has two layers. The **CLI** (`konmari`) is the engine: it checks every gate,
records every decision with identity and time, and refuses what the charter does not allow. The
**skill** (`skills/konmari/`) lets Claude Code operate that CLI for you. Claude does the legwork;
the people your charter names decide.

## Install

In Claude Code:

```
/plugin marketplace add KI-Klub-Werder-e-V/MarieKondoForAgents
/plugin install konmari@konmari-kit
```

You need Python 3.12 and [uv](https://docs.astral.sh/uv/). Claude runs the CLI with
`uvx --from git+https://github.com/KI-Klub-Werder-e-V/MarieKondoForAgents konmari` (or
`uv run konmari` inside a clone), so there is nothing else to install.

Open Claude Code in an empty folder for your pilot and say what you want: "Räum unsere Daten
für einen FAQ-Agenten auf" or "Tidy our data for a support agent". To try it on fictional data
first, ask Claude to copy `examples/verein-faq-assistant/` into a temp folder.

## Who does what

| Claude (the skill) | People named in the charter |
|---|---|
| interviews you and drafts `charter.yaml`, `metrics.yaml`, specs, product descriptors | decide scope, roles, data protection screening, targets |
| registers sources, runs scans, detectors, chunking, PII scans, tests | approve the charter, metrics, category order, verdicts, discards, policies, products |
| reads every report and explains it in plain words | supply baselines, measurements, labels, golden questions and answers |
| writes a handoff note when someone else must approve | confirm each approval in the conversation, or run it themselves |

Claude never runs a command that records an approval until it has shown you what it is about and
you have confirmed that you are the person the charter names (or authorised by them). It never
invents a baseline, a label or an answer. Data stays on your machine.

## The loop: `konmari next`

The skill works in a loop around one read-only command:

```
konmari next          # human-readable
konmari next --json   # what the skill reads
```

It prints the state of the charter and the metric set, every in-scope category in the approved
order with its state per step, and the first unfinished item: its status (`ready`, `blocked`,
`needs_approval`, `needs_input`, `done`), every unmet reason, the exact next command(s), who must
approve (`approval_needed`) and which values only a person can supply (`input_needed`). It writes
nothing, not even an audit event, and always exits 0. You can use it without Claude too.

Because every decision lives in the catalog, you can stop at any time. To continue, open the
pilot folder again and run `konmari next`, or ask Claude to continue.

## Connecting your agent

Once a data product is published, `konmari serve --stdio` gives agents read-only access. How to add
it to Claude Code with `claude mcp add`, and the agent contract that `konmari eval run` uses:
[`skills/konmari/references/connect-agent.md`](../skills/konmari/references/connect-agent.md).
