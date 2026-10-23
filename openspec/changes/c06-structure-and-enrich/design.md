## Context

Step 3 of the KonMari sequence (source brief, `research/00-quelle.md`). Inputs: kept items (04 `retention-decisions`), normalized content and the authority ranking (05 `normalization`, `source-of-truth-policy`), owners/format/frequency/sensitivity (02 `source-inventory`, `category-map`), all inside the pilot scope (01 `pilot-charter`). Outputs feed governance (07), agent interfaces (08) and evaluation (09). Users are non-expert organizations (SMEs, associations, public bodies) running a self-hosted Python 3.12 `konmari` CLI with Postgres (DuckDB locally) as the readiness catalog. DSGVO and the EU AI Act apply, so content must not leave the premises by default and every decision must be auditable. Research basis: `research/c06-structure-and-enrich.md` (cited below as R§x).

## Goals / Non-Goals

**Goals:**
- Turn pilot documents and tables into retrievable chunks/records with stable IDs and reviewable reports.
- Attach a complete, mostly derived metadata envelope (provenance, authority, freshness, lineage, ACL, sensitivity) and gate readiness on it.
- Provide a small, human-approved, bilingual business glossary linked to columns, metrics and chunks.
- Keep every step reversible and logged as decisions in the catalog.

**Non-Goals:**
- Enforcing ACLs at query time (07 `access-control`), masking (07 `sensitive-data-masking`), the audit log store itself (07 `audit-logging`).
- Serving chunks/glossary to agents (08), embedding model choice tuning and retrieval evaluation (09).
- Enterprise-wide glossary or ontology; only pilot terms. OCR of scanned documents (flagged `needs-review`).
- Running a full catalogue product (OpenMetadata/DataHub) as a dependency.

## Decisions

### D1 Parser and chunker: Docling HybridChunker, Unstructured optional, recursive splitter as fallback
Docling (MIT, local) parses PDF/DOCX/PPTX/HTML into one hierarchy and its HybridChunker already implements split-oversized / merge-small-peers / repeat-table-header with the embedding tokenizer (R§1.1). Unstructured `chunk_by_title` is an optional partitioner for e-mail formats. Plain text uses a recursive character splitter, which Chroma found strong when tuned (R§1.1).
Alternatives: fixed-size token splitting (cuts tables, poor IoU, R§4); semantic/LLM chunkers (best recall in Chroma's study but costly, non-deterministic, and would send content to a model; revisit if 09 shows a gap).

### D2 Default profile: 400 tokens max, 0 overlap structured, 15 % overlap fallback, merge < 50
Chroma found 200–400 token recursive chunks with no overlap consistently strong and overlap reducing IoU; the 800/400 default performed poorly (R§1.1, R§2). 400 is our assumption at the top of that range to keep table rows and procedures together; 800 is the warning flag. Overlap only for the fallback where no structure carries context. Profiles are per category and every override is a decision (KonMari: deliberate, owned choices).
Alternatives: 512 / 25 % overlap (widely quoted heuristic, not benchmark-derived, R§1.1).

### D3 Deterministic chunk IDs and supersede-not-delete
ID = hash(source_id, source_version, heading_path, ordinal, content_hash). Enables idempotent re-runs, stable citations and evaluation baselines (R§4 pitfall 9), and rollback by switching the active chunk set per run.
Alternative: random UUIDs (breaks citations on every re-run).

### D4 Metadata envelope as catalog columns plus OpenLineage facets
Envelope fields live in `chunk_metadata` (typed columns for filterable fields such as `acl_tags`, `sensitivity`, `authority`, `freshness_class`, JSONB for the rest). Field semantics are mapped to OpenLineage `ownership`, `schema`, `documentation`, `columnLineage` facets and a custom `konmari_chunkMetadata` facet with versioned `_schemaURL` pinned to a git SHA (R§1.3), with DCAT/PROV-O names (`dcterms:modified`, `prov:wasDerivedFrom`) used in exports for EU catalogue compatibility. ACL tags are stored per chunk so that 07 can pre-filter before similarity search, which sources agree is the safe pattern (R§1.3); they are refreshed every run and flagged `acl_stale` on failure because stale ACL metadata is the main leakage risk.
Alternatives: metadata only at document level (cannot filter chunks from mixed-permission docs); post-retrieval filtering (content already reaches the model, R§1.3).

### D5 Completeness gate never defaults owner or ACL
A chunk without owner or ACL is un-governable; the KonMari answer is "fix or discard", so the item goes to `needs-review` rather than receiving a default (R§4 pitfall 4). 100 % mandatory-field completeness is our assumption (R§2).

### D6 Deterministic context header by default, LLM context opt-in
Anthropic's contextual retrieval cut top-20 failures by 35–67 % (R§1.2), but requires sending every chunk with its document to a model. Default is a deterministic header (title, heading path, source, date ≤ 100 tokens, as in Docling `contextualize()`), with LLM context opt-in, self-hosted or approved-processor only, never on unmasked personal data (DSGVO Art. 28/44, R§4 pitfall 8). Generated text is labelled.

### D7 Glossary: SKOS-aligned model in Postgres, OpenMetadata-style approval, SKOS export
SKOS gives a vendor-neutral, bilingual label model with clear integrity rules (one prefLabel per language, disjoint labels, R§1.4). Approval follows the OpenMetadata pattern (draft → review → approved, edit re-opens review) but closes its pitfall of auto-approval without reviewers by requiring a steward and four-eyes (R§1.4, R§4 pitfall 6). Export via rdflib to Turtle and JSON for 08. Metrics: our own minimal YAML (term, formula/query, grain, unit, owner), compatible in shape with MetricFlow semantic models so organizations with a warehouse can adopt MetricFlow (Apache 2.0 since Oct 2025, R§1.4) without rework.
Alternatives: depend on OpenMetadata/DataHub (heavy for SMEs; offered as optional sync later); MetricFlow as mandatory dependency (only useful with SQL warehouses).

### D8 Human-in-the-loop and EU AI Act evidence
Every override, approval, rollback and gate result goes to the catalog `decision` table (actor, timestamp, reason, affected IDs) and is forwarded to 07 `audit-logging`. Lineage across normalization → chunk → enrich documents preparation operations, which is what AI Act Art. 10 asks for high-risk systems (R§1.3); cheap to keep even for non-high-risk pilots.

## Risks / Trade-offs

- [Docling parse quality varies, esp. tables in PDFs] → table round-trip tests, `needs-review` on failures, choose the higher-quality PDF backend by default (R§1.1).
- [400-token default wrong for a given corpus] → per-category profiles, audited overrides; 09 compares profiles with the same eval set; rollback per run.
- [Source permissions cannot be read (e.g. file shares without ACL API)] → item blocked as `needs-review`; data owner assigns ACL tags manually as a logged decision.
- [Glossary becomes a bottleneck for stewards] → harvest ranks candidates by frequency so only the top terms for the pilot are reviewed; drafts do not block chunk readiness, only column documentation does.
- [Synonym-based chunk linking yields false positives] → linking uses whole-word matches on approved labels only; stewards can unlink, logged as decision.
- [Re-enrichment cost on large corpora] → incremental by content hash and source `modified_at`; pilot scope limits volume.

## Migration Plan

New tables only; no existing data migrated. Catalog migrations are additive and reversible (down migrations provided). Rollback of a run uses the per-run active-set switch; dropping the change means dropping the new tables.

## Open Questions

- Which embedding model/tokenizer is the default (affects token counts)? Proposed: a self-hostable multilingual model chosen together with 09.
- Should ACL tags use source-native group IDs or a normalized role vocabulary defined by 07? Needs alignment with 07 `access-control`.
- Minimum glossary size for pilot readiness (e.g. all terms used in pilot questions) — to be set with 01 `success-metrics` and 03 `readiness-scorecard`.
