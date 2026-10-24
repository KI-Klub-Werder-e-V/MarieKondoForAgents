# Guide: evaluate and monitor (step 9 of the KonMari kit: "evaluate")

After step 8 an agent can read the pilot data. Nobody knows yet whether it answers correctly. This step tests that with real questions, then keeps watching whether the data stays fresh and clean. It has two parts:

- **Evaluate** (`konmari eval`): a set of real questions with expected answers (the *golden set*), a run against the agent, and gates that block a regression.
- **Monitor** (`konmari monitor`): regular checks of every source and data product for freshness, volume, metadata, duplicates and schema changes. Problems go to the accountable owner.

This is not legal advice. Questions taken from real user logs are personal data while they contain names or customer numbers. Your data protection officer sets the retention period (default 12 months, our assumption).

## 1. Who decides what

| Decision | Who |
|---|---|
| Collect questions | domain experts (`data_owner`, `data_steward`) together with agent users (`agent_user`) |
| Approve a question (`eval items approve`) | domain expert or source owner (`data_owner`, `data_steward`) |
| Activate the golden set (`eval set activate`) | anyone in the charter; the kit checks the minimums |
| Make a run the baseline (`eval baseline approve`) | a named person; undo with `eval baseline revert` |
| Different thresholds | only through the charter's success metrics (step 1) |
| Different freshness threshold for a source (`monitor threshold`) | only the source's owner |
| Acknowledge, resolve or accept the risk of an incident | a named person, with a reason |
| Next category | proposed by the kit, confirmed by the `use_case_owner` |

All of this is in the audit trail (`konmari audit verify`).

## 2. Build a golden set from real questions

1. **Collect questions** people actually asked: the office inbox, tickets, chat logs. Questions the project team makes up only test what the team built.
2. **Go through them with a domain expert.** For each question write down the **expected answer** in one sentence (`reference_answer`), the **source** that contains it (`expected_sources`, document or chunk ID from step 6), and the **expected behaviour**: `answer`, `abstain` (the agent should say "I don't know" because the data does not contain it) or `flag_conflict` (two sources disagree; list the leading source first).
3. **Include questions without an answer**: at least 20 % `abstain` or `flag_conflict`, or an agent that always says something will not stand out.
4. **State the origin**: `user_log`, `domain_expert` or `synthetic`. At most half may be `synthetic`.

```yaml
items:
  - {id: q01, question: How much is the annual fee for adults?, origin: domain_expert, category: kb-and-sops,
     expected_behaviour: answer, reference_answer: 120 EUR per year, expected_sources: [fee-rules]}
  - {id: q02, question: Who wins the 2030 tournament?, origin: synthetic, category: kb-and-sops,
     expected_behaviour: abstain}
```

```
konmari eval items import questions.yaml --as clara   # drafts; user_log questions are masked first
konmari eval items approve q01 --as clara             # approve each question
konmari eval set activate --as ben                    # >= 50 questions, >= 20 % without answer, <= 50 % synthetic
```

`user_log` questions go through the step 7 masking before they are stored ("Mrs Jane Doe, customer number KD-482913" becomes "Mrs `<PERSON>`, customer number `<CUSTOMER_NUMBER>`"); who asked is never stored. After the retention date `konmari eval items retention --as <id>` deletes the question unless it was approved again (a new approval extends the date). An edited question becomes a new draft version; the old version stays readable and keeps running until the new one is approved and the set re-activated. Small pilots may lower the minimum in `metrics.yaml` (`golden_set: {min_questions: 30, min_abstention: 5}`), never below 30 questions with 5 negatives; the override is audited.

## 3. Evaluate the agent

The agent is asked through its MCP interface, with the rights of a **test principal** registered as an agent in step 7. `eval.yaml`:

```yaml
agent: {command: [uv, run, python, agent/server.py], tool: ask, version: faq-bot@1.4, timeout_seconds: 30}
principal: {agent: eval-bot, user: test-eval, groups: [support-team]}
judge: {provider: lexical}    # or {provider: ragas, model: ..., version: ..., base_url: ...}
```

The tool returns `answer`, `behaviour` (`answer`, `abstain`, `flag_conflict`), `retrieved` (chunk IDs) and `statements` (each with its `citations`).

```
konmari eval run --as ben     # exit 1 if the agent was unreachable (partial results are kept)
konmari eval report           # per-item results as JSON
konmari eval gate             # exit 1 if a gate fails
```

Reading the report: `faithfulness` (share of statements supported by the retrieved chunks; see `unsupported_claims`), `context_recall` (was the expected source retrieved; see `missing_sources`), `context_precision`, `citation_check` (every citation exists, is permitted for the test principal and is the current version; `stale_source`, `out_of_scope` with an audit finding), `citation_precision` / `citation_recall`, `abstention_rate` (`hallucinated_answer` = the agent guessed) and `answer_rate` (`over_abstention` = the agent is too cautious). Categories that fail a threshold are listed as `fix candidate` and written to the step 3 scorecard. When all pass, the kit proposes the next category from the step 2 order; only `konmari eval next-category --confirm <proposal> --as ben` sets it.

**Judge.** The default `lexical` judge needs no model: a statement is supported when all its content words and numbers occur in the passages. `ragas` uses a language model (`uv sync --extra ragas`; self-hosted, or hosted under a data processing agreement); model and version are pinned. Calibrate it against at least 50 human labels that were not used to tune it (`konmari eval calibrate labels.csv --judge-model ... --judge-version ... --tuning ids.txt --as clara`, CSV `item_id,human,judge`). Below kappa 0.6, or after a model or version change, judge metrics are `uncalibrated` and the gate does not pass them.

**Baseline and CI.** `konmari eval baseline approve <run> --as ben` and `konmari eval baseline revert --as ben`. A metric more than 0.05 below the baseline fails the gate. A GitHub Actions example is in `examples/ci/konmari-eval.yml`.

## 4. Monitor continuously

```
konmari monitor run --dry-run      # show only
konmari monitor run                # check, open incidents, notify
konmari monitor schedule           # cron line (or --format systemd)
```

Every source needs an owner and an update interval from the inventory (step 2); otherwise it is reported as `unmonitorable` and the `use_case_owner` is notified. Freshness warns past 1x the interval and errors past 2x (7-day interval, changed 15 days ago: error); the owner can override (`konmari monitor threshold <asset> --error-after-days 30 --reason ... --as <owner>`). Volume changes over 20 %, more than 5 % chunks without mandatory metadata and more than 5 % duplicates are errors; schema changes warn. Settings live in `monitor.yaml` (`defaults`, per-asset `checks`, `notify` channel email / webhook / log, drift `topics`). Chunks from an asset in `error` carry `freshness_status: error` and its last change timestamp, so the agent can flag the answer or abstain.

**Incidents.** One open incident and one notification per asset and check, however often the check fails. Alerts name dataset, check and observed value, never personal-data values. `konmari monitor incident acknowledge|resolve|accept-risk <id> --reason ... --as <id>`. A recovered check resolves the incident itself (`system`). An `error` left unacknowledged for more than 2 business days escalates to the `use_case_owner`. After 30 days in error the asset is proposed to the discard pass (step 4); nothing is archived or deleted before an approval there.

**Drift.** `konmari monitor drift --questions log.jsonl --as dirk` (lines `{"ts", "text"}`) compares last week's topics with the week before and asks the `use_case_owner` to add golden items for new topics. Drift never blocks. dbt source freshness and Soda results can be imported: `konmari monitor import dbt target/sources.json`.

## 5. Thresholds and where they come from

All values can be changed through the charter's success metrics. Sources: [research/c09-evaluate-and-monitor.md](../research/c09-evaluate-and-monitor.md), section 2.

| Threshold | Default | Basis |
|---|---|---|
| Faithfulness | ≥ 0.80 | assumption, aligned with the promptfoo example (0.8) |
| Citation precision and recall | ≥ 0.80 each | assumption |
| Cited source exists, permitted, current | 100 % | assumption, deterministic check |
| Abstention on negative items | ≥ 0.90 | assumption |
| Answer rate on answerable items | ≥ 0.85 | assumption, guards over-abstention |
| Regression against the baseline | at most 0.05 | assumption |
| Judge kappa | ≥ 0.6 on ≥ 50 items | assumption; sources quote 100–500 labels only as a heuristic |
| Golden set | ≥ 50 items, ≥ 20 % negative, ≤ 50 % synthetic | assumption |
| Freshness | warn at 1x, error at 2x the interval | assumption, dbt warn/error pattern |
| Escalation | 2 business days | assumption, no source |
| Discard proposal | 30 days in error | assumption, no source |
| Retention of user-log questions | 12 months | assumption; the DPO decides |
| Volume, metadata, duplicates, drift | 20 %, 5 %, 5 %, 0.2 | assumptions, changeable in `monitor.yaml` |
