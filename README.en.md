<p align="center"><img src="docs/assets/banner-en.png" alt="On the left a heap of tilted files such as angebot_final_v3(2).docx and kunden_ALT.xlsx; on the right the same data standing upright in a box, each labelled with category, owner and date." width="100%"></p>

<p align="center">
  <a href="https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten/actions/workflows/ci.yml/badge.svg"></a>
  <a href="LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/License-Apache--2.0-1a7a83"></a>
  <img alt="Python 3.12" src="https://img.shields.io/badge/Python-3.12-1a7a83">
  <img alt="MCP" src="https://img.shields.io/badge/Agents-MCP-1a7a83">
  <img alt="GDPR-aware" src="https://img.shields.io/badge/GDPR-aware-1a7a83">
</p>

# MarieKondoFürAgenten

**Your agent is only as good as the filing cabinet it reads from.**

[Deutsch](README.md) · English

Point an AI agent at your SharePoint, your CRM and your file shares and it will find
`angebot_final_v3(2).docx`, three price lists from three different years and a customer table
nobody has touched since 2021. Then it answers confidently and wrongly. That is rarely the
model's fault. It is the data.

The **KonMari-Kit** tidies exactly that, using Marie Kondo's method translated for data: decide
what you are tidying for, discard first, sort by category, give every item a place and an owner,
and only then connect it. What you end up with is an MCP server that shows your agents only
current, approved, well-described data, and that is allowed to say "I don't know" instead of
guessing.

A project of [KI Klub Werder e. V.](https://github.com/KI-Klub-Werder-e-V), Apache-2.0.

## Does it spark joy for your agent?

Every file, table and source goes through the same test: six questions, each scored 0 to 3,
with evidence.

| Question | What the kit checks |
|---|---|
| Authoritative and current? | last change against the agreed update frequency, superseded versions |
| Does someone own it? | a named owner who maintains it |
| Is the meaning clear? | columns and terms are in the glossary, not just `kd_st_2` |
| Can an agent retrieve the right piece? | well-cut passages, clean table schemas |
| Are conflicts resolved? | a rule for which source wins |
| Are permissions explicit? | the source's permissions travel with the data into the index |

The result is a proposal: **keep**, **fix** or **discard**. A human always confirms it, and
every decision can be reversed.

## Before and after

| | Before | With the KonMari-Kit |
|---|---|---|
| Format | whole documents, raw tables | chunked passages, versioned data products |
| Metadata | file name | source, date, type, owner, freshness, definitions |
| Access | whatever the service account sees | read scopes per agent, source permissions preserved |
| Consistency | duplicates and three versions of the truth | deduplicated, one leading source per field |
| Freshness | "did anyone ever update this?" | scheduled refresh with change tracking |
| Discoverability | buried in silos | described in a catalog and callable via MCP |

## How the tidying works

Nine steps, always for **one** use case, say an FAQ assistant for a club office. Each step
checks that the previous one is properly done, so the kit won't let you enrich what you
haven't discarded yet.

```
 1  charter       What are we tidying for? Use case, roles, data protection, success metrics
 2  inventory     What lives where? Sources across systems, sorted by category, not by location
 3  score         Does it spark joy? Six questions, proposal keep / fix / discard
 4  discard       Thank it and let it go: duplicates, old versions, off-topic items to the archive
 5  normalize     One truth: normalize dates, IDs and names, decide the leading source
 6  chunk/enrich  Fold it: cut passages, attach metadata, maintain the glossary
 7  govern        Secure it: permissions, PII masking, tamper-evident audit trail
 8  product/serve Connect it: data products and a read-only MCP server
 9  eval/monitor  Check it: real questions, citations, abstention, continuous freshness monitoring
```

Categories are tackled in order of risk: the public FAQ first, contracts later, anything with
special-category personal data (GDPR Art. 9) last.

## Quick start

You need Python 3.12 and [uv](https://docs.astral.sh/uv/). The example is a fictional sports
club with an FAQ assistant for its office.

```bash
git clone https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten
cd MarieKondoFuerAgenten && uv sync
cd examples/verein-faq-assistant

uv run konmari charter validate          # check the charter: roles, data protection, data scope
uv run konmari charter approve --as anna # the accountable person signs off
uv run konmari metrics validate          # check success metrics and the question set
uv run konmari metrics approve --as ben
uv run konmari charter gate --step inventory
```

The last command fails, on purpose:

```
unmet: metrics: must_have metric 'answer_accuracy' has no baseline or measurement plan
```

No baseline, no tidying, because otherwise nobody can tell afterwards whether it helped. The
walkthrough continues in the [guide](docs/guide.en.md). Every command at a glance:
[`docs/cli.de.md`](docs/cli.de.md) (German).

## What's inside

- **One catalog for every decision.** DuckDB locally, Postgres 16 in production, same migrations.
  Every decision records person, role, time and reason.
- **Humans decide, the kit proposes.** Discarding, masking, erasing and publishing need sign-off
  from the role named in the charter.
- **Reversible and logged.** Archive instead of trash, restore by command, a hash-chained audit
  trail (`konmari audit verify`).
- **Duplicates at three levels:** identical files, near-identical text (MinHash) and
  semantically identical text (embeddings, optional).
- **Data protection built in.** PII detected locally with Presidio and spaCy, retention and
  deletion rules per category, keyed pseudonymisation without a reverse lookup table, a record
  of processing activities as export.
- **Open standards, not home-grown formats:** DCAT for the inventory, OpenLineage for lineage,
  ODPS and ODCS for data products, SKOS for the glossary, MCP for agents.
- **Self-hosted, no mandatory cloud.** Embedding and judge models run locally by default; a hosted
  API is a config switch.

## Who is it for?

- clubs, associations, small and mid-sized companies and public bodies putting a first agent on
  their own data
- volunteers, IT leads and consultants guiding such a project
- anyone who needs to know, and prove, which data their agent can see

It runs on the command line. Approvals need no programming skills, but someone has to run the
commands.

## Honest status

The project is young (version 0.1, October 2026). It was built spec-first with
[OpenSpec](https://github.com/Fission-AI/OpenSpec): nine changes, each with requirements,
scenarios and research notes with sources in [`research/`](research/).

**Built and tested**

- All nine steps are implemented, from charter to monitoring: 22 command groups, 686 automated
  tests (DuckDB and Postgres 16), every spec scenario with its own test.
- After each step was built, an independent verification pass read it against the spec and
  found and fixed 8 real bugs, including one where erased data would have reappeared on the
  next scan.
- Three tasks are deliberately open: an optional Debezium adapter for real-time changes, a
  sign-off on one interface between steps, and a review of the guide by a non-developer.

**Not yet proven**

- No run against real systems. SharePoint, CRM, file shares and models are tested against fakes.
  Start with a copy and one small use case.
- Thresholds (such as when two texts count as duplicates) are reasoned assumptions from the
  research, not measurements. Calibrate them after the first pilot.
- The German spaCy model `de_core_news_lg` is not loaded in tests.

**Data protection**

The kit helps you document compliance; it does not replace a legal review. The charter asks for
the DPIA and EU AI Act classification and blocks approval while they are open.

## Contributing

Pilot stories are what we need most. If you try the kit on a real use case, tell us where it
got stuck. Also welcome: more connectors (Nextcloud, Confluence, Google Drive, more CRMs),
better examples and translations. Bugs and ideas go to
[issues](https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten/issues).

```bash
uv sync && uv run pytest -q              # tests; Postgres tests use KONMARI_TEST_PG_URL or a local Postgres 16
openspec validate --all --strict         # check the specs
```

New features start as an OpenSpec change; see [`CONTRIBUTING.md`](CONTRIBUTING.md) and
[`AGENTS.md`](AGENTS.md). Real personal data never goes into the repository.

## License

Apache-2.0, see [`LICENSE`](LICENSE).
