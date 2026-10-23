## Context

This change is step 2 of the KonMari sequence: clean and standardize the data before enriching
(06) or connecting (08) it. Its input is the pilot's remaining records after the discard pass
(04). Those records come from the sources in `source-inventory` (02), grouped by entity in
`category-map` (02). Pilot scope, field owners and approvers come from `pilot-charter` (01), and
the target thresholds come from `success-metrics` (01). Today the same fact is written in several
formats, the same entity has several IDs across systems, and nothing says which system wins. The
users are the client's own staff or volunteers, the data owners, and compliance. So every decision
has to be explainable without expert help, approved by a human, reversible, and audited. All
research references point to `research/c05-standardize-source-of-truth.md` (below: "R§").

## Goals / Non-Goals

**Goals:**
- Machine-checkable normalization specs per in-scope field (R§1.1, ISO 8000-110 "can be checked by computer").
- Additive normalization: raw values are never overwritten (R§4 pitfall 1).
- Cross-source entity resolution with a human review band and measured precision and recall (R§1.3).
- Per-field precedence policy, golden records with attribute lineage, and a conflict register with owner resolution (R§1.4).
- DSGVO-aware handling of personal-data conflicts (R§1.5).

**Non-Goals:**
- Writing corrections back into source systems automatically. This change records a write-back ticket only.
- Enterprise MDM hub features (real-time sync, stewardship UI beyond the CLI and a simple review export).
- Within-source deduplication (owned by `dedup-detection`, 04), chunking and enrichment (06), and access policy itself (07).
- Normalizing data outside the pilot scope.

## Decisions

1. **Declarative YAML specs + catalog tables, not code per field.** Each field gets a spec
   (`normalize/*.yaml`) and each entity a policy file (`policy/*.yaml`). Both are versioned in git and
   in the catalog. This makes rules reviewable by non-developers and exportable as ODCS data-contract
   checks (R§1.2). *Alternative:* dbt models per field, which are less readable for owners and tie the
   kit to a warehouse. *Alternative:* Great Expectations-only, which validates values but does not
   normalize them.

2. **Additive storage `raw | canonical | match_key | spec_version | run_id`.** NFKC, transliteration
   and unit conversion lose information (R§1.2, R§4), and reversibility is a KonMari rule.
   *Alternative:* in-place update. Rejected because it cannot be reverted.

3. **Stdlib + focused libraries for normalizers.** `datetime`/`zoneinfo` for RFC 3339 UTC plus the
   original zone. `python-stdnum` for IBAN, VAT and other standard numbers. `phonenumbers` for E.164.
   `unicodedata` for NFC/NFKC. `pint` + `ucumvert` for units (R§3). Ambiguity (no offset, DST-repeated
   hour, unknown unit, invalid check digit) leads to **quarantine**, never a guess (R§1.2, R§4 pitfall 2).
   *Alternative:* `dateutil.parser` fuzzy parsing. Rejected because it silently guesses DD/MM order.

4. **Splink (pinned) on DuckDB for entity resolution.** It implements Fellegi-Sunter, trains without
   labels, supports clerical labels, runs locally, and scales to Spark if needed (R§1.3). Two
   thresholds create a review band. The defaults (upper 0.99, lower 0.50, precision ≥ 0.98, recall ≥
   0.95, sample ≥ 100) come from the Kestra Splink example or are our assumptions (R§2), and the
   pilot overrides them via `success-metrics`. *Alternative:* `dedupe` (active learning), a fallback
   for sources with very few columns. *Alternative:* deterministic match keys only, which have
   low recall on dirty names.

5. **Survivorship strategies limited to four.** `source_priority`, `most_recent`, `most_complete` and
   `owner_decision`, following the vendor-neutral core of Stibo/Reltio/Profisee terminology (R§1.4).
   There is no implicit "recency default" (Reltio's default) because it silently favours whichever
   system synced last (R§4 pitfall 3). *Alternative:* trust scores per source, which are harder for
   owners to reason about. They may come later.

6. **Policy changes are dry-run, approved, then activated.** A pending version shows its impact
   (how many golden values change) before the owner approves it. Rollback re-applies an earlier
   approved version (R§4 pitfall 7).

7. **Conflict register distinguishes `open`, `resolved`, `accepted_deviation` and `reopened`, plus the
   "overridden" disagreements that rules decided.** This makes "fixed the view but not the source"
   visible (R§4 pitfall 6) and supports the DSGVO accuracy duty (R§1.5). The 30-day due date default
   is our assumption, not a legal deadline (R§2).

8. **Lineage via OpenLineage `columnLineage`.** Normalization emits DIRECT/TRANSFORMATION, and
   survivorship emits DIRECT/IDENTITY from the winning input field (R§1.1). This matches the project's
   default stack.

9. **Agent-facing contract: value + provenance + status.** Golden attributes carry the source, rule and
   `resolved|unresolved` status, so 08 can let agents say "I don't know" (source brief, "Imagine the
   ideal").

## Risks / Trade-offs

- [Thresholds tuned on a small pilot overfit] → keep thresholds configurable, store them per run, and re-measure on every new source.
- [Transitive chaining merges distinct entities (R§1.3)] → upper threshold 0.99 by default, cluster split command, and review of clusters with more than N members (N set by the pilot, our assumption).
- [Splink API drift (R§1.3)] → pin the version and run regression tests on a synthetic fixture.
- [Owners overwhelmed by the review queue] → order the queue by match probability and record impact. The review band width is configurable.
- [Conflict register leaks personal data] → store only the candidate values, and apply 07 masking and access control.
- [Check-digit valid but non-existent identifiers] → flag them `syntax_valid` only (R§4 pitfall 9). Existence checks (e.g. VIES) are out of scope.
- [German transliteration causes false matches] → off by default, enabled per field.

## Migration Plan

This is a new capability, so there is nothing to migrate. Roll it out per category in KonMari order
(low-sentiment categories first). Rollback: deactivate the policy version, or drop the run's canonical
rows by `run_id`. Raw values are untouched, so rollback loses no data.

## Open Questions

- Should golden records live only in the catalog, or also be materialized into the warehouse for 08?
- Which ticketing system receives write-back tickets (GitHub issues, Jira, plain CSV)? Default: a free-text reference.
- Is an existence check for VAT IDs (EU VIES) wanted, and how does it fit DSGVO data minimisation?
- Confirm the `phonenumbers` API and the Splink 4 vs 5 choice in the first spike (R§1.2 and R§1.3 gaps).
