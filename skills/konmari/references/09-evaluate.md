# Step 9: evaluate and monitor

Full guide: `docs/evaluate-guide.en.md`. CI example: `examples/ci/konmari-eval.yml`.

## What it does

Asks the agent real golden-set questions through its MCP interface as a test principal and scores
faithfulness, retrieval, citations (exist, permitted, current) and abstention, with gates against
an approved baseline. Then monitors freshness, volume, metadata, duplicates and schema of every
source and product and routes incidents to the owner.

## Commands

```
konmari eval items import <questions.yaml> --as <you>
konmari eval items approve <item> --as <domain expert or source owner>
konmari eval set activate --as <you>
konmari eval run --as <you>
konmari eval report
konmari eval gate
konmari eval baseline approve <run> --as <named person>
konmari monitor run --dry-run
konmari monitor run --as <you>
konmari monitor schedule
konmari monitor incident acknowledge <id> --reason "<reason>" --as <owner>
konmari metrics measure <metric> --kind gate --step evaluate --value <value> --method "<method>" --as <metric owner>
konmari categories step <category> evaluate --status complete --as <category approver>
```

## Reading the output

- Golden set minimums: ≥ 50 questions (pilot may lower to 30 in `metrics.yaml`), ≥ 20 % expecting
  `abstain`/`flag_conflict`, ≤ 50 % synthetic. Questions from user logs are masked before storage.
- Report: `faithfulness` (`unsupported_claims`), `context_recall` (`missing_sources`),
  `citation_check` (`stale_source`, `out_of_scope`), `abstention_rate` (`hallucinated_answer` =
  the agent guessed), `answer_rate` (`over_abstention`). Failing categories become fix candidates
  in the scorecard. When all pass, the kit proposes the next category; the use case owner confirms
  it with `eval next-category`.
- `monitor run`: warn past 1× the update interval, error past 2×; one incident per asset and check.

## Common blockers

- Reference answers and expected sources come from domain experts. Draft wording, never answers.
- `eval run` exit 1: the agent in `eval.yaml` is unreachable (see `connect-agent.md`).
- Gate thresholds change only through the charter's success metrics.
