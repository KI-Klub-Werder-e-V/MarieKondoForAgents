# Command reference

English · [Deutsch](cli.de.md)

The `konmari` commands by step. Each step links to the guide that explains them in detail.

Installation (Python 3.12, [uv](https://docs.astral.sh/uv/)):

```
uv sync
uv run konmari --help
```

Where we are and what comes next (read-only; the basis of the Claude Code skill, see [docs/agents.md](agents.md)):

```
konmari next                                        # state of charter, metrics, categories and the next command
konmari next --json                                 # the same, machine-readable
```

Step 1, pilot charter (guide: [docs/guide.en.md](guide.en.md), example: `examples/verein-faq-assistant/`):

```
konmari charter init --template minimal --lang de   # create a charter.yaml template
konmari charter validate                            # check (schema, RACI, data protection, data scope)
konmari charter approve --as <id>                   # approve (Accountable for charter_approval)
konmari charter amend --reason "..." --as <id>      # new version after approval
konmari charter diff 1 2                            # compare versions (also: show, log, status)
konmari metrics validate                            # check metrics and golden set
konmari metrics approve --as <id>                   # approve metrics
konmari metrics baseline <metric> --value 0.4 --method "..." --as <id>
konmari metrics measure <metric> --kind gate --step discard --value 0.7 --method "..." --as <id>
konmari metrics report --format json              # state per metric and pilot result (or md)
konmari charter gate --step inventory               # approval check for later steps (exit 0 = ok)
```

Step 2, inventory and categories (guide: [docs/inventory-guide.en.md](inventory-guide.en.md)):

```
konmari inventory source add --connector sql --locator <url> --secret-ref env:PW --as <id>  # only sources from the charter
konmari inventory scan                              # read-only; personal-data hints run locally (Presidio)
konmari inventory import <file.csv> --as <id>      # systems without a connector
konmari inventory set <asset> --owner <id> --update-frequency P1M --as <id>
konmari inventory tier <asset> --confirm --as <id>  # confirm protection tier (or --set ... --reason ...)
konmari inventory report --gate                     # completeness (also: --csv, waive, export as DCAT)
konmari categories suggest --as <id>                # proposals, then accept/assign/reject/revert
konmari categories order --propose --as <id>        # order by risk, then categories approve
konmari categories handoff                          # handoff to step 3 (exit 0 = ok)
```

Step 3, joy test (guide: [docs/scorecard-guide.en.md](scorecard-guide.en.md)):

```
konmari score evidence <asset> --kind acl_snapshot --data '{"principals": [...]}' --ref <ref> --as <id>  # evidence (also: score ingest --soda/--odcs)
konmari score --as <id>                             # six criteria per asset, proposal keep/fix/discard
konmari review list --status proposed               # then review accept|override|reverse <asset> --as <id>
konmari score fix-done <asset> <criterion> --as <id>  # rescore (also: score --changed, score alert)
konmari scorecard report --format json              # assets and category maturity (md, csv, json)
```

Step 4, discard (guide: [docs/discard-guide.en.md](discard-guide.en.md)):

```
konmari discard detect --items items.jsonl --as <id>   # duplicates, versions, out of pilot scope, sensitive content
konmari discard calibrate --detector minhash --sample 60 --as <id>   # then --labels pairs.csv --threshold 0.8
konmari discard propose --as <id>                   # proposals; change nothing
konmari discard approve <proposal> --as <id>       # or reject, bulk-approve, cancel
konmari discard execute --as <id>                   # archive, exclude, send to masking, delete
konmari discard restore <item> --as <id>         # also: history, log verify, check-propagation, report
konmari retention rule add ... --as <id>            # retention periods per category (compliance), then rule approve
konmari retention check --as <id>                   # expired periods, including in the archive (also: retention hold)
```

Step 5, standardize and source of truth (guide: [docs/standardize-guide.en.md](standardize-guide.en.md)):

```
konmari normalize --records records.jsonl --specs normalize/ --as <id>   # raw value stays, canonical value is added
konmari normalize quarantine list --as <id>         # then quarantine resolve|revert <id> --value ... --as <id>
konmari resolve run --entity customer --config resolve.yaml --as <id>    # duplicates across systems (Splink)
konmari resolve review export --entity customer -o pairs.csv           # then review import pairs.csv --as <id>
konmari resolve publish --entity customer --known known.csv --as <id>  # only if precision/recall are above the floor
konmari policy load --file policy/customer.yaml --as <id>              # new version "pending" with impact
konmari policy approve customer --version 1 --as <id>                  # field owner approves (also: rollback)
konmari policy check --exposure exposure.yaml       # every field visible to agents needs a rule
konmari policy build customer                       # golden records with provenance (also: policy golden <entity> <cluster>)
konmari conflicts list --as <id>                    # then conflicts resolve <id> --source erp --ticket IT-4711 --as <id>
konmari conflicts report --overdue                  # overdue conflicts (also: accept, revert, export-tickets)
```

Step 6, structure and enrich (guide: [docs/enrich-guide.en.md](enrich-guide.en.md)):

```
konmari chunk run --dry-run --as <id>               # chunks and report, writes nothing (then without --dry-run)
konmari chunk profile set <category> --max-tokens 600 --reason "..." --as <id>   # also: profile show, rollback <run>
konmari enrich schema load schema.yaml --as <id>    # agent-visible tables with column descriptions
konmari enrich run --as <id>                        # metadata envelope, completeness, ACL tags, lineage
konmari enrich override <item> --field owner --value <id> --reason "..." --as <id>   # also: lineage, rollback
konmari glossary stewards --steward <id> --as <id>  # then glossary add|submit|approve|harvest|revert|deprecate
konmari glossary export --format skos -o glossar.ttl   # approved terms only (also: --format json, report)
```

Step 7, secure (guide: [docs/govern-guide.en.md](govern-guide.en.md)):

```
konmari govern policy seed --as <id>                # source policy per category, then policy edit|approve|revert|show
konmari govern agent <agent> --scope read:<category> --as <id>   # agent with least privilege (also: govern grant)
konmari govern acl-sync --as <id>                   # permissions from the sources; revocation applies everywhere
konmari govern scan --as <id>                       # find personal data (then review list|confirm|reject|revert)
konmari govern label export --category <category> --as <id>   # label a sample, then label import <file> --as <id>
konmari govern mask --as <id>                       # masked agent index (also: govern reidentify, compliance only)
konmari govern check --as <id>                      # leak test and approval per source (also: leak-test, status, ropa)
konmari audit verify                                # one audit log for all steps (also: export, purge)
```

Step 8, connect (guide: [docs/connect-guide.en.md](connect-guide.en.md)):

```
konmari product init <id> --category <category> --owner <id> --use-case "<use-case>"   # create ODPS + ODCS
konmari product lint products/<id> --as <id>        # schemas, required fields, prerequisites; creates the draft
konmari product refresh <id> --as <id>              # load changed rows, test, only then apply (also: all)
konmari product test <id>                           # quality rules (also: diff products/<id> for the semver check)
konmari product approve <id> --version 1.0.0 --as <owner>    # then product publish <id> --version 1.0.0 --as <owner>
konmari product publish <id> --rollback --reason "..." --as <owner>   # also: deprecate, list --json, schedule
konmari serve --check-pins                          # check new tools, then serve --approve-tool <name> --as <id>
konmari serve --stdio                               # MCP server for a local agent (or --http with OAuth)
```

Step 9, evaluate and monitor (guide: [docs/evaluate-guide.en.md](evaluate-guide.en.md), CI example: `examples/ci/konmari-eval.yml`):

```
konmari eval items import fragen.yaml --as <id>     # golden-set questions (questions from logs are masked), then items approve <id> --as <id>
konmari eval set activate --as <id>                 # >= 50 questions, >= 20 % unanswerable, <= 50 % synthetic
konmari eval run --as <id>                          # through the agent's MCP interface (eval.yaml), also: report
konmari eval gate                                   # thresholds and baseline (exit 1 = blocked)
konmari eval baseline approve <run> --as <id>      # or baseline revert; also: calibrate, next-category, items retention
konmari monitor run --as <id>                       # freshness, volume, metadata, duplicates, schema (also --dry-run, schedule)
konmari monitor incident acknowledge <id> --reason "..." --as <id>   # or resolve, accept-risk; also: incidents, threshold, drift, import
```

For the personal-data hints, download the language models once: `uv run python -m spacy download de_core_news_lg` (step 7 also needs `en_core_web_lg`). SMB shares: `uv sync --extra smb`. Ragas as the evaluation model: `uv sync --extra ragas`. Semantic duplicates: `uv sync --extra semantic`, S3 archive: `uv sync --extra s3`, Docling parser: `uv sync --extra docling`.

The catalog lives in `.konmari/catalog.duckdb` by default. For Postgres 16: `uv sync --extra postgres` and `KONMARI_CATALOG=postgresql://...` or `--catalog`. Interfaces for later changes: [docs/gate-contract.md](gate-contract.md), [docs/enrich-contract.md](enrich-contract.md), [docs/governance-contract.md](governance-contract.md), [docs/connect-contract.md](connect-contract.md). Tests: `uv run pytest -q`.
