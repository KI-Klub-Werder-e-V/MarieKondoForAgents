## Context

This is KonMari step 6 ("test with real questions; monitor continuously", research/00-quelle.md). Earlier changes have already produced the parts it relies on: the charter and success metrics (01), an inventory that lists owners and update intervals (02), the readiness scorecard (03), source-of-truth rules (05), chunks with metadata (06), access control, masking and the audit log (07), and the MCP and data-product interfaces (08). The people running a pilot are self-hosting organisations under DSGVO, and the EU AI Act may apply to them. Every evidence claim below is in research/c09-evaluate-and-monitor.md (cited as R§n).

## Goals / Non-Goals

**Goals:**
- Show with numbers that the pilot agent stays grounded, cites current and permitted sources, and abstains when evidence is missing or conflicting.
- Block regressions in CI and keep the baseline under human control.
- Catch stale, broken or drifting data before users notice it, and send it to the person who owns it.
- Feed the results back into the scorecard, the discard pass and the choice of the next category.

**Non-Goals:**
- Large-scale benchmarking across many models, or public leaderboards.
- Vendor observability platforms as required dependencies. They can be added later through adapters.
- Online A/B testing and user-feedback widgets. These can be added after the pilot.
- A legal determination of AI Act high-risk status. The audit trail is built so it can serve as evidence if a pilot turns out to be high-risk.

## Decisions

1. **We write our own thin evaluator and use Ragas only for the grounding and context metrics.** Ragas covers faithfulness and context precision/recall, but it has no citation or abstention metric (R§1.1). Our code covers the deterministic citation checks, the citation precision/recall definitions from ALCE (R§1.2) and the abstention scoring. *Alternatives:* promptfoo as the only runner, which is YAML-first, adds a Node runtime and gives weaker access to the catalog; Langfuse experiments, which work well but bring a heavy stack (ClickHouse, Redis) for a pilot. Both stay optional adapters.
2. **Deterministic citation checks come before model-judged ones.** The check that a source exists, is in scope and is the current source-of-truth version needs no model and no calibration. Only the entailment step (does the passage support the statement?) uses NLI or an LLM. ALCE's automatic judgments matched humans only 77.6–85.1 % of the time (R§1.2), so we keep model-judged scores to the part a deterministic check cannot answer.
3. **Abstention and answer rate are gated together.** Benchmarks show abstention is unsolved and that configurations trade answerable accuracy against rejection (AbstentionBench, UAEval4RAG; R§1.3). If only abstention were gated, a system that always says "I don't know" would pass. *Alternative:* a single F1 over both rates. We rejected it because a single number hides which way the system is failing.
4. **The judge model is pinned and calibrated with kappa on a held-out set of at least 50 items, with kappa ≥ 0.6.** Sources say to use kappa rather than percent agreement, to keep calibration items separate from prompt tuning, and to watch for silent judge drift (R§1.4). The size and threshold are our own assumptions, because sources quote 100–500 labels only as a heuristic and give no fixed kappa. A pilot can raise them in its charter.
5. **Default gate thresholds are set as documented assumptions and the charter can override them.** The 0.80 grounding threshold matches the promptfoo example (R§1.1). The other values are listed in R§2 and marked as assumptions. The charter's success metrics take precedence, which keeps the "commit first" rule.
6. **The freshness tiers are 1× and 2× each source's declared update interval.** This follows the dbt warn_after/error_after and Soda tiered-check pattern (R§1.5). We key the tiers to the interval already recorded in the inventory, so no one has to configure a second set of thresholds. *Alternative:* running dbt freshness or Soda directly. That only covers SQL warehouses and misses SharePoint and file shares. Their results can still be imported.
7. **Check results are emitted as OpenLineage `dataQualityAssertions` facets** (R§1.5), so lineage and quality share one event stream and no extra schema is needed.
8. **Drift detection uses Evidently as an optional dependency and a simple built-in default.** The default is a topic-share comparison over embedded questions against a reference window. Evidently recommends model-based drift detection for embeddings (R§1.5), and we use it when it is installed. Drift results only produce a `warn` and a request to add golden items. They never block anything.
9. **Changes go through humans.** Golden items, baseline promotion, threshold overrides, incident closure, discard proposals and the choice of the next category all need a named human, are written to the audit log, and can be reverted. This keeps the KonMari rule that decisions are approved, reversible and audited.
10. **DSGVO is handled before storage.** User-derived questions go through the 07 masking step before anything is stored, carry a retention date, and alerts never include personal-data values (R§1.6, pitfall 7).

## Risks / Trade-offs

- [Faithful but stale answers pass grounding metrics] → Citations are checked against the current source-of-truth version, and freshness status is shown to the agent.
- [Golden set rots as sources change] → Items whose expected source was superseded are flagged for re-review. User-derived items expire on their retention date.
- [Judge drift after a provider update] → The judge version is pinned, metrics are marked `uncalibrated` on any change, and the gate treats uncalibrated metrics as failed.
- [Small pilot sets make metrics noisy] → Per-item results are reported, the 0.05 regression tolerance is our assumption, and charters can raise the set size.
- [Alert fatigue] → One incident per asset and check, escalation only for unacknowledged `error` incidents, and the owner is taken from the inventory.
- [Synthetic items inflate scores] → Synthetic items are capped at 50 % and labelled as such.
- [Default thresholds (2 business days, 30 days, 50 %) have no source] → They are marked as assumptions here and in R§2, and the pilot charter can override them.

## Migration Plan

This change only adds things. It adds new catalog tables and the CLI groups `konmari eval` and `konmari monitor`. To roll back, disable the scheduled monitor job and remove the CI gate step. The catalog data stays in place for audit.

## Open Questions

- Should the judge be a self-hosted model by default, for DSGVO reasons, or a hosted API with a data processing agreement (AVV)?
- Which alert channels are required at launch beyond e-mail and webhook (for example Matrix or Teams)?
- What is the default retention period for user-derived golden items: 12 months, or set by each pilot's DPO?
