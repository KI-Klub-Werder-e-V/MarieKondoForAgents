# Checklisten Absichern: DSFA-Vorprüfung (DSGVO Art. 35) und KI-Verordnung

Vorlagen für den Piloten, ausgefüllt von der Begleitung zusammen mit dem Datenschutz. Sie halten eine Vorprüfung fest, keine rechtliche Bewertung. Prüfen Sie vorher den aktuellen Wortlaut der KI-Verordnung (Änderungen durch den Digital Omnibus) und die DSFA-Liste Ihrer Aufsichtsbehörde.

## DSFA-Vorprüfung (Art. 35)

Kreuzen Sie jedes zutreffende Kriterium an (Kriterien nach EDSA/WP248). Ab zwei Kreuzen ist meist eine DSFA nötig. Prüfen Sie zusätzlich die Liste der Verarbeitungen, die immer eine DSFA brauchen (in Deutschland die „Muss-Liste“ der DSK).

- [ ] Bewertung oder Einstufung von Personen (Profiling, Vorhersage)
- [ ] Automatisierte Entscheidungen mit rechtlicher oder ähnlich erheblicher Wirkung
- [ ] Systematische Überwachung von Personen
- [ ] Besondere Kategorien (Art. 9) oder höchstpersönliche Daten (Quellen mit `special-category` im Piloten)
- [ ] Umfangreiche Verarbeitung (Zahl der Personen, Menge, Dauer, räumliche Reichweite)
- [ ] Abgleich oder Zusammenführung von Datensätzen aus verschiedenen Quellen (c05, Abgleich über Systeme hinweg)
- [ ] Daten schutzbedürftiger Personen (Kinder, Beschäftigte, Patientinnen und Patienten)
- [ ] Innovative Technik (ein LLM-Agent zählt in der Regel dazu)
- [ ] Verarbeitung, die Personen an der Ausübung eines Rechts oder der Nutzung eines Dienstes hindert

Ergebnis: `erforderlich` / `nicht erforderlich`, Begründung, entschieden von (Datenschutz), Datum. Eintragen in der Charta (`compliance.dpia_screening`).

## KI-Verordnung

- [ ] **Art. 4 KI-Kompetenz**: Alle, die den Pilot-Agenten betreiben oder nutzen, sind eingewiesen (Charta `ai_literacy_briefing`); Datum und Teilnehmende festgehalten.
- [ ] **Art. 50 Transparenz**: Nutzende erfahren, dass sie mit einem KI-System sprechen; veröffentlichte erzeugte Texte sind gekennzeichnet, wo Art. 50 es verlangt.
- [ ] **Einstufung**: Der Einsatz steht nicht in Anhang III (Charta `compliance.ai_act.classification`). Steht er dort (`high`), in `governance.yaml` `audit.ai_act_high_risk: true` setzen und den nächsten Punkt abarbeiten.
- [ ] Art. 26 (nur bei Hochrisiko): Einsatz nach der Gebrauchsanweisung des Anbieters; menschliche Aufsicht durch benannte, geschulte Personen; Eingabedaten relevant und repräsentativ; Betrieb überwacht, Vorfälle gemeldet; Protokolle mindestens sechs Monate aufbewahrt (`konmari audit` setzt das Minimum durch); Beschäftigte und ihre Vertretung vor dem Einsatz am Arbeitsplatz informiert (Art. 26 Abs. 7); DSFA nutzt die Angaben des Anbieters (Art. 26 Abs. 9).

## Verzeichnis nach Art. 30

`konmari govern ropa -o ropa-entry.yaml` füllt den Eintrag für den Pilot-Agenten aus dem Katalog vor (Zweck, Datenkategorien aus der Inventur, Agenten und Berechtigungen als Empfänger, Aufbewahrung, Maßnahmen). Alle `<FILL>`-Felder ergänzen und ins Verzeichnis der Verarbeitungstätigkeiten aufnehmen.
