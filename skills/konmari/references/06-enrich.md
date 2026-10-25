# Step 6: chunk, enrich, glossary

Full guide: `docs/enrich-guide.en.md`.

## What it does

Cuts kept, normalized documents into passages an agent can find and cite (tables kept intact),
attaches a metadata envelope to every chunk and table (source, date, owner, authority, ACL tags,
sensitivity) and keeps an approved business glossary. No language model is called unless switched on.

## Commands

```
konmari chunk run --dry-run --as <you>
konmari chunk run --as <you>
konmari chunk profile set <category> --max-tokens 600 --reason "<reason>" --as <you>
konmari enrich schema load schema.yaml --as <you>
konmari enrich run --as <you>
konmari enrich override <item> --field owner --value <id> --reason "<reason>" --as <data owner>
konmari glossary stewards --steward <id> --as <sponsor or use case owner>
konmari glossary harvest --as <you>
konmari glossary approve <term> --comment "<comment>" --as <other steward>
konmari glossary report
konmari categories step <category> enrich --status complete --as <category approver>
```

## Reading the output

- Chunk report: `refused` items (`not-kept`, `out-of-pilot-scope`, `not-normalized`), token sizes
  (default max 400), chunks above 800 tokens, plain-text fallbacks, parse errors (`needs-review`,
  e.g. a scanned PDF without text), and whether the corpus is small enough for full context.
- Enrich report: every chunk or table `needs-review` and why. Owner and ACL tags are never filled
  automatically. `acl_stale` = permissions could not be read this run.
- Glossary: only approved terms are linked and exported; approval needs a second steward.

## Common blockers

- "not-normalized": mark `standardize` complete for the category first (step 5).
- Missing owner or ACL tags: ask the data owner; record with `enrich override` and a reason.
- LLM context: only with a self-hosted model or a processor the charter allows.
