# Connect guide (step 8 of the KonMari-Kit: "connect")

After governance (step 7) the pilot data is clean, described and permissioned, but agents still cannot use it reliably. This step turns it into **data products**: described, versioned interfaces with an owner, a purpose, field definitions, quality rules and a freshness promise. An **MCP server** (`konmari serve`) offers them to agents as read-only tools.

This is not legal advice. Your data protection officer reviews each product's purpose before the first publish (checklist in section 7).

## 1. Who decides what

| Decision | Who |
|---|---|
| Write the descriptor (`product.yaml`, `contract.yaml`) | facilitator or data owner, in the git repository |
| Approve (`product approve`) and publish (`product publish`) a version | only the product owner (`owner` in `product.yaml`) |
| Roll back (`product publish --rollback --reason ...`) | only the product owner |
| Review and pin a tool definition (`serve --approve-tool`) | a second person from the charter (e.g. `data_steward`) |
| Purpose statement and personal-data justification | compliance reviews before the first publish |

Every approval, publish, rollback, tool approval and agent call is in the audit trail (`konmari audit verify`).

## 2. The first product in STDIO mode

STDIO means the agent host (e.g. a desktop assistant) starts `konmari serve` itself on the same machine. No network port is opened.

```
konmari product init support-faq --category kb-and-sops --owner ben --use-case "Member FAQ assistant"
# fill in products/support-faq/product.yaml and contract.yaml: replace every TODO
konmari product lint products/support-faq --as ben     # schemas, mandatory elements, upstream steps; registers the draft
konmari product refresh support-faq --as ben           # load from the source (changed rows only)
konmari product test support-faq                       # quality rules on the loaded data
konmari product approve support-faq --version 1.0.0 --as ben
konmari product publish support-faq --version 1.0.0 --as ben
konmari serve --check-pins                             # new tools wait for review (exit 1)
konmari serve --print-tools                            # read the definitions
konmari serve --approve-tool support_faq.v1.search --as clara
konmari serve --approve-tool support_faq.v1.get --as clara
konmari product list                                   # catalog: versions, freshness, tools
```

`lint` also checks the earlier steps: the use case is in the approved charter, every source has a `keep` verdict, the declared source of truth matches the step 5 policy, there is an approved step 7 source policy, and every personal-data field is masked (a rule for its `piiEntity` in that policy) or justified in `personalDataJustification`.

The agent's identity comes from environment variables, e.g. in the agent host's configuration:

```
KONMARI_AGENT=faq-bot                  # registered with "konmari govern agent faq-bot --scope read:kb-and-sops"
KONMARI_ON_BEHALF_OF=finn              # the user the agent acts for (omit only for service agents)
KONMARI_GROUPS=support-team
KONMARI_SCOPES=product:support-faq:read
command: konmari serve --stdio
```

Without `KONMARI_AGENT` the server refuses to start.

## 3. What an agent gets

- Per product and major version two tools: `<product>.v<N>.search` (keywords, filters, at most 100 records per page, `next_cursor` for the next page) and `<product>.v<N>.get` (one record by key). No SQL, no file paths, no writes.
- Only the contract's fields. Personal data is masked per the source policy; rows the person may not see are filtered out.
- In every result: product, version, `as_of` and `fresh`/`stale`. When the data is older than the freshness SLA, the text warns, so the agent can say the data may be outdated.
- Resources: `konmari://catalog` (all callable products) and per version `konmari://products/<id>/<version>` (descriptor) plus `.../docs`.
- Limits in `connect.yaml`: `limits: {default_records: 100, max_records: 1000, calls_per_minute: 60, timeout_seconds: 10}`, `deprecation_days: 90`, `protocol_version: "2026-07-28"`, `fallback_protocol_version: "2025-11-25"` (set to `null` once no older agent host connects).

## 4. Reviewing and pinning tools

Every tool definition gets a SHA-256 fingerprint. The server shows a tool only while its fingerprint matches the approved one. When a definition changes (new version, other purpose, new field), the tool disappears until someone approves it again. This blocks silently changed tools ("rug pull"). Record content never goes into a definition.

## 5. New versions, refresh, rollback

- `konmari product diff products/support-faq` compares with the published version: removed field or changed type = **major** (2.0.0), new field = **minor** (1.1.0), description only = **patch** (1.0.1). A version number that is too small is refused at publish.
- After a new major version, the old one stays callable for 90 days as deprecated, with successor and sunset date.
- Refresh without manual steps: `konmari product schedule support-faq` (cron line) or `--format systemd` (timer). The interval is half the freshness SLA. If a quality test fails, the old data and its `as_of` stay; the failed run is in `refresh_run` for monitoring (step 9).
- Deleted source rows and records archived or erased in step 4 leave the product at the next run.
- Roll back: `konmari product publish support-faq --rollback --reason "wrong field" --as ben`. The previous version is current again; the rolled-back one is withdrawn and gone for agents without a restart.
- Turn agent access off entirely: stop `konmari serve`. The data is not touched.

## 6. HTTP with OAuth (e.g. Keycloak)

For several users on a network, the server runs over HTTP as an OAuth 2.1 resource server per the MCP specification. In Keycloak: one realm for the pilot, one client per agent, client scopes `product:<id>:read`, and an audience mapper for the server's address. `connect.yaml`:

```yaml
http:
  resource: https://konmari.club.example/mcp          # canonical URI = expected audience
  authorization_servers: [https://id.club.example/realms/pilot]
  issuer: https://id.club.example/realms/pilot
  jwks_url: https://id.club.example/realms/pilot/protocol/openid-connect/certs
  # or: introspection: {url: ..., client_id: konmari, secret_env: KONMARI_INTROSPECTION_SECRET}
```

```
konmari serve --http --host 127.0.0.1 --port 8765                       # without TLS: localhost only
konmari serve --http --host 0.0.0.0 --tls-cert cert.pem --tls-key key.pem
```

The server publishes `/.well-known/oauth-protected-resource/mcp`, answers requests without a valid token with 401 pointing to it, rejects tokens for other services, expired tokens and tokens in the URL, and answers 403 `insufficient_scope` when the product's scope is missing. Tokens are never passed on to source systems.

## 7. Checklist for the data protection officer (before the first publish)

- [ ] The purpose (`description.purpose`) is a use case of the approved charter and nothing beyond it.
- [ ] `description.limitations` says what the product is not for (e.g. no individual decisions).
- [ ] Every field with tier `personal` or `special-category` is masked, or the reason in `personalDataJustification` holds (data minimisation, Art. 5(1)(c) GDPR).
- [ ] Fields the purpose does not need are not in the contract (then they are not copied either).
- [ ] Row permissions (`aclColumn` or the source policy principals) match the roles in the organization.
- [ ] The product is in the record of processing activities (`konmari govern ropa`).

Interface for step 9: [connect-contract.md](connect-contract.md).
