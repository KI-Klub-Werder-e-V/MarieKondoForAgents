# 01 Pilot charter: research notes

Change: `c01-pilot-charter` (KonMari rule 1 "commit", sequence step 1 "scope to one use case").
Capabilities: `pilot-charter`, `success-metrics`.
Research date: 2026-10-23. Numbers carry a source; anything else is marked **[assumption]**.

## 1. Why a charter comes first

- **Gartner (26 Feb 2025)** predicts that "through 2026, organizations will abandon 60% of AI projects unsupported by AI-ready data", and reports that 63% of organizations lack or are unsure they have the right data management practices for AI (survey of 1,203 data management leaders, July 2024). Gartner frames AI-readiness as relative to a *specific use case*, not as a global property of data. This is the core argument for scoping to one pilot use case. (Press release returned HTTP 403 to our fetcher; the figures are confirmed through the search index snippet of the primary URL.)
- **MIT NANDA, "The GenAI Divide: State of AI in Business 2025"**: press coverage reports that about 95% of enterprise GenAI pilots show no measurable P&L impact, and attributes this mainly to brittle workflows and weak contextual learning, not model quality. Methodology counts differ between write-ups, and the figure comes from a preliminary research programme. **We use it only as motivation, never as a benchmark.**
- **Google, Rules of Machine Learning**: Rule #2 is "First, design and implement metrics", and its advice is to "track as much as possible in your current system" before building anything. Rule #1 says a simple baseline gives "baseline metrics and a baseline behavior" to compare against. For us this means: measure the *as-is* process (humans, current search) before any data is touched.

## 2. Use-case scoping (NIST AI RMF 1.0, MAP function)

NIST AI 100-1 / AI RMF Playbook, MAP function:
- MAP 1.1: intended purpose, potentially beneficial uses, context-specific laws, norms and expectations, and the intended deployment setting are understood and documented.
- MAP 1.3: "The organization's mission and relevant goals for the AI technology are understood and documented."
- MAP 1.6: system requirements (e.g. respecting user privacy) are gathered from and understood by relevant AI actors.
- MAP 3.1: potential benefits are examined and documented.
- MAP 3.3: "Targeted application scope is specified and documented", based on capability, context and categorization.

What the charter must therefore hold: purpose, in-scope and out-of-scope questions/tasks, deployment setting (who uses the agent, read-only vs write), applicable legal frame, expected benefit, known misuse cases.

Pilot selection criteria (our method, **[assumption]**, derived from the source brief and MAP 3.3): one use case with (a) a named business owner, (b) a measurable as-is process, (c) a bounded set of 3–10 candidate sources (**[assumption]**), (d) low-sentiment data first (product docs, FAQs, SOPs) per KonMari rule 5, (e) no high-risk AI Act category in the first pilot unless the organization explicitly accepts the extra obligations.

## 3. Stakeholders and RACI (DAMA-DMBOK2)

DMBOK2 defines stewardship as "the formalization of accountability and responsibility for data and data assets on behalf of stakeholders" (secondary sources; we did not have the primary text). Practitioner consensus derived from DMBOK:
- **Data owner** = business leader, **Accountable** (A): sets policy, approves keep/discard decisions.
- **Data steward** = business-side, **Responsible** (R): quality, definitions, glossary.
- **Data custodian** = IT, Responsible for storage, security, backups.
- RACI rule: exactly one A per activity (standard RACI practice).

KonMari-Kit roles for a pilot (mapped to the source brief's list):

| Role | Typical person | Notes |
|---|---|---|
| Executive sponsor | department head | approves charter, budget, cadence |
| Use-case owner | business lead of the agent's process | A for success metrics |
| Data owner (per source) | business owner of SharePoint site, CRM, etc. | A for retention/discard per source |
| Data steward / domain expert | SME | R for definitions, golden questions, reference answers |
| Data custodian | IT admin | R for connectors, access |
| Compliance / DPO | Datenschutzbeauftragte(r), legal | C on every phase, A for DPIA decision |
| Agent users | support staff, analysts | C on questions, I on results; source of real questions |
| Facilitator | volunteer, consultant or internal lead running the method | R for method execution, never A on the organization's data decisions |

## 4. DSGVO / EU AI Act constraints on a charter

- **GDPR Art. 35**: DPIA needed when processing "is likely to result in a high risk"; Art. 35(3) lists mandatory triggers; Art. 35(2) requires seeking the DPO's advice where one is designated; Art. 35(7) sets minimum DPIA content (purpose, necessity/proportionality, risks, measures). Supervisory-authority lists apply; commentators say meeting two or more WP248 criteria usually makes a DPIA mandatory. → The charter MUST record a DPIA screening decision (required / not required + reason) signed off by compliance before data is touched.
- **GDPR Art. 5(1)(b) and (c)** purpose limitation and data minimisation, **Art. 25** data protection by design, **Art. 30** records of processing. → The charter states the purpose of processing and the personal-data categories in scope; anything outside is out of scope by default.
- **BDSG §38**: in Germany a DPO must be designated where at least 20 persons are regularly engaged in automated processing of personal data. → Most Mittelstand clients have a DPO to consult.
- **EU AI Act Art. 4** (AI literacy) has applied since 2 Feb 2025. The Commission FAQ recommends tailoring to target groups and keeping records of training. Secondary sources conflict on whether the 2026 Digital Omnibus softened Art. 4; check the consolidated EUR-Lex text. → The charter records an AI-literacy briefing for agent users as an onboarding item, not as a legal certification.
- **EU AI Act Art. 26** (deployers of high-risk systems): human oversight by people with competence, training and authority. → The charter records the AI Act risk classification of the use case; a high-risk classification blocks the default pilot flow until compliance signs off.

## 5. Success metrics: baseline before target

Principle (Google Rule #2; vendor guidance agrees): define criteria *before* launch and measure the as-is baseline; do not debate criteria after the data arrives.

Metric families for an agent-readiness pilot:
1. **Agent answer quality** on a golden question set (owned by `c09-evaluate-and-monitor`, defined here): Ragas offers Faithfulness, Context Precision, Context Recall, Response Relevancy, and for agents Tool Call Accuracy, Tool Call F1, Agent Goal Accuracy (docs.ragas.io). A practitioner guide suggests ≥30 golden questions including ≥5 where the correct answer is "I don't know", and ~100 for more reliable scores (secondary source). We adopt min 30, ≥5 abstention cases, as the default.
2. **Abstention correctness**: share of unanswerable questions where the agent says "I don't know" (from the source brief's ideal state).
3. **Data readiness** (owned by `c03-joy-test-scorecard`): share of in-scope sources passing the joy test; duplicate rate; share with named owner; freshness SLA met.
4. **Business outcome**: time-to-answer / handling time, deflection or self-service rate, escalation rate, CSAT. Vendor sources give support-domain ranges (deflection 20–40% in first 3–6 months; accuracy targets 80–90%; hallucination ceiling ~2–5%). These are **vendor claims, not standards**; the charter uses them only as discussion anchors, and the organization sets its own target.
5. **Governance**: zero unauthorised access events in pilot; 100% of discard decisions human-approved (**[assumption]**, our target since KonMari-Kit decisions must be auditable).

Rules for each metric definition (our method): name, family, unit, formula, data source, measurement method, owner (exactly one), baseline value + date + method, target value, gate type (must-have / target / stretch, tiered per vendor practice), measurement cadence. A target without a baseline is invalid; a baseline of "unknown" is allowed only with a plan and date to measure it, and blocks phase 02 data work until recorded **[assumption]**.

Pitfalls:
- Deflection overstates success (customer gave up = "resolved"); pair it with resolution and CSAT.
- Very low escalation can hide frustration.
- Moving targets: changing a target after baseline needs a versioned, approved change with reason.
- Golden set written by the facilitator instead of domain experts → measures the wrong thing.
- Vanity readiness ("80% of all data AI-ready") instead of use-case metrics (Gartner: readiness is use-case specific).

## 6. Engagement cadence (not a one-off)

KonMari rule 1 says "not a one-off cleanup". Our cadence (**[assumption]**, facilitator practice, no standard prescribes it): kickoff; weekly 30-min working session with use-case owner + steward; bi-weekly steering with sponsor + compliance; phase gate reviews at the end of each KonMari step (02…09); metric re-measurement at each gate and at pilot close; post-pilot quarterly review owned by the organization, so the system sustains itself. Each meeting produces a dated decision log entry.

## 7. Recommended OSS tools

| Tool | Licence | Use here |
|---|---|---|
| Ragas | Apache-2.0 | metric definitions for the golden set baseline (Faithfulness, Context Recall, Agent Goal Accuracy) |
| Open Data Contract Standard (Bitol, Linux Foundation) v3.x | Apache-2.0 | its `team`/roles and SLA blocks are a vendor-neutral format for owners per source; reuse the role vocabulary in the charter export |
| datacontract-cli | MIT | validate/lint ODCS files downstream (02/08) |
| JSON Schema + `jsonschema` (Python) | MIT | validate `charter.yaml` and `metrics.yaml` in the `konmari` CLI |
| Postgres (or DuckDB local) | PostgreSQL / MIT | readiness catalog; charter + metric + decision tables (project default stack) |
| OpenLineage | Apache-2.0 | not needed for the charter itself; the charter ID becomes a run facet for later lineage events |

## 8. Implications for the spec

- Charter is a versioned file (`charter.yaml`) plus catalog rows, created by `konmari charter init`, validated by `konmari charter validate`, and approved by named humans; approval is recorded with who/when/version hash.
- Gate: no connector in `c02-category-inventory` may run until the charter is approved and every must-have metric has a baseline or a dated measurement plan.
- Exactly one use case per charter; a second use case = a new charter.
- Every change after approval creates a new version, needs re-approval and is audited; previous versions remain readable (reversible).
- DPIA screening and AI Act classification are mandatory charter fields.

## Sources

- Gartner press release, "Lack of AI-Ready Data Puts AI Projects at Risk", 26 Feb 2025: https://www.gartner.com/en/newsroom/press-releases/2025-02-26-lack-of-ai-ready-data-puts-ai-projects-at-risk
- MIT NANDA report coverage: https://virtualizationreview.com/Articles/2025/08/19/MIT-Report-Finds-Most-AI-Business-Investments-Fail-Reveals-GenAI-Divide.aspx ; https://letsdatascience.com/news/mit-report-documents-genai-pilot-roi-gap-e0924d7d
- Google, Rules of Machine Learning: https://developers.google.com/machine-learning/guides/rules-of-ml/
- NIST AI RMF Playbook, MAP: https://airc.nist.gov/airmf-resources/playbook/map/ ; overview https://docs.modulos.ai/frameworks/nist-ai-rmf/map
- DAMA-DMBOK roles (secondary): https://soda.io/blog/data-steward-vs-data-owner ; https://open-exam-prep.com/study-guides/cdmp/data-governance-b/data-stewardship
- GDPR Art. 35 DPIA: https://nisd2.eu/en/wiki/gdpr/dpia ; https://www.aiactblog.nl/en/posts/dpia-ai-systems-when-required-guide ; GDPR text https://eur-lex.europa.eu/eli/reg/2016/679/oj
- BDSG §38: https://www.gesetze-im-internet.de/bdsg_2018/__38.html
- EDPB Opinion 28/2024 commentary: https://www.matheson.com/insights/edpb-publishes-eagerly-anticipated-opinion-on-ai-models/
- EU AI Act AI literacy FAQ (Commission): https://digital-strategy.ec.europa.eu/en/faqs/ai-literacy-questions-answers ; Art. 26 context https://data.consilium.europa.eu/doc/document/ST-12517-2024-INIT/en/pdf ; Digital Omnibus conflict https://opsiocloud.com/knowledge-base/eu-ai-act-ai-literacy-article-4/ and https://blckalpaca.at/en/knowledge-base/ai-agents/eu-ai-act-for-ai-agents/art-4-ki-kompetenz-pflicht
- Support-AI pilot KPIs (vendor): https://www.twig.so/blog/how-long-ai-support-pilot-before-evaluating-results ; https://www.twig.so/blog/kpis-measure-ai-customer-support-success
- Ragas metrics: https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/ ; golden-set sizing (secondary): https://qaskills.sh/skills/thetestingacademy/rag-evaluation-metrics
- ODCS: https://bitol.io/odcs-version-3-transforming-data-contracts-with-enhanced-flexibility-integration-and-global-collaboration/ ; https://docs.datacontract.com/docs/open-data-contract-standard.md
