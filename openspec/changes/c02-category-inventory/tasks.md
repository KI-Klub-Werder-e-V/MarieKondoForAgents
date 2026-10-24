## 1. Catalog schema and audit

- [x] 1.1 Add migrations for `inventory_source`, `inventory_run`, `source_asset`, `sensitivity_hint`, `category`, `asset_category`, `category_order_version` (Postgres and DuckDB; reuse `audit_event` from 01); test: migrate up/down on both engines
- [x] 1.2 Use the shared `audit_event` writer from 01 (actor, timestamp, entity, previous value, new value, reason); test: every write path in later groups asserts exactly one log row
- [x] 1.3 Implement a DCAT 3 field mapping table and export helper; test: exported JSON-LD of a fixture record contains `dcterms:accrualPeriodicity`, `dcterms:modified`, `dcterms:format`, `dcat:contactPoint`, `dcterms:accessRights`

## 2. Source registration and connectors

- [x] 2.1 Implement `konmari inventory source add` with charter validation against `pilot-charter`; tests: in-charter source accepted, out-of-charter source rejected with no records created
- [x] 2.2 Implement secret-reference resolution for connector credentials; test: after a scan, grep of catalog dump and logs finds no secret value
- [x] 2.3 Implement the SharePoint connector (`msgraph-sdk`, `Sites.Selected`, explicit site/drive enumeration, delta links); tests with recorded Graph fixtures: token with `Sites.Read.All` aborts, delta `deleted` facet handled, no Search endpoint called
- [x] 2.4 Implement the SQL connector (SQLAlchemy inspector, tables/views, row-count estimate, schema hash); test against a Postgres fixture DB
- [x] 2.5 Implement the file-share connector (`smbprotocol` and local path, folder-subtree assets, mtime/size); test on a temp directory tree
- [x] 2.6 Implement CSV/YAML manual import with per-row validation and `origin=manual`; test: 10-row file with one invalid row creates 9 records and reports line 7
- [x] 2.7 Add a read-only guard test per connector that fails on any non-GET or write call against the fixtures

## 3. Inventory records, completeness, rescans

- [x] 3.1 Implement asset upsert keyed by `asset_uri` with `first_seen`/`last_seen`; test: two unchanged scans give the same record count
- [x] 3.2 Implement the owner rule (single named individual, group/shared-mailbox detection) and completeness status with missing-field list; tests: group owner → `incomplete`, all fields set → `complete`
- [x] 3.3 Implement incremental rescans and `missing`/`missing_since` with status restore on reappearance; tests for delete, reappear, and stable `source_id` across renamed sources
- [x] 3.4 Implement the completeness gate and audited waivers (`konmari inventory report --gate`, `inventory waive`); tests: non-zero exit with 2 incomplete records, pass after waiver, waiver in `audit_event`

## 4. Sensitivity hints and tiers

- [x] 4.1 Integrate Presidio Analyzer with spaCy `de_core_news_lg`, the German recognizers, a configurable threshold (default 0.5) and sample size (default 200); DE_PLZ only with address context
- [x] 4.2 Persist only entity type, count, and max score per asset; test: fixture with 12 Steuer-IDs yields count 12 and no ID string in any table or log
- [x] 4.3 Add an offline test: the sensitivity scan passes with network egress blocked (socket guard)
- [x] 4.4 Implement tier proposal (never below hints), owner/compliance confirm and override with reason; tests: health-insurance hint → `special-category` proposed, override audited
- [x] 4.5 Implement the RoPA link field and the `ropa_missing` report section; test: `personal` record without `ropa_ref` listed under RoPA gaps
- [x] 4.6 Build a labelled German PII fixture set and record precision/recall in the test output as a regression baseline

## 5. Category map

- [x] 5.1 Seed the five default categories per pilot; implement add/rename/retire with the non-empty-retire guard; tests for both scenarios
- [x] 5.2 Implement primary/secondary assignment with the single-primary constraint; test: second primary rejected
- [x] 5.3 Implement the DE/EN keyword, media-type and table-name rule engine producing `suggested` assignments with a rule ID; tests: `Vertraege` → `policies-and-contracts`, suggestion alone blocks handoff
- [x] 5.4 Implement accept/change/reject/revert with audit; test: revert to earlier assignment yields two log entries
- [x] 5.5 Implement the cross-system category view (`konmari categories show`); test: three systems grouped under one category
- [x] 5.6 Implement overlap hints (normalised title, content hash, same table/column names) without status changes; test: identical PDF in SharePoint and file share produces a hint only

## 6. Category ordering and handoff

- [x] 6.0 Implement the per-category step-status table and `konmari.categories.require_step(category, step)` that changes 03-09 call; tests for both scenarios in "Step order within each category" and for an audited waiver
- [x] 6.1 Implement the configurable risk score and the proposal output showing inputs and weights; test: output contains score, inputs, and weights per category
- [x] 6.2 Enforce the special-category-last rule and the default tie-break order; test: `support-history` with one special-category asset is placed last
- [x] 6.3 Implement approval by charter approvers, versioning, and the latest-approved read API for `readiness-scorecard`; tests: unapproved order returns `category order not approved`, change creates version 2 and keeps version 1

## 7. Reports, lineage, docs

- [x] 7.1 Implement the Markdown and CSV inventory report (status counts, completeness %, tier counts, RoPA gaps, missing assets); test: 40 records with 36 complete shows 90 %
- [x] 7.2 Emit OpenLineage dataset events with `ownership` and `schema` facets; test against an in-memory transport
- [x] 7.3 Write the non-expert guide: the `Sites.Selected` admin grant step, the read-only credential checklist, the CSV import template, and how to confirm tiers and approve the order
- [x] 7.4 End-to-end test: fixture pilot with SharePoint, SQL and file-share sources runs scan → confirm → assign → approve order, and the gate passes
