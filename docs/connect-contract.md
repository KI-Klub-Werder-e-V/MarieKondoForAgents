# c08 interface contract (for c09)

What `connect` hands to `c09-evaluate-and-monitor`. Use these functions and tables; other internals may change.

## Calling the agent interface

- Evaluation goes through the real MCP server: `konmari serve --stdio` with `KONMARI_AGENT`, `KONMARI_ON_BEHALF_OF`, `KONMARI_GROUPS`, `KONMARI_SCOPES`, or in process with `konmari.connect.mcp_app.build(ProductServer(cat, sc), identity)` and the MCP SDK `Client`.
- Tools: `<product_with_underscores>.v<major>.search` (`keywords`, filterable fields, `limit`, `cursor`) and `.get` (key field). Results: `{product, version, as_of, freshness: fresh|stale, records, next_cursor[, deprecation]}` in `structuredContent`, the same JSON after a summary line in the text block.
- Every call writes one `agent.tool_call` audit event (c07 schema) plus `client_id`, `product`, `version`, `argument_names`, `records`, `applied_policies`, `outcome`, `duration_ms`. No argument values.

## Freshness monitoring

- `refresh_run` (`product_id`, `started_at`, `ended_at`, `status` ok/failed, `cursor_from`, `cursor_to`, `inserted`, `updated`, `deleted`, `as_of`, `test_id`, `error`). A failed run keeps the previous `as_of`.
- `konmari.connect.products.as_of(cat, pid)` and `descriptor.sla(desc)` (a `timedelta` from ODCS `slaProperties` latency); `products.freshness(as_of, sla)` gives `fresh`/`stale`.
- `products.listing(cat)` is the catalog (`konmari product list --json`), including `last_refresh` and the callable versions.
- Lineage: `lineage_event` holds the OpenLineage START/COMPLETE/FAIL run events of every refresh and publish.

## Versions

`data_product_version.state`: `draft`, `approved`, `published`, `superseded`, `deprecated` (with `successor`, `sunset_at`), `sunset`, `withdrawn`. Only `published` and not yet sunset `deprecated` versions are callable (`products.callable_versions(cat)`).
