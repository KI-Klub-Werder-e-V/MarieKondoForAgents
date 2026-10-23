## Context

Changes 01-07 leave the pilot with scoped, discarded, standardized, enriched and governed datasets recorded in the Postgres readiness catalog. Agents still have no stable interface to them. This change adds the "Connect" step: data products with contracts, scheduled change-tracked refreshes, a catalog, and an MCP server as the only agent entry point. Users are self-hosting organizations (SMEs, associations, public bodies) with limited ops capacity, so the default path must run on one machine without Kafka or a catalog platform. All research and source citations are in `research/c08-connect-agent-interfaces.md` (cited below as R§n).

Constraints: open source only, self-hostable, DSGVO/EU AI Act aware, every decision human-approved, reversible and audited in the catalog, pilot scope only.

## Goals / Non-Goals

**Goals:**
- Describe each pilot data product in a vendor-neutral, machine-readable format with owner, purpose, semantics, quality and SLA.
- Make breaking changes impossible to publish silently; make every publish reversible.
- Keep products current automatically with change tracking and lineage.
- Expose products to agents only through governed, read-only, described MCP tools/resources with spec-conformant auth.

**Non-Goals:**
- Write-back tools to source systems (agents acting on data) - later change, needs its own risk assessment.
- A catalog UI or data marketplace; OpenMetadata/DataHub export is optional.
- Building an authorization server; we integrate an existing OIDC AS.
- Defining access/masking policies (owned by `c07-governance`) or freshness alerting (owned by `c09-evaluate-and-monitor`).
- Organization-wide data mesh; only the pilot's products.

## Decisions

### D1 Descriptor = ODPS v1.0 product + ODCS v3.1 contract (YAML in git)
Both are Linux Foundation (Bitol) standards; ODCS carries schema, quality, SLA, ownership and supports unstructured data, ODPS wraps ports and lifecycle (R§1.3). Kit-specific fields (purpose-to-charter link, scopes, masking reference) go in `customProperties`.
Alternatives: own YAML schema (lock-in, no tooling); dbt model YAML only (tables only, no SLA/ownership standard); Open Data Product Specification (name clash, less tooling with ODCS) (R§1.3).

### D2 Versioning gate via `datacontract-cli`
`datacontract breaking` exits 1 on incompatible change and `datacontract test` checks real data (R§1.3), so we wrap these instead of writing a diff engine. Semver policy: major = breaking, minor = compatible add, patch = metadata (R§1.3, our assumption). Deprecated majors stay callable 90 days (our assumption, modelled on MCP's own deprecation policy, R§1.1).
Alternative: hand-written schema diff - more code, less coverage.

### D3 Publish is a catalog state transition with recorded owner approval
States: `draft -> approved -> published -> superseded|deprecated -> sunset`, plus `withdrawn` on rollback. Approval records approver, diff summary and test result; the server reads the state at request time. Rollback is a new approved transition, never a delete. This keeps the KonMari "human decides, reversible, audited" rule.
Alternative: git tags as source of truth - not queryable by the server, no approval identity.

### D4 Refresh: dlt incremental merge by default, Debezium CDC opt-in
dlt gives cursor-based incremental loading with merge keys and persisted state, no broker needed (R§1.5). Debezium log-based CDC is enabled only when the charter requires sub-hour freshness, because it needs logical replication slots and careful slot/WAL management (R§1.5, pitfall 7). Data is loaded into a staging schema, contract-tested, then swapped atomically (transactional rename) so failures never overwrite good data. Scheduling via cron/systemd timer calling `konmari product refresh`; Dagster/Airflow optional (R§1.5).
Alternatives: full reload (simple, but no change counts, expensive); Kafka-based CDC always (ops burden too high for SMEs).

### D5 Lineage via `openlineage-python`, stored in catalog, Marquez optional
Standard run events (START/COMPLETE/FAIL) with input datasets and output `product@version` (R§1.5). Catalog storage keeps the single-Postgres default; Marquez can receive the same events.

### D6 MCP server on the official Python SDK, protocol pinned (default 2026-07-28)
The SDK provides tools, resources and a resource-server auth hook (R§1.1). 2026-07-28 is current and stateless (R§1.1); we pin version in config and keep 2025-11-25 fallback switchable because agent hosts lag (pitfall 8).
Alternative: generic REST/OpenAPI with function calling - still offered indirectly because tools have JSON Schemas usable for function calling, but MCP gives discovery (`tools/list`, `resources/list`) for free.

### D7 Tools are generated, read-only, purpose-bound; no generic query tool
One tool per product output port, e.g. `support_faq.search` and `support_faq.get`, parameters derived from contract fields marked filterable. Annotations set explicitly because spec defaults assume destructive and open-world (R§1.1), but enforcement is server-side because annotations are only hints (R§1.1, pitfall 3). No `execute_sql` (pitfall 1). Field whitelist from the contract output schema implements DSGVO Art. 25 minimisation in the access architecture (R§1.6).

### D8 Hash-pinned tool definitions with reviewer approval
SHA-256 over the canonical JSON of the full definition, approval stored in catalog; mismatch = tool hidden. This is the OWASP rug-pull/tool-poisoning defence (R§1.2). Record content never flows into definitions.

### D9 Auth: OAuth 2.1 resource server per MCP spec; STDIO via environment
RFC 9728 metadata, 401 with `resource_metadata`, audience check against canonical URI, 403 `insufficient_scope`, no token passthrough (R§1.1). Token validation via JWKS (JWT) or RFC 7662 introspection, configurable. Scopes `product:<id>:read`; identity claims map to `c07-governance` access policies. Recommended self-hosted AS: Keycloak (not researched in depth, our assumption). STDIO follows the spec's advice to read credentials from the environment (R§1.1).
Alternative: static API keys over HTTP - not spec-conformant, no audience binding.

### D10 Limits and freshness in every answer
Default cap 100 records, max 1000, 60 calls/min per identity, per-call timeout (all our assumptions, R§2). Every result carries `product`, `version`, `as_of`, `freshness` so agents can say "as of" or "I don't know" (pitfall 6); resources carry `lastModified` (R§1.1).

### D11 Audit fail-closed via `c07-governance` audit sink
Every call logged with redacted arguments as OWASP recommends (R§1.2); if audit write fails the call fails, because untraceable access is worse than unavailability for DSGVO accountability.

## Risks / Trade-offs

- [MCP spec churn; agent hosts on older revisions] → protocol version pinned in config, conformance tests against both 2026-07-28 and 2025-11-25, SDK version pinned.
- [Generated descriptions too generic for good tool selection] → description template requires purpose and when-not-to-use; reviewer edits before pinning; `c09-evaluate-and-monitor` measures tool-choice accuracy.
- [Hash pinning slows iteration] → `--approve-tool` is one command; pins only gate exposure, not development.
- [dlt cursor misses hard deletes] → deletes handled via retention-decision tombstones or periodic key reconciliation; CDC for sources where this is insufficient.
- [Debezium slot bloat] → opt-in only, documented WAL caps and heartbeat (R§1.5).
- [Fail-closed audit reduces availability] → accepted trade-off; audit sink health is monitored.
- [Thresholds (cap, rate, 90 days, token lifetime) are assumptions] → all configurable, confirmed in pilot charter.
- [Secondary sources for GDPR interpretation] → DPO of the organization reviews purpose statements before first publish.

## Migration Plan

New capability, no existing interface to migrate. Rollout: (1) one low-sensitivity product (e.g. FAQ/product docs, per KonMari order) in STDIO mode; (2) HTTP with OAuth after `c07-governance` policies are in place; (3) further products. Rollback: `konmari product publish --rollback` per product; stopping `konmari serve` removes agent access entirely without touching data.

## Open Questions

- Which OIDC authorization server will pilot organizations already have (Keycloak, Entra ID, Authentik), and does it support RFC 8707 resource indicators?
- Which agent host(s) does the pilot use, and which MCP revision do they support?
- Do any pilot sources require sub-hour freshness (and therefore CDC)?
- Should the optional OpenMetadata export also publish its own MCP server, or must `konmari serve` stay the single agent entry point?
