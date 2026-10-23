## ADDED Requirements

### Requirement: Pilot-scoped detection input
The system SHALL run discard detection only on items (files, documents, records) that belong to assets registered in the `source-inventory` for the active pilot charter, and MUST refuse to run when no approved pilot charter exists. Each run MUST record the charter ID, the inventory snapshot ID, the detector configuration, and its start and end time in the catalog.

#### Scenario: Run limited to pilot inventory
- **WHEN** `konmari discard detect` runs for a pilot whose inventoried assets contain 1,200 items while the connected SharePoint site holds 40,000 files
- **THEN** exactly the 1,200 inventoried items are fingerprinted, and the run record references the charter ID and the inventory snapshot ID

#### Scenario: No approved charter
- **WHEN** `konmari discard detect` runs and the pilot charter is missing or not approved
- **THEN** the command exits with a non-zero status and an error naming the missing charter, and no fingerprints are written

### Requirement: Exact duplicate detection
The system SHALL compute a SHA-256 fingerprint over each item's normalized text (Unicode NFC, whitespace collapsed, case preserved) and over its raw bytes. It MUST group items with identical normalized-text fingerprints into an exact-duplicate cluster. The normalization version MUST be stored with each fingerprint.

#### Scenario: Same document in two systems
- **WHEN** the same PDF exists on a file share and as a SharePoint attachment with different file names and modification dates
- **THEN** both items land in one exact-duplicate cluster with similarity 1.0 and method `exact`

#### Scenario: Whitespace-only difference
- **WHEN** two text items differ only in line breaks and repeated spaces
- **THEN** their normalized-text fingerprints are equal and they form an exact-duplicate cluster, while their raw-byte fingerprints differ

### Requirement: Lexical near-duplicate and containment detection
The system SHALL detect lexical near-duplicates with MinHash LSH over word shingles and MUST verify every LSH candidate pair by computing its exact Jaccard similarity before flagging it. It SHALL also compute containment C(A,B) = |S(A) ∩ S(B)| / |S(A)| for verified pairs. Defaults are 5-word shingles, 128 permutations, Jaccard threshold 0.8 and containment threshold 0.9. The defaults MUST be configurable per run and recorded in the run record.

#### Scenario: Near-duplicate flagged after verification
- **WHEN** two SOP versions have an exact shingle Jaccard similarity of 0.86
- **THEN** they are flagged as a near-duplicate pair with method `minhash`, the verified score 0.86, and the parameters used

#### Scenario: LSH false positive discarded
- **WHEN** LSH returns a candidate pair whose exact Jaccard similarity is 0.62 against a threshold of 0.8
- **THEN** the pair is not flagged, and the run statistics count it as a rejected candidate

#### Scenario: Document embedded in a larger one
- **WHEN** a 2-page FAQ is fully pasted into a 40-page manual so that C(FAQ, manual) is 0.97 while Jaccard is 0.08
- **THEN** the pair is flagged with method `containment`, the contained item is identified, and the score 0.97 is stored

### Requirement: Semantic near-duplicate detection
Where the semantic detector is enabled, the system SHALL embed each item with a configured multilingual embedding model and flag pairs whose cosine similarity is at or above the configured threshold. The model name, model version and threshold MUST be recorded. A semantic flag alone MUST NOT produce a proposal stronger than `archive`.

#### Scenario: Semantic pair flagged
- **WHEN** a German and an English version of the same policy have a cosine similarity of 0.95 against a threshold of 0.93
- **THEN** the pair is flagged with method `semantic`, the score, and the model identifier

#### Scenario: Semantic-only evidence cannot propose erasure
- **WHEN** two items are linked only by a semantic flag
- **THEN** no `erase` or `exclude` proposal is generated for either item from that evidence

### Requirement: Threshold calibration before first use
Before near-duplicate or semantic thresholds are used to create proposals in a pilot, the system SHALL support a calibration run. The run samples candidate pairs across the score range, a domain expert labels them as `duplicate`, `related-but-distinct` or `unrelated`, and the system reports precision and recall per candidate threshold. Proposals from a detector MUST be blocked until a calibration with at least 50 labeled pairs is stored for that detector, or a named approver records an explicit override with a reason.

#### Scenario: Calibration report
- **WHEN** a domain expert labels 60 sampled pairs for the `minhash` detector
- **THEN** the system stores the labels and reports precision and recall at thresholds 0.7, 0.8 and 0.9, and the selected threshold is recorded with the calibrator's name

#### Scenario: Uncalibrated detector blocked
- **WHEN** `konmari discard propose` runs and the `semantic` detector has neither a stored calibration nor an override
- **THEN** semantic flags are reported but no proposals are generated from them, and the output names the missing calibration

### Requirement: Duplicate clusters with canonical candidate
The system SHALL merge flagged pairs into clusters (connected components per method family) and MUST propose one canonical member per cluster. Ranking order: an explicit source-of-truth designation from the inventory, then the most recent effective date from source metadata, then the highest source version number, then the most complete item by content length. The ranking reason MUST be stored with the proposal.

#### Scenario: Canonical by source version metadata
- **WHEN** a cluster contains SharePoint versions 3.0 and 4.1 of a document and a file-share copy with a newer filesystem modification time but no version metadata
- **THEN** version 4.1 is proposed as canonical, with reason `source-version`, and the file-share copy's modification time is not used to override it

### Requirement: Superseded-version detection
The system SHALL flag an item as `superseded` when at least one of these holds: source version history shows a newer version of the same logical document; a valid-until or effective date in metadata or text has passed and a newer item in the same cluster exists; or the item's text names a replacing document that is present in the inventory. Each flag MUST record which signals fired. Filename patterns (e.g. `_v2`, `final`, dates) MAY add evidence but MUST NOT be the only signal for a superseded flag.

#### Scenario: Superseded by version history
- **WHEN** the inventory contains price list version 2024-03 and version 2025-01 of the same logical document
- **THEN** version 2024-03 is flagged `superseded`, with signal `version-history` and a reference to version 2025-01

#### Scenario: Filename alone is insufficient
- **WHEN** two items differ only in that one file is named `Angebot_final.docx` and the other `Angebot_final_v2.docx`, with no version metadata and no near-duplicate link
- **THEN** no superseded flag is created, and the pair is listed for human review with signal `filename-only`

### Requirement: Relevance flagging against the pilot charter
The system SHALL flag items as `off-scope` when their category in the `category-map` is not listed as in scope in the `pilot-charter`, or when a reviewer marks them off-scope during review. Automated relevance scoring MAY rank items for review, but MUST NOT by itself create an off-scope flag.

#### Scenario: Category outside pilot scope
- **WHEN** a pilot charter for a customer-support agent lists the categories `kb-and-sops` and `support-history`, and an item is mapped to `financial-ops-metrics`
- **THEN** the item is flagged `off-scope`, with reason `category-not-in-charter`

### Requirement: Sensitive-item routing signal
The system SHALL flag items whose inventory sensitivity is `personal`, `special-category` or `confidential`, or in which configured detectors find personal identifiers, as `sensitive-review`. The system MUST NOT mask or transform content itself; masking is delegated to the `sensitive-data-masking` capability.

#### Scenario: Personal data found in an FAQ export
- **WHEN** the detector finds e-mail addresses and phone numbers in an item whose inventory sensitivity says `public`
- **THEN** the item is flagged `sensitive-review` with the detected entity types and counts but not the values, and the inventory mismatch is reported

### Requirement: Detection evidence and report
Every flag SHALL store the method, the score(s), the parameters, the related item IDs and the run ID, and MUST NOT store excerpts of personal data. Each run SHALL produce a report with counts per flag type, cluster sizes, the duplicate rate (items in non-canonical cluster positions divided by items scanned), and the calibration status per detector. The report is written to the catalog and as a Markdown/JSON file.

#### Scenario: Run report
- **WHEN** a detection run finishes over 1,200 items with 180 items in non-canonical cluster positions
- **THEN** the report shows a duplicate rate of 15.0%, counts per method and flag type, and the calibration status of each detector

#### Scenario: Evidence has no personal excerpts
- **WHEN** a near-duplicate pair between two customer letters is flagged
- **THEN** the stored evidence contains IDs, scores and parameters, and no text excerpt from either letter
