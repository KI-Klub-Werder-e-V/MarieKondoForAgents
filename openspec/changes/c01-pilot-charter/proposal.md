## Why

KonMari rule 1 is "commit": a tidy-up only works when it is scoped and owned. Gartner predicts that through 2026 organizations will abandon 60% of AI projects unsupported by AI-ready data, and treats AI-readiness as specific to a use case (research/c01-pilot-charter.md §1). Without a written pilot charter, the KonMari-Kit would start pulling data before anyone agrees on the use case, who decides, what DSGVO/AI Act obligations apply, and how success is measured. The charter and its baseline metrics are the gate every later step depends on.

## What Changes

- New `konmari charter` command group: `init`, `validate`, `approve`, `amend`, `show`, `status`.
- A versioned, schema-validated `charter.yaml` per engagement holding exactly one pilot use case: purpose, in-scope and out-of-scope tasks, deployment setting, agent users, misuse cases (NIST AI RMF MAP 1.1, MAP 3.3).
- A stakeholder register with a RACI matrix (DAMA-DMBOK roles: sponsor, use-case owner, data owners, stewards/domain experts, custodians, compliance/DPO, agent users, facilitator), with exactly one Accountable per activity.
- Mandatory compliance fields: DPIA screening decision (GDPR Art. 35), purpose and personal-data categories (Art. 5(1)(b)/(c)), EU AI Act risk classification, AI-literacy briefing record (Art. 4).
- A data-scope section: candidate sources, in-scope category IDs and one approver per category, which every later step reads instead of keeping its own list.
- An engagement cadence (working sessions, steering, phase-gate reviews, post-pilot review) with a decision log, so the effort is not a one-off.
- New `konmari metrics` command group and `metrics.yaml`: success metrics with formula, owner, baseline (value, date, method), target and gate tier, defined and approved before any data is touched.
- A readiness gate (`konmari charter gate`) that later pipeline steps call: it fails unless the charter is approved and every must-have metric has a baseline or a dated measurement plan.
- Charter, metric and approval records are written to the readiness catalog (Postgres, DuckDB locally) with an audit trail; amendments create new versions and keep old ones readable.

## Capabilities

### New Capabilities
- `pilot-charter`: one-use-case charter, stakeholder RACI, compliance screening fields, engagement cadence and decision log, human approval and versioning, and the readiness gate for downstream steps.
- `success-metrics`: metric definitions, baseline-before-target rule, gate tiers, golden-question set requirements, re-measurement at phase gates, and approved target changes.

### Modified Capabilities
<!-- None: no existing specs in openspec/specs/. -->

## Impact

- **Code**: new `konmari.charter` and `konmari.metrics` modules, JSON Schemas for `charter.yaml` and `metrics.yaml`, catalog tables `charter_version`, `stakeholder`, `raci_assignment`, `metric_definition`, `metric_measurement`, `approval`, `decision_log`.
- **Dependencies**: `jsonschema`, `PyYAML` (both permissively licensed); no new services.
- **Downstream changes** (all consume the charter gate and the charter ID):
  - `c02-category-inventory` (source-inventory, category-map): inventories only sources needed by the chartered use case; data owners come from the stakeholder register.
  - `c03-joy-test-scorecard` (readiness-scorecard): readiness metrics defined here are computed there.
  - `c04-discard-pass` (dedup-detection, retention-decisions): retention approvals go to the data owners named in the RACI.
  - `c05-standardize-source-of-truth` (normalization, source-of-truth-policy): conflict-resolution authority comes from the RACI.
  - `c06-structure-and-enrich` (document-chunking, semantic-metadata, business-glossary): stewards from the register own glossary terms.
  - `c07-governance` (access-control, sensitive-data-masking, audit-logging): reuses the DPIA screening and personal-data categories; the audit log format is shared.
  - `c08-connect-agent-interfaces` (data-products, mcp-exposure): exposure is limited to the charter's deployment setting and agent users.
  - `c09-evaluate-and-monitor` (agent-evaluation, freshness-monitoring): runs the golden-question set and reports against the baselines and targets defined here.
- **Upstream**: none; this is the first change in the sequence.
