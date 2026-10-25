# Step 8: data products and the MCP server

Full guide: `docs/connect-guide.en.md`. Wiring into Claude Code: `connect-agent.md`.

## What it does

Turns governed data into versioned data products (ODPS descriptor plus ODCS contract) with an
owner, purpose, fields, quality rules and a freshness promise, and serves them read-only over MCP:
per product and major version a `search` and a `get` tool, results with `as_of` and `fresh`/`stale`.

## Commands

```
konmari product init <product> --category <category> --owner <owner> --use-case "<use case from the charter>"
konmari product lint products/<product> --as <you>
konmari product refresh <product> --as <you>
konmari product test <product>
konmari product approve <product> --version 1.0.0 --as <owner>
konmari product publish <product> --version 1.0.0 --as <owner>
konmari product list
konmari serve --check-pins
konmari serve --print-tools
konmari serve --approve-tool <tool> --as <second person, e.g. data steward>
konmari categories step <category> connect --status complete --as <category approver>
```

## Reading the output

- `lint` checks the descriptor and every earlier step: use case in the charter, `keep` verdicts,
  step 5 policy, approved step 7 policy, every personal-data field masked or justified.
- `serve --check-pins` exits 1 while a tool definition waits for review; a changed definition
  disappears until it is approved again.
- `product diff products/<product>` tells major / minor / patch; a too-small version is refused.

## Common blockers

- Fill every `TODO` in `product.yaml` and `contract.yaml` with the owner; the purpose must be the
  charter's use case and nothing more. Compliance reviews it before the first publish.
- Only the product owner approves and publishes; tool approval needs a second person.
