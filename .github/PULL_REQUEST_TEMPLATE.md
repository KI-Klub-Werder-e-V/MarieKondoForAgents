## Was ändert sich? / What changes?

<!-- Kurz. Bei Verhaltensänderungen: welcher OpenSpec-Change? -->

OpenSpec-Change: `openspec/changes/<name>` (oder: reine Fehlerbehebung, keine Anforderung geändert)

## Prüfung / Checks

- [ ] `uv run pytest -q` ist grün
- [ ] `openspec validate --all --strict` ist grün
- [ ] Jedes neue oder geänderte Szenario hat einen Test, der das THEN prüft
- [ ] Keine echten personenbezogenen oder vertraulichen Daten
- [ ] Neue Schwellenwerte haben eine Quelle oder sind in `research/` als Annahme markiert
- [ ] Anleitung (`docs/*-guide.*.md`) und `docs/cli.de.md` angepasst, falls Befehle sich ändern
