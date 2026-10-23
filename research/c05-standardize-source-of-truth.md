# 05 Standardize & Source of Truth: research notes

Scope: KonMari sequence step 2 ("clean & standardize") after discarding (change 04). Covers
normalization of dates, IDs, names and units, cross-system entity resolution, and an explicit
source-of-truth (precedence) policy with a conflict register that owners resolve.
Research date: 2026-10-23. Numbers carry a source, or are marked **[assumption]**.

## 1. Findings

### 1.1 Standards frame
- **ISO 8000-110:2021** (master data exchange of characteristic data) requires conformance to a
  formal syntax, semantic encoding, conformance to a data specification, and access to the data
  dictionary that decodes the message. The ISO listing says these requirements "can be checked by
  computer". Takeaway: every normalized field needs a machine-checkable specification (type,
  canonical format, reference dictionary), not just a cleanup script. ISO 8000-120 covers
  provenance, which we model as source plus rule per value. Neither standard covers accuracy on
  its own: ISO 8000-110 is necessary but not sufficient.
- **DAMA-DMBOK (Master & Reference Data)**: our searches did not reach the primary text. Vendor
  and practitioner MDM material agrees on the core ideas: a golden record built by per-attribute
  **survivorship**, a crosswalk that keeps the source identifiers, and **ownership by field**
  rather than one system being "the truth" for everything (NetSuite ownership-matrix guide: customer
  address, credit status and item description can each have a different owner).
- **OpenLineage `columnLineage` facet**: each input field carries a transformation with
  `type` DIRECT/INDIRECT and a subtype (e.g. IDENTITY, TRANSFORMATION, AGGREGATION). The `masking`
  flag marks values that were obfuscated. This fits the project default (OpenLineage-style events):
  a normalization emits DIRECT/TRANSFORMATION, a survivorship pick emits DIRECT/IDENTITY from the
  winning source.

### 1.2 Normalization rules by data type
- **Dates/times**: store a canonical UTC instant in ISO 8601 / RFC 3339 form. RFC 3339 is a stricter
  profile that requires an explicit offset or `Z`. Avoid ISO week and ordinal dates. Keep the original
  zone in a companion field when provenance matters. Reject or flag inputs without an offset rather
  than guessing silently. DST makes some local times ambiguous, so they need an explicit zone.
  Epoch units (s, ms, µs) are a common source of bugs. Keep event time and processing time as
  separate fields. Pure calendar dates (birth date, contract date) stay `YYYY-MM-DD` with no
  zone **[assumption: avoids day shifts]**. German `TT.MM.JJJJ` inputs must be parsed with an
  explicit per-source format, never by guessing (DD/MM and MM/DD clash) **[assumption]**.
- **Identifiers**: `python-stdnum` "parse[s], validate[s] and reformat[s]" standard numbers
  (IBAN, national VAT IDs via `get_cc_module(cc,'vat')`). `validate()` returns a compact
  canonical form or raises `ValidationError`. Caveat: it checks syntax and check digits only, not
  whether the account or registration exists. Phone numbers: Google libphonenumber
  (`phonenumbers` port) with E.164 output. Our search did not confirm its API, so verify it in the
  spike.
- **Names/text**: apply Unicode normalization (NFC) before comparing. Compatibility forms
  (NFKC) lose information that cannot be recovered, so keep the original value and compare on a
  derived **match key** (NFKC + casefold + whitespace collapse). German umlaut transliteration
  (ü→ue) is a language convention, not part of Unicode normalization. It must be an explicit,
  per-field rule **[source gap: no primary source found]**.
- **Units**: Pint holds quantities with editable unit definitions. `ucumvert` parses UCUM strings
  into Pint units, covers all UCUM 2.2 (June 2024) units, and only converts UCUM→Pint. UCUM has no
  single canonical spelling (`m/s` vs `m.s-1`), so normalize to a declared target unit per field and
  store value + unit. The ucumvert authors ask users to review the mappings, so any unit outside
  the pilot's declared list goes to review.
- **Quality rules as contracts**: Open Data Contract Standard (ODCS) quality rules (types sql /
  library / text / custom; library checks such as `nullValues`, `duplicateValues`) can be
  exported by the Data Contract CLI to dbt tests, Great Expectations suites, SodaCL or DQX, and
  tagged with a dimension. Takeaway: express "field X is normalized" as a contract check that runs
  in CI and on every pipeline run.

### 1.3 Entity resolution across systems
- **Fellegi-Sunter with two thresholds**: pairs above the upper threshold are linked, pairs
  below the lower one are not, and pairs in between go to **clerical review**. Thresholds follow
  from the error rates the team will tolerate, measured as precision and recall.
- **Splink** implements Fellegi-Sunter. It trains unsupervised with EM, runs on DuckDB locally
  (around 1M records "in around a minute" on a laptop per the project page) and on Spark or Athena
  for 100M+ records. It supports term-frequency adjustments and labelled pairs from clerical
  review (`register_labels_table`, ROC curves). It works best with several weakly correlated
  columns and not with a single bag-of-words column.
- **Example thresholds** (Kestra Splink blueprint, an example and not a standard): clustering at match
  probability 0.99, precision floor 0.98 and recall floor 0.95 on a clerically reviewed sample.
  The blueprint warns that 0.9 chains relatives into one entity. An ONS/IJPDS paper notes that
  choosing thresholds clerically is expensive and that automatic methods were untested on
  low-quality data.
- **Measurement pitfall**: a sample of linked pairs estimates precision only. Recall needs a
  sample of unlinked pairs or an independent list of known duplicates.
- **Splink API drift**: older demos use `DuckDBLinker`, newer ones `Linker`/`DuckDBAPI`/`SettingsCreator`.
  Users report clustering output with pairs below the threshold. Pin the version and test it.

### 1.4 Source-of-truth / survivorship policy
- Stibo names the two basic rule families "Most Recent" and "Trusted Source". Survivorship is
  defined per attribute (and per attribute group).
- Strategy menu (Reltio draft, unreviewed): Source Priority, Most Recent, Most Complete,
  Most Frequent. Reltio's default is Recency when no strategy is set.
- Per-attribute ownership example: CRM authoritative for contact preferences, ERP for billing
  terms. Different consumers may need different survivorship for the same attribute (Reltio
  community).
- **Attribute lineage**: record which source supplied the winning value and why. Crosswalks keep
  the original source IDs, timestamps and provenance.
- Profisee and LatentView: work out which source is reliable per attribute before building
  anything, and decide with business owners, not IT alone.

### 1.5 Legal frame (DSGVO / EU AI Act)
- **GDPR Art. 5(1)(d)** accuracy: personal data must be accurate and, where necessary, kept up to
  date. Commentary says the controller must fix or erase inaccurate data on its own initiative.
  **Art. 16** gives the data subject the right to rectification "without undue delay". The
  obligation scales with harm. (Our sources here are secondary, so check the primary text.)
  Takeaway: a conflict on a personal-data field is an accuracy issue. The conflict register needs
  owners, deadlines, and a path to correct the source, not only the agent-facing view.
- **EU AI Act Art. 10** (high-risk systems): datasets must be "relevant, sufficiently representative,
  and to the best extent possible, free of errors and complete". Governance practices explicitly
  include "cleaning, updating, enrichment". Most pilot agents (RAG/tool use) will not be high-risk
  or trained on this data, but documented cleaning decisions are cheap evidence if they ever are.
  The application date of the high-risk obligations was not verified (one source says 2026-08-02).

## 2. Concrete thresholds / metrics we will use

| Metric | Value | Basis |
|---|---|---|
| Entity match auto-link threshold | match probability ≥ 0.99 | Kestra Splink example; **[assumption]** for pilot default, tunable |
| Entity match auto-reject threshold | match probability < 0.50 | **[assumption]**; band between goes to clerical review (Fellegi-Sunter) |
| Precision floor on reviewed sample | ≥ 0.98 | Kestra Splink example, adopted as pilot default **[assumption]** |
| Recall floor (known-duplicate list) | ≥ 0.95 | Kestra Splink example, adopted as pilot default **[assumption]** |
| Clerical review sample size | ≥ 100 pairs or all pairs if fewer | **[assumption]** |
| Normalization conformance per in-scope field | 100 % of values canonical or quarantined with reason | **[assumption]**; ISO 8000-110 "checkable by computer" |
| Conflict register SLA (personal data fields) | owner decision ≤ 30 days | **[assumption]** aligned with GDPR "without undue delay" spirit; not a legal deadline |
| Fields without precedence rule | 0 for agent-exposed fields | **[assumption]**; follows source brief "agents know which source wins" |

## 3. Recommended OSS tools

| Tool | Rationale |
|---|---|
| **Splink** (pinned 4.x/5.x, DuckDB backend) | Fellegi-Sunter ER with unsupervised EM, clerical labels, runs locally on DuckDB like the stack. |
| **python-stdnum** | One API to validate and compact IBAN, VAT IDs and many national numbers. |
| **phonenumbers** (libphonenumber port) | De-facto standard for E.164 phone normalization (verify the API in the spike). |
| **Pint** + **ucumvert** | Unit-safe quantities, with UCUM parsing to Pint for imported unit strings. |
| Python stdlib `zoneinfo`, `datetime`, `unicodedata` | IANA zones, ISO parsing, NFC/NFKC: no extra dependency. |
| **Data Contract CLI** (ODCS) | Declares field formats and quality rules and exports them to dbt/GX/Soda tests. |
| **OpenLineage Python client** | Emits `columnLineage` facets for normalization and survivorship steps. |

Evaluated but not chosen as a default: commercial MDM hubs (Reltio, Stibo, Profisee, Semarchy) are
too heavy for a one-use-case pilot and not self-hostable OSS. Their survivorship vocabulary is
reused. dedupe.io / `dedupe` (active learning) is an alternative to Splink if the pilot has
few columns. Not benchmarked here.

## 4. Pitfalls
1. **Overwriting originals**: normalization must be additive (keep raw, add canonical). NFKC and
   transliteration cannot be reversed.
2. **Guessing date formats or time zones**: quarantine instead.
3. **One "system of record" for everything**: precedence is per field. Recency-only defaults
   silently prefer whichever system synced last.
4. **Transitive chaining in clustering**: low thresholds merge relatives or sister companies.
5. **Measuring precision only**: recall needs known duplicates.
6. **Fixing the agent view but not the source**: GDPR accuracy points to correcting at
   the source. The register must track write-back or an explicit "won't fix at source" decision.
7. **Silent policy changes**: changing a precedence rule changes agent answers. Version it,
   require approval, and keep it reversible.
8. **Library API drift** (Splink): pin the version and add regression tests on a fixture.
9. **Check-digit validity is not existence**: stdnum validates syntax only.

## 5. Sources
- ISO 8000-110:2021: https://www.iso.org/standard/78501.html
- PECB ISO 8000-110 listing: https://store.pecb.com/products/standard-iso-8000-110
- Profisee, MDM survivorship: https://profisee.com/blog/mdm-survivorship/
- Stibo Golden Records Survivorship Rules: https://doc.stibosystems.com/doc/version/10.2/web/content/mtchlnkmrg/survivorship/grsurvrules.html
- Reltio community, survivorship: https://community.reltio.com/reltio-best-practices/mdm-survivorship
- Semarchy xDM survivorship: https://www.semarchy.com/doc/xdm/5.3.14/xdm/5.3/Design/matching/survivorship.html
- LatentView, golden record: https://www.latentview.com/blog/mdm-golden-record/
- Houseblend, NetSuite master data ownership matrix: https://www.houseblend.io/articles/pdfs/netsuite-master-data-ownership-matrix.pdf
- Gravitee corpus, MDM golden record sync: https://gravitee.io/corpus/gen-953/enterprise-information-integration/master-data-management-and-golden-record-synchronization-for-enterprise-information-integration.html
- Splink on PyPI: https://pypi.org/project/splink
- Kestra Splink blueprint: https://kestra.io/blueprints/splink-entity-resolution-golden-records
- IJPDS, automatic threshold in probabilistic linkage: https://ijpds.org/article/view/3163
- (Almost) All of Entity Resolution: https://arxiv.org/pdf/2008.04443
- Splink discussion #1760: https://github.com/moj-analytical-services/splink/discussions/1760
- Splink pairwise labels demo: https://nbviewer.org/github/moj-analytical-services/splink_demos/blob/max_pair/example_pairwise_labels.ipynb
- Timestamps / ISO 8601 guide: https://www.cleverence.com/articles/oracle-documentation/time-formats-5829/
- NXLog, timestamp normalization: https://nxlog.co/news-and-blog/posts/log-timestamp-normalization-iso-8601
- Cross-time-zone integrity: https://blog.dataengineerthings.org/cross-time-zone-data-integrity-for-ensuring-consistency-in-data-pipelines-004b66d4fdba
- python-stdnum docs: https://arthurdejong.org/python-stdnum/doc/
- ucumvert: https://pypi.org/project/ucumvert
- Pint FAQ: https://pint.readthedocs.io/en/0.20/user/faq.html
- Unicode normalization guide: https://unicodefyi.com/guide/unicode-normalization-guide/
- Unicode list, caseless matching: https://corp.unicode.org/pipermail/unicode/2024-March/010812.html
- Data Contract CLI quality rules (ODCS): https://docs.datacontract.com/quality-rules
- OpenLineage column lineage facet: https://openlineage.io/docs/spec/facets/dataset-facets/column_lineage_facet/
- GDPR accuracy principle (secondary): https://gdprlocal.com/gdpr-accuracy-principle/
- ICO right to rectification: https://ico.org.uk/for-organisations/guide-to-dp/guide-to-the-uk-gdpr/individual-rights/right-to-rectification/
- EU AI Act Art. 10 (Mishcon de Reya): https://www.mishcon.com/eu-uk-ai-navigator/article-10
- EU AI Act Art. 10 (AI Act browser): https://ai-act-browser.ch.innoq.io/article/10
