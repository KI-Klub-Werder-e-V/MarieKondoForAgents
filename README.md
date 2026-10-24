# MarieKondoFürAgenten (KonMari-Kit)

Dieser Ordner enthält das KonMari-Kit (Planung und Code) des KI Klub Werder e.V. Das Kit ist ein Open-Source-Werkzeug mit einer festen Methode: Es bereitet die Daten **eines** Pilot-Anwendungsfalls so auf, dass KI-Agenten sie zuverlässig nutzen können. Die Grundidee steht in `research/00-quelle.md`: erst festlegen, worum es geht, dann aussortieren, nach Kategorien aufräumen, vereinheitlichen, anreichern, absichern, anbinden und am Ende mit echten Fragen prüfen.

## Inhalt

- `research/` – die Quelle (`00-quelle.md`) und je Change eine Recherche-Notiz mit den Quellen
- `openspec/changes/c01-…` bis `c09-…` – neun OpenSpec-Changes in der Reihenfolge der Umsetzung, jeweils mit `proposal.md`, `design.md`, `tasks.md` und `specs/`
- `src/konmari/` – das Kommandozeilen-Werkzeug `konmari`, `tests/` – Tests, `docs/` – Anleitungen, `examples/` – erfundene Beispiele
- `openspec/ROADMAP.md` – Übersicht aller Changes, Abdeckungsmatrix, gemeinsames Vokabular, offene Fragen

## Benutzung

Installation (Python 3.12, [uv](https://docs.astral.sh/uv/)):

```
uv sync
uv run konmari --help
```

Schritt 1, Pilot-Charta (Anleitung: [docs/guide.de.md](docs/guide.de.md), Beispiel: `examples/verein-faq-assistant/`):

```
konmari charter init --template minimal --lang de   # Vorlage charter.yaml anlegen
konmari charter validate                            # prüfen (Schema, RACI, Datenschutz, Datenumfang)
konmari charter approve --as <id>                   # freigeben (Accountable für charter_approval)
konmari charter amend --reason "..." --as <id>      # neue Fassung nach der Freigabe
konmari charter diff 1 2                            # Fassungen vergleichen (auch: show, log, status)
konmari metrics validate                            # Kennzahlen und Golden Set prüfen
konmari metrics approve --as <id>                   # Kennzahlen freigeben
konmari metrics baseline <metrik> --value 0.4 --method "..." --as <id>
konmari metrics measure <metrik> --kind gate --step discard --value 0.7 --method "..." --as <id>
konmari metrics report --format json              # Stand je Kennzahl und Pilot-Ergebnis (oder md)
konmari charter gate --step inventory               # Freigabe-Prüfung für spätere Schritte (Exit 0 = ok)
```

Schritt 2, Inventur und Kategorien (Anleitung: [docs/inventory-guide.de.md](docs/inventory-guide.de.md)):

```
konmari inventory source add --connector sql --locator <url> --secret-ref env:PW --as <id>  # nur Quellen aus der Charta
konmari inventory scan                              # nur lesend; Personendaten-Hinweise lokal (Presidio)
konmari inventory import <datei.csv> --as <id>      # Systeme ohne Konnektor
konmari inventory set <asset> --owner <id> --update-frequency P1M --as <id>
konmari inventory tier <asset> --confirm --as <id>  # Schutzstufe bestätigen (oder --set ... --reason ...)
konmari inventory report --gate                     # Vollständigkeit (auch: --csv, waive, export als DCAT)
konmari categories suggest --as <id>                # Vorschläge, dann accept/assign/reject/revert
konmari categories order --propose --as <id>        # Reihenfolge nach Risiko, dann categories approve
konmari categories handoff                          # Übergabe an Schritt 3 (Exit 0 = ok)
```

Schritt 3, Freude-Test (Anleitung: [docs/scorecard-guide.de.md](docs/scorecard-guide.de.md)):

```
konmari score evidence <asset> --kind acl_snapshot --data '{"principals": [...]}' --ref <ref> --as <id>  # Belege (auch: score ingest --soda/--odcs)
konmari score --as <id>                             # sechs Kriterien je Asset, Vorschlag keep/fix/discard
konmari review list --status proposed               # dann review accept|override|reverse <asset> --as <id>
konmari score fix-done <asset> <kriterium> --as <id>  # neu bewerten (auch: score --changed, score alert)
konmari scorecard report --format json              # Assets und Kategorie-Reife (md, csv, json)
```

Schritt 4, Aussortieren (Anleitung: [docs/discard-guide.de.md](docs/discard-guide.de.md)):

```
konmari discard detect --items items.jsonl --as <id>   # Dubletten, Fassungen, außerhalb des Piloten, sensible Inhalte
konmari discard calibrate --detector minhash --sample 60 --as <id>   # dann --labels pairs.csv --threshold 0.8
konmari discard propose --as <id>                   # Vorschläge; ändern nichts
konmari discard approve <vorschlag> --as <id>       # oder reject, bulk-approve, cancel
konmari discard execute --as <id>                   # archivieren, ausschließen, zur Maskierung, löschen
konmari discard restore <element> --as <id>         # auch: history, log verify, check-propagation, report
konmari retention rule add ... --as <id>            # Fristen je Kategorie (Compliance), dann rule approve
konmari retention check --as <id>                   # abgelaufene Fristen, auch im Archiv (auch: retention hold)
```

Schritt 5, Vereinheitlichen und führende Quelle (Anleitung: [docs/standardize-guide.de.md](docs/standardize-guide.de.md)):

```
konmari normalize --records records.jsonl --specs normalize/ --as <id>   # Rohwert bleibt, kanonischer Wert kommt dazu
konmari normalize quarantine list --as <id>         # dann quarantine resolve|revert <id> --value ... --as <id>
konmari resolve run --entity customer --config resolve.yaml --as <id>    # Dubletten über Systeme hinweg (Splink)
konmari resolve review export --entity customer -o pairs.csv           # dann review import pairs.csv --as <id>
konmari resolve publish --entity customer --known known.csv --as <id>  # nur bei Precision/Recall über der Untergrenze
konmari policy load --file policy/customer.yaml --as <id>              # neue Fassung "pending" mit Auswirkung
konmari policy approve customer --version 1 --as <id>                  # Feldverantwortliche/r gibt frei (auch: rollback)
konmari policy check --exposure exposure.yaml       # jedes für Agenten sichtbare Feld braucht eine Regel
konmari policy build customer                       # Golden Records mit Herkunft (auch: policy golden <entity> <cluster>)
konmari conflicts list --as <id>                    # dann conflicts resolve <id> --source erp --ticket IT-4711 --as <id>
konmari conflicts report --overdue                  # überfällige Konflikte (auch: accept, revert, export-tickets)
```

Schritt 6, Strukturieren und Anreichern (Anleitung: [docs/enrich-guide.de.md](docs/enrich-guide.de.md)):

```
konmari chunk run --dry-run --as <id>               # Chunks und Bericht, schreibt nichts (dann ohne --dry-run)
konmari chunk profile set <kategorie> --max-tokens 600 --reason "..." --as <id>   # auch: profile show, rollback <lauf>
konmari enrich schema load schema.yaml --as <id>    # für Agenten sichtbare Tabellen mit Spaltenbeschreibungen
konmari enrich run --as <id>                        # Metadaten-Umschlag, Vollständigkeit, ACL-Tags, Lineage
konmari enrich override <element> --field owner --value <id> --reason "..." --as <id>   # auch: lineage, rollback
konmari glossary stewards --steward <id> --as <id>  # dann glossary add|submit|approve|harvest|revert|deprecate
konmari glossary export --format skos -o glossar.ttl   # nur freigegebene Begriffe (auch: --format json, report)
```

Schritt 7, Absichern (Anleitung: [docs/govern-guide.de.md](docs/govern-guide.de.md)):

```
konmari govern policy seed --as <id>                # Quellen-Richtlinie je Kategorie, dann policy edit|approve|revert|show
konmari govern agent <agent> --scope read:<kategorie> --as <id>   # Agent mit kleinsten Rechten (auch: govern grant)
konmari govern acl-sync --as <id>                   # Berechtigungen aus den Quellen, Entzug wirkt überall
konmari govern scan --as <id>                       # Personendaten finden (dann review list|confirm|reject|revert)
konmari govern label export --category <kategorie> --as <id>   # Muster auszeichnen, dann label import <datei> --as <id>
konmari govern mask --as <id>                       # maskierter Agenten-Index (auch: govern reidentify, nur compliance)
konmari govern check --as <id>                      # Leck-Test und Freigabe je Quelle (auch: leak-test, status, ropa)
konmari audit verify                                # ein Prüfprotokoll für alle Schritte (auch: export, purge)
```

Schritt 8, Anbinden (Anleitung: [docs/connect-guide.de.md](docs/connect-guide.de.md)):

```
konmari product init <id> --category <kategorie> --owner <id> --use-case "<anwendungsfall>"   # ODPS + ODCS anlegen
konmari product lint products/<id> --as <id>        # Schemas, Pflichtangaben, Vorstufen; legt den Entwurf an
konmari product refresh <id> --as <id>              # geänderte Zeilen laden, testen, erst dann übernehmen (auch: all)
konmari product test <id>                           # Qualitätsregeln (auch: diff products/<id> für die Semver-Prüfung)
konmari product approve <id> --version 1.0.0 --as <owner>    # dann product publish <id> --version 1.0.0 --as <owner>
konmari product publish <id> --rollback --reason "..." --as <owner>   # auch: deprecate, list --json, schedule
konmari serve --check-pins                          # neue Werkzeuge prüfen, dann serve --approve-tool <name> --as <id>
konmari serve --stdio                               # MCP-Server für einen lokalen Agenten (oder --http mit OAuth)
```

Schritt 9, Prüfen und Beobachten (Anleitung: [docs/evaluate-guide.de.md](docs/evaluate-guide.de.md), CI-Beispiel: `examples/ci/konmari-eval.yml`):

```
konmari eval items import fragen.yaml --as <id>     # Golden-Set-Fragen (Protokoll-Fragen werden maskiert), dann items approve <id> --as <id>
konmari eval set activate --as <id>                 # >= 50 Fragen, >= 20 % ohne Antwort, <= 50 % synthetisch
konmari eval run --as <id>                          # über die MCP-Schnittstelle des Agenten (eval.yaml), auch: report
konmari eval gate                                   # Grenzwerte und Vergleichsbasis (Exit 1 = blockiert)
konmari eval baseline approve <lauf> --as <id>      # oder baseline revert; auch: calibrate, next-category, items retention
konmari monitor run --as <id>                       # Aktualität, Menge, Metadaten, Dubletten, Schema (auch --dry-run, schedule)
konmari monitor incident acknowledge <id> --reason "..." --as <id>   # oder resolve, accept-risk; auch: incidents, threshold, drift, import
```

Für die Personendaten-Hinweise einmal die Sprachmodelle laden: `uv run python -m spacy download de_core_news_lg` (Schritt 7 zusätzlich `en_core_web_lg`). SMB-Ablagen: `uv sync --extra smb`. Ragas als Bewertungsmodell: `uv sync --extra ragas`. Semantische Dubletten: `uv sync --extra semantic`, S3-Archiv: `uv sync --extra s3`, Docling-Parser: `uv sync --extra docling`.

Der Katalog liegt standardmäßig in `.konmari/catalog.duckdb`. Für Postgres 16: `uv sync --extra postgres` und `KONMARI_CATALOG=postgresql://...` oder `--catalog`. Schnittstelle für spätere Changes: [docs/gate-contract.md](docs/gate-contract.md), [docs/enrich-contract.md](docs/enrich-contract.md), [docs/governance-contract.md](docs/governance-contract.md), [docs/connect-contract.md](docs/connect-contract.md). Tests: `uv run pytest -q`.

Die Changes werden der Reihe nach umgesetzt, beginnend mit `c01-pilot-charter`:

```
/opsx:apply c01-pilot-charter
```

Danach folgen `c02-category-inventory`, `c03-joy-test-scorecard` und so weiter bis `c09-evaluate-and-monitor`. Ein späterer Change baut auf den früheren auf, deshalb bitte keinen überspringen.

Nützliche Befehle:

```
openspec list                      # alle Changes mit Fortschritt
openspec status --change <name>    # Stand eines Changes
openspec validate --all --strict   # alle Specs prüfen
/opsx:archive <name>               # fertigen Change archivieren
```

Vor dem Start bitte die offenen Fragen in [openspec/ROADMAP.md](openspec/ROADMAP.md) klären.

## Lizenz

Apache-2.0, siehe [LICENSE](LICENSE). Beiträge: [CONTRIBUTING.md](CONTRIBUTING.md).
