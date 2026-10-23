## ADDED Requirements

### Requirement: Single pilot use case per charter
The system SHALL let a facilitator create a charter with `konmari charter init`, and each charter MUST describe exactly one pilot use case. The use case MUST contain: a name, a purpose statement, the intended deployment setting (agent users and whether the agent reads only or also writes), at least one in-scope task or question type, at least one explicit out-of-scope item, and at least one foreseeable misuse case. `konmari charter validate` SHALL reject a charter that lists more than one use case or misses any of these fields.

#### Scenario: Valid single use case
- **WHEN** a facilitator runs `konmari charter validate` on a charter with one use case and all required fields filled
- **THEN** the command exits with code 0 and reports the charter as valid

#### Scenario: Second use case rejected
- **WHEN** a charter file lists two use cases
- **THEN** `konmari charter validate` exits non-zero and the message states that a second use case needs its own charter

#### Scenario: Missing out-of-scope list
- **WHEN** the out-of-scope list is empty
- **THEN** validation fails and names the missing field

### Requirement: Stakeholder register with RACI
The charter MUST contain a stakeholder register in which every person has a name, contact, organization unit and one or more roles from the fixed vocabulary: `sponsor`, `use_case_owner`, `data_owner`, `data_steward`, `data_custodian`, `compliance`, `agent_user`, `facilitator`. The charter MUST contain a RACI matrix over the standard activities (charter approval, metric approval, source inclusion, retention/discard approval, source-of-truth conflict resolution, DPIA decision, go-live approval). Each activity MUST have exactly one Accountable and at least one Responsible. The Accountable for the DPIA decision MUST hold the `compliance` role. A person holding only the `facilitator` role MUST NOT be Accountable for any activity.

#### Scenario: Two Accountables rejected
- **WHEN** the activity "retention/discard approval" has two people marked Accountable
- **THEN** validation fails and names the activity and both people

#### Scenario: Facilitator as Accountable rejected
- **WHEN** the only Accountable for "charter approval" holds only the `facilitator` role
- **THEN** validation fails stating that the organization's own staff must be Accountable

#### Scenario: Required roles present
- **WHEN** the register has no person with role `data_owner`, `compliance` or `agent_user`
- **THEN** validation fails and lists each missing role

### Requirement: DSGVO and EU AI Act screening fields
The charter MUST record: the purpose of processing; the personal-data categories expected in scope (or an explicit `none`); whether special categories under GDPR Art. 9 are expected; a DPIA screening result (`required`, `not_required` or `pending`) with a written reason and the compliance stakeholder who made it; the EU AI Act risk classification (`minimal`, `limited`, `high`, `prohibited` or `pending`) with a reason; and the date of an AI-literacy briefing for the agent users or a planned date. A charter MUST NOT be approved while the DPIA screening or the AI Act classification is `pending`. A charter with AI Act classification `prohibited` MUST NOT be approved at all.

#### Scenario: Pending DPIA blocks approval
- **WHEN** a sponsor runs `konmari charter approve` while the DPIA screening is `pending`
- **THEN** the approval is refused and the message names the DPIA screening as the blocker

#### Scenario: High-risk classification needs explicit compliance sign-off
- **WHEN** the AI Act classification is `high`
- **THEN** approval requires an additional approval record from the `compliance` stakeholder that references the classification, and is refused without it

#### Scenario: Personal data declared
- **WHEN** the personal-data categories field is missing
- **THEN** validation fails; an explicit `none` passes

### Requirement: Engagement cadence and decision log
The charter MUST define an engagement cadence with at least: a kickoff date, a recurring working session (interval in days), a recurring steering meeting (interval in days, with sponsor and compliance as attendees), a phase-gate review after each KonMari step, a pilot close date, and a post-pilot review interval owned by an organization stakeholder. The system SHALL provide `konmari charter log` to append dated decision-log entries (date, attendees, decisions, owner of each action) to the catalog. `konmari charter status` SHALL report a cadence breach when the last steering entry is older than the steering interval plus 7 days.

#### Scenario: Missing post-pilot review rejected
- **WHEN** the cadence has no post-pilot review interval
- **THEN** validation fails stating that the engagement must not be a one-off

#### Scenario: Overdue steering meeting reported
- **WHEN** the steering interval is 14 days and the last steering log entry is 22 days old
- **THEN** `konmari charter status` reports a cadence breach naming the sponsor

#### Scenario: Decision logged
- **WHEN** a facilitator runs `konmari charter log` with attendees and decisions
- **THEN** a decision-log row with timestamp, author, charter version and content is stored and shown by `konmari charter show --log`

### Requirement: Human approval with recorded identity
A charter SHALL become effective only through `konmari charter approve`, run by or on behalf of the stakeholder who is Accountable for charter approval. Each approval MUST record the approver's identity, role, timestamp, charter version number and the SHA-256 hash of the canonical charter content. The system MUST NOT auto-approve a charter.

#### Scenario: Approval recorded
- **WHEN** the Accountable sponsor approves charter version 1
- **THEN** an approval record with identity, role, timestamp, version 1 and the content hash is written to the catalog and the charter status becomes `approved`

#### Scenario: Wrong approver refused
- **WHEN** a person who is not Accountable for charter approval runs `konmari charter approve`
- **THEN** the approval is refused and no status change is stored

#### Scenario: Tampering detected
- **WHEN** the approved `charter.yaml` is edited on disk without `konmari charter amend`
- **THEN** `konmari charter validate` reports that the content hash no longer matches the approved version

### Requirement: Versioned, reversible amendments
Changes after approval SHALL be made through `konmari charter amend`, which MUST create a new charter version with a required reason, set the status to `draft` until re-approved, and keep all earlier versions and their approvals readable. The system SHALL support `konmari charter show --version N` and `konmari charter diff N M`. Reverting SHALL be done by amending to the content of an earlier version, never by deleting versions.

#### Scenario: Amendment creates a new version
- **WHEN** an approved version 1 is amended with reason "add second data owner"
- **THEN** version 2 exists in status `draft`, version 1 stays readable with its approval, and an audit entry records author, reason and timestamp

#### Scenario: Diff between versions
- **WHEN** a user runs `konmari charter diff 1 2`
- **THEN** the output lists every changed field with old and new value

### Requirement: Readiness gate for downstream steps
The system SHALL provide `konmari charter gate` (and an equivalent Python function) that later pipeline steps MUST call before reading any source data. The gate SHALL pass only when the latest charter version is `approved`, its hash matches, and the success-metrics gate condition is met. When it fails, the gate MUST exit non-zero and list every unmet condition. Every gate call MUST be written to the audit trail with the calling step, result and charter version.

#### Scenario: Inventory blocked by draft charter
- **WHEN** a downstream step (for example the source inventory from `c02-category-inventory`) calls the gate while the charter is in `draft`
- **THEN** the gate fails, the step does not connect to any source, and the audit trail records the refused call

#### Scenario: Gate passes
- **WHEN** the charter is approved and all must-have metrics have baselines
- **THEN** the gate exits with code 0 and returns the charter ID and version for the caller to attach to its own records

### Requirement: Pilot data scope and category approvers
The charter MUST declare the pilot's data scope: a list of candidate sources (connector type `sharepoint`, `sql`, `fileshare` or `csv-import`, plus a locator) and the list of in-scope categories, using the category IDs of `category-map` (change 02; defaults `kb-and-sops`, `customer-records`, `policies-and-contracts`, `financial-ops-metrics`, `support-history`). For every in-scope category the charter MUST name one Accountable approver holding the `data_owner` role and at least one reviewer holding `data_owner`, `data_steward` or `agent_user`. Later steps MUST take their allowed sources, in-scope categories, verdict reviewers, discard approvers and field owners from this section and nowhere else. Adding a source or category MUST go through `konmari charter amend`.

#### Scenario: Category without approver rejected
- **WHEN** the charter lists `customer-records` as in scope but names no `data_owner` approver for it
- **THEN** `konmari charter validate` fails and names the category

#### Scenario: Unknown category ID rejected
- **WHEN** the charter lists the category `payroll` and no such category exists in the default set or in a pilot extension declared in the charter
- **THEN** validation fails and lists the valid category IDs

#### Scenario: Downstream reads approvers from the charter
- **WHEN** `c04-discard-pass` asks who may approve a proposal for an item in `policies-and-contracts`
- **THEN** the answer is the approver named for that category in the latest approved charter version
