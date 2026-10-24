# Govern guide (step 7 of the KonMari kit)

After structure and enrich (step 6) the pilot data sits in the catalog as chunks, but with no enforced permissions, no handling of personal data and no record of who accessed what. This step makes sure that

- **source permissions travel with the data** (whoever cannot read a document in SharePoint gets no chunk of it through an agent),
- **personal data is detected and masked** before an agent sees it,
- every agent access is in the **audit trail** (without content),
- and only a **computed gate status** opens a source for step 8 (connect).

This is not legal advice. The templates (Art. 30 record entry, DPIA screening) do not replace your data protection officer's decision.

## 1. Who decides what

| Decision | Who |
|---|---|
| Edit a source policy (new version, waits for approval) | anyone in the charter, usually the `facilitator` |
| Approve a source policy or revert it to an earlier version | `data_owner` or `compliance` |
| Legal basis for special categories (Art. 9, e.g. health data) | `compliance` only |
| Write grant for an agent (expires after 90 days at most) | the category's data owner |
| Confirm, reject or revert a PII finding | `data_steward`, `data_owner` or `compliance` |
| Resolve a pseudonym (e.g. for an Art. 15 access request) | `compliance` only, with a stated purpose |
| Export the audit trail (Art. 15) | `compliance` |

Every decision is in the audit trail with actor, time, before/after and reason.

## 2. Order of commands

```
konmari govern config                                    # check governance.yaml (retention, thresholds)
konmari govern policy seed --as <facilitator>            # one draft per category (tier and owner from the inventory)
konmari govern policy edit support-history --exposure allowed --mask PERSON=pseudonymize --as <facilitator>
konmari govern policy show support-history
konmari govern policy approve support-history --version 2 --reason "pilot" --as <data_owner>
konmari govern agent faq-bot --scope read:support-history --as <facilitator>
konmari govern acl-sync --as <id>                        # read permissions from the sources
konmari govern scan --as <id>                            # find personal data (local, Presidio)
konmari govern review list                               # uncertain findings, then review confirm|reject <finding> --as <id>
konmari govern label export --category support-history -o labels.csv --as <id>   # label 200 chunks by hand
konmari govern label import labels.csv --as <id>         # measure recall (exit 1 below 0.95)
konmari govern mask --as <id>                            # build the masked agent index
konmari govern leak-test --suite leak-suite.yaml --as <id>
konmari govern check --as <id>                           # compute and record the gate
konmari govern status                                    # what may be connected?
konmari govern ropa -o ropa-entry.yaml                   # Art. 30 record entry
konmari audit verify                                     # verify the audit trail of all steps
```

Load the language models once: `uv run python -m spacy download de_core_news_lg` and `uv run python -m spacy download en_core_web_lg`. Without them `scan` stops instead of downloading anything.

## 3. Source policy

A policy covers one category: exposure to agents (`allowed` or `blocked`), sensitivity tier, masking per entity (`redact`, `mask`, `replace-with-type`, `pseudonymize`), ACL sync cadence (default `PT24H`), permitted tools, owner. For sources whose permissions the kit cannot read (legacy file share, CSV), name the principals by hand: `--allow group:office`.

Every change starts as `pending`; the approved version stays in force until approval. Revert: `konmari govern policy revert <category> --to 1 --as <data_owner>` creates a new, immediately active version with the old content.

## 4. Permissions

`acl-sync` reads permissions per document (SharePoint via Microsoft Graph, file share via file permissions, Postgres via table grants, CSV/CRM exports from the `acl` column of the item manifest). Groups are stored, not their members. Every chunk inherits its document's permissions. Where nothing can be read, `acl_status = unknown` and nobody gets the chunk.

When a group loses read access in the source, the next `acl-sync` applies that to every chunk and records the revocation. If the last successful sync is older than the cadence, the source becomes `acl_stale` and returns nothing until a sync succeeds.

## 5. Personal data

`scan` stores entity type, position and score per finding, never the text. Mid-score findings (between `pii.low` 0.4 and `pii.high` 0.85) go to the review list and are masked until decided. A rejection takes effect on the next `mask`, and so does a revert.

Health data and other special categories (Art. 9) stay out of the index unless the policy records a legal basis approved by `compliance`.

Pseudonyms such as `<PERSON_7f3a9c01>` remain personal data. The key lives in its own schema `konmari_keys`, which no agent can read. There is no reverse lookup table: `konmari govern reidentify <token> --purpose "Art. 15 request" --as <compliance>` searches the original chunks and records the lookup.

Recall: in the exported sample (default 200 chunks) mark every person, email, phone number and IBAN in the `labels` column, e.g. `PERSON=Petra Schulz; IBAN_CODE=DE89 3704 0044 0532 0130 00`. The file holds pilot text: keep it in house and delete it after the import. Below 0.95 for any of these types the source stays blocked (`pii_recall`). When the detector changes, the result is outdated and must be measured again.

## 6. Leak suite and gate

`leak-suite.yaml` lists test principals:

```yaml
principals:
  - {id: board, user: test-board, groups: [Board]}
  - {id: guest, user: test-guest, groups: []}
queries: ["fee increase"]   # optional, on top of the generated ones
```

For each test principal the kit probes for chunks it must **not** see. If one comes back, the suite fails and names chunk, source and principal.

`check` sets the status per source: `approved` only with 100 % ACL coverage, an approved policy with `allowed`, a passed recall gate (where personal data occurs), a passed leak suite for the current policy versions and active audit logging. Nobody can set the status by hand. Step 8 connects `approved` sources only.

Rollback: set the policy to `--exposure blocked` and approve it; the source is blocked at once. Access control and the audit trail always stay on.

## 7. Audit trail

All steps write to one hash-chained table. `konmari audit verify` checks the chain and names the first altered event. Retention: 6 months (`audit.retention_months`), at most the period in your Art. 30 record (`audit.max_retention_months`). For AI Act high-risk systems the kit refuses less than 6 months (Art. 26(6)). `konmari audit purge --as <id>` deletes older events and writes a checkpoint from which the chain stays verifiable. `konmari audit export --subject <person> -o file.jsonl --as <compliance>` returns every event of one person.

On Postgres the roles are separate: `konmari_app` may only insert and read, `konmari_retention` only delete, `konmari_agent` only read the masked index, `konmari_keyholder` the key. If the migrating user may not create roles, an administrator creates them. The hash chain shows tampering but does not prevent it against a database superuser.

Checklists for DPIA screening and the AI Act: [govern-checklists.en.md](govern-checklists.en.md). Interface for step 8: [governance-contract.md](governance-contract.md).
