## Why

KonMari rule 3 says "finish discarding first": organizing junk wastes effort, and for agents it is worse, because duplicates, superseded versions and off-topic material rank alongside the authoritative source and make answers contradictory or wrong. Once the pilot's sources are inventoried (02) and scored (03), the organization needs a safe, repeatable way to remove that clutter from the pilot corpus. Every decision has to be approved by a person, reversible where the law allows, audited, and consistent with DSGVO storage-limitation and erasure duties.

## What Changes

- New `konmari discard` pipeline step that runs on the pilot's inventoried items only:
  - exact duplicate detection via content hashing over normalized text;
  - lexical near-duplicate and containment detection via MinHash LSH with verified Jaccard / containment scores;
  - optional semantic near-duplicate detection via embeddings with a calibrated cosine threshold;
  - superseded-version detection from source version metadata, effective dates and naming signals;
  - relevance flagging against the pilot charter's purpose and the category map.
- Duplicate clusters with a proposed canonical member and the evidence behind every flag.
- A discard decision workflow. The tool proposes one of `keep`, `archive`, `exclude`, `route-to-masking` or `erase`. Only a named human approver can execute a proposal, and every proposal, approval, execution and restore is written to the catalog's decision log.
- Reversible archive to an access-restricted cold-storage target with a manifest, a restore command, and propagation of discards to downstream indexes.
- A retention rule register per data category: legal basis, rule source, start event and period, legal holds, and restriction instead of erasure where a retention duty applies (Art. 17(3)(b) DSGVO, §35(3) BDSG). Includes scheduled checks that surface expired items, including archived ones.
- Erasure as a separate, stronger path: two-person approval, a waiting period, and an erasure record that keeps no personal content.

## Capabilities

### New Capabilities
- `dedup-detection`: exact, near-duplicate, containment, semantic and superseded-version detection plus relevance flagging over the pilot corpus. Covers threshold calibration, duplicate clusters with canonical candidates, and the evidence stored for every flag.
- `retention-decisions`: human-approved, reversible, audited discard decisions (keep / archive / exclude / route-to-masking / erase), cold-storage archive and restore, per-category retention rules with legal holds and restriction, scheduled expiry checks, and downstream propagation of discards.

### Modified Capabilities
<!-- none: no existing specs in openspec/specs/ -->

## Impact

- **Upstream dependencies:** `c01-pilot-charter` (`pilot-charter` purpose, scope and approver roles; `success-metrics` baseline such as duplicate rate), `c02-category-inventory` (`source-inventory` items with owner, sensitivity and update frequency; `category-map` categories used for retention rules and relevance), `c03-joy-test-scorecard` (`readiness-scorecard` "fix or discard" verdicts are an input to discard proposals).
- **Downstream consumers:** `c05-standardize-source-of-truth` (normalization and `source-of-truth-policy` run only on items kept here, and reuse the canonical-member choice), `c06-structure-and-enrich` (chunking skips discarded items), `c07-governance` (`sensitive-data-masking` receives `route-to-masking` items; `audit-logging` and `access-control` govern the decision log and archive), `c08-connect-agent-interfaces` (data products and MCP exposure include only kept items), `c09-evaluate-and-monitor` (challenge queries verify discarded items no longer surface; `freshness-monitoring` reuses the expiry check schedule).
- **Code:** new `konmari discard` and `konmari retention` CLI command groups; new catalog tables for fingerprints, clusters, proposals, decisions, retention rules, legal holds and the archive manifest.
- **Dependencies:** `datasketch` (MIT) for MinHash LSH; `sentence-transformers` with a multilingual model as an optional extra for semantic dedup; any S3-compatible bucket or a filesystem path as the archive target.
- **People:** data owners and domain experts approve discards; the data protection officer or compliance approves retention rules and every erasure.
