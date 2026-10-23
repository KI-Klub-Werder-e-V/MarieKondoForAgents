# KonMari-Kit roadmap

Nine OpenSpec changes, applied in order. Each one maps to a KonMari rule and a step of the practical sequence in `research/00-quelle.md`. Change directories carry a `c` prefix (`c01-…`) because the openspec CLI 1.6.0 rejects change names that start with a digit in `status`, `instructions` and `apply`.

## Changes

| # | Change | Goal | Capabilities | Depends on | Rough effort* |
|---|---|---|---|---|---|
| 1 | `c01-pilot-charter` | Commit to one use case: scope, stakeholders/RACI, DSGVO/AI Act screening, data scope with per-category approvers, success metrics with baselines, readiness gate | `pilot-charter`, `success-metrics` | none | M (≈5 d) |
| 2 | `c02-category-inventory` | Inventory the charter's sources across systems; group by category; risk-ordered, approved category sequence; per-category step order | `source-inventory`, `category-map` | c01 | L (≈10 d) |
| 3 | `c03-joy-test-scorecard` | "Does it spark joy?" rubric (6 criteria, 0–3), evidence probes, keep/fix/discard verdicts with human review, category maturity per table aspect | `readiness-scorecard` | c01, c02 | M (≈6 d) |
| 4 | `c04-discard-pass` | Discard first: exact/near/semantic duplicates, superseded versions, off-scope items; approved archive/exclude/mask/erase; retention rules, legal holds | `dedup-detection`, `retention-decisions` | c01, c02, c03 | L (≈10 d) |
| 5 | `c05-standardize-source-of-truth` | Normalize dates/IDs/names/units, cross-system entity resolution, per-field source-of-truth policy, golden records, conflict register | `normalization`, `source-of-truth-policy` | c01, c02, c04 | L (≈10 d) |
| 6 | `c06-structure-and-enrich` | Structure-aware chunking, mandatory metadata envelope, clean table schemas, approved business glossary | `document-chunking`, `semantic-metadata`, `business-glossary` | c01–c05 | L (≈9 d) |
| 7 | `c07-governance` | ACLs that travel with the data, RLS enforcement, agent + on-behalf-of identity, source policies, PII masking, single hash-chained audit trail, governance gate | `access-control`, `sensitive-data-masking`, `audit-logging` | c01–c06 | XL (≈12 d) |
| 8 | `c08-connect-agent-interfaces` | Data products (ODPS/ODCS) with semver, approved publish, scheduled change-tracked refresh; governed read-only MCP server | `data-products`, `mcp-exposure` | c01–c03, c05–c07 (c04 decides what stays in the corpus) | L (≈10 d) |
| 9 | `c09-evaluate-and-monitor` | Golden questions through the real MCP interface, citation/abstention gates, judge calibration; freshness, quality and drift monitoring with owner alerts | `agent-evaluation`, `freshness-monitoring` | c01–c08 | L (≈8 d) |

\* Our own estimate for one experienced developer, from the task counts (31–46 tasks per change). It is not a measured value.

`c03` is re-run after each later step (fix items close, `c09` monitoring feeds currentness), so the scorecard is a loop, not a single pass.

## Coverage matrix (source brief → requirements)

### The six KonMari rules

| Brief item | Covered by (change / capability / requirement) |
|---|---|
| 1 Commit: not a one-off | c01 `pilot-charter` "Engagement cadence and decision log" (post-pilot review required) |
| 1 Commit: buy-in from owners, experts, compliance, agent users | c01 "Stakeholder register with RACI" (required roles) |
| 1 Commit: one concrete pilot use case | c01 "Single pilot use case per charter", "Pilot data scope and category approvers" |
| 1 Commit: success metrics before touching data | c01 `success-metrics` "Baseline before target and before data work", "Readiness gate for downstream steps" |
| 2 Ideal: discoverable and callable | c08 `data-products` "Product catalog", `mcp-exposure` "Resources with catalog and freshness annotations" |
| 2 Ideal: current, consistent, owned | c02 "Owner and metadata completeness gate", c05 `source-of-truth-policy`, c09 `freshness-monitoring` |
| 2 Ideal: rich metadata and semantic context (definitions, owners, dates, authority, lineage) | c06 `semantic-metadata` "Mandatory metadata envelope", "Lineage recording", `business-glossary` |
| 2 Ideal: properly permissioned | c07 `access-control` |
| 2 Ideal: structured for retrieval (chunks, table schemas) | c06 `document-chunking`, `semantic-metadata` "Clean table schemas" |
| 2 Ideal: agent knows which source wins | c05 "Per-field precedence policy", "Agent-visible provenance and conflict status" |
| 2 Ideal: agent can say "I don't know" | c05 "Agent-visible provenance…" (`unresolved`), c08 "Every result states its freshness and source", c09 "Abstention and conflict handling" |
| 2 Ideal: target picture as a test | c03 "Versioned readiness rubric", "Category maturity rollup" |
| 3 Discard: duplicates and near-duplicates | c04 `dedup-detection` "Exact duplicate detection", "Lexical near-duplicate…", "Semantic near-duplicate…" |
| 3 Discard: outdated / superseded versions | c04 "Superseded-version detection" |
| 3 Discard: irrelevant to the agent's job | c04 "Relevance flagging against the pilot charter", c03 verdict reason `irrelevant` |
| 3 Discard: ungovernable sensitive data, or mask | c04 "Sensitive-item routing signal", "Exclude and route-to-masking"; c07 `sensitive-data-masking` |
| 3 Discard: move the rest to cold storage | c04 `retention-decisions` "Reversible archive to cold storage", "Restore" |
| 3 Discard: finish discarding first | c02 "Step order within each category" (discard before standardize/enrich) |
| 4 By category across systems (KB/SOPs, customers, policies/contracts, metrics, support) | c02 `category-map` "Default category set", "Cross-system category assignment" |
| 4 Reveals redundancy and conflict | c02 "Cross-system overlap hints" → c04, c05 "Conflict detection" |
| 5 Easy categories first, sensitive later | c02 "Risk-based category ordering", "Approved order as handoff" |
| 5 Within a category: clean before enrich/connect | c02 "Step order within each category"; c06 "Pilot-scoped chunking input" |
| 6 Authoritative and current? | c03 criterion `authoritative_current` (gate) |
| 6 Owned and maintained? | c03 `owned_maintained` |
| 6 Business meaning clear? | c03 `meaning_clear` ← c06 `business-glossary` |
| 6 Precise chunk/record retrievable? | c03 `retrievable_precise` ← c06, c09 retrieval metrics |
| 6 Conflicts resolved, permissions explicit? | c03 `conflicts_resolved` ← c04/c05; `permissions_explicit` (gate) ← c07 |
| 6 Yes → keep; No → fix or discard | c03 "Verdict proposal with gates", "Human review and reversible decisions" |

### Poorly prepared vs agent-ready table

| Aspect | Agent-ready target | Covered by |
|---|---|---|
| Format | Chunked passages / clean data products | c06 `document-chunking`, c08 `data-products` |
| Metadata | Source, date, type, owner, freshness, definitions | c02 inventory record, c06 metadata envelope + glossary |
| Access | Explicit read/write boundaries, RBAC | c07 `access-control` (RLS, least-privilege tool scopes), c08 scope-to-product mapping |
| Consistency | Deduplicated, source-of-truth policy | c04 `dedup-detection`, c05 `source-of-truth-policy` |
| Update mechanism | Scheduled pipelines with change tracking | c08 "Scheduled refresh with change tracking", c09 freshness checks, c07 ACL re-sync |
| Discoverability | Catalogued, described, tool/MCP-exposed | c02 inventory + DCAT export, c08 product catalog and MCP server |

c03 "Category maturity rollup" scores every category on these six aspects.

### Practical sequence

| Step | Covered by |
|---|---|
| 1 Scope to one use case; inventory sources (owner, update frequency, format, sensitivity) | c01, c02 `source-inventory` |
| 2 Clean & standardize (dedupe, normalize dates/IDs/names, resolve conflicts) | c04, c05 |
| 3 Structure & enrich (chunks, semantic metadata, business terms) | c06 |
| 4 Govern (ACLs that travel, audit logging, source policies) | c07 |
| 5 Connect (data products, function calling, MCP) | c08 (MCP tools are the function-calling surface) |
| 6 Test with real questions; monitor freshness/quality | c09 |

No item in the brief is left without a requirement. Thin spots: "function calling" outside MCP has no adapter of its own (MCP tools only), and drift monitoring has a default method (topic-share comparison, c09 design) but no default threshold, and it only warns.

## Shared vocabulary

These names must stay identical across changes.

**Catalog entities**
- *charter* (c01): one use case, versioned; `charter_id` and version travel with every downstream record.
- *asset* (c02): one inventory record (document library, folder subtree, table/view, CRM object). Verdicts (c03) are per asset.
- *item* (c04–c06): a document, file or record inside an asset. Discard decisions, normalization and chunking are per item.
- *chunk* (c06): retrievable passage or table chunk with a deterministic ID.
- *entity cluster / golden record* (c05); *data product* and *product version* (c08); *golden item*, *run*, *baseline*, *incident* (c09).
- *audit_event* (created in c01, hardened in c07): the single append-only, hash-chained audit trail. c02, c04, c05 and c06 write here. c01's `decision_log` holds only steering-meeting minutes.

**Roles** (c01): `sponsor`, `use_case_owner`, `data_owner`, `data_steward`, `data_custodian`, `compliance`, `agent_user`, `facilitator`. Each in-scope category has one `data_owner` approver in the charter data scope. Monitoring escalations go to the `use_case_owner`.

**Category IDs** (c02 defaults): `kb-and-sops`, `customer-records`, `policies-and-contracts`, `financial-ops-metrics`, `support-history`.

**KonMari step IDs** (c02 step tracking): `inventory`, `score`, `discard`, `standardize`, `enrich`, `govern`, `connect`, `evaluate`.

**Metadata fields**
- Inventory (c02): `asset_uri`, `source_id`, `system_type`, `owner`, `steward`, `update_frequency` (ISO 8601 duration | `continuous` | `irregular` | `unknown`), `last_modified`, `format`, `sensitivity_tier`, `personal_data_categories`, `ropa_ref`, `access_rights_note`, `status`.
- Chunk/record envelope (c06): `source_id`, `source_uri`, `source_system`, `content_type` (= category ID), `owner`, `created_at`, `modified_at`, `freshness_class` (`live|daily|weekly|monthly|static`), `authority` (`primary|secondary`, from c05 policy), `lineage`, `acl_tags` (normalized principals captured by c07 access-control), `sensitivity`, `language`, `enriched_at`; optional `glossary_terms`, `valid_from`, `valid_until`, `version`, `generated_context`.

**Value sets**
- Sensitivity tier (c02, c04, c06, c07): `public`, `internal`, `confidential`, `personal`, `special-category`.
- Joy criteria (c03): `authoritative_current`, `owned_maintained`, `meaning_clear`, `retrievable_precise`, `conflicts_resolved`, `permissions_explicit`; levels 0–3 or `unknown`.
- Scorecard verdict (c03): `keep`, `fix`, `discard`; review status `proposed`, `approved`. Maturity state: `poorly_prepared`, `in_progress`, `agent_ready`.
- Discard action (c04): `keep`, `archive`, `exclude`, `route-to-masking`, `erase`. Item states: `active`, `archived`, `excluded`, `awaiting-masking`, `restricted`, erased (erasure record only).
- Conflict status (c05): `open`, `resolved`, `accepted_deviation`, `reopened`; attribute status `unresolved`.
- Chunk/record status (c06): `ready-for-agents`, `needs-review`, `superseded`, flags `acl_stale`, `expired`.
- Governance status (c07): `approved`, `blocked` (with reason), `acl_stale`.
- Product version state (c08): draft, published, deprecated, withdrawn, sunset. Result freshness (c08): `fresh` / `stale` against the product SLA.
- Monitoring status (c09): `warn` (age > 1× interval), `error` (> 2×), `unmonitorable`. Incidents: `open`, `acknowledged`, `resolved`, `accepted_risk`.
- Golden item behaviour (c01, c09): `answer`, `abstain`, `flag_conflict`. Golden set floor 30 items / 5 negative (c01); default 50 items / ≥ 20 % negative (c09).

## Fixed in the consistency pass

- Renamed all changes to `c01-…`–`c09-…` and updated every cross-reference, so `openspec status/instructions/apply` and `/opsx:apply` work.
- c01: new requirement "Pilot data scope and category approvers". c02, c03, c04, c05 and c08 relied on a charter source list, in-scope categories and per-category approvers that c01 did not define.
- c02: new requirement "Step order within each category" (rule 5, clean before enrich/connect).
- c07: new requirement "Single shared audit trail". c02 had its own `decision_log` table, c04 had a separate hash chain and c07 created `audit_event` a second time. They are now one table.
- c07: ACL capture has one owner (c07 access-control), and c06 `acl_tags` is filled from it. `stale-acl` is now `acl_stale`, and the `confidential` tier was added to sensitivity classes.
- c08: audit events follow the c07 schema and no longer hold argument values. Identity derivation comes from c07.
- c01/c09: golden-set minimums are aligned, and the expected answer vocabulary is now `abstain`/`flag_conflict`.
- c04/c06/c09: scenarios use the c02 category IDs. c04 tells apart *asset* (inventory) from *item*. c06 uses c04 discard actions (there is no `discard` action), and c09 escalates to `use_case_owner` (there is no "pilot lead" role). "Consultant" in c07 is now "facilitator".

## Open questions for Tim

1. **Naming**: the `c` prefix is in place. Do you want another scheme (e.g. `pilot-charter-01`) before the first apply or archive?
2. ~~**Project context**~~ resolved (2026-10-23): open-source software of the KI Klub Werder e.V. GitHub organization; organizations run it themselves, optionally with a volunteer or consultant as facilitator.
3. **Thresholds are mostly our assumptions** (marked in the research notes): golden set 30/5 vs 50/20 %, Jaccard 0.8 / containment 0.9 / cosine 0.93, ER 0.99/0.50 and P/R floors 0.98/0.95, PII recall 0.95 on 200 samples, kappa ≥ 0.6, freshness 1×/2×, 14-day erasure wait, 30-day conflict SLA, 6-month audit retention, 100/1000 result caps, 90-day deprecation. Calibrate them after the first pilot, or set them now?
4. **Identity provider** of pilot organizations: Entra ID (OBO) or Keycloak/Authentik (RFC 8693, RFC 8707)? This drives c07/c08.
5. **Judge and embedding models**: self-hosted by default (DSGVO), or a hosted API with a DPA? Which multilingual/German embedding model (c04, c06, c09)?
6. **Connectors in v1**: which CRMs get a native connector and which go through CSV import? Should existing MIP sensitivity labels override the proposed tier or only inform it?
7. **Non-technical reviewers**: is CLI-only review enough, or do we need a minimal web review page (verdicts, discard approvals, glossary)?
8. **Write-back target** for c05 conflict tickets: GitHub issues, Jira, or CSV?
9. **Regulatory checks still open** (primary texts not fetched): EU AI Act Art. 4 wording after the Digital Omnibus and the postponed high-risk dates, final EDPB Guidelines 01/2025 on pseudonymisation, DIN 66398 status. Gartner figures in c01 are confirmed only through a search snippet (fetch returned 403).
10. **Reverse pseudonym map** (c07): keep only the HMAC key by default, or also a lookup table for Art. 15 requests?
