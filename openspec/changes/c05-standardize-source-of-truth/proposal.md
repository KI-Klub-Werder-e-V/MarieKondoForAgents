## Why

After the discard pass (change 04), the pilot's remaining data still writes the same facts in
different ways: `03.04.2025` and `2025-04-03T00:00+02:00`, `DE 123456789` and `DE123456789`,
"Müller GmbH" and "MUELLER GMBH", kg and t. The same customer can also exist in the CRM, the ERP
and a SharePoint list under three IDs, with conflicting values. An agent cannot tell which value
is correct, so it guesses. Step 2 of the KonMari sequence ("clean & standardize before enriching
or connecting") has to make values comparable, link the records that describe one entity, and
make an explicit decision about **which source wins** for each field. A human owner approves that
decision, and it can be reversed (research/c05-standardize-source-of-truth.md §1.4, §4).

## What Changes

- Add a declarative **normalization spec** per in-scope field: type, canonical format, parser and
  target unit. Normalization is additive: the raw value is kept and a canonical value is added.
- Ship **built-in normalizers** for dates/times (ISO 8601 / RFC 3339, UTC instant plus original
  zone), identifiers (IBAN, VAT ID and other standard numbers via python-stdnum, phone numbers in E.164),
  names and text (NFC plus a derived match key) and units (Pint/UCUM to a declared target unit).
- Values that cannot be normalized go to **quarantine** with a reason. The system never guesses
  ambiguous dates, time zones or units.
- Add **cross-system entity resolution** (Splink, Fellegi-Sunter) with two thresholds:
  pairs above the upper threshold are auto-linked, pairs in the band between go to human clerical
  review, and pairs below the lower threshold are rejected. Precision and recall are measured
  against floors before results are used.
- Add a versioned **source-of-truth policy** per entity and field: a survivorship strategy (source
  priority, most recent, most complete, or a named owner decision), a field owner, and a tie-breaker.
  Changes require approval.
- Build **golden records** with attribute lineage: each value records its winning source, the rule
  that picked it and the time it was picked. The records are emitted as OpenLineage `columnLineage`
  facets.
- Add a **conflict register**: unresolved disagreements are assigned to the field owner with a due
  date. A resolution records the decision and whether the source system is corrected (write-back
  ticket) or the deviation is accepted. Personal-data conflicts are marked for the DSGVO Art. 5(1)(d)/16 accuracy duty.
- Add the `konmari normalize`, `konmari resolve`, `konmari policy` and `konmari conflicts`
  CLI commands. Every decision is written to the readiness catalog, and every decision can be
  reverted.

## Capabilities

### New Capabilities
- `normalization`: declarative per-field normalization of dates, identifiers, names and units,
  additive raw/canonical storage, quarantine of ambiguous values, conformance checks, and
  cross-system entity resolution with human clerical review.
- `source-of-truth-policy`: versioned, owner-approved precedence (survivorship) rules per entity and
  field, golden records with attribute lineage, a conflict register with owner resolution and
  write-back tracking, and agent-visible provenance.

### Modified Capabilities
<!-- none: no existing specs in openspec/specs/ -->

## Impact

- **Upstream dependencies**: `c01-pilot-charter` (`pilot-charter` provides the pilot scope, the
  named data owners and the approvers, and `success-metrics` provides the target thresholds);
  `c02-category-inventory` (`source-inventory` lists the sources with owner, format and sensitivity, and
  `category-map` groups the entities across systems); `c04-discard-pass` (`dedup-detection`
  removes exact and near duplicates *within* a source before this change links records *across*
  sources, and `retention-decisions` excludes discarded records).
- **Downstream consumers**: `c03-joy-test-scorecard` (`readiness-scorecard` reads conformance
  rate, open conflicts and policy coverage for the "conflicts resolved?" question);
  `c06-structure-and-enrich` (`semantic-metadata` and `business-glossary` reuse field definitions,
  owners and canonical formats); `c07-governance` (`audit-logging` receives policy and
  conflict decisions, `access-control` and `sensitive-data-masking` apply to raw and
  quarantined values); `c08-connect-agent-interfaces` (`data-products` and `mcp-exposure` serve
  golden records with provenance and the "unresolved conflict" status); `c09-evaluate-and-monitor`
  (`agent-evaluation` tests conflict questions, and `freshness-monitoring` reuses the source
  timestamps behind "most recent" rules).
- **Code**: new `konmari.normalize`, `konmari.resolve` and `konmari.policy` modules, CLI commands,
  catalog tables (normalization specs, quarantine, entity clusters, policies, golden records, conflicts).
- **Dependencies** (all OSS): `splink` (pinned), `python-stdnum`, `phonenumbers`, `pint`,
  `ucumvert`, `openlineage-python`. The standard library supplies `zoneinfo` and `unicodedata`.
- **Compliance**: DSGVO accuracy (Art. 5(1)(d)) and rectification (Art. 16) for conflicts on
  personal data. The documented cleaning decisions also serve as evidence for EU AI Act Art. 10 data
  governance if a high-risk use ever applies.
