# c06 interface contract (for c03, c07, c08 and c09)

What `structure-and-enrich` hands to later changes. Consumers read these catalog views and functions only; the tables behind them may change.

## Catalog views

| View | Columns | Consumer |
|---|---|---|
| `agent_chunk` | `chunk_id`, `item_id`, `heading_path` (JSON list), `kind` (`text`/`table`), `method` (`structure`/`fallback`), `content`, `tokens`, `location` (JSON), `envelope` (JSON, all envelope fields), `context_header`, `acl_tags` (JSON `{allow, deny}`), `acl_stale`, `sensitivity`, `authority`, `freshness_class`, `stale_after`, `expired`, `glossary_terms` (JSON list), `status` (`ready-for-agents`/`needs-review`) | c08 data products and MCP (serve only `status = 'ready-for-agents'`), c09 retrieval evaluation |
| `chunk_acl` | `subject_id`, `subject_kind` (`chunk`/`table`), `item_id`, `acl_tags`, `acl_stale` | c07 access-control: pre-filter before similarity search; deny every row with `acl_stale` |
| `metadata_completeness` | `category_id`, `subject_kind`, `subjects`, `complete`, `ready` | c03 readiness scorecard, c09 monitoring |
| `approved_glossary_term` | `term_id`, `glossary_id`, `version`, `deprecated`, `replaced_by`, `content` (JSON: labels, definition, owner, category, metric) | c08 (serve approved terms; mark deprecated ones), c03 `meaning_clear` |

Only active chunks appear in `agent_chunk`; superseded and rolled-back chunks never do. Tables have their envelope in `chunk_metadata` with `subject_kind = 'table'` and `subject_id = <asset_uri>`.

## Python

```python
from konmari.enrich import store as enrich
from konmari.glossary import store as glossary

enrich.record_envelope(cat, table_uri, record_modified_at)  # envelope of one exposed record
enrich.schema_view(cat, table_uri)                           # columns with description and approved term definition
enrich.lineage(cat, chunk_id)                                # source -> normalize -> chunk -> enrich steps
glossary.exposed(cat)                                        # approved and deprecated terms only
glossary.export_skos(cat, charter_id)                        # Turtle; export_json for JSON
```

## Metrics and evidence

- `standardize_metric` rows with `kind = 'metadata_completeness'` (entity = category ID) and `kind = 'schema_coverage'` (entity = table URI), one per enrichment run.
- `asset_evidence` kinds for the c03 probes: `stable_id` (after each chunking run, per chunked asset) and `glossary_coverage` (per exposed table: `described`, `mapped`, `total`, `contract_status`).
- `derived_artifact` rows (`kind = 'chunk'`) so the c04 discard pass can propagate removals.

## Decisions

All c06 decisions go to `audit_event` with `step = 'enrich'`: `chunk.run`, `chunk.profile`, `chunk.rollback`, `enrich.run`, `enrich.override`, `enrich.column`, `enrich.acl_change`, `enrich.rollback`, `glossary.*` (stewards, add, edit, submit, approve, reject, deprecate, revert, conflict.resolve). c07 audit-logging verifies them with the same hash chain.

## OpenLineage

Every `konmari enrich run` emits one `COMPLETE` run event (job `konmari/enrich`) to `$KONMARI_LINEAGE_FILE`. Each output dataset `chunk_metadata/<item or table>` carries the `ownership`, `schema` and `documentation` facets and the custom `konmari_chunkMetadata` facet; its JSON schema is `src/konmari/schemas/chunk-metadata-facet.schema.json`, and the `_schemaURL` is pinned to the release tag.

## Open alignment points

- ACL tags are normalized as `<type>:<name>` (`group:Vertrieb`, `user:ben@example.org`). c07 `access-control` owns ACL capture; the adapters in `konmari.enrich.providers` move there with c07.
- The default tokenizer is an offline approximation until the embedding model is chosen with c09.
