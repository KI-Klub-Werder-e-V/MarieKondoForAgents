## Context

At this point in the KonMari sequence the pilot charter (01) fixes purpose, in-scope categories and approver roles. The inventory and category map (02) list every item the pilot needs, with owner and sensitivity, and the scorecard (03) has marked items "keep", "fix" or "discard". Nothing has been normalized, chunked or exposed yet, so this is the cheapest moment to remove clutter. Research for this change is in `research/c04-discard-pass.md` (cited below as R§n).

Constraints: open source and self-hostable (Python 3.12 CLI `konmari`, Postgres catalog, DuckDB for local runs); users are often non-experts in small organizations; DSGVO and German retention law apply; every step writes auditable decisions to the catalog.

Stakeholders: data owners and domain experts (approve discards, label calibration pairs), the data protection officer or compliance (approve retention rules and erasures), and the person running the pilot (operates the CLI).

## Goals / Non-Goals

**Goals:**
- Find exact duplicates, near-duplicates, contained copies, superseded versions, off-scope items and items needing sensitive review in the pilot inventory, with evidence a person can check.
- Turn findings into proposals that only a named, authorized person can execute; archive and exclude stay reversible; every step is in a tamper-evident log.
- Encode retention duties as data per category, so storage limitation (Art. 5(1)(e)) and Art. 17 duties are checked on a schedule, including for archived items.

**Non-Goals:**
- Deleting data in source systems (SharePoint, CRM, file shares). We report open actions to source owners.
- Masking or anonymization logic (owned by `sensitive-data-masking`, change 07).
- Resolving conflicting *facts* between kept sources (owned by `source-of-truth-policy`, change 05).
- Organization-wide cleanup outside the pilot inventory.
- Legal advice: the tool stores retention rules that compliance approves; it ships no authoritative periods.

## Decisions

### D1. Detection cascade: exact → lexical → semantic (optional)
SHA-256 over normalized text runs first, because it is cheap and has no false positives. MinHash LSH (datasketch) over 5-word shingles follows, with exact Jaccard verification of each candidate, because LSH is approximate near the threshold (R§1.2, [S1, S2]). Containment is computed on verified pairs to catch documents pasted into larger ones (Broder, R§1.2). Embedding similarity is an optional extra (R§1.3).
- *Alternative: SimHash (64-bit, Hamming ≤ 3).* Compact, but its validated parameters are for web pages, and a comparison found shingling detects more duplicate types (R§1.2, [S6, S6b]). Rejected as the default, kept as a possible future detector.
- *Alternative: NeMo Curator SemDeDup.* Needs a GPU and is built for web-scale corpora (R§1.3). Pilot corpora are in the thousands of items, so plain numpy/FAISS cosine search with sentence-transformers is enough.

### D2. Starting thresholds are assumptions; calibration gates proposals
Defaults: Jaccard 0.8, containment 0.9, cosine 0.93 (from NeMo's `eps=0.07` example). Only the cosine default comes from a source, and even that source gives no universal value (R§2). So the spec requires a labeled calibration with ≥ 50 pairs per detector (our assumption) or a named override before a detector can produce proposals. Precision and recall per threshold come from the labels, not from literature.
- *Alternative: ship fixed thresholds.* Rejected: corpora differ (German SOPs vs. CRM notes), and the datasketch optimizer itself is imprecise (R§1.2).

### D3. Semantic evidence is capped at `archive`
Two policy versions that differ in one number embed almost identically (R§1.3, R§4). A semantic flag therefore never leads to `exclude` or `erase` on its own, so an error stays reversible.

### D4. Canonical choice and superseded detection prefer source metadata
Ranking: explicit source-of-truth designation, then effective date, then source version number, then completeness. Filesystem mtime is not used, because copies and migrations reset it (R§4). Filename patterns only add evidence (R§1.4). This order is compatible with what change 05 formalizes as `source-of-truth-policy`. Change 05 can replace the ranking without changing this spec's interface.

### D5. Five actions, with reversibility matching each one's risk
`keep` < `archive` (copy verified, then removed from corpus) < `exclude` (removed, source untouched) < `route-to-masking` (held until change 07 registers a masked version) < `erase` (irreversible). Real erasure cannot be reversible, so it gets two-person approval (item approver plus DPO/compliance), a waiting period with cancel (default 14 days, our assumption), and an erasure record without content (R§4). The source system is never modified, because that requires the source owner's own processes.
- *Alternative: soft-delete flag only.* Rejected: it does not meet Art. 17 once erasure is actually due, and data kept "deleted" stays in DSGVO scope.

### D6. Append-only, hash-chained decision log in Postgres
Each event stores a SHA-256 over (previous hash + canonical JSON of the event). A `log verify` command recomputes the chain. Postgres is already in the stack. Insert-only grants plus the chain make tampering detectable without a new service. The format is exported for change 07's `audit-logging`, which owns retention and access of logs. This supports Art. 5(2) accountability and ISO 15489's documented disposition decisions (R§1.7).
- *Alternative: an external ledger or WORM store.* Overkill for a pilot; it can be added later behind the export.

### D7. Cold storage = any S3-compatible bucket or a filesystem path, plus a manifest
Copy, verify the checksum, then remove from the corpus. The manifest lives in the catalog. MinIO ILM tiering is optional; its exclusive-access and metadata-backup caveats (R§1.8, [S20]) argue for a plain bucket in a pilot. Archived personal data stays in scope: each manifest entry carries the retention rule, and `retention check` scans the archive (R§1.8).

### D8. Retention rules are data, per category, approved by compliance
Rule fields follow the DIN 66398 structure (data type/category → deletion class → standard period with start event; R§1.7, [S17, S18]). A wildcard rule is rejected because the EDPB coordinated action named one-period-for-everything as a common failure (R§1.7, [S8]). German periods (8 years for booking vouchers since BEG IV, 10 for annual accounts, 6 for business letters, 10 for vouchers again at banks, insurers and investment firms) appear only in documentation examples, never as code constants, because they have changed twice since 2024 (R§1.7, [S15, S16]).

### D9. Restriction instead of erasure is a first-class state
`restricted` implements Art. 17(3)(b) DSGVO and §35(3) BDSG (R§1.7, [S14]): the item leaves every agent-facing path, but it is kept until its retention end date. The expiry check then proposes erasure.

### D10. Propagation is checked, not assumed
Vector indexes keep chunks of removed documents unless deletions are reconciled against the source of truth (R§1.4, [S9]). Every transition out of `active` emits an OpenLineage-style event, and a `check-propagation` command fails on any derived artifact still present. Change 09 reuses it in challenge queries.

## Risks / Trade-offs

- [Calibration fatigue: experts won't label 50 pairs] → The sampler stratifies by score, so labels concentrate near the threshold. The CLI shows pairs side by side with a diff. Named override with reason is the escape hatch.
- [Over-eager discard of "related but distinct" documents] → Semantic evidence is capped at `archive` (D3). Restore is one command. The duplicate rate and rejected-proposal rate are reported per run.
- [Personal data leaks into evidence or logs] → Evidence stores IDs, scores and entity counts only, never excerpts. The erasure record keeps no content.
- [Retention rules wrong or outdated] → Rules carry their source and approver. Erasure is blocked without an approved rule. The docs flag BEG IV and its partial reversal as examples of change.
- [Archive becomes a forgotten attic] → The retention check covers the archive. The report warns when the check is overdue.
- [Source systems still hold the clutter] → Out of scope by design. Open actions per source owner are listed so the organization can clean at the source.
- [Hash chain gives detection, not prevention] → Accepted for the pilot. Insert-only DB grants plus export to change 07's audit store.
- [Embedding model download conflicts with self-hosting or offline use] → Semantic detection is optional, the model is configurable and can run locally, and lexical detection covers most cases.

## Migration Plan

New tables and commands only; no existing data is migrated. Deploy: run the catalog migration, then `konmari discard detect` → `calibrate` → `propose` → review/approve → `execute`. Rollback: `konmari discard restore` for archived and excluded items. The migration's down step drops the new tables only after an export of the decision log.

## Open Questions

- Should approvers review in the CLI only, or is a minimal web review page needed for non-technical data owners? (Could reuse change 08's interfaces.)
- Default embedding model for German-heavy corpora: which multilingual model gives the best calibration results on real pilot data?
- Final status of EDPB Guidelines 01/2025 and the CJEU EDPS v SRB ruling: does it change how masked items routed to change 07 are classified? (R§1.6)
- Is a 14-day erasure waiting period acceptable to DPOs, or should it align with the one-month response window for data subject requests?
- Is DIN 66398's current DIN status (withdrawn vs. current) relevant enough to cite it in user documentation? (R§1.7)
