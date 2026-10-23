## Why

KonMari rule 4 says tidy by category, not by location. Until every source the pilot agent needs has been gathered across SharePoint/M365, CRM, file shares and warehouses, nobody can see where the same knowledge exists twice, who owns it, or how sensitive it is. Every later step (joy test, discard, standardize, govern) needs that inventory first. Rule 5 then needs a defensible order, so the low-risk categories are tidied before PII, financials and legal (research/c02-category-inventory.md §3).

## What Changes

- New `konmari inventory scan` command. Read-only connectors (SharePoint/M365 via Graph `Sites.Selected`, SQL warehouses/CRM databases, SMB/local file shares, CSV import for anything else) enumerate only the sources named in the pilot charter.
- Each source asset gets an inventory record in the catalog: owner (one named person), steward, update frequency, last modified, format, sensitivity tier, personal-data categories, an optional link to the existing GDPR Art. 30 RoPA entry, and an access-rights note. Field names follow DCAT 3 and the OpenLineage ownership facet.
- Local, sample-based sensitivity hints (Presidio with German recognizers). Only entity-type counts are stored, never matched values. Hints stay suggestions until a human confirms them.
- Incremental rescans (Graph delta, mtime, schema hash). Records that disappear get a `missing_since` date and are never hard-deleted.
- Category map: five default cross-system categories (KB & SOPs, customer records, policies & contracts, financial/operational metrics, support history), extensible per pilot. Each asset has one primary category and optional secondary ones, assigned by rule-based suggestion plus human approval.
- Category ordering: a transparent risk score plus the source brief's default order. The human-approved sequence feeds steps 03–09.
- Per-category step tracking: a step (03-09) refuses a category until the previous step is complete for it, so cleaning comes before enriching and connecting.
- Every assignment, override and approval is written to the audit trail and can be reverted. OpenLineage dataset events are emitted for each inventoried asset.

## Capabilities

### New Capabilities
- `source-inventory`: scoped, read-only discovery of pilot sources and one auditable inventory record per asset (owner, steward, update frequency, format, sensitivity, personal-data categories, RoPA link), including incremental rescans and gap reporting.
- `category-map`: cross-system grouping of inventoried assets into categories, human-approved assignments, and a risk-based, human-approved category order (rule 5).

### Modified Capabilities
<!-- none: no existing specs in openspec/specs/ -->

## Impact

- **Upstream:** `c01-pilot-charter` (`pilot-charter` supplies the allowed source list, the pilot scope and the approvers; `success-metrics` supplies the target questions used for relevance hints).
- **Downstream:** `c03-joy-test-scorecard` (`readiness-scorecard` scores each inventoried asset per category in the approved order); `c04-discard-pass` (`dedup-detection` and `retention-decisions` work category by category across systems); `c05-standardize-source-of-truth` (`source-of-truth-policy` picks the authoritative source among assets in one category); `c06-structure-and-enrich` (`semantic-metadata` reuses owner, frequency and format; `business-glossary` reuses category names); `c07-governance` (`access-control` and `sensitive-data-masking` start from the sensitivity tiers; `audit-logging` receives the inventory decisions); `c08-connect-agent-interfaces` (`data-products` are cut along categories); `c09-evaluate-and-monitor` (`freshness-monitoring` checks the declared update frequency).
- **Code:** new `konmari.inventory` package (connectors, scanner, classifier) and `konmari.categories` package; catalog tables `inventory_source`, `inventory_run`, `source_asset`, `sensitivity_hint`, `category`, `asset_category`, `category_order_version` (audit entries go to the shared `audit_event` table from `c01-pilot-charter`); CLI commands `inventory scan|import|report` and `categories suggest|assign|order|approve`.
- **Dependencies:** `msgraph-sdk`, `presidio-analyzer`, spaCy `de_core_news_lg`, `smbprotocol`, SQLAlchemy, `openlineage-python`. All are open source and run locally.
- **Compliance:** the scanner runs with least-privilege, read-only credentials; no PII values are persisted; sensitivity output is documented as input to the DSGVO RoPA/DPIA process, not a replacement for it.
