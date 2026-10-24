## 1. Setup and catalog schema

- [x] 1.1 Create `konmari.charter` and `konmari.metrics` packages and register the `charter` and `metrics` CLI command groups; `konmari charter --help` and `konmari metrics --help` list all subcommands
- [x] 1.2 Add `jsonschema` and `PyYAML` to project dependencies with pinned versions
- [x] 1.3 Write SQL migrations for `charter_version`, `stakeholder`, `raci_assignment`, `metric_set_version`, `metric_definition`, `metric_measurement`, `approval`, `decision_log`, `audit_event` (append-only); migrations apply cleanly on Postgres 16 and DuckDB
- [x] 1.4 Implement canonical-content hashing (parsed YAML, sorted keys, JSON, SHA-256) with a unit test showing key order and whitespace do not change the hash
- [x] 1.5 Implement the append-only audit writer used by all commands; test that update and delete on `audit_event` are refused

## 2. Charter schema and validation

- [x] 2.1 Write `charter.schema.json`: single use case (purpose, deployment setting, in-scope, out-of-scope, misuse cases), stakeholder register with fixed role vocabulary, RACI over the seven standard activities, compliance fields, cadence
- [x] 2.2 Implement cross-field checks: exactly one Accountable and at least one Responsible per activity, compliance role Accountable for DPIA decision, facilitator-only person never Accountable, required roles present
- [x] 2.3 Implement `konmari charter init` with `--template full|minimal` producing an English and a German template that passes schema validation once placeholders are filled
- [x] 2.4 Implement `konmari charter validate` with field-level error messages and non-zero exit on failure
- [x] 2.5 Unit tests for every scenario in `specs/pilot-charter` "Single pilot use case per charter", "Stakeholder register with RACI" and "DSGVO and EU AI Act screening fields"
- [x] 2.6 Add the data-scope section to `charter.schema.json` (candidate sources, in-scope category IDs, per-category approver and reviewers) with a lookup API `konmari.charter.scope()` used by changes 02-09; tests for every scenario in "Pilot data scope and category approvers"

## 3. Approval, versioning and decision log

- [x] 3.1 Implement `konmari charter approve`: checks Accountable for charter approval, refuses `pending` DPIA/AI Act, refuses `prohibited`, requires extra compliance approval for `high`; stores identity, role, timestamp, version and hash
- [x] 3.2 Implement hash-mismatch detection in `validate` for approved charters edited on disk
- [x] 3.3 Implement `konmari charter amend --reason`, `show --version N` and `diff N M`; amendments create a `draft` version and keep earlier versions and approvals readable
- [x] 3.4 Implement `konmari charter log` and `show --log` for decision-log entries
- [x] 3.5 Implement `konmari charter status` with cadence-breach detection (steering interval + 7 days)
- [x] 3.6 Tests for every scenario in "Engagement cadence and decision log", "Human approval with recorded identity" and "Versioned, reversible amendments", including a revert-by-amend round trip

## 4. Success metrics

- [x] 4.1 Write `metrics.schema.json`: metric fields, five families, three gate tiers, direction, cadence, owner reference, golden-set reference
- [x] 4.2 Implement `konmari metrics validate`: complete definitions, owner in register, minimum family coverage, 100% human-approved-decisions governance metric
- [x] 4.3 Implement golden-set validation: at least 30 questions, at least 5 abstention questions, author roles, expected answer per question, file hash, personal-data rule tied to DPIA screening
- [x] 4.4 Implement `konmari metrics baseline`, `measure --kind gate|final --step <step>` storing value, date, method, author, charter version, metric-set version and golden-set hash
- [x] 4.5 Implement `konmari metrics approve` and `amend` with versioning and the `target_lowered_after_measurement` flag
- [x] 4.6 Implement `konmari metrics report --format md|json` with per-metric status and the pilot success verdict
- [x] 4.7 Tests for every scenario in `specs/success-metrics`

## 5. Readiness gate

- [x] 5.1 Implement `konmari charter gate --step <step>` and `konmari.charter.gate(step)`: approved latest version, matching hash, approved metric set, must-have baselines recorded; `not_yet_measured` with plan passes only for the `c02-category-inventory` inventory step
- [x] 5.2 Write every gate call to the audit trail with step, result and charter version
- [x] 5.3 Tests for both gate scenarios plus the planned-baseline case (inventory passes, `c04-discard-pass` fails)
- [x] 5.4 Document the gate contract (call signature, return value, exit codes) for changes 02–09

## 6. End-to-end check and docs

- [x] 6.1 Add an example engagement (fictional "Verein FAQ assistant") with charter, metrics and golden set that passes `validate`, `approve` and `gate` end to end in CI on DuckDB
- [x] 6.2 Add an integration test running the same flow on Postgres in CI
- [x] 6.3 Write a non-expert guide (EN and DE): how to pick the pilot use case, fill the RACI, do the DPIA screening with the DPO, measure baselines; vendor KPI ranges labelled as vendor claims with sources from research/c01-pilot-charter.md
- [x] 6.4 Run `openspec validate c01-pilot-charter --strict` and the full test suite; both pass
