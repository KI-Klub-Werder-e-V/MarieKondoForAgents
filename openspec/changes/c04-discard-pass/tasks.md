## 1. Catalog schema and setup

- [ ] 1.1 Add catalog migration for tables `discard_run`, `fingerprint`, `flag`, `cluster`, `calibration_label`, `proposal`, `decision_event`, `archive_manifest`, `retention_rule`, `legal_hold`, `erasure_record` (Postgres and DuckDB); migration up/down passes in CI
- [ ] 1.2 Grant insert-only access on `decision_event` to the application role; a test asserts that UPDATE and DELETE are denied
- [ ] 1.3 Add `datasketch` as a core dependency and `sentence-transformers` as the optional extra `konmari[semantic]`; `pip install konmari` works without the extra
- [ ] 1.4 Add `konmari discard` and `konmari retention` CLI command groups with `--help` text

## 2. Pilot scoping and exact duplicates

- [ ] 2.1 Implement the charter/inventory guard: `detect` refuses without an approved charter and records the charter ID and inventory snapshot ID (test both scenarios)
- [ ] 2.2 Implement text normalization v1 (NFC, whitespace collapse, case preserved) with stored version; unit tests cover whitespace-only differences
- [ ] 2.3 Implement SHA-256 fingerprints over normalized text and raw bytes, plus exact-duplicate clustering; fixture test with the same PDF from two connectors

## 3. Near-duplicate, containment and semantic detection

- [ ] 3.1 Implement 5-word shingling and MinHash LSH (128 perms, configurable threshold) with exact Jaccard verification; test that a rejected candidate (0.62 at threshold 0.8) is counted and not flagged
- [ ] 3.2 Implement containment scoring on verified pairs; fixture test with an FAQ embedded in a manual (high containment, low Jaccard)
- [ ] 3.3 Implement optional semantic detection (configurable model, cosine threshold, model ID recorded); test it with a small local model and two translated policy fixtures
- [ ] 3.4 Enforce the cap: semantic-only evidence never yields `exclude`/`erase` proposals (unit test)
- [ ] 3.5 Store evidence without text excerpts; test asserts no flag row contains item content

## 4. Calibration

- [ ] 4.1 Implement `konmari discard calibrate`: stratified sampling of candidate pairs by score, side-by-side diff view, labels `duplicate`/`related-but-distinct`/`unrelated`
- [ ] 4.2 Compute and report precision and recall at 0.7/0.8/0.9 (and the configured threshold); store the selected threshold with the calibrator's name
- [ ] 4.3 Block proposals from uncalibrated detectors (< 50 labels) unless an override with reason exists; test both paths

## 5. Clusters, superseded versions, relevance, sensitive routing

- [ ] 5.1 Build clusters as connected components per method family; canonical ranking source-of-truth > effective date > version > completeness, with the reason stored; test that mtime is ignored
- [ ] 5.2 Implement superseded detection (version history, passed valid-until with a newer cluster member, named replacement); test that a filename-only pair goes to review, not to a flag
- [ ] 5.3 Implement off-scope flagging from charter categories versus the category map; test the payroll item in a support pilot
- [ ] 5.4 Implement `sensitive-review` flags from inventory sensitivity and configured identifier detectors (entity types and counts only); test the inventory-mismatch report
- [ ] 5.5 Implement the run report (Markdown + JSON + catalog) with duplicate rate, counts per flag and calibration status; golden-file test (180/1,200 → 15.0%)

## 6. Proposals, approval and decision log

- [ ] 6.1 Generate proposals from flags and scorecard "discard" verdicts with plain-language reasons; test that proposing changes no data
- [ ] 6.2 Implement the hash-chained append-only event writer and `konmari discard log verify`; test that a tampered row is detected at the right position
- [ ] 6.3 Implement approve/reject/change-action with role check against the charter's category approvers and a proposer ≠ approver rule; tests for an unauthorized approver and a changed action
- [ ] 6.4 Implement bulk approval restricted to the same action+category+method with an item-ID preview and explicit confirmation; test it
- [ ] 6.5 Implement the per-item history query; test the archive→restore sequence

## 7. Execution: archive, exclude, masking hand-off, restore, propagation

- [ ] 7.1 Implement the archive target adapter (filesystem path and S3-compatible bucket) with archive-role-only access documented and tested against MinIO in CI
- [ ] 7.2 Implement archive execution: copy, verify SHA-256, manifest entry with retention rule, then remove from corpus; test that a checksum mismatch aborts and leaves the item `active`
- [ ] 7.3 Implement `exclude` and `route-to-masking` (state `awaiting-masking`, excluded from all exports) with the hand-off interface to change 07
- [ ] 7.4 Implement `konmari discard restore` with checksum verification and erasure-record refusal; test both scenarios
- [ ] 7.5 Emit OpenLineage-style events on every transition out of `active`, mark derived artifacts, and implement `check-propagation` (non-zero on leftover chunks); integration test with a stub index

## 8. Retention rules, holds, restriction, erasure

- [ ] 8.1 Implement `konmari retention rule add/list/approve` with the fields purpose, legal basis, source, start event, period, expiry action and approver; reject wildcard rules; test it
- [ ] 8.2 Compute per-item expiry dates from the rule start event (e.g. end of calendar year of last entry); unit tests at year boundaries
- [ ] 8.3 Implement legal holds (item/category, reason, owner, review date) and refuse erasure under a hold; test it
- [ ] 8.4 Implement the `restricted` state for erasure-due items inside a retention period; test the invoice-in-8-year-period scenario
- [ ] 8.5 Implement erasure: two-person approval (category approver + DPO/compliance), configurable waiting period (default 14 days) with cancel, deletion from corpus/archive/derived artifacts, content-free erasure record listing source location as open action; tests for a single approval and a completed erasure
- [ ] 8.6 Block `erase` for categories without an approved rule; test it
- [ ] 8.7 Implement `konmari retention check` (active, archived, restricted; expiry window default 30 days) that creates proposals per rule, records its last run, and adds an overdue warning in `discard report`; tests for an archived item expiring and an overdue check

## 9. End-to-end and documentation

- [ ] 9.1 End-to-end test on a synthetic German pilot fixture (duplicates, versions, off-scope, personal data): detect → calibrate → propose → approve → execute → restore → retention check, then assert the decision log verifies
- [ ] 9.2 Write the user guide "Discard pass" for non-experts: workflow, approver roles, the thresholds and why they are calibrated, example German retention rules marked as examples with sources (BEG IV and its reversal for financial firms) and the note that compliance must confirm
- [ ] 9.3 Document the interfaces consumed by changes 05, 07, 08 and 09 (kept-item view, masking hand-off, lineage events, propagation check)
