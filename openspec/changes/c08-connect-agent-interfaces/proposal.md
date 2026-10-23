## Why

After the pilot data has been discarded, standardized, enriched and governed (changes 04-07), agents still cannot use it reliably: it sits in tables and folders, has no contract an agent can rely on, is not discoverable, and is refreshed by hand. KonMari step 5 "Connect" turns the tidied data into described, versioned data products exposed through governed MCP tools and resources, with scheduled, change-tracked refreshes, so agents call well-defined interfaces instead of scraping raw sources (see research/c08-connect-agent-interfaces.md).

## What Changes

- Introduce **data products** for the pilot: each product has a machine-readable descriptor (ODPS v1.0 product + ODCS v3.1 contract) with owner, purpose, schema, semantics, quality rules, freshness SLA, classification and lineage, registered in the readiness catalog.
- Semantic versioning of data products with an automated breaking-change gate (`datacontract breaking`), human approval of every publish, and a deprecation window for old major versions.
- Scheduled refresh pipelines with change tracking (cursor-based incremental merge by default, log-based CDC optional) that record a run, its row-level change counts and an OpenLineage event per refresh; a refresh that fails its contract tests does not publish.
- A product **catalog** queryable via CLI and exposed to agents as an MCP resource.
- An **MCP server** (`konmari serve`) that exposes approved data products as read-only, purpose-bound tools with JSON-Schema inputs/outputs and as resources with freshness annotations; tool definitions are generated from contracts, reviewed and hash-pinned.
- Authorization per the MCP spec for HTTP transport (OAuth 2.1 resource server, RFC 9728 metadata, audience validation, no token passthrough), scope-to-product mapping, rate limiting and result caps; STDIO mode for local pilots reads credentials from the environment.
- Every tool call and every publish/deprecate decision is written to the audit log.

## Capabilities

### New Capabilities
- `data-products`: descriptor (product + contract), registration, semantic versioning and breaking-change gate, human-approved publish and deprecation, scheduled change-tracked refresh with lineage, and the product catalog.
- `mcp-exposure`: MCP server exposing published data products as governed tools and resources, tool definition generation/review/pinning, authorization per the MCP spec, input/output validation, result limits, rate limiting, error semantics, freshness signalling and audit of every call.

### Modified Capabilities
<!-- none: no existing specs in openspec/specs/ -->

## Impact

- **Upstream dependencies**: `c01-pilot-charter` (`pilot-charter`, `success-metrics`: which questions/products are in scope, freshness targets); `c02-category-inventory` (`source-inventory`, `category-map`: source systems, owners, update frequency); `c03-joy-test-scorecard` (`readiness-scorecard`: only data that passed is eligible for a product); `c05-standardize-source-of-truth` (`normalization`, `source-of-truth-policy`: conflict winner and canonical IDs); `c06-structure-and-enrich` (`document-chunking`, `semantic-metadata`, `business-glossary`: field definitions and chunk records used in contracts and descriptions); `c07-governance` (`access-control`, `sensitive-data-masking`, `audit-logging`: policies enforced at the tool boundary and the audit sink this change writes to).
- **Downstream**: `c09-evaluate-and-monitor` (`agent-evaluation` calls the MCP tools with real questions; `freshness-monitoring` consumes the refresh runs, SLAs and `as_of` values defined here).
- **Code**: new `konmari product` (`init`, `lint`, `test`, `diff`, `publish`, `deprecate`, `list`, `refresh`) and `konmari serve` CLI commands; new catalog tables for products, versions, refresh runs and tool pins.
- **Dependencies**: `mcp` (official Python SDK), `datacontract-cli`, `dlt`, `openlineage-python`; optional Debezium (CDC), Marquez (lineage UI), an OIDC authorization server such as Keycloak, OpenMetadata export.
- **Compliance**: DSGVO Art. 25 by design (purpose-bound tools, field whitelists, minimised results); EU AI Act transparency/logging supported by per-call audit records.
