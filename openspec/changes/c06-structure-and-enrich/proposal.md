## Why

After discarding (04) and standardizing (05), the pilot's data is clean but still shaped for humans: whole documents, raw tables, filenames and cryptic column names. Agents need precise, retrievable passages and records that carry their own context (source, date, owner, authority, permissions) and terms whose business meaning is defined, otherwise they cannot cite, filter, resolve conflicts or say "I don't know" (source brief step 3, joy-test questions "Business meaning clear?" and "Can an agent retrieve a precise chunk/record?"). Research shows chunking choices alone shift retrieval recall by up to 9 % and that library defaults can perform poorly (research/c06-structure-and-enrich.md §1.1).

## What Changes

- New `konmari chunk` step: structure-aware chunking of kept, standardized documents into passages (headings, lists, tables kept intact; table headers repeated on split), token-bounded by the embedding model's tokenizer, with deterministic chunk IDs and a reviewable chunking report; plain-text fallback splitter; dry-run and re-run without losing IDs of unchanged chunks.
- New `konmari enrich` step: attaches a mandatory metadata envelope to every chunk and structured record (source, source URI, type, owner, created/modified date, freshness class, authority from the source-of-truth policy, lineage, ACL tags, sensitivity, glossary links) and emits OpenLineage-compatible lineage events; completeness gate blocks hand-off when mandatory fields are missing.
- Clean table schemas: every agent-exposed table and column gets a description and/or glossary-term link; schema coverage report.
- New business glossary: SKOS-aligned terms (bilingual DE/EN labels, definition, synonyms, owner, steward, status) with a human approval workflow (draft → in review → approved/rejected → deprecated), versioned and reversible, mapped to columns, metrics and chunks; only approved terms are exposed downstream; SKOS export.
- All decisions (chunking parameters, metadata overrides, term approvals) are written to the readiness catalog as audited, reversible decisions.

## Capabilities

### New Capabilities
- `document-chunking`: structure-aware, token-bounded, deterministic chunking of pilot documents and tables into retrievable passages, with reports and safe re-runs.
- `semantic-metadata`: mandatory metadata envelope (provenance, ownership, freshness, authority, lineage, ACL tags, sensitivity) on every chunk and record, schema/column descriptions, completeness gate and lineage events.
- `business-glossary`: governed business glossary / semantic layer defining terms, synonyms and metrics, with human approval workflow and links to columns and chunks.

### Modified Capabilities
<!-- none: no existing specs in openspec/specs/ -->

## Impact

- **Upstream dependencies**: `c01-pilot-charter` (`pilot-charter` scope limits which sources are chunked; `success-metrics` informs evaluation targets), `c02-category-inventory` (`source-inventory` supplies owner, format, update frequency, sensitivity; `category-map` supplies the type/category), `c03-joy-test-scorecard` (`readiness-scorecard` consumes metadata completeness, schema coverage and glossary coverage), `c04-discard-pass` (`retention-decisions`: only kept items are chunked; `dedup-detection` canonical IDs), `c05-standardize-source-of-truth` (`normalization` provides normalized dates/IDs; `source-of-truth-policy` provides the authority rank written into metadata).
- **Downstream consumers**: `c07-governance` (`access-control` enforces the ACL tags at retrieval; `sensitive-data-masking` acts on sensitivity tags; `audit-logging` receives decision events), `c08-connect-agent-interfaces` (`data-products`, `mcp-exposure` serve chunks, metadata and approved glossary terms), `c09-evaluate-and-monitor` (`agent-evaluation` measures retrieval quality per chunking profile; `freshness-monitoring` uses freshness class and modified dates).
- **Code**: new `konmari chunk`, `konmari enrich`, `konmari glossary` CLI groups; new catalog tables (`chunk`, `chunk_metadata`, `column_annotation`, `glossary_term`, `glossary_link`; decisions go to the shared `audit_event` trail from 01); optional pgvector column for embeddings.
- **Dependencies**: Docling (MIT), Unstructured OSS (Apache 2.0, optional), LangChain text splitters (MIT), Hugging Face tokenizers/tiktoken, OpenLineage Python client (Apache 2.0), rdflib (BSD). All self-hostable; no external LLM call is required by default (DSGVO).
