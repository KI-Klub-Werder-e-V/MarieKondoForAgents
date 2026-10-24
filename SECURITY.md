# Sicherheit / Security

Das KonMari-Kit verarbeitet Berechtigungen und personenbezogene Daten. Sicherheitslücken nehmen
wir deshalb besonders ernst.

**Bitte melde Sicherheitslücken nicht als öffentliches Issue**, sondern über
[GitHub Private Vulnerability Reporting](https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten/security/advisories/new)
oder per E-Mail an admin@ki-klub-werder.de. Wir bestätigen den Eingang innerhalb von 7 Tagen.
Das Projekt wird ehrenamtlich gepflegt; einen festen Zeitrahmen für die Behebung können wir
nicht zusagen, halten dich aber auf dem Laufenden.

Besonders interessant sind:

- Wege, auf denen ein Agent über den MCP-Server Daten sieht, die ihm laut Richtlinie oder
  Quellberechtigung nicht zustehen
- Lücken in der Maskierung personenbezogener Daten
- Manipulationen am Prüfprotokoll, die `konmari audit verify` nicht erkennt
- Geheimnisse (Passwörter, Tokens), die im Katalog oder in Logs landen

Unterstützt wird jeweils nur der aktuelle Stand von `main`.

**English:** please do not open public issues for vulnerabilities. Use GitHub private
vulnerability reporting or email admin@ki-klub-werder.de. We acknowledge within 7 days; the
project is volunteer-run, so no fixed remediation timeline. Only `main` is supported.
