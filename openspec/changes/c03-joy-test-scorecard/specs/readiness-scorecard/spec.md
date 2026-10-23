## ADDED Requirements

### Requirement: Versioned readiness rubric
The system SHALL maintain a versioned rubric that defines exactly six joy criteria: `authoritative_current`, `owned_maintained`, `meaning_clear`, `retrievable_precise`, `conflicts_resolved` and `permissions_explicit`. For each criterion, the rubric SHALL define the 0–3 level descriptors (0 = absent, 1 = partial, 2 = meets, 3 = meets and is automated or monitored), whether the criterion is a gate, and the mapped standard dimensions (DAMA, ISO/IEC 25012, FAIR). Every stored score MUST reference the rubric version it was produced with. A published rubric version MUST be immutable.

#### Scenario: Default rubric is available
- **WHEN** a user runs `konmari score` in a catalog without a custom rubric
- **THEN** the system uses the shipped default rubric version with all six criteria, level descriptors, and `authoritative_current` and `permissions_explicit` marked as gates

#### Scenario: Published rubric cannot be edited
- **WHEN** a user attempts to change a level descriptor or gate flag of a published rubric version
- **THEN** the system rejects the change and requires a new rubric version to be created instead

#### Scenario: Scores reference rubric version
- **WHEN** any criterion score is written to the catalog
- **THEN** the row contains the rubric version identifier, and a query of scores grouped by rubric version returns no row without a version

### Requirement: Pilot-scoped scoring
The system SHALL score only assets that are in the source inventory and in scope of the active pilot charter. Every scorecard run MUST record the pilot charter identifier. Assets outside the pilot scope MUST NOT receive a verdict.

#### Scenario: Out-of-scope asset is skipped
- **WHEN** a scoring run starts and an inventoried asset belongs to a category that the pilot charter excludes
- **THEN** the asset receives no criterion scores and no verdict, and the run summary lists it as out of scope

#### Scenario: No active charter
- **WHEN** a user runs `konmari score` and no approved pilot charter exists
- **THEN** the command exits with a non-zero status and a message naming the missing charter, and writes no scores

### Requirement: Automated evidence probes
The system SHALL pre-fill each criterion score using automated probes, and SHALL store for every probe result the probe name, the evidence reference (catalog IDs, check results or report IDs), the evidence timestamp and the proposed level. The probes SHALL at least detect:
- staleness, as last update older than the expected update frequency in the source inventory
- a missing or inactive owner
- the share of fields or sections mapped to glossary terms
- a missing stable record or chunk identifier
- open duplicate or conflict clusters
- broad access grants (organisation-wide groups such as "Everyone", "Everyone except external users", or anonymous links)

A criterion without usable evidence MUST be marked `unknown` instead of being given a level.

#### Scenario: Stale asset detected
- **WHEN** an asset's expected update frequency is 30 days and its last modification is 75 days ago
- **THEN** the `authoritative_current` probe proposes level 0 or 1 and stores the last-modified timestamp and the expected frequency as evidence

#### Scenario: Broad ACL detected
- **WHEN** the ACL snapshot for a SharePoint document shows a grant to "Everyone except external users"
- **THEN** the `permissions_explicit` probe proposes level 0 and stores the ACL snapshot reference and its capture timestamp

#### Scenario: Missing evidence
- **WHEN** no retrieval evaluation result and no stable identifier information exist for an asset
- **THEN** `retrievable_precise` is stored as `unknown`, and the asset cannot receive a `keep` proposal

#### Scenario: Expired evidence
- **WHEN** a probe's evidence timestamp is older than the configured evidence maximum age
- **THEN** the score is flagged `stale_evidence` and the probe re-runs before any verdict is proposed

### Requirement: Evidence excludes personal data content
Probe evidence and review justifications stored by the scorecard MUST contain only references, hashes, counts and metadata. They MUST NOT contain content excerpts from assets classified as containing personal data.

#### Scenario: Personal data asset evidence
- **WHEN** a probe evaluates an asset whose sensitivity in the source inventory is `personal`
- **THEN** the stored evidence contains identifiers and counts only, and a test scanning the evidence column for the asset's text content finds no match

### Requirement: Verdict proposal with gates
The system SHALL propose exactly one verdict per scored asset, using these rules:
- `discard` when the asset is marked irrelevant to the pilot use case, OR when it is a superseded or duplicate copy for which a winning copy exists, OR when it is stale beyond the configured multiple (default 2×) of its expected update frequency and has no active owner
- otherwise `keep` when every criterion is at level ≥ 2 (the configured keep threshold) and no criterion is `unknown`
- otherwise `fix`

A gate criterion at level 0 MUST prevent `keep` regardless of any aggregate score. Each `fix` verdict MUST list one fix item per criterion below the threshold, naming the downstream capability expected to resolve it.

#### Scenario: All criteria meet the bar
- **WHEN** an asset scores 2 or 3 on all six criteria and has no open conflict cluster
- **THEN** the proposed verdict is `keep`

#### Scenario: Gate blocks keep
- **WHEN** an asset scores 3 on five criteria and 0 on `permissions_explicit`
- **THEN** the proposed verdict is `fix`, with a fix item for `permissions_explicit` routed to `access-control`

#### Scenario: Duplicate with winner
- **WHEN** an asset belongs to a duplicate cluster in which another asset is designated the winning copy
- **THEN** the proposed verdict is `discard` with reason `redundant` and a reference to the winning asset

#### Scenario: Irrelevant to pilot
- **WHEN** a domain expert marks an in-scope asset as not needed for any pilot question
- **THEN** the proposed verdict is `discard` with reason `irrelevant`

### Requirement: Human review and reversible decisions
A proposed verdict SHALL become effective only after a named reviewer who is authorised in the pilot charter accepts or overrides it. An override MUST include a free-text justification. Every proposal, acceptance, override and reversal MUST be appended to the audit trail with the actor, the timestamp, the previous and new verdict, and the rubric version. A decision MUST be reversible by a new review entry. The scorecard MUST NOT delete, move or modify source data.

#### Scenario: Reviewer accepts
- **WHEN** an authorised reviewer runs `konmari review accept <asset>`
- **THEN** the verdict status becomes `approved` and an audit entry records the reviewer and the timestamp

#### Scenario: Override without justification
- **WHEN** a reviewer overrides `discard` to `keep` without a justification
- **THEN** the system rejects the override and the verdict remains `proposed`

#### Scenario: Unauthorised reviewer
- **WHEN** a user who is not listed as reviewer in the pilot charter attempts to approve a verdict
- **THEN** the system rejects the action and logs the denied attempt

#### Scenario: Reversal
- **WHEN** a reviewer reverses an approved `discard` before c04-discard-pass has executed it
- **THEN** a new review entry sets the verdict to the new value, the earlier entries remain in the audit trail, and downstream consumers see only the latest approved verdict

#### Scenario: Source untouched
- **WHEN** a `discard` verdict is approved
- **THEN** the source asset is unchanged, and the verdict is exposed for c04-discard-pass retention-decisions to act on

### Requirement: Re-scoring on change
The system SHALL re-score an asset when its source-inventory entry changes, when a downstream fix is marked complete, or when a freshness alert for it is raised. A re-score that changes the proposed verdict MUST set the verdict status back to `proposed` for review.

#### Scenario: Fix completed
- **WHEN** a fix item for `meaning_clear` is marked complete by 06 business-glossary
- **THEN** the asset is re-scored and, if all criteria now meet the bar, a new `keep` proposal awaits review

### Requirement: Category maturity rollup
The system SHALL compute, per category in the category map, a maturity view over the six aspects of the source brief's table: Format, Metadata, Access, Consistency, Update mechanism and Discoverability. Each aspect maps to one or more criteria. For each aspect, the view SHALL report the share of in-scope assets scoring ≥ 2 on the mapped criteria and a state of `poorly_prepared`, `in_progress` or `agent_ready`. `agent_ready` requires the share to be at least the configured threshold (default 80 %). The view SHALL also report the counts of keep, fix, discard and unreviewed verdicts. The aggregation rule MUST be documented in the report output.

#### Scenario: Category rollup computed
- **WHEN** a category has 10 in-scope assets and 9 score ≥ 2 on `permissions_explicit`
- **THEN** the Access aspect for that category shows 90 % and state `agent_ready`

#### Scenario: Unknown scores count against readiness
- **WHEN** an asset's mapped criterion is `unknown`
- **THEN** it is counted as not meeting the bar in the share calculation

### Requirement: Scorecard reports
The system SHALL produce, via `konmari scorecard report`, an asset-level and a category-level report in Markdown, CSV and JSON. The reports SHALL include per-criterion scores, the verdict and its review status, fix items, evidence timestamps, the rubric version and the pilot charter identifier. The JSON output MUST validate against a published JSON Schema.

#### Scenario: Report generated
- **WHEN** a user runs `konmari scorecard report --format json`
- **THEN** the output validates against the shipped schema and contains every scored asset with its six criterion scores and current verdict

#### Scenario: Report never shows aggregate without criteria
- **WHEN** a Markdown report is generated
- **THEN** every asset row shows all six criterion levels alongside any aggregate figure
