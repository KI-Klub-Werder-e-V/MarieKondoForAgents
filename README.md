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

Für die Personendaten-Hinweise einmal das Sprachmodell laden: `uv run python -m spacy download de_core_news_lg`. SMB-Ablagen: `uv sync --extra smb`.

Der Katalog liegt standardmäßig in `.konmari/catalog.duckdb`. Für Postgres 16: `uv sync --extra postgres` und `KONMARI_CATALOG=postgresql://...` oder `--catalog`. Schnittstelle für spätere Changes: [docs/gate-contract.md](docs/gate-contract.md). Tests: `uv run pytest -q`.

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
