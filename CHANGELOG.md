# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: [SemVer](https://semver.org/).

## [0.1.0] - 2026-10-27

First public release.

### Added

- `konmari` CLI covering nine gated steps for one pilot use case: charter and success metrics,
  inventory and categories, joy-test scorecard, discard and retention, normalisation and
  source-of-truth policy, chunking/metadata/glossary, governance and audit, data products with a
  read-only MCP server, evaluation and monitoring.
- `konmari next`: read-only report of pilot state and the next command.
- Claude Code plugin `konmari` with a skill that operates the CLI and asks before every approval.
- DuckDB and Postgres 16 catalogs with the same migrations; hash-chained audit trail.
- Fictional example pilot `examples/verein-faq-assistant`.
- Guides in English and German, OpenSpec specs for every step, research notes with sources.

[0.1.0]: https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten/releases/tag/v0.1.0
