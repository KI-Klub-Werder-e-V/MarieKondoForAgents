## ADDED Requirements

### Requirement: Discard proposals from detection flags
The system SHALL turn detection flags and `readiness-scorecard` "discard" verdicts into proposals. Each proposal has exactly one action from `keep`, `archive`, `exclude`, `route-to-masking` or `erase`, plus the item ID, the supporting flag IDs and a plain-language reason. Proposals MUST NOT change any source system, archive or index until they are approved and executed.

#### Scenario: Proposal for a non-canonical duplicate
- **WHEN** an item is a non-canonical member of an exact-duplicate cluster
- **THEN** an `archive` proposal is created that references the cluster, the canonical item and the reason "exact duplicate of <canonical title>"

#### Scenario: Proposal does not change data
- **WHEN** `konmari discard propose` creates 200 proposals
- **THEN** no source item, archive object or downstream index changes until a proposal is approved and executed

### Requirement: Human approval by an authorized role
Each proposal SHALL require approval or rejection by a named person who holds the approver role for the item's category in the `pilot-charter`. The approver MUST NOT be the person or service account that created the proposal. Approvers SHALL be able to approve, reject, or change the action, and every response MUST store the approver identity, a timestamp and an optional comment. Bulk approval SHALL be allowed only for proposals that share action, category and flag method, and it MUST list the affected item IDs before confirmation.

#### Scenario: Unauthorized approver
- **WHEN** a user without the approver role for the category `policies-and-contracts` tries to approve an `archive` proposal for a contract
- **THEN** the approval is rejected with an authorization error, and the attempt is written to the decision log

#### Scenario: Approver changes the action
- **WHEN** an approver changes an `exclude` proposal to `keep` with the comment "still referenced by support team"
- **THEN** the decision is stored as `keep` with the original proposed action, the approver, the timestamp and the comment

#### Scenario: Bulk approval preview
- **WHEN** an approver bulk-approves 120 `archive` proposals of method `exact` in the category `kb-and-sops`
- **THEN** the system lists the 120 item IDs and requires an explicit confirmation before recording the decisions

### Requirement: Append-only decision log
The system SHALL write every proposal, approval, rejection, execution, restore, erasure and failed attempt as an append-only event to the catalog, with actor, timestamp, item ID, action, prior state, new state, and a hash chained to the previous event. Events are stored in the shared `audit_event` trail created by `pilot-charter` (change 01); this hash chain is the one `audit-logging` (change 07) verifies. The log MUST be exportable for the `audit-logging` capability, and no command may update or delete existing events.

#### Scenario: Chain verification detects tampering
- **WHEN** a decision event is altered directly in the database
- **THEN** `konmari discard log verify` reports the first event whose chain hash no longer matches, and exits non-zero

#### Scenario: Full history of one item
- **WHEN** a user queries the history of an item that was archived and later restored
- **THEN** the log returns the proposal, the approval, the archive execution and the restore events in order, each with its actor

### Requirement: Reversible archive to cold storage
Executing an approved `archive` decision SHALL copy the item's content and metadata to the configured cold-storage target and verify the copy by SHA-256. Only after verification may it remove the item from the active pilot corpus. The archive target MUST be readable only by the archive role. Each archived item SHALL have a manifest entry with the original location, the checksum, the decision ID and the applicable retention rule. The system SHALL NOT delete the item in the source system as part of archiving.

#### Scenario: Verified archive
- **WHEN** an approved archive decision runs for an item
- **THEN** the archived copy's checksum matches the source, a manifest entry is created, the item's catalog state becomes `archived`, and the source system is unchanged

#### Scenario: Checksum mismatch aborts
- **WHEN** the archived copy's checksum does not match the source
- **THEN** the item stays `active` in the pilot corpus, the partial copy is removed, and a failed-execution event is logged

### Requirement: Restore
The system SHALL restore an archived or excluded item to the active pilot corpus on request by an approver for the item's category, provided the item has no erasure record. The restore MUST verify the checksum against the manifest and MUST be logged.

#### Scenario: Restore archived item
- **WHEN** an approver runs `konmari discard restore <item-id>` for an archived item
- **THEN** the item returns to `active` with checksum verified, and a restore event references the original archive decision

#### Scenario: Restore after erasure refused
- **WHEN** a restore is requested for an item with an erasure record
- **THEN** the command refuses with the message that the item was erased on the recorded date, and logs the attempt

### Requirement: Exclude and route-to-masking
An executed `exclude` decision SHALL remove the item from the active pilot corpus without archiving a copy, and it MUST be reversible by restoring from the unchanged source system. An executed `route-to-masking` decision SHALL hand the item to the `sensitive-data-masking` capability, and it MUST keep the item out of the active pilot corpus until a masked version is registered.

#### Scenario: Sensitive item waits for masking
- **WHEN** a `route-to-masking` decision is executed for a support ticket export that contains customer names
- **THEN** the item is in state `awaiting-masking` and is excluded from every downstream export until change 07 registers a masked version

### Requirement: Downstream propagation of discards
When an item leaves the active pilot corpus (archive, exclude, awaiting-masking, erase), the system SHALL emit a lineage event and mark every derived artifact registered in the catalog (chunks, embeddings, data-product rows) for removal. A propagation check MUST report any derived artifact of a discarded item that is still present.

#### Scenario: Stale chunks detected
- **WHEN** an item is archived but its chunks are still present in the vector index
- **THEN** `konmari discard check-propagation` lists the item and the chunk IDs, and exits non-zero

### Requirement: Retention rule register per category
The system SHALL hold a retention rule for each category in the `category-map` that contains personal data or records with statutory retention duties. Each rule records: purpose, legal basis (DSGVO article), the retention source (statute and paragraph, contract or internal policy), the start event (e.g. end of the calendar year of the last entry), the period, the action at expiry (`erase` or `review`), and the approver from data protection or compliance. Periods MUST be data, not code constants. A pilot MUST NOT execute `erase` decisions for a category that lacks an approved rule.

#### Scenario: Rule with statutory source
- **WHEN** compliance registers a rule for the category `financial-ops-metrics` (booking vouchers) with source "§147(3) AO / §257(4) HGB", start event "end of calendar year of last entry" and period 8 years
- **THEN** the rule is stored with the approver and the date, and the expiry date of each item in the category is computed from its last-entry date

#### Scenario: Missing rule blocks erasure
- **WHEN** an `erase` proposal exists for an item whose category has no approved retention rule
- **THEN** execution is refused with the message that the retention rule is missing, and the proposal stays pending

#### Scenario: One period for all categories rejected
- **WHEN** a user tries to register a single wildcard rule that covers all categories
- **THEN** the system rejects it and asks for rules per category

### Requirement: Legal hold and restriction instead of erasure
The system SHALL support legal holds on items or categories, recording reason, owner and review date. Erasure MUST be refused while a hold applies. Where a statutory or contractual retention duty prevents erasure that is otherwise due (Art. 17(3)(b) DSGVO, §35(3) BDSG), the system SHALL set the item to `restricted`: excluded from the pilot corpus and from all agent-facing exports, and readable only by the archive role, until the retention period ends.

#### Scenario: Hold blocks erasure
- **WHEN** an approved `erase` decision is executed for an item under a legal hold for a pending dispute
- **THEN** execution is refused, the hold reason is shown, and the attempt is logged

#### Scenario: Erasure request during retention period
- **WHEN** an erasure is due for a customer's invoice record that is still within its 8-year booking-voucher retention period
- **THEN** the item becomes `restricted` with the retention rule reference and its end date, rather than being erased

### Requirement: Erasure with two-person approval and waiting period
An `erase` decision SHALL require approval by an item-category approver and, separately, by a data protection or compliance approver. It MUST wait a configurable period (default 14 days, our assumption) before execution, and the decision MUST be cancellable during the wait. Execution SHALL delete the item from the pilot corpus, the archive and the derived artifacts registered in the catalog, and it MUST produce an erasure record holding the item ID, category, rule, approvers and timestamp, with no content or personal data. Deleting from the source system is out of scope, and the erasure record MUST list the source location as an open action for the source owner.

#### Scenario: Single approval insufficient
- **WHEN** an `erase` decision has the data owner's approval but no compliance approval
- **THEN** it is not scheduled for execution

#### Scenario: Erasure executed after waiting period
- **WHEN** a doubly approved erase decision reaches the end of its waiting period without cancellation
- **THEN** archive copies and registered derived artifacts are deleted, an erasure record without content is written, and the source location is listed as an open action

### Requirement: Scheduled retention expiry check
The system SHALL provide `konmari retention check`, which lists active, archived and restricted items whose retention period has expired or will expire within a configurable window (default 30 days). For each such item it creates a proposal matching the rule's action at expiry. The check MUST cover the archive, and its schedule and last run time MUST be recorded.

#### Scenario: Archived item expires
- **WHEN** an archived HR application item reaches the end of its retention period
- **THEN** the check creates an `erase` proposal for it, and the proposal enters the normal two-person approval flow

#### Scenario: Check overdue
- **WHEN** the last successful retention check is older than the configured schedule interval
- **THEN** `konmari discard report` shows a warning that the retention check is overdue
