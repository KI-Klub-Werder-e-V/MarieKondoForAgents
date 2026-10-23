## ADDED Requirements

### Requirement: Data product descriptor
The system SHALL represent every data product as a descriptor consisting of an ODPS v1.0 product document and one ODCS v3.1 data contract per output port, stored as YAML under version control and registered in the readiness catalog. The descriptor MUST contain: a unique product id, a human-readable name, a purpose statement tied to a pilot use case from the pilot charter, an accountable owner, the source datasets with their source-of-truth status, the output schema with a business definition for every field (from the business glossary), data classification per field, quality rules, a freshness SLA, the allowed access scopes, and a semantic version.

#### Scenario: Complete descriptor is accepted
- **WHEN** a user runs `konmari product lint` on a descriptor that contains every mandatory element and validates against the ODPS and ODCS JSON Schemas
- **THEN** the command exits with status 0 and reports the descriptor as valid

#### Scenario: Missing mandatory element is rejected
- **WHEN** a descriptor lacks an owner, a purpose, a freshness SLA, or a business definition for any field
- **THEN** `konmari product lint` exits with a non-zero status and lists each missing element by path

#### Scenario: Purpose not in pilot charter
- **WHEN** a descriptor's purpose references a use case that is not in the approved pilot charter
- **THEN** lint fails with an error stating that the product is out of pilot scope

### Requirement: Only ready and governed data enters a product
The system SHALL allow a dataset as a product source only if it passed the readiness scorecard, has a resolved source-of-truth decision, and has access-control and masking policies assigned. Fields classified as personal data MUST be either excluded, masked according to the assigned masking policy, or explicitly justified by the purpose statement.

#### Scenario: Source failed readiness
- **WHEN** a descriptor references a dataset whose latest readiness scorecard result is "fix" or "discard"
- **THEN** lint fails and names the dataset and its scorecard result

#### Scenario: Unjustified personal data field
- **WHEN** an output field is classified as personal data, has no masking policy and no justification in the purpose statement
- **THEN** lint fails with an error referencing the field and DSGVO data minimisation

### Requirement: Contract tests against real data
The system SHALL run the contract's schema and quality rules against the materialised product data before any publish and after every refresh, and SHALL record the result (pass/fail, failing rules, row counts) in the catalog.

#### Scenario: Quality rule fails
- **WHEN** `konmari product test` runs and a quality rule (for example a not-null or uniqueness rule) is violated
- **THEN** the command exits non-zero, lists the failing rules with violation counts, and stores a failed test result in the catalog

### Requirement: Semantic versioning with breaking-change gate
The system SHALL version every data product with semantic versioning (MAJOR.MINOR.PATCH). Before publishing a new version, the system MUST compare the contract with the latest published version; a backward-incompatible change (removed or renamed field, changed type, tightened enum, changed primary key) MUST require a MAJOR increment, a compatible addition MUST require at least a MINOR increment, and a metadata-only change MUST require at least a PATCH increment.

#### Scenario: Breaking change with minor bump is blocked
- **WHEN** a field is removed from the contract and the version is raised only from 1.2.0 to 1.3.0
- **THEN** `konmari product diff` reports the change as breaking and `konmari product publish` refuses with a message requiring version 2.0.0

#### Scenario: Compatible addition
- **WHEN** a new optional field is added and the version is raised from 1.2.0 to 1.3.0
- **THEN** diff reports a non-breaking change and publish is allowed to proceed to approval

### Requirement: Human-approved, audited publish
The system SHALL publish a data product version only after an approval by the product owner recorded in the catalog with approver identity, timestamp, version, contract diff summary and test result. Publishing MUST write an audit log entry. A version whose contract tests failed MUST NOT be publishable.

#### Scenario: Publish without approval
- **WHEN** a user runs `konmari product publish` for a version that has no recorded owner approval
- **THEN** the command refuses, leaves the published version unchanged, and records the refused attempt in the audit log

#### Scenario: Approved publish
- **WHEN** the owner approves version 1.3.0 whose tests passed and publish is run
- **THEN** the catalog marks 1.3.0 as the current published version, keeps 1.2.0 as superseded, and writes an audit entry with approver and diff summary

### Requirement: Reversible publish and deprecation window
The system SHALL allow rolling back to the previously published version with a single command that is itself approved and audited. When a new MAJOR version is published, the previous MAJOR version MUST remain callable and marked deprecated for a configurable window (default 90 days) and its descriptor MUST state the successor and the sunset date.

#### Scenario: Rollback
- **WHEN** the owner runs `konmari product publish --rollback` on a product whose current version is 1.3.0 and previous is 1.2.0
- **THEN** 1.2.0 becomes the current version, 1.3.0 is marked withdrawn, and an audit entry records the rollback and its reason

#### Scenario: Deprecated major still callable
- **WHEN** version 2.0.0 is published and 30 days have passed
- **THEN** version 1.x is still listed in the catalog as deprecated with successor 2.0.0 and its sunset date

### Requirement: Scheduled refresh with change tracking
The system SHALL refresh every published data product on a schedule derived from its freshness SLA, without manual steps. Each refresh MUST load only changed source records (cursor-based incremental merge by default, log-based CDC where configured), MUST be idempotent when re-run for the same source state, and MUST record a refresh run with start/end time, source cursor or log position, counts of inserted, updated and deleted records, and the resulting `as_of` timestamp.

#### Scenario: Incremental refresh
- **WHEN** a scheduled refresh runs after 12 source records were updated since the last cursor value
- **THEN** only those records are merged, the run records 12 updates, and the product's `as_of` advances to the run's source high-water mark

#### Scenario: Re-run is idempotent
- **WHEN** the same refresh is executed twice with no source changes in between
- **THEN** the second run records zero inserts, updates and deletes and the product data is unchanged

#### Scenario: Deletes are propagated
- **WHEN** a source record that belongs to the product is deleted or archived by a retention decision
- **THEN** the next refresh removes or tombstones it in the product and counts it as a delete

### Requirement: Failed refresh never overwrites good data
The system SHALL run contract tests on refreshed data before swapping it into the published product. If tests fail or the refresh errors, the previously published data MUST stay in place, the product MUST keep its previous `as_of`, and the failure MUST be recorded for freshness monitoring.

#### Scenario: Refresh fails contract test
- **WHEN** a refresh produces data that violates a uniqueness rule
- **THEN** the published data remains the previous snapshot, the run is stored with status failed and the violated rule, and `as_of` is not advanced

### Requirement: Lineage events per refresh and publish
The system SHALL emit an OpenLineage run event (START and COMPLETE or FAIL) for every refresh and publish, naming the job, the input datasets and the output data product version, and SHALL store the events in the catalog.

#### Scenario: Lineage of a refresh
- **WHEN** a refresh of product `support-faq` version 1.3.0 completes
- **THEN** the catalog holds a START and a COMPLETE OpenLineage event whose outputs reference `support-faq@1.3.0` and whose inputs reference each source dataset

### Requirement: Product catalog
The system SHALL provide a catalog of data products listing for each product its id, name, purpose, owner, current and deprecated versions, freshness SLA, current `as_of`, last refresh status, classification summary and the MCP tools and resources that expose it. The catalog MUST be available via `konmari product list` (table and JSON output) and MUST NOT list draft or withdrawn versions as callable.

#### Scenario: List catalog as JSON
- **WHEN** a user runs `konmari product list --json`
- **THEN** the output is valid JSON containing one entry per product with all listed attributes, and draft versions are absent from the callable versions
