# Contributing

Issues and pull requests are welcome in English or German.

## What helps most

1. **Pilot reports.** Tried the kit on a real use case? Open a
   [pilot report](https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten/issues/new?template=pilot.yml),
   including failed attempts. No real personal data.
2. **Connectors** for more systems: Nextcloud, Confluence, Google Drive, CRMs.
3. **Examples and translations**, especially German test data without personal data.

## Workflow

- Open an issue first for anything larger than a small fix.
- Behaviour changes go through an [OpenSpec](https://github.com/Fission-AI/OpenSpec) change:
  `/opsx:propose` (or by hand in `openspec/changes/<name>/`), then `/opsx:apply <name>`.
  Bug fixes that don't change requirements need no change.
- Every `#### Scenario` in a spec has a test, named after it, that asserts the THEN clause.
- Before opening a pull request:

  ```bash
  uv run pytest -q
  openspec validate --all --strict
  ```

- Thresholds and statistics need a source in `research/` or are marked there as a default to
  calibrate.
- No real personal or confidential data in issues, tests, fixtures or examples.

Rules that are easy to break (append-only audit trail, human approvals, reversible decisions) are
in [`AGENTS.md`](AGENTS.md). They apply to people and coding agents alike.

Contributions are licensed under [Apache-2.0](LICENSE). Please follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
