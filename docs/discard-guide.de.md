# Anleitung Aussortieren (Schritt 4 des KonMari-Kits)

KonMari-Regel 3: erst aussortieren. Dubletten, veraltete Fassungen und Material, das der Pilot nicht braucht, verwirren einen Agenten, weil sie gleichrangig neben der richtigen Quelle stehen. Dieser Schritt findet solchen Ballast im Pilot-Bestand, schlägt für jedes Element eine Maßnahme vor und lässt eine benannte Person entscheiden. Ohne Freigabe ändert sich nichts. Archivieren und Ausschließen lassen sich rückgängig machen, und jeder Schritt landet in einem manipulationssicheren Entscheidungsprotokoll.

Das Kit löscht nie etwas in den Quellsystemen (SharePoint, CRM, Dateiablage). Es nimmt Elemente aus dem **Pilot-Bestand** (dem, was spätere Schritte und Agenten sehen) und listet offene Aufräumarbeiten für die Verantwortlichen der Quelle auf.

Voraussetzung sind die Schritte 1 bis 3: eine freigegebene Charta mit gemessenen Ausgangswerten (`konmari charter gate --step discard`) und je Kategorie ein abgeschlossener Freude-Test (Schritt `score` abgeschlossen) in der freigegebenen Reihenfolge.

## 1. Wer entscheidet was

| Entscheidung | Wer | Regel |
|---|---|---|
| Archivieren, ausschließen, zur Maskierung, behalten | die freigebende Person (`approver`) der Kategorie in der Charta (`data_scope.categories`) | nicht die Person, die den Vorschlag erstellt hat |
| Löschen | die freigebende Person der Kategorie **und** eine Person mit der Rolle `compliance` | zwei verschiedene Personen, danach eine Wartefrist (Standard 14 Tage) |
| Aufbewahrungsregeln | eine Person mit der Rolle `compliance` (Datenschutzbeauftragte) | eine Regel je Kategorie, keine Einheitsfrist für alles |
| Detektor ohne Kalibrierung freischalten | eine freigebende Person | mit schriftlicher Begründung |

## 2. Erkennen

```
konmari discard detect --items items.jsonl --as dirk
```

Gelesen werden nur Elemente von Assets aus der Inventur. Elemente lokaler Dateiablagen findet das Kit selbst. Für andere Systeme die Elemente als Dateien exportieren und in einer Liste (ein JSON-Objekt je Zeile) angeben:

```
{"asset_uri": "manual://kb/sops", "path": "export/Satzung_v4.txt", "version": "4.1", "effective_date": "2025-03-01"}
{"asset_uri": "manual://kb/sops", "path": "export/Preisliste_2024-03.txt", "logical_id": "preisliste", "version": "2024-03"}
```

Hilfreiche Angaben: `version`, `logical_id` (dasselbe Dokument über Fassungen hinweg), `effective_date`, `valid_until`, `source_of_truth: true`, `last_entry` (für Aufbewahrungsfristen), `category` (wenn ein Element zu einer anderen Kategorie gehört als sein Asset), `text_path` (bereits extrahierter Text, etwa bei PDFs).

| Hinweis | Wie | Vorschlag |
|---|---|---|
| exakte Dublette | gleicher Text nach Unicode-NFC und Bereinigung von Leerraum (SHA-256) | alle Kopien außer der maßgeblichen archivieren |
| Beinahe-Dublette (`minhash`) | MinHash LSH über 5-Wort-Folgen, danach die exakte Jaccard-Ähnlichkeit jedes Kandidaten | archivieren |
| enthaltene Kopie (`containment`) | Anteil der Wortfolgen eines Dokuments, der in einem anderen steckt (z. B. FAQ im Handbuch) | archivieren |
| semantisch (`semantic`, optional) | Kosinus-Ähnlichkeit aus einem selbst betriebenen mehrsprachigen Modell (`pip install konmari[semantic]`, `semantic: true` in `discard.yaml`) | höchstens archivieren, nie ausschließen oder löschen |
| überholt | neuere Fassung in der Versionsgeschichte, abgelaufenes `valid_until` mit neuerer Kopie oder „ersetzt durch <Dokument>“ im Text | archivieren |
| außerhalb des Piloten | die Kategorie des Elements steht nicht in der Charta | ausschließen |
| sensible Prüfung | Schutzstufe personal, special-category oder confidential, oder gefundene E-Mail-Adressen, Telefonnummern oder IBANs | zur Maskierung (Schritt 7) |
| Prüfung (nur Dateiname) | Namen wie `Angebot_final.docx` und `Angebot_final_v2.docx` | keiner: ein Mensch schaut es an |

Die maßgebliche Kopie eines Clusters wählt das Kit nach Quell-Metadaten: Kennzeichen „maßgebliche Quelle“, dann Gültigkeitsdatum, dann Versionsnummer, dann Vollständigkeit. Änderungszeiten von Dateien zählen nicht, weil Kopien und Migrationen sie zurücksetzen.

Der Bericht (`konmari discard report`, auch unter `.konmari/reports/`) zeigt die Dublettenquote (Elemente auf nicht-maßgeblichen Plätzen geteilt durch geprüfte Elemente), die Hinweise je Art, Clustergrößen und den Stand der Kalibrierung. Belege enthalten IDs, Werte und Parameter, nie Textauszüge.

## 3. Schwellen kalibrieren

Die Startwerte (Jaccard 0,8, Enthaltensein 0,9, Kosinus 0,93) sind unsere Annahmen, keine belegten Werte. Entscheidend sind Ihre Dokumente. Bis ein Detektor kalibriert ist, erzeugt er Hinweise, aber **keine Vorschläge**:

```
konmari discard calibrate --detector minhash --sample 60 -o pairs.csv --as clara   # pairs.csv + pairs.html (nebeneinander)
# Spalte label ausfüllen: duplicate, related-but-distinct oder unrelated
konmari discard calibrate --detector minhash --labels pairs.csv --threshold 0.8 --as clara
```

Die Ausgabe zeigt Genauigkeit (precision) und Trefferquote (recall) bei 0,7, 0,8 und 0,9. Nötig sind mindestens 50 bewertete Paare (unsere Annahme). Ohne sie kann eine freigebende Person eine Ausnahme mit Begründung erfassen: `--override-reason "..." --as ben`.

## 4. Vorschlagen, prüfen, ausführen

```
konmari discard propose --as dirk
konmari discard list --status pending
konmari discard approve <vorschlag> --as ben                    # oder --action keep --comment "..."
konmari discard reject <vorschlag> --comment "..." --as ben
konmari discard bulk-approve --action archive --category kb-and-sops --method exact --as ben   # zeigt die IDs
konmari discard bulk-approve ... --yes                          # bestätigt
konmari discard execute --as dirk
```

- **archive**: Das Element wird in den Kaltspeicher kopiert (`--archive PFAD` oder `s3://bucket/prefix`, Standard `.konmari/archive`), die Kopie per SHA-256 geprüft, erst dann verlässt es den Bestand. Stimmt die Prüfsumme nicht, ändert sich nichts.
- **exclude**: Das Element verlässt den Bestand; die unveränderte Quelle ist die Kopie.
- **route-to-masking**: Das Element wartet als `awaiting-masking`, bis Schritt 7 eine maskierte Fassung meldet.
- **Wiederherstellen**: `konmari discard restore <element> --as ben` holt archivierte oder ausgeschlossene Elemente zurück (mit Prüfsumme).
- Jedes Element, das den Bestand verlässt, erzeugt ein Lineage-Ereignis (`.konmari/lineage.jsonl`); seine Chunks und Embeddings werden zum Entfernen markiert. `konmari discard check-propagation --index ids.json` schlägt fehl, solange davon noch etwas vorhanden ist.

Das Archiv darf nur die Archiv-Rolle lesen. Ein Archiv im Dateisystem legt das Kit mit den Rechten 0700 (Dateien 0600) für den Benutzer an, der konmari ausführt. Bei S3 oder MinIO bekommt nur diese Rolle eine Bucket-Richtlinie mit Leserecht. Unter Postgres legt die Migration die Rolle `konmari_app` mit `SELECT, INSERT` auf `audit_event` an, ohne `UPDATE` und `DELETE`. Darf der migrierende Benutzer keine Rollen anlegen, bitte von Hand vergeben.

## 5. Aufbewahrungsregeln, Sperren, Löschen

Aufbewahrungsfristen sind Daten, die Compliance einträgt, nie fest eingebaute Werte:

```
konmari retention rule add --category financial-ops-metrics --purpose "Buchungsbelege" \
  --legal-basis "Art. 6 Abs. 1 lit. c DSGVO" --source "§147 Abs. 3 AO / §257 Abs. 4 HGB" \
  --start-event end-of-calendar-year-of-last-entry --period P8Y --expiry-action erase --as eva
konmari retention rule approve <regel> --as eva
konmari retention hold add --item <element> --reason "laufender Rechtsstreit" --owner eva --review-date 2027-01-31 --as eva
konmari retention check --as dirk          # regelmäßig einplanen, z. B. wöchentlich (--schedule P7D)
```

Beispiele für deutsche Fristen (**nur Beispiele, Compliance muss sie für die eigene Organisation bestätigen**): Buchungsbelege 8 Jahre seit dem Bürokratieentlastungsgesetz IV (BEG IV, ab 2025; §147 Abs. 3 AO, §257 Abs. 4 HGB), für Banken, Versicherungen und Wertpapierinstitute nach einer späteren Gesetzesänderung wieder 10 Jahre; Jahresabschlüsse 10 Jahre; Geschäftsbriefe 6 Jahre. Diese Fristen haben sich seit 2024 zweimal geändert. Deshalb führt das Kit sie als Daten mit Quelle.

- `retention check` prüft aktive, archivierte und gesperrte Elemente. Für jedes Element, dessen Frist abgelaufen ist oder in 30 Tagen abläuft, entsteht ein Vorschlag (löschen, oder behalten bei „review“). `discard report` warnt, wenn die letzte Prüfung älter als ihr Turnus ist.
- **Löschen** braucht eine freigegebene Regel für die Kategorie, die freigebende Person der Kategorie und Compliance sowie 14 Tage Wartefrist (`konmari discard cancel <vorschlag>` stoppt es). Eine Sperre (legal hold) verhindert es. Gilt noch eine Aufbewahrungspflicht, wird das Element stattdessen **eingeschränkt** (`restricted`, Art. 17 Abs. 3 lit. b DSGVO, §35 Abs. 3 BDSG): raus aus Bestand und allen Exporten, aufbewahrt bis zum Fristende.
- Beim Löschen verschwinden Bestandseintrag, Archivkopie und gemeldete abgeleitete Artefakte. Es bleibt ein Löschnachweis ohne Inhalt. Der Speicherort in der Quelle steht dort als offene Aufgabe für die Verantwortlichen der Quelle.
- Löschanträge betroffener Personen: `konmari discard request <element> --action erase --reason "..." --as dirk`.

## 6. Entscheidungsprotokoll

Jeder Vorschlag, jede Freigabe, Ablehnung, Ausführung, Wiederherstellung und Löschung ist ein Ereignis im gemeinsamen, nur anfügbaren Audit-Protokoll, verkettet mit SHA-256.

```
konmari discard history <element>
konmari discard log verify         # Exit 1 und das erste beschädigte Ereignis, wenn eine Zeile geändert wurde
konmari discard log export -o entscheidungen.jsonl
```
