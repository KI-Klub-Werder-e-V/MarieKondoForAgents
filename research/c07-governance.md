# 07 Governance: research notes

KonMari step 4 ("govern"): access controls that travel with the data, PII handling, audit logging of agent access, source policies, and the DSGVO / EU AI Act duties that come with them. Research date: 2026-10-23. All numbers below come from a cited source, or are labelled **[assumption]**.

## 1. Key findings

### 1.1 Permissions must travel into the index (permission-aware retrieval)

- Embedding a document drops its ACL. A naive "embed everything, top-k" RAG pipeline matches on similarity, not authorization, and leaks restricted content to whoever asks [S1, S2, S3].
- The consensus pattern: tag every chunk at index time with normalized ACL metadata taken from the source system (group IDs, not user IDs), and apply a permission filter **inside** the retrieval query, before anything reaches the model [S2, S3, S4]. Filtering after generation is too late, because the model has already seen the text [S3].
- Further practitioner rules [S3, S4]: propagate the end-user identity, never a broad service account; deny overrides grant; keep ACLs fresh, since a stale revocation is a live leak. Per-chunk ACL copies make every share/unshare event fan out to many chunks [S3], so ACL sync needs its own job and a freshness metric.
- Two architectural alternatives:
  - **Pre-filter with ReBAC "list objects"**: ask an authorization service (OpenFGA, SpiceDB; Zanzibar-style) which object IDs the user may read and pass that list as a vector-store filter [S5, S6]. OpenFGA adds `ListObjects` on top of Zanzibar's `Check` [S5].
  - **Check between retrieval and generation**: run a per-hit `Check` before chunks enter the prompt [S1]. Simple, but it degrades recall when many hits are dropped.
- Postgres row-level security (RLS) works with pgvector: a policy compares a column to `current_setting('app.…')`, `FORCE ROW LEVEL SECURITY` covers the table owner, and the context must be set with `SET LOCAL` inside a transaction, or it bleeds across pooled connections [S7, S8]. Table owners bypass policies unless forced [S8]. RLS covers rows only; it does not hide the schema [S8]. Index the policy columns, and check the planner with `EXPLAIN` on realistic data [S8].

### 1.2 Agent identity vs. on-behalf-of user

- MCP authorization (2025-06-18 revision onward) builds on OAuth 2.1. The MCP server is a resource server; a separate authorization server issues tokens; clients discover it via RFC 9728 protected-resource metadata [S9, S10]. Clients use RFC 8707 resource indicators so tokens are bound to the intended audience [S9].
- **Token passthrough is forbidden**: "MCP servers MUST NOT accept any tokens that were not explicitly issued for the MCP server" [S11]. Passthrough breaks accountability, because the server cannot tell clients apart and downstream logs show the wrong identity [S11].
- Scope minimization [S11]: start with a minimal read-only scope, escalate step by step via `WWW-Authenticate scope=` challenges, avoid wildcard or omnibus scopes, log elevation events with correlation IDs, and never treat claimed scopes as a substitute for server-side authorization logic.
- Session rules [S11]: MCP servers MUST verify every inbound request, MUST NOT use sessions for authentication, and SHOULD bind session data to the user (`<user_id>:<session_id>`).
- **Delegation**: OAuth 2.0 Token Exchange (RFC 8693) issues a new token where `sub` is the user and `act.sub` is the acting agent. Only the current actor is enforceable; nested `act` claims are informational. Without an actor token the result is impersonation, not delegation [S12, S13]. This gives the audit log a natural (user, agent) pair.
- The 2025-11-25 MCP revision changes client registration details (Client ID Metadata Documents; Dynamic Client Registration becomes MAY) [S10]. We did not verify this against the spec text; pin the protocol version per deployment.

### 1.3 Excessive agency and disclosure

- OWASP Top 10 for LLM Applications 2025: **LLM02 Sensitive Information Disclosure** (moved up from #6 to #2) and **LLM06 Excessive Agency** (new category) [S14, S15]. Mitigations include output filtering/redaction, access controls and human oversight [S14]. These map directly onto least-privilege read/write boundaries and masking.

### 1.4 PII detection and masking

- **Microsoft Presidio** (MIT): Analyzer (NER + regex + rules + checksums, custom recognizers) and Anonymizer (operators: replace, redact, mask, hash, encrypt, custom) [S16, S17].
- **German needs configuration**: "In its default configuration, it contains recognizers and models for English" [S18]. For German you provide a German NLP model (spaCy, Stanza or transformers) through `NlpEngineProvider`, register recognizers with `supported_language="de"` and German context words, and call `analyze(language="de")`. Regex logic is language-agnostic; context words are not [S18]. spaCy ships `de_core_news_sm/md/lg` (lg about 560 MB) [S19, S20].
- Presidio publishes no recall guarantee for German. We therefore require a measured recall on a labelled pilot sample. The threshold is **[assumption]**: recall ≥ 0.95 on direct identifiers (name, e-mail, phone, IBAN) before an index may be built from a category that contains personal data.
- **Pseudonymised data is still personal data** (EDPB Guidelines 01/2025, Art. 4(5) GDPR). Pseudonymisation is a risk-reducing measure and needs no separate legal basis [S21, S22]. The guidelines were adopted for public consultation in January 2025; we did not confirm final adoption [S21, S23]. Consequence: the re-identification key or mapping table has to be stored separately, access-controlled and audited, and masked data stays in DSGVO scope.

### 1.5 Audit logging

- **EU AI Act Art. 26(6)**: deployers of high-risk AI systems "shall keep the logs automatically generated by that high-risk AI system … to the extent such logs are under their control, for a period appropriate to the intended purpose … of at least six months, unless provided otherwise in applicable Union or national law, in particular in Union law on the protection of personal data" [S24, S25]. Most pilot agents (support, knowledge Q&A) are **not** Annex III high-risk. We still adopt 6 months as the default retention floor because it is the only concrete number the Act gives **[assumption: policy choice]**, with DSGVO storage limitation (Art. 5(1)(e)) as the ceiling.
- Art. 26(11): Annex III deployers that decide or assist decisions about people must inform those people [S25].
- **Timing**: Art. 4 (AI literacy) has applied since 2 Feb 2025; Art. 50 transparency (tell users they are talking to an AI) applies from 2 Aug 2026 [S26, S27]. Secondary sources report a "Digital Omnibus" amendment that moves Annex III high-risk obligations to Dec 2027 and leaves Art. 4, 5 and 50 unchanged [S26]. Not verified against the Official Journal.
- **OpenTelemetry GenAI semantic conventions** define `invoke_agent` and `execute_tool` spans with `gen_ai.agent.name` and the tool name. Prompt, completion and tool-argument content is **not captured by default**, because it routinely contains PII. Status: Development/experimental, so pin versions [S28, S29]. This lets audit events double as trace spans without writing payloads.
- **DSGVO**: an agent use case is its own processing activity in the Art. 30 record (purpose, data categories, recipients, retention) [S30]. Bavarian LDA guidance asks controllers to check and document whether a DPIA under Art. 35 is required [S31]. Tamper-evident agent interaction logs are recommended by vendors, not mandated by a regulator [S30]. We use a hash chain as cheap tamper evidence.
- NIST AI 600-1 (GenAI Profile) names data privacy and information security among the GenAI risks and suggests periodic audits and monitoring of outputs for sensitive-data exposure [S32, S33].

## 2. Concrete thresholds / metrics

| Metric | Value | Source |
|---|---|---|
| Log retention floor (high-risk deployers) | ≥ 6 months | EU AI Act Art. 26(6) [S24] |
| Log retention for pilot (non-high-risk) | 6 months default, max set in Art. 30 record | [assumption, policy] |
| OAuth `state` lifetime (MCP proxy) | short, e.g. 10 min, single-use | MCP security best practices [S11] |
| PII recall gate (German direct identifiers) | ≥ 0.95 on a labelled sample of ≥ 200 records/chunks | [assumption] |
| Permission-leak tests | 0 unauthorized chunks returned in the leak test suite | [assumption, hard gate] |
| ACL sync lag (source revoke → index) | ≤ 24 h for pilot; revocations logged | [assumption] |
| Audit coverage | 100 % of agent tool calls produce an audit event | [assumption, hard gate] |

## 3. Recommended OSS tools

- **Postgres RLS** (+ pgvector): already in the default stack; enforces row/chunk permissions in the database itself [S7, S8].
- **OpenFGA** (Apache-2.0, CNCF): Zanzibar-style ReBAC with `Check` and `ListObjects`, for when source ACLs are group/folder hierarchies [S5]. **SpiceDB** is an equivalent alternative [S6].
- **Open Policy Agent / Cedar**: ABAC policy-as-code for tool-level allow/deny decisions **[assumption: not researched in depth here]**.
- **Microsoft Presidio**: PII detection and anonymization with pluggable German NLP [S16, S18].
- **spaCy `de_core_news_lg`**: German NER model for Presidio [S19].
- **Keycloak** (Apache-2.0): self-hostable OAuth 2.1/OIDC authorization server with token exchange **[assumption: token-exchange support level to verify per version]**.
- **OpenTelemetry SDK** with GenAI semconv: trace and audit correlation without payload capture [S28].

## 4. Pitfalls

1. Indexing with a service account that sees everything, which then becomes the agent's effective permission set [S3, S4].
2. Post-generation filtering, after the model has already read restricted text [S3].
3. Stale ACLs after an unshare or a person leaving [S3].
4. `SET` instead of `SET LOCAL` behind a connection pool, so the user context leaks to the next request [S7]. Table owner bypassing RLS [S8].
5. Token passthrough to downstream APIs, and omnibus scopes [S11].
6. Assuming Presidio works for German out of the box [S18].
7. Treating pseudonymised data as anonymous [S21].
8. Logging full prompts or tool arguments in the audit log, which turns the log into a PII store. OTel deliberately avoids this by default [S28].
9. Unbounded log retention, which violates storage limitation; the reverse mistake is deleting before the 6-month floor where Art. 26 applies [S24].
10. Write tools exposed by default (excessive agency, OWASP LLM06) [S14].

## 5. Sources

- [S1] Descope via NHI Mgmt Group, "ReBAC is the missing permission layer for enterprise RAG pipelines", 2026 — https://nhimg.org/articles/rebac-is-the-missing-permission-layer-for-enterprise-rag-pipelines/
- [S2] Pinecone, "RAG with Access Control" — https://www.pinecone.io/learn/rag-access-control/md/
- [S3] Tian Pan, "Permission-Aware Retrieval…", 2026 — https://tianpan.co/blog/2026-05-04-permission-aware-retrieval-enterprise-rag-access-control
- [S4] Wavect, "RAG permissions: SharePoint, Confluence, Drive" — https://wavect.io/blog/rag-permissions-sharepoint-confluence-drive/
- [S5] OpenFGA docs, Zanzibar / ReBAC — https://openfga.dev/docs/learn/zanzibar , https://openfga.dev/docs/authorization-concepts
- [S6] PyCon DE 2025 talk (ReBAC for RAG, SpiceDB) — https://2025.pycon.de/talks/GRWYQB
- [S7] pg_trickle blog, multi-tenant vector search with RLS — https://api.pgxn.org/src/pg_trickle/pg_trickle-0.83.0/blog/multi-tenant-vector-search-rls.md
- [S8] EDB, "Securing pgvector" — https://www.enterprisedb.com/docs/pg_extensions/pgvector/security/ ; Supabase, "RAG with Permissions" — https://supabase.com/docs/guides/ai/rag-with-permissions
- [S9] MCP spec 2025-06-18, Authorization — https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization
- [S10] Aaron Parecki on the MCP spec — https://aaronparecki.com/2025/06/18/10/mcp-spec ; Wavect, enterprise MCP authorization — https://wavect.io/blog/enterprise-mcp-authorization-architecture/
- [S11] MCP Security Best Practices — https://modelcontextprotocol.io/specification/2025-06-18/basic/security_best_practices
- [S12] WSO2 Identity Server, token exchange for agents — https://is.docs.wso2.com/en/next/guides/agentic-ai/ai-agents/token-exchange-for-agents/
- [S13] MojoAuth, RFC 8693 for agent delegation — https://mojoauth.com/blog/oauth-2-0-token-exchange-rfc-8693-for-agent-delegation-a-worked-example ; RFC 8693 — https://datatracker.ietf.org/doc/html/rfc8693
- [S14] Invicti, OWASP Top 10 LLM 2025 — https://invicti.com/blog/web-security/owasp-top-10-risks-llm-security-2025
- [S15] HackerOne, OWASP Top 10 for LLMs 2025 — https://www.hackerone.com/blog/owasp-top-10-llms-2025-how-genai-risks-are-evolving
- [S16] Presidio overview — https://docsearch.algolia.com/mcp/docs/repo/data-privacy-stack/presidio
- [S17] Presidio practical guide — https://medium.com/@nkbvikram/presidio-by-microsoft-a-practical-guide-to-detecting-and-masking-pii-at-scale-c3b39ce4f52c
- [S18] Presidio docs, languages — https://raw.githubusercontent.com/microsoft/presidio/main/docs/analyzer/languages.md
- [S19] spaCy de_core_news_lg — https://huggingface.co/spacy/de_core_news_lg
- [S20] spaCy models — https://spacy.io/usage/models
- [S21] Hunton, EDPB pseudonymisation guidelines — https://www.hunton.com/insights/publications/edpb-advises-on-pseudonymisation-for-gdpr-compliance
- [S22] Taylor Wessing on EDPB Guidelines 01/2025 — https://taylorwessing.com/-/media/taylor-wessing/files/germany/2025/01/tw25_edpb-guidelines-1-2025.pdf
- [S23] PPC Land, EDPB pseudonymisation guidelines — https://ppc.land/european-data-regulators-release-updated-pseudonymisation-guidelines-for-2025/
- [S24] EU AI Act Service Desk, Article 26 — https://ai-act-service-desk.ec.europa.eu/en/ai-act/article-26
- [S25] artificialintelligenceact.eu, Article 26 — https://artificialintelligenceact.eu/article/26/
- [S26] Dastra, AI Act timeline — https://www.dastra.eu/en/blog/ai-act-timeline-key-compliance-deadlines-under-the-european-artificial-intellig/60278
- [S27] Ropes & Gray, AI Act transparency obligations — https://www.ropesgray.com/en/insights/viewpoints/102nfqm/you-talkin-to-me-operationalising-the-eu-ai-acts-transparency-obligations
- [S28] OpenTelemetry, GenAI agent spans — https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans
- [S29] Greptime, OTel GenAI semconv timeline — https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions
- [S30] compound.law, KI im Kundenservice DSGVO — https://compound.law/de-DE/compliance/ki-kundenservice-datenschutz/
- [S31] BayLDA, KI-Checkliste — https://www.lda.bayern.de/media/ki_checkliste.pdf
- [S32] Hunton, NIST GenAI Profile data protection actions — https://www.hunton.com/privacy-and-cybersecurity-law-blog/nist-generative-ai-profile-highlights-actions-for-addressing-data-protection-risks-associated-with-generative-ai
- [S33] Anaconda, NIST AI 600-1 guide — https://www.anaconda.com/blog/nist-ai-600-1-generative-ai-profile-guide
