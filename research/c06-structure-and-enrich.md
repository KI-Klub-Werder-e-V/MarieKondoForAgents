# 06 Structure & Enrich: research notes

Sequence step 3 of the source brief (`research/00-quelle.md`): chunk documents into retrievable
passages, give tables clean schemas, attach semantic metadata to every chunk/record, and define
business terms in a glossary / semantic layer. Research date: 2026-10-23.

## 1. Findings

### 1.1 Chunking: structure first, then size

- **Chunking strategy measurably changes retrieval quality.** Chroma's token-level evaluation
  (precision, recall, IoU over retrieved tokens, 472 queries over 5 corpora) found strategies differ
  by up to 9 % in recall, and that popular defaults can be poor: the OpenAI-assistant default
  (800-token chunks, 400 overlap) scored slightly below-average recall and the lowest scores on the
  other metrics. [Chroma]
- **Small chunks, little or no overlap, tuned heuristics.** In the same study
  RecursiveCharacterTextSplitter at 200 tokens / 0 overlap reached 88.1 % recall, IoU 6.9; at 400 / 0
  it reached 89.5 % recall. Reducing overlap improved IoU (400-token recursive: IoU 3.3 with 200
  overlap vs 3.6 with none). Exception: a weak embedding model (all-MiniLM-L6-v2) at 250 tokens needed
  overlap (recall 82.4 with 125 overlap vs 77.1 without). [Chroma]
- **Anthropic** reports chunks of "no more than a few hundred tokens" as typical, and that if the
  whole knowledge base is under ~200,000 tokens, RAG may be unnecessary (put it in the prompt).
  [Anthropic]
- **Practitioner starting ranges** of 256–512 tokens with 10–25 % overlap are widely repeated
  (e.g. Azure guidance 512 tokens / 25 % as cited second-hand), but these are heuristics, not
  benchmark optima, and optimal size is dataset-dependent (HiCBench paper). [PremAI, arXiv 2509.11552]
- **Structure-aware chunkers exist off the shelf.** Docling's HybridChunker starts from the parsed
  document hierarchy, splits only chunks over the token limit, merges undersized successive peers
  sharing the same headings (`merge_peers`, default on), repeats the table header in each chunk of a
  split table (`repeat_table_header`, default on) and uses the embedding model's own tokenizer. Its
  `contextualize()` serialises chunk + headings/captions for embedding. [Docling]
- **Unstructured** `chunk_by_title` preserves section (optionally page) boundaries, with a hard
  `max_characters` (default 500) and soft `new_after_n_chars`; tables are never merged with
  other elements and oversized tables become `TableChunk`s. [Unstructured]
- **Tables need their own tests**: header preservation, merged cells, row/column alignment, numeric
  accuracy, citation fidelity; Docling table quality depends on the PDF backend (pypdfium faster but
  worse at table structure). [Docling report / Haystack integration]

### 1.2 Contextual enrichment of chunks

- Anthropic's *Contextual Retrieval* prepends a 50–100 token, document-aware context to each chunk
  before embedding and BM25 indexing. Top-20 retrieval failure rate fell from 5.7 % to 3.7 % (‑35 %)
  with contextual embeddings, 2.9 % (‑49 %) adding contextual BM25, 1.9 % (‑67 %) adding reranking.
  Top-20 retrieved chunks beat 5 or 10. [Anthropic]
- Unstructured reproduced the idea on SEC 10-K filings (‑47 % failures with Anthropic's prompt,
  ‑84 % with their own), setup not directly comparable. [Unstructured blog]
- A deterministic variant (heading path + document title + date as a chunk header) is cheaper and
  needs no LLM; Docling's `contextualize()` does exactly this. Our assumption: start deterministic,
  add LLM context only if evaluation (change 09) shows a gap.

### 1.3 Metadata on every chunk / record

- **OpenLineage dataset facets** already standardise much of what we need: `ownership` (owners with
  URN-style identifiers and a type), `schema` (fields with name, type, description), `documentation`,
  `columnLineage`, `datasource`, `lifecycleStateChange`, `version`, `tags`, `type`, data-quality
  facets. Custom facets need a project prefix and a versioned `_schemaURL` (pin to git SHA).
  [OpenLineage facets]
- **W3C DCAT 3** + **PROV-O**: `dcat:version`, `dcat:previousVersion`, `dcterms:modified`,
  `prov:wasDerivedFrom` / `prov:wasRevisionOf` express versioning and derivation; DCAT is the EU
  open-data catalogue vocabulary (basis of DCAT-AP). [DCAT 3, W3C mailing list]
- **ISO 8000-120** defines provenance as the history/pedigree of a property value and specifies
  requirements for representing and exchanging it (without prescribing a data model). [ISO 8000-120]
- **ACL tags on chunks**: consensus is that permissions must be enforced at retrieval time,
  pre-filtering on per-chunk ACL metadata before similarity search; post-filtering is "too late"
  because content already reached the model. The main failure mode is stale ACL metadata, so ACLs
  must be synced from the source system. [Truto, tianpan.co, Cerbos; vendor blogs, treat with care]
- **EU AI Act Art. 10** (high-risk systems) requires documented data governance: origin of data,
  preparation operations (annotation, labelling, cleaning, updating, enrichment, aggregation),
  relevance and representativeness for intended purpose. Chunk lineage + enrichment logs are cheap
  evidence even when the pilot is not high-risk. [AI Act Service Desk]
- **NIST AI 600-1** (GenAI profile) lists data provenance among inventory documentation (GV-1.6-003
  as reported second-hand). [NIST AI 600-1 guides]

### 1.4 Business glossary / semantic layer

- **SKOS** (W3C) is the neutral model for a glossary: one `skos:prefLabel` per language (S14),
  `prefLabel`/`altLabel`/`hiddenLabel` pairwise disjoint (S13), preferred label strongly recommended
  unique within a scheme, `skos:definition`, `scopeNote`, `example`, `changeNote`, `historyNote`,
  `broader`/`related` (disjoint with transitive broader, S27), `exactMatch`/`closeMatch` for mappings.
  Acronyms and spelling variants are `altLabel`; misspellings are `hiddenLabel`. [SKOS Reference]
  This fits German/English bilingual glossaries (`@de`, `@en`).
- **OpenMetadata** glossary approval: once reviewers are assigned, new terms start as Draft, only
  reviewers approve/reject, editing an approved term sends it back to In Review; without reviewers
  terms auto-approve (a pitfall we must avoid). [OpenMetadata docs]
- **MetricFlow** (dbt Semantic Layer engine) became Apache 2.0 in October 2025 and is maintained as
  part of the Open Semantic Interchange (OSI); it reads semantic-model and metric YAML and generates
  SQL; its JSON metadata schema is released standalone. [dbt Labs, The New Stack, dbt docs]
- **DAMA-DMBOK** treats the business glossary and data dictionary as part of Metadata Management;
  we use it as framing only (no concrete thresholds in public sources found).

## 2. Concrete thresholds / metrics (with provenance)

| Item | Value | Source / status |
|---|---|---|
| Default max chunk size | 400 tokens of the embedding tokenizer | Assumption, within Chroma's well-performing 200–400 range |
| Default overlap | 0 for structure-aware chunks; up to 15 % only for plain-text fallback | Chroma (overlap hurts IoU); 15 % is our assumption inside the 10–25 % heuristic |
| Hard upper limit | No chunk above the embedding model's max input; flag chunks > 800 tokens | 800 = Chroma's poorly performing default, our flag threshold (assumption) |
| Min chunk size | Merge chunks < 50 tokens with same-heading peers | Docling merge_peers behaviour; 50 is our assumption |
| Context header | ≤ 100 tokens | Anthropic (50–100 tokens) |
| Skip-RAG check | Pilot corpus < 200k tokens → report "full-context feasible" | Anthropic |
| Metadata completeness | 100 % of chunks have mandatory fields (source, source_uri, type, owner, date, acl_tags, lineage) | Assumption: hard gate (an agent cannot cite or filter without them) |
| Column description coverage | 100 % of columns exposed to the agent have a description or glossary link | Assumption, aligned with the "Business meaning clear?" joy-test question |
| Glossary term status | Only `approved` terms are exposed; Draft/In Review never | OpenMetadata workflow pattern |
| Retrieval failure eval | Measured by change 09 at top-k = 20 | Anthropic used top-20 |

## 3. Recommended OSS tools

- **Docling** (MIT): layout-aware parsing of PDF/DOCX/PPTX/HTML into one document model plus
  HybridChunker; runs locally, good for DSGVO.
- **Unstructured (open-source library, Apache 2.0)**: alternative partitioner with `chunk_by_title`
  for formats Docling handles poorly (e-mail, EML/MSG).
- **LangChain text splitters** (`RecursiveCharacterTextSplitter`, MIT): fallback for plain text;
  the strategy Chroma found strong when tuned.
- **tiktoken / Hugging Face tokenizers**: count tokens with the embedding model's tokenizer.
- **OpenLineage Python client** (Apache 2.0): emit lineage events with standard facets.
- **SKOS via rdflib** (BSD): export/import the glossary as SKOS Turtle for interchange.
- **MetricFlow** (Apache 2.0): optional semantic layer for metric definitions over SQL warehouses.
- **OpenMetadata / DataHub** (Apache 2.0): optional catalogue sync target, not a hard dependency.
- **pgvector** (PostgreSQL licence): store chunk embeddings next to the Postgres catalog with
  metadata pre-filtering.

## 4. Pitfalls

1. **Fixed-size splitting that cuts tables and lists**: destroys row/header association. Keep tables
   as their own chunks; repeat headers on split.
2. **Copying library defaults** (800/400 overlap) without evaluation. [Chroma]
3. **Tokenizer mismatch** between chunker and embedding model → silent truncation. [Docling]
4. **Metadata as afterthought**: chunks without owner/date/ACL can't be filtered, cited or
   permission-checked; enrichment must happen at chunk creation, not later.
5. **Stale ACL tags** after source permission changes → leakage. Re-sync on every run; ACL
   enforcement itself is owned by change 07.
6. **Auto-approved glossary terms** (OpenMetadata without reviewers). Require a named reviewer.
7. **LLM-generated definitions/context taken as truth**: treat as proposals; human approval
   required for glossary definitions; LLM chunk context must be labelled as generated.
8. **Sending personal data to external LLM APIs** for contextual enrichment (DSGVO Art. 28/44):
   default to local models or deterministic headers; chunks tagged as personal data are never sent
   externally unless change 07 masking ran.
9. **Re-chunking without stable IDs** breaks citations and evaluation baselines. Use deterministic
   chunk IDs derived from source ID + version + structural path + content hash.

## 5. Sources

- [Chroma] Smith & Troynikov, "Evaluating Chunking Strategies for Retrieval", Chroma Research, 2024 — https://www.trychroma.com/research/evaluating-chunking
- [Anthropic] "Introducing Contextual Retrieval", 2024 — https://www.anthropic.com/news/contextual-retrieval
- [Unstructured blog] Contextual chunking in Unstructured Platform — https://www.unstructured.io/blog/contextual-chunking-in-unstructured-platform-boost-your-rag-retrieval-accuracy
- [Docling] Chunking concepts — https://docling-project.github.io/docling/concepts/chunking/
- [Docling report] Docling technical report, arXiv 2501.17887 — https://arxiv.org/abs/2501.17887v1
- [Haystack] DoclingConverter — https://docs.haystack.deepset.ai/docs/doclingconverter
- [Unstructured] Chunking (open source) — https://docs.unstructured.io/open-source/core-functionality/chunking
- [arXiv 2509.11552] HiCBench / hierarchical chunking — https://arxiv.org/html/2509.11552v2
- [PremAI] RAG chunking strategies 2026 guide (secondary) — https://blog.premai.io/rag-chunking-strategies-the-2026-benchmark-guide
- [OpenLineage facets] — https://openlineage.io/docs/spec/facets/dataset-facets/ ; https://openlineage.io/docs/guides/facets ; https://openlineage.io/spec/facets/1-0-0/OwnershipDatasetFacet.json ; https://openlineage.io/docs/spec/facets/dataset-facets/schema
- [DCAT 3] W3C Data Catalog Vocabulary — https://w3c.github.io/dxwg/dcat/ ; PROV-O adoption thread https://lists.w3.org/Archives/Public/public-dxwg-wg/2018Aug/0058.html
- [ISO 8000-120] — https://iteh.es/catalog/standards/iso/658a15d1-7d8f-4b4d-92f6-e6b44cef0756/iso-8000-120-2016
- [SKOS Reference] W3C — https://www.w3.org/TR/skos-reference/
- [OpenMetadata docs] Glossary approval workflow — https://docs.open-metadata.org/latest/how-to-guides/data-governance/glossary/approval
- [dbt Labs] Open-sourcing MetricFlow — https://www.getdbt.com/blog/dbt-labs-affirms-commitment-to-open-semantic-interchange-by-open-sourcing-metricflow ; https://docs.getdbt.com/docs/build/about-metricflow
- [The New Stack] MetricFlow independent schema — https://thenewstack.io/dbt-labs-open-sources-metricflow-an-independent-schema-for-data-interoperability/
- [AI Act Service Desk] Article 10 — https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-10
- [NIST AI 600-1] — https://doi.org/10.6028/NIST.AI.600-1 (action IDs reported via https://www.anaconda.com/blog/nist-ai-600-1-generative-ai-profile-guide)
- [Truto] Document-level RBAC in RAG — https://truto.one/blog/how-to-maintain-document-level-rbac-in-enterprise-rag-pipelines/
- [tianpan.co] Vector store access control — https://tianpan.co/blog/2026-04-17-vector-store-access-control-rag-rls
- [Cerbos] RAG integrations — https://www.cerbos.dev/ecosystem/rag
