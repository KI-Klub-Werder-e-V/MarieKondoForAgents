# Step 4: discard pass and retention

Full guide: `docs/discard-guide.en.md`.

## What it does

Finds clutter in the pilot corpus: exact duplicates, near-duplicates (MinHash), contained copies,
optional semantic duplicates, superseded versions, off-scope items, sensitive items for masking.
Proposes archive, exclude or route-to-masking; the category approver decides (never the person who
proposed). Archive and exclude are reversible; nothing is deleted at the source. Erasure needs an
approved retention rule, the approver **and** compliance, and a 14-day wait.

## Commands

```
konmari discard detect --items <items.jsonl> --as <you>
konmari discard report
konmari discard calibrate --detector minhash --sample 60 -o pairs.csv --as <you>
konmari discard calibrate --detector minhash --labels pairs.csv --threshold 0.8 --as <you>
konmari discard propose --as <you>
konmari discard list --status pending
konmari discard approve <proposal> --as <category approver>
konmari discard reject <proposal> --comment "<reason>" --as <category approver>
konmari discard execute --as <category approver>
konmari discard restore <item> --as <category approver>
konmari retention rule add --category <category> --purpose "<purpose>" --legal-basis "<basis>" --source "<law>" --start-event <event> --period P8Y --expiry-action erase --as <compliance>
konmari retention check --as <you>
konmari categories step <category> discard --status complete --as <category approver>
```

The items manifest has one JSON object per line: `{"asset_uri": ..., "path": ..., "version": ...,
"effective_date": ..., "logical_id": ...}`. Local file-share items are found automatically.

## Reading the output

- The report shows the duplicate rate, counts per flag, cluster sizes and calibration status.
- An uncalibrated detector creates flags but **no proposals**. Calibration needs ≥ 50 pairs labelled
  by a person (`duplicate`, `related-but-distinct`, `unrelated`). Draft the sheet; do not label it.
- The canonical copy is chosen by source-of-truth flag, effective date, version, completeness.

## Common blockers

- "created proposal ... and may not decide it": the proposer (`<you>`) and the approver must differ.
- Retention periods are data compliance enters with a legal source; never suggest a period as fact.
- `score` not complete for the category: finish the review in step 3 first.
