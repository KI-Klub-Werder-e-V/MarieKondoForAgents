# Mitmachen

Danke, dass du beim KonMari-Kit des KI Klub Werder e. V. mitmachen willst. (English below.)

## Was am meisten hilft

1. **Pilot-Erfahrungen.** Ihr habt das Kit an einem echten Anwendungsfall ausprobiert? Erzählt
   es uns im Issue-Formular „Pilot-Bericht“, auch wenn es gescheitert ist. Bitte ohne echte
   Personendaten.
2. **Konnektoren** für weitere Systeme (Nextcloud, Confluence, Google Drive, CRMs).
3. **Beispiele und Übersetzungen**, besonders deutschsprachige Testdaten ohne Personenbezug.

## So läuft ein Beitrag

- **Fehler und Ideen** zuerst als Issue, damit wir uns abstimmen können, bevor du Zeit investierst.
- **Verhalten ändert sich nur über einen OpenSpec-Change.** Lege ihn mit `/opsx:propose` an
  (oder von Hand unter `openspec/changes/<name>/`) und setze ihn mit `/opsx:apply <name>` um.
  Kleine Fehlerbehebungen ohne Änderung der Anforderungen brauchen keinen Change.
- **Jedes Szenario braucht einen Test**, der das THEN prüft, nicht nur, dass etwas läuft.
- **Vor dem Pull Request** muss grün sein:

  ```bash
  uv run pytest -q
  openspec validate --all --strict
  ```

- **Zahlen brauchen Quellen.** Schwellenwerte und Statistiken stehen mit Quelle in `research/`
  oder sind dort als Annahme markiert.
- **Keine echten Daten.** Keine personenbezogenen oder vertraulichen Daten in Issues, Tests,
  Fixtures oder Beispielen. Nur erfundene Beispiele.

Die Regeln, die beim Programmieren leicht kaputtgehen (Prüfprotokoll nur anhängen, Freigaben
durch Menschen, umkehrbare Entscheidungen), stehen in [`AGENTS.md`](AGENTS.md). Sie gelten für
Menschen und Coding-Agenten gleichermaßen.

Mit einem Beitrag stimmst du zu, dass er unter Apache-2.0 veröffentlicht wird (siehe
[`LICENSE`](LICENSE)). Für den Umgang miteinander gilt der [Verhaltenskodex](CODE_OF_CONDUCT.md).

---

## Contributing (English)

Pilot stories help most: open a "Pilot report" issue, even if it failed, without real personal
data. Behaviour changes go through an OpenSpec change (`/opsx:propose`, then `/opsx:apply`).
Every spec scenario needs a test that asserts its THEN clause. Before a pull request,
`uv run pytest -q` and `openspec validate --all --strict` must pass. Numbers need a source in
`research/` or are marked as assumptions. No real personal or confidential data anywhere in the
repo. Rules for humans and coding agents: [`AGENTS.md`](AGENTS.md). Contributions are licensed
under Apache-2.0.
