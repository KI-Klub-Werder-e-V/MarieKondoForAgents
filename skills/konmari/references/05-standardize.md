# Step 5: normalize, resolve, source-of-truth policy, conflicts

Full guide: `docs/standardize-guide.en.md`.

## What it does

Makes values comparable (dates, datetimes, IDs, phone numbers, names, quantities) without guessing,
links records of one entity across systems (Splink), and lets the field owner decide which source
wins per field. Raw values stay next to canonical ones; corrections at the source go through tickets.

## Commands

```
konmari normalize --records <records.jsonl> --specs <normalize/> --as <you>
konmari normalize quarantine list --as <you>
konmari normalize quarantine resolve <id> --value <canonical value> --as <field owner>
konmari resolve run --entity <entity> --config resolve.yaml --as <you>
konmari resolve review export --entity <entity> -o pairs.csv
konmari resolve review import pairs.csv --as <reviewer>
konmari resolve publish --entity <entity> --known known.csv --as <data owner>
konmari policy load --file policy/<entity>.yaml --as <you>
konmari policy approve <entity> --version <n> --as <rule owner>
konmari policy check --exposure exposure.yaml
konmari policy build <entity>
konmari conflicts list --as <you>
konmari conflicts resolve <id> --source <source> --ticket <ticket> --as <rule owner>
konmari categories step <category> standardize --status complete --as <category approver>
```

## Reading the output

- `normalize` prints per field the share of canonical and quarantined values. A value without a
  declared zone, a wrong check digit or an unknown unit goes to **quarantine** with a reason.
- `resolve publish` refuses below 100 labelled pairs, precision 0.98 or recall 0.95.
- Golden attributes without a decision are `unresolved` and carry no value: the agent says "unknown".

## Common blockers

- A field without a spec stops the run. Draft the spec with the user; the spec names its owner.
- Pair labels (`match`, `non_match`) come from a person, not from you.
- Pilots with documents only (no records): there may be nothing to normalize. Explain that, and the
  category approver can mark the step complete.
