## ADDED Requirements

### Requirement: Golden question items
The system SHALL store golden question items in the catalog, scoped to one pilot. Each item MUST record: question text, origin (`user_log`, `domain_expert`, `synthetic`), category from the category map, expected behaviour (`answer`, `abstain`, `flag_conflict`), a reference answer when the expected behaviour is `answer`, the expected source or chunk IDs, the approving human, the approval timestamp and a version number.

#### Scenario: Item missing a required field is rejected
- **WHEN** a user imports a golden item with expected behaviour `answer` but no reference answer or no expected source IDs
- **THEN** the import fails for that item with an error naming the missing field and no item is stored

#### Scenario: Editing creates a new version
- **WHEN** an approved golden item is edited
- **THEN** the system stores a new version with a new approval requirement and keeps the previous version readable and unchanged

### Requirement: Human approval of golden items
Golden items SHALL only be used in evaluation runs after a named human (domain expert or source owner) has approved them. Synthetic items MUST be marked `synthetic` and MUST NOT exceed 50 % of the active set.

#### Scenario: Unapproved item excluded
- **WHEN** an evaluation run starts and the golden set contains a draft item
- **THEN** the draft item is not executed and the run report lists it as excluded

#### Scenario: Synthetic share exceeded
- **WHEN** activating a golden set version in which more than 50 % of items have origin `synthetic`
- **THEN** activation is refused with a message stating the current synthetic share

### Requirement: Golden set composition
An active golden set SHALL contain at least 50 approved items, of which at least 20 % MUST have expected behaviour `abstain` or `flag_conflict`. The minimums MUST be overridable only through the pilot charter's success metrics, never below the charter floor of 30 items with at least 5 negative items (`success-metrics`, change 01), and the override MUST be audited.

#### Scenario: Too few negative items
- **WHEN** a user activates a golden set with 60 items of which 6 expect `abstain` or `flag_conflict`
- **THEN** activation is refused and the message states that 12 such items are required

#### Scenario: Charter override
- **WHEN** the pilot charter sets the minimum set size to 30 and a set of 30 compliant items is activated
- **THEN** activation succeeds and an audit entry references the charter override

### Requirement: Pseudonymisation of user-derived questions
Questions with origin `user_log` SHALL be passed through the sensitive-data masking capability before they are stored. The system MUST NOT store the raw user question or the user identity, and each such item MUST carry a retention date after which it is deleted or re-approved.

#### Scenario: Personal data masked on import
- **WHEN** a user-log question containing a customer name and customer number is imported
- **THEN** the stored question contains masking placeholders instead of the name and number, and no raw copy exists in the catalog

#### Scenario: Retention expiry
- **WHEN** the retention date of a user-derived item has passed and it was not re-approved
- **THEN** the item is deleted and an audit entry records the deletion

### Requirement: Evaluation runs through the agent interface
`konmari eval run` SHALL execute every approved item of the active golden set against the agent through its MCP interface, using the access scope of a declared test principal, and MUST record per item the answer, the retrieved chunk IDs, the cited source IDs, the agent's declared behaviour and latency. Each run MUST record the golden set version, agent/config version, judge model and version, and start/end timestamps.

#### Scenario: Run recorded with versions
- **WHEN** a user runs `konmari eval run --pilot support`
- **THEN** the catalog contains one run record with golden set version, agent config version and judge model version, and one result per executed item

#### Scenario: Agent unreachable
- **WHEN** the MCP endpoint does not respond during a run
- **THEN** the run is marked `failed` with the error, partial results are kept, and no gate may pass on a failed run

### Requirement: Retrieval and grounding metrics
For every item with expected behaviour `answer`, the system SHALL compute context precision and context recall against the expected source IDs, and faithfulness as the share of answer claims supported by the retrieved context. Each metric MUST be stored per item and aggregated per run and per category.

#### Scenario: Unsupported claim lowers faithfulness
- **WHEN** an answer contains four claims of which one is not supported by the retrieved context
- **THEN** the item's faithfulness score is 0.75 and the unsupported claim is listed in the item result

#### Scenario: Expected source not retrieved
- **WHEN** the expected source ID for an item is absent from the retrieved chunks
- **THEN** the item's context recall is below 1.0 and the missing source ID is listed

### Requirement: Citation correctness
The system SHALL check every citation in an answer deterministically: the cited source MUST exist in the catalog, MUST be within the test principal's permitted scope, and MUST be the current version according to the source-of-truth policy. The system SHALL additionally compute citation precision (share of citations whose passage supports the statement) and citation recall (share of statements that carry a supporting citation).

#### Scenario: Superseded source cited
- **WHEN** an answer cites a document version that the source-of-truth policy marks as superseded
- **THEN** the citation is marked `stale_source` and the item fails the deterministic citation check

#### Scenario: Out-of-scope source cited
- **WHEN** an answer cites a source outside the test principal's permitted scope
- **THEN** the citation is marked `out_of_scope`, the item fails, and an access-control finding is written to the audit log

### Requirement: Abstention and conflict handling
For items with expected behaviour `abstain`, the system SHALL score the item as passed only when the agent explicitly states that it cannot answer from the available evidence and asserts no answer. For items with expected behaviour `flag_conflict`, the item SHALL pass only when the agent names the conflict and cites the conflicting sources or the winning source per the source-of-truth policy. The run MUST report both the abstention rate on negative items and the answer rate on answerable items.

#### Scenario: Agent guesses on an unanswerable item
- **WHEN** an item expecting `abstain` receives a substantive answer
- **THEN** the item fails with reason `hallucinated_answer`

#### Scenario: Over-abstention is visible
- **WHEN** the agent abstains on 30 % of items expecting `answer`
- **THEN** the run report shows an answer rate of 0.70 on answerable items alongside the abstention rate

### Requirement: Judge calibration
When an LLM judge is used for any metric, the system SHALL pin the judge model identifier and version per run, and SHALL compute Cohen's kappa between judge verdicts and independent human labels on a calibration subset of at least 50 items that MUST NOT be used to tune the judge prompt. Judge-based metrics MUST be marked `uncalibrated` while kappa is below 0.6 or when the judge model changed since the last calibration.

#### Scenario: Judge model changed
- **WHEN** a run uses a judge model version different from the one used in the last calibration
- **THEN** all judge-based metrics in the run are marked `uncalibrated` and the gate treats them as not passed

#### Scenario: Calibration passes
- **WHEN** humans label 60 calibration items and the judge's kappa against them is 0.72
- **THEN** the calibration record stores kappa, the confusion matrix and judge version, and judge-based metrics are marked `calibrated`

### Requirement: Regression gates
`konmari eval gate` SHALL compare a completed run against thresholds and against the approved baseline run, and SHALL exit non-zero when any gate fails. Default thresholds MUST be: faithfulness ≥ 0.80, citation precision ≥ 0.80, citation recall ≥ 0.80, deterministic citation check 100 %, abstention rate on negative items ≥ 0.90, answer rate on answerable items ≥ 0.85, and no aggregate metric lower than the baseline by more than 0.05. Thresholds defined in the pilot charter's success metrics MUST take precedence over defaults.

#### Scenario: Regression blocks release
- **WHEN** the baseline faithfulness is 0.90 and the new run's faithfulness is 0.83
- **THEN** the gate fails with exit code 1 and names the metric, baseline value, new value and allowed drop

#### Scenario: Charter threshold wins
- **WHEN** the pilot charter sets the abstention threshold to 0.95 and a run achieves 0.92
- **THEN** the gate fails on abstention although the default threshold would pass

### Requirement: Baseline approval
A run SHALL become the baseline only through explicit approval by a named human, recorded in the audit log with the previous baseline. Reverting to the previous baseline MUST be possible with one command.

#### Scenario: Baseline promotion audited
- **WHEN** a user approves run R2 as the new baseline
- **THEN** the audit log records approver, timestamp, previous baseline R1 and new baseline R2

#### Scenario: Baseline revert
- **WHEN** a user reverts the baseline
- **THEN** R1 is the active baseline again and the revert is audited

### Requirement: Scorecard and next-category feedback
After each completed run, the system SHALL write per-category aggregates (pass rate, faithfulness, citation, abstention) to the readiness scorecard and SHALL list categories whose results fail a gate as `fix` candidates. The next category for tidying MUST be proposed but only set after human confirmation.

#### Scenario: Failing category flagged
- **WHEN** a run shows category `policies-and-contracts` failing the citation gate
- **THEN** the readiness scorecard entry for `policies-and-contracts` records the run result and the category is listed as a `fix` candidate

#### Scenario: Next category needs confirmation
- **WHEN** all gates pass for the pilot's current categories
- **THEN** the system proposes the next category from the category map and does not change the active category until a human confirms
