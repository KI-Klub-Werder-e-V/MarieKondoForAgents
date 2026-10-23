## ADDED Requirements

### Requirement: Mandatory metadata envelope
The system SHALL attach a metadata envelope to every chunk and every agent-exposed structured record. The envelope MUST contain the mandatory fields `source_id`, `source_uri`, `source_system`, `content_type` (category from the category map), `owner`, `created_at`, `modified_at`, `freshness_class`, `authority`, `lineage`, `acl_tags`, `sensitivity`, `language` and `enriched_at`, and MAY contain `glossary_terms`, `valid_from`/`valid_until`, `version` and `generated_context`. Dates MUST be ISO 8601 with time zone.

#### Scenario: Envelope present on chunk
- **WHEN** `konmari enrich run` completes for a chunked document
- **THEN** every active chunk of that document has all mandatory envelope fields populated in the catalog

#### Scenario: Envelope on structured record
- **WHEN** a CRM table is marked agent-exposed
- **THEN** each record view exposed to agents carries the same mandatory envelope fields at table level and the record's own `modified_at`

### Requirement: Metadata derived from upstream decisions
The system SHALL populate `owner`, `source_system`, `content_type` and `sensitivity` from the source inventory and category map (change 02), `authority` from the source-of-truth policy (change 05), and `acl_tags` from the source system's permissions at enrichment time. Manual overrides MUST be stored as decisions with author, reason and previous value, and MUST NOT be overwritten by later automated runs.

#### Scenario: Authority from source-of-truth policy
- **WHEN** the source-of-truth policy ranks the ERP above the CRM for "customer address"
- **THEN** chunks and records from the ERP carry `authority: primary` and CRM ones carry `authority: secondary`

#### Scenario: Manual override survives re-run
- **WHEN** a data owner overrides `owner` of a document and `konmari enrich run` runs again
- **THEN** the override value is kept and the decision log shows the original override

### Requirement: Completeness gate
The system SHALL compute metadata completeness per source and category and SHALL block marking a chunk or record as `ready-for-agents` while any mandatory field is missing. Missing `owner` or `acl_tags` MUST never be defaulted; the item MUST be set to `needs-review` and listed in the enrichment report.

#### Scenario: Missing ACL blocks readiness
- **WHEN** a chunk's source system returns no permission information
- **THEN** the chunk gets status `needs-review`, is not `ready-for-agents`, and the report lists it under "missing acl_tags"

#### Scenario: Completeness reported to scorecard
- **WHEN** enrichment finishes
- **THEN** per-category completeness percentages are written to the catalog for consumption by the readiness scorecard (change 03)

### Requirement: ACL tag synchronization
The system SHALL refresh `acl_tags` on every enrichment run using the single ACL capture and normalization implementation specified in `access-control` (change 07), so `acl_tags` holds the normalized allowed and denied principals and SHALL record when ACL tags of a chunk change. Chunks whose source permissions could not be refreshed in the current run MUST be flagged `acl_stale` so that access enforcement (change 07) can deny them.

#### Scenario: Permission change propagates
- **WHEN** a SharePoint folder's group "Vertrieb" loses read access and enrichment re-runs
- **THEN** all chunks from that folder no longer contain the tag for "Vertrieb" and an ACL change event is recorded

#### Scenario: Source unreachable marks stale
- **WHEN** the source connector fails during enrichment
- **THEN** affected chunks keep their previous tags, are flagged `acl_stale`, and the report lists them

### Requirement: Freshness classification
The system SHALL assign each chunk and record a `freshness_class` (`live`, `daily`, `weekly`, `monthly`, `static`) from the source's update frequency in the source inventory, and SHALL compute `stale_after` as `modified_at` plus the class interval. Content with `valid_until` in the past MUST be flagged `expired`.

#### Scenario: Expired policy flagged
- **WHEN** a policy chunk has `valid_until: 2025-12-31` and enrichment runs in 2026
- **THEN** the chunk is flagged `expired` and listed in the enrichment report

### Requirement: Lineage recording
The system SHALL record lineage for every chunk and record from the original source object through normalization, chunking and enrichment, including tool name and version per step, and SHALL emit OpenLineage-compatible run events with standard dataset facets (`schema`, `ownership`, `documentation`, `columnLineage` where applicable) plus a project-prefixed custom facet for chunk metadata with a versioned `_schemaURL`.

#### Scenario: Chunk traceable to source
- **WHEN** a user runs `konmari enrich lineage <chunk-id>`
- **THEN** the system shows the source object URI and version, the normalization run, the chunking run and profile, and the enrichment run that produced the chunk

#### Scenario: OpenLineage event emitted
- **WHEN** an enrichment run completes with lineage emission enabled
- **THEN** a COMPLETE run event is emitted whose output datasets carry the `ownership` and `schema` facets and the custom chunk-metadata facet

### Requirement: Clean table schemas
The system SHALL require, for every table and column exposed to agents, a human-readable description and a declared data type, unit (where numeric), allowed values or format (where applicable), nullability and a glossary-term link where a term exists. The system SHALL report schema coverage and MUST NOT mark a table `ready-for-agents` while any exposed column lacks both a description and a glossary link.

#### Scenario: Undocumented column blocks table
- **WHEN** column `kd_stat` has neither description nor glossary link
- **THEN** the table remains not ready and the schema coverage report lists `kd_stat`

#### Scenario: Column described via glossary
- **WHEN** column `kd_stat` is linked to the approved term "Kundenstatus / customer status"
- **THEN** the column counts as documented and the term definition is exposed with the schema

### Requirement: Contextual chunk header
The system SHALL generate for each chunk a deterministic context header of at most 100 tokens built from document title, heading path, source system, and `modified_at`, used as input for embedding and keyword indexing. LLM-generated context MAY be enabled per profile; when enabled it MUST be stored in `generated_context` labelled with the model name, MUST use a self-hosted model or an approved processor, and MUST NOT be applied to chunks with `sensitivity` of personal or special-category data unless masking (change 07) has been applied.

#### Scenario: Deterministic header created
- **WHEN** enrichment runs with the default profile
- **THEN** each chunk has a context header such as "Returns policy > Exceptions | SharePoint | 2026-03-02" of at most 100 tokens and no LLM is called

#### Scenario: Personal data not sent to LLM
- **WHEN** LLM context is enabled and a chunk is tagged `sensitivity: personal` without masking
- **THEN** no LLM call is made for that chunk and the report counts it as skipped

### Requirement: Audited and reversible enrichment
The system SHALL store each enrichment run with its configuration and SHALL allow rollback of a run's metadata changes to the previous values. Every override, rollback and gate decision MUST be written to the catalog's decision log with actor, timestamp, reason and affected IDs.

#### Scenario: Rollback restores previous metadata
- **WHEN** a user runs `konmari enrich rollback <run-id>`
- **THEN** metadata values changed by that run are restored to their previous values and a rollback decision is logged
