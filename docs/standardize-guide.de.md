# Anleitung: Vereinheitlichen und führende Quelle (Schritt 5 des KonMari-Kits)

Nach dem Aussortieren steht dieselbe Angabe oft noch unterschiedlich da (`03.04.2025` und `2025-04-03T00:00+02:00`, `DE 123 456 788` und `DE123456788`, „Müller GmbH" und „MUELLER GMBH", kg und t). Ein Kunde kann im CRM, im ERP und in einer SharePoint-Liste unter drei Nummern stehen. Dieser Schritt macht Werte vergleichbar, verknüpft die Datensätze, die dasselbe beschreiben, und lässt eine benannte verantwortliche Person je Feld festlegen, **welche Quelle gilt**. Jede Entscheidung gibt ein Mensch frei, sie steht im Prüfprotokoll und lässt sich zurücknehmen.

Das Kit ändert nichts in Ihren Quellsystemen. Rohwerte werden in den Katalog kopiert, der kanonische Wert kommt daneben. Korrekturen in der Quelle laufen über ein Ticket, das Sie angeben.

Voraussetzung sind die Schritte 1 bis 4: eine freigegebene Charta (Personen mit der Rolle `data_owner` entscheiden), die Inventur (Schutzstufen) und das Aussortieren (dort entfernte Datensätze werden übersprungen).

## 1. Wer entscheidet was

| Entscheidung | Wer |
|---|---|
| Normalisierungsregel eines Feldes | darf jede/r schreiben, wird versioniert; die Regel nennt die Feldverantwortliche (`owner`) |
| Wert in Quarantäne (setzen, „nicht behebbar", Regeländerung anfordern, zurücknehmen) | die Feldverantwortliche aus der Regel |
| Paar in der Prüfliste, Cluster trennen oder wiederherstellen | jede in der Charta genannte Person |
| Cluster ohne Liste bekannter Dubletten freigeben | eine Person mit Rolle `data_owner`, mit Begründung |
| Fassung der Quellen-Regeln (freigeben, zurückrollen) | die Verantwortliche jeder geänderten Regel, als `data_owner` in der Charta |
| Konflikt (entscheiden, Abweichung akzeptieren, zurücknehmen) | die Verantwortliche der Regel |

## 2. Normalisierungsregeln schreiben

Je Quelle eine YAML-Datei in `normalize/`. Jeder Eintrag beschreibt ein Quellfeld (`objekt.spalte`) und das Merkmal, das es füllt:

```
fields:
  - {source: crm, field: kontakt.geaendert, entity: customer, attribute: updated_at, type: datetime, owner: ben,
     parser: {pattern: "DD.MM.YYYY HH:mm", zone: Europe/Berlin}}
  - {source: erp, field: debitor.ustid, entity: customer, attribute: vat_id, type: identifier, owner: ben,
     parser: {kind: de.vat}}
  - {source: crm, field: kontakt.telefon, entity: customer, attribute: phone, type: phone, owner: ben,
     parser: {region: DE}}
  - {source: crm, field: kontakt.firma, entity: customer, attribute: legal_name, type: name, owner: ben,
     asset: "manual://crm/kontakte", parser: {transliterate_de: true, strip_legal_forms: true}}
  - {source: erp, field: debitor.liefermenge, entity: customer, attribute: annual_volume, type: quantity, owner: ben,
     parser: {target_unit: kg, decimal_comma: true, allowed_units: [kg, t, g]}}
```

| Typ | Kanonischer Wert | Einstellungen |
|---|---|---|
| `date` | `JJJJ-MM-TT`, ohne Zeitzonen-Umrechnung | `pattern` (Platzhalter `YYYY MM DD`), Pflicht |
| `datetime` | UTC-Zeitpunkt `2025-03-30T12:00:00Z`, ursprüngliche Zone oder Versatz getrennt gespeichert | `pattern` (`ISO` oder Platzhalter mit `HH mm ss`) und `zone`, oder `epoch_unit` (`s`, `ms`, `us`) |
| `identifier` | Kurzform der python-stdnum-Art, Kennzeichen `syntax_valid` | `kind`, z. B. `iban`, `de.vat`, `eu.vat` |
| `phone` | E.164 (`+49301234567`) | `region`, z. B. `DE` |
| `name`, `text` | Unicode NFC, Leerzeichen bereinigt; ein getrennter Vergleichsschlüssel nur zum Abgleichen | `transliterate_de` (ü→ue), `strip_legal_forms` (beide standardmäßig aus) |
| `quantity` | Zahl und Einheit getrennt | `target_unit`, `decimal_comma`, `allowed_units`, `vocabulary: ucum` |

`asset` verbindet das Feld mit seinem Inventur-Eintrag. Schutzstufe `personal` oder `special-category` kennzeichnet Konflikte und Quarantäne-Einträge als personenbezogen.

Das Kit rät nie. Eine Ortszeit ohne angegebene Zone, eine Uhrzeit, die es bei der Zeitumstellung doppelt oder gar nicht gibt, eine falsche Prüfziffer, eine unbekannte oder unpassende Einheit: Solche Werte kommen mit Grund in die **Quarantäne**.

## 3. Normalisieren

Datensätze je Quelle als JSON-Zeilen exportieren und starten:

```
{"source": "crm", "object": "kontakt", "record_id": "K-0001", "values": {"firma": "Müller GmbH", "telefon": "030 1234567"}}
konmari normalize --records records.jsonl --specs normalize/ --as ben
```

Fehlt für ein Feld die Regel, bricht der Lauf ab, bevor etwas geschrieben wird. Derselbe Lauf mit derselben Regelfassung ändert beim zweiten Mal nichts. Die Ausgabe zeigt je Feld den Anteil kanonischer und quarantänierter Werte. Ein Feld gilt nur als konform, wenn jeder gelesene Wert kanonisch ist oder mit Grund in der Quarantäne liegt (`konmari normalize conformance <lauf>`).

```
konmari normalize quarantine list --as ben
konmari normalize quarantine resolve <id> --value 2025-04-03 --as ben     # oder --unfixable / --spec-change
konmari normalize quarantine revert <id> --reason "..." --as ben
konmari normalize contract export -o contract.yaml                         # ODCS-v3-Datenvertrag für die CI
konmari normalize contract check contract.yaml
konmari normalize rollback <lauf> --reason "..." --as ben                  # kanonische Werte eines Laufs entfernen
```

## 4. Datensätze über Systeme verknüpfen und Paare prüfen

`resolve.yaml` legt je Entität die verglichenen Merkmale, die Vorauswahl (Blocking) und die Fellegi-Sunter-Wahrscheinlichkeiten m/u fest (Beispiel: `tests/fixtures/sot/resolve.yaml`). Splink bewertet jedes Kandidatenpaar:

- ab 0,99: automatisch verknüpft,
- zwischen 0,50 und 0,99: Prüfliste,
- unter 0,50: nicht verknüpft.

Schwellen und Untergrenzen kommen aus den Erfolgskennzahlen, wenn dort `er_upper_threshold`, `er_lower_threshold`, `er_precision` und `er_recall` stehen. Dubletten innerhalb einer Quelle kommen aus dem Aussortieren und werden hier nicht neu entschieden.

```
konmari resolve run --entity customer --config resolve.yaml --as ben
konmari resolve review export --entity customer -o pairs.csv     # Prüfliste plus Stichprobe verknüpfter Paare
konmari resolve review import pairs.csv --as clara               # Spalte label: match oder non_match
konmari resolve publish --entity customer --known known.csv --as ben
konmari resolve split <cluster> --entity customer --records erp:D-S2 --reason "Schwesterfirmen" --as clara
konmari resolve restore <split> --as clara
```

Ein `non_match` bleibt gespeichert: Das Paar wird nie wieder automatisch verknüpft. `publish` verweigert, wenn weniger als 100 verknüpfte Paare (oder alle, wenn es weniger sind) bewertet sind, wenn die Precision unter 0,98 liegt oder der Recall gegen die Liste bekannter Dubletten unter 0,95. Ohne diese Liste braucht es eine ausdrückliche, protokollierte Ausnahme einer Datenverantwortlichen (`--override-recall-reason`).

## 5. Quellen-Regeln freigeben (welche Quelle gilt)

Je Entität eine Datei, je Feld eine Regel mit genau einer Strategie:

```
entity: customer
rules:
  payment_terms: {strategy: source_priority, sources: [erp, crm], tie_breaker: most_recent,
                  timestamp_field: updated_at, owner: ben, rationale: Die Buchhaltung pflegt Zahlungsbedingungen im ERP.}
  legal_name: {strategy: owner_decision, tie_breaker: owner_decision, owner: ben, rationale: Abgleich mit dem Handelsregister.}
```

Strategien: `source_priority` (Reihenfolge `sources`), `most_recent` (braucht `timestamp_field`), `most_complete`, `owner_decision` (geht immer ins Konfliktregister). Ein stilles „der letzte Stand gewinnt" gibt es nicht.

```
konmari policy load --file policy/customer.yaml --as dirk    # neue Fassung "pending"; Probelauf zeigt geänderte Werte
konmari policy approve customer --version 2 --as ben         # Regelverantwortliche/r gibt frei; Golden Records neu
konmari policy rollback customer --to 1 --reason "..." --as ben
konmari policy check --exposure exposure.yaml                # jedes für Agenten lesbare Feld braucht eine Regel
konmari policy build customer                                # Golden Records + OpenLineage-columnLineage-Ereignis
konmari policy golden customer <cluster>                     # Wert, Quelle, Regel, Status je Merkmal
```

Jedes Merkmal im Golden Record nennt Quelle, Datensatz-ID, Regel, Regelfassung und Zeitpunkte. Ein Merkmal ohne Entscheidung ist `unresolved` und hat keinen Wert, damit ein Agent „weiß ich nicht" sagt statt zu raten.

## 6. Konflikte entscheiden

Ein Konflikt entsteht, wenn sich die Datensätze eines Clusters widersprechen und die Regel nicht entscheiden kann. Widersprüche, die eine Regel entschieden hat, bleiben als `overridden` sichtbar. Werte, die nach dem Normalisieren gleich sind (`+49 30 1234567` und `030/1234567`), sind kein Konflikt.

```
konmari conflicts list --status open --as ben
konmari conflicts resolve <id> --source erp --ticket IT-4711 --as ben    # das CRM wird über Ticket IT-4711 korrigiert
konmari conflicts accept <id> --source crm --reason "..." --as ben       # die Abweichung in der Quelle bleibt
konmari conflicts revert <id> --reason "..." --as ben
konmari conflicts report --overdue
konmari conflicts export-tickets -o write-back-tickets.csv
```

Zeigt ein späterer Lauf denselben Widerspruch bei einem entschiedenen Eintrag, wird er `reopened` und die Verantwortliche benachrichtigt (`.konmari/notifications.jsonl` oder `$KONMARI_NOTIFY_FILE`). Eine akzeptierte Abweichung (`accepted_deviation`) öffnet sich nicht wieder.

## 7. Datenschutz (DSGVO)

- Konflikte bei personenbezogenen Daten (Schutzstufe `personal` oder `special-category` in der Inventur) werden gekennzeichnet und bekommen eine Frist von 30 Tagen ab Erkennung (`conflict_due_days` in der Regeldatei). Die 30 Tage sind unsere Annahme, keine gesetzliche Frist. `konmari conflicts report --overdue` ist die Liste für die Pflichten zur Richtigkeit (Art. 5 Abs. 1 lit. d) und Berichtigung (Art. 16) der verantwortlichen Stelle.
- Das Register speichert nur die Kandidatenwerte, die für die Entscheidung nötig sind, nie ganze Quelldatensätze. Die Ticket-CSV enthält keine Werte.
- Kandidatenwerte und Rohwerte in der Quarantäne sind bei personenbezogenen Feldern für alle außer der Feldverantwortlichen und der Rolle `compliance` geschwärzt. Das ist eine Übergangslösung, bis Zugriffssteuerung und Maskierung (Schritt 7) da sind.
- Prüfziffern belegen nur die Schreibweise, nicht dass eine IBAN oder USt-IdNr. existiert. Das Kit fragt keine externen Register (VIES) ab.
