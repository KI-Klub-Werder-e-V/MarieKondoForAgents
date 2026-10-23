## ADDED Requirements

### Requirement: Declarative normalization spec per field
The system SHALL let the operator declare, for every in-scope field of the pilot (as listed in the
`source-inventory` of change 02), a normalization spec containing: entity, field, source, data type
(`date`, `datetime`, `identifier`, `phone`, `name`, `text`, `quantity`), canonical format, parser
configuration (e.g. explicit input date pattern, source time zone, identifier kind, target unit) and
owner. Specs SHALL be stored as versioned YAML in the project and loaded into the readiness catalog.
The system SHALL refuse to run normalization for a field that has no spec.

#### Scenario: Field without spec is rejected
- **WHEN** the operator runs `konmari normalize` for a source whose field `crm.contact.birthdate` has no normalization spec
- **THEN** the command exits non-zero, names the missing field, and writes no canonical values for that source

#### Scenario: Spec versions are recorded
- **WHEN** a normalization spec file is changed and loaded again
- **THEN** the catalog stores a new spec version with author, timestamp and diff, and every canonical value references the spec version that produced it

### Requirement: Additive normalization preserves raw values
The system SHALL store the original raw value unchanged next to the canonical value, together with
the spec version, the normalizer name and the run ID. Normalization SHALL NOT overwrite or delete
raw values in the catalog or in source systems.

#### Scenario: Raw value survives normalization
- **WHEN** the raw value `03.04.2025` is normalized with input pattern `DD.MM.YYYY`
- **THEN** the catalog holds raw `03.04.2025`, canonical `2025-04-03`, the spec version and the run ID

#### Scenario: Re-running a run is idempotent
- **WHEN** the same normalization run is executed twice on unchanged input with the same spec version
- **THEN** the second run produces identical canonical values and creates no duplicate rows

### Requirement: Date and time normalization
The system SHALL normalize datetimes to a UTC instant serialized as RFC 3339 with `Z`, and SHALL store
the original IANA time zone or offset in a companion field. Calendar dates without time SHALL be
normalized to ISO 8601 `YYYY-MM-DD` without time-zone conversion. The parser SHALL use the explicit
input pattern and source time zone from the spec and SHALL NOT infer day/month order. Event time and
processing time SHALL be kept as separate fields. Epoch inputs SHALL declare their unit (s, ms, µs).

#### Scenario: Local time with declared zone
- **WHEN** raw `2025-03-30 14:00` arrives from a source whose spec declares zone `Europe/Berlin`
- **THEN** the canonical value is `2025-03-30T12:00:00Z` and the companion zone field is `Europe/Berlin`

#### Scenario: Ambiguous local time is quarantined
- **WHEN** raw `2025-10-26 02:30` arrives with zone `Europe/Berlin` (a repeated hour during the DST fall-back)
- **THEN** the value is quarantined with reason `ambiguous_local_time` and no canonical value is written

#### Scenario: Missing offset without declared zone
- **WHEN** a datetime without offset arrives and the spec declares no source time zone
- **THEN** the value is quarantined with reason `missing_timezone`

### Requirement: Identifier normalization and validation
The system SHALL normalize identifiers with the canonical compact form of the declared identifier
kind (python-stdnum modules for IBAN, VAT IDs and other supported numbers; E.164 for phone numbers)
and SHALL validate check digits where the kind defines them. Identifiers that fail validation SHALL
be quarantined with the validator's error. The system SHALL record that validation proves syntax only,
not real-world existence.

#### Scenario: VAT ID compacted
- **WHEN** raw VAT ID `DE 123 456 789` with a valid check digit is normalized with kind `de.vat`
- **THEN** the canonical value is `DE123456789` and the value is flagged `syntax_valid`

#### Scenario: Invalid IBAN quarantined
- **WHEN** an IBAN with a wrong check digit is normalized
- **THEN** it is quarantined with reason `invalid_checksum` and the raw value is kept

#### Scenario: Phone number to E.164
- **WHEN** raw phone `030 1234567` is normalized with default region `DE`
- **THEN** the canonical value is `+49301234567`

### Requirement: Name and text normalization with match keys
The system SHALL normalize names and free-text keys to Unicode NFC with trimmed and collapsed
whitespace as the canonical value. It SHALL additionally derive a separate match key (NFKC, casefold,
punctuation and legal-form tokens stripped as configured, optional German transliteration such as
`ü→ue` only when enabled per field). The match key SHALL be used for comparison only and SHALL NOT
be exposed as the display value.

#### Scenario: Equivalent names share a match key
- **WHEN** `Müller GmbH` and `MUELLER  GmbH` are normalized for a field with German transliteration and legal-form stripping enabled
- **THEN** both receive the match key `mueller`, while their canonical display values remain `Müller GmbH` and `MUELLER GmbH`

#### Scenario: Transliteration off by default
- **WHEN** a name field spec does not enable transliteration
- **THEN** `Müller` and `Mueller` receive different match keys

### Requirement: Unit normalization
The system SHALL normalize quantities to the target unit declared in the spec and store numeric value
and unit separately. Unit strings SHALL be parsed through a declared unit vocabulary (Pint, with UCUM
input via ucumvert). Units outside the declared list for that field or dimensionally incompatible units
SHALL be quarantined.

#### Scenario: Tonnes to kilograms
- **WHEN** raw `1,5 t` is normalized for a field with target unit `kg` and decimal comma
- **THEN** the canonical value is `1500` with unit `kg`

#### Scenario: Incompatible unit
- **WHEN** raw `3 m` arrives for a field with target unit `kg`
- **THEN** the value is quarantined with reason `incompatible_dimension`

### Requirement: Quarantine with human review
The system SHALL keep every value that could not be normalized in a quarantine table with the raw
value, reason code, source, field, spec version and run ID. A field owner SHALL be able to resolve a
quarantined value by supplying a canonical value, marking it unfixable, or requesting a spec change;
each resolution SHALL record the approver and timestamp and SHALL be reversible. Quarantined values
SHALL NOT be passed to golden records or agent interfaces.

#### Scenario: Owner resolves a quarantined value
- **WHEN** the field owner runs `konmari normalize quarantine resolve <id> --value 2025-04-03`
- **THEN** the canonical value is stored with origin `manual`, the approver and timestamp are recorded, and the quarantine entry is closed

#### Scenario: Reverting a manual resolution
- **WHEN** the owner reverts that resolution
- **THEN** the manual canonical value is withdrawn, the entry is reopened, and both actions remain in the audit trail

### Requirement: Normalization conformance check
The system SHALL compute, per field and run, the share of in-scope values that are canonical, the
share quarantined, and the reason distribution, and SHALL expose these to `readiness-scorecard`
(change 03). A field SHALL count as conformant only when every value is either canonical or
quarantined with a reason (no silently dropped or unparsed values). Specs SHALL be exportable as data
contract checks runnable in CI.

#### Scenario: Silent drop fails conformance
- **WHEN** a run reads 1,000 values for a field but writes 990 canonical values and 5 quarantine entries
- **THEN** the conformance check fails for that field and reports 5 unaccounted values

#### Scenario: Metrics reach the scorecard
- **WHEN** a normalization run completes
- **THEN** per-field canonical rate, quarantine rate and reason counts are written to the catalog where `readiness-scorecard` can read them

### Requirement: Cross-system entity resolution with clerical review
The system SHALL link records that describe the same real-world entity across sources using
probabilistic matching (Fellegi-Sunter, implemented with Splink) on canonical values and match keys.
It SHALL apply two configurable thresholds on match probability, defaulting to an upper threshold of
0.99 and a lower threshold of 0.50: pairs at or above the upper threshold are auto-linked, pairs
below the lower threshold are not linked, and pairs in between SHALL go to a clerical review queue
where a named reviewer accepts or rejects each pair. Within-source duplicates SHALL be taken from
`dedup-detection` (change 04) and not re-decided here. Every link SHALL record the cross-source
crosswalk (source, source record ID, cluster ID).

#### Scenario: High-probability pair auto-linked
- **WHEN** a CRM and an ERP customer record score match probability 0.995
- **THEN** they are placed in the same cluster with link origin `auto` and both source IDs in the crosswalk

#### Scenario: Borderline pair goes to review
- **WHEN** a pair scores 0.80
- **THEN** it is placed in the clerical review queue and is not linked until a reviewer accepts it

#### Scenario: Reviewer rejects a pair
- **WHEN** a reviewer rejects a queued pair
- **THEN** the pair is stored as a negative label, never auto-linked in later runs with the same records, and the decision is audited

### Requirement: Entity resolution quality gate
The system SHALL estimate precision on a sample of linked pairs (at least 100 pairs, or all if fewer)
labelled by reviewers, and recall against a list of known duplicates supplied by the data owner. Results
SHALL NOT be published to golden records unless precision and recall meet the floors in
`success-metrics` (change 01), defaulting to precision ≥ 0.98 and recall ≥ 0.95. The model version,
thresholds and measured metrics SHALL be recorded per run.

#### Scenario: Precision below floor blocks publication
- **WHEN** the labelled sample yields precision 0.95 against a floor of 0.98
- **THEN** `konmari resolve publish` exits non-zero, no clusters are published, and the report names the floor that failed

#### Scenario: Missing recall evidence
- **WHEN** no known-duplicate list has been supplied
- **THEN** the quality report marks recall as `unmeasured` and publication requires an explicit owner override that is audited

### Requirement: Reversible entity links
The system SHALL allow any cluster to be split or any link to be removed by an authorized reviewer.
Splitting SHALL recompute affected golden records and SHALL keep the previous cluster state in history.

#### Scenario: Wrongly merged entities are split
- **WHEN** a reviewer splits a cluster that merged two sister companies
- **THEN** two clusters result, both golden records are recomputed, the split is audited, and the prior cluster can be restored
