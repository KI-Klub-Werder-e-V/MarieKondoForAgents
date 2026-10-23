## Context

KonMari step 4 ("govern") sits between structure/enrich (06) and connect (08). At this point the pilot's data is deduplicated, normalized, chunked and annotated in the Postgres readiness catalog, but it has no enforced permissions, no PII handling and no record of who accessed what. The users are self-running organizations (SMEs, associations, public bodies) or volunteers helping them. They have limited security staff, so the defaults must be safe, explainable to a data-protection officer, and built on the existing stack (Python 3.12 CLI, Postgres, MCP). DSGVO always applies. The EU AI Act applies at least through Art. 4 (literacy) and Art. 50 (transparency); Art. 26 applies only if the pilot is an Annex III high-risk use, which is rare for the typical support or knowledge pilot (research/c07-governance.md §1.5).

Stakeholders: data owners (approve source policies and write grants), compliance/DPO (approve special-category exposure, re-identification, retention), and the facilitator (staff member or volunteer) who runs `konmari`.

## Goals / Non-Goals

**Goals:**
- Permissions that travel from source ACL → catalog → every chunk → retrieval query, enforced in the database.
- A two-identity model (agent + on-behalf-of user) that change 08's MCP servers can adopt without inventing their own.
- German-capable PII detection with a measured, not assumed, recall gate.
- An audit trail cheap enough to always be on, free of payload content, tamper-evident, and exportable for Art. 15.
- Every governance decision human-approved, versioned and reversible.

**Non-Goals:**
- Shipping an identity provider or authorization server (we integrate with an existing OIDC/OAuth 2.1 server such as Keycloak or Entra ID).
- Full AI Act conformity assessment or high-risk provider obligations.
- Data-loss prevention on model outputs (output filtering belongs to 08/09 as defence in depth).
- Governing sources outside the pilot scope.
- Legal advice: we provide templates (Art. 30 entry, DPIA screening), not determinations.

## Decisions

### D1: Enforce permissions in Postgres RLS by default; OpenFGA as an optional pre-filter
Chunks and embeddings live in Postgres/pgvector. RLS policies compare the chunk's `allowed_principals` / `denied_principals` arrays to a principal set placed in `SET LOCAL app.principals` per transaction, with `FORCE ROW LEVEL SECURITY` so the table owner cannot bypass it (research §1.1, S7, S8). This keeps one moving part and makes enforcement independent of application bugs.
For sources with deep folder or group hierarchies, an optional OpenFGA adapter computes `ListObjects` for the user and passes the ID list as an additional filter (S5, S6).
- *Alternative: post-retrieval check per hit* (S1): rejected as the primary control because restricted text reaches the application layer and recall drops; kept only as a test assertion.
- *Alternative: separate index per role*: rejected, because it multiplies storage and breaks for overlapping groups.
- *Alternative: OpenSearch DLS*: not in the default stack.

### D2: Store group principals on the chunk and resolve user → groups at request time
Storing group IDs, not users, limits fan-out when membership changes (S3, S4). User→group resolution comes from the IdP token or a cached directory sync. Deny overrides allow. An unknown ACL means nobody has access (fail closed).

### D3: Two identities via OAuth 2.1 and RFC 8693; intersection semantics
The MCP/data-product layer accepts only tokens issued for its audience (RFC 8707 resource indicator) and never passes tokens through (S9, S11). Delegated calls carry `sub` = user and `act.sub` = agent (S12, S13). Effective access = agent scopes ∩ user data permissions. Autonomous agents use a service principal with explicitly granted resources only.
- *Alternative: API keys per agent*: rejected, because no user context means the agent sees everything its key sees, which is the "whatever the service account can see" anti-pattern from the source brief.

### D4: Read-only default, expiring owner-approved write grants, no omnibus scopes
This follows the MCP scope-minimization guidance (S11) and OWASP LLM06 Excessive Agency (S14). The 90-day expiry is our assumption: it forces periodic re-approval within one pilot cycle.

### D5: Presidio with spaCy `de_core_news_lg` + `en_core_web_lg`, custom DE recognizers, measured recall gate
Presidio is MIT-licensed, self-hostable, pluggable, and supports pseudonymization operators (S16, S17). Its default configuration is English-only (S18), so we ship a `languages-config.yml` with German NLP, German context words, and recognizers for German formats (IBAN DE, Steuer-ID, phone formats, PLZ+Ort). The recall threshold (≥ 0.95 on ≥ 200 labelled items) is **our assumption** (research §2), configurable per pilot, and must be agreed in the pilot charter's success metrics (01).
- *Alternative: LLM-based PII detection*: rejected as default, because sending data to a model to find personal data is itself processing, and its output is non-deterministic. Allowed later as an additional recognizer.
- *Alternative: drop all personal data*: allowed by policy (`redact`), but too blunt for support use cases that need entity continuity, so `pseudonymize` exists.

### D6: Pseudonymization = keyed HMAC tokens; key and mapping in a separate schema
Under EDPB 01/2025 pseudonymised data remains personal data (S21), so we do not relabel it as anonymous. Tokens are `HMAC-SHA256(key, normalized value)` truncated for readability. The key and the optional reverse map live in schema `konmari_keys` with a dedicated role, and every lookup is audited. Rotating the key re-tokenizes on the next run.

### D7: Audit log in Postgres, append-only role, hash chain, no payloads
A dedicated `audit_event` table; the app role gets INSERT/SELECT only; each row stores `hash = sha256(prev_hash || canonical_json(row))`. Payload content is excluded, mirroring OTel GenAI's default of not capturing content (S28), so the log does not become a second PII store. Agent tool calls fail closed if the audit write fails.
- *Alternative: external SIEM / WORM storage*: optional export target; not required for a self-hosting SME.
- *Alternative: log everything including prompts*: rejected (pitfall 8 in research).

### D8: Retention 6 months by default, purge with checkpoints
Six months mirrors AI Act Art. 26(6) (S24, S25), even though most pilots are not high-risk; the ceiling comes from DSGVO storage limitation and the Art. 30 record. Purge runs under a separate retention role and writes a checkpoint event so the chain verifies from the checkpoint.

### D9: Source policies as versioned catalog rows with approval workflow
`source_policy(id, version, status pending|approved|superseded, exposure, sensitivity, masking_rule, acl_sync_cadence, tools, owner, approver, approved_at)`. Only approved versions are enforced; reverts create a new version pointing to the old content. Precedence for conflicting sources is taken from `source-of-truth-policy` (05), not re-decided here.

### D10: Governance gate computed, not declared
`governance_status` per source is derived from ACL coverage = 100 %, approved policy, PII gate, leak suite pass and an audit heartbeat. Change 08 reads this status; nobody can set `approved` by hand.

## Risks / Trade-offs

- [RLS on vector queries can defeat the ANN index or slow queries] → index the principal arrays (GIN), benchmark with `EXPLAIN` on pilot data, fall back to the OpenFGA ID pre-filter for large ACL sets.
- [Connector cannot read ACLs, e.g. legacy file share] → fail closed (`acl_status = unknown`); the owner assigns principals in the source policy; flagged on the readiness scorecard (03).
- [ACL sync lag leaves revoked access live for up to 24 h] → cadence configurable down to hourly; freshness-monitoring (09) alerts; owners can trigger a manual sync.
- [German PII recall below gate] → custom recognizers, a larger model, a human review queue; the source stays blocked rather than shipped.
- [False positives over-mask product terms] → human review with reversible decisions; allow-list per category.
- [Audit log volume] → no payloads, IDs only; partition by month; purge.
- [Hash chain gives tamper evidence, not tamper proof, against a DB superuser] → document it; optional periodic anchor export of the latest hash to an external store.
- [Regulatory timelines shift (Digital Omnibus)] → obligations are configured as flags (high-risk yes/no), not hard-coded dates; the Art. 4/50 checklist is kept in docs.

## Migration Plan

New capability, nothing to migrate. Rollout per pilot: (1) run migrations, which create tables, RLS policies and roles; (2) backfill ACLs for existing chunks from 06, where chunks without ACL stay blocked; (3) PII scan + labelled sample + gate; (4) owner approval of source policies; (5) leak suite; (6) the gate opens for 08. Rollback: disable exposure (sources revert to `blocked`). RLS and audit stay on; removing them is not a supported rollback.

## Open Questions

- Which IdP do pilot organizations typically have (Entra ID vs. Keycloak vs. none)? Does Entra ID support the RFC 8693 shape we need, or do we map its OBO flow?
- Should the reverse pseudonym map exist at all by default, or only the HMAC key (no map, so re-identification requires the original value)?
- Exact German recognizer list (Steuer-ID, Sozialversicherungsnummer, Kfz-Kennzeichen) to be validated with a DPO.
- Is a Germany-specific Beschäftigtendatenschutz rule needed when the pilot touches employee data (works council information, cf. Art. 26(7))?
