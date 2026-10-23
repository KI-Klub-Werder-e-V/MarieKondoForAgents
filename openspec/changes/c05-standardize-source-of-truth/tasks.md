## 1. Setup and catalog schema

- [ ] 1.1 Add pinned dependencies `splink`, `python-stdnum`, `phonenumbers`, `pint`, `ucumvert`, `openlineage-python` to the `konmari` package and verify `pip install` in a clean Python 3.12 venv
- [ ] 1.2 Spike: confirm `phonenumbers` E.164 API and the chosen Splink major version on a synthetic fixture. Record the outcome in design.md Open Questions
- [ ] 1.3 Create catalog migrations (Postgres + DuckDB) for `normalization_spec`, `normalized_value`, `quarantine`, `er_pair`, `er_cluster`, `crosswalk`, `policy_version`, `policy_rule`, `golden_attribute` and `conflict`. The migration test applies them and rolls them back on both engines
- [ ] 1.4 Create a synthetic pilot fixture (CRM + ERP + SharePoint list, about 1,000 customers, known duplicates, DST edge cases, invalid IBAN/VAT, mixed units). No real personal data

## 2. Normalization specs and normalizers

- [ ] 2.1 Implement YAML schema and loader for normalization specs with versioning (author, timestamp, diff). Test: loading a changed spec creates a new version
- [ ] 2.2 Implement a `konmari normalize` run that refuses fields without a spec and writes raw/canonical/spec_version/run_id additively. Tests: missing-spec exit code, idempotent re-run
- [ ] 2.3 Implement date/datetime normalizer (explicit pattern, declared zone, RFC 3339 UTC + companion zone, epoch unit, separate event/processing time). Tests: Europe/Berlin conversion, DST-repeated hour and missing-offset quarantine
- [ ] 2.4 Implement identifier normalizer via python-stdnum and phone normalizer via phonenumbers. Tests: VAT compaction, invalid IBAN quarantine, E.164 output, `syntax_valid` flag
- [ ] 2.5 Implement name/text normalizer (NFC display, NFKC+casefold match key, optional legal-form stripping and transliteration). Tests: `Müller GmbH`/`MUELLER  GmbH` share a key only with transliteration enabled
- [ ] 2.6 Implement unit normalizer with Pint + ucumvert and a per-field allowed unit list. Tests: `1,5 t` → `1500 kg`, incompatible dimension quarantined
- [ ] 2.7 Implement quarantine table plus `konmari normalize quarantine list|resolve|revert` with approver and timestamp. Tests: resolve and revert keep the full history
- [ ] 2.8 Implement per-field conformance metrics (canonical rate, quarantine rate, reasons, unaccounted count) written to the catalog for `readiness-scorecard`. Test: a silently dropped value fails conformance
- [ ] 2.9 Export normalization specs as ODCS data-contract quality rules and run them in CI on the fixture

## 3. Entity resolution

- [ ] 3.1 Implement Splink linkage on canonical values and match keys across sources, consuming `dedup-detection` clusters for within-source duplicates. Test: known cross-source duplicates in the fixture are scored
- [ ] 3.2 Implement two-threshold routing (defaults 0.99 / 0.50, overridable from `success-metrics`) into auto-link, review queue and reject. Tests: 0.995 auto-link, 0.80 queued
- [ ] 3.3 Implement `konmari resolve review` (CSV export/import and CLI accept/reject) storing negative labels. Test: a rejected pair is never auto-linked again
- [ ] 3.4 Implement the quality gate: precision on a ≥100-pair labelled sample, recall against the owner's known-duplicate list, and `konmari resolve publish` blocking below the floors. Tests: precision 0.95 blocks, missing recall list requires an audited override
- [ ] 3.5 Implement crosswalk persistence and `konmari resolve split|restore` with golden-record recomputation. Test: split two merged sister companies and restore them
- [ ] 3.6 Add a Splink regression test that pins expected cluster counts on the fixture to catch API or version drift

## 4. Source-of-truth policy and golden records

- [ ] 4.1 Implement policy YAML schema (source_priority, most_recent with a required timestamp field, most_complete, owner_decision, owner, tie-breaker, rationale) with validation. Test: `most_recent` without a timestamp is rejected
- [ ] 4.2 Implement the policy lifecycle (`pending` → approved by the charter-named owner → active, with dry-run impact report and rollback). Tests: an unapproved version stays inactive, rollback recomputes the golden records
- [ ] 4.3 Implement `konmari policy check` for coverage of agent-exposed fields (from 08 `data-products` / `mcp-exposure` declarations). Test: uncovered `customer.email` blocks publication
- [ ] 4.4 Implement the golden-record builder with per-attribute provenance (source, record ID, rule, policy version, timestamps). Test: CRM wins contact preference and ERP wins payment terms on the fixture
- [ ] 4.5 Emit OpenLineage events with a `columnLineage` facet for normalization and golden-record runs. Test: the event validates against the OpenLineage schema and maps `customer.payment_terms` to its ERP input field

## 5. Conflict register

- [ ] 5.1 Implement conflict detection (undecidable → `open`, rule-decided → `overridden`, normalization-equal → none). Tests: the phone format difference creates no conflict, and an `owner_decision` disagreement creates one
- [ ] 5.2 Implement `konmari conflicts list|resolve|accept|revert` with write-back ticket reference, approver and timestamp. Test: resolve with ticket `IT-4711`, then revert
- [ ] 5.3 Implement reopening on recurrence (except `accepted_deviation`) and owner notification. Test: an uncorrected source reopens a resolved entry
- [ ] 5.4 Implement the personal-data flag from `source-inventory` sensitivity, a 30-day default due date, and `konmari conflicts report --overdue`. Test: an overdue personal-data conflict is listed
- [ ] 5.5 Integrate `access-control` / `sensitive-data-masking` (07) on conflict and quarantine listings. Test: an unauthorized user sees masked candidate values

## 6. Integration, audit and docs

- [ ] 6.1 Send all approvals, rollbacks, reviews, quarantine and conflict resolutions to `audit-logging` (07). Test: each action type produces an event with actor, before/after state and reason
- [ ] 6.2 Expose golden values with provenance and `resolved|unresolved` status to `data-products` / `mcp-exposure` (08). Contract test: an unresolved attribute returns no asserted value
- [ ] 6.3 Publish metrics (conformance, ER precision/recall, policy coverage, open/overdue conflicts) for `readiness-scorecard` (03) and `freshness-monitoring` (09)
- [ ] 6.4 End-to-end test on the fixture: normalize → resolve → review → policy approve → golden records → conflicts, with a full rollback leaving the raw data unchanged
- [ ] 6.5 Write operator and owner guides (German and English, plain language): writing a spec, reviewing pairs, approving policy, resolving conflicts, DSGVO notes
