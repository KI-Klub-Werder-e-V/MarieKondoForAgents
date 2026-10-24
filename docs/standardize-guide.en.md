# Standardize and source-of-truth guide (step 5 of the KonMari-Kit)

After the discard pass, the same fact is still written in different ways (`03.04.2025` and `2025-04-03T00:00+02:00`, `DE 123 456 788` and `DE123456788`, "Müller GmbH" and "MUELLER GMBH", kg and t), and one customer can sit in the CRM, the ERP and a SharePoint list under three IDs. This step makes values comparable, links the records that describe one entity, and lets a named owner decide **which source wins** for each field. Every decision is approved by a person, written to the audit trail and can be undone.

The kit never changes your source systems. Raw values are copied into the catalog next to the canonical value. Corrections at the source happen through a write-back ticket that you name.

You need steps 1 to 4: an approved charter (stakeholders with the `data_owner` role decide), the source inventory (sensitivity tiers) and the discard pass (records it removed are skipped).

## 1. Who decides what

| Decision | Who |
|---|---|
| Normalization spec of a field | written by anyone, versioned; the spec names the field `owner` |
| Quarantined value (resolve, mark unfixable, request a spec change, revert) | the field owner from the spec |
| Pair in the review queue, split or restore a cluster | any stakeholder of the charter (named reviewer) |
| Publish clusters without a known-duplicate list | a stakeholder with the `data_owner` role, with a reason |
| Policy version (approve, roll back) | the owner of every changed rule, who must be a `data_owner` in the charter |
| Conflict (resolve, accept deviation, revert) | the rule owner |

## 2. Write a normalization spec

One YAML file per source in `normalize/`. Each entry describes one source field (`object.column`) and the entity attribute it feeds:

```
fields:
  - {source: erp, field: debitor.stand, entity: customer, attribute: updated_at, type: datetime, owner: ben,
     parser: {pattern: ISO}}
  - {source: crm, field: kontakt.geaendert, entity: customer, attribute: updated_at, type: datetime, owner: ben,
     parser: {pattern: "DD.MM.YYYY HH:mm", zone: Europe/Berlin}}
  - {source: erp, field: debitor.ustid, entity: customer, attribute: vat_id, type: identifier, owner: ben,
     parser: {kind: de.vat}}
  - {source: crm, field: kontakt.telefon, entity: customer, attribute: phone, type: phone, owner: ben,
     parser: {region: DE}}
  - {source: crm, field: kontakt.firma, entity: customer, attribute: legal_name, type: name, owner: ben,
     asset: "manual://crm/kontakte", parser: {transliterate_de: true, strip_legal_forms: true}}
  - {source: erp, field: debitor.liefermenge, entity: customer, attribute: annual_volume, type: quantity, owner: ben,
     parser: {target_unit: kg, decimal_comma: true, allowed_units: [kg, t, g]}}
```

| Type | Canonical value | Parser settings |
|---|---|---|
| `date` | `YYYY-MM-DD`, no time-zone conversion | `pattern` (tokens `YYYY MM DD`), required |
| `datetime` | UTC instant `2025-03-30T12:00:00Z`, original zone or offset kept separately | `pattern` (`ISO` or tokens incl. `HH mm ss`) and `zone`, or `epoch_unit` (`s`, `ms`, `us`) |
| `identifier` | compact form of the python-stdnum kind, flag `syntax_valid` | `kind`, e.g. `iban`, `de.vat`, `eu.vat` |
| `phone` | E.164 (`+49301234567`) | `region`, e.g. `DE` |
| `name`, `text` | Unicode NFC, whitespace collapsed; a separate match key for comparison only | `transliterate_de`, `strip_legal_forms` (both off by default) |
| `quantity` | number and unit stored separately | `target_unit`, `decimal_comma`, `allowed_units`, `vocabulary: ucum` |

`asset` links the field to its inventory record; a `personal` or `special-category` tier marks conflicts and quarantine entries as personal data.

The kit never guesses. A local time without a declared zone, a wall time that repeats or does not exist at a daylight-saving switch, a wrong check digit, an unknown or incompatible unit: these values go to **quarantine** with a reason.

## 3. Normalize

Export the records of each source as JSON lines and run:

```
{"source": "crm", "object": "kontakt", "record_id": "K-0001", "values": {"firma": "Müller GmbH", "telefon": "030 1234567"}}
konmari normalize --records records.jsonl --specs normalize/ --as ben
```

A field without a spec stops the run before anything is written. Running the same input with the same spec version again changes nothing. The output lists, per field, the share of canonical and quarantined values. A field is conformant only when every value read is canonical or quarantined with a reason (`konmari normalize conformance <run>`).

```
konmari normalize quarantine list --as ben
konmari normalize quarantine resolve <id> --value 2025-04-03 --as ben     # or --unfixable / --spec-change
konmari normalize quarantine revert <id> --reason "..." --as ben
konmari normalize contract export -o contract.yaml                         # ODCS v3 data contract for CI
konmari normalize contract check contract.yaml
konmari normalize rollback <run> --reason "..." --as ben                   # drop a run's canonical values
```

## 4. Link records across systems and review pairs

`resolve.yaml` declares, per entity, the attributes to compare, the blocking rules and the Fellegi-Sunter m/u probabilities (see `tests/fixtures/sot/resolve.yaml`). Splink scores every candidate pair:

- at or above 0.99: linked automatically,
- between 0.50 and 0.99: review queue,
- below 0.50: not linked.

The thresholds and the quality floors come from the success metrics when they define `er_upper_threshold`, `er_lower_threshold`, `er_precision` and `er_recall`. Duplicates inside one source come from the discard pass and are not decided again.

```
konmari resolve run --entity customer --config resolve.yaml --as ben
konmari resolve review export --entity customer -o pairs.csv     # queue plus a precision sample of linked pairs
konmari resolve review import pairs.csv --as clara               # label column: match or non_match
konmari resolve publish --entity customer --known known.csv --as ben
konmari resolve split <cluster> --entity customer --records erp:D-S2 --reason "sister companies" --as clara
konmari resolve restore <split> --as clara
```

A `non_match` label is kept: the pair is never linked again automatically. `publish` refuses when fewer than 100 linked pairs (or all, if fewer) are labelled, when precision is below 0.98, or when recall against the owner's known-duplicate list is below 0.95. Without a known-duplicate list a data owner must give an explicit, audited override (`--override-recall-reason`).

## 5. Approve the policy (which source wins)

One file per entity, one rule per field, exactly one strategy each:

```
entity: customer
rules:
  payment_terms: {strategy: source_priority, sources: [erp, crm], tie_breaker: most_recent,
                  timestamp_field: updated_at, owner: ben, rationale: Accounting owns billing terms in the ERP.}
  legal_name: {strategy: owner_decision, tie_breaker: owner_decision, owner: ben, rationale: Trade register check.}
```

Strategies: `source_priority` (ordered `sources`), `most_recent` (needs `timestamp_field`), `most_complete`, `owner_decision` (always goes to the conflict register). There is no hidden "last write wins".

```
konmari policy load --file policy/customer.yaml --as dirk    # new version is pending; dry run lists changed values
konmari policy approve customer --version 2 --as ben         # the rule owner approves; golden records are rebuilt
konmari policy rollback customer --to 1 --reason "..." --as ben
konmari policy check --exposure exposure.yaml                # every field agents can read needs a rule
konmari policy build customer                                # golden records + OpenLineage columnLineage event
konmari policy golden customer <cluster>                     # value, source, rule, status per attribute
```

Each golden attribute records the winning source, record ID, rule, policy version and timestamps. An attribute without a decision is `unresolved` and carries no value, so an agent answers "unknown" instead of guessing.

## 6. Resolve conflicts

A conflict is created when cluster members disagree and the rule cannot decide. Disagreements that a rule did decide are kept as `overridden`, so you can check rule outcomes. Values that are equal after normalization (`+49 30 1234567` and `030/1234567`) are no conflict.

```
konmari conflicts list --status open --as ben
konmari conflicts resolve <id> --source erp --ticket IT-4711 --as ben    # the CRM gets corrected via ticket IT-4711
konmari conflicts accept <id> --source crm --reason "..." --as ben       # the difference at the source stays
konmari conflicts revert <id> --reason "..." --as ben
konmari conflicts report --overdue
konmari conflicts export-tickets -o write-back-tickets.csv
```

If a later normalization run still shows the same disagreement on a resolved entry, the entry is `reopened` and the owner is notified (`.konmari/notifications.jsonl`, or `$KONMARI_NOTIFY_FILE`). An `accepted_deviation` does not reopen.

## 7. Data protection (DSGVO)

- Conflicts on personal data (inventory tier `personal` or `special-category`) are flagged and get a due date 30 days after detection (`conflict_due_days` in the policy file). The 30 days are our assumption, not a legal deadline. `konmari conflicts report --overdue` is the list for the controller's accuracy (Art. 5(1)(d)) and rectification (Art. 16) process.
- The register stores only the candidate values needed for the decision, never whole source records. The write-back CSV contains no values.
- Candidate values and quarantined raw values of personal-data fields are masked for everyone except the field owner and the `compliance` role. This is a stand-in until access control and masking (step 7) are in place.
- Check digits prove syntax only, not that an IBAN or VAT ID exists. The kit does not query external registers (VIES).
