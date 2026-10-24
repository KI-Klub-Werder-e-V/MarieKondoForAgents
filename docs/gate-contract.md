# Readiness gate contract (for changes c02–c09)

Every pipeline step must call the gate before it reads any source data. If the gate fails, the step stops and does not connect to a source.

## Python

```python
from konmari.charter import gate, scope

res = gate(step, charter_path="charter.yaml", catalog=None, actor=None)
if not res.passed:
    raise SystemExit("\n".join(res.unmet))
# attach res.charter_id and res.charter_version to every record you write
```

- `step`: one of the KonMari step IDs `inventory`, `score`, `discard`, `standardize`, `enrich`, `govern`, `connect`, `evaluate`. Any other value raises `ValueError`.
- `charter_path`: the engagement's `charter.yaml`. Its canonical hash must match the approved version.
- `catalog`: a `konmari.catalog.Catalog`. The default is `connect()`, which reads `$KONMARI_CATALOG` and falls back to `duckdb:///.konmari/catalog.duckdb`.
- `actor`: recorded in the audit trail. The default is the OS user.

It returns a `GateResult(passed, step, charter_id, charter_version, metric_set_version, unmet)`. `unmet` lists every failed condition, not only the first one.

The gate passes only when all of these hold:

1. The latest charter version is `approved`, and `charter.yaml` matches its content hash.
2. The latest metric-set version is `approved`.
3. Every `must_have` metric has a baseline value. A `not_yet_measured` baseline with a plan date is accepted only for `step="inventory"`.

Every call, passed or failed, writes an `audit_event` row with `action = 'charter.gate'`, the step, the result (`passed` or `failed`), the charter version and the unmet conditions.

## CLI

```
konmari charter gate --step <step> [--file charter.yaml] [--json]
```

| Exit code | Meaning |
|---|---|
| 0 | gate passed (`--json` prints the `GateResult`) |
| 1 | gate failed; each unmet condition goes to stderr as `unmet: ...` (or into `unmet` with `--json`) |
| 2 | usage error, for example an unknown step |

## Data scope lookup

Later steps take their sources, in-scope categories, approvers and reviewers only from the latest approved charter version:

```python
sc = scope("verein-faq-assistant")      # raises LookupError if no version is approved
sc.sources                              # [{"id", "connector", "locator", ...}]
sc.approver("policies-and-contracts")   # stakeholder ID of the data_owner approver; KeyError if out of scope
sc.reviewers("kb-and-sops")             # [stakeholder IDs]
sc.stakeholders["ben"]                  # register entry (name, contact, roles)
```

## Audit trail

Write your own decisions with `konmari.catalog.audit(cat, action, actor=..., charter_id=..., charter_version=..., step=..., result=..., details={...})`. `audit_event` is append-only. The catalog layer refuses `UPDATE`, `DELETE` and `TRUNCATE` on it, and on Postgres a trigger refuses them too. c07 adds hash chaining.
