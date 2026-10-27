<p align="center"><img src="docs/assets/banner.png" alt="Links ein Haufen schief liegender Dateien wie angebot_final_v3(2).docx und kunden_ALT.xlsx, rechts dieselben Daten aufrecht in einer Box, jede mit Kategorie, verantwortlicher Person und Datum beschriftet." width="100%"></p>

# MarieKondo&shy;ForAgents

**Daten agententauglich machen, nach der KonMari-Methode.**

[English](README.md) · Deutsch

Ein KI-Agent auf SharePoint, CRM und Netzlaufwerk findet `angebot_final_v3(2).docx`, drei
Preislisten aus drei Jahren und eine Kundentabelle, die seit 2021 niemand gepflegt hat. Dann
antwortet er überzeugt und falsch.

Das KonMari-Kit repariert die Daten, nicht den Prompt. Es nimmt einen Anwendungsfall, sortiert aus,
was nicht dazugehört, ordnet den Rest nach Kategorie, gibt jedem Teil eine verantwortliche Person,
eine Definition und die richtigen Rechte und stellt das Ergebnis über einen MCP-Server nur zum Lesen
bereit. Jeder Schritt hat eine Freigabe-Schranke, jede Entscheidung trifft eine benannte Person, alles
wird protokolliert und lässt sich rückgängig machen.

## Schnellstart mit Claude Code

```
/plugin marketplace add KI-Klub-Werder-e-V/MarieKondoForAgents
/plugin install konmari@konmari-kit
```

Claude Code in einem leeren Ordner öffnen und sagen, was ihr braucht:

> Räum unsere Daten für einen FAQ-Agenten in der Geschäftsstelle auf.

Claude führt das Charta-Interview, trägt eure Quellen ein, startet Scans und Prüfungen und erklärt
jeden Bericht. Vor jeder Freigabe hält es an und fragt die Person, die die Charta dafür benennt.
Voraussetzungen: Python 3.12 und [uv](https://docs.astral.sh/uv/). Mehr in [`docs/agents.md`](docs/agents.md).

<details>
<summary><b>Nur Kommandozeile</b></summary>

```bash
git clone https://github.com/KI-Klub-Werder-e-V/MarieKondoForAgents
cd MarieKondoForAgents && uv sync
cd examples/verein-faq-assistant          # erfundener Sportverein mit FAQ-Assistent

uv run konmari next                       # Stand und nächster Schritt
uv run konmari charter validate
uv run konmari charter approve --as anna
uv run konmari metrics validate
uv run konmari metrics approve --as ben
uv run konmari charter gate --step inventory
```

Die Schranke bleibt zu, bis jede Pflicht-Kennzahl einen Ausgangswert hat:

```
unmet: metrics: must_have metric 'answer_accuracy' has no baseline or measurement plan
```

Weiter mit der [Anleitung](docs/guide.de.md). Alle Befehle: [`docs/cli.de.md`](docs/cli.de.md).

</details>

## So funktioniert es

Neun Schritte für einen Anwendungsfall. Jeder Schritt prüft, ob der vorige abgeschlossen ist.

| # | Schritt | Befehle | Was passiert |
|---|---|---|---|
| 1 | Festlegen | `charter` `metrics` | Anwendungsfall, Rollen, DSGVO/AI-Act-Prüfung, Erfolgskennzahlen mit Ausgangswerten |
| 2 | Nach Kategorie sammeln | `inventory` `categories` | Quellen über alle Systeme, nach Kategorie statt Ablageort, nach Risiko sortiert |
| 3 | Macht es Freude? | `score` `review` | Sechs Fragen je Asset, Vorschlag behalten / reparieren / aussortieren |
| 4 | Erst aussortieren | `discard` `retention` | Dubletten, überholte Fassungen, Irrelevantes in ein umkehrbares Archiv |
| 5 | Eine Wahrheit | `normalize` `resolve` `policy` `conflicts` | Datumsangaben, IDs, Namen vereinheitlicht, führende Quelle je Feld |
| 6 | Falten | `chunk` `enrich` `glossary` | Abschnitte nach Struktur, Metadaten an jedem Abschnitt, freigegebenes Glossar |
| 7 | Absichern | `govern` `audit` | Rechte der Quelle bis in den Index, Personendaten maskiert, verkettetes Prüfprotokoll |
| 8 | Anschließen | `product` `serve` | Versionierte Datenprodukte und ein MCP-Server nur zum Lesen |
| 9 | Prüfen | `eval` `monitor` | Testfragen mit Belegen und „weiß ich nicht“, Warnungen bei veralteten Daten |

Risikoarme Kategorien zuerst (öffentliche FAQ), besonders schützenswerte Daten zuletzt.

## Der Freude-Test

Jedes Asset bekommt 0 bis 3 Punkte je Kriterium, mit Belegen:

| Kriterium | Geprüft wird |
|---|---|
| Maßgeblich und aktuell | letzte Änderung gegen vereinbarte Frequenz, überholte Fassungen |
| Hat eine verantwortliche Person | benannt und pflegt es |
| Bedeutung klar | Spalten und Begriffe im Glossar definiert |
| Gut abrufbar | sinnvoll geschnittene Abschnitte, saubere Tabellenschemata |
| Widersprüche geklärt | Regel, welche Quelle gewinnt |
| Rechte klar | Berechtigungen der Quelle vorhanden und weitergegeben |

| | Vorher | Nachher |
|---|---|---|
| Format | ganze Dokumente, rohe Tabellen | Abschnitte, versionierte Datenprodukte |
| Metadaten | Dateiname | Quelle, Datum, verantwortliche Person, Aktualität, Definitionen |
| Zugriff | alles, was das Dienstkonto sieht | Leserechte je Agent, Quellrechte bleiben erhalten |
| Konsistenz | Dubletten, drei Wahrheiten | entdoppelt, eine führende Quelle je Feld |
| Aktualität | manuell, vergessen | geplante Aktualisierung mit Änderungsverfolgung |
| Auffindbarkeit | in Silos vergraben | im Katalog und per MCP abrufbar |

## Funktionen

- **Deterministisch.** Schranken, Bewertung, Dubletten und Regeln mit versionierten
  Schwellenwerten. Gleiche Eingaben, gleiches Ergebnis. Modelle sind optional und festgelegt.
- **Menschen geben frei.** Aussortieren, Maskieren, Löschen und Veröffentlichen braucht die
  Freigabe der Rolle aus der Charta, gespeichert mit Person und Zeitpunkt.
- **Umkehrbar und prüfbar.** Archiv statt Löschen, Wiederherstellen per Befehl, verkettetes
  Prüfprotokoll (`konmari audit verify`).
- **Dubletten auf drei Ebenen.** Identische Dateien, fast gleiche Texte (MinHash/LSH),
  inhaltlich gleiche Texte (Embeddings, optional).
- **DSGVO eingebaut.** Personendaten lokal erkennen (Presidio, spaCy), Fristen und
  Aufbewahrungspflichten je Kategorie, Pseudonymisierung mit Schlüssel, Export des
  Verzeichnisses der Verarbeitungstätigkeiten.
- **Offene Standards.** DCAT, OpenLineage, ODPS/ODCS, SKOS, MCP.
- **Selbst gehostet.** DuckDB lokal, Postgres 16 im Betrieb. Kein Cloud-Dienst nötig.

## Dokumentation

| | |
|---|---|
| Anleitung | [`docs/guide.de.md`](docs/guide.de.md) und je Schritt eine `*-guide.de.md` |
| Befehle | [`docs/cli.de.md`](docs/cli.de.md) |
| Claude-Code-Skill | [`docs/agents.md`](docs/agents.md), [`skills/konmari/`](skills/konmari/) |
| Specs und Roadmap | [`openspec/ROADMAP.md`](openspec/ROADMAP.md), [`openspec/changes/`](openspec/changes/) |
| Recherche mit Quellen | [`research/`](research/) |

## Mitmachen

Am meisten hilft ein **[Pilot-Bericht](https://github.com/KI-Klub-Werder-e-V/MarieKondoForAgents/issues/new?template=pilot.yml)**:
welcher Anwendungsfall, welche Systeme, wie weit ihr gekommen seid und wo es geklemmt hat.
Konnektoren (Nextcloud, Confluence, Google Drive, weitere CRMs), Beispiele und Übersetzungen
sind ebenfalls willkommen. Siehe [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Lizenz

[Apache-2.0](LICENSE). Ein Projekt des [KI Klub Werder e. V.](https://ki-klub-werder.de)
