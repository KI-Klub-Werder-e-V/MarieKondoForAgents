# Inventory and category guide (step 2 of the KonMari-Kit)

KonMari rule 4: tidy by category, not by location. This step lists every source the approved charter names, gives each container (document library, folder, table) one inventory record, groups the records into categories across systems and fixes the order in which the categories are tidied. Nothing in your sources is changed: every connector only reads.

You need an approved charter with metric baselines (step 1). `konmari charter gate --step inventory` must pass.

## 1. Prepare read-only access

Checklist for IT (the `data_custodian`):

- [ ] One technical account per source, used only by the kit, with **read** rights and nothing else.
- [ ] SQL: a role with `SELECT` on the in-scope schemas only. The kit also opens every Postgres session read-only.
- [ ] File share: a user with read permission on the share path from the charter.
- [ ] SharePoint/M365: an Entra ID app with the application permission **`Sites.Selected`** only (see below). The scan stops if the token has `Sites.Read.All` or any `*.ReadWrite.All` role.
- [ ] Passwords and tokens stay out of the charter, the command line and the catalog. Store them in an environment variable or a file and pass only the reference: `--secret-ref env:CRM_DB_PASSWORD` or `--secret-ref file:/run/secrets/crm`. For a file share the secret holds `user:password`.

### The `Sites.Selected` grant (once, by a tenant admin)

1. In Entra ID, register an app and add the Microsoft Graph **application** permission `Sites.Selected`. Grant admin consent.
2. Give the app read access to exactly the sites in the charter, for example with Microsoft Graph: `POST /sites/{site-id}/permissions` with `{"roles": ["read"], "grantedToIdentities": [{"application": {"id": "<app id>", "displayName": "konmari"}}]}`. PnP PowerShell offers `Grant-PnPAzureADAppSitePermission -Permissions Read`.
3. Get an access token for the app (client credentials flow) and put it in the secret, e.g. `export SP_TOKEN=...`. Tokens expire after about an hour, so get one right before the scan.

The kit enumerates sites and libraries explicitly and never calls Graph Search, because Search ignores the `Sites.Selected` scope.

## 2. Register and scan

```
konmari inventory source add --connector sharepoint --locator contoso.sharepoint.com/sites/support --secret-ref env:SP_TOKEN --as dirk
konmari inventory source add --connector sql --locator postgresql+psycopg://reader@db.example.org/crm --secret-ref env:CRM_DB_PASSWORD --as dirk
konmari inventory source add --connector fileshare --locator smb://nas.example.org/office/faq --secret-ref env:NAS_LOGIN --as dirk
konmari inventory scan --as dirk
```

A source that is not in the approved charter is refused. Amend the charter first. File shares over SMB need `uv sync --extra smb`.

The scan reads a sample (default: the first 200 items or rows per container) and looks for personal data locally with Presidio and the German spaCy model. Install the model once with `uv run python -m spacy download de_core_news_lg`. Only the entity type, the number of hits and the highest score are stored, never the values. Rescans only reread what changed (Graph delta, file time and size, table schema). A container that disappears is marked `missing`, never deleted.

Systems without a connector, for example a SaaS CRM, go in through a CSV or YAML file with the same columns. The template is `examples/verein-faq-assistant/exports/fee-rules.csv`. Register the file as a `csv-import` source, then run `konmari inventory import exports/fee-rules.csv --as ben`. Invalid rows are reported with their line number. The other rows are imported.

## 3. Complete the records

A record is `complete` when it has one named person as owner (not a team or shared mailbox), an update frequency (ISO 8601 such as `P1W`, or `continuous`, `irregular`), a format and a confirmed sensitivity tier.

```
konmari inventory set <asset_uri> --owner ben --steward clara --update-frequency P1M --as ben
konmari inventory tier <asset_uri> --confirm --as ben                          # owner or compliance
konmari inventory tier <asset_uri> --set internal --reason "test data, synthetic" --as ben
konmari inventory set <asset_uri> --ropa-ref VVT-07 --as eva                   # or --ropa-missing
konmari inventory report --csv inventory.csv                                    # status, %, tiers, RoPA gaps
konmari inventory report --gate                                                 # exit 1 while gaps remain
konmari inventory waive <asset_uri> --reason "..." --as ben                     # audited exception
konmari inventory export -o inventory.jsonld                                    # DCAT 3 JSON-LD
```

Tiers are `public`, `internal`, `confidential`, `personal` and `special-category` (GDPR Art. 9, e.g. health data). The kit proposes a tier from the hints and never proposes one below them. A person decides. The hints feed your record of processing activities (RoPA) and DPIA. They do not replace them.

## 4. Categories and their order

```
konmari categories suggest --as ben               # rule-based, each suggestion names its rule
konmari categories accept <asset_uri> --as ben    # or: assign <asset_uri> <category>, reject, demote, revert
konmari categories show policies-and-contracts    # one category across all systems, with overlap hints
konmari categories order --propose --as ben       # risk score with its inputs and weights
konmari categories approve --as ben               # a category approver from the charter
konmari categories handoff                        # exit 0 = step 2 is done, prints the sequence
```

The five default categories are `kb-and-sops`, `customer-records`, `policies-and-contracts`, `financial-ops-metrics` and `support-history`. Approvers can add, rename and retire categories. The order runs from the lowest risk score up. A category with confirmed special-category data always goes last. The weights are our assumptions; change them with `--weight systems=0.7`. A changed order (`--set a,b,c --reason ...`) becomes a new version that needs approval again.

Later steps run category by category in this order. `konmari categories step <category> <step>` shows whether a step may start. A category may skip ahead of earlier categories only with an approver's waiver: `--waive --reason ...`.
