# Step 3: joy test (score and review)

Full guide: `docs/scorecard-guide.en.md`.

## What it does

Checks every in-scope asset against six criteria (0–3 or `unknown`): authoritative and current,
owned, meaning clear, retrievable, conflicts resolved, permissions explicit. Proposes **keep**
(all ≥ 2, nothing unknown), **fix** (with fix items and the step that fixes them) or **discard**
(irrelevant, redundant, stale and unowned). A reviewer of the category decides. Nothing is changed
at the source; discard is only a recommendation for step 4.

## Commands

```
konmari score evidence <asset> --kind acl_snapshot --data '{"principals": ["<group>"]}' --ref <ref> --as <you>
konmari score evidence <asset> --kind stable_id --data '{"present": true}' --ref <ref> --as <you>
konmari score irrelevant <asset> --reason "<reason>" --as <you>
konmari score --as <you>
konmari review list --status proposed
konmari review accept <asset> --as <category reviewer>
konmari review accept --all-keep --category <category> --as <category reviewer>
konmari review override <asset> --to fix --justification "<reason>" --as <category reviewer>
konmari score fix-done <asset> <criterion> --as <you>
konmari scorecard report --format md
```

## Reading the output

- `score` prints one line per asset: `proposed keep|fix|discard (reason): <asset>; fix: <criteria>`.
- Missing evidence is never a pass: the criterion stays `unknown`, so the asset cannot be `keep`.
  Evidence older than 7 days is `stale_evidence`.
- The scorecard report rates each category on six aspects (Format, Metadata, Access, Consistency,
  Update mechanism, Discoverability): `agent_ready` from 80 %, `in_progress` from 40 %.
- When every asset of a category has an approved verdict, its `score` step is complete.

## Explaining to the user

Translate each fix item into a plain action: "nobody owns this file" → name an owner;
"shared with Everyone" → permissions need fixing in step 7; "no description" → glossary in step 6.

## Common blockers

- "not a reviewer of category": only reviewers and the approver from `data_scope.categories`. Hand off.
- Everything `unknown`: record evidence first (ACL snapshot, stable IDs) or accept `fix` for now.
