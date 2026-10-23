# MarieKondoFürAgenten (KonMari-Kit)

Dieser Ordner enthält die Planung für das KonMari-Kit des KI Klub Werder e.V. Das Kit ist ein Open-Source-Werkzeug mit einer festen Methode: Es bereitet die Daten **eines** Pilot-Anwendungsfalls so auf, dass KI-Agenten sie zuverlässig nutzen können. Die Grundidee steht in `research/00-quelle.md`: erst festlegen, worum es geht, dann aussortieren, nach Kategorien aufräumen, vereinheitlichen, anreichern, absichern, anbinden und am Ende mit echten Fragen prüfen.

## Inhalt

- `research/` – die Quelle (`00-quelle.md`) und je Change eine Recherche-Notiz mit den Quellen
- `openspec/changes/c01-…` bis `c09-…` – neun OpenSpec-Changes in der Reihenfolge der Umsetzung, jeweils mit `proposal.md`, `design.md`, `tasks.md` und `specs/`
- `openspec/ROADMAP.md` – Übersicht aller Changes, Abdeckungsmatrix, gemeinsames Vokabular, offene Fragen

## Benutzung

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
