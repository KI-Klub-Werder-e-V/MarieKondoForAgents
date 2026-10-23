## ADDED Requirements

### Requirement: PII detection before indexing
The system SHALL scan every in-scope chunk and record field for personal data before it is written to any agent-facing index, using a detector configured for both German and English (language detected per chunk), and SHALL store each finding (entity type, location, score, detector version) in the catalog, not the detected value.

#### Scenario: German text with personal data
- **WHEN** a German support ticket chunk contains "Frau Petra Schulz, petra.schulz@example.de, IBAN DE89 3704 0044 0532 0130 00"
- **THEN** findings of types PERSON, EMAIL_ADDRESS and IBAN_CODE are recorded for that chunk with offsets and scores, and the raw values are not stored in the findings table

#### Scenario: Chunk not yet scanned
- **WHEN** an index build includes a chunk with no completed PII scan
- **THEN** the build fails for that chunk and reports it as `pii_unscanned`

### Requirement: Sensitivity classification
The system SHALL assign each chunk a sensitivity class (`public`, `internal`, `confidential`, `personal`, `special-category`, the same tiers as `source-inventory` in change 02) from its source policy and its PII findings, using the higher of the two. Data of special categories (DSGVO Art. 9) SHALL be classified `special-category` and SHALL be blocked from agent exposure unless the source policy records an explicit legal basis approved by compliance.

#### Scenario: Internal source with personal data
- **WHEN** a chunk from an `internal` source has a PERSON finding above threshold
- **THEN** its class is `personal`

#### Scenario: Health data
- **WHEN** a chunk contains a finding of a health-data recognizer
- **THEN** its class is `special-category` and it is excluded from indexes unless an approved legal basis is recorded

### Requirement: Masking and pseudonymization per policy
The system SHALL apply the masking rule referenced by the source policy to each finding before indexing, supporting at least the operators `redact`, `mask`, `replace-with-type` and `pseudonymize` (stable keyed token per value). The original unmasked content SHALL NOT be written to agent-facing indexes.

#### Scenario: Pseudonymize customer names
- **WHEN** the policy for customer tickets is `pseudonymize` for PERSON and the same customer name appears in two tickets
- **THEN** both indexed chunks contain the same token (e.g. `<PERSON_7f3a>`) and neither contains the name

#### Scenario: Redact phone numbers
- **WHEN** the policy is `redact` for PHONE_NUMBER
- **THEN** the indexed chunk contains no digits of the phone number

### Requirement: Pseudonym key separation
Pseudonymized data SHALL be treated as personal data. The system SHALL store the pseudonymization key and any token-to-value mapping in a separate schema with its own role, accessible to no agent identity, and SHALL write an audit event for every re-identification lookup, including the requester and stated purpose.

#### Scenario: Agent attempts re-identification
- **WHEN** an agent identity queries the pseudonym mapping table
- **THEN** the database denies access and an audit event with decision `deny` is written

#### Scenario: Authorized re-identification
- **WHEN** a user with the compliance role resolves a token, stating purpose "Auskunftsersuchen Art. 15"
- **THEN** the value is returned to that user only and the lookup is audited with requester and purpose

### Requirement: Detection recall gate
For every source classified as containing personal data, the system SHALL measure detector recall per entity type on a labelled sample from the pilot (default at least 200 chunks or records, labelled by a human), and SHALL block the source's governance status until recall for direct identifiers (PERSON, EMAIL_ADDRESS, PHONE_NUMBER, IBAN_CODE) reaches the configured threshold (default 0.95). Results SHALL be stored with sample ID and detector version.

#### Scenario: Recall below threshold
- **WHEN** PERSON recall on the German sample is 0.88
- **THEN** the gate fails, the source stays `blocked` with reason `pii_recall`, and the report lists the missed examples by reference

#### Scenario: Detector updated
- **WHEN** the detector model or recognizer configuration version changes
- **THEN** previous gate results are marked outdated and the gate is re-evaluated before the next index build

### Requirement: Human review of uncertain findings
The system SHALL queue findings with scores between the configured low and high thresholds for human review, and SHALL record every reviewer decision (confirm, reject) with reviewer and timestamp. A decision SHALL be reversible, and reversing it SHALL re-mask the affected chunks on the next run.

#### Scenario: Reviewer rejects false positive
- **WHEN** a reviewer rejects a PERSON finding on the product name "Hermes"
- **THEN** the finding is marked rejected, the chunk is re-indexed without that mask, and the decision is audited

#### Scenario: Decision reverted
- **WHEN** the rejection is later reverted
- **THEN** the mask is reapplied on the next `konmari govern mask` run
