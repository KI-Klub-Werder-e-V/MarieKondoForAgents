# Step 1: charter, success metrics, baselines

Full guide: `docs/guide.en.md` (German: `docs/guide.de.md`). Example: `examples/verein-faq-assistant/`.

## What it does

Commits the organisation to **one** use case before any data is touched: scope, people and RACI,
data protection screening, the sources and categories in scope (each with one approver), success
metrics with targets, a golden question set and as-is baselines. Every later command calls the
readiness gate, which passes only when the charter and the metric set are approved, the files
match the approved versions and every `must_have` metric has a baseline.

## Commands

```
konmari charter init --template minimal --lang de     # or --template full (comments on every field)
konmari charter validate
konmari charter approve --as <accountable for charter_approval>
konmari metrics validate
konmari metrics approve --as <accountable for metric_approval>
konmari metrics baseline <metric> --value <value> --method "<how it was measured>" --as <metric owner>
konmari metrics baseline <metric> --not-yet-measured --plan-date <YYYY-MM-DD> --as <metric owner>
konmari charter gate --step inventory
konmari charter amend --reason "<reason>" --as <you>  # any change after approval, then approve again
konmari charter diff 1 2
konmari charter log --meeting steering --date <YYYY-MM-DD> --attendees <id,id> --decision "<decision>" --as <you>
konmari charter status
konmari metrics report --format md
```

## Drafting with the user

- One use case per charter. A good first pilot: a named owner who wants it, a process measurable
  today, 3–10 sources, low-risk data (FAQ, manuals, SOPs), not an AI Act high-risk use.
- `stakeholders`: every person with `id`, name, contact, `org_unit`, roles. Required roles:
  `sponsor`, `use_case_owner`, `data_owner`, `compliance`, `agent_user`.
- `raci`: exactly one Accountable per activity; the DPIA decision belongs to `compliance`; a
  `facilitator` (e.g. a volunteer or you on behalf of a consultant) is never Accountable.
- `compliance`: `dpia_screening.result` and `ai_act.classification` are decided by the compliance
  person. `pending` blocks approval, `high` needs `charter approve --as <compliance id> --compliance-signoff`
  first, `prohibited` can never be approved. You explain; you do not decide.
- `data_scope.sources`: `id`, `connector` (`sharepoint`, `sql`, `fileshare`, `csv-import`),
  `locator` (no password), categories. `data_scope.categories`: one `data_owner` approver and at
  least one reviewer each.
- `metrics.yaml`: at least one `must_have` metric each for answer quality, abstention and business
  outcome, plus a data-readiness and a governance metric; `human_approved_decisions` targets 100.
  Targets are the organisation's choice (vendor numbers are claims, not benchmarks).
- Golden set: at least 30 real questions (from agent users or experts, paraphrased so no personal
  data remains), at least 5 expecting `abstain` or `flag_conflict`. You may help word them; the
  questions and answers come from people.

## Reading the output

- `validate` prints every problem as `error: charter.yaml: <field>: <message>`. Fix them all, re-run.
- `approve` prints the version and SHA-256; after that the file is frozen. Editing it makes
  `next` report `changed` and ask for `amend` plus a new approval.
- The gate prints `unmet: ...` lines. A planned baseline lets only the inventory step run.

## Common blockers

- "is not Accountable for charter approval": only the RACI `A` may approve. Hand off.
- Template placeholders `<FILL ...>` left: `validate` lists each one.
- No baseline: measure the as-is process without the agent (e.g. median minutes for 20–50 real
  questions). Never estimate it yourself.
