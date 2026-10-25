## ADDED Requirements

### Requirement: Read-only next-step report
`konmari next [--json]`, run in the pilot folder (`charter.yaml`, `metrics.yaml`), SHALL report where the pilot stands and what to do next without changing any state: it MUST NOT write audit events, versions, approvals, measurements, category rows or step states, and it MUST NOT create a DuckDB catalog file that does not exist yet. It SHALL exit 0 whatever the pilot state is, and 2 on a usage error.

#### Scenario: No audit events or state written
- **WHEN** `konmari next` runs against a pilot catalog in any state
- **THEN** the number of rows in every catalog table is the same before and after the run

#### Scenario: Exit code zero while blocked
- **WHEN** the pilot is waiting for an approval or for missing input
- **THEN** `konmari next` exits 0, and an unknown option exits 2

### Requirement: Charter and metric-set state first
The report SHALL start with the state of the charter and of the metric set and SHALL make the first unmet one the next item: no charter file (`needs_input`, suggest `konmari charter init --template minimal --lang <de|en>`), validation errors (`needs_input` with every error), a charter or metric set that is not approved or whose file no longer matches the approved version (`needs_approval`), and must-have metrics without a baseline (`needs_input`).

#### Scenario: No charter yet
- **WHEN** the working folder has no charter file and no catalog
- **THEN** the next item is `charter` with status `needs_input`, the command `konmari charter init --template minimal --lang <de|en>`, and no catalog file is created

#### Scenario: Invalid charter lists every error
- **WHEN** the charter file still has template placeholders or fails validation
- **THEN** the next item has status `needs_input` and its reasons contain every validation error

#### Scenario: Charter waiting for approval
- **WHEN** the charter is valid but no version is approved
- **THEN** the next item has status `needs_approval`, the command `konmari charter approve --as anna`, and `approval_needed` names `anna` with the role `Accountable for charter_approval`

#### Scenario: Metric set waiting for approval
- **WHEN** the charter is approved and the metric set is not
- **THEN** the next command is `konmari metrics approve --as ben` with `approval_needed` naming the Accountable for `metric_approval`

### Requirement: Human-only values are never invented
Values only a human can supply (baselines, measurements, labels, reference answers) SHALL be reported as `needs_input` with a placeholder such as `<value>` in the command, together with the person who owns the value. The report MUST NOT contain a number it did not read from the catalog.

#### Scenario: Missing baselines flagged as input
- **WHEN** charter and metric set are approved and must-have metrics have no baseline
- **THEN** the next item has status `needs_input`, lists each such metric with its owner, and every command uses `--value <value>` instead of a number

### Requirement: Approver taken from the charter
Every command in the report that records an approval or decision SHALL carry `--as` filled with the stakeholder who holds the required role in the charter, and the item SHALL carry `approval_needed` with `who`, `role` and `what`. Commands that only do legwork SHALL carry `--as <you>`, the stakeholder ID of the person operating the kit.

#### Scenario: Category order approval names the category approver
- **WHEN** a category order has been proposed but not approved
- **THEN** the next command is `konmari categories approve --as <approver>` with the approver named in the charter's data scope

### Requirement: Per-category step report
From the inventory step on, the report SHALL list each in-scope category in the approved category order (the charter order until one is approved) with its number of accepted assets and its state for each KonMari step, using the c02 step order. The first unfinished item is the earliest step that is not complete for a category with accepted assets, and within that step the first such category in the order; its reasons include the unmet conditions of `check_step` and of the readiness gate for that step.

#### Scenario: Unregistered charter sources come first
- **WHEN** baselines are recorded and no charter source is registered
- **THEN** the next item is `inventory` with status `ready` and one `konmari inventory source add` command per charter source

#### Scenario: Categories in the approved order
- **WHEN** the category order is approved and the handoff has passed
- **THEN** the categories are listed in the approved order with `inventory` complete, and the next item is the `score` step of the first category with the command `konmari score --as <you>`

#### Scenario: Step completion names the category approver
- **WHEN** the `score` step is complete for every category and `discard` has not started
- **THEN** the next item is `discard` for the first category in the order, and its `approval_needed` names that category's approver for `konmari categories step <category> discard --status complete`

### Requirement: Machine-readable output
With `--json` the command SHALL print one JSON object with the keys `charter`, `metrics`, `categories` and `next`, where `next` has `step`, `category`, `status`, `reasons`, `commands`, `approval_needed` and `input_needed`.

#### Scenario: JSON shape
- **WHEN** `konmari next --json` runs at any state
- **THEN** the output parses as JSON with those keys
