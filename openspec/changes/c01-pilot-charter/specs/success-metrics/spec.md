## ADDED Requirements

### Requirement: Complete metric definitions
The system SHALL store success metrics in `metrics.yaml`, linked to one charter, and `konmari metrics validate` MUST reject any metric missing: a unique ID, name, family (`answer_quality`, `abstention`, `data_readiness`, `business_outcome`, `governance`), unit, formula, data source, measurement method, exactly one owner from the stakeholder register, target value with comparison direction (`>=` or `<=`), gate tier (`must_have`, `target`, `stretch`) and measurement cadence.

#### Scenario: Metric without owner rejected
- **WHEN** a metric has no owner or an owner not present in the stakeholder register
- **THEN** `konmari metrics validate` exits non-zero and names the metric and the problem

#### Scenario: Valid metric set
- **WHEN** every metric has all required fields
- **THEN** validation passes and the metrics are written to the catalog with the charter ID

### Requirement: Minimum metric coverage
A metric set MUST contain at least one `must_have` metric in each of the families `answer_quality`, `abstention` and `business_outcome`, and at least one metric in `data_readiness` and in `governance`. The governance family MUST include a metric for the share of discard and retention decisions that were human-approved, with a target of 100%.

#### Scenario: Missing business outcome
- **WHEN** the metric set has no `must_have` metric in `business_outcome`
- **THEN** validation fails stating that technical metrics alone do not show business value

#### Scenario: Human-approval metric required
- **WHEN** the governance family lacks the human-approved-decisions metric or its target is below 100%
- **THEN** validation fails

### Requirement: Baseline before target and before data work
Every `must_have` metric MUST have a recorded baseline before the charter readiness gate passes. A baseline record MUST contain value, unit, measurement date, method and the person who measured it. The system SHALL accept a baseline of `not_yet_measured` only together with a measurement plan date no later than the pilot close date; such a metric SHALL let the gate pass only for the inventory step of `c02-category-inventory`, and MUST block all later steps until a value is recorded. Baselines MUST describe the as-is process without the agent (for example current handling time, current search success).

#### Scenario: Missing baseline blocks gate
- **WHEN** a `must_have` metric has neither a value nor a measurement plan
- **THEN** `konmari charter gate` fails and lists the metric

#### Scenario: Planned baseline allows inventory only
- **WHEN** a `must_have` metric is `not_yet_measured` with a plan date and the step `c04-discard-pass` calls the gate
- **THEN** the gate fails for that step while it passes for the inventory step

#### Scenario: Baseline recorded
- **WHEN** a user runs `konmari metrics baseline <metric-id> --value 0.41 --method "50 sampled tickets"`
- **THEN** a measurement row of kind `baseline` with value, date, method and author is stored

### Requirement: Approved targets and audited target changes
The metric set SHALL be approved through `konmari metrics approve` by the stakeholder Accountable for metric approval, recording identity, timestamp and content hash. After approval, any change to a target, formula or gate tier MUST go through `konmari metrics amend` with a reason, create a new metric-set version, and require re-approval; prior versions MUST stay readable. Lowering a target after a measurement exists MUST be flagged in the output and in the audit trail as `target_lowered_after_measurement`.

#### Scenario: Target lowered after measurement
- **WHEN** a target is reduced from 0.85 to 0.70 after a gate measurement of 0.72 exists
- **THEN** the amendment is stored as a new version in `draft`, flagged `target_lowered_after_measurement`, and the old target stays visible

#### Scenario: Unapproved metric set blocks gate
- **WHEN** the metric set is in `draft`
- **THEN** `konmari charter gate` fails and names the metric set as unapproved

### Requirement: Golden question set specification
The `answer_quality` and `abstention` metrics MUST reference a golden question set whose questions come from real agent users or domain experts named in the stakeholder register. The set MUST contain at least 30 questions, at least 5 of which have the expected behaviour `abstain` (the agent should say "I don't know" or "not in scope") or `flag_conflict`; this is a floor, and `agent-evaluation` (change 09) applies a default of 50 items with at least 20 % negative items unless these metrics set a lower value. Each question MUST record its author, the expected answer or answer criteria, and the expected source where one exists. The golden set file MUST be versioned and its hash stored with every measurement. Questions MUST NOT contain personal data unless the DPIA screening covers it.

#### Scenario: Too few abstention questions
- **WHEN** a golden set has 40 questions but only 3 with expected behaviour `abstain` or `flag_conflict`
- **THEN** `konmari metrics validate` fails and states that at least 5 abstention questions are required

#### Scenario: Facilitator-only authorship flagged
- **WHEN** all golden questions are authored by persons holding only the `facilitator` role
- **THEN** validation fails stating that questions must come from agent users or domain experts

### Requirement: Re-measurement at phase gates and status report
The system SHALL record measurements of kind `gate` at each phase-gate review and of kind `final` at pilot close, each tied to the charter version, metric-set version and the KonMari step. `konmari metrics report` SHALL output, per metric, baseline, latest value, target, tier and status (`met`, `not_met`, `not_measured`), and SHALL mark the pilot as not successful when any `must_have` metric is `not_met` or `not_measured` at pilot close. The report SHALL be exportable as Markdown and JSON.

#### Scenario: Pilot close with unmet must-have
- **WHEN** at pilot close one `must_have` metric is below its `>=` target
- **THEN** `konmari metrics report` marks the pilot as not successful and lists the metric with baseline, final value and target

#### Scenario: JSON export
- **WHEN** a user runs `konmari metrics report --format json`
- **THEN** the output is valid JSON containing every metric with baseline, latest value, target, tier and status
