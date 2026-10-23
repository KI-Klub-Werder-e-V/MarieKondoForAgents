# Research: 03 Joy test scorecard (KonMari rules 2 + 6)

Scope: a per-asset readiness scorecard that asks "does it spark joy for agents?" across six criteria from the source brief (authoritative and current, owned, business meaning clear, retrievable as a precise chunk/record, conflicts resolved, permissions explicit), gives a keep / fix / discard verdict, and rolls up to a category-level maturity view that matches the brief's "poorly prepared vs agent-ready" table.

Research date: 2026-10-23. Numbers carry a source. Anything without one is marked **[assumption]**.

## 1. Findings

### 1.1 No canonical "AI-ready data" rubric exists, so we define ours and say so
- Published 2025–2026 scorecards disagree on dimensions, scales and weights. GetWidget uses six dimensions on a 0–4 maturity scale with "3 = enforced, tested, gated, repeatable" as the ship bar. Aparavi uses a 0–100 composite (security 35 %, quality 25 %, accessibility 20 %). Zoolatech scores eight criteria 0–3 against a specific use case. Datalere uses five pillars and three stages (Emerging / Evolving / Transforming), but its scale and thresholds sit behind a registration form. All of these are vendor templates, not standards.
- Gartner (press release, 26 Feb 2025) argues that AI readiness is **use-case specific** and is **proven through metadata** (lineage, observability, governance), and that it is **continuous** rather than a one-off. That matches KonMari rule 1 (pilot scope) and argues against a generic company-wide score.
- **Implication:** the scorecard scores assets *for the pilot use case* (from c01-pilot-charter). It also versions the rubric, so scores stay comparable between runs and teams can see when the rubric itself changed.

### 1.2 Mapping the six joy criteria to standards
| Joy criterion (brief) | Closest standard dimensions | Evidence an automated probe can collect |
|---|---|---|
| Authoritative & current | DAMA *timeliness*/*currency*; ISO/IEC 25012 *currentness*, *credibility*; GDPR Art. 5(1)(d) "accurate and, where necessary, kept up to date" | last-modified vs. expected update frequency from source-inventory; superseded flag; designated source-of-truth flag (05) |
| Owned & maintained | ISO/IEC 27001:2022 A.5.9 inventory & ownership; OpenMetadata ownership coverage KPI | named owner present and still active; steward assigned |
| Business meaning clear | ISO/IEC 25012 *understandability*; DAMA *validity*; OpenMetadata glossary terms; ODCS semantics | description present; columns/fields mapped to glossary terms (06 business-glossary) |
| Retrievable as precise chunk/record | ISO/IEC 25012 *accessibility*; FAIR F1 (unique, persistent identifier), A1; RAG retrieval metrics (hit rate@k, MRR) | stable record ID / chunk ID present; per-asset hit rate on pilot questions (09) |
| Conflicts resolved | DAMA *consistency*, *uniqueness*; ROT "redundant" | open duplicate/conflict clusters from 04 dedup-detection; conflict rule from 05 source-of-truth-policy |
| Permissions explicit | ISO/IEC 25012 *confidentiality*; GDPR Art. 5(1)(c) minimisation; Microsoft's Copilot oversharing guidance | ACL is not broad "everyone"/EEEU; sensitivity class set; lawful basis noted for personal data |

- DAMA UK's six core dimensions are accuracy, completeness, consistency, timeliness, uniqueness and validity. The DAMA UK whitepaper itself notes that "even amongst data quality professionals the key data quality dimensions are not universally agreed". That is a second reason to keep our criteria fixed to the brief and only *map* them to DAMA.
- ISO/IEC 25012 has 15 characteristics. Some are "inherent" (assessed from the data itself) and some "system dependent" (assessed from the environment where data is stored and accessed). Our criteria mix both kinds, so each criterion needs to record its evidence source.
- FAIR principles stress machine-actionability, so that systems can find and use data "with little or no human intervention". F1 (unique, persistent identifier) and R1.2 (provenance) support "retrievable" and "authoritative".

### 1.3 Verdict logic: keep / fix / discard
- The ROT taxonomy (redundant, obsolete, trivial) is the established information-governance basis for discard decisions. Practitioner guidance says: identify ROT → classify by type → choose an action such as delete, archive or update. It also warns that "trivial" content may still be subject to legal discovery or retention. So a discard verdict here is a **recommendation** that 04 retention-decisions executes. The scorecard never deletes anything.
- GDPR Art. 5(1)(c) (minimisation) and 5(1)(e) (storage limitation) provide the legal basis for "discard if irrelevant to the purpose". Art. 5(1)(d) (accuracy) provides the basis for "fix or discard if stale".
- EU AI Act Art. 10(3) requires data sets for high-risk systems to be "relevant, sufficiently representative, and to the best extent possible, free of errors and complete in view of the intended purpose". That is again purpose-bound. Where the pilot is high-risk, the scorecard evidence can support Art. 10 documentation **[assumption: most pilots (support, contract review) are not high-risk; 01 records the classification]**.
- A weighted average alone hides blockers (a perfectly described file that is shared with everyone is still not agent-ready). Rubrics like GetWidget's use an explicit ship bar. We therefore use **gating criteria**: a criterion at level 0 on "permissions explicit" or "authoritative & current" blocks "keep" regardless of the total. **[assumption: our own design choice, informed by the ship-bar pattern]**

### 1.4 Scale and maturity
- Maturity models commonly use five levels (Initial, Managed, Defined, Measured, Optimized in CMMI-style models). DCAM uses sub-capability scores from 1 (Not Initiated) to 6 (Enhanced). Aggregation from sub-capabilities to capabilities is reported as unclear in DCAM 2.2. Lesson: **define the aggregation explicitly**.
- For per-asset criteria we use a 0–3 ordinal scale (0 = absent, 1 = partial, 2 = meets, 3 = meets and is automated or monitored), similar to Zoolatech's 0–3. A small scale keeps human reviewers consistent. **[assumption: 0–3 chosen for inter-rater consistency; not measured]**
- The category-level view uses the six aspects of the brief's table (Format, Metadata, Access, Consistency, Update mechanism, Discoverability). Each is shown on a two-pole scale from "poorly prepared" to "agent-ready", plus a share-of-assets measure. This is a direct mapping, not an invented maturity model.

### 1.5 Retrievability needs real questions
- Hit rate@k (does at least one relevant item appear in the top k) and MRR (1/rank of the first relevant hit, 0 if none) are standard, simple retrieval metrics. They need a labelled set of queries mapped to relevant passage IDs. Ragas context precision and recall are LLM-judged and can blur retrieval and generation issues.
- **Implication:** the scorecard *consumes* per-asset retrieval results from 09 agent-evaluation when they exist. Before 06 and 09 exist, it uses structural proxies (stable ID, size of a single retrievable unit). It records which evidence was used.

### 1.6 Permissions: the most common agent failure in M365 estates
- Microsoft's own guidance names sharing with "Everyone except external users" (EEEU) as a main oversharing signal before a Copilot rollout. Copilot surfaces whatever the user can reach. SharePoint data access governance reports cover the last 28 days, and their data can lag by up to 48 hours.
- **Implication:** "permissions explicit" scores 0 when an asset is reachable via EEEU/Everyone/anonymous links. Probes must store the timestamp of the ACL snapshot.

## 2. Concrete thresholds (ours unless sourced)
| Item | Value | Basis |
|---|---|---|
| Criterion scale | 0–3 ordinal | [assumption]; pattern from Zoolatech 0–3 |
| Gating criteria | authoritative_current, permissions_explicit | [assumption]; ship-bar pattern (GetWidget) |
| Keep | all criteria ≥ 2 and no open conflict | [assumption] |
| Fix | any criterion < 2, no gate failed or gate fixable, asset relevant to pilot | [assumption] |
| Discard (recommend) | not relevant to pilot use case, OR superseded/duplicate with a winning copy, OR stale beyond 2× expected update interval with no owner | ROT taxonomy + GDPR Art. 5(1)(c)(e); the 2× factor is [assumption] |
| Stale | last update older than the expected update frequency recorded in source-inventory | DAMA timeliness; factor configurable |
| Broad ACL | EEEU / Everyone / anonymous link present | Microsoft SharePoint DAG guidance |
| ACL evidence age | ≤ 48 h lag noted | Microsoft DAG reports lag up to 48 h |
| Category "agent-ready" on an aspect | ≥ 80 % of in-scope assets score ≥ 2 on mapped criteria | [assumption]; configurable |

## 3. Recommended OSS tools
- **Soda Core (Apache 2.0)**: v4 (first public release 4.0.5, 28 Jan 2026) makes data contracts the default and has native freshness, schema, row-count and duplicate checks. Good for evidence probes on SQL sources.
- **Great Expectations (Apache 2.0)**: mature expectations library. Freshness needs custom expectations. An alternative to Soda if a client already uses it.
- **Open Data Contract Standard (ODCS, Bitol / Linux Foundation)**: YAML contract with quality rules and SLAs, v3.1.0 adds scheduled SLA checks. Lets an asset's "fix" target be expressed as a contract instead of a free-text note.
- **OpenMetadata (Apache 2.0)**: data contracts since 1.9 (schema, semantics such as required owner/description, quality, SLA). Tiers, glossary approval workflow, ownership-coverage KPIs. Optional sink to publish scores. Not required.
- **AIDRIN (arXiv 2406.19256)**: academic AI-data-readiness inspector (completeness, duplicates, outliers, FAIR, privacy). Useful as a reference for metric definitions. The licence and repo were not confirmed from the abstract page, so check them before adopting it.
- **Ragas / plain hit-rate & MRR**: per-asset retrievability evidence, owned by 09.

Recommendation: implement the scorecard natively in the `konmari` CLI against the Postgres catalog. Use Soda Core checks and ODCS contracts as *optional evidence providers*, without making them hard dependencies. This keeps the project vendor-neutral and runnable by non-experts.

## 4. Pitfalls
- **Score theatre:** a single composite number invites gaming and hides blockers. Use gates and always show per-criterion scores.
- **Generic readiness:** scoring against "all of AI" instead of the pilot (Gartner: readiness is use-case specific).
- **Silent auto-discard:** "trivial" may be legally retained. The verdict is a proposal; only a named human approves it, and 04 executes it.
- **Unstable rubric:** changing weights mid-engagement breaks comparisons. Version the rubric and store the version with each score.
- **Stale evidence:** ACL reports lag (up to 48 h for M365 DAG). Store evidence timestamps and expire them.
- **Rater drift:** humans overriding probes without reasons. Require a free-text justification and log every override.
- **Personal data in evidence:** evidence snippets can leak PII into the catalog. Store references and hashes, not content (DSGVO minimisation).
- **Aggregation ambiguity** (as reported for DCAM 2.2): document how asset scores become category maturity.

## 5. Sources
- Source brief: research/00-quelle.md
- Gartner, "Lack of AI-Ready Data Puts AI Projects at Risk", 26 Feb 2025: https://www.gartner.com/en/newsroom/press-releases/2025-02-26-lack-of-ai-ready-data-puts-ai-projects-at-risk (the page returned 403 to our fetch; claims taken from search excerpts)
- Gartner IT Symposium Barcelona 2024 highlights: https://www.gartner.com/en/newsroom/press-releases/2024-11-04-gartner-it-symposium-xpo-2024-barcelona-day-1-highlights
- GetWidget, How to Run an AI Readiness Assessment: https://www.getwidget.dev/blog/how-to-run-an-ai-readiness-assessment/
- Aparavi AI readiness methodology: https://aparavi.com/resources/ai-readiness-methodology
- Datalere Agentic AI Readiness Scorecard: https://datalere.com/articles/agentic-ai-readiness-scorecard
- AIDRIN, arXiv 2406.19256: https://arxiv.org/abs/2406.19256
- DAMA dimensions (secondary): https://wiki.gccollab.ca/Data_Quality ; https://www.naa.gov.au/information-management/building-interoperability/interoperability-development-phases/data-governance-and-management/data-quality
- ISO/IEC 25012 overview: https://quality.arc42.org/standards/iso-iec-25012 ; https://iso25000.com/index.php/en/iso-25000-standards/iso-25012
- DCAM / maturity comparisons: https://datacrossroads.nl/2021/04/19/dama-dmbok2-vs-dcam-2-2-maturity-assessment/ ; https://www.snowflake.com/en/fundamentals/data-governance/framework/dcam/
- NIST AI RMF Playbook (Measure): https://airc.nist.gov/airmf-resources/playbook/measure/ ; crosswalk: https://airc.nist.gov/docs/20241216-Crosswalk_NIST_AI_RMF_TTA_Guidebook3.pdf
- EU AI Act Art. 10: https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-10
- GDPR Art. 5: https://www.legislation.gov.uk/eur/2016/679/article/5 (EUR-Lex is authoritative)
- ROT: https://blogs.opentext.com/fighting-the-rot-in-your-company-with-information-governance/ ; https://cio-wiki.com/wiki/ROT_(Redundant,_Outdated,_Trivial_Information) ; https://blog.netwrix.com/2020/03/06/redundant-obsolete-trivial-data
- FAIR: https://data-guidelines.scilifelab.se/topics/fair-principles/ ; https://about.zenodo.org/principles
- Retrieval metrics: https://www.kunalganglani.com/blog/rag-evaluation-metrics-retrieval-quality ; https://blog.premai.io/rag-evaluation-metrics-frameworks-testing-2026
- Microsoft SharePoint data access governance reports: https://learn.microsoft.com/en-us/sharepoint/data-access-governance-reports ; Copilot best practices: https://learn.microsoft.com/en-us/sharepoint/sharepoint-copilot-best-practices
- Soda Core release notes: https://docs.soda.io/soda-v4/release-notes/soda-core-release-notes ; contract language: https://beta.docs.soda.io/reference/contract-language-reference
- ODCS v3.1.0: https://bitol.io/bitol-announces-odcs-v3-1-0-stronger-smarter-and-stricter/ ; https://odcs.readthedocs.io/en/latest/user/what-is-odcs/
- OpenMetadata 1.9 / data contracts: https://blog.open-metadata.org/announcing-openmetadata-1-9-68b63005bb25 ; https://docs.open-metadata.org/v1.13.x/how-to-guides/data-contracts/spec
