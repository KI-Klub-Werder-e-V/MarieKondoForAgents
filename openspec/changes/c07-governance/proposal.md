## Why

Once the pilot's data is cleaned, chunked and enriched (changes 04–06), the next risk is access. A naive index sees whatever the service account can see, drops the source ACLs, and returns restricted or personal data to any agent that asks (OWASP LLM02/LLM06; research/c07-governance.md §1.1, §1.3). Before anything is exposed to agents (change 08), permissions have to travel with the data, personal data has to be masked or excluded, and every agent access has to be attributable and audited, in a way that holds up under DSGVO and, where it applies, EU AI Act Art. 26.

## What Changes

- Add a governance step `konmari govern` that runs after structure/enrich and before connect, for the pilot scope only.
- Capture source ACLs per document/row during ingestion, normalize them to group principals, store them on every chunk/record in the catalog, and enforce them **inside** retrieval queries (Postgres RLS by default; OpenFGA optional for hierarchical ACLs).
- Model two identities on every request: the agent (client) and the on-behalf-of user (OAuth 2.1 / RFC 8693 `sub` + `act`). Effective access is the intersection of both. No token passthrough, no omnibus scopes.
- Per-tool least-privilege boundaries: read-only by default; write tools need explicit, human-approved grants that expire.
- Source policies per category: allowed/blocked for agents, sensitivity class, masking rule, and ACL-sync cadence, all owner-approved and versioned.
- PII detection (Presidio with German and English NLP) on every chunk before indexing; masking/pseudonymization per policy. Re-identification keys are stored separately. A recall gate is measured on a labelled pilot sample.
- An append-only, hash-chained audit log of agent access (who, on behalf of whom, which tool, which resource IDs, decision, policy version) without payload content. Retention defaults to 6 months; an export supports DSGVO Art. 15 requests and AI Act Art. 26(6).
- A governance gate: change 08 MUST NOT expose a source whose governance status is not `approved`.

## Capabilities

### New Capabilities
- `access-control`: ACL capture and propagation into indexes, RLS/ReBAC enforcement at retrieval, agent vs. on-behalf-of identity, least-privilege tool scopes, source policies, the leak test suite and the governance gate.
- `sensitive-data-masking`: PII detection (DE/EN), sensitivity classification, masking/pseudonymization operators, key separation, recall gate, and human review of detections.
- `audit-logging`: Append-only, tamper-evident audit events for agent access and governance decisions, retention and deletion, export for data-subject and authority requests, OTel correlation.

### Modified Capabilities
<!-- none: no existing specs in openspec/specs/ -->

## Impact

- **Upstream dependencies**: `pilot-charter` and `success-metrics` (01) define scope, roles and the compliance contact; `source-inventory` (02) supplies the sensitivity and owner per source; `category-map` (02) supplies the order (low-sensitivity categories governed first); `readiness-scorecard` (03) gets the criterion "permissions explicit"; `retention-decisions` (04) decides what is discarded before masking; `source-of-truth-policy` (05) feeds the source-policy precedence; `document-chunking` and `semantic-metadata` (06) produce the chunks that carry the ACL and sensitivity fields.
- **Downstream consumers**: `data-products` and `mcp-exposure` (08) MUST enforce the identity model, scopes and governance gate and MUST emit audit events; `agent-evaluation` (09) reuses the leak test suite; `freshness-monitoring` (09) tracks ACL-sync lag.
- **Code**: new `konmari.governance` package (acl, policy, pii, audit), catalog migrations (ACL columns, RLS policies, `source_policy`, `pii_finding`, `audit_event`, `pseudonym_key` in a separate schema), CLI commands `konmari govern …` and `konmari audit …`.
- **Dependencies**: `presidio-analyzer`, `presidio-anonymizer`, spaCy `de_core_news_lg` and `en_core_web_lg`; optional `openfga-sdk`; OpenTelemetry SDK. An OIDC/OAuth 2.1 authorization server (e.g. Keycloak) is assumed and is not shipped.
- **Compliance artefacts**: an Art. 30 record template entry and a DPIA (Art. 35) screening checklist for the pilot.
