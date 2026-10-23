## ADDED Requirements

### Requirement: Glossary term model
The system SHALL store business glossary terms with: stable term ID, exactly one preferred label per language (at least German or English), optional alternative labels (synonyms, acronyms) and hidden labels (common misspellings), a definition, optional scope note and examples, owner, steward (reviewer), status, version, broader/related term links and source references. Preferred, alternative and hidden labels of a term MUST be pairwise disjoint, and a preferred label MUST be unique within the glossary per language.

#### Scenario: Duplicate preferred label rejected
- **WHEN** a user creates a term with preferred label "Kunde"@de while an approved term with the same label exists
- **THEN** the system rejects the creation and points to the existing term

#### Scenario: Label used twice in one term rejected
- **WHEN** a term lists "Umsatz" both as preferred and as alternative label in German
- **THEN** validation fails with a label-disjointness error

### Requirement: Pilot-driven term harvesting
The system SHALL propose candidate terms for the pilot from column names in agent-exposed tables, frequent capitalized or acronym tokens in pilot chunks, and the pilot charter's questions, and SHALL rank candidates by frequency across pilot sources. Candidates MUST be created with status `draft` and MUST NOT be exposed downstream until approved.

#### Scenario: Acronym proposed as candidate
- **WHEN** the token "MRR" appears in 40 pilot chunks and 2 column names
- **THEN** a draft candidate "MRR" is proposed with its occurrence count and example locations

### Requirement: Human approval workflow
The system SHALL enforce the term lifecycle `draft` → `in_review` → `approved` | `rejected`, and `approved` → `deprecated`. Every glossary MUST have at least one named steward, and only a steward other than the term's author MUST be able to approve or reject a term. Editing the definition or labels of an approved term MUST create a new version in `in_review` while the previous approved version stays active until the new one is approved. Every transition MUST be logged as a decision with actor, timestamp and comment.

#### Scenario: Author cannot self-approve
- **WHEN** the author of a draft term attempts `konmari glossary approve` on it
- **THEN** the system refuses and the term remains `in_review`

#### Scenario: Edit of approved term goes back to review
- **WHEN** a steward edits the definition of the approved term "Aktiver Kunde"
- **THEN** version 2 is created with status `in_review` and version 1 remains the exposed definition until version 2 is approved

#### Scenario: Glossary without steward blocked
- **WHEN** a glossary has no steward assigned
- **THEN** terms cannot leave `draft` and the system reports the missing steward

### Requirement: AI-drafted definitions are proposals only
The system MAY draft definitions or synonyms with a language model, but such text MUST be labelled `ai_drafted` with the model name, MUST enter as `draft`, and MUST require steward approval before exposure. Drafting MUST use a self-hosted model or an approved processor and MUST NOT send chunks tagged personal or special-category data.

#### Scenario: AI draft needs approval
- **WHEN** a definition for "Churn" is drafted by a model
- **THEN** the term is `draft` with flag `ai_drafted` and is not returned by downstream interfaces

### Requirement: Links to columns, metrics and chunks
The system SHALL link approved terms to columns (via column annotations), to metric definitions and to chunks where a preferred or alternative label occurs, and SHALL write these links into the `glossary_terms` metadata field of affected chunks and records. A metric definition MUST reference its term, formula or source query, grain, unit and owner.

#### Scenario: Synonym match links chunk
- **WHEN** an approved term "Kündigungsquote" has alternative label "Churn-Rate" and a chunk contains "Churn-Rate"
- **THEN** that chunk's `glossary_terms` contains the term ID

#### Scenario: Metric definition complete
- **WHEN** a steward approves metric "Monatlicher wiederkehrender Umsatz" without a grain
- **THEN** approval is refused with a missing-field error for `grain`

### Requirement: Conflict detection
The system SHALL detect and report conflicting definitions: the same label defined by different terms across categories, or columns from different sources linked to the same term with incompatible types or units. Conflicts MUST be resolved by a steward decision and MUST NOT be auto-resolved.

#### Scenario: Unit conflict reported
- **WHEN** column `umsatz` in the ERP (EUR) and column `revenue` in the CRM (kEUR) both link to term "Umsatz"
- **THEN** a conflict is listed in the glossary report and both links remain flagged until a steward decides

### Requirement: Glossary export and coverage
The system SHALL export approved terms as SKOS (Turtle) and as JSON for downstream interfaces, and SHALL report glossary coverage: share of agent-exposed columns linked to an approved term or described, number of terms per status, and open conflicts. Only `approved` and `deprecated` (flagged as such) terms MUST be included in exports.

#### Scenario: Export excludes drafts
- **WHEN** a user runs `konmari glossary export --format skos`
- **THEN** the file contains every approved term with `skos:prefLabel`, `skos:altLabel`, `skos:definition`, deprecated terms marked as deprecated, and no draft, in-review or rejected terms

#### Scenario: Coverage reported
- **WHEN** a glossary report is generated
- **THEN** it shows coverage percentage of exposed columns, counts per status and the open conflict list

### Requirement: Reversible glossary changes
The system SHALL keep all term versions and SHALL allow a steward to revert a term to a previous approved version; deprecation MUST keep the term resolvable with a pointer to its replacement term if one exists.

#### Scenario: Revert to previous version
- **WHEN** a steward runs `konmari glossary revert <term-id> --to 1`
- **THEN** version 1 becomes the exposed approved version and the revert is logged as a decision
