## ADDED Requirements

### Requirement: Default category set
The system SHALL seed each pilot with five default categories: `kb-and-sops`, `customer-records`, `policies-and-contracts`, `financial-ops-metrics`, and `support-history`. Each has a name, a description, and a default sensitivity baseline. Approvers MUST be able to add, rename, or retire categories per pilot. Retiring a category that still has assigned assets MUST be rejected.

#### Scenario: Defaults present on new pilot
- **WHEN** a new pilot catalog is initialised
- **THEN** exactly the five default categories exist with descriptions

#### Scenario: Retire non-empty category rejected
- **WHEN** an approver retires `support-history` while 3 assets are assigned to it
- **THEN** the system rejects the change and lists the 3 assets

### Requirement: Cross-system category assignment
Every in-scope inventory record (from `source-inventory`) SHALL have exactly one primary category and zero or more secondary categories, regardless of source system. A category view MUST list all assets of that category across all systems, grouped by `system_type`.

#### Scenario: Category spans systems
- **WHEN** contracts are inventoried from a SharePoint library, a file-share folder, and a CRM attachment table, and all three are assigned `policies-and-contracts`
- **THEN** `konmari categories show policies-and-contracts` lists all three assets grouped under `sharepoint`, `fileshare`, and `sql`

#### Scenario: Second primary rejected
- **WHEN** a user assigns a second primary category to an asset that already has one
- **THEN** the system rejects the assignment and tells the user to demote the existing primary first

### Requirement: Rule-based suggestions with human approval
The system SHALL suggest a primary category for each unassigned asset from transparent rules (path and title keywords in German and English, media type, table names, and sensitivity hints). Each suggestion MUST record which rule fired. Suggestions MUST remain in state `suggested` until a human accepts, changes, or rejects them, and downstream steps MUST NOT treat a `suggested` assignment as final.

#### Scenario: Keyword rule suggests category
- **WHEN** an asset path contains `Vertraege` or `contracts`
- **THEN** the system suggests `policies-and-contracts` with the rule ID and matched keyword recorded

#### Scenario: Suggestion not final until accepted
- **WHEN** an asset has only a `suggested` assignment and the category handoff runs
- **THEN** the asset is reported as unassigned and blocks the handoff

### Requirement: Audited and reversible assignments
Every accept, change, reject, and revert of a category assignment SHALL write an audit entry with actor, timestamp, asset, previous value, new value, and reason. The system MUST allow reverting an asset to any earlier assignment, and the revert MUST itself be audited.

#### Scenario: Revert to earlier assignment
- **WHEN** a user reverts an asset from `support-history` back to its earlier `customer-records` assignment
- **THEN** the primary category is `customer-records`, and the audit trail holds both the original change and the revert

### Requirement: Cross-system overlap hints
For each category the system SHALL flag likely overlaps across systems: assets with matching normalised titles, identical content hashes where available, or the same table or column names in different systems. These are hints only and MUST be passed to `dedup-detection` without any discard action in this change.

#### Scenario: Same document in two systems
- **WHEN** `Reisekostenrichtlinie_2025.pdf` is inventoried in both SharePoint and a file share with identical content hash
- **THEN** the category view of `policies-and-contracts` shows an overlap hint linking both assets, and no asset status changes

### Requirement: Risk-based category ordering
The system SHALL compute a risk score per category from documented inputs: the highest confirmed sensitivity tier among its assets, the share of assets with personal data, the number of distinct source systems, and the number of overlap hints. It SHALL propose an order from lowest to highest score, breaking ties with the default order `kb-and-sops`, `support-history`, `policies-and-contracts`, `customer-records`, `financial-ops-metrics`. Any category containing a `special-category` asset MUST be ordered after all categories without one. The weights MUST be configurable, and they MUST be shown in the proposal output.

#### Scenario: Special-category data goes last
- **WHEN** `support-history` contains one asset confirmed as `special-category` and no other category does
- **THEN** the proposed order places `support-history` last regardless of the other scores

#### Scenario: Score inputs are explained
- **WHEN** `konmari categories order --propose` runs
- **THEN** the output shows each category's score, the input values, and the weights used

### Requirement: Approved order as handoff
The category order SHALL take effect only after an approver named in the pilot charter approves it. The approved order MUST be versioned, every later change MUST create a new version with a reason, and downstream steps MUST read the latest approved version. Handoff MUST be blocked while any in-scope asset has no accepted primary category.

#### Scenario: Unapproved order not used
- **WHEN** an order has been proposed but not approved and `readiness-scorecard` requests the category sequence
- **THEN** the system returns no sequence and the reason `category order not approved`

#### Scenario: Order change versioned
- **WHEN** an approver moves `customer-records` before `policies-and-contracts` in approved version 1, with a reason
- **THEN** version 2 becomes the active order, and version 1 stays retrievable with its approver and timestamp

### Requirement: Step order within each category
The system SHALL track, per in-scope category, the status of each KonMari step in this fixed order: `inventory` (02), `score` (03), `discard` (04), `standardize` (05), `enrich` (06), `govern` (07), `connect` (08), `evaluate` (09). A step MUST refuse to process a category until the preceding step is `complete` for that category, so cleaning and standardizing always come before enriching and connecting. A step MAY start on a category that comes later in the approved category order before earlier categories have finished that step only when an approver records a waiver with a reason. Each status change and waiver MUST be audited.

#### Scenario: Enrich before standardize refused
- **WHEN** `konmari enrich run` is started for `kb-and-sops` while its `standardize` step is not `complete`
- **THEN** the command exits non-zero and names the missing step

#### Scenario: Skipping ahead in the category order needs a waiver
- **WHEN** `konmari discard detect` is started for `customer-records` while the earlier category `kb-and-sops` has not finished `discard`
- **THEN** the command is refused unless an approver has recorded a waiver for this category and step
