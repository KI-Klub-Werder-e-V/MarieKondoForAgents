# Discard pass guide (step 4 of the KonMari-Kit)

KonMari rule 3: finish discarding first. Duplicates, outdated versions and material the pilot does not need confuse an agent, because they rank next to the right source. This step finds that clutter in the pilot corpus, proposes what to do with each item, and lets a named person decide. Nothing changes before a person approves it, archive and exclude can be undone, and every step goes into a tamper-evident decision log.

The kit never deletes anything in your source systems (SharePoint, CRM, file shares). It removes items from the **pilot corpus** (what later steps and agents see) and lists open clean-up actions for the source owners.

You need steps 1 to 3: an approved charter with measured baselines (`konmari charter gate --step discard`) and, per category, a finished joy test (`score` step complete) in the approved category order.

## 1. Who decides what

| Decision | Who | Rule |
|---|---|---|
| Archive, exclude, route to masking, keep | the `approver` of the item's category in the charter (`data_scope.categories`) | not the person who created the proposal |
| Erase | the category approver **and** a stakeholder with the `compliance` role | two different people, then a waiting period (default 14 days) |
| Retention rules | a stakeholder with the `compliance` role (data protection officer) | one rule per category, no "one period for everything" |
| Detector override without calibration | any category approver | with a written reason |

## 2. Detect

```
konmari discard detect --items items.jsonl --as dirk
```

The kit reads only items of assets in your inventory. Items of local file-share assets are found automatically. For other systems export the items to files and list them in a manifest (one JSON object per line):

```
{"asset_uri": "manual://kb/sops", "path": "export/Satzung_v4.txt", "version": "4.1", "effective_date": "2025-03-01"}
{"asset_uri": "manual://kb/sops", "path": "export/Preisliste_2024-03.txt", "logical_id": "preisliste", "version": "2024-03"}
```

Useful metadata: `version`, `logical_id` (same document across versions), `effective_date`, `valid_until`, `source_of_truth: true`, `last_entry` (for retention), `category` (if an item belongs to another category than its asset), `text_path` (pre-extracted text, e.g. for PDFs).

What the detectors look for:

| Flag | How | Proposal |
|---|---|---|
| exact duplicate | same text after Unicode NFC and whitespace clean-up (SHA-256) | archive all but the canonical copy |
| near-duplicate (`minhash`) | MinHash LSH over 5-word shingles, then the exact Jaccard similarity of every candidate | archive |
| contained copy (`containment`) | share of one document's shingles found in another (e.g. an FAQ pasted into a manual) | archive |
| semantic (`semantic`, optional) | cosine similarity of embeddings from a self-hosted multilingual model (`pip install konmari[semantic]`, `semantic: true` in `discard.yaml`) | at most archive, never exclude or erase |
| superseded | newer version in the version history, passed `valid_until` with a newer copy, or "replaced by <document>" in the text | archive |
| off-scope | the item's category is not in the charter | exclude |
| sensitive-review | inventory tier personal, special-category or confidential, or e-mail addresses, phone numbers or IBANs found | route to masking (step 7) |
| review (filename only) | names like `Angebot_final.docx` vs `Angebot_final_v2.docx` | none: a person looks at it |

The canonical copy of a cluster is chosen by source metadata: source-of-truth flag, then effective date, then version number, then completeness. File modification times are ignored because copies and migrations reset them.

The report (`konmari discard report`, also in `.konmari/reports/`) shows the duplicate rate (items in non-canonical positions divided by items scanned), counts per flag, cluster sizes and the calibration status. Evidence stores IDs, scores and parameters, never text excerpts.

## 3. Calibrate the thresholds

The starting thresholds (Jaccard 0.8, containment 0.9, cosine 0.93) are our assumptions, not proven values. Your documents decide. Until a detector is calibrated it creates flags but **no proposals**:

```
konmari discard calibrate --detector minhash --sample 60 -o pairs.csv --as clara   # pairs.csv + pairs.html (side by side)
# fill the label column: duplicate, related-but-distinct or unrelated
konmari discard calibrate --detector minhash --labels pairs.csv --threshold 0.8 --as clara
```

The output shows precision and recall at 0.7, 0.8 and 0.9. At least 50 labeled pairs are needed (our assumption). Without them, an approver can record an override: `--override-reason "..." --as ben`.

## 4. Propose, review, execute

```
konmari discard propose --as dirk
konmari discard list --status pending
konmari discard approve <proposal> --as ben                     # or --action keep --comment "..."
konmari discard reject <proposal> --comment "..." --as ben
konmari discard bulk-approve --action archive --category kb-and-sops --method exact --as ben   # lists the item IDs
konmari discard bulk-approve ... --yes                          # confirms
konmari discard execute --as dirk
```

- **archive**: the item is copied to cold storage (`--archive PATH` or `s3://bucket/prefix`, default `.konmari/archive`), the copy is checked by SHA-256, then the item leaves the corpus. If the checksum does not match, nothing changes.
- **exclude**: the item leaves the corpus; the unchanged source is the copy.
- **route-to-masking**: the item waits as `awaiting-masking` until step 7 registers a masked version.
- **restore**: `konmari discard restore <item> --as ben` brings archived or excluded items back (checksum checked).
- Every item that leaves the corpus produces a lineage event (`.konmari/lineage.jsonl`), and its chunks and embeddings are marked for removal. `konmari discard check-propagation --index ids.json` fails while any of them is still present.

The archive must be readable only by the archive role. A filesystem archive is created with mode 0700 (files 0600) for the user that runs konmari. For S3 or MinIO give only that role a bucket policy with read access. On Postgres the migration creates the role `konmari_app` with `SELECT, INSERT` on `audit_event` and no `UPDATE` or `DELETE`. If the migrating user may not create roles, grant this by hand.

## 5. Retention rules, legal holds, erasure

Retention periods are data that compliance enters, never values built into the kit:

```
konmari retention rule add --category financial-ops-metrics --purpose "booking vouchers" \
  --legal-basis "Art. 6(1)(c) GDPR" --source "§147(3) AO / §257(4) HGB" \
  --start-event end-of-calendar-year-of-last-entry --period P8Y --expiry-action erase --as eva
konmari retention rule approve <rule> --as eva
konmari retention hold add --item <item> --reason "pending dispute" --owner eva --review-date 2027-01-31 --as eva
konmari retention check --as dirk          # schedule it, e.g. weekly (--schedule P7D)
```

Examples of German periods (**examples only, compliance must confirm them for your organisation**): booking vouchers 8 years since the Bürokratieentlastungsgesetz IV (BEG IV, from 2025; §147(3) AO, §257(4) HGB), for banks, insurers and investment firms again 10 years after a later change of the law; annual accounts 10 years; business letters 6 years. These periods changed twice since 2024, which is why the kit keeps them as data with their source.

- `retention check` covers active, archived and restricted items. For each item whose period has ended or ends within 30 days it creates a proposal (erase, or keep for "review"). `discard report` warns when the last check is older than its schedule.
- **Erase** needs an approved rule for the category, the category approver and compliance, and a 14-day waiting period (`konmari discard cancel <proposal>` stops it). A legal hold blocks it. If a retention duty still applies, the item becomes **restricted** instead (Art. 17(3)(b) GDPR, §35(3) BDSG): out of the corpus and every export, kept until the period ends.
- Erasure deletes the corpus entry, the archive copy and the registered derived artifacts, and writes an erasure record without content. The source location stays listed as an open action for the source owner.
- Erasure requests from data subjects: `konmari discard request <item> --action erase --reason "..." --as dirk`.

## 6. Decision log

Every proposal, approval, refusal, execution, restore and erasure is an event in the shared, append-only audit trail, chained with SHA-256.

```
konmari discard history <item>
konmari discard log verify         # exit 1 and the first broken event if a row was changed
konmari discard log export -o decision-log.jsonl
```
