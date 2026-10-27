# Connect an agent

Two different things are connected in step 8 and 9:

1. **Your agent reads the pilot data** through `konmari serve` (MCP, read-only).
2. **The evaluation asks your agent** through the agent contract in `eval.yaml`.

## 1. konmari serve in Claude Code

Only after at least one product is published and its tools are approved
(`konmari serve --check-pins` exits 0). The server must run in the pilot folder and needs an
agent registered in step 7 (`konmari govern agent <agent> --scope read:<category> --as <you>`).

```
claude mcp add konmari-<pilot> --scope local \
  -e KONMARI_AGENT=<agent> -e KONMARI_ON_BEHALF_OF=<stakeholder id> \
  -e KONMARI_SCOPES=product:<product>:read \
  -- sh -c 'cd /path/to/pilot && exec uvx --from git+https://github.com/KI-Klub-Werder-e-V/MarieKondoForAgents konmari serve --stdio'
```

Inside a clone use `uv run --project /path/to/clone konmari serve --stdio` after the `cd`. Add
`-e KONMARI_CATALOG=postgresql://...` for a Postgres catalog. Check with `claude mcp list`; in a
session `/mcp` shows the tools `<product>.v<N>.search` and `<product>.v<N>.get` and the resources
`konmari://catalog` and `konmari://products/<id>/<version>`.

Without `KONMARI_AGENT` the server refuses to start. Rows and fields the person may not see are
filtered; personal data is masked per the source policy. Every call is in the audit trail.
To turn agent access off, remove the server (`claude mcp remove konmari-<pilot>`); data is untouched.

For several users on a network use `konmari serve --http` with OAuth (see `docs/connect-guide.en.md`, section 6).

## 2. The agent contract in eval.yaml

`konmari eval run` starts your agent as an MCP server and calls one tool per question, with the
rights of a test principal:

```yaml
agent: {command: [uv, run, python, agent/server.py], tool: ask, version: faq-bot@1.4, timeout_seconds: 30}
principal: {agent: eval-bot, user: test-eval, groups: [support-team]}
judge: {provider: lexical}    # or {provider: ragas, model: ..., version: ..., base_url: ...}
```

The tool receives the question and returns JSON with `answer`, `behaviour` (`answer`, `abstain`,
`flag_conflict`), `retrieved` (chunk IDs) and `statements` (each with its `citations`). Your agent
gets its context from the `konmari serve` tools above. The default `lexical` judge needs no model;
a model judge is self-hosted or runs under a data processing agreement the charter allows. A
minimal stub agent for tests is `tests/stub_agent_stdio.py` in the repository.
