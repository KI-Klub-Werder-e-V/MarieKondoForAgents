---
name: konmari
description: >-
  Make one pilot use case's organizational data agent-ready with the KonMari-Kit CLI (konmari):
  charter and success metrics, source inventory and categories, joy test (keep/fix/discard),
  discard pass, normalization and source of truth, chunking and metadata, governance (permissions,
  PII masking, audit trail), data products with a read-only MCP server, and evaluation with real
  questions. Use when the user wants to tidy, clean up or prepare data for an AI agent, RAG or
  chatbot, build an MCP server over their own documents or tables, or mentions KonMari or
  Marie Kondo for data. German triggers: "Daten für Agenten aufräumen", "Datenbestand aufräumen",
  "KonMari", "agent-ready data", "Daten agentenfähig machen", "MCP-Server für unsere Daten",
  "FAQ-Agent mit unseren Daten", "Ablage für KI aufräumen", "Pilot-Charta".
---

# KonMari-Kit: tidy one pilot's data for agents

You operate the deterministic `konmari` CLI for the user. **The CLI is the engine and the
record of every decision; you are the operator.** You do the legwork (interviews, drafting YAML,
registering sources, running scans and detectors, reading reports, explaining them in plain words,
proposing fixes). You never make a decision that belongs to a person the charter names.

Speak the user's language (German or English). Explain results without jargon: what was found,
what it means for the agent, what the user has to decide.

## Setup

1. Find the CLI:
   - With a local clone of `KI-Klub-Werder-e-V/MarieKondoForAgents`: `uv sync` in the clone, then
     `uv run --project <clone> konmari ...` (works from the pilot folder; plain `uv run` only inside the clone).
   - Anywhere else: `uvx --from git+https://github.com/KI-Klub-Werder-e-V/MarieKondoForAgents konmari ...`.
     Needs Python 3.12 and [uv](https://docs.astral.sh/uv/). In this file `konmari` stands for
     whichever of the two you use.
2. One working folder per pilot, e.g. `~/konmari/<pilot-name>/`. Run every command from there. It holds
   `charter.yaml`, `metrics.yaml`, the golden set, exports, and the catalog in `.konmari/catalog.duckdb`
   (default). For Postgres set `KONMARI_CATALOG=postgresql://...` (no password in the URL).
3. Ask the user once **which stakeholder they are** (their `id` in the charter). That ID replaces
   `<you>` in legwork commands. If they are not in the register yet, they are added while drafting
   the charter.
4. To learn the steps on fictional data first, copy `examples/verein-faq-assistant/` from the
   repository into a temp folder and run the loop there. Its `office-share` source is the local
   folder `exports/faq/` (a few fictional FAQ pages, one of them a duplicate), `fee-rules` is a CSV export.
5. Scans detect personal data locally with spaCy. Before the first scan install a German model once:
   `de_core_news_lg` for real pilots, or the small `de_core_news_sm` for trials (then set
   `KONMARI_NLP_MODEL=de_core_news_sm`). Install with `uv run --project <clone> python -m spacy download <model>`.
   Ask before downloading the large model (about 500 MB).

## The loop

```
konmari next --json
```

`next` is read-only. It reports the charter and metric-set state, every in-scope category in the
approved order with its state per step, and the **first unfinished item**:

| field | meaning |
|---|---|
| `step`, `category` | where you are (`charter`, `metrics`, `inventory`, `score` … `evaluate`) |
| `status` | `ready`: run the commands. `needs_input`: a human must supply values. `needs_approval`: a named person must decide. `blocked`: an earlier condition is unmet. `done`: pilot complete |
| `reasons` | every unmet condition, verbatim from the CLI |
| `commands` | the exact next command(s); `<...>` are placeholders you fill with real values |
| `approval_needed` | `{who, role, what}`: the stakeholder the charter names for this decision |
| `input_needed` | values only a human can supply, with the person who owns them |

Then:

1. Run `konmari next --json`.
2. Read the reference for the step (`references/0N-<step>.md`) the first time you reach it.
3. Do the item: fill placeholders, run the `ready` commands `next` printed (not others from the
   reference: e.g. `next` picks `inventory import` or `scan` per connector), read their output and
   the step's report, explain it to the user. For `needs_input` and `needs_approval` follow the rules below.
4. Run `konmari next --json` again. Repeat.

Never skip ahead of `next`, never work around a refused command. A refusal (exit 1, `error: ...`)
is information: explain it and fix the cause. Exit 2 is a usage error in your command.

## Approval protocol (hard rule)

Commands whose `--as` records an approval or decision (`charter approve`, `metrics approve`,
`metrics baseline`/`measure`, `categories accept|assign|approve|step ... --status complete|--waive`,
`inventory tier`, `review accept|override|reverse`, `discard approve|reject|bulk-approve|execute|restore`,
`retention rule approve`, `policy approve|rollback`, `conflicts resolve|accept`, `glossary approve`,
`govern policy approve|revert`, `govern review confirm|reject`, `product approve|publish`,
`serve --approve-tool`, `eval items approve`, `eval baseline approve`, `monitor incident ...`, and
any other command that records a decision for a person) run **only** when all of this holds:

1. You have shown the user, in this conversation, **what** is being approved (the item, its
   effect, the evidence) and **who** the charter names for it (`approval_needed.who` and role).
2. The user has explicitly confirmed, in this conversation, that **they are that person or are
   authorised by them** for this specific decision.
3. The `--as` value is exactly the stakeholder the charter names. Never pick another approver,
   never use an ID the charter does not contain, never put the user's ID where the charter names
   someone else.

An explicit statement like "I am Ben" (or "I am Anna and Ben") settles who the user is; small
organisations often have one person in several roles, so do not question it because the charter
lists separate people. Still confirm each decision.

Never infer consent from silence, from an earlier "go ahead", or from the user asking you to
"just do everything". Never batch approvals silently: list each decision (or each bulk command
with the item IDs it covers) and get a confirmation for it.

If the approver is someone else, do not run the command. Write a short **handoff note** the user
can forward:

```
To: <name> (<role> in charter <charter_id>)
Decision: <what, in one sentence>
Why: <evidence, reasons from konmari next>
Command (run in <pilot folder>): konmari <exact command with --as <their id>>
```

Legwork commands with `--as <you>` (register a source, scan, import, suggest, propose, run
detectors, chunk, enrich, scan for PII) record who did the work; they decide nothing and can run
after you have told the user what you are about to do.

## Never invent values

Baselines, measurements, labels (duplicate pairs, PII samples, entity-resolution pairs),
golden-set questions and reference answers, retention periods, sensitivity tiers and owners
come from people. When `next` says `needs_input`:

- Ask the owner named in `input_needed`, or help them measure (e.g. time 20 real questions,
  draft a labelling sheet with `discard calibrate`/`govern label export`, collect real questions
  from the inbox).
- Write down how it was measured (`--method "..."`). A measurement plan
  (`--not-yet-measured --plan-date`) is allowed for the inventory step only.
- You may draft structure (YAML skeletons, question wording) for a person to correct and approve.
  Never fill in a number, label or answer yourself and present it as measured.

## Data stays local

- All scans, PII detection, duplicate detection and the default judge run on this machine.
  Do not send file contents, records, chunks or questions to external services, other tools or
  web searches unless the charter (`compliance`) allows a processor for it.
- Credentials go in as references only (`--secret-ref env:NAME`). Never ask the user to paste a
  password into the chat; ask them to export it in their shell.
- Do not paste personal data into reasons, justifications or handoff notes. Name the item, not
  its content.

## Charter interview (step 1, the first time)

Start from the template and interview the user; one topic at a time:

```
konmari charter init --template minimal --lang de     # or --lang en
```

Ask for: the one use case and its purpose, what is out of scope, a misuse case; the people
(name, contact, unit, roles: `sponsor`, `use_case_owner`, `data_owner`, `data_steward`,
`data_custodian`, `compliance`, `agent_user`, `facilitator`); who is Accountable for each RACI
activity; the DPIA screening and AI Act classification **as decided by their compliance person**
(you may explain the options; you do not decide them); the sources and categories with one
approver each. Fill `charter.yaml`, run `konmari charter validate`, fix what it reports. Then
`metrics.yaml` and the golden set in the same way (`references/01-charter.md`).

## Step references

| step | reference |
|---|---|
| 1 charter, metrics, baselines | `references/01-charter.md` |
| 2 inventory and categories | `references/02-inventory.md` |
| 3 joy test (score, review) | `references/03-score.md` |
| 4 discard pass and retention | `references/04-discard.md` |
| 5 normalize, resolve, policy, conflicts | `references/05-standardize.md` |
| 6 chunk, enrich, glossary | `references/06-enrich.md` |
| 7 govern | `references/07-govern.md` |
| 8 data products and MCP server | `references/08-connect.md` |
| 9 evaluate and monitor | `references/09-evaluate.md` |
| connect an agent (Claude Code, eval.yaml) | `references/connect-agent.md` |

Full command list: `docs/cli.md` in the repository, or `konmari <group> --help`.

## Stop conditions

Stop and report to the user (what is done, what is open, who must act) when:

- `next` says `needs_approval` for someone other than the user, or `needs_input` that the user
  cannot supply now: write the handoff note(s).
- A command is refused for a reason you cannot fix without a decision (e.g. a source missing
  from the approved charter needs `charter amend` and a new approval).
- Anything would send data off this machine without a processor allowed by the charter.
- `next` says `done`: summarise with `konmari metrics report` and suggest scheduling
  `konmari monitor run` and the product refresh.

## Resume

Everything lives in the catalog and the pilot folder. To continue later, in a new conversation
or after someone else approved something: `cd` into the pilot folder and run `konmari next`.
`konmari charter status` shows the steering cadence; `konmari audit verify` checks the audit trail.
