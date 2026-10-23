## ADDED Requirements

### Requirement: Monitored asset registry
The system SHALL monitor every source and data product in the pilot scope. Each monitored asset MUST have an accountable owner and a declared update interval taken from the source inventory; an asset without owner or interval MUST be reported as `unmonitorable` instead of being silently skipped.

#### Scenario: Asset without update interval
- **WHEN** a pilot source has no declared update interval in the source inventory
- **THEN** the monitor run reports it as `unmonitorable` with reason `missing_update_interval` and notifies the `use_case_owner`

### Requirement: Freshness checks with warn and error tiers
`konmari monitor run` SHALL compute each asset's age as check time minus the newest change timestamp of the asset (record timestamp, document modified date or pipeline load time). By default an asset SHALL be `warn` when its age exceeds 1× its declared update interval and `error` when it exceeds 2×. Per-asset thresholds MAY be overridden only by the asset owner, and overrides MUST be audited.

#### Scenario: Stale source becomes error
- **WHEN** a source with a declared update interval of 7 days was last changed 15 days ago
- **THEN** its freshness status is `error` with age 15 days and threshold 14 days

#### Scenario: Owner override audited
- **WHEN** an owner sets the error threshold of their source to 30 days
- **THEN** subsequent checks use 30 days and the audit log records owner, old and new threshold

### Requirement: Quality checks
For each monitored asset, the system SHALL run the quality checks declared for it (at least: row or document count change against the previous run, share of records or chunks missing mandatory metadata, duplicate share, and schema change). Each check result MUST store observed value, expected range and status.

#### Scenario: Volume drop detected
- **WHEN** a document library's count drops from 1,000 to 400 between two runs and the declared tolerance is 20 %
- **THEN** the volume check is `error` with observed value 400 and expected range 800–1,200

#### Scenario: Missing metadata share
- **WHEN** 15 % of chunks of a data product lack an owner or a valid-from date and the tolerance is 5 %
- **THEN** the metadata completeness check is `error` and lists sample chunk IDs

### Requirement: Drift checks
The system SHALL compare, per pilot, the distribution of incoming agent questions and of retrieved content against a reference window and SHALL report drift when the configured drift test exceeds its threshold. A drift report MUST name the reference window, current window, method and score.

#### Scenario: Question drift detected
- **WHEN** questions in the current week concern a product line absent from the reference window and the drift score exceeds the threshold
- **THEN** a drift result with status `warn` is stored and the `use_case_owner` is asked to review adding golden items for the new topic

### Requirement: Lineage-compatible check results
Every check result SHALL be persisted in the catalog and emitted as an OpenLineage-compatible event using the `dataQualityAssertions` dataset facet (assertion, success, column where applicable).

#### Scenario: Result emitted as facet
- **WHEN** a freshness check fails for dataset `crm.contacts`
- **THEN** an event for `crm.contacts` carries a `dataQualityAssertions` entry with assertion `freshness` and `success: false`

### Requirement: Owner-routed alerts
On a transition to `warn` or `error`, the system SHALL notify the asset's accountable owner through the configured channel (e-mail or webhook). Alerts MUST be deduplicated into one open incident per asset and check; repeated failures MUST update the incident instead of sending new alerts. Alert content MUST NOT include record values that are classified as personal data.

#### Scenario: Repeated failure does not spam
- **WHEN** the same freshness check fails on three consecutive runs
- **THEN** exactly one incident exists, its failure count is 3, and only one notification was sent

#### Scenario: Personal data kept out of alert
- **WHEN** a duplicate-share check fails on a customer table
- **THEN** the alert names the table, check and observed share but contains no customer names or IDs

### Requirement: Incident lifecycle
Each incident SHALL move through `open`, `acknowledged`, `resolved` or `accepted_risk`. Acknowledgement, resolution and risk acceptance MUST be made by a named human with a reason and MUST be audited. An `error` incident not acknowledged within the escalation period (default 2 business days) MUST escalate to the `use_case_owner` named in the pilot charter.

#### Scenario: Escalation on silence
- **WHEN** an `error` incident remains `open` for more than 2 business days
- **THEN** the `use_case_owner` is notified and the escalation is recorded on the incident

#### Scenario: Auto-resolve on recovery
- **WHEN** a check that caused an open incident passes on a later run
- **THEN** the incident status becomes `resolved` with resolver `system` and the recovering run ID

### Requirement: Agent exposure of freshness state
The freshness status of each asset SHALL be available to the agent interface so that answers drawing on an asset in `error` state can be flagged or abstained on.

#### Scenario: Stale asset flagged to agent
- **WHEN** the agent retrieves a chunk from an asset whose freshness status is `error`
- **THEN** the chunk metadata delivered through the agent interface includes `freshness_status: error` and the asset's last change timestamp

### Requirement: Scorecard and discard feedback
Monitoring results SHALL update the readiness scorecard's currentness and quality criteria per asset after each run. An asset in `error` for more than 30 consecutive days SHALL be proposed as a candidate for the discard pass; the proposal MUST NOT archive or delete anything without human approval.

#### Scenario: Long-stale asset proposed for discard
- **WHEN** an asset has been in freshness `error` for 31 consecutive days
- **THEN** a discard candidate referencing the asset and incident is created and the asset remains unchanged until a human decides

#### Scenario: Scorecard updated
- **WHEN** a monitor run completes
- **THEN** each monitored asset's scorecard currentness criterion reflects the latest freshness status and run ID
