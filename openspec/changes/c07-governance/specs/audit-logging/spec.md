## ADDED Requirements

### Requirement: Audit event for every agent access
The system SHALL write one audit event for every agent tool call and retrieval, allowed or denied, containing at least: event ID, UTC timestamp, agent identity, on-behalf-of user (or `none`), tool name, operation type (read/write), resource IDs returned or touched, decision (`allow`/`deny`) with reason, active source-policy versions, and trace ID. Audit events SHALL NOT contain prompt text, completion text, tool argument values or chunk content.

#### Scenario: Allowed retrieval
- **WHEN** an agent retrieves 5 chunks on behalf of user U
- **THEN** one audit event records agent, U, tool, the 5 chunk IDs, `allow` and the policy versions, and contains no chunk text

#### Scenario: Audit write fails
- **WHEN** the audit event for a tool call cannot be persisted
- **THEN** the tool call fails closed and returns an error instead of data

### Requirement: Audit event for governance decisions
The system SHALL write an audit event for every governance decision: source-policy creation, approval, revert; grant and expiry of write scopes; ACL-sync results and revocations; PII review decisions; re-identification lookups; and gate status changes. Each event SHALL record actor, before/after values (references, not personal data) and reason.

#### Scenario: Policy approved
- **WHEN** a data owner approves source policy version 3
- **THEN** an audit event records approver, policy ID, version 2→3 and the stated reason

### Requirement: Append-only tamper-evident storage
Audit events SHALL be stored append-only: the application role SHALL have insert and select rights but no update or delete rights, and each event SHALL include a hash over its content and the previous event's hash. The system SHALL provide `konmari audit verify` that recomputes the chain and reports the first broken link.

#### Scenario: Update attempt
- **WHEN** the application role issues an UPDATE on the audit table
- **THEN** the database rejects the statement

#### Scenario: Tampered row detected
- **WHEN** an audit row's content is altered directly by a superuser
- **THEN** `konmari audit verify` exits non-zero and reports the event ID where the chain breaks

### Requirement: Retention and deletion
Audit events SHALL be kept for a configured retention period, at least 6 months by default (aligned with EU AI Act Art. 26(6)), and no longer than the maximum documented in the pilot's DSGVO Art. 30 record. Expired events SHALL be deleted by a scheduled purge run by a separate retention role, and the purge SHALL write a checkpoint event (range, count, last hash) so the chain stays verifiable.

#### Scenario: Purge after retention
- **WHEN** the purge runs with retention 6 months
- **THEN** events older than 6 months are deleted, a checkpoint event is written, and `konmari audit verify` passes from the checkpoint onward

#### Scenario: Retention misconfigured
- **WHEN** retention is configured below 6 months for a deployment flagged as high-risk under the AI Act
- **THEN** configuration validation fails with a message citing Art. 26(6)

### Requirement: Audit export for data subjects and authorities
The system SHALL export all audit events for a given user identity or pseudonym token, or for a time range, as JSON Lines and CSV, for DSGVO Art. 15 requests and authority inspections. The export SHALL itself be audited.

#### Scenario: Data subject request
- **WHEN** compliance runs `konmari audit export --subject <user>`
- **THEN** a file containing every event where that user is the on-behalf-of user is produced, and an export event is logged

### Requirement: Trace correlation
Each audit event SHALL carry the trace ID of the request, and the system SHALL emit OpenTelemetry spans for agent invocations and tool executions (GenAI semantic conventions, `invoke_agent` / `execute_tool`) with content capture disabled, so audit events and operational traces can be joined without duplicating payloads.

#### Scenario: Correlate a denied call
- **WHEN** an operator looks up a denied audit event's trace ID
- **THEN** the matching `execute_tool` span is found, and it contains no prompt or argument content

### Requirement: Single shared audit trail
All pipeline steps (changes 01-06, 08 and 09) SHALL write their decisions to the one `audit_event` table created by `pilot-charter` (change 01); this capability hardens that table (append-only grants, hash chain, retention, export) and MUST NOT introduce a second audit store. `konmari audit verify` MUST cover events from every step, and step-specific commands such as `konmari discard log verify` MUST verify the same chain filtered by step.

#### Scenario: Earlier decisions in the same chain
- **WHEN** `konmari audit verify` runs after the inventory, discard and governance steps have written events
- **THEN** it verifies one continuous chain that contains the inventory waivers, the discard approvals and the policy approvals
