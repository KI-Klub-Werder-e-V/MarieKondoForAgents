# Joy test guide (step 3 of the KonMari-Kit)

KonMari rule 6: does it spark joy for agents? This step checks every in-scope asset of the pilot against six criteria, proposes **keep**, **fix** or **discard**, and lets a named reviewer decide. Nothing in your sources is changed, moved or deleted. A `discard` is only a recommendation that step 4 (discard pass) executes later with its retention rules.

You need step 2 finished: `konmari categories handoff` must pass, and every `must_have` metric needs a measured baseline (`konmari charter gate --step score`).

## 1. The six criteria

Each criterion gets a level from 0 to 3, or `unknown` when there is no usable evidence:

| Criterion | Question | 0 means | Gate |
|---|---|---|---|
| `authoritative_current` | Authoritative and current? | superseded, or older than 2× the expected update frequency | yes |
| `owned_maintained` | Owned and maintained? | no owner | |
| `meaning_clear` | Business meaning clear? | no description | |
| `retrievable_precise` | Precise chunk or record retrievable? | no stable ID, or never retrieved | |
| `conflicts_resolved` | Conflicts resolved? | open duplicate cluster without a winner | |
| `permissions_explicit` | Permissions explicit? | shared with Everyone, "Everyone except external users" or an anonymous link | yes |

Level 2 means "meets", level 3 "meets and is checked automatically". The full descriptors and the mapping to DAMA, ISO/IEC 25012 and FAIR are in `src/konmari/scorecard/rubric.default.yaml`. That rubric version is fixed once used. A changed rubric needs a new `rubric_version` (`konmari score --rubric my-rubric.yaml`).

## 2. Feed the evidence

The kit reads the inventory itself (owner, steward, update frequency, last change, sensitivity tier). Other facts come from later steps (dedup, glossary, ACL capture, retrieval tests). Until those exist, record them by hand or import them:

```
konmari score evidence <asset> --kind acl_snapshot --data '{"principals": ["ben", "Everyone except external users"]}' --ref acl:2026-10-20 --as dirk
konmari score evidence <asset> --kind glossary_coverage --data '{"described": true, "mapped": 3, "total": 4}' --ref glossary:v2 --as clara
konmari score evidence <asset> --kind stable_id --data '{"present": true}' --ref chunks:run-5 --as dirk
konmari score evidence <asset> --kind dedup_check --data '{"open_clusters": 0}' --ref dedup:run-1 --as dirk
konmari score ingest --soda results.json --as dirk        # Soda Core freshness and duplicate checks (optional)
konmari score ingest --odcs contract.yaml --as dirk       # ODCS data contract: description and glossary coverage
konmari score irrelevant <asset> --reason "not needed for any pilot question" --as clara
```

Missing evidence is never a pass: the criterion stays `unknown` and the asset cannot get `keep`. Evidence older than 7 days is flagged `stale_evidence`. The kit re-captures it where a connector can, otherwise the criterion counts as `unknown`.

## 3. Run the joy test

```
konmari score --as ben
```

The run scores the categories in the approved order and prints one proposal per asset:

- **discard** when an expert marked the asset irrelevant, when another copy is the winner of its duplicate cluster (`redundant`), or when it is older than 2× its update frequency and has no active owner (`stale_unowned`).
- **keep** when all six criteria are at level 2 or higher and none is `unknown`.
- **fix** otherwise, with one fix item per weak criterion and the step expected to fix it (for example `permissions_explicit -> access-control`). A gate at 0 always means fix, however good the rest is.

Assets in categories the charter does not include are listed as out of scope and get no scores.

Thresholds are our assumptions. Change them per pilot in `scorecard.yaml` next to `charter.yaml`. Each run stores the values it used:

```yaml
keep_level: 2
stale_multiple: 2
agent_ready_share: 0.8
in_progress_share: 0.4
evidence_max_age_days: 7
```

## 4. Review the proposals

Only the reviewers and the approver of the asset's category in the charter (`data_scope.categories`) may decide. Attempts by anybody else are refused and logged.

```
konmari review list --status proposed
konmari review accept <asset> --as clara
konmari review accept --all-keep --as clara      # only keep proposals without unknown criteria
konmari review override <asset> --to fix --justification "..." --as clara
konmari review reverse <asset> --to keep --justification "..." --as ben   # change an approved verdict
konmari review list <asset>                       # full history
```

Every proposal, acceptance, override and reversal is a new row. Nothing is overwritten, so the history stays complete. Later steps act only on the latest approved verdict. When all assets of a category are approved, the `score` step of that category is complete and the next category can start.

## 5. Re-score

```
konmari score --changed --as ben                         # assets whose inventory entry changed
konmari score fix-done <asset> meaning_clear --as clara   # a fix item is done
konmari score alert <asset> --status error --ref monitor:17 --as dirk   # freshness alert
```

If the proposal changes, it goes back to `proposed` and needs a new review.

## 6. Read the scorecard

```
konmari scorecard report                       # Markdown
konmari scorecard report --format json -o scorecard.json
konmari scorecard report --format csv --level category
konmari scorecard compare <run_a> <run_b>      # refused across rubric versions without --allow-rubric-mismatch
```

The asset table always shows all six levels next to the lowest level (`Min`). The category view rates the six aspects of the "poorly prepared vs agent-ready" table: Format, Metadata, Access, Consistency, Update mechanism and Discoverability. An aspect's share counts assets whose mapped criteria all reach level 2 (Update mechanism needs `authoritative_current` at 3). `unknown` counts as not reached. From 80 % the aspect is `agent_ready`, from 40 % `in_progress`, below that `poorly_prepared`. The report prints this rule every time.

## Data protection (DSGVO)

- The scorecard stores references, hashes, counts and flags only, never content from your assets (Art. 5(1)(c) GDPR). `konmari score evidence` refuses free text. For an ACL it keeps the broad-grant flags, the number of principals and one hash of the list, not the names.
- Do not paste personal data into review justifications. Write the reason ("duplicate of the printed handbook"), not the content.
- `discard` deletes nothing. Legal retention duties and legal holds are checked in step 4 before anything is archived or erased.
