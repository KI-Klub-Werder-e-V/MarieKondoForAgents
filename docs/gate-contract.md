# Readiness gate contract (for changes c02–c09)

Every pipeline step must call the gate before it reads any source data. If the gate fails, the step stops and does not connect to a source.

## Python

```python
from konmari.charter import gate, scope

res = gate(step, charter_path="charter.yaml", catalog=None, actor=None)
if not res.passed:
    raise SystemExit("\n".join(res.unmet))
# attach res.charter_id and res.charter_version to every record you write
```

- `step`: one of the KonMari step IDs `inventory`, `score`, `discard`, `standardize`, `enrich`, `govern`, `connect`, `evaluate`. Any other value raises `ValueError`.
- `charter_path`: the engagement's `charter.yaml`. Its canonical hash must match the approved version.
- `catalog`: a `konmari.catalog.Catalog`. The default is `connect()`, which reads `$KONMARI_CATALOG` and falls back to `duckdb:///.konmari/catalog.duckdb`.
- `actor`: recorded in the audit trail. The default is the OS user.

It returns a `GateResult(passed, step, charter_id, charter_version, metric_set_version, unmet)`. `unmet` lists every failed condition, not only the first one.

The gate passes only when all of these hold:

1. The latest charter version is `approved`, and `charter.yaml` matches its content hash.
2. The latest metric-set version is `approved`.
3. Every `must_have` metric has a baseline value. A `not_yet_measured` baseline with a plan date is accepted only for `step="inventory"`.

Every call, passed or failed, writes an `audit_event` row with `action = 'charter.gate'`, the step, the result (`passed` or `failed`), the charter version and the unmet conditions.

## CLI

```
konmari charter gate --step <step> [--file charter.yaml] [--json]
```

| Exit code | Meaning |
|---|---|
| 0 | gate passed (`--json` prints the `GateResult`) |
| 1 | gate failed; each unmet condition goes to stderr as `unmet: ...` (or into `unmet` with `--json`) |
| 2 | usage error, for example an unknown step |

## Data scope lookup

Later steps take their sources, in-scope categories, approvers and reviewers only from the latest approved charter version:

```python
sc = scope("verein-faq-assistant")      # raises LookupError if no version is approved
sc.sources                              # [{"id", "connector", "locator", ...}]
sc.approver("policies-and-contracts")   # stakeholder ID of the data_owner approver; KeyError if out of scope
sc.reviewers("kb-and-sops")             # [stakeholder IDs]
sc.stakeholders["ben"]                  # register entry (name, contact, roles)
```

## Audit trail

Write your own decisions with `konmari.catalog.audit(cat, action, actor=..., charter_id=..., charter_version=..., step=..., result=..., details={...})`. `audit_event` is append-only. The catalog layer refuses `UPDATE`, `DELETE` and `TRUNCATE` on it, and on Postgres a trigger refuses them too. Since c04 every event is hash-chained (`seq`, `prev_hash`, `hash` = SHA-256 over the previous hash and the canonical JSON of the event); `konmari.catalog.verify_chain(cat)` returns the first broken event or `None`. Events written before the c04 migration have no `seq` and are outside the chain. c07 `audit-logging` verifies the same chain.

## Category sequence and step order (from c02)

Steps 03–09 work category by category, in the approved order, and call `require_step` before they process a category:

```python
from konmari.categories import StepRefused, handoff, require_step

require_step("kb-and-sops", "score", charter_path="charter.yaml", catalog=None)  # raises StepRefused(reasons)
```

It refuses when the previous KonMari step is not `complete` for that category, when no category order is approved, or when an earlier category in the order has not finished this step and no approver recorded a waiver (`konmari categories step <category> <step> --waive --reason ...`). Categories without accepted assets never hold up later ones. A step marks its own progress with `konmari.categories.store.set_step(cat, scope, category, step, "in_progress" | "complete", actor)`; every change is audited.

`readiness-scorecard` (c03) reads the sequence with `handoff(cat, scope)`. It returns `(categories, [])`, or `(None, reasons)` with `category order not approved`, unassigned assets (no accepted primary category) and incomplete inventory records. Passing completes the `inventory` step for every in-scope category. Overlap hints for c04 come from `konmari.categories.store.overlap_hints(cat, scope, category)`.

## Approved verdicts (from c03)

Later steps act only on approved scorecard verdicts. c04 retention-decisions executes approved `discard`, c08 exposes only approved `keep`:

```python
from konmari.scorecard import effective_verdict

effective_verdict(cat, asset_uri)   # "keep" | "fix" | "discard" | None (never approved)
```

It returns the latest approved verdict (accept, override or reversal) from the append-only `verdict_review` history. `konmari.scorecard.review.status(cat, asset_uri)["status"]` is `proposed` while a re-score waits for review. Evidence for the scorecard is written with `konmari.scorecard.probes.record_evidence(cat, asset_uri, kind, payload, ref, actor, captured_at)`; the kinds and their allowed fields are in `probes.KINDS` (references, hashes, counts and flags only). After a fix, call `konmari.scorecard.engine.complete_fix(cat, asset_uri, criterion, actor)` to re-score.

## Discard pass (from c04)

Later steps read only the **kept items**: the active pilot corpus. Archived, excluded, awaiting-masking and restricted items never appear there.

```python
from konmari.discard import kept_items, register_derived, check_propagation, awaiting_masking, register_masked

kept_items(cat, charter_id)            # c05 normalization, c06 chunking, c08 data products: rows of view kept_item
register_derived(cat, item_id, artifact_id, kind)   # c06/c08: register every chunk, embedding or product row
check_propagation(cat, charter_id, index)           # c09: {item_id: [artifact IDs still present]}; {} = clean
awaiting_masking(cat, charter_id)      # c07 sensitive-data-masking: items handed over for masking
register_masked(cat, scope, item_id, masked_ref, actor)  # c07: the masked version re-enters the corpus
```

- `index` is any object with `present(ids) -> set` (and `delete(ids)` for execution); `konmari.discard.archive.JsonIndex` is the file stub. Without an index every registered artifact of a discarded item counts as present.
- Canonical members: table `cluster` (`family` exact | lexical | semantic, `canonical_item`, `canonical_reason`). c05 can replace the ranking (source-of-truth > effective date > version > completeness) without changing this table.
- Lineage: every transition out of `active` emits an OpenLineage `DatasetEvent` (namespace from the asset URI, name = item ID) with the facet `konmari_discard` {`previous_state`, `state`, `proposal_id`, `derived_artifacts_marked`} to the same transport as c02 (`$KONMARI_LINEAGE_FILE`, default `.konmari/lineage.jsonl`).
- Decision events: view `decision_event` (= `audit_event` rows with `step = 'discard'`); `details` holds `item_id`, `prior_state`, `new_state` and the action, never item content. `konmari discard log export` writes them as JSONL for c07.
- Retention: `konmari.discard.retention.approved_rule(cat, charter_id, category)` and `expiry_date(rule, item)`; c09 freshness monitoring can reuse the `retention_check_run` schedule.
