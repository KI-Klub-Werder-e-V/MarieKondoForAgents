# 02 Category inventory: research notes

Scope: KonMari rule 4 ("tidy by category, not by location"), rule 5 ("follow a sensible order") and practical-sequence step 1 ("inventory the sources the pilot needs: owner, update frequency, format, sensitivity"). Capabilities: `source-inventory`, `category-map`.

Research date: 2026-10-23. Numbers below either cite a source or are marked **[assumption]**.

## 1. What a source-inventory record must hold

Several standards converge on almost the same minimum field set. We reuse their vocabulary instead of inventing our own.

| Field we need | Standard term | Source |
|---|---|---|
| Owner (accountable person) | `dcterms:publisher` / `dcat:contactPoint`; ISO 27001 A.5.9 "owner"; OpenLineage `ownership` facet `owners[{name,type}]` | DCAT 3, ISO 27001 guides, OpenLineage spec |
| Steward (operational care) | DAMA-DMBOK2 "data steward" (accountability for effective control and use of data assets) | DMBOK2 via DaLiCo glossary; if4it inventory attributes |
| Update frequency | `dcterms:accrualPeriodicity` | DCAT 3 (dcat:Dataset "frequency") |
| Last modified | `dcterms:modified` | DCAT 3 |
| Format | `dcterms:format`, `dcat:mediaType` (on distribution) | DCAT 3 |
| Category / theme | `dcat:theme` (sub-property of `dcterms:subject`) | DCAT 3 |
| Access rights | `dcterms:accessRights` | DCAT 3 |
| Sensitivity / classification | ISO 27001 A.5.12 classification by protection need | ISO 27001:2022 guides |
| Personal-data categories, data subjects, recipients, erasure periods | GDPR Art. 30(1)(c), (d), (f) | GDPR Art. 30 |
| Authoritative source system | "authoritative source system" attribute | if4it inventory attributes |

Findings:
- ISO 27001:2022 A.5.9 requires an inventory of information assets, each with a named owner who decides on classification, access and decommissioning. Practitioner guides stress that the owner is a single person, not a team. A.5.12 asks for classification by protection need.
- GDPR Art. 30(1) requires the controller's record of processing (RoPA / "Verzeichnis von Verarbeitungstätigkeiten") to list purposes, categories of data subjects and personal data, recipients, third-country transfers, erasure time limits "where possible", and TOMs. Art. 30(5) exempts organisations under 250 staff only when processing is occasional, low-risk and has no special categories, so most organisations that hold customer or HR data still need a RoPA. Our inventory should therefore **link to** existing RoPA entries, not duplicate them.
- GDPR Art. 9(1) special categories (health, religion, trade-union membership, biometrics for identification, etc.) and Art. 35(3) (large-scale processing of special categories triggers a DPIA) give an objective "highest sensitivity" tier.
- EU AI Act Art. 10(2) (high-risk systems) asks for data governance covering data origin, preparation, and "an assessment of the availability, quantity and suitability" of the data sets. A pilot agent is usually not high-risk, but the inventory produces that evidence cheaply anyway.
- NIST AI 600-1 (GenAI profile) maps GOVERN/MAP/MEASURE/MANAGE onto GenAI risks, including data privacy and leakage. Practitioners describe MAP ("know what you have") as the step everything else depends on. That is commentary, not NIST text.

## 2. Connectors and scanning

- **SharePoint/M365**: Microsoft Graph `Sites.Selected` gives an app no site access until an admin grants it per site (GA per Microsoft 365 Dev Blog). Library-level grants also need `Lists.SelectedOperations.Selected`, otherwise the request returns 403 (vendor doc). A community report says `Sites.Selected` does **not** restrict Graph Search, so the scanner must enumerate sites/drives explicitly and never use search. Incremental scans use `driveItem` delta: follow `@odata.nextLink` until it runs out, store `@odata.deltaLink`, and treat items carrying the `deleted` facet as removals. `token=latest` skips the initial crawl.
- **Stale-entry handling**: DataHub "stateful ingestion" soft-deletes entities that were seen in the previous run and are missing from the current one (`remove_stale_metadata`, keyed by a stable `pipeline_name`). Changing that name loses the state, a known pitfall. We copy the pattern: mark missing entries as `missing_since`, never hard-delete.
- **Sensitivity pre-classification**: Microsoft Presidio ships global recognizers (EMAIL_ADDRESS, IBAN_CODE, PHONE_NUMBER, PERSON, ...) and nine Germany-specific ones (DE_TAX_ID, DE_TAX_NUMBER, DE_PASSPORT, DE_ID_CARD, DE_SOCIAL_SECURITY, DE_HEALTH_INSURANCE, DE_KFZ, DE_HANDELSREGISTER, DE_PLZ). DE_PLZ has a base confidence of 0.05 and is unreliable without address context. The default NLP config is English-only, so German text needs a German spaCy/Stanza model. OpenMetadata's auto-classification also uses an NLP profiler on sample data to suggest PII tags.
- **Microsoft Purview** can mirror sensitivity labels into its Data Map. It is proprietary and paid, so at most we read existing MIP labels as an input signal.

## 3. Category map (rule 4) and ordering (rule 5)

- The source brief names five default categories: KB & SOPs, customer records, policies & contracts, financial/operational metrics, support history. ISO 27001 practice runs "inventory → classify into categories → owners → controls" (Sprinto), which matches our order.
- A category spans systems. One source item may hold several categories (a SharePoint site with contracts and SOPs), so the mapping has to be many-to-many at the asset level, with one primary category per asset.
- Ordering: no standard prescribes "low sentiment first". We derive a risk score from objective signals: highest sensitivity tier present (Art. 9 → top), personal data present, number of systems, conflict/duplication hints. The brief's rule 5 (product docs/FAQs/public policies first; PII, financials, legal last) is the default tie-breaker. The weights are **[assumption]** and must be human-approved.

## 4. Thresholds and metrics

| Metric | Value | Basis |
|---|---|---|
| Inventory coverage: sources from the pilot charter that have an inventory record | 100 % before step 03 starts | **[assumption]**, a KonMari "gather everything first" rule |
| Records with a named single owner | 100 % of in-scope records; otherwise the record is `blocked` | ISO 27001 A.5.9 (single named owner) |
| Records with update frequency, format, sensitivity | 100 % (value `unknown` allowed but counted as a gap) | DCAT fields; **[assumption]** for "unknown counts as gap" |
| Presidio minimum score for a PII hint | 0.5 default, DE_PLZ ignored without context | Presidio default `score_threshold` is 0; DE_PLZ 0.05 documented; 0.5 is **[assumption]** |
| Sample size per source for sensitivity scanning | first 200 files/rows per container | **[assumption]**, keeps the scan cheap and limits exposure |
| Expected ROT share | Veritas 2016 survey: 33 % ROT, 52 % dark data (vendor survey, 2,550 respondents) | Context only, not a target |

## 5. Recommended OSS tools

- **Microsoft Graph SDK for Python (`msgraph-sdk`)**: official, supports `Sites.Selected` and delta queries.
- **Presidio Analyzer** (MIT): PII hints with German recognizers, runs locally, so nothing leaves the host.
- **spaCy `de_core_news_lg`**: German NER for Presidio.
- **OpenLineage Python client**: emits dataset events with the `ownership` and `schema` facets, so downstream catalogs (Marquez, DataHub, OpenMetadata) can consume the inventory.
- **DataHub / OpenMetadata** (Apache-2.0): optional sinks. We copy their stale-entity and auto-classification patterns but do not depend on them (the Postgres catalog stays the source of truth).
- **SQLAlchemy inspector**: schema/table enumeration for SQL warehouses and CRM databases without vendor SDKs.
- **`smbprotocol` / local `os.scandir`**: file-share enumeration.

## 6. Pitfalls

1. Inventorying by location (one row per SharePoint site) hides cross-system redundancy. The map must group by category.
2. Team mailboxes as "owner". ISO 27001 practice requires a named individual.
3. Tenant-wide Graph permissions (`Sites.Read.All`) for a pilot violate least privilege. Use `Sites.Selected`.
4. Graph Search bypasses `Sites.Selected` scoping (community report). Never use it for enumeration.
5. Copying raw PII samples into the catalog. Store only entity-type counts and locations, never matched values.
6. Treating automatic sensitivity hints as decisions. They are suggestions until the owner or compliance confirms them.
7. Renaming the scan/pipeline identifier breaks stale detection (DataHub pitfall).
8. Duplicating the RoPA. Link to it instead.
9. Scope creep beyond the pilot charter ("make all data AI-ready" is the anti-pattern in the source brief).

## Sources

- W3C DCAT Version 3 Recommendation: https://www.w3.org/TR/vocab-dcat-3/
- GDPR Art. 30 text: https://gdpr-info.eu/art-30-gdpr/
- Irish DPC RoPA guidance: https://www.dataprotection.ie/sites/default/files/uploads/2023-04/Records%20of%20Processing%20Activities%20%28RoPA%29%20under%20Article%2030%20GDPR.pdf
- Special categories / DPIA (IDPC Malta): https://idpc.org.mt/for-organisations/data-protection-impact-assessment/
- AEPD DPIA list (Art. 35(4)): https://www.AEPD.es/documento/listas-dpia-en-35-4.pdf
- EU AI Act Art. 10 (AI Act Service Desk): https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-10
- NIST AI 600-1: https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
- ISO 27001 A.5.9 (UpGuard): https://www.upguard.com/compliance/iso-27001/5-9
- ISO 27001 A.5.12 (HighTable): https://hightable.io/iso-27001-annex-a-5-12-classification-of-information
- ISO 27001 asset management (Sprinto): https://sprinto.com/iso-27001/asset-management/
- DMBOK2 stewardship definition (DaLiCo glossary): https://www2.bui.haw-hamburg.de/tematres/vocab/?tema=2445
- Inventory ownership attributes (if4it): https://if4it.org/best-practices/data-and-information-inventory-and-attributes/ownership-and-stakeholder-attributes-for-the-data-and-information-inventory/
- ILO data inventory guide: https://www.ilo.org/sites/default/files/2025-02/guide%204%20data%20inventory_Final.pdf
- Sites.Selected GA (Microsoft 365 Dev Blog): https://devblogs.microsoft.com/microsoft365dev/controlling-app-access-on-specific-sharepoint-site-collections/
- Sites.Selected library grants (Unique docs): https://docs.unique.ai/it-operators/integrations-connectors/sharepoint-connector/sharepoint-connector-technical-manual/sharepoint-connector-permissions
- Sites.Selected and Graph Search gap (Microsoft Q&A): https://learn.microsoft.com/en-us/answers/a/12532120
- Graph delta query overview: https://learn.microsoft.com/en-in/graph/delta-query-overview
- Graph driveItem delta (PowerShell ref): https://github.com/MicrosoftDocs/microsoftgraph-docs-powershell/blob/live/microsoftgraph/graph-powershell-1.0/Microsoft.Graph.Groups/Get-MgGroupDriveItemDelta.md
- DataHub stateful ingestion: https://docs.datahub.com/docs/metadata-ingestion/docs/dev_guides/stateful
- DataHub stale lineage assets: https://support.datahub.com/hc/en-us/articles/51175512794907-Stateful-Ingestion-Not-Removing-Assets-Created-by-Lineage
- OpenMetadata auto-classification: https://docs.open-metadata.org/v1.3.x/how-to-guides/data-governance/classification/auto
- OpenLineage ownership dataset facet: https://openlineage.io/docs/next/spec/facets/dataset-facets/ownership
- OpenLineage schema dataset facet: https://openlineage.io/docs/spec/facets/dataset-facets/schema
- Presidio supported entities: https://presidio.dataprivacystack.org/supported_entities/
- Presidio FAQ (languages): https://microsoft.github.io/presidio/faq/
- Purview Data Map sensitivity labels: https://learn.microsoft.com/en-us/purview/create-sensitivity-label
- Veritas Databerg 2016: https://www.veritas.com/news-releases/2016-03-15-veritas-global-databerg-report-finds-85-percent-of-stored-data
