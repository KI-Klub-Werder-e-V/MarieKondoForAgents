## 1. Catalog schema

- [ ] 1.1 Add migrations for `golden_item` (versioned), `golden_set`, `eval_run`, `eval_result`, `citation_check`, `judge_calibration`, `baseline` tables; test: migration up/down on Postgres and DuckDB
- [ ] 1.2 Add migrations for `monitored_asset`, `check_result`, `incident`, `drift_result` tables linked to source inventory and data products; test: migration up/down
- [ ] 1.3 Wire all approval, override, baseline and incident state changes to the 07 audit log; test: each state change writes one audit entry

## 2. Golden question sets

- [ ] 2.1 Implement `konmari eval items import` (CSV/YAML) with field validation per expected behaviour; test: missing reference answer or source IDs rejected
- [ ] 2.2 Route `user_log` items through 07 masking before storage and set retention date; test: fixture with name and customer number stores only placeholders, no raw copy
- [ ] 2.3 Implement item versioning and `konmari eval items approve`; test: edit creates new draft version, old version unchanged
- [ ] 2.4 Implement set activation checks (≥ 50 items, ≥ 20 % negative, ≤ 50 % synthetic, charter overrides); test: each refusal case and audited override
- [ ] 2.5 Implement retention job deleting expired, non-re-approved items; test: expired item deleted and audited

## 3. Evaluation run

- [ ] 3.1 Implement `konmari eval run` calling the 08 MCP interface as a declared test principal, recording answer, retrieved chunks, citations, behaviour, latency and all versions; test: against a stub MCP server
- [ ] 3.2 Handle unreachable agent: mark run `failed`, keep partial results; test: stub timeout
- [ ] 3.3 Integrate Ragas for faithfulness, context precision and context recall, stored per item and aggregated per run/category; test: fixture with 1 of 4 unsupported claims yields 0.75
- [ ] 3.4 Implement deterministic citation checks (exists, in scope, current SoT version) using 05 and 07; test: superseded source → `stale_source`, out-of-scope → `out_of_scope` plus audit finding
- [ ] 3.5 Implement citation precision/recall via entailment judge; test: hand-labelled fixture
- [ ] 3.6 Implement abstention and conflict scoring plus answer rate on answerable items; test: guessed answer fails with `hallucinated_answer`, over-abstention reported

## 4. Judge calibration

- [ ] 4.1 Pin judge model id/version in run config and record it per run; test: version stored
- [ ] 4.2 Implement `konmari eval calibrate` computing Cohen's kappa and confusion matrix on a held-out human-labelled subset (≥ 50, disjoint from prompt-tuning items); test: known label pairs give expected kappa
- [ ] 4.3 Mark judge metrics `uncalibrated` on kappa < 0.6 or judge change; test: changed version flips status

## 5. Regression gates and baseline

- [ ] 5.1 Implement `konmari eval gate` with default thresholds and charter precedence from 01 success metrics; non-zero exit on failure; test: each threshold and the 0.05 regression rule
- [ ] 5.2 Gate fails on `failed` runs and `uncalibrated` metrics; test: both cases
- [ ] 5.3 Implement `konmari eval baseline approve|revert` with audit; test: promotion and one-command revert
- [ ] 5.4 Add CI example (GitHub Actions) running run + gate on PRs touching pipeline or agent config; test: sample workflow passes on fixture project

## 6. Freshness, quality and drift monitoring

- [ ] 6.1 Build monitored asset registry from 02 source inventory and 08 data products; report `unmonitorable` assets; test: asset without interval reported
- [ ] 6.2 Implement freshness checks with 1×/2× interval tiers and audited owner overrides; test: 7-day interval, 15-day age → `error`
- [ ] 6.3 Implement quality checks (volume change, metadata completeness, duplicate share, schema change); test: volume drop and missing-metadata fixtures
- [ ] 6.4 Implement drift check (built-in topic-share default, optional Evidently adapter) with reference/current window, method and score; test: synthetic shifted question set produces `warn`
- [ ] 6.5 Emit OpenLineage `dataQualityAssertions` facets for every check result; test: event validated against the facet JSON schema
- [ ] 6.6 Optional importer for dbt freshness / Soda results into `check_result`; test: sample `sources.json`
- [ ] 6.7 Add `konmari monitor run` scheduling (cron/systemd timer example); test: dry run on fixture catalog

## 7. Alerts and incidents

- [ ] 7.1 Implement owner-routed notifications (e-mail, webhook) with one incident per asset and check; test: three consecutive failures → one incident, count 3, one notification
- [ ] 7.2 Strip personal-data values from alert payloads using 07 classification; test: customer table alert has no names or IDs
- [ ] 7.3 Implement incident lifecycle (acknowledge, resolve, accept risk with reason), auto-resolve on recovery, escalation after 2 business days; test: each transition and escalation

## 8. Feedback loops

- [ ] 8.1 Expose freshness status and last change timestamp in chunk metadata via 08 interface; test: chunk from `error` asset carries `freshness_status: error`
- [ ] 8.2 Write eval aggregates and monitor results to the 03 readiness scorecard; flag failing categories as `fix`; test: scorecard rows updated with run IDs
- [ ] 8.3 Create 04 discard candidates for assets in `error` > 30 consecutive days, with no automatic archive/delete; test: day-31 fixture creates candidate, asset unchanged
- [ ] 8.4 Propose next category from 02 category map, set only on human confirmation; test: proposal without confirmation leaves active category unchanged

## 9. Documentation

- [ ] 9.1 Write a non-expert guide: building a golden set from real questions with domain experts, reading the run report, handling incidents; review by one non-developer tester
- [ ] 9.2 Document default thresholds, which are sourced and which are assumptions, with links to research/c09-evaluate-and-monitor.md
