## Context

Step 1 of the practical sequence and KonMari rules 4 and 5 (research/00-quelle.md). Change `c01-pilot-charter` has already fixed one pilot use case, its source list, and the approvers. This change gathers those sources across systems, records the minimum metadata every later step needs, groups assets into categories, and proposes a risk-ordered sequence. Users are organisations running the kit themselves (SMEs, associations, public bodies), often without a data team, under DSGVO and possibly the EU AI Act. The catalog is Postgres (DuckDB for local runs). Findings, numbers, and sources are in research/c02-category-inventory.md (abbreviated R below).

## Goals / Non-Goals

**Goals:**
- One inventory record per asset for every charter source, with owner, steward, update frequency, format, sensitivity tier, personal-data categories, and a RoPA link.
- Read-only, least-privilege, local-only scanning that never stores PII values.
- Cross-system category view, human-approved assignments, and a human-approved, versioned category order.
- Every decision audited and reversible.

**Non-Goals:**
- Deduplication or discard decisions (`c04-discard-pass`), only overlap hints here.
- Readiness scoring (`c03-joy-test-scorecard`).
- Writing or maintaining the GDPR RoPA or a DPIA. We only link to them.
- Access-control enforcement or masking (`c07-governance`).
- Inventorying anything outside the pilot charter.

## Decisions

1. **Field vocabulary from DCAT 3 + OpenLineage, not a home-grown schema.** DCAT 3 already defines publisher/contactPoint, accrualPeriodicity, modified, format, theme, and accessRights. The OpenLineage `ownership` facet carries owners to any lineage backend (R §1).
   *Alternatives:* a DataHub/OpenMetadata entity model (couples us to one catalog); a free-form schema (no interoperability).

2. **Postgres catalog is the source of truth; DataHub/OpenMetadata are optional sinks via OpenLineage.** This keeps the kit self-hostable for small organisations without a catalog platform (R §5).
   *Alternatives:* require OpenMetadata (heavy to run for one pilot); Microsoft Purview (proprietary and paid, R §2). We only read existing MIP labels as an input signal.

3. **SharePoint via Graph `Sites.Selected` with explicit enumeration and delta.** Least privilege is GA. Graph Search is known to bypass Sites.Selected scoping, so it is forbidden. Delta links give cheap rescans (R §2). The connector refuses tokens with `Sites.Read.All`/`*.ReadWrite.All`.
   *Alternatives:* `Sites.Read.All` (tenant-wide, violates pilot scope); the SharePoint REST API with app-only ACS (being retired, and not needed).

4. **Asset granularity = container, not file.** We use document library or folder subtree, table/view, and CRM object. Per-file records would explode the inventory and recreate "by location". File-level detail belongs to `c06-structure-and-enrich`. Container size and dominant media type are aggregated.
   *Alternative:* per-file inventory (more precise, but too noisy for human approval in a pilot).

5. **Sensitivity hints with local Presidio + German spaCy model, sampled, counts only.** Presidio ships nine DE recognizers. DE_PLZ (base score 0.05) is excluded without address context. Default threshold 0.5 and sample 200 are our assumptions (R §4). Matched values are never stored (R pitfall 5).
   *Alternatives:* OpenMetadata auto-classification (needs the platform); cloud DLP APIs (data leaves the host, a DSGVO transfer issue); full scans (cost and exposure).

6. **Five-tier sensitivity scale aligned to GDPR.** `public < internal < confidential < personal < special-category`. Art. 9 special categories form the top tier because Art. 35(3) ties large-scale processing of them to a mandatory DPIA (R §1). The tier stays `proposed` until a human confirms it, and the system never proposes below what the hints support.
   *Alternative:* a client-specific scheme only (still supported via a mapping table, but the default must work out of the box).

7. **Completeness gate before handoff.** Owner must be a single named person (ISO 27001 A.5.9 practice), and `unknown` counts as a gap. Waivers are allowed but audited, which keeps the KonMari pace without hiding gaps (R §4).

8. **Stateful rescans with soft "missing" status.** This follows the DataHub stateful-ingestion pattern, keyed by a stable `source_id` so renames do not lose state (R §2, pitfall 7). Nothing is hard-deleted, so decisions stay reversible.

9. **Rule-based category suggestions, not ML.** Keyword, media-type, and table-name rules are transparent, testable, and explainable to non-experts. Each suggestion stores its rule ID. An optional LLM suggester can be added later behind the same `suggested` state.
   *Alternative:* embedding/LLM classification (opaque, needs a model host, harder to audit).

10. **Ordering = explainable weighted score + hard rule + human approval.** No standard prescribes the order (R §3), so the score inputs and weights are shown, special-category data always goes last (the brief's "sensitive later"), and ties follow the brief's default order. The approved order is versioned.
    *Alternative:* a fixed order from the brief only (ignores the pilot's real risk profile).

11. **Catalog tables.** `inventory_source`, `inventory_run`, `source_asset`, `sensitivity_hint`, `category`, `asset_category` (with state `suggested|accepted|rejected`, `is_primary`), `category_order_version`, plus audit entries written through the shared audit writer into the append-only `audit_event` table created by `c01-pilot-charter`, which `c07-governance`/`audit-logging` later hardens (hash chain, retention).

## Risks / Trade-offs

- [Graph permission grants need a tenant admin, who may be slow or unavailable] → Document the one-time `Sites.Selected` grant step and allow CSV import as a fallback.
- [Sampling misses PII deeper in a container] → The tier is human-confirmed. The report states the sample size, and the never-below-hints rule plus the owner's knowledge cover the gap. `c07-governance` rescans before exposure.
- [German PII recognizers produce false positives/negatives] → Threshold is configurable, DE_PLZ needs context, and tests cover a labelled German fixture.
- [Owners unknown or refuse ownership] → The record stays `incomplete` and blocks handoff unless waived. This makes the organisational gap visible instead of hiding it.
- [Keyword rules misclassify] → Suggestions are never final. Revert is cheap and audited.
- [Risk weights look authoritative] → They are labelled as assumptions in the output, configurable, and require approval.
- [Catalog holds metadata about personal data (data subject categories, locations)] → It is itself treated as `confidential`, access is restricted to pilot roles, and no values are stored.

## Migration Plan

New tables only, created by the `konmari` migration tool. Rollback is dropping these tables, since no source systems are modified. Rescans are idempotent.

## Open Questions

- Which CRM systems need a native API connector in v1 versus CSV import (depends on the first pilot organisations)?
- Should existing Microsoft Information Protection labels override or only inform the proposed tier?
- Is the default sample size of 200 adequate for large file shares, or should it scale with container size?
