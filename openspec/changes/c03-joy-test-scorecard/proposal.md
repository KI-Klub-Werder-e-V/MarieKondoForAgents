## Why

Once the pilot's sources are inventoried and grouped by category (01, 02), nobody yet knows which assets an agent can rely on and which ones would mislead it. KonMari rule 2 ("imagine the ideal") and rule 6 ("does it spark joy for agents?") need a repeatable, evidence-based test. That test decides what to keep, fix or discard before anyone spends effort cleaning or enriching data. Published AI-readiness rubrics disagree with each other and are not use-case specific (research/c03-joy-test-scorecard.md §1.1), so the kit needs its own versioned, pilot-scoped scorecard.

## What Changes

- New versioned **readiness rubric** with the six joy criteria from the source brief: authoritative & current, owned & maintained, business meaning clear, retrievable as a precise chunk/record, conflicts resolved, permissions explicit. Each criterion is scored on a 0–3 scale, and each score is mapped to DAMA / ISO/IEC 25012 / FAIR dimensions.
- **Automated evidence probes** that pre-fill scores from the catalog: freshness vs. expected update frequency, owner presence, glossary coverage, stable IDs, open duplicate clusters, broad ACLs. Every probe records its evidence source and timestamp.
- **Verdict engine** that proposes `keep`, `fix` or `discard` per asset. It uses gating criteria, so a blocker cannot be averaged away, and it lists a fix item for every criterion below the bar.
- **Human review flow**: a named reviewer accepts or overrides each proposed verdict with a justification. Decisions are reversible and every one is written to the catalog's audit trail. Nothing is ever deleted by this change.
- **Category maturity view** that rolls asset scores up per category into the six aspects of the brief's "poorly prepared vs agent-ready" table (Format, Metadata, Access, Consistency, Update mechanism, Discoverability).
- CLI commands `konmari score`, `konmari review`, `konmari scorecard report` with Markdown/CSV/JSON output.

## Capabilities

### New Capabilities
- `readiness-scorecard`: Versioned rubric, evidence probes, per-asset 0–3 criterion scores, keep/fix/discard verdict proposal with gates, human review and override with audit, and category-level maturity rollup mapped to the poorly-prepared vs agent-ready table.

### Modified Capabilities
<!-- none: no existing specs in openspec/specs/ -->

## Impact

- **Upstream dependencies**: `c01-pilot-charter` (pilot-charter supplies the use case, scope, named reviewers and the AI Act risk classification; success-metrics supplies target readiness levels). `c02-category-inventory` (source-inventory supplies assets with owner, update frequency, format and sensitivity; category-map supplies the categories for the rollup).
- **Downstream consumers**: `c04-discard-pass` (dedup-detection feeds the "conflicts resolved" evidence; retention-decisions executes the approved discard verdicts). `c05-standardize-source-of-truth` (normalization and source-of-truth-policy work through "fix" items for currency and conflicts). `c06-structure-and-enrich` (document-chunking, semantic-metadata and business-glossary close "retrievable" and "meaning" fixes). `c07-governance` (access-control and sensitive-data-masking close "permissions explicit" fixes; audit-logging is the shared trail). `c08-connect-agent-interfaces` (data-products and mcp-exposure only expose assets with an approved `keep`). `c09-evaluate-and-monitor` (agent-evaluation supplies per-asset retrieval evidence; freshness-monitoring re-triggers scoring).
- **Code**: new `konmari.scorecard` package, catalog tables `rubric_version`, `criterion_score`, `verdict`, `verdict_review`, `category_maturity`, and CLI subcommands.
- **Dependencies**: none mandatory. Soda Core and ODCS contracts are optional evidence providers.
- **DSGVO**: evidence stores references, hashes and metadata only, never content excerpts of personal data.
