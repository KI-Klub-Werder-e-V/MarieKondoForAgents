# Anleitung Prüfen und Beobachten (Schritt 9 des KonMari-Kits: „evaluate“)

Nach dem Anbinden (Schritt 8) kann ein Agent die Pilotdaten lesen. Ob er damit richtig antwortet, weiß noch niemand. Dieser Schritt prüft das mit echten Fragen und beobachtet danach laufend, ob die Daten frisch und sauber bleiben. Er hat zwei Teile:

- **Prüfen** (`konmari eval`): ein Satz echter Fragen mit erwarteter Antwort (das *Golden Set*), ein Prüflauf gegen den Agenten, Grenzwerte, die eine Verschlechterung blockieren.
- **Beobachten** (`konmari monitor`): regelmäßige Prüfung jeder Quelle und jedes Datenprodukts auf Aktualität, Menge, Metadaten, Dubletten und Schemaänderungen. Probleme gehen an die verantwortliche Person.

Das ist keine Rechtsberatung. Fragen aus echten Nutzer-Protokollen sind Personendaten, solange Namen oder Kundennummern darin stehen. Die Datenschutzbeauftragte legt die Aufbewahrungsfrist fest (Voreinstellung 12 Monate, unsere Annahme).

## 1. Wer entscheidet was

| Entscheidung | Wer |
|---|---|
| Fragen sammeln | Fachleute (`data_owner`, `data_steward`) zusammen mit Nutzerinnen (`agent_user`) |
| Frage freigeben (`eval items approve`) | Fachperson oder Quellen-Verantwortliche (`data_owner`, `data_steward`) |
| Golden Set aktivieren (`eval set activate`) | jede Person aus der Charta; die Mindestgrößen prüft das Kit |
| Ein Lauf wird neue Vergleichsbasis (`eval baseline approve`) | eine benannte Person; Rücknahme mit `eval baseline revert` |
| Andere Grenzwerte | nur über die Erfolgskennzahlen der Charta (Schritt 1) |
| Andere Frische-Schwelle einer Quelle (`monitor threshold`) | nur die Verantwortliche der Quelle |
| Störung bestätigen, beheben, Risiko akzeptieren | eine benannte Person mit Begründung |
| Nächste Kategorie | Vorschlag vom Kit, Bestätigung durch `use_case_owner` |

Alles davon steht im Prüfprotokoll (`konmari audit verify`).

## 2. Ein Golden Set aus echten Fragen bauen

1. **Fragen sammeln.** Nehmen Sie Fragen, die wirklich gestellt wurden: aus dem Postfach der Geschäftsstelle, aus Tickets, aus dem Chat-Protokoll. Fragen, die das Projektteam sich ausdenkt, prüfen nur, was das Team gebaut hat.
2. **Mit einer Fachperson durchgehen.** Für jede Frage legen Sie fest:
   - die **erwartete Antwort** in einem Satz (`reference_answer`),
   - die **Quelle**, in der sie steht (`expected_sources`, Dokument- oder Abschnitts-ID aus Schritt 6),
   - das **erwartete Verhalten**: `answer` (beantworten), `abstain` (der Agent soll „weiß ich nicht“ sagen, weil die Daten es nicht hergeben) oder `flag_conflict` (zwei Quellen widersprechen sich; die führende Quelle zuerst nennen).
3. **Auch Fragen ohne Antwort aufnehmen.** Mindestens 20 % sollen `abstain` oder `flag_conflict` sein. Ein Agent, der immer etwas sagt, fällt sonst nicht auf.
4. **Herkunft angeben**: `user_log` (aus echten Protokollen), `domain_expert` (von einer Fachperson formuliert) oder `synthetic` (künstlich erzeugt). Höchstens die Hälfte darf `synthetic` sein.

Datei `fragen.yaml`:

```yaml
items:
  - id: q01
    question: Wie hoch ist der Jahresbeitrag für Erwachsene?
    origin: domain_expert
    category: kb-and-sops
    expected_behaviour: answer
    reference_answer: 120 Euro im Jahr
    expected_sources: [fee-rules]
  - id: q02
    question: Wer gewinnt das Turnier 2030?
    origin: synthetic
    category: kb-and-sops
    expected_behaviour: abstain
```

CSV geht auch (Spalten `id,question,origin,category,expected_behaviour,reference_answer,expected_sources`, mehrere Quellen mit `;` getrennt).

```
konmari eval items import fragen.yaml --as clara    # Entwürfe; user_log-Fragen werden vorher maskiert
konmari eval items approve q01 --as clara           # jede Frage einzeln freigeben
konmari eval set activate --as ben                  # mindestens 50 Fragen, 20 % ohne Antwort, höchstens 50 % synthetisch
```

Fragen mit Herkunft `user_log` laufen vor dem Speichern durch die Maskierung aus Schritt 7. Aus „Frau Petra Schulz, Kundennummer KD-482913“ wird „Frau `<PERSON>`, Kundennummer `<CUSTOMER_NUMBER>`“. Wer gefragt hat, wird nicht gespeichert. Nach Ablauf der Frist löscht `konmari eval items retention --as <id>` die Frage, außer sie wurde erneut freigegeben (eine neue Freigabe verlängert die Frist).

Eine geänderte Frage wird eine neue Fassung im Entwurf. Die alte Fassung bleibt lesbar und läuft weiter, bis die neue freigegeben und das Set neu aktiviert ist.

Kleine Pilotprojekte dürfen die Mindestgröße in `metrics.yaml` senken (`golden_set: {min_questions: 30, min_abstention: 5}`), aber nie unter 30 Fragen mit 5 ohne Antwort. Das Kit vermerkt die Abweichung im Prüfprotokoll.

## 3. Den Agenten prüfen

Der Agent wird über seine MCP-Schnittstelle gefragt, so wie ihn auch Nutzerinnen erreichen. Er bekommt die Rechte einer **Test-Person**, die in Schritt 7 als Agent registriert ist. Datei `eval.yaml`:

```yaml
agent:
  command: [uv, run, python, agent/server.py]   # oder url: https://agent.example.org/mcp
  tool: ask                                     # Werkzeug mit dem Argument "question"
  version: faq-bot@1.4                          # wird mit jedem Lauf gespeichert
  timeout_seconds: 30
principal: {agent: eval-bot, user: test-eval, groups: [support-team]}
judge: {provider: lexical}                      # oder: {provider: ragas, model: ..., version: ..., base_url: ...}
```

Das Werkzeug antwortet mit `answer`, `behaviour` (`answer`, `abstain`, `flag_conflict`), `retrieved` (gefundene Abschnitts-IDs) und `statements` (Aussagen mit ihren Quellen `citations`).

```
konmari eval run --as ben             # Exit 1, wenn der Agent nicht erreichbar war
konmari eval report                   # Ergebnis je Frage als JSON
konmari eval gate                     # Exit 1, wenn ein Grenzwert verfehlt ist
```

### Den Bericht lesen

| Kennzahl | Bedeutung | Was tun, wenn sie zu niedrig ist |
|---|---|---|
| `faithfulness` | Anteil der Aussagen, die durch die gefundenen Abschnitte gedeckt sind | `unsupported_claims` je Frage ansehen: erfindet der Agent etwas? |
| `context_recall` | Wurde die erwartete Quelle gefunden? | `missing_sources` ansehen: Abschnitte (Schritt 6) oder Suche verbessern |
| `context_precision` | Anteil der gefundenen Abschnitte aus der erwarteten Quelle | zu viel Beifang: Kategorien oder Metadaten schärfen |
| `citation_check` | Jede zitierte Quelle existiert, ist für die Test-Person erlaubt und aktuell | `stale_source`: veraltete Fassung zitiert (Schritt 5); `out_of_scope`: Rechte prüfen (Schritt 7), steht als Befund im Prüfprotokoll |
| `citation_precision`, `citation_recall` | Belegen die Zitate die Aussagen? Hat jede Aussage einen Beleg? | Agent-Anweisung: jede Aussage mit Quelle |
| `abstention_rate` | Anteil richtiger „weiß ich nicht“ / Widerspruch erkannt | `hallucinated_answer`: der Agent rät |
| `answer_rate` | Anteil beantworteter Fragen, die eine Antwort haben | `over_abstention`: der Agent ist zu vorsichtig |

Kategorien, die einen Grenzwert verfehlen, stehen als `fix candidate` in der Ausgabe und im Freude-Test (Schritt 3). Bestehen alle Kategorien, schlägt das Kit die nächste Kategorie aus der Reihenfolge von Schritt 2 vor. Erst `konmari eval next-category --confirm <vorschlag> --as ben` setzt sie.

### Bewertungsmodell (Judge)

Ob eine Aussage durch einen Abschnitt gedeckt ist, entscheidet ein Bewertungsmodell. Die Voreinstellung `lexical` arbeitet ohne KI-Modell: eine Aussage gilt als gedeckt, wenn alle ihre Inhaltswörter und Zahlen in den Abschnitten stehen. Mit `ragas` entscheidet ein Sprachmodell (`uv sync --extra ragas`, ein selbst betriebenes oder per Auftragsverarbeitungsvertrag angebundenes Modell). Modell und Version sind festgeschrieben.

Bevor Sie den Bewertungen trauen, vergleichen Fachleute sie mit eigenen Urteilen: mindestens 50 Fragen, die nicht zum Einstellen des Modells benutzt wurden. Datei `labels.csv` mit `item_id,human,judge`:

```
konmari eval calibrate labels.csv --judge-model konmari-lexical --judge-version 1 --tuning tuning-ids.txt --as clara
```

Unter einem Kappa von 0,6, oder sobald Modell oder Version wechseln, gelten die Modell-Kennzahlen als `uncalibrated`, und das Gate lässt sie nicht durch.

### Vergleichsbasis und CI

```
konmari eval baseline approve <lauf> --as ben     # dieser Lauf ist die neue Vergleichsbasis
konmari eval baseline revert --as ben             # zurück zur vorherigen
```

Fällt eine Kennzahl um mehr als 0,05 unter die Vergleichsbasis, schlägt das Gate fehl. Ein Beispiel für GitHub Actions, das bei jeder Änderung an Pipeline- oder Agent-Konfiguration prüft, liegt in `examples/ci/konmari-eval.yml`.

## 4. Laufend beobachten

```
konmari monitor run --dry-run          # nur anzeigen
konmari monitor run                    # prüfen, Störungen anlegen, benachrichtigen
konmari monitor schedule               # Cron-Zeile (oder --format systemd für einen Timer)
```

Jede Quelle braucht eine Verantwortliche und ein Aktualisierungsintervall aus der Inventur (Schritt 2, `inventory set --owner ... --update-frequency P7D`). Fehlt eines, meldet der Lauf die Quelle als `unmonitorable`, und `use_case_owner` bekommt eine Nachricht.

- **Aktualität**: Warnung, wenn die letzte Änderung älter als das Intervall ist; Fehler ab dem Doppelten. Beispiel: Intervall 7 Tage, letzte Änderung vor 15 Tagen, also Fehler. Die Verantwortliche kann das ändern: `konmari monitor threshold <quelle> --error-after-days 30 --reason "..." --as <owner>`.
- **Menge**: mehr als 20 % mehr oder weniger Dokumente/Zeilen als beim letzten Lauf ist ein Fehler.
- **Metadaten**: mehr als 5 % Abschnitte ohne Pflichtangaben (Verantwortliche, Gültig-ab-Datum) ist ein Fehler.
- **Dubletten**: mehr als 5 % Dubletten ist ein Fehler.
- **Schema**: geänderte Felder sind eine Warnung.

Einstellungen in `monitor.yaml`:

```yaml
defaults: {volume_tolerance: 0.2, metadata_tolerance: 0.05, duplicate_tolerance: 0.05, drift_threshold: 0.2}
assets:
  "sql://crm/contacts": {checks: [freshness, duplicate_share]}
notify: {channel: email, smtp_host: mail.example.org, from: konmari@example.org, user: konmari, password_env: SMTP_PASSWORD}
# oder: {channel: webhook, url: https://chat.example.org/hooks/...}; ohne Angabe: .konmari/notifications.jsonl
topics: {beitrag: [beitrag, gebühr], halle: [halle, training]}
```

Agenten sehen den Zustand: Abschnitte aus einer Quelle mit Fehler tragen `freshness_status: error` und das Datum der letzten Änderung. Der Agent soll dann darauf hinweisen oder nicht antworten.

### Störungen bearbeiten

Pro Quelle und Prüfung gibt es höchstens eine offene Störung und eine Nachricht, auch wenn die Prüfung jeden Tag wieder fehlschlägt. Nachrichten nennen Quelle, Prüfung und Messwert, aber nie Inhalte mit Personenbezug.

```
konmari monitor incidents --status open
konmari monitor incident acknowledge <id> --reason "Export kaputt, wird repariert" --as ben
konmari monitor incident resolve <id> --reason "Export repariert" --as ben
konmari monitor incident accept-risk <id> --reason "Archivtabelle, keine Änderungen mehr" --as ben
```

Besteht die Prüfung wieder, schließt das Kit die Störung selbst (`resolved` durch `system`). Bleibt ein Fehler mehr als 2 Werktage unbestätigt, geht eine Eskalation an `use_case_owner`. Ist eine Quelle mehr als 30 Tage am Stück veraltet, schlägt das Kit sie für das Aussortieren (Schritt 4) vor. Archiviert oder gelöscht wird erst nach einer Freigabe dort.

### Neue Themen erkennen

```
konmari monitor drift --questions fragen-protokoll.jsonl --as dirk   # Zeilen {"ts": "...", "text": "..."}
```

Das Kit vergleicht die Themen der letzten Woche mit der Woche davor. Tauchen neue Themen auf, bekommt `use_case_owner` die Bitte, passende Fragen ins Golden Set aufzunehmen. Drift blockiert nichts.

Ergebnisse von `dbt source freshness` oder Soda lassen sich übernehmen: `konmari monitor import dbt target/sources.json`.

## 5. Grenzwerte und woher sie kommen

Alle Werte lassen sich über die Erfolgskennzahlen der Charta ändern (Kennzahl mit dem Namen des Grenzwerts, z. B. `faithfulness`, oder Familie `abstention`). Quellen: [research/c09-evaluate-and-monitor.md](../research/c09-evaluate-and-monitor.md), Abschnitt 2.

| Grenzwert | Voreinstellung | Herkunft |
|---|---|---|
| Faithfulness | ≥ 0,80 | Annahme, angelehnt an das promptfoo-Beispiel (0,8) |
| Zitat-Präzision und -Vollständigkeit | je ≥ 0,80 | Annahme |
| Zitierte Quelle existiert, erlaubt, aktuell | 100 % | Annahme, deterministische Prüfung |
| Richtiges „weiß ich nicht“ | ≥ 0,90 | Annahme |
| Antwortquote bei beantwortbaren Fragen | ≥ 0,85 | Annahme, verhindert übervorsichtige Agenten |
| Verschlechterung gegenüber der Vergleichsbasis | höchstens 0,05 | Annahme |
| Bewertungsmodell: Kappa | ≥ 0,6 auf ≥ 50 Fragen | Annahme; Quellen nennen 100–500 Urteile nur als Faustregel |
| Golden Set | ≥ 50 Fragen, ≥ 20 % ohne Antwort, ≤ 50 % synthetisch | Annahme |
| Aktualität | Warnung ab 1×, Fehler ab 2× Intervall | Annahme, Muster von dbt warn/error |
| Eskalation | nach 2 Werktagen | Annahme ohne Quelle |
| Vorschlag zum Aussortieren | nach 30 Tagen Fehler | Annahme ohne Quelle |
| Aufbewahrung von Protokoll-Fragen | 12 Monate | Annahme; die Datenschutzbeauftragte entscheidet |
| Menge, Metadaten, Dubletten, Drift | 20 %, 5 %, 5 %, 0,2 | Annahmen, in `monitor.yaml` änderbar |
