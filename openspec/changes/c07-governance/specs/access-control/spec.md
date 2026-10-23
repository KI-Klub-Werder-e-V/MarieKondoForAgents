## ADDED Requirements

### Requirement: Source ACL capture
The system SHALL capture, for every in-scope document or row ingested from a source, the access control entries of that item in the source system (allowed principals, denied principals, inheritance origin), and SHALL store them in the catalog with the source ACL version or timestamp. This is the only ACL capture implementation; the `acl_tags` field of `semantic-metadata` (change 06) is filled from it. The system SHALL normalize principals to group identifiers where the source provides group membership, and SHALL record individual user identifiers only when no group grant exists.

#### Scenario: Document with group permissions
- **WHEN** a SharePoint document shared with group `support-team` is ingested
- **THEN** the catalog record for that document lists principal `group:support-team` as allowed, together with the source ACL timestamp

#### Scenario: Source exposes no ACL
- **WHEN** a connector cannot read ACLs for a source
- **THEN** the system marks every item from that source `acl_status = unknown` (empty `acl_tags`, status `needs-review` in `semantic-metadata`) and treats it as accessible to no principal until an owner-approved source policy assigns explicit principals

### Requirement: ACL propagation to chunks and records
The system SHALL copy the normalized ACL of a parent item to every chunk, embedding row or derived record produced from it by document chunking or normalization, and SHALL NOT create any index entry without ACL fields. When the parent ACL changes, the system SHALL update all derived entries.

#### Scenario: Chunks inherit ACL
- **WHEN** a document with allowed principals {A, B} is split into 12 chunks
- **THEN** all 12 chunk rows carry allowed principals {A, B} and the same ACL version as the parent

#### Scenario: Index write without ACL rejected
- **WHEN** a pipeline step attempts to insert a chunk whose ACL fields are empty
- **THEN** the insert fails and an error naming the chunk and source is logged

### Requirement: Permission enforcement inside retrieval
The system SHALL enforce access inside the retrieval query itself (Postgres row-level security keyed on a transaction-scoped principal context, or a pre-filter list of authorized object IDs from the ReBAC service), so that unauthorized chunks are never returned to the agent runtime or placed in a model prompt. Deny entries SHALL override allow entries. Post-generation filtering SHALL NOT be used as the only control.

#### Scenario: User without access queries
- **WHEN** a user who is not in any allowed principal of a chunk runs a semantic query whose best match is that chunk
- **THEN** the chunk is absent from the result set and the result contains only chunks the user is allowed to read

#### Scenario: Explicit deny wins
- **WHEN** a chunk allows `group:sales` and denies `user:x`, and `user:x` is in `group:sales`
- **THEN** retrieval for `user:x` does not return the chunk

#### Scenario: Principal context does not leak across pooled connections
- **WHEN** two requests for different users run sequentially on the same pooled database connection
- **THEN** the second request sees only rows permitted for its own user, because the principal context is set per transaction

### Requirement: ACL freshness and revocation
The system SHALL re-sync source ACLs on the cadence set in the source policy (default at most 24 hours), SHALL apply revocations to all derived index entries in the same sync run, and SHALL record the sync lag per source in the catalog.

#### Scenario: Access revoked in source
- **WHEN** a group is removed from a document's permissions in the source and the next ACL sync runs
- **THEN** after that sync the group's members no longer retrieve any chunk of the document, and the revocation is recorded as an audit event

#### Scenario: Sync overdue
- **WHEN** a source's last successful ACL sync is older than its configured cadence
- **THEN** the source's governance status changes to `acl_stale` and retrieval from that source is blocked until a sync succeeds

### Requirement: Agent identity and on-behalf-of user
Every agent request SHALL carry two identities: the agent (client) identity and the end user on whose behalf it acts, taken from an access token issued for the KonMari interface (OAuth 2.1; delegation expressed as `sub` = user and `act.sub` = agent per RFC 8693). Effective permissions SHALL be the intersection of the agent's granted scopes and the user's data permissions. Tokens not issued for the KonMari interface SHALL be rejected and SHALL NOT be passed through to downstream systems.

#### Scenario: Agent broader than user
- **WHEN** an agent with scope over all support documents acts on behalf of a user who may read only the "Produkt A" folder
- **THEN** results are limited to "Produkt A" documents

#### Scenario: Foreign token
- **WHEN** a request presents a token whose audience is not the KonMari interface
- **THEN** the request is rejected with HTTP 401 and an audit event with decision `deny` and reason `invalid_audience` is written

#### Scenario: Autonomous agent without user
- **WHEN** a scheduled agent runs with no on-behalf-of user
- **THEN** only resources explicitly granted to that agent's service principal in the source policy are accessible, and the audit event records `on_behalf_of = none`

### Requirement: Least-privilege tool scopes
Each agent-facing tool SHALL declare whether it reads or writes and which source categories it touches. Read-only scopes SHALL be the default grant. A write scope SHALL require an approval by the data owner recorded in the catalog with an expiry date (default at most 90 days), and wildcard or omnibus scopes SHALL be refused.

#### Scenario: Write tool without approval
- **WHEN** an agent calls a write tool for a category with no active owner-approved write grant
- **THEN** the call is refused with HTTP 403 and an audit event with decision `deny` is written

#### Scenario: Write grant expires
- **WHEN** a write grant's expiry date passes
- **THEN** subsequent write calls are refused until the owner renews the grant

#### Scenario: Wildcard scope requested
- **WHEN** a client configuration requests scope `*` or `all`
- **THEN** configuration validation fails and names the offending scope

### Requirement: Source policies
The system SHALL maintain one versioned source policy per in-scope source or category with: agent exposure (`allowed` | `blocked`), sensitivity class, masking rule reference, ACL-sync cadence, permitted tools, data owner and approver. A policy change SHALL take effect only after a named approver with the data-owner or compliance role approves it, and every prior version SHALL remain retrievable so a change can be reverted.

#### Scenario: Policy change pending
- **WHEN** a facilitator edits a source policy to set exposure to `allowed`
- **THEN** the new version is stored as `pending` and enforcement continues to use the previously approved version until approval

#### Scenario: Revert policy
- **WHEN** an approver reverts a source policy to an earlier version
- **THEN** that version becomes active, and both the revert and the actor are recorded in the audit log

### Requirement: Permission leak test suite
The system SHALL provide an automated leak test suite that, for a set of test principals defined per pilot, issues retrieval queries designed to hit restricted content and asserts zero unauthorized chunks are returned. The suite SHALL run in CI and as part of `konmari govern check`.

#### Scenario: Leak detected
- **WHEN** any test query returns a chunk not permitted for the test principal
- **THEN** the suite fails, reports the chunk ID, source and principal, and the governance gate stays closed

#### Scenario: Clean run
- **WHEN** all test queries return only permitted chunks
- **THEN** the suite passes and records the run result and policy versions in the catalog

### Requirement: Governance gate before exposure
The system SHALL compute a governance status per source (`approved` only if ACL coverage is 100 %, the source policy is approved, the PII recall gate passed for sources with personal data, the leak suite passed and audit logging is active). Agent interfaces SHALL NOT expose a source whose status is not `approved`.

#### Scenario: Source not ready
- **WHEN** a source has 3 % of chunks with `acl_status = unknown`
- **THEN** its governance status is `blocked` with reason `acl_coverage` and the connect step refuses to expose it

#### Scenario: Source ready
- **WHEN** all gate conditions are met for a source
- **THEN** its governance status is `approved` and `konmari govern status` lists it as exposable together with the policy version
