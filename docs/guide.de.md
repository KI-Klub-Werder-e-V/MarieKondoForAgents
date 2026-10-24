# Leitfaden Pilot-Charta (Schritt 1 des KonMari-Kits)

Dieser Leitfaden richtet sich an alle, die einen Piloten durchführen: die fachlich verantwortliche Person, die oder den Datenschutzbeauftragten (DSB), die IT und, falls vorhanden, eine ehrenamtliche oder beratende Begleitung. Ein eigenes Datenteam ist nicht nötig. Am Ende stehen zwei Dateien, `charter.yaml` und `metrics.yaml`, und ein Satz Prüffragen. Erst wenn diese freigegeben sind, dürfen spätere Schritte Daten anfassen.

Ein vollständiges, erfundenes Beispiel liegt in `examples/verein-faq-assistant/`.

## 1. Einen Pilot-Anwendungsfall wählen

```
konmari charter init --template minimal --lang de   # oder --template full mit Erklärung zu jedem Feld
```

Wählen Sie **einen** Anwendungsfall. Für einen zweiten legen Sie eine eigene Charta an. Ein guter erster Pilot:

- hat eine benannte fachliche Verantwortliche, die ihn will,
- ersetzt einen Ablauf, den Sie heute schon messen können (wie lange dauert eine Antwort jetzt?),
- kommt mit wenigen Quellen aus (unsere Annahme: 3 bis 10),
- beginnt mit unkritischen Daten wie FAQ, Handbüchern und Arbeitsanweisungen, nicht mit Personal- oder Gesundheitsdaten,
- ist nach der KI-Verordnung kein Hochrisiko-Fall, außer Sie nehmen die zusätzlichen Pflichten bewusst in Kauf.

Halten Sie fest, was der Agent tun soll, mindestens eine Sache, die er ausdrücklich **nicht** tun soll, und mindestens einen möglichen Missbrauch (zum Beispiel „private Daten von Mitgliedern nachschlagen“).

## 2. Stakeholder-Register und RACI ausfüllen

Jede Person bekommt eine `id`, Namen, Kontakt, Bereich und eine oder mehrere Rollen: `sponsor` (Auftraggeber), `use_case_owner` (fachlich verantwortlich), `data_owner` (Dateneigentümer), `data_steward` (Fachexperte), `data_custodian` (IT), `compliance` (Datenschutz), `agent_user` (Nutzer), `facilitator` (Begleitung).

Die RACI-Tabelle legt für jede der sieben Aktivitäten fest, wer **A**ccountable ist (genau eine Person, sie entscheidet), wer **R**esponsible ist (mindestens eine Person, sie erledigt die Arbeit), wer **C**onsulted (gefragt) und wer **I**nformed (informiert) wird. Das Werkzeug prüft:

- Je Aktivität gibt es genau eine Accountable-Person.
- Über die DSFA entscheidet eine Person mit der Rolle `compliance`.
- Die Begleitung ist nie Accountable. Die Entscheidungen bleiben in Ihrer Organisation.
- Jede Datenkategorie im Umfang hat einen `data_owner` als Freigeber und mindestens eine prüfende Person.

## 3. Die DSFA-Schwellwertanalyse mit dem DSB machen

Die Charta ersetzt keine Datenschutz-Folgenabschätzung (DSFA). Sie hält nur das **Ergebnis der Vorprüfung** fest und wer es entschieden hat:

- `dpia_screening.result`: `required` (DSFA nötig), `not_required` (nicht nötig) oder `pending` (offen). Dazu gehören eine Begründung mit mindestens 20 Zeichen und die ID der Datenschutz-Person, die entschieden hat. Nach Art. 35 Abs. 2 DSGVO holen Sie dazu den Rat des DSB ein. In Deutschland ist ein DSB ab 20 Personen Pflicht, die regelmäßig personenbezogene Daten automatisiert verarbeiten (§ 38 BDSG).
- `ai_act.classification`: `minimal`, `limited`, `high`, `prohibited` oder `pending`, jeweils mit Begründung.
- `personal_data_categories`: eine Liste oder `none`.
- `ai_literacy_briefing`: das Datum, an dem die Nutzer eingewiesen werden (KI-Kompetenz nach Art. 4 KI-VO). Das ist ein Onboarding-Punkt und keine rechtliche Bescheinigung.

`pending` verhindert die Freigabe. Bei `high` muss die Datenschutz-Person zusätzlich zustimmen (`konmari charter approve --as <dsb-id> --compliance-signoff`). `prohibited` lässt sich nie freigeben. Das Kit ersetzt keine Rechtsberatung. Den aktuellen Gesetzestext finden Sie auf [EUR-Lex](https://eur-lex.europa.eu/eli/reg/2016/679/oj).

Danach:

```
konmari charter validate
konmari charter approve --as <id der Accountable-Person für charter_approval>
```

Nach der Freigabe ändern Sie die Charta nur noch mit `konmari charter amend --reason "..." --as <id>`. Zurück zu einer früheren Fassung kommen Sie mit `--from-version N`. `konmari charter diff 1 2` zeigt, was sich geändert hat.

## 4. Kennzahlen festlegen und den Ist-Stand messen

`metrics.yaml` braucht mindestens je eine `must_have`-Kennzahl für Antwortqualität, für Enthaltung („weiß ich nicht“, wenn die Antwort nicht in den Daten steht) und für den geschäftlichen Nutzen. Dazu kommen mindestens eine Kennzahl zur Datenreife und eine zur Governance. Die Governance-Kennzahl `human_approved_decisions` hat als Ziel 100 %.

Der Satz Prüffragen (Golden Set) hat mindestens 30 Fragen. Bei mindestens 5 davon ist die richtige Reaktion `abstain` (nicht antworten) oder `flag_conflict` (Widerspruch melden). Die Fragen stammen von Nutzern oder Fachleuten, nicht von der Begleitung. Formulieren Sie echte Anfragen so um, dass keine personenbezogenen Daten darin stehen.

Messen Sie den **heutigen** Ablauf ohne Agent, bevor Daten bearbeitet werden:

```
konmari metrics validate
konmari metrics approve --as <id der Accountable-Person für metric_approval>
konmari metrics baseline time_to_answer --value 12 --method "50 Stichproben im März" --as ben
konmari metrics baseline answer_accuracy --not-yet-measured --plan-date 2026-03-01 --as clara
```

Solange ein Ist-Wert nur geplant ist, darf allein die Bestandsaufnahme (Schritt `inventory`) laufen. Aussortieren und alle weiteren Schritte warten auf echte Werte.

### Zu Zahlen von Anbietern

Anbieter von Support-KI nennen Spannen wie 20 bis 40 % weniger Anfragen in den ersten 3 bis 6 Monaten, 80 bis 90 % richtige Antworten und höchstens etwa 2 bis 5 % erfundene Antworten ([twig.so](https://www.twig.so/blog/kpis-measure-ai-customer-support-success), [twig.so zur Pilotdauer](https://www.twig.so/blog/how-long-ai-support-pilot-before-evaluating-results)). **Das sind Angaben von Anbietern, keine Standards.** Nutzen Sie sie höchstens als Gesprächsgrundlage und leiten Sie Ihre eigenen Ziele aus Ihrem Ist-Wert ab. Die Quote abgefangener Anfragen wirkt besser, als sie ist: Wer aufgibt, gilt als „erledigt“. Messen Sie deshalb auch die Lösungsquote oder die Zufriedenheit.

## 5. Im Rhythmus bleiben

Halten Sie jede Steuerungsrunde fest:

```
konmari charter log --meeting steering --date 2026-02-10 --attendees anna,eva --decision "Bestandsaufnahme starten" --action "dirk: Lesezugriff einrichten" --as gina
konmari charter status    # warnt, wenn die Steuerungsrunde überfällig ist (Intervall + 7 Tage)
```

An jedem Phasen-Übergang erfassen Sie Messwerte (`konmari metrics measure <id> --kind gate --step <schritt> ...`), zum Pilotende mit `--kind final`. `konmari metrics report` zeigt dann, ob der Pilot erfolgreich war.
