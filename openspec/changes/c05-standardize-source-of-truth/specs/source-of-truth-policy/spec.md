## ADDED Requirements

### Requirement: Per-field precedence policy
The system SHALL maintain a source-of-truth policy that defines, for each entity and each field exposed
to agents, exactly one survivorship strategy from: `source_priority` (ordered list of sources),
`most_recent` (by a declared source timestamp field), `most_complete`, or `owner_decision` (always
routed to the conflict register). Each rule SHALL name a field owner, a tie-breaker, and the rationale.
Policies SHALL be stored as versioned YAML and loaded into the readiness catalog.

#### Scenario: CRM wins contact preferences, ERP wins billing terms
- **WHEN** the policy declares `customer.contact_preference: source_priority [crm, erp]` and `customer.payment_terms: source_priority [erp, crm]`
- **THEN** golden records take contact preference from the CRM and payment terms from the ERP whenever both have a value

#### Scenario: Most recent requires a timestamp field
- **WHEN** a `most_recent` rule is declared without a source timestamp field
- **THEN** loading the policy fails with a validation error naming the rule

### Requirement: Policy coverage for agent-exposed fields
The system SHALL report every field that is exposed to agents through `data-products` or
`mcp-exposure` (change 08) but has no precedence rule, and SHALL block publication of golden records
for that entity until coverage is complete. Implicit defaults (e.g. "last write wins") SHALL NOT be
applied.

#### Scenario: Uncovered field blocks publication
- **WHEN** `customer.email` is exposed to agents and the policy has no rule for it
- **THEN** `konmari policy check` exits non-zero listing `customer.email`, and golden records for `customer` are not published

### Requirement: Approved and versioned policy changes
The system SHALL require approval from the field owner named in the `pilot-charter` (change 01) before
a new or changed rule becomes active. Every policy version SHALL record author, approver, timestamp,
diff, and the number of golden-record values the change would alter (dry-run impact). The system SHALL
allow rollback to any previous approved version.

#### Scenario: Unapproved change stays inactive
- **WHEN** an operator edits a rule and loads the policy without owner approval
- **THEN** the new version is stored as `pending`, golden records keep using the last approved version, and a dry-run impact report lists the values that would change

#### Scenario: Rollback restores previous winners
- **WHEN** the owner rolls back to the previous approved policy version
- **THEN** golden records are recomputed with that version and the rollback is audited

### Requirement: Golden records with attribute lineage
The system SHALL build one golden record per entity cluster by applying the active policy to the
canonical (normalized) values of the cluster members. Each golden-record attribute SHALL carry the
winning source, source record ID, the rule and policy version that selected it, the source timestamp,
and the selection time. The system SHALL emit an OpenLineage run event with a `columnLineage` facet
mapping each golden-record field to its input fields.

#### Scenario: Provenance on every attribute
- **WHEN** a golden record for customer cluster `C-17` is built
- **THEN** each attribute lists its winning source, source record ID, rule, policy version and timestamps

#### Scenario: Lineage event emitted
- **WHEN** a golden-record build run completes
- **THEN** an OpenLineage COMPLETE event is emitted whose `columnLineage` facet maps `customer.payment_terms` to `erp.debitor.zahlungsbedingung`

### Requirement: Conflict detection
The system SHALL detect a conflict when cluster members hold different canonical values for the same
field and the active rule cannot decide (rule is `owner_decision`, tie-breaker exhausted, or all
candidate sources are equally ranked), and SHALL also record as `overridden` every disagreement that a
rule decided, so owners can review rule outcomes. Values that are equal after normalization SHALL NOT
count as conflicts.

#### Scenario: Normalization-only difference is no conflict
- **WHEN** the CRM holds `+49 30 1234567` and the ERP holds `030/1234567` for the same customer
- **THEN** both normalize to `+49301234567` and no conflict is recorded

#### Scenario: Undecidable disagreement creates conflict
- **WHEN** the rule for `customer.legal_name` is `owner_decision` and two sources disagree
- **THEN** a conflict entry is created and the golden-record attribute is marked `unresolved`

### Requirement: Conflict register with owner resolution
The system SHALL keep a conflict register in which each entry holds entity, cluster, field, candidate
values with their sources, detection time, assigned field owner, due date, status (`open`,
`resolved`, `accepted_deviation`, `reopened`) and a personal-data flag taken from the
`source-inventory` sensitivity classification. The owner SHALL resolve an entry by choosing a value or
entering a corrected value, and SHALL state whether the source system is to be corrected (write-back
ticket reference) or the deviation is accepted with a reason. Resolutions SHALL record approver and
timestamp and SHALL be reversible.

#### Scenario: Owner resolves with write-back
- **WHEN** the owner resolves a `customer.legal_name` conflict choosing the ERP value and supplies ticket `IT-4711` to correct the CRM
- **THEN** the golden record uses the ERP value with origin `owner_decision`, the entry is `resolved`, and ticket `IT-4711` is stored

#### Scenario: Recurring conflict reopens
- **WHEN** a later run detects the same disagreement on a resolved entry because the source was not corrected
- **THEN** the entry is set to `reopened` and the owner is notified, unless it was `accepted_deviation`

### Requirement: Personal-data conflicts tracked for accuracy duty
The system SHALL flag conflicts on fields classified as personal data, SHALL default their due date
to 30 days from detection (configurable per pilot), and SHALL list overdue personal-data conflicts in a
report for the controller's DSGVO accuracy (Art. 5(1)(d)) and rectification (Art. 16) process.
Conflict entries SHALL NOT duplicate raw personal data beyond the candidate values needed for the
decision, and SHALL be subject to `access-control` and `sensitive-data-masking` (change 07).

#### Scenario: Overdue personal-data conflict reported
- **WHEN** a personal-data conflict remains `open` after its due date
- **THEN** `konmari conflicts report --overdue` lists it with owner and age

#### Scenario: Masked for unauthorized viewers
- **WHEN** a user without access to the personal-data field lists the conflict register
- **THEN** the candidate values are masked according to `sensitive-data-masking`

### Requirement: Agent-visible provenance and conflict status
The system SHALL make the winning source, rule and conflict status of each golden-record attribute
available to `data-products` and `mcp-exposure` (change 08), so that an agent can cite the source and
answer "unknown / unresolved" instead of choosing a value for an attribute marked `unresolved`.

#### Scenario: Agent sees unresolved status
- **WHEN** an agent requests `customer.legal_name` for a cluster whose conflict is open
- **THEN** the interface returns status `unresolved` with the candidate sources and no single asserted value

### Requirement: Audited decisions
The system SHALL write every policy approval, rollback, conflict resolution, reopening, clerical review
decision and quarantine resolution as an event to `audit-logging` (change 07), including actor,
timestamp, before/after state and reason.

#### Scenario: Resolution appears in audit log
- **WHEN** an owner resolves a conflict
- **THEN** an audit event with actor, timestamp, previous and new golden-record value, and reason is recorded
