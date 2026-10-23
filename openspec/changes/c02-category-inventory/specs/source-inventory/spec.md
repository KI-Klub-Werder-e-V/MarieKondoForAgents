## ADDED Requirements

### Requirement: Pilot-scoped source registration
The system SHALL only scan sources listed in the approved pilot charter (from `pilot-charter`). Each source registration MUST name a connector type (`sharepoint`, `sql`, `fileshare`, or `csv-import`), a locator (site/drive ID, connection reference, share path, or file), and the charter ID. Registering a source that is not in the approved charter MUST be rejected.

#### Scenario: Source in charter is accepted
- **WHEN** a user registers SharePoint site `contoso.sharepoint.com/sites/support` and that site is listed in the approved charter
- **THEN** the system stores the source registration linked to the charter ID and records the action in the audit trail

#### Scenario: Source outside charter is rejected
- **WHEN** a user registers file share `\\fs01\hr` and the approved charter does not list it
- **THEN** the system rejects the registration with an error naming the charter and creates no inventory records

### Requirement: Least-privilege read-only connectors
Connectors MUST use read-only credentials resolved from a secret reference at run time, and MUST NOT write to, move, or change permissions on any source. The SharePoint/M365 connector MUST authenticate with the Microsoft Graph `Sites.Selected` application permission and MUST enumerate sites and drives explicitly, never through Graph Search. Credentials MUST NOT be written to the catalog, logs, or reports.

#### Scenario: Tenant-wide permission is refused
- **WHEN** the SharePoint connector starts with a token whose roles include `Sites.Read.All` or any `*.ReadWrite.All` role
- **THEN** the scan aborts before any request to a site, with an error that names the permission to replace

#### Scenario: Secret never persisted
- **WHEN** a scan completes with a SQL connection whose password comes from a secret reference
- **THEN** neither the catalog tables nor the run log contain the password value, only the reference name

### Requirement: Inventory record per asset
For each discovered asset (document library, folder subtree, file collection, table/view, CRM object, or imported row), the system SHALL create exactly one inventory record with these fields: `asset_uri`, `source_id`, `system_type`, `title`, `owner` (one named person), `steward`, `update_frequency` (ISO 8601 duration or one of `continuous`, `irregular`, `unknown`), `last_modified`, `format` (media type or `table`), `size` (items or rows), `sensitivity_tier`, `personal_data_categories`, `ropa_ref`, `access_rights_note`, `first_seen`, `last_seen`, and `status`. Field semantics MUST map to DCAT 3 (`dcterms:publisher`, `dcat:contactPoint`, `dcterms:accrualPeriodicity`, `dcterms:modified`, `dcterms:format`, `dcterms:accessRights`) as documented in the export.

#### Scenario: Record created for a document library
- **WHEN** a scan discovers document library `Shared Documents` on a registered site
- **THEN** exactly one inventory record exists for its `asset_uri`, with `system_type` `sharepoint`, `format` derived from the dominant media type, and `last_modified` set to the newest item's modification time

#### Scenario: Rescan does not duplicate
- **WHEN** the same source is scanned twice without changes
- **THEN** the number of inventory records is unchanged and only `last_seen` is updated

### Requirement: Owner and metadata completeness gate
An inventory record SHALL have status `complete` only when `owner` is a single named individual (not a group or shared mailbox) and `update_frequency`, `format`, and `sensitivity_tier` are set to a value other than `unknown`. Otherwise its status MUST be `incomplete`, and the record MUST list the missing fields. The system MUST block the downstream handoff to `readiness-scorecard` while any in-scope record is `incomplete`, unless an approver records a waiver with a reason.

#### Scenario: Group owner keeps record incomplete
- **WHEN** a record's owner is set to the distribution list `support-team@example.org`
- **THEN** the record status is `incomplete` with missing field `owner` and the reason `owner must be a named individual`

#### Scenario: Handoff blocked by gaps
- **WHEN** `konmari inventory report --gate` runs and 2 of 40 in-scope records are `incomplete` with no waiver
- **THEN** the command exits non-zero and lists both records with their missing fields

#### Scenario: Waiver allows handoff
- **WHEN** an approver records a waiver with a reason for an incomplete record
- **THEN** the gate passes for that record and the waiver, approver, and reason are in the audit trail

### Requirement: Local sample-based sensitivity hints
The system SHALL detect personal-data hints locally (with no external API call) on a bounded sample per asset. The default sample is the first 200 items or rows, configurable per run. Detection MUST support German recognizers (at least tax ID, ID card, passport, social security, health insurance, IBAN, e-mail, phone). It MUST store only entity types, counts, and the maximum confidence score per asset, never matched values or text excerpts. Hints with a score below the configured threshold (default 0.5) MUST be discarded, and postal-code-only hits MUST NOT count as personal data without address context.

#### Scenario: German ID detected without storing values
- **WHEN** a sampled CSV contains 12 valid Steueridentifikationsnummern
- **THEN** the asset's hints show `DE_TAX_ID` with count 12, and no catalog table or log contains any of the numbers

#### Scenario: No outbound call
- **WHEN** a sensitivity scan runs on a host whose outbound network is blocked except to the registered sources
- **THEN** the scan completes and produces hints

### Requirement: Human-confirmed sensitivity tier
Each record's `sensitivity_tier` SHALL be one of `public`, `internal`, `confidential`, `personal`, or `special-category` (GDPR Art. 9). The system MAY propose a tier from the hints, but the tier MUST remain `proposed` until the asset owner or a compliance role confirms or overrides it. The system MUST NOT propose a tier lower than one the hints support. Confirmation and override MUST record actor, timestamp, previous value, new value, and reason.

#### Scenario: Proposal awaits confirmation
- **WHEN** hints contain `DE_HEALTH_INSURANCE` for an asset
- **THEN** the proposed tier is `special-category` with state `proposed`, and the record counts as `incomplete` until it is confirmed

#### Scenario: Owner override is audited
- **WHEN** the owner overrides a proposed `personal` tier to `internal` with the reason "test data, synthetic"
- **THEN** the tier becomes `internal`, and the audit trail holds the actor, both values, and the reason

### Requirement: RoPA linkage
For records with tier `personal` or `special-category`, the system SHALL require either a `ropa_ref` that points to an existing GDPR Art. 30 record-of-processing entry, or an explicit `ropa_missing` flag that the report surfaces to compliance. The system MUST NOT generate or modify RoPA entries itself.

#### Scenario: Missing RoPA surfaced
- **WHEN** a `personal` record has no `ropa_ref`
- **THEN** the inventory report lists it under "RoPA gaps for compliance" with its owner

### Requirement: Incremental rescans and missing assets
The system SHALL support incremental rescans: Graph delta links for SharePoint, modification time and size for file shares, and a schema hash for SQL. State is keyed by a stable source ID. An asset absent from a full rescan, or reported deleted by a delta, MUST get status `missing` and a `missing_since` timestamp, and MUST NOT be deleted from the catalog. An asset that reappears MUST return to its previous status.

#### Scenario: Deleted library marked missing
- **WHEN** a delta response marks a previously inventoried folder as deleted
- **THEN** its record has status `missing` with `missing_since` set and is still queryable

#### Scenario: Reappearing asset restored
- **WHEN** a later scan finds an asset that is in status `missing`
- **THEN** its status reverts to the value before it went missing and `missing_since` is cleared

### Requirement: Manual import for unsupported systems
The system SHALL accept a CSV or YAML import with the same fields as an inventory record, for systems without a connector (for example a SaaS CRM without database access). Imported records MUST be marked `origin=manual` and validated against the same field rules, and invalid rows MUST be reported with line numbers without aborting valid rows.

#### Scenario: Partially invalid import
- **WHEN** a CSV with 10 rows is imported and row 7 has `update_frequency=weekly-ish`
- **THEN** 9 records are created, and the command reports that line 7 has an invalid `update_frequency`

### Requirement: Inventory report and lineage export
The system SHALL produce an inventory report (Markdown and CSV) per pilot. The report lists records by status, completeness percentage, sensitivity-tier counts, RoPA gaps, and missing assets. For each inventoried asset the system SHALL emit an OpenLineage dataset event carrying the `ownership` and, where known, `schema` facets.

#### Scenario: Report totals are consistent
- **WHEN** `konmari inventory report` runs for a pilot with 40 records, 36 of them complete
- **THEN** the report shows 90 % completeness, and the CSV contains 40 data rows

#### Scenario: Lineage event emitted
- **WHEN** a scan creates a record for table `crm.public.tickets`
- **THEN** an OpenLineage event for dataset `crm.public.tickets` is emitted, with an `ownership` facet naming the record owner
