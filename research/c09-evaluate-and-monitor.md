# 09 Evaluate and monitor: research notes

Sequence step 6 of the source brief ("Test with real questions; monitor freshness/quality continuously").
Researched 2026-10-23. Capabilities informed: `agent-evaluation`, `freshness-monitoring`.

Labels: **[S]** = stated by a cited source; **[A]** = our assumption / project default, to be confirmed per pilot.

## 1. Findings

### 1.1 Retrieval and answer evaluation (RAG metrics)

- Ragas lists these RAG metrics: Context Precision, Context Recall, Context Entities Recall, Noise
  Sensitivity, Response Relevancy, Faithfulness (plus multimodal variants and the Nvidia-style Answer
  Accuracy, Context Relevance, Response Groundedness). It lists **no citation or abstention metric**. [S: Ragas docs]
  → citation correctness and abstention must be scored by our own code.
- Faithfulness = share of answer claims supported by the retrieved context (supported / total). It
  measures grounding, not truth: a faithful answer over a stale source is still wrong. [S: Ragas summaries]
  → faithfulness must be combined with freshness of the cited sources (link to `freshness-monitoring`).
- Context recall and most context-precision variants need a reference answer or gold context. [S]
  → golden items need a reference answer AND the expected source IDs.
- Promptfoo exposes RAG assertions `context-faithfulness`, `context-recall`, `context-relevance`,
  `answer-relevance`, with threshold-based pass/fail for CI; one practitioner example uses 0.8 for
  context-faithfulness. [S: secondary walkthrough; official syntax not verified]

### 1.2 Citation correctness

- ALCE (Gao et al., EMNLP 2023) defines **citation recall** (per statement: has a citation AND the cited
  passages entail it) and **citation precision** (per citation: does the cited passage support the
  statement), scored with an NLI model. [S: arXiv 2305.14627]
- ALCE's automatic judgments agreed with humans at 85.1 % (recall) and 77.6 % (precision); NLI cannot
  detect partial support. [S: arXiv 2305.14627] → automatic citation scores need human spot checks.
- Cheapest deterministic check (no model): every cited source ID must exist in the catalog, be in the
  agent's permitted scope and be the current source-of-truth version. [A]

### 1.3 Abstention ("I don't know")

- AbstentionBench (Meta, NeurIPS 2025): 20 datasets (unknown answers, underspecification, false premises,
  subjective, outdated info); across 20 frontier LLMs, abstention is unsolved, scaling helps little,
  reasoning fine-tuning degrades abstention by 24 % on average; a careful system prompt helps but does
  not fix it. [S: arXiv 2506.09038]
- UAEval4RAG (ACL 2025): synthesizes unanswerable queries for a given knowledge base using a six-category
  taxonomy, scores "unanswered ratio" and "acceptable ratio"; no single RAG configuration was best on both
  answerable and unanswerable queries. [S: ACL 2025.acl-long.415]
  → the golden set MUST contain unanswerable and conflicting-evidence items, and both
  answer-rate-on-answerable and abstention-rate-on-unanswerable must be gated together (a system that
  always says "I don't know" must fail).

### 1.4 LLM-as-judge calibration

- Raw percent agreement misleads under class imbalance; Cohen's kappa separates judges better; read it
  with the confusion matrix. [S: arXiv 2406.12624; practitioner guides]
- Calibration set must be separate from judge-prompt tuning examples; humans label independently first;
  if humans cannot apply the rubric consistently, the judge cannot fix it. [S: practitioner guides]
- Gold-label sizes quoted: ~100–200 labels (community workflow) or 200–500 per rubric (vendor). These
  are heuristics, not standards. [S, heuristic] No source gives a fixed "good" kappa. [S]
  → our default: kappa ≥ 0.6 against a ≥ 50-item human-labelled subset for the pilot. [A]
- Pin the judge model and version; provider-side updates silently shift judge behaviour; re-calibrate on
  judge change. [S]
- Use a different model as judge than the system under test. [S: practitioner]

### 1.5 Freshness and data-quality monitoring

- dbt: `warn_after` / `error_after` (count + period) on `loaded_at_field` (or `loaded_at_query`);
  warn does not halt, error fails; command is now `dbt freshness`. [S: docs.getdbt.com]
- Soda freshness check = scan time − max(timestamp column); tiered thresholds e.g. warn > 6h,
  fail > 24h in a published blueprint. [S: docs.soda.io; Kestra blueprint]
- Great Expectations has no native freshness check; implemented via timestamp expectations. [S]
- ISO/IEC 25012 defines 15 data quality characteristics (inherent / system-dependent), incl. accuracy,
  consistency, currentness. [S: ISO catalog / arc42 summary] → use "currentness" as the named dimension.
- OpenLineage has dataset facets `dataQualityAssertions` (assertion, success, column) and
  `dataQualityMetrics`, plus a run-level Test Run facet. [S: openlineage.io] → emit check results as
  OpenLineage facets so lineage and quality share one event stream.
- Drift: Evidently (open source, Apache-2.0) offers 100+ metrics from data drift to LLM judges; for
  embedding drift its blog recommends model-based (classifier) drift detection as a default. [S]

### 1.6 Regulation and frameworks

- NIST AI 600-1 (GenAI Profile, July 2024) lists confabulation among 12 GenAI risks; its Measure (MS)
  actions apply. [S: NIST] Exact MS action texts not quoted here (verify in PDF).
- EU AI Act Art. 15 (accuracy, robustness, consistent performance over lifecycle) and Art. 72
  (post-market monitoring for high-risk systems, plan in Annex IV docs). [S: artificialintelligenceact.eu]
  Digital Omnibus provisional deal (7 May 2026) moves Annex III high-risk obligations to 2 Dec 2027;
  final OJ publication not confirmed in our search. [S: Gibson Dunn alert]
  → most pilots (internal support/knowledge agents) are not high-risk, but the monitoring log is built
  so it can serve as Art. 72 evidence if a pilot is high-risk. [A]
- DSGVO: golden questions drawn from real user logs are personal data if they contain names, customer
  numbers etc.; Art. 5(1)(c) data minimisation and 5(1)(e) storage limitation apply. [A, legal reading]
  → pseudonymise before storing; retention period per item.

## 2. Concrete thresholds (defaults, all overridable by the pilot charter's success metrics)

| Metric | Default gate | Basis |
|---|---|---|
| Faithfulness (mean) | ≥ 0.80 | [A], aligned with promptfoo example 0.8 [S] |
| Citation precision / recall | ≥ 0.80 each | [A] |
| Cited source exists + permitted + current SoT version | 100 % | [A] deterministic |
| Abstention on unanswerable / conflicting items | ≥ 0.90 | [A] |
| Answer rate on answerable items | ≥ 0.85 | [A] guards over-abstention |
| Regression vs. baseline | no metric drops > 0.05 absolute | [A] |
| Judge–human kappa | ≥ 0.6 on ≥ 50 items | [A]; sizes 100–500 quoted as heuristics [S] |
| Golden set size (pilot start) | ≥ 50 items, ≥ 20 % unanswerable/conflict | [A] |
| Freshness | warn at 1×, error at 2× declared update interval | [A], pattern from dbt warn/error [S] |

## 3. Recommended OSS tools

- **Ragas** (Apache-2.0): standard faithfulness/context metrics; Python, fits `konmari` CLI.
- **promptfoo** (MIT): YAML assertions with CI pass/fail; alternative runner for regression gates.
- **Langfuse** (MIT, self-hostable): traces, datasets, experiments, LLM-judge and code evaluators; keeps
  logs on-prem (DSGVO).
- **Evidently** (Apache-2.0): drift reports incl. text/embedding drift.
- **dbt freshness / Soda Core** (Apache-2.0): warehouse freshness checks with warn/error tiers.
- **Great Expectations** (Apache-2.0): broader data-quality expectations where already used.
- **OpenLineage** (Apache-2.0): carry quality results as `dataQualityAssertions` facets.

Choice for the kit: own thin Python evaluator (citation, abstention, deterministic checks) + Ragas for
faithfulness/context metrics; own freshness checker on the catalog (works for file shares/SharePoint
where dbt/Soda don't reach), with optional dbt/Soda import. Langfuse/Evidently optional adapters.

## 4. Pitfalls

1. Faithfulness ≠ correctness: stale but faithful answers pass. Combine with source freshness.
2. Golden sets written by the build team test what they built, not what users ask; source from real
   user questions and domain experts.
3. Over-abstention looks safe; gate answer rate too.
4. Unpinned judge model → silent metric drift; pin and re-calibrate.
5. Judge calibrated on the same items used to tune its prompt → inflated agreement.
6. Golden set leakage into prompts/few-shot examples → inflated scores.
7. Personal data in logged questions → DSGVO exposure; pseudonymise, set retention.
8. Alert fatigue: alerts without a named owner and dedup get ignored; route to the source owner from the
   inventory, deduplicate per incident.
9. Golden set rots as sources change; re-review items whose expected source was superseded.

## Sources

- Ragas available metrics: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/
- Ragas metric summaries: https://docs.nvidia.com/nemo/microservices/26.3.0/evaluator/metrics/rag.html
- Promptfoo regression evals in CI (secondary): https://medium.com/@alexrodriguesj/testing-llm-prompts-like-code-regression-evals-in-ci-cd-with-promptfoo-5242b4dcb9be
- ALCE, Gao et al. 2023: https://arxiv.org/pdf/2305.14627
- Citation generation capacity: https://arxiv.org/pdf/2410.11217
- AbstentionBench: https://arxiv.org/abs/2506.09038v1
- UAEval4RAG (ACL 2025): https://aclanthology.org/2025.acl-long.415/
- Judging the Judges: https://arxiv.org/html/2406.12624v2
- Judge calibration with kappa (practitioner): https://oneuptime.com/blog/post/2026-08-31-calibrate-llm-judge-cohens-kappa/view
- Arize human–judge alignment: https://arize.com/blog/measuring-human-llm-judge-alignment/
- NIST AI 600-1: https://airc.nist.gov/docs/NIST.AI.600-1.GenAI-Profile.ipd.pdf
- EU AI Act Art. 15: https://artificialintelligenceact.eu/article/15/
- Art. 72 commentary: https://secureprivacy.ai/blog/eu-ai-act-post-market-monitoring-guide-articles-72-and-73-2026
- Digital Omnibus deal: https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines/
- dbt freshness: https://docs.getdbt.com/reference/resource-properties/freshness
- Soda freshness: https://docs.soda.io/soda-cl/freshness.html
- Soda tiered blueprint: https://kestra.io/blueprints/soda-freshness-checks
- OpenLineage data quality assertions: https://openlineage.io/docs/spec/facets/dataset-facets/data_quality_assertions
- OpenLineage data quality metrics: https://openlineage.io/docs/spec/facets/dataset-facets/data_quality_metrics
- ISO/IEC 25012 summary: https://quality.arc42.org/standards/iso-iec-25012
- Evidently embedding drift: https://evidentlyai.com/blog/embedding-drift-detection
- Evidently: https://pypi.org/project/evidently/
- Langfuse: https://langfuse.com/resources/engineering/clarifications
