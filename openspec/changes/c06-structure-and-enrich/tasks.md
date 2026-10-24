## 1. Catalog schema and setup

- [x] 1.1 Add dependencies (docling, langchain-text-splitters, tokenizers/tiktoken, openlineage-python, rdflib; unstructured as optional extra) to the `konmari` package and verify install on Python 3.12
- [x] 1.2 Write additive, reversible catalog migrations for `chunking_profile`, `chunk_run`, `chunk`, `chunk_metadata`, `column_annotation`, `glossary`, `glossary_term`, `glossary_term_version`, `glossary_link`, `metric_definition`, reusing the shared `decision` table; test up/down on Postgres and DuckDB
- [x] 1.3 Add fixture corpus: pilot-scoped sample DOCX, PDF with a multi-page table, HTML FAQ, plain-text file, scanned PDF without text layer, and a CRM sample table

## 2. Document chunking

- [x] 2.1 Implement input selection that only accepts in-scope, `keep`, normalized items and reports refusals (`not-kept`, `out-of-pilot-scope`); unit tests for both refusal reasons
- [x] 2.2 Implement Docling-based structure-aware chunking with heading path and source location; test section boundary and heading path scenarios
- [x] 2.3 Implement table chunking (dedicated chunks, row-boundary split, repeated header and caption, row-preserving serialization); round-trip test that the union of rows equals the source table
- [x] 2.4 Implement chunking profiles (default 400 max tokens, 0 / 15 % fallback overlap, merge < 50 tokens, model max-input guard) using the embedding tokenizer; tests for split, merge and guard
- [x] 2.5 Implement audited profile overrides per category (decision with old/new value, actor, reason); test
- [x] 2.6 Implement deterministic chunk IDs and supersede-on-change; tests for idempotent re-run (zero new chunks) and superseded paragraph
- [x] 2.7 Implement plain-text fallback (`chunking_method: fallback`) and `needs-review` on parse failure; test with the scanned PDF fixture
- [x] 2.8 Implement `konmari chunk run [--dry-run]` and the chunking report (size distribution, table count, > 800 token flags, fallbacks, errors, corpus tokens, < 200k full-context notice); test that dry-run writes nothing
- [x] 2.9 Implement `konmari chunk rollback <run-id>` with active-set switch and rollback decision; test

## 3. Semantic metadata

- [x] 3.1 Implement the metadata envelope model with mandatory/optional fields and ISO 8601 validation; schema unit tests
- [x] 3.2 Implement derivation from source inventory, category map and source-of-truth policy (owner, source_system, content_type, sensitivity, authority); test authority primary/secondary scenario
- [x] 3.3 Implement manual overrides as decisions that survive re-runs; test
- [x] 3.4 Implement ACL tag sync from connectors with change events and `acl_stale` on connector failure; tests with a mocked SharePoint connector for permission removal and outage
- [x] 3.5 Implement freshness classification, `stale_after` and `expired` flag; test expired policy
- [x] 3.6 Implement the completeness gate (`ready-for-agents` vs `needs-review`, never default owner/ACL) and write per-category completeness to the catalog for the readiness scorecard; tests
- [x] 3.7 Implement column annotations and schema coverage gate for agent-exposed tables; test undocumented column and glossary-described column
- [x] 3.8 Implement deterministic context header (≤ 100 tokens) and opt-in LLM context with model label, processor allow-list and personal-data skip; tests including "no LLM call for unmasked personal data"
- [x] 3.9 Implement lineage recording and `konmari enrich lineage <chunk-id>`, plus OpenLineage COMPLETE events with `ownership`, `schema`, `documentation` and custom `konmari_chunkMetadata` facet (versioned `_schemaURL`); validate emitted events against the OpenLineage JSON schema in tests
- [x] 3.10 Implement `konmari enrich run` report and `konmari enrich rollback <run-id>`; test rollback restores previous values

## 4. Business glossary

- [x] 4.1 Implement the SKOS-aligned term model with label uniqueness and disjointness validation; tests for duplicate preferred label and same-label-twice
- [x] 4.2 Implement candidate harvesting from exposed column names, chunk acronyms/tokens and pilot questions, ranked by frequency, created as `draft`; test with fixture corpus
- [x] 4.3 Implement the lifecycle state machine with steward requirement, four-eyes approval, versioned edits of approved terms and decision logging; tests for self-approval refusal, edit-goes-to-review and missing steward
- [x] 4.4 Implement optional AI-drafted definitions flagged `ai_drafted`, entering as `draft`, with processor allow-list and personal-data exclusion; test
- [x] 4.5 Implement term links to columns, chunks (whole-word match on approved labels) and metric definitions with mandatory fields (formula/query, grain, unit, owner); tests for synonym link and missing grain
- [x] 4.6 Implement conflict detection (label clashes, type/unit mismatches) requiring steward resolution; test ERP EUR vs CRM kEUR
- [x] 4.7 Implement `konmari glossary export --format skos|json` (approved and flagged-deprecated only) and coverage report; test export excludes drafts and parses with rdflib
- [x] 4.8 Implement `konmari glossary revert` and deprecation with replacement pointer; test

## 5. Integration, docs and verification

- [x] 5.1 End-to-end test on the fixture corpus: chunk → enrich → glossary approve → export, asserting all fixtures end `ready-for-agents` or `needs-review` with reasons and all decisions present in the decision log
- [x] 5.2 Verify no network call to external LLM endpoints in the default profile (test with network blocked)
- [x] 5.3 Document the step for non-experts (how to review the chunking report, override a profile, approve terms, roll back) in DE and EN
- [ ] 5.4 Publish the interface contract consumed by 03, 07, 08 and 09 (catalog views for chunk metadata, completeness, ACL tags, approved glossary) and review it with those change owners
