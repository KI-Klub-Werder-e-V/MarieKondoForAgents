# Source brief: Marie Kondo for your company's data (agent-ready data)

**Marie Kondo for your company's data** means applying the KonMari method so AI agents get clean, purposeful, usable information instead of a cluttered digital attic.

Agents fail (or hallucinate, act wrongly, or become untrustworthy) when they face duplicates, stale records, missing context, conflicting sources, or poor access controls. The fix is deliberate decluttering focused on what actually enables reliable agent work.

## The KonMari rules, adapted for agent-ready data

1. **Commit yourself (and the organization).** Not a one-off cleanup. Stakeholder buy-in from data owners, domain experts, compliance, and agent-using teams. Start with a concrete pilot use case (customer-support agent, contract reviewer, revenue analyst), not "make all data AI-ready". Define success metrics before touching anything.
2. **Imagine the ideal.** Discoverable and callable (APIs, tools, MCP servers, described data products). Current, consistent, owned. Rich metadata and semantic context (definitions, owners, dates, source authority, lineage). Properly permissioned. Structured so retrieval works (chunked passages, clear table schemas). Agents know which source wins on conflict and can say "I don't know" instead of guessing.
3. **Finish discarding first.** Don't organize junk. Remove/archive duplicates and near-duplicates, outdated/superseded versions, data irrelevant to the agent's job, sensitive information that cannot be governed (or mask/anonymize). Ask: does this help the agent succeed reliably, or create noise and risk? Move the rest to cold storage.
4. **Tidy by category, not by location.** Gather each category across systems (SharePoint, CRM, warehouse): product/knowledge base & SOPs, customer records, policies & contracts, financial/operational metrics, support history. Reveals redundancy and conflict.
5. **Follow a sensible order.** Easy, low-sentiment categories first (product docs, FAQs, public policies), sensitive/complex later (customer PII, financials, legal). Within a category: clean and standardize before enriching or connecting.
6. **"Does it spark joy?" test for agents.** Authoritative and current? Owned and maintained? Business meaning clear? Can an agent retrieve a precise chunk/record? Conflicts resolved and permissions explicit? Yes → keep and organize. No → fix or discard.

## Poorly prepared vs. agent-ready

| Aspect | Poorly prepared | Agent-ready |
|---|---|---|
| Format | Whole documents, raw tables | Chunked passages / clean data products |
| Metadata | Filename or column name only | Source, date, type, owner, freshness, definitions |
| Access | Whatever the service account can see | Explicit read/write boundaries, RBAC |
| Consistency | Duplicates & conflicts | Deduplicated, source-of-truth policy |
| Update mechanism | Manual or forgotten | Scheduled pipelines with change tracking |
| Discoverability | Buried in silos | Catalogued, described, tool/MCP-exposed |

## Practical sequence

1. Scope to one use case and inventory the sources it needs (owner, update frequency, format, sensitivity).
2. Clean & standardize (dedupe, normalize dates/IDs/names, resolve conflicts).
3. Structure & enrich (chunk documents, semantic metadata, business terms).
4. Govern (access controls that travel with the data, audit logging, source policies).
5. Connect (governed interfaces: data products, function calling, MCP servers).
6. Test with real questions; monitor freshness/quality continuously.

Agent-ready data is about *purposeful clarity*, not perfect completeness. Start small, discard ruthlessly, build the sustainable system.
