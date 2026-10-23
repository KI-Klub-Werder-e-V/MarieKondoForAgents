## Context

The pilot charter (01) fixes one use case, its scope and its reviewers. The category inventory (02) lists every asset the pilot needs, with owner, update frequency, format, sensitivity and category. This change adds the KonMari "does it spark joy for agents?" test on top of that catalog. The test is applied per asset, rests on evidence, and is approved by humans. Research for every decision below is in `research/c03-joy-test-scorecard.md` (cited as R§x).

Constraints: open source and self-hostable, Python 3.12 `konmari` CLI, Postgres catalog (DuckDB for local runs), runnable by non-experts, DSGVO and EU AI Act aware, and every decision auditable.

## Goals / Non-Goals

**Goals:**
- A fixed, versioned rubric of the six brief criteria, mapped to DAMA / ISO/IEC 25012 / FAIR (R§1.2).
- Probes that pre-fill scores from catalog evidence, so humans review proposals instead of scoring from scratch.
- Keep / fix / discard proposals that a single strong criterion cannot game, with fix items routed to downstream changes.
- A category maturity view that reads like the brief's "poorly prepared vs agent-ready" table.

**Non-Goals:**
- Executing discards, archiving or deletion (04 retention-decisions).
- Fixing data: dedup (04), normalisation (05), chunking and metadata (06), ACL changes (07).
- Running retrieval evaluations (09). This change consumes their results.
- A company-wide AI maturity assessment (people, infrastructure, talent).

## Decisions

1. **Own rubric, mapped to standards, not adopted from a vendor.** No canonical AI-ready rubric exists, and published ones differ in dimensions and weights (R§1.1). We keep the brief's six criteria as the contract and store a mapping to DAMA/ISO 25012/FAIR for traceability.
   *Alternatives:* the DAMA six dimensions directly (they do not cover ownership or permissions, R§1.2); ISO/IEC 25012's 15 characteristics (too many for non-expert reviewers); the Aparavi/GetWidget composite (vendor-specific weights).

2. **0–3 ordinal scale per criterion, with `unknown` as a distinct state.** A small scale keeps reviewers consistent. `unknown` stops missing evidence from passing silently (R§1.4, our assumption).
   *Alternatives:* a 0–100 continuous score (false precision, invites gaming); a 1–5 CMMI-style scale per asset (better suited to organisational capability than to an asset).

3. **Gates instead of a pure weighted average.** `authoritative_current` and `permissions_explicit` are gates: level 0 blocks `keep`. A well-described file shared with "Everyone" is still unsafe for an agent (R§1.3, R§1.6). The rubric stores gates, so a pilot can add more gates in a new rubric version.
   *Alternatives:* a weighted composite with a threshold (hides blockers); all criteria as gates (too strict, so most assets end up as `fix` with no way to prioritise).

4. **Discard is a proposal, never an action.** ROT guidance warns that "trivial" data may be legally retained (R§1.3). The scorecard writes an approved verdict, and 04 retention-decisions executes it with retention rules. Reversal is a new review row (append-only), so history survives.
   *Alternatives:* auto-archive on discard (breaks reversibility and legal hold); a mutable verdict column (loses audit history).

5. **Evidence is referenced, not copied.** Probes store IDs, counts, hashes and timestamps, and never content from personal-data assets (GDPR Art. 5(1)(c), R§4). Evidence has a maximum age (default 7 days, our assumption). Microsoft ACL reports can lag up to 48 h (R§1.6), so the capture timestamp is mandatory.
   *Alternatives:* store snippets for reviewer convenience (a DSGVO risk); no expiry (verdicts drift from reality).

6. **Probes are pluggable, and the core has no hard dependencies.** The built-in probes read the catalog. Optional adapters ingest Soda Core check results and ODCS contract status (R§3). Retrievability uses 09's per-asset hit rate@k / MRR when present, otherwise structural proxies (a stable ID exists) (R§1.5).
   *Alternatives:* require OpenMetadata or Soda (lock-in, harder self-hosting); LLM-judged scoring (non-deterministic, hard to audit).

7. **Category rollup over the brief's six aspects with an explicit aggregation rule.** Aspect → criteria mapping: Format → `retrievable_precise`. Metadata → `meaning_clear`, `owned_maintained`. Access → `permissions_explicit`. Consistency → `conflicts_resolved`. Update mechanism → `authoritative_current` at level 3 (automated). Discoverability → `retrievable_precise` and `meaning_clear`. An aspect's share counts assets with all mapped criteria ≥ 2. `agent_ready` applies at a share ≥ 80 %, `in_progress` at a share ≥ 40 %, otherwise `poorly_prepared` (thresholds are our assumptions and configurable). The aggregation is printed in every report, because opaque aggregation is a known weakness of maturity models (R§1.4).
   *Alternatives:* averaging criterion levels (masks gaps); DCAM-style capability scoring (designed for organisations, not asset sets).

8. **Data model (append-only where decisions live).** The tables are `rubric_version` (immutable after publish), `criterion_score` (asset, criterion, level|unknown, probe, evidence_ref, evidence_at, rubric_version, run_id), `verdict` (asset, proposed, reason, fix_items, run_id), `verdict_review` (append-only: actor, action, from, to, justification, at) and `category_maturity` (a materialised view per run). Review actions also emit to 07 audit-logging, and runs emit OpenLineage-style run events.

## Risks / Trade-offs

- [Probe false positives, e.g. a legitimately static policy flagged as stale] → expected update frequency comes from the inventory and can be "never". Reviewers override with a justification, and the override is logged.
- [Reviewer rubber-stamping] → reports show the counts of overrides and acceptances per reviewer. Bulk accept is only possible for `keep` proposals that have no `unknown`.
- [Thresholds (2×, 80 %, 7 days) are assumptions] → they are configurable per pilot, stored with each run, and revisited with 01 success-metrics after the first run.
- [Rubric changes break comparability] → immutable versions. Reports refuse to compare runs across rubric versions without an explicit flag.
- [Upstream changes not yet built (04, 06, 09)] → probes return `unknown`, so assets land in `fix` rather than a false `keep`.

## Migration Plan

New tables only, created by a forward migration with a matching down migration. Rollback means running the down migration. No source data is touched.

## Open Questions

- Should high-risk pilots (EU AI Act Art. 10) get extra criteria such as representativeness, or a separate rubric version? This depends on the 01 classification.
- Who may author new rubric versions: the project maintainers only, or each pilot?
- Is 7 days the right default evidence age for slowly changing file shares?
