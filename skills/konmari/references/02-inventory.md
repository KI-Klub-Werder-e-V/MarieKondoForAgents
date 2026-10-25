# Step 2: inventory and categories

Full guide: `docs/inventory-guide.en.md`.

## What it does

Lists every source the approved charter names, one inventory record per container (library,
folder, table), groups them into categories across systems and fixes the order (low risk first,
special-category data last). Connectors only read. Personal-data hints are computed locally and
store only entity type, count and score.

## Commands

```
konmari inventory source add --connector fileshare --locator <path or smb://...> --as <you>
konmari inventory source add --connector sql --locator <url without password> --secret-ref env:<NAME> --as <you>
konmari inventory source add --connector csv-import --locator <file.csv> --as <you>
konmari inventory scan --as <you>                 # read-only; SMB needs: uv sync --extra smb
konmari inventory import <file.csv> --as <you>    # systems without a connector (template: examples/.../exports/fee-rules.csv)
konmari inventory report
konmari inventory report --gate                   # exit 1 while records are incomplete
konmari inventory set <asset_uri> --owner <id> --update-frequency P1M --format <media type> --as <you>
konmari inventory tier <asset_uri> --confirm --as <owner or compliance>
konmari inventory tier <asset_uri> --set internal --reason "<reason>" --as <owner or compliance>
konmari inventory waive <asset_uri> --reason "<reason>" --as <category approver>
konmari categories suggest --as <you>
konmari categories show <category>
konmari categories accept <asset_uri> --as <category approver>
konmari categories assign <asset_uri> <category> --as <category approver>
konmari categories order --propose --as <you>
konmari categories approve --as <category approver>
konmari categories handoff
```

## Reading the output

- `scan` prints per source: created, updated, unchanged, missing. A container that disappears is
  `missing`, never deleted.
- A record is complete with a named person as owner (not a team mailbox), an ISO 8601 update
  frequency (`P1W`, `P1M`) or `continuous`/`irregular`, a format and a **confirmed** tier.
- `categories suggest` names the rule behind every suggestion; accepting is the approver's call.
- `categories order --propose` prints the risk score with its inputs; weights are assumptions.
- `handoff` exit 0 prints the category sequence and completes `inventory` for every category.

## Common blockers

- "not listed in approved charter": the source must be added to the charter (`charter amend`, new approval).
- Credentials: ask IT for a read-only account; the user exports the secret
  (`export CRM_DB_PASSWORD=...`) and you pass `--secret-ref env:CRM_DB_PASSWORD`. SharePoint needs
  an Entra app with `Sites.Selected` only.
- Missing spaCy model: `uv run python -m spacy download de_core_news_lg` (or `de_core_news_sm` with `KONMARI_NLP_MODEL=de_core_news_sm` for trials).
- Owner unknown: ask the user who maintains it; a team is not an owner.
