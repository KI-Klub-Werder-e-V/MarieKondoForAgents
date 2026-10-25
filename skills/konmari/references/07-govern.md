# Step 7: govern

Full guide: `docs/govern-guide.en.md`. Checklists: `docs/govern-checklists.en.md`.

## What it does

Source permissions travel into every chunk, personal data is found locally and masked before an
agent sees it, every agent access goes into the hash-chained audit trail, and a **computed** gate
status (never set by hand) opens a source for step 8.

## Commands

```
konmari govern config
konmari govern policy seed --as <you>
konmari govern policy show <category>
konmari govern policy approve <category> --version <n> --reason "<reason>" --as <data owner>
konmari govern agent <agent> --scope read:<category> --as <you>
konmari govern acl-sync --as <you>
konmari govern scan --as <you>
konmari govern review list
konmari govern label export --category <category> -o labels.csv --as <you>
konmari govern label import labels.csv --as <you>
konmari govern mask --as <you>
konmari govern leak-test --suite leak-suite.yaml --as <you>
konmari govern check --as <you>
konmari govern status
konmari audit verify
konmari categories step <category> govern --status complete --as <category approver>
```

## Reading the output

- `govern status`: per source `approved` only with 100 % ACL coverage, an approved `allowed`
  policy, a passed PII recall gate (≥ 0.95 where personal data occurs), a passed leak suite and
  active audit logging. Everything else names what is missing.
- `scan` stores entity type, position and score, never text. Mid-score findings go to review.
- Leak suite: names chunk, source and principal for every chunk a test principal must not see.

## Common blockers

- Recall sample: a person marks every PERSON, EMAIL, PHONE and IBAN in `labels.csv`. The file holds
  pilot text; it stays in house and is deleted after the import. Do not label it yourself and do
  not send it anywhere.
- Special categories (Art. 9) stay out unless compliance records a legal basis.
- spaCy models: `uv run python -m spacy download de_core_news_lg` and `en_core_web_lg`.
