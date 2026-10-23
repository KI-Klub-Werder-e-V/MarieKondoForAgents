## Context

This is the first change in the KonMari-Kit sequence (01 → 09). Nothing exists yet: no specs, no catalog. Every later step must be able to answer "which use case, who decides, what counts as success, and is it allowed?" before touching data. Research: research/c01-pilot-charter.md (Gartner on use-case-specific readiness §1, NIST AI RMF MAP §2, DAMA-DMBOK roles §3, GDPR/BDSG/AI Act §4, metrics §5, cadence §6, tools §7).

Users are SMEs, associations and public bodies in Germany/EU running the method themselves or with a volunteer or consultant as facilitator. Many have no data team, so the charter must be a readable YAML file plus a short CLI, not a platform.

Constraints: open source only, self-hostable, Python 3.12 CLI `konmari`, Postgres catalog (DuckDB for local runs), auditable decisions.

## Goals / Non-Goals

**Goals:**
- One use case per charter, with explicit scope and misuse cases (NIST MAP 1.1, MAP 3.3).
- Stakeholder register and RACI with exactly one Accountable per activity (DMBOK owner/steward/custodian split).
- DPIA screening and AI Act classification as blocking fields (GDPR Art. 35, AI Act Art. 26).
- Baseline before target, before any data work (Google Rules of ML, Rule #2).
- Human approval, versioning, diff, audit trail; reversible by amendment.
- A single gate function that downstream changes call.

**Non-Goals:**
- Running the DPIA itself or giving legal advice; the charter only records the screening decision and who made it.
- Computing readiness scores (`c03-joy-test-scorecard`) or running agent evaluations (`c09-evaluate-and-monitor`); this change only defines the metrics and stores measurements.
- Full audit-log infrastructure (`c07-governance`, audit-logging); this change writes to a minimal append-only table that 07 later adopts.
- Authentication and SSO; approver identity is the OS user plus a declared stakeholder ID (see Risks).
- A web UI.

## Decisions

1. **YAML files in the engagement repo plus catalog rows**, not catalog-only. Files are diffable in Git and readable by non-experts; the catalog holds versions, approvals and measurements for querying and audit. Alternative: catalog-only with a web form (rejected: needs a UI and hides changes from review). Alternative: Markdown charter (rejected: not machine-validatable).

2. **JSON Schema validation via `jsonschema`** for `charter.yaml` and `metrics.yaml`, plus Python checks for cross-field rules (one Accountable, owner exists in register). Alternative: Pydantic models (viable, but JSON Schema is language-neutral and can be published for other tools). Research §7.

3. **Fixed role vocabulary from DAMA-DMBOK** (`sponsor`, `use_case_owner`, `data_owner`, `data_steward`, `data_custodian`, `compliance`, `agent_user`, `facilitator`) and the RACI rule "exactly one A". Owners are business people, custodians are IT (research §3). The facilitator is never Accountable, so decisions stay with the organization. Alternative: free-text roles (rejected: downstream changes 04/05/06 need to look up "data owner of source X" reliably). The vocabulary maps onto the ODCS `team` role field for later export (research §7).

4. **Compliance fields are blocking, but content is the organization's.** DPIA screening `pending` and AI Act `pending` block approval; `high` needs an extra compliance approval; `prohibited` cannot be approved. GDPR Art. 35(2) requires seeking the DPO's advice, and BDSG §38 makes a DPO common in German organizations (research §4). Alternative: warnings only (rejected: the brief requires governance before connection, and a warning is easy to ignore).

5. **Approval = identity + timestamp + version + SHA-256 of canonical content** (YAML parsed, keys sorted, serialized as JSON). Detects edits after approval without needing signatures. Alternative: GPG-signed commits (rejected for now: too hard for non-experts; can be added later).

6. **Amend, never delete.** Each amendment creates a version, resets status to `draft`, and requires a reason. Revert = amend to an earlier version's content. This keeps the KonMari "reversible, audited" rule.

7. **Gate tiers from vendor practice (must_have / target / stretch) and only must_have blocks** (research §5). Numeric targets are left to the organization; vendor numbers (e.g. 20–40% deflection) are shown in docs only as discussion anchors, labelled as vendor claims.

8. **Golden set minimum: 30 questions, ≥5 abstention cases** (research §5, practitioner guide; our adopted default). Authors must be agent users or domain experts. Ragas metric names (Faithfulness, Context Recall, Agent Goal Accuracy) are offered as formula templates, but the Ragas dependency belongs to `c09-evaluate-and-monitor`, not here.

9. **`not_yet_measured` baseline lets only the inventory step pass.** Inventory (02) reads metadata, so it is useful while baselines are still being measured; discard and later steps change data and need the baseline. Alternative: block everything (rejected: some baselines, like current handling time, need a few weeks of sampling and inventory can run in parallel). Our assumption, research §5.

10. **Cadence breach is a status warning, not a hard gate.** Steering older than interval + 7 days is reported (7-day grace is our assumption). Hard-blocking on missed meetings would stall work for calendar reasons.

11. **Catalog schema**: `charter_version`, `stakeholder`, `raci_assignment`, `metric_set_version`, `metric_definition`, `metric_measurement`, `approval`, `decision_log`, `audit_event` (append-only). Postgres and DuckDB share the same DDL subset; migrations with plain SQL files.

## Risks / Trade-offs

- [Approver identity is self-declared, not authenticated] → Record OS user and stakeholder ID; document that organizations needing stronger proof use signed Git commits; revisit when `c07-governance` adds access control.
- [Organizations fill compliance fields with placeholders to pass the gate] → Require a written reason of at least 20 characters and the compliance stakeholder's ID; approval of a `high` classification needs a separate compliance approval.
- [Vendor KPI numbers are read as benchmarks] → Docs label them as vendor claims; the schema has no default target values.
- [Moving goalposts after poor results] → `target_lowered_after_measurement` flag in audit and report.
- [Golden questions contain personal data] → Validation rule tied to DPIA screening; docs recommend paraphrasing real tickets.
- [Too much ceremony for a small association] → `konmari charter init --template minimal` pre-fills cadence and RACI; a full charter fits on two printed pages.
- [AI Act Art. 4 text may have changed with the 2026 Digital Omnibus] → The field records the briefing date only and makes no legal claim; docs link to EUR-Lex.

## Migration Plan

New capability, no migration. Rollback: drop the new catalog tables; YAML files remain in the engagement repo.

## Open Questions

- Should the charter export to ODCS `team` blocks now or in `c08-connect-agent-interfaces`? Current plan: in 08.
- German-language templates and CLI messages in this change or a separate i18n change? Current plan: English and German templates here, CLI messages English only.
- Is the 7-day cadence grace and the 30/5 golden-set minimum right for small organizations? Revisit after the first pilot.
