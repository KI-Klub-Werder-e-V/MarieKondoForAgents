# Structure and enrich guide (step 6 of the KonMari-Kit)

After discarding (step 4) and standardizing (step 5) your pilot data is clean, but it is still shaped for people: whole documents, raw tables, short column names. This step cuts documents into passages an agent can find and cite (**chunks**), puts a label on every chunk and table (source, date, owner, authority, permissions, sensitivity: the **metadata envelope**), and gives business words an agreed meaning (**glossary**). Every choice is written to the audit trail and can be undone.

Nothing leaves your premises by default. No language model is called unless you switch it on (see section 6).

You need steps 1 to 5: an approved charter, the inventory (owners, update frequency, sensitivity), the discard pass (only kept items are chunked) and the `standardize` step marked complete for each category (`konmari categories step <category> standardize --status complete --as <id>`).

## 1. Who decides what

| Decision | Who |
|---|---|
| Chunking profile of a category (size, tokenizer, LLM context on/off) | any stakeholder, with a reason |
| Manual metadata value (owner, ACL tags, valid until, ...) | the data owner, with a reason |
| Table and column descriptions | the data owner of the table |
| Glossary stewards | the sponsor or use case owner |
| Approve, reject, deprecate or revert a term; resolve a glossary conflict | a steward who did **not** write that term version (four eyes) |

## 2. Chunk and review the chunking report

```
konmari chunk run --dry-run --as <id>   # computes everything, writes nothing
konmari chunk run --as <id>
```

Read the report:

- **refused**: items that are not chunked and why: `not-kept` (archived or excluded in step 4), `out-of-pilot-scope` (source or category not in the charter), `not-normalized` (step 5 not complete for the category).
- **token sizes**: min, median, p95 and max per chunk. The default maximum is 400 tokens.
- **table chunks**: tables are never mixed with text. Long tables are split between rows and each part repeats the header and caption.
- **chunks above the flag size** (800 tokens): look at them; usually a very long list item or table row.
- **plain-text fallback**: documents without headings, lists or tables were split by a plain splitter. Adding headings at the source gives better chunks.
- **parse errors**: e.g. a scanned PDF without a text layer. Such a document gets the status `needs-review`; decide whether to OCR it outside the kit, replace it, or discard it.
- **full-context feasible**: the whole pilot corpus is below 200,000 tokens, so a long-context model could read it whole instead of searching chunks.

Running the same command again on unchanged documents creates no new chunks. When a document changes, unchanged passages keep their chunk IDs and removed passages are marked `superseded`.

## 3. Override a chunking profile

```
konmari chunk profile show
konmari chunk profile set policies-and-contracts --max-tokens 600 --model-max-tokens 1024 --reason "long clauses" --as <id>
```

The old and new value, your ID and the reason go to the audit trail. To count tokens exactly like your embedding model, set its tokenizer: `--tokenizer hf:/path/to/tokenizer.json`. The default `regex-v1` is an offline approximation.

## 4. Enrich and fix what is missing

```
konmari enrich schema load schema.yaml --as <id>   # tables that agents may read, with column descriptions
konmari enrich run --as <id>
```

The report lists every chunk or table that is `needs-review` and why. Owner and permissions are never filled in automatically: a chunk without an owner or without ACL tags stays `needs-review` until a person decides.

```
konmari enrich override <item-or-chunk-or-table> --field owner --value clara --reason "maintains the FAQ" --as <id>
konmari enrich override <item> --field acl_tags --value "group:Vorstand,group:Büro" --reason "file share has no ACL API" --as <id>
konmari enrich lineage <chunk-id>                  # where a chunk comes from, step by step
konmari enrich schema report                       # columns without description or glossary link (exit 1)
```

Manual values survive later runs. `acl_stale` means the source could not be asked for permissions in this run; the previous tags are kept and access control (step 7) denies those chunks until the next successful run. `expired` means `valid_until` is in the past.

A `schema.yaml` looks like this:

```
tables:
  - table: manual://crm/invoices
    created_at: 2024-05-01T00:00:00Z
    acl: {allow: ["group:Geschäftsstelle"]}
    columns:
      - {name: kd_nr, description: Kundennummer, type: text, nullable: false}
      - {name: kd_stat, type: text, nullable: false, term: Kundenstatus}
      - {name: umsatz, description: Jahresumsatz, type: decimal, unit: EUR, nullable: true}
```

## 5. Approve glossary terms

```
konmari glossary stewards --steward clara --steward ben --as anna
konmari glossary harvest --as <id>            # proposes frequent acronyms and words as drafts
konmari glossary add --file term.yaml --as <id>
konmari glossary submit <term> --as <id>
konmari glossary approve <term> --comment "ok" --as <other steward>
konmari glossary report                       # coverage, terms per status, open conflicts
konmari glossary export --format skos -o glossary.ttl
```

A term file:

```
labels:
  pref: {de: Kündigungsquote, en: churn rate}
  alt: {de: [Churn-Rate]}
definition: Anteil der Mitglieder, die im Jahr kündigen.
owner: ben
category: customer-records
metric: {formula: "gekündigt / Mitglieder", grain: year, unit: "%", owner: ben}   # only for metrics
```

Only approved terms are linked to chunks and columns and exported. Editing an approved term creates a new version in review; the approved one stays visible until the new one is approved. Conflicts (the same label in two terms, or columns with different units linked to one term) are never resolved automatically: `konmari glossary conflict <id> --decision "..." --as <steward>`.

## 6. Language models (opt-in only)

LLM context for chunks and AI-drafted definitions are off by default. If you switch them on, use a self-hosted model or a processor with a data processing agreement (DSGVO Art. 28):

```
konmari chunk profile set kb-and-sops --llm-context on --reason "test contextual retrieval" --as <id>
konmari enrich run --llm-url http://localhost:11434 --llm-model llama3 --as <id>
```

Chunks tagged `personal` or `special-category` are never sent unless masking (step 7) has been applied. Generated text is labelled with the model name, and AI-drafted definitions stay drafts until a steward approves them.

## 7. Roll back

```
konmari chunk rollback <run-id> --reason "profile too small" --as <id>
konmari enrich rollback <run-id> --reason "..." --as <id>
konmari glossary revert <term> --to 1 --reason "..." --as <steward>
konmari glossary deprecate <term> --replaced-by <new-term> --comment "merged" --as <steward>
```

A chunk rollback makes the previous chunks active again. An enrichment rollback restores the previous metadata values. Deprecated terms stay resolvable and point to their replacement.
