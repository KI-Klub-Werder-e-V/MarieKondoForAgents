## 1. Catalog schema and dependencies

- [ ] 1.1 Add dependencies `mcp`, `datacontract-cli`, `dlt`, `openlineage-python` with pinned versions; CI installs them
- [ ] 1.2 Create catalog migrations for `data_product`, `data_product_version` (state machine incl. withdrawn), `product_approval`, `refresh_run`, `lineage_event`, `tool_pin`; migration up/down test passes on Postgres and DuckDB
- [ ] 1.3 Unit test the version state machine: only allowed transitions succeed, every transition writes an audit entry via the `c07-governance` audit sink

## 2. Data product descriptor

- [ ] 2.1 Implement `konmari product init` generating an ODPS v1.0 + ODCS v3.1 skeleton with kit `customProperties` (charter use case, scopes, masking policy refs)
- [ ] 2.2 Implement `konmari product lint`: JSON-Schema validation plus mandatory elements (owner, purpose, SLA, field definitions from business glossary, classification); test with fixtures for each missing element
- [ ] 2.3 Add lint checks against upstream: charter use case exists, sources passed readiness scorecard, source-of-truth resolved, access/masking policies assigned, personal-data fields masked or justified; tests per rule
- [ ] 2.4 Implement `konmari product test` wrapping `datacontract test`, storing results in the catalog; test with a failing not-null and uniqueness fixture

## 3. Versioning, approval and publish

- [ ] 3.1 Implement `konmari product diff` wrapping `datacontract breaking`/`changelog` and enforcing the semver policy; tests: field removal requires major, optional add requires minor, description edit requires patch
- [ ] 3.2 Implement `konmari product approve` recording approver, diff summary and test result
- [ ] 3.3 Implement `konmari product publish` (refuses without approval or with failed tests, refusal audited) and `--rollback`; tests for refusal, success and rollback
- [ ] 3.4 Implement deprecation of previous major with configurable window (default 90 days), successor and sunset date; test with a frozen clock

## 4. Scheduled refresh with change tracking

- [ ] 4.1 Implement `konmari product refresh` using dlt incremental cursor + merge into a staging schema; record refresh run with counts and `as_of`
- [ ] 4.2 Run contract tests on staging and swap atomically on pass; on fail keep previous data and `as_of`; tests for both paths
- [ ] 4.3 Propagate deletes via retention-decision tombstones and key reconciliation; test that a deleted source record disappears and is counted
- [ ] 4.4 Idempotency test: two consecutive runs without source change record zero changes and identical data
- [ ] 4.5 Add optional Debezium CDC source adapter behind config flag, with documented WAL/slot limits; integration test with a Postgres container
- [ ] 4.6 Generate cron/systemd timer from the freshness SLA (`konmari product schedule`); test schedule derivation
- [ ] 4.7 Emit OpenLineage START/COMPLETE/FAIL events for refresh and publish, store in catalog, optional HTTP transport to Marquez; test event contents

## 5. Catalog

- [ ] 5.1 Implement `konmari product list` (table and `--json`) with all catalog attributes; test that draft and withdrawn versions are not callable
- [ ] 5.2 Optional exporter of descriptors to OpenMetadata; documented as optional, test with recorded API fixture

## 6. MCP server: tools and resources

- [ ] 6.1 Implement `konmari serve` on the MCP Python SDK with STDIO and Streamable HTTP, protocol version pinned in config (default 2026-07-28, fallback 2025-11-25)
- [ ] 6.2 Generate tool definitions from contracts (names, title, description template, inputSchema with `additionalProperties: false`, outputSchema, explicit annotations, deterministic order); `--print-tools`; test schemas validate as JSON Schema 2020-12
- [ ] 6.3 Implement tool execution: input validation, field whitelist, freshness envelope (`product`, `version`, `as_of`, `freshness`) in structuredContent and text, `isError` vs JSON-RPC error semantics; tests per scenario
- [ ] 6.4 Implement resources `konmari://catalog` and `konmari://products/{id}/{version}` with `lastModified`, URI validation; test path traversal is rejected
- [ ] 6.5 Implement tool definition hashing, `--check-pins` and `--approve-tool` with audited approvals; test that an unapproved description change hides the tool
- [ ] 6.6 Ensure only published/deprecated-callable versions are exposed and state changes take effect without restart; test withdraw while running

## 7. Security and governance at the tool boundary

- [ ] 7.1 Implement OAuth 2.1 resource server: RFC 9728 metadata endpoint, 401 with `resource_metadata`, JWKS or introspection validation, audience check, query-string token rejection, no passthrough; tests for missing, expired, wrong-audience and query-string tokens
- [ ] 7.2 Map scopes `product:<id>:read` to products and return 403 `insufficient_scope` with required scopes; test
- [ ] 7.3 Implement STDIO identity from environment and localhost-only binding without TLS; tests for missing identity and bind address
- [ ] 7.4 Call `c07-governance` access-control and masking on every call/read; test row-level filtering and masked fields
- [ ] 7.5 Implement record cap with pagination, per-identity rate limit and timeout; tests for 250-record pagination and the 61st call
- [ ] 7.6 Implement output sanitisation; test that injection text in records never alters tool definitions
- [ ] 7.7 Write a redacted audit entry for every call/read, fail closed on audit failure; tests for entry contents and sink outage

## 8. End-to-end and documentation

- [ ] 8.1 End-to-end test: fixture source -> product publish -> refresh -> MCP client lists and calls tool over STDIO and HTTP (with a test OIDC issuer) and receives fresh, audited, masked results
- [ ] 8.2 MCP conformance check with the official MCP Inspector against both pinned protocol revisions
- [ ] 8.3 Write non-expert guide: first product in STDIO mode, approving and pinning tools, enabling OAuth with Keycloak, rollback; includes DSGVO checklist for purpose statements reviewed by the DPO
- [ ] 8.4 Run `openspec validate c08-connect-agent-interfaces --strict` and the full test suite green before marking the change complete
