# Anleitung Absichern (Schritt 7 des KonMari-Kits: „govern“)

Nach dem Strukturieren (Schritt 6) liegen die Pilotdaten als Chunks im Katalog, aber noch ohne durchgesetzte Berechtigungen, ohne Schutz personenbezogener Daten und ohne Nachweis, wer was abgefragt hat. Dieser Schritt sorgt dafür, dass

- die **Berechtigungen der Quelle mitwandern** (wer ein Dokument in SharePoint nicht lesen darf, bekommt auch über den Agenten keinen Abschnitt davon),
- **personenbezogene Daten erkannt und maskiert** werden, bevor ein Agent sie sieht,
- jeder Zugriff eines Agenten im **Prüfprotokoll** steht (ohne Inhalte),
- und erst ein **berechnetes Freigabe-Ergebnis** eine Quelle für Schritt 8 (Anbinden) öffnet.

Das ist keine Rechtsberatung. Die Vorlagen (Verzeichnis nach Art. 30, DSFA-Vorprüfung) ersetzen nicht die Entscheidung Ihrer Datenschutzbeauftragten.

## 1. Wer entscheidet was

| Entscheidung | Wer |
|---|---|
| Quellen-Richtlinie bearbeiten (neue Fassung, wartet auf Freigabe) | jede Person aus der Charta, meist die Begleitung (`facilitator`) |
| Quellen-Richtlinie freigeben oder auf eine frühere Fassung zurücksetzen | Datenverantwortliche (`data_owner`) oder Datenschutz (`compliance`) |
| Rechtsgrundlage für besondere Kategorien (Art. 9, z. B. Gesundheitsdaten) | nur `compliance` |
| Schreibrecht für einen Agenten (läuft nach höchstens 90 Tagen ab) | die Datenverantwortliche der Kategorie |
| PII-Fund bestätigen, verwerfen oder zurücknehmen | `data_steward`, `data_owner` oder `compliance` |
| Pseudonym auflösen (z. B. für ein Auskunftsersuchen nach Art. 15) | nur `compliance`, mit Zweckangabe |
| Prüfprotokoll exportieren (Art. 15) | `compliance` |

Jede dieser Entscheidungen steht mit Person, Zeit, vorher/nachher und Begründung im Prüfprotokoll.

## 2. Reihenfolge der Befehle

```
konmari govern config                                    # governance.yaml prüfen (Aufbewahrung, Schwellen)
konmari govern policy seed --as <begleitung>             # je Kategorie ein Entwurf (Schutzstufe, Verantwortliche aus der Inventur)
konmari govern policy edit support-history --exposure allowed --mask PERSON=pseudonymize --as <begleitung>
konmari govern policy show support-history               # Fassungen ansehen
konmari govern policy approve support-history --version 2 --reason "Pilot" --as <data_owner>
konmari govern agent faq-bot --scope read:support-history --as <begleitung>
konmari govern acl-sync --as <id>                        # Berechtigungen aus den Quellen lesen
konmari govern scan --as <id>                            # personenbezogene Daten finden (lokal, Presidio)
konmari govern review list                               # unsichere Funde, dann review confirm|reject <fund> --as <id>
konmari govern label export --category support-history -o labels.csv --as <id>   # 200 Abschnitte von Hand auszeichnen
konmari govern label import labels.csv --as <id>         # Trefferquote messen (Exit 1, wenn unter 0,95)
konmari govern mask --as <id>                            # maskierten Agenten-Index bauen
konmari govern leak-test --suite leak-suite.yaml --as <id>
konmari govern check --as <id>                           # Freigabe berechnen und festhalten
konmari govern status                                    # was darf angebunden werden?
konmari govern ropa -o ropa-entry.yaml                   # Eintrag für das Verzeichnis nach Art. 30
konmari audit verify                                     # Prüfprotokoll aller Schritte prüfen
```

Für die Erkennung einmal die Sprachmodelle laden: `uv run python -m spacy download de_core_news_lg` und `uv run python -m spacy download en_core_web_lg`. Ohne sie bricht `scan` ab, statt etwas herunterzuladen.

## 3. Quellen-Richtlinie

Eine Richtlinie gilt für eine Kategorie und legt fest: Freigabe für Agenten (`allowed` oder `blocked`), Schutzstufe, Maskierung je Fundart (`redact`, `mask`, `replace-with-type`, `pseudonymize`), wie oft Berechtigungen neu gelesen werden (Standard `PT24H`), erlaubte Werkzeuge, Verantwortliche. Für Quellen, deren Berechtigungen das Kit nicht lesen kann (alte Dateiablage, CSV), tragen Sie die Berechtigten von Hand ein: `--allow group:Geschäftsstelle`.

Jede Änderung ist zuerst `pending`. Bis zur Freigabe gilt die alte Fassung weiter. Zurücksetzen: `konmari govern policy revert <kategorie> --to 1 --as <data_owner>`; das legt eine neue, sofort gültige Fassung mit dem alten Inhalt an.

## 4. Berechtigungen

`acl-sync` liest die Berechtigungen je Dokument (SharePoint über Microsoft Graph, Dateiablage über die Dateirechte, Postgres über Tabellenrechte, CSV/CRM-Export aus der Spalte `acl` der Elementliste). Gespeichert werden Gruppen, nicht einzelne Mitglieder. Jeder Abschnitt erbt die Berechtigungen seines Dokuments. Wo nichts lesbar ist, steht `acl_status = unknown`: niemand bekommt den Abschnitt.

Entzieht jemand in der Quelle einer Gruppe das Leserecht, wirkt das nach dem nächsten `acl-sync` in allen Abschnitten, und der Entzug steht im Prüfprotokoll. Ist der letzte erfolgreiche Abgleich älter als der eingestellte Takt, wird die Quelle `acl_stale` und liefert nichts mehr, bis ein Abgleich gelingt.

## 5. Personenbezogene Daten

`scan` speichert je Fund nur Art, Position und Wert der Sicherheit, nie den Text. Funde mit mittlerer Sicherheit (zwischen `pii.low` 0,4 und `pii.high` 0,85) kommen in die Prüfliste; bis zur Entscheidung werden sie maskiert. Eine verworfene Entscheidung wirkt beim nächsten `mask`, eine zurückgenommene ebenso.

Gesundheitsdaten und andere besondere Kategorien (Art. 9) bleiben aus dem Index, solange die Richtlinie keine von `compliance` freigegebene Rechtsgrundlage nennt.

Pseudonyme wie `<PERSON_7f3a9c01>` sind weiter personenbezogene Daten. Der Schlüssel liegt im eigenen Schema `konmari_keys`, auf das kein Agent zugreifen kann. Eine Rückübersetzungstabelle gibt es nicht: `konmari govern reidentify <token> --purpose "Auskunftsersuchen Art. 15" --as <compliance>` sucht den Wert in den Originalabschnitten und protokolliert die Abfrage.

Trefferquote: Zeichnen Sie im exportierten Muster (Standard 200 Abschnitte) in der Spalte `labels` jede Person, E-Mail, Telefonnummer und IBAN ein, z. B. `PERSON=Petra Schulz; IBAN_CODE=DE89 3704 0044 0532 0130 00`. Die Datei enthält Pilottext: im Haus behalten und nach dem Import löschen. Liegt die Quote einer dieser Arten unter 0,95, bleibt die Quelle gesperrt (`pii_recall`). Ändert sich das Erkennungsmodell, gilt das Ergebnis als veraltet und wird neu gemessen.

## 6. Leck-Test und Freigabe

`leak-suite.yaml` beschreibt Test-Personen:

```yaml
principals:
  - {id: vorstand, user: test-vorstand, groups: [Vorstand]}
  - {id: gast, user: test-gast, groups: []}
queries: ["Mitgliedsbeitrag Erhöhung"]   # optional, zusätzlich zu den automatischen
```

Für jede Test-Person fragt das Kit gezielt nach Abschnitten, die sie **nicht** sehen darf. Kommt einer zurück, schlägt der Test fehl und nennt Abschnitt, Quelle und Person.

`check` setzt den Status je Quelle: `approved` nur bei 100 % gelesenen Berechtigungen, freigegebener Richtlinie mit `allowed`, bestandener Trefferquote (wo personenbezogene Daten vorkommen), bestandenem Leck-Test für die aktuellen Richtlinien-Fassungen und laufendem Prüfprotokoll. Den Status kann niemand von Hand setzen. Schritt 8 bindet nur `approved`-Quellen an.

Zurücknehmen: Richtlinie auf `--exposure blocked` setzen und freigeben; die Quelle ist sofort gesperrt. Zugriffsschutz und Prüfprotokoll bleiben immer an.

## 7. Prüfprotokoll

Alle Schritte schreiben in eine einzige, verkettete Tabelle. `konmari audit verify` prüft die Kette und nennt das erste veränderte Ereignis. Aufbewahrung: 6 Monate (`audit.retention_months`), höchstens so lange wie im Verzeichnis nach Art. 30 angegeben (`audit.max_retention_months`). Bei Hochrisiko-Systemen nach KI-Verordnung lehnt das Kit weniger als 6 Monate ab (Art. 26 Abs. 6). `konmari audit purge --as <id>` löscht Ältere und schreibt einen Prüfpunkt, ab dem die Kette weiter prüfbar ist. `konmari audit export --subject <person> -o datei.jsonl --as <compliance>` liefert alle Ereignisse einer Person.

Unter Postgres sind die Rollen getrennt: `konmari_app` darf nur einfügen und lesen, `konmari_retention` nur löschen, `konmari_agent` nur den maskierten Index lesen, `konmari_keyholder` den Schlüssel. Fehlt dem einrichtenden Benutzer das Recht, Rollen anzulegen, legt sie eine Administratorin an. Die Hash-Kette zeigt Manipulation an, verhindert sie aber nicht gegenüber einem Datenbank-Superuser.

Checklisten zu DSFA-Vorprüfung und KI-Verordnung: [govern-checklists.de.md](govern-checklists.de.md). Schnittstelle für Schritt 8: [governance-contract.md](governance-contract.md).
