## 1. Catalog schema and roles

- [x] 1.1 Add migration: `allowed_principals`, `denied_principals`, `acl_version`, `acl_status` columns on document, chunk and record tables, with GIN indexes on the principal arrays
- [x] 1.2 Add migration: `source_policy` (versioned, status pending/approved/superseded), `write_grant` (with expiry), `governance_status` view
- [x] 1.3 Add migration: `pii_finding` (type, offsets, score, detector version, review state; no raw values) and `pii_gate_result`
- [x] 1.4 Add migration: separate schema `konmari_keys` with `pseudonym_key` / mapping and a dedicated role; revoke all agent-role access
- [x] 1.5 Extend the `audit_event` table created by `c01-pilot-charter`: partition by month, add `prev_hash`/`hash` columns; app role INSERT/SELECT only; separate retention role with DELETE
- [x] 1.6 Test: migrations apply and roll back on an empty catalog; an UPDATE on `audit_event` as the app role is rejected

## 2. Audit logging core

- [x] 2.0 Adopt the events written by changes 01-06 (including the hash chain started by 04) into the hardened chain without rewriting their content; test that `konmari audit verify` and `konmari discard log verify` agree on one chain
- [x] 2.1 Implement `konmari.governance.audit.write_event()` with canonical JSON, hash chaining and a schema that rejects payload fields (prompt, completion, args, content)
- [x] 2.2 Implement fail-closed wrapper for tool calls: the call errors if the audit write fails
- [x] 2.3 Implement `konmari audit verify` (recompute chain, report the first broken event, honour checkpoints)
- [x] 2.4 Implement retention purge with checkpoint events; config validation (≥ 6 months when `ai_act_high_risk = true`; ≤ the Art. 30 maximum)
- [x] 2.5 Implement `konmari audit export --subject|--from/--to` to JSONL and CSV, itself audited
- [x] 2.6 Emit OTel `invoke_agent` / `execute_tool` spans with content capture disabled; store the trace ID on audit events
- [x] 2.7 Tests: chain verifies; manual row tamper detected; purge plus checkpoint still verifies; payload field rejected; export contains exactly the subject's events

## 3. Source policies and approvals

- [x] 3.1 Implement source-policy CRUD in the CLI (`konmari govern policy edit|approve|revert|show`) that creates `pending` versions; only approvers with the owner or compliance role can approve
- [x] 3.2 Seed one draft policy per in-scope source from `source-inventory` (sensitivity, owner) and `category-map` order
- [x] 3.3 Implement write-grant approval with an expiry (default 90 days) and a wildcard-scope validator
- [x] 3.4 Audit every policy, grant and revert event (uses 2.1)
- [x] 3.5 Tests: pending version not enforced; revert reactivates the old version; expired grant denies a write; `*` scope rejected

## 4. ACL capture, propagation and sync

- [x] 4.1 Extend connectors (SharePoint/M365, file share, CRM, SQL) to return normalized ACL entries; mark `acl_status = unknown` where unsupported
- [x] 4.2 Propagate parent ACLs to chunks/records in the chunking and normalization outputs; reject index inserts with empty ACL fields
- [x] 4.3 Implement `konmari govern acl-sync` honouring per-source cadence; apply revocations to all derived rows; record sync lag; set `stale-acl` when overdue
- [x] 4.4 Tests: 12 chunks inherit the parent ACL; insert without ACL fails; a revocation removes access after sync; an overdue sync blocks the source

## 5. Retrieval enforcement and identity

- [x] 5.1 Create RLS policies with FORCE on chunk/embedding tables (allow ∩ ¬deny on `current_setting('app.principals')`)
- [x] 5.2 Implement the request context: validate the OAuth 2.1 token audience, extract `sub` and `act.sub`, resolve user groups, compute the intersection with agent scopes, and `SET LOCAL` per transaction
- [x] 5.3 Support service-principal mode for autonomous agents (`on_behalf_of = none`, explicit grants only)
- [x] 5.4 Optional OpenFGA adapter: `ListObjects` → ID pre-filter, behind a config flag
- [x] 5.5 Benchmark a pgvector query under RLS on the pilot sample with `EXPLAIN ANALYZE`; record p95 latency in the design notes
- [x] 5.6 Tests: user without access never gets the chunk; deny beats allow; no principal leak across pooled connections; foreign-audience token → 401 + deny event; agent ∩ user scoping

## 6. PII detection and masking

- [x] 6.1 Add Presidio and spaCy `de_core_news_lg` / `en_core_web_lg`; ship `languages-config.yml` and German recognizers (IBAN DE, phone, Steuer-ID, PLZ+Ort) with German context words
- [x] 6.2 Implement `konmari govern scan`: per-chunk language detection, store findings without values, fail index builds for unscanned chunks
- [x] 6.3 Implement sensitivity classification (max of policy and findings; Art. 9 → `special-category`, blocked without an approved legal basis)
- [x] 6.4 Implement `konmari govern mask` with operators redact / mask / replace-with-type / pseudonymize (HMAC tokens, key from `konmari_keys`)
- [x] 6.5 Implement a re-identification command for the compliance role only, with mandatory purpose, audited
- [x] 6.6 Implement the review queue for mid-score findings; reversible decisions trigger re-mask on the next run
- [x] 6.7 Implement the recall gate: labelling export/import for ≥ 200 items, per-entity recall, threshold config (default 0.95), outdated on detector version change
- [x] 6.8 Tests: the German example yields PERSON, EMAIL_ADDRESS and IBAN_CODE; the same name gives the same token; redact leaves no digits; agent role denied on mapping; recall 0.88 blocks the source

## 7. Leak suite and governance gate

- [x] 7.1 Implement the leak test suite: per-pilot test principals plus adversarial queries targeting restricted chunks; assert zero unauthorized results
- [x] 7.2 Compute `governance_status` (ACL coverage 100 %, approved policy, PII gate, leak suite, audit heartbeat) and `konmari govern status` / `konmari govern check`
- [x] 7.3 Expose a read API for change 08 to query exposable sources; document the contract
- [x] 7.4 Add the leak suite and the governance tests to CI
- [x] 7.5 Tests: 3 % unknown ACL → `blocked: acl_coverage`; all conditions met → `approved`; a leak fails the gate

## 8. Compliance artefacts and docs

- [x] 8.1 Write an Art. 30 record template entry for the pilot agent (purpose, categories, recipients, retention) prefilled from the catalog
- [x] 8.2 Write a DPIA (Art. 35) screening checklist and an AI Act checklist (Art. 4 literacy, Art. 50 disclosure, Art. 26 if high-risk)
- [x] 8.3 Write a non-expert runbook "govern step" covering the order of commands, who approves what, and how to revert
- [x] 8.4 Dry-run the full govern step on the sample pilot dataset end to end and record the result in the catalog
