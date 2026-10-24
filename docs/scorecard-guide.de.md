# Anleitung Freude-Test (Schritt 3 des KonMari-Kits)

KonMari-Regel 6: Macht es Agenten Freude? Dieser Schritt prüft jedes Asset im Pilot-Umfang an sechs Kriterien, schlägt **keep** (behalten), **fix** (nachbessern) oder **discard** (aussortieren) vor und lässt eine benannte Prüfperson entscheiden. In Ihren Quellen wird nichts geändert, verschoben oder gelöscht. Ein `discard` ist nur eine Empfehlung, die Schritt 4 (Aussortieren) später mit seinen Aufbewahrungsregeln umsetzt.

Voraussetzung ist Schritt 2: `konmari categories handoff` muss bestehen, und jede `must_have`-Kennzahl braucht einen gemessenen Ausgangswert (`konmari charter gate --step score`).

## 1. Die sechs Kriterien

Jedes Kriterium bekommt eine Stufe von 0 bis 3 oder `unknown`, wenn es keinen brauchbaren Beleg gibt:

| Kriterium | Frage | 0 heißt | Sperre |
|---|---|---|---|
| `authoritative_current` | Maßgeblich und aktuell? | überholt oder älter als 2× die erwartete Aktualisierung | ja |
| `owned_maintained` | Verantwortet und gepflegt? | keine verantwortliche Person | |
| `meaning_clear` | Fachliche Bedeutung klar? | keine Beschreibung | |
| `retrievable_precise` | Genauer Abschnitt oder Datensatz abrufbar? | keine stabile ID oder nie gefunden | |
| `conflicts_resolved` | Widersprüche aufgelöst? | offene Dubletten-Gruppe ohne Gewinner | |
| `permissions_explicit` | Berechtigungen ausdrücklich? | freigegeben für „Jeder“, „Jeder außer externen Benutzern“ oder einen anonymen Link | ja |

Stufe 2 heißt „erfüllt“, Stufe 3 „erfüllt und wird automatisch geprüft“. Die vollständigen Beschreibungen und die Zuordnung zu DAMA, ISO/IEC 25012 und FAIR stehen in `src/konmari/scorecard/rubric.default.yaml`. Eine einmal benutzte Rubrik-Fassung ist unveränderlich. Eine geänderte Rubrik braucht eine neue `rubric_version` (`konmari score --rubric meine-rubrik.yaml`).

## 2. Belege erfassen

Das Kit liest das Inventar selbst (Verantwortliche, Pflegende, Aktualisierung, letzte Änderung, Schutzstufe). Weitere Fakten kommen aus späteren Schritten (Dubletten, Glossar, Berechtigungen, Abruftests). Bis dahin von Hand erfassen oder importieren:

```
konmari score evidence <asset> --kind acl_snapshot --data '{"principals": ["ben", "Everyone except external users"]}' --ref acl:2026-10-20 --as dirk
konmari score evidence <asset> --kind glossary_coverage --data '{"described": true, "mapped": 3, "total": 4}' --ref glossar:v2 --as clara
konmari score evidence <asset> --kind stable_id --data '{"present": true}' --ref chunks:lauf-5 --as dirk
konmari score evidence <asset> --kind dedup_check --data '{"open_clusters": 0}' --ref dedup:lauf-1 --as dirk
konmari score ingest --soda results.json --as dirk        # Soda-Core-Prüfungen zu Aktualität und Dubletten (optional)
konmari score ingest --odcs contract.yaml --as dirk       # ODCS-Datenvertrag: Beschreibung und Glossar-Abdeckung
konmari score irrelevant <asset> --reason "für keine Pilot-Frage nötig" --as clara
```

Ein fehlender Beleg zählt nie als bestanden: Das Kriterium bleibt `unknown`, und das Asset kann kein `keep` bekommen. Belege, die älter als 7 Tage sind, werden als `stale_evidence` markiert. Wo ein Konnektor es kann, holt das Kit sie neu, sonst zählt das Kriterium als `unknown`.

## 3. Freude-Test starten

```
konmari score --as ben
```

Der Lauf bewertet die Kategorien in der freigegebenen Reihenfolge und zeigt je Asset einen Vorschlag:

- **discard**, wenn eine Fachperson das Asset als unnötig markiert hat, wenn eine andere Kopie Gewinner der Dubletten-Gruppe ist (`redundant`) oder wenn es älter als 2× seine Aktualisierung ist und keine aktive verantwortliche Person hat (`stale_unowned`).
- **keep**, wenn alle sechs Kriterien mindestens Stufe 2 haben und keines `unknown` ist.
- sonst **fix**, mit einem Nachbesserungspunkt je schwachem Kriterium und dem Schritt, der ihn beheben soll (z. B. `permissions_explicit -> access-control`). Eine Sperre auf 0 heißt immer fix, egal wie gut der Rest ist.

Assets in Kategorien außerhalb der Charta werden als „out of scope“ aufgeführt und nicht bewertet.

Die Schwellen sind unsere Annahmen. Je Pilot lassen sie sich in `scorecard.yaml` neben `charter.yaml` ändern. Jeder Lauf speichert die verwendeten Werte:

```yaml
keep_level: 2
stale_multiple: 2
agent_ready_share: 0.8
in_progress_share: 0.4
evidence_max_age_days: 7
```

## 4. Vorschläge prüfen

Entscheiden dürfen nur die Prüfpersonen und die Freigabeperson der Kategorie des Assets laut Charta (`data_scope.categories`). Versuche anderer Personen werden abgelehnt und protokolliert.

```
konmari review list --status proposed
konmari review accept <asset> --as clara
konmari review accept --all-keep --as clara      # nur keep-Vorschläge ohne unknown
konmari review override <asset> --to fix --justification "..." --as clara
konmari review reverse <asset> --to keep --justification "..." --as ben   # freigegebene Entscheidung ändern
konmari review list <asset>                       # vollständiger Verlauf
```

Jeder Vorschlag, jede Annahme, Abweichung und Rücknahme ist eine neue Zeile. Nichts wird überschrieben, der Verlauf bleibt vollständig. Spätere Schritte richten sich nur nach der jüngsten freigegebenen Entscheidung. Sind alle Assets einer Kategorie freigegeben, ist dort der Schritt `score` abgeschlossen, und die nächste Kategorie kann beginnen.

## 5. Neu bewerten

```
konmari score --changed --as ben                         # Assets mit geändertem Inventar-Eintrag
konmari score fix-done <asset> meaning_clear --as clara   # Nachbesserung erledigt
konmari score alert <asset> --status error --ref monitor:17 --as dirk   # Aktualitäts-Alarm
```

Ändert sich der Vorschlag, steht er wieder auf `proposed` und muss erneut geprüft werden.

## 6. Scorecard lesen

```
konmari scorecard report                       # Markdown
konmari scorecard report --format json -o scorecard.json
konmari scorecard report --format csv --level category
konmari scorecard compare <lauf_a> <lauf_b>    # über Rubrik-Fassungen hinweg nur mit --allow-rubric-mismatch
```

Die Asset-Tabelle zeigt immer alle sechs Stufen neben der niedrigsten (`Min`). Die Kategorie-Ansicht bewertet die sechs Aspekte der Tabelle „schlecht vorbereitet vs. agentenbereit“: Format, Metadata, Access, Consistency, Update mechanism und Discoverability. Der Anteil eines Aspekts zählt die Assets, deren zugeordnete Kriterien alle Stufe 2 erreichen (Update mechanism braucht `authoritative_current` auf 3). `unknown` zählt als nicht erreicht. Ab 80 % ist der Aspekt `agent_ready`, ab 40 % `in_progress`, darunter `poorly_prepared`. Der Bericht druckt diese Regel jedes Mal mit.

## Datenschutz (DSGVO)

- Die Scorecard speichert nur Verweise, Hashes, Anzahlen und Merkmale, nie Inhalte aus Ihren Assets (Art. 5 Abs. 1 lit. c DSGVO). `konmari score evidence` lehnt Freitext ab. Von einer Berechtigungsliste bleiben nur die Merkmale für zu breite Freigaben, die Anzahl der Berechtigten und ein Hash der Liste, nicht die Namen.
- Keine personenbezogenen Daten in Begründungen schreiben. Den Grund nennen („Dublette des gedruckten Handbuchs“), nicht den Inhalt.
- `discard` löscht nichts. Gesetzliche Aufbewahrungspflichten und Sperren prüft Schritt 4, bevor etwas archiviert oder gelöscht wird.
