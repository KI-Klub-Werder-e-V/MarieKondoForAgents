## Why

Steps 01–08 tidy, govern and expose the pilot's data, but nothing yet proves that the agent answers real questions correctly from that data, or notices when the data goes stale again. Without a test with real questions and continuous monitoring, the tidy state decays silently and agents return faithful-but-outdated or invented answers (see research/c09-evaluate-and-monitor.md, pitfalls 1–3).

## What Changes

- Golden question sets built from real (pseudonymised) user questions and domain experts, each item human-approved, versioned and carrying a reference answer, expected source IDs and an expected behaviour (`answer`, `abstain`, `flag_conflict`).
- An evaluation run (`konmari eval run`) that scores retrieval (context precision/recall), grounding (faithfulness), citation correctness (deterministic + entailment-based) and abstention on unanswerable or conflicting items, through the agent's real MCP interface.
- Regression gates (`konmari eval gate`) with default thresholds from the pilot charter's success metrics, comparing against an approved baseline; non-zero exit for CI.
- LLM-judge calibration against a human-labelled subset (Cohen's kappa) with a pinned judge model.
- Continuous freshness, quality and drift monitoring (`konmari monitor run`) per source and data product, with warn/error tiers derived from each source's declared update interval.
- Alerts routed to the accountable source owner, deduplicated per incident, with acknowledgement and resolution audited.
- Results written to the catalog and fed back into the readiness scorecard and the choice of the next category.

## Capabilities

### New Capabilities
- `agent-evaluation`: golden question sets, evaluation runs (retrieval, grounding, citation, abstention), judge calibration, regression gates and scorecard feedback.
- `freshness-monitoring`: scheduled freshness, quality and drift checks, owner-routed alerts, incident lifecycle and scorecard feedback.

### Modified Capabilities
- (none)

## Impact

- Upstream dependencies: `c01-pilot-charter` (`success-metrics` supply gate thresholds; `pilot-charter` scope), `c02-category-inventory` (`source-inventory` owners and update frequency; `category-map` for next-category choice), `c03-joy-test-scorecard` (`readiness-scorecard` receives results), `c05-standardize-source-of-truth` (`source-of-truth-policy` decides which cited version is current), `c06-structure-and-enrich` (`document-chunking`, `semantic-metadata` provide chunk IDs and timestamps), `c07-governance` (`access-control` for permitted-scope citation checks, `sensitive-data-masking` for pseudonymising questions, `audit-logging` for decisions), `c08-connect-agent-interfaces` (`mcp-exposure`, `data-products` are the system under test and the monitored assets).
- Downstream: closes the loop to `c03-joy-test-scorecard` and `c02-category-inventory` for the next KonMari category; `c04-discard-pass` receives candidates for sources that fail freshness repeatedly.
- Code: new `konmari eval` and `konmari monitor` CLI groups, catalog tables for golden items, runs, results, baselines, checks and incidents.
- Dependencies: Ragas (Apache-2.0) for faithfulness/context metrics; optional adapters for Langfuse, Evidently, dbt/Soda freshness results. A judge LLM endpoint (self-hostable model allowed).
