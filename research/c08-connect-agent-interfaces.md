# 08 Connect: agent interfaces (data products + MCP exposure)

Research for change `c08-connect-agent-interfaces` (KonMari sequence step 5, "Connect").
Research date: 2026-10-23. Scope: how curated, governed pilot data is exposed to agents as
described, versioned data products and governed tools; how it is made discoverable; how it stays
current without manual updates.

Convention: numbers either cite a source in brackets or are marked **[assumption]** (our choice,
to be confirmed in the pilot charter of change 01).

## 1. Key findings

### 1.1 MCP is the agent interface; the spec moved in 2026

- The **current** MCP protocol revision is **2026-07-28**; 2025-11-25 is the previous one [S1].
  2026-07-28 makes MCP stateless: no `initialize` handshake, no `Mcp-Session-Id`; every request
  carries protocol version and client capabilities in `_meta`; servers MUST implement
  `server/discover`; `resources/subscribe` is replaced by `subscriptions/listen`; list and read
  results carry `ttlMs` and `cacheScope` freshness hints; servers SHOULD return `tools/list` in a
  deterministic order; Dynamic Client Registration is deprecated in favour of Client ID Metadata
  Documents; a feature deprecation window of at least twelve months applies [S2].
  Consequence for us: target 2026-07-28, keep 2025-11-25 compatibility only if the pilot's agent
  host still needs it (SDKs and hosts lag spec releases).
- **Tools** (model-controlled): each has `name`, optional `title`, `description`, `inputSchema`
  (MUST be a valid JSON Schema object), optional `outputSchema`, optional `annotations` [S3].
  Tool names SHOULD be 1-128 chars from `[A-Za-z0-9_.-]` and unique per server [S3].
  If `outputSchema` is given, servers MUST return conforming `structuredContent`, and SHOULD also
  return the serialized JSON in a text block for compatibility [S3].
  Business/validation failures are reported as tool results with `isError: true` (so the model can
  self-correct); unknown tools and malformed requests are JSON-RPC protocol errors [S3].
  Servers MUST validate all tool inputs, implement access controls, rate-limit invocations and
  sanitize outputs; there SHOULD always be a human in the loop able to deny tool invocations [S3].
- **Tool annotations** (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`)
  are hints, not guarantees; clients MUST treat them as untrusted unless the server is trusted [S3].
  Defaults: `readOnlyHint=false`, `destructiveHint=true`, `idempotentHint=false`,
  `openWorldHint=true` [S4] - so a read-only data tool must set `readOnlyHint: true` explicitly.
- **Resources** (application-controlled): `uri`, `name`, optional `title`, `description`,
  `mimeType`, `size`; annotations `audience` (`user`/`assistant`), `priority` (0.0-1.0),
  `lastModified` (ISO 8601) [S5]. Resource templates use RFC 6570 URI templates [S5].
  Servers MUST validate all resource URIs; access controls SHOULD be implemented for sensitive
  resources [S5]. `lastModified` is the natural carrier for freshness to the agent.
- **Authorization (HTTP transports)**: MCP server = OAuth 2.1 resource server [S6]. Servers MUST
  publish OAuth 2.0 Protected Resource Metadata (RFC 9728) with at least one
  `authorization_servers` entry, via `WWW-Authenticate: Bearer resource_metadata=...` on 401 and/or
  the `/.well-known/oauth-protected-resource` URI; SHOULD include `scope` in the challenge [S6].
  Servers MUST validate tokens per OAuth 2.1 §5.2, MUST verify they are the intended audience
  (RFC 8707), MUST NOT accept or pass through any other token; invalid/expired token -> 401;
  insufficient scope -> 403 with `error="insufficient_scope"` and the needed scopes [S6].
  Tokens MUST NOT be in query strings; AS SHOULD issue short-lived access tokens and MUST rotate
  refresh tokens for public clients; all AS endpoints over HTTPS [S6].
  STDIO servers SHOULD NOT use this flow and take credentials from the environment [S6].
- The official **MCP Python SDK** ships resource-server support (`TokenVerifier` +
  `AuthSettings(issuer_url, resource_server_url, required_scopes)`); the example verifier is a
  stub, real validation (JWKS or RFC 7662 introspection) is our job [S7].

### 1.2 MCP-specific threats (beyond classic API security)

- OWASP MCP Security Cheat Sheet [S8]: least privilege per server with narrow scopes; **pin tool
  definitions by hash** (incl. `inputSchema`, `outputSchema`, `annotations`) and require review
  on change ("rug pull" defence - detects metadata changes only, not code changes); strict input
  schemas (`additionalProperties: false`, `pattern`), guard against injection/path traversal/SSRF;
  treat all tool output as untrusted before it enters the LLM context; explicit human
  confirmation for destructive, financial or data-sharing actions, never auto-approve; log every
  invocation with parameters, user context and timestamp, redacting secrets and PII; rate limits,
  quotas and timeouts per session/tenant; treat each server as a separate security domain.
- Tool poisoning: malicious instructions hidden in tool metadata the model reads but humans
  rarely see [S9]. Confused deputy / token passthrough: forbidden by the spec [S6].

### 1.3 Data products: describe, version, contract

- A data product should be discoverable, addressable, trustworthy, self-describing,
  interoperable and secure (Dehghani's data-mesh characteristics) [S10]. Trustworthiness means
  the owner measures and stands behind completeness, lag/timeliness and shape [S10].
- Two Linux Foundation (Bitol) standards fit together: **ODCS** (Open Data Contract Standard,
  v3.1.0, late 2025) = YAML contract with fundamentals, schema, quality rules, servers, ownership,
  SLA, support; v3 covers relational, hierarchical, streaming and unstructured data; v3.1 adds
  property relationships, stricter JSON Schema validation and executable SLAs; all v3.0.x
  contracts remain valid [S11]. **ODPS** (Open Data Product Standard, v1.0.0) = the product
  wrapper with lifecycle and ports; "ODCS covers the contract, ODPS covers the product" [S12].
  Beware the name clash with the separate "Open Data Product Specification" [S13].
- **Versioning**: `datacontract-cli` offers `changelog` and `breaking`; `breaking` classifies
  changes by severity and exits with status 1 on a backward-incompatible change, which makes it a
  CI gate; `datacontract test` runs schema and quality checks against the real source [S14].
  Common semver rule for contracts: major = breaking, minor = compatible addition,
  patch = metadata fix [S15] (secondary source; we adopt it as **[assumption]** policy).

### 1.4 Catalog / discoverability

- OpenMetadata (Apache-2.0) supports domains and data products and ships an embedded MCP server
  at `/mcp` that respects platform permissions [S16]; DataHub (Apache-2.0) is the alternative and
  also ingests ODCS contracts [S17]. Comparison material is mostly vendor-authored [S16] - we do
  not depend on either: the Postgres readiness catalog is the system of record and the kit can
  export to a catalog.
- For a single pilot, discoverability for agents is primarily `tools/list` + `resources/list`
  descriptions plus a `catalog` resource listing products; a full catalog UI is optional.

### 1.5 Update mechanism: scheduled pipelines with change tracking

- **CDC from Postgres** with Debezium: `wal_level=logical`, one replication slot per connector,
  heartbeats for low-traffic DBs, cap retained WAL for stalled slots, idempotent consumers because
  delivery is at-least-once [S18]. Transactional outbox avoids dual writes [S19].
  Practitioner guidance: CDC when latency < 1 s is needed, polling acceptable otherwise [S19].
- **Incremental loads without Kafka**: dlt (Apache-2.0) supports cursor-based incremental
  loading (`dlt.sources.incremental` on a timestamp/ID) with `write_disposition="merge"` and a
  primary/merge key, strategies delete-insert (default), upsert, SCD2; state is persisted between
  runs; `replace`/`drop_data` resets state for a rebuild [S20].
- **Scheduling + freshness**: Dagster freshness = asset materialized within a defined window,
  otherwise overdue; checks must be driven by a schedule or sensor [S21]. Plain cron/systemd
  timers are enough for one pilot **[assumption]**.
- **Lineage**: OpenLineage run events (START then COMPLETE/FAIL/ABORT) with job and dataset
  facets; Marquez is the reference backend [S22]. Each publish of a data product version should
  emit a run event so agent answers can be traced back to inputs.

### 1.6 DSGVO / EU AI Act angle

- Art. 25 GDPR (data protection by design and by default): process only what each purpose needs;
  commentary argues minimisation must be enforced in the access architecture (which fields a tool
  returns), not only by intended use, and that purpose limitation also covers tool arguments,
  retrieved context and logs [S23] (secondary sources; EDPB primary texts to be checked by the
  client's DPO). Our translation: each tool is bound to a declared purpose and returns only
  whitelisted fields; PII masking itself is owned by change 07.

## 2. Concrete thresholds and defaults

| Item | Value | Basis |
|---|---|---|
| MCP protocol target | 2026-07-28 (current) | [S1] |
| Tool name | 1-128 chars, `[A-Za-z0-9_.-]`, unique per server | [S3] |
| Invalid/expired token | HTTP 401 + `WWW-Authenticate` with `resource_metadata` | [S6] |
| Insufficient scope | HTTP 403 `error="insufficient_scope"` | [S6] |
| Breaking contract change | `datacontract breaking` exit code 1 blocks publish | [S14] |
| Access token lifetime | <= 15 min | **[assumption]** (spec only says "short-lived" [S6]) |
| Default max rows per tool call | 100, hard cap 1000 | **[assumption]** |
| Default rate limit | 60 calls/min per client | **[assumption]** |
| Default refresh cadence | daily batch; CDC only if charter needs < 1 h freshness | **[assumption]**, CDC rationale [S19] |
| Old major version kept callable | 90 days after successor published | **[assumption]**, inspired by MCP deprecation window [S2] |
| Description length | each tool/resource description >= 1 sentence of purpose + when-not-to-use | **[assumption]** |

## 3. Recommended OSS tools

| Tool | Licence | Why |
|---|---|---|
| MCP Python SDK (`mcp`) | MIT | Official SDK; tools, resources, OAuth resource-server hooks [S7] |
| Open Data Contract Standard (ODCS) v3.1 | Apache-2.0 | Vendor-neutral YAML contract incl. SLA and quality [S11] |
| Open Data Product Standard (ODPS) v1.0 | Apache-2.0 | Product descriptor (ports, lifecycle) paired with ODCS [S12] |
| datacontract-cli | MIT | `lint`, `test`, `breaking`, `changelog` as CI gates [S14] |
| dlt | Apache-2.0 | Incremental + merge loads with persisted cursor state, no Kafka needed [S20] |
| Debezium | Apache-2.0 | Log-based CDC when sub-hour freshness is required [S18] |
| OpenLineage + Marquez | Apache-2.0 | Standard lineage events and reference backend [S22] |
| OpenMetadata (optional) | Apache-2.0 | Catalog with data products, domains, embedded MCP [S16] |
| Keycloak (optional) | Apache-2.0 | Self-hosted OAuth 2.1/OIDC AS with discovery and PKCE **[assumption: not researched in depth]** |

## 4. Pitfalls

1. **Exposing raw tables as tools** ("execute_sql") - bypasses contracts, minimisation and
   row/field governance; agents also generate bad SQL. Expose purpose-built, read-only tools.
2. **Thin descriptions** - the model chooses tools by description; vague text causes wrong tool
   choice. But descriptions are also an injection surface (tool poisoning) [S8][S9]: review and
   hash-pin them.
3. **Trusting annotations** - `readOnlyHint` is a hint; the server must enforce read-only [S3].
4. **Token passthrough** to the source system - forbidden; use a separate downstream credential
   [S6].
5. **Silent breaking changes** - renaming a field breaks agents without error; gate with
   `datacontract breaking` and semver [S14].
6. **Stale data looking fresh** - always return `lastModified`/`as_of` so the agent can say
   "data as of ..." or "I don't know" (brief, rule 2).
7. **Replication slot bloat** with Debezium when a connector stalls [S18].
8. **Spec churn** - MCP 2026-07-28 removed sessions and subscriptions semantics [S2]; pin the
   protocol version and SDK version and test against the pilot's agent host.
9. **Unbounded results** - large result sets blow the context window and leak data; paginate
   and cap.

## Sources

- [S1] MCP Versioning (current revision 2026-07-28): https://modelcontextprotocol.io/specification/versioning
- [S2] MCP 2026-07-28 Key Changes: https://modelcontextprotocol.io/specification/2026-07-28/changelog
- [S3] MCP 2025-11-25 Server Tools: https://modelcontextprotocol.io/specification/2025-11-25/server/tools
- [S4] Tool annotation defaults (secondary): https://azukiazusa.dev/en/blog/mcp-tool-annotations ; https://docs.praison.ai/docs/mcp/mcp-tool-annotations
- [S5] MCP 2025-11-25 Server Resources: https://modelcontextprotocol.io/specification/2025-11-25/server/resources
- [S6] MCP 2025-11-25 Authorization: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization (2026-07-28 version: https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [S7] MCP Python SDK authorization docs: https://py.sdk.modelcontextprotocol.io/authorization/
- [S8] OWASP MCP Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/MCP_Security_Cheat_Sheet.html
- [S9] WorkOS, MCP security risks: https://workos.com/blog/mcp-security-risks-best-practices
- [S10] Data product characteristics (secondary on Dehghani): https://venturebeat.com/datadecisionmakers/data-mesh-what-it-is-and-why-you-should-care/ ; https://estuary.dev/blog/data-products-usability/
- [S11] Bitol, ODCS v3.1.0 announcement: https://bitol.io/bitol-announces-odcs-v3-1-0-stronger-smarter-and-stricter/
- [S12] Bitol, ODPS v1.0.0 announcement: https://bitol.io/announcing-odps-v1-0-0-building-the-language-of-data-products/
- [S13] ODPS naming clash: https://architecturalbytes.substack.com/p/odps-odps-why-your-data-product-strategy
- [S14] datacontract-cli `breaking` / compare versions: https://docs.datacontract.com/commands/breaking ; https://docs.datacontract.com/compare-contract-versions
- [S15] Contract semver guideline (secondary): https://etlantic.readthedocs.io/en/latest/03_DATA_CONTRACTS/VERSIONING/
- [S16] OpenMetadata vs DataHub (vendor comparison): https://atlan.com/openmetadata-vs-datahub ; https://open-metadata.org/llms.txt
- [S17] DataHub ODCS source: https://docs.datahub.com/docs/generated/ingestion/sources/odcs
- [S18] Debezium with PostgreSQL (practitioner): https://oneuptime.com/blog/post/2026-01-27-debezium-postgresql/view
- [S19] Transactional outbox and CDC: https://conduktor.io/blog/transactional-outbox-pattern-database-kafka
- [S20] dlt incremental loading / merge loading: https://dlthub.com/docs/general-usage/incremental-loading ; https://dlthub.com/docs/general-usage/merge-loading.md
- [S21] Dagster freshness checks: https://docs.dagster.io/concepts/assets/asset-checks/checking-for-data-freshness
- [S22] OpenLineage docs and facets: https://openlineage.io/docs/ ; https://openlineage.io/docs/spec/facets/
- [S23] GDPR Art. 25 / minimisation for agents (secondary): https://captaincompliance.com/education/purpose-limitation-and-data-minimization-in-agentic-ai-what-gdpr-compliance-actually-requires/ ; https://sota.io/blog/gdpr-article-25-privacy-by-design-paas-saas-developer-guide
