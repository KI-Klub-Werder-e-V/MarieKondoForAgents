## 1. Catalog schema and rubric

- [ ] 1.1 Add forward and down migrations for `rubric_version`, `criterion_score`, `verdict`, `verdict_review` (append-only) and `category_maturity`. Test: migrate up, migrate down, then migrate up again on Postgres and DuckDB
- [ ] 1.2 Ship the default rubric as YAML: six criteria, 0–3 level descriptors, gate flags on `authoritative_current` and `permissions_explicit`, DAMA/ISO 25012/FAIR mapping. Test: loader validates it against a JSON Schema
- [ ] 1.3 Make published rubric versions immutable. Test: an update attempt on a published version raises an error
- [ ] 1.4 Add pilot config for thresholds (keep level 2, stale multiple 2×, agent-ready 80 %, in-progress 40 %, evidence max age 7 days), stored per run. Test: an override in config shows up in the run record

## 2. Pilot scoping

- [ ] 2.1 Load the active approved pilot charter (01) and in-scope assets from source-inventory/category-map (02). Test: an out-of-scope asset gets no scores and is listed in the run summary
- [ ] 2.2 Exit non-zero with a clear message when no approved charter exists. Test: CLI exit code and zero rows written

## 3. Evidence probes

- [ ] 3.1 Define the probe plugin interface (name, criterion, run(asset) → level|unknown, evidence_ref, evidence_at)
- [ ] 3.2 `authoritative_current` probe (last-modified vs. expected frequency, superseded flag). Test: the 30-day/75-day fixture yields level ≤ 1
- [ ] 3.3 `owned_maintained` probe (owner present and active). Test: a missing owner yields level 0
- [ ] 3.4 `meaning_clear` probe (description present, glossary coverage share). Test: fixtures at 0 %, 50 % and 100 % coverage map to the expected levels
- [ ] 3.5 `retrievable_precise` probe (stable ID present; consumes 09 hit rate@k/MRR if available, else proxy). Test: no evidence yields `unknown`
- [ ] 3.6 `conflicts_resolved` probe (open clusters from 04 dedup-detection). Test: an open cluster yields level ≤ 1
- [ ] 3.7 `permissions_explicit` probe (detect Everyone / EEEU / anonymous links in the ACL snapshot, store the capture timestamp). Test: an EEEU fixture yields level 0
- [ ] 3.8 Evidence expiry: flag `stale_evidence` and re-run the probe before proposing. Test: an old evidence timestamp triggers a re-run
- [ ] 3.9 Evidence minimisation: store refs, hashes and counts only. Test: scan stored evidence for fixture text from a `personal` asset and find no match
- [ ] 3.10 Optional adapters to ingest Soda Core check results and ODCS contract status as evidence. Test: a sample result file maps to probe rows

## 4. Verdict engine

- [ ] 4.1 Implement the discard rules (irrelevant, redundant with winner, stale beyond the multiple without an owner). Test: one fixture per reason
- [ ] 4.2 Implement keep/fix with gates and `unknown` handling. Test: the five-3s-plus-gate-0 fixture yields `fix`, and all ≥ 2 yields `keep`
- [ ] 4.3 Generate fix items per failing criterion, routed to the downstream capability (e.g. permissions → access-control). Test: the routing table covers all six criteria
- [ ] 4.4 Property test: no asset with a gate at 0 or any `unknown` ever gets `keep`

## 5. Human review and audit

- [ ] 5.1 `konmari review list|accept|override|reverse`, with reviewer authorisation checked against the pilot charter. Test: an unauthorised user is rejected and the attempt is logged
- [ ] 5.2 Require a justification on override. Test: an override without justification is rejected and the status stays `proposed`
- [ ] 5.3 Append-only review history with reversal and latest-approved resolution. Test: after a reversal the history has 3 rows and the effective verdict is the latest
- [ ] 5.4 Emit review actions to 07 audit-logging and runs as OpenLineage-style events. Test: event payload contains actor, from, to, rubric version
- [ ] 5.5 Bulk accept limited to `keep` proposals without `unknown`. Test: bulk accept skips `fix` and `discard`
- [ ] 5.6 Verify that the scorecard never mutates source systems. Test: connector mock asserts no write calls during score and review

## 6. Re-scoring

- [ ] 6.1 Re-score triggers: inventory change, fix item completed, freshness alert from 09. Test: completing a fix re-scores and resets the verdict to `proposed` when it changes

## 7. Category maturity and reports

- [ ] 7.1 Implement the aspect → criteria mapping and share/state computation, counting `unknown` as not meeting the bar. Test: the 10-asset/9-pass fixture yields 90 % `agent_ready`
- [ ] 7.2 `konmari scorecard report` in Markdown, CSV and JSON, with the aggregation rule printed. Test: the JSON output validates against the shipped schema
- [ ] 7.3 Markdown report shows all six criterion levels per asset. Test: snapshot test
- [ ] 7.4 Refuse cross-rubric-version comparison without `--allow-rubric-mismatch`. Test: CLI error without the flag

## 8. Documentation and end-to-end

- [ ] 8.1 Non-expert guide: running the joy test, reading the scorecard, reviewing verdicts (DSGVO notes included)
- [ ] 8.2 End-to-end test on a fixture pilot (one category, about 10 assets covering keep/fix/discard): score → review → report, checked against the expected outputs
