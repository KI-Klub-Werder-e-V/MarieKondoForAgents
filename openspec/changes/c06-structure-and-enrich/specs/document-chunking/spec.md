## ADDED Requirements

### Requirement: Pilot-scoped chunking input
The system SHALL chunk only documents that are inside the active pilot charter scope, are `active` in the pilot corpus after the discard pass (no executed `archive`, `exclude`, `route-to-masking` or `erase` decision in change 04) and have passed normalization (change 05). It MUST refuse to chunk any other item and MUST list refused items with the reason in the run report.

#### Scenario: Discarded document is not chunked
- **WHEN** `konmari chunk run` is executed and a document has an executed `archive` or `exclude` decision
- **THEN** no chunks are created for that document and the run report lists it with reason `not-kept`

#### Scenario: Out-of-scope source is refused
- **WHEN** a document belongs to a source that is not in the active pilot charter
- **THEN** the system creates no chunks for it and reports reason `out-of-pilot-scope`

### Requirement: Structure-aware segmentation
The system SHALL split documents along their parsed structure (sections and headings, paragraphs, list items, tables, figure captions) before applying any size limit. A chunk MUST NOT span two top-level sections, and a list or a table row MUST NOT be split mid-item. Each chunk MUST record its heading path (ordered list of ancestor headings) and its source location (page number or character/element range).

#### Scenario: Section boundary respected
- **WHEN** a document has sections "1 Returns" and "2 Warranty" each shorter than the size limit
- **THEN** the system produces separate chunks for each section and no chunk contains text from both

#### Scenario: Heading path recorded
- **WHEN** a paragraph sits under "Policies > Returns > Exceptions"
- **THEN** its chunk carries heading path `["Policies", "Returns", "Exceptions"]` and the page or element range it came from

### Requirement: Table-preserving chunking
The system SHALL emit tables as dedicated table chunks that are never merged with prose. When a table exceeds the size limit it MUST be split on row boundaries and every resulting chunk MUST repeat the table header row(s) and caption. Table chunks MUST be serialized in a format that preserves row/column association (Markdown table or row-wise "column: value" text).

#### Scenario: Large table split with header repeated
- **WHEN** a 300-row price table exceeds the token limit
- **THEN** it is split into several table chunks on row boundaries, each starting with the same header row and caption, and the union of rows equals the original table

#### Scenario: Table not merged into prose
- **WHEN** a short table follows a short paragraph under the same heading
- **THEN** the table and the paragraph remain in separate chunks

### Requirement: Token-bounded size with tokenizer alignment
The system SHALL measure chunk size in tokens using the tokenizer of the configured embedding model. The default chunking profile MUST use a maximum of 400 tokens, 0 overlap for structure-aware chunks, a 15 % overlap only for the plain-text fallback, and MUST merge successive chunks under 50 tokens that share the same heading path. No chunk MUST exceed the embedding model's maximum input length. All values MUST be overridable per chunking profile and every override MUST be recorded as a decision with author and reason.

#### Scenario: Oversized section split within limit
- **WHEN** a single section has 1,100 tokens under the default profile
- **THEN** it is split into chunks of at most 400 tokens each, on paragraph or sentence boundaries

#### Scenario: Tiny fragments merged
- **WHEN** three consecutive 20-token paragraphs share the same heading path
- **THEN** they are merged into one chunk

#### Scenario: Profile override is audited
- **WHEN** a user sets `max_tokens: 600` for the category `policies-and-contracts`
- **THEN** the catalog stores a decision record with the old value, new value, user, timestamp and reason, and subsequent runs for that category use 600

### Requirement: Deterministic and stable chunk identity
The system SHALL derive each chunk ID deterministically from the source item ID, source version, heading path, ordinal position and a content hash. Re-running chunking on unchanged input with the same profile MUST produce identical chunk IDs and content. When a source changes, chunks whose content is unchanged MUST keep their IDs, and chunks that disappear MUST be marked `superseded` rather than deleted.

#### Scenario: Idempotent re-run
- **WHEN** `konmari chunk run` is executed twice on the same unchanged documents and profile
- **THEN** the second run creates zero new chunks and reports all chunks as unchanged

#### Scenario: Changed source supersedes removed chunks
- **WHEN** a paragraph is deleted from a source document and chunking is re-run
- **THEN** the chunk for that paragraph is marked `superseded` with a link to the new source version, and unaffected chunks keep their IDs

### Requirement: Reviewable dry-run and chunking report
The system SHALL provide a dry-run mode that computes chunks without writing them, and SHALL write a chunking report per run containing: documents processed, chunk count, token size distribution (min, median, p95, max), number of table chunks, chunks flagged above 800 tokens, documents that fell back to the plain-text splitter, parse errors, and the total pilot corpus token count. When the total pilot corpus is below 200,000 tokens the report MUST state that full-context use is feasible as an alternative to retrieval.

#### Scenario: Dry-run writes nothing
- **WHEN** a user runs `konmari chunk run --dry-run`
- **THEN** the report is produced and the catalog contains no new or changed chunk rows

#### Scenario: Small corpus hint
- **WHEN** the pilot corpus totals 150,000 tokens
- **THEN** the report includes a "full-context feasible" notice

### Requirement: Plain-text fallback and parse failure handling
The system SHALL use a recursive plain-text splitter only for documents without detectable structure and MUST mark such chunks with `chunking_method: fallback`. Documents that fail to parse MUST NOT be silently skipped: they MUST be listed in the report with the error and set to status `needs-review` for a human decision.

#### Scenario: Unparseable PDF surfaced
- **WHEN** a scanned PDF without text layer cannot be parsed
- **THEN** no chunks are created, the document status becomes `needs-review` and the report lists the parse error

### Requirement: Reversible chunking runs
The system SHALL record every chunking run with its profile, tool versions and input versions, and SHALL allow a user to roll back a run, restoring the previous active chunk set for the affected documents. Rollback MUST be recorded as a decision.

#### Scenario: Roll back a bad profile
- **WHEN** a user runs `konmari chunk rollback <run-id>`
- **THEN** chunks created by that run become inactive, the previous chunk set becomes active again, and a rollback decision is stored
