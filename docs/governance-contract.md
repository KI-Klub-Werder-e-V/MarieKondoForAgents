# c07 interface contract (for c08 and c09)

What `governance` hands to later changes. Consumers use these functions and views only; the tables behind them may change.

## Exposable sources (the governance gate)

```python
from konmari.governance import gate, access

gate.exposable(cat, scope)   # [{category_id, status: "approved", reasons: [], policy_version, acl_coverage}]
gate.status(cat, scope)      # every in-scope source: approved | blocked (with reasons) | acl_stale
```

- A "source" in the gate is one in-scope category of the pilot (category IDs from c02).
- `exposable()` computes the status live. Change 08 MUST call it before exposing a source and MUST NOT expose a source missing from it. Nobody can set `approved` by hand.
- The view `governance_status` (`category_id`, `status`, `reasons` JSON, `policy_version`, `checked_at`) holds the result of the latest `konmari govern check`, for reports and monitoring (c09).
- Block reasons: `policy_missing`, `policy_blocked`, `no_chunks`, `acl_coverage`, `pii_unscanned`, `pii_recall`, `leak_suite`, `audit_inactive`, and `acl_stale` (status `acl_stale`).

## Identity and retrieval

```python
ctx = access.from_token(cat, bearer_token, validator, audience="konmari")   # 401 + deny event otherwise
hits = access.retrieve(cat, ctx, query, k=5, tool="search")                # audited, fail closed
access.write(cat, ctx, "create_ticket", category, fn)                      # 403 without an active write grant
```

- Tokens must carry the KonMari audience (`aud`, RFC 8707). Delegation: `sub` = user, `act.sub` = agent (RFC 8693). A token without `act` is accepted only for an agent registered as a service principal (`on_behalf_of = none`). Tokens are never passed on to other systems.
- `validator` is `access.IntrospectionValidator(url, client_id, secret)` (RFC 7662, works with Keycloak, Authentik and Entra ID) or, for local runs, `access.HmacValidator(secret)`.
- Effective access = the agent's `read:<category>` scopes ∩ the user's principals (`user:<sub>`, `group:<g>` from the `groups` claim, `group:everyone`). Agents are registered with `konmari govern agent <id> --scope read:<category>`.
- `retrieve` reads the `agent_index` only: masked content, ACL on every row. On Postgres the filter is row-level security (`FORCE`, role `konmari_agent`, `SET LOCAL app.principals` per transaction). On DuckDB the same predicate is part of the SQL. Sources with an overdue ACL sync return nothing.
- Every call writes one `agent.tool_call` audit event: agent, on-behalf-of user or `none`, tool, `read`/`write`, resource IDs, `allow`/`deny` with reason, active policy versions, trace ID. It holds no prompt, completion, argument or chunk text. If the event cannot be written, the call raises `AuditUnavailable` and returns no data.
- OpenTelemetry: each call is an `execute_tool <tool>` span (`gen_ai.operation.name`, `gen_ai.tool.name`, `gen_ai.agent.id`), content capture off, exceptions not recorded. The audit event's `trace_id` is the span's trace ID. Configure the OTel SDK and exporter in the server process; the kit depends on the API only.

## Leak suite (for c09)

`gate.run_leak_suite(cat, scope, suite, actor)` with `suite = {"principals": [{id, user, groups, agent?}], "queries": [...]}`. It probes with queries built from every chunk a test principal must not read and records the run in `leak_run`. c09 reuses it in its evaluation runs.

## Audit trail

One table, `audit_event`, for every step (c01–c09). `konmari audit verify` checks the whole hash chain, `konmari discard log verify` checks the same chain. Write events with `konmari.governance.audit.write_event(cat, action, actor=..., **ids)`; it rejects payload fields (`prompt`, `completion`, `args`, `arguments`, `content`, `text`, `query`).

## ACL sync lag (for c09 freshness monitoring)

Table `acl_sync` (`policy_id`, `started_at`, `status` ok/failed, `lag_seconds`, `items`, `unknown`, `revocations`). `acl.stale(cat, category)` is true when the last successful sync is older than the policy's `acl_sync_cadence` (default `PT24H`).
