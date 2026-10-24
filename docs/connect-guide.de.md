# Anleitung Anbinden (Schritt 8 des KonMari-Kits: „connect“)

Nach dem Absichern (Schritt 7) sind die Pilotdaten bereinigt, beschrieben und mit Berechtigungen versehen. Ein Agent kommt aber noch nicht verlässlich an sie heran. Dieser Schritt macht aus den Daten **Datenprodukte**: beschriebene, versionierte Schnittstellen mit Verantwortlicher, Zweck, Feldbeschreibungen, Qualitätsregeln und einer Frische-Zusage. Ein **MCP-Server** (`konmari serve`) stellt sie Agenten als reine Lese-Werkzeuge bereit.

Das ist keine Rechtsberatung. Die Zweckangabe jedes Produkts prüft Ihre Datenschutzbeauftragte vor der ersten Veröffentlichung (Checkliste in Abschnitt 6).

## 1. Wer entscheidet was

| Entscheidung | Wer |
|---|---|
| Produktbeschreibung schreiben (`product.yaml`, `contract.yaml`) | Begleitung oder Datenverantwortliche, im Git-Repository |
| Version freigeben (`product approve`) und veröffentlichen (`product publish`) | nur die Produktverantwortliche (`owner` in `product.yaml`) |
| Zurückrollen (`product publish --rollback --reason ...`) | nur die Produktverantwortliche |
| Werkzeug-Beschreibung prüfen und festschreiben (`serve --approve-tool`) | eine zweite Person aus der Charta (z. B. `data_steward`) |
| Zweckangabe und Personendaten-Begründung | Datenschutz (`compliance`) prüft vor der ersten Veröffentlichung |

Jede Freigabe, Veröffentlichung, Rücknahme, Werkzeug-Freigabe und jeder Agenten-Aufruf steht im Prüfprotokoll (`konmari audit verify`).

## 2. Das erste Produkt im STDIO-Modus

STDIO heißt: der Agent (z. B. ein Desktop-Assistent) startet `konmari serve` selbst auf demselben Rechner. Es gibt keinen offenen Netzwerk-Port.

```
konmari product init support-faq --category kb-and-sops --owner ben --use-case "Member FAQ assistant"
# products/support-faq/product.yaml und contract.yaml ausfüllen: jedes TODO ersetzen
konmari product lint products/support-faq --as ben     # Schemas, Pflichtangaben, Vorstufen; legt den Entwurf an
konmari product refresh support-faq --as ben           # Daten aus der Quelle laden (nur geänderte Zeilen)
konmari product test support-faq                       # Qualitätsregeln auf den geladenen Daten
konmari product approve support-faq --version 1.0.0 --as ben
konmari product publish support-faq --version 1.0.0 --as ben
konmari serve --check-pins                             # neue Werkzeuge warten auf Prüfung (Exit 1)
konmari serve --print-tools                            # Beschreibungen lesen
konmari serve --approve-tool support_faq.v1.search --as clara
konmari serve --approve-tool support_faq.v1.get --as clara
konmari product list                                   # Katalog: Versionen, Frische, Werkzeuge
```

`lint` prüft auch die früheren Schritte: Der Anwendungsfall steht in der freigegebenen Charta, jede Quelle hat im Freude-Test „keep“, die führende Quelle passt zur Richtlinie aus Schritt 5, es gibt eine freigegebene Quellen-Richtlinie aus Schritt 7, und jedes Feld mit Personendaten wird maskiert (Regel für seinen `piiEntity` in der Richtlinie) oder ist in `personalDataJustification` begründet.

Der Agent bekommt seine Identität über Umgebungsvariablen, zum Beispiel in der Konfiguration des Agenten-Programms:

```
KONMARI_AGENT=faq-bot                  # mit "konmari govern agent faq-bot --scope read:kb-and-sops" angelegt
KONMARI_ON_BEHALF_OF=finn              # für wen der Agent fragt (ohne: nur Dienst-Agenten)
KONMARI_GROUPS=support-team
KONMARI_SCOPES=product:support-faq:read
command: konmari serve --stdio
```

Ohne `KONMARI_AGENT` startet der Server nicht.

## 3. Was ein Agent bekommt

- Je Produkt und Hauptversion zwei Werkzeuge: `<produkt>.v<N>.search` (Stichworte, Filter, höchstens 100 Datensätze je Seite, `next_cursor` für die nächste Seite) und `<produkt>.v<N>.get` (ein Datensatz über den Schlüssel). Keine SQL-Abfragen, keine Dateipfade, keine Schreibzugriffe.
- Nur die Felder aus dem Vertrag. Personendaten werden nach der Quellen-Richtlinie maskiert; Zeilen, die die Person nicht sehen darf, fehlen ganz.
- In jeder Antwort: Produkt, Version, `as_of` (Stand der Daten) und `fresh`/`stale`. Ist der Stand älter als die Frische-Zusage, steht im Text eine Warnung, damit der Agent „die Daten sind möglicherweise veraltet“ sagen kann.
- Ressourcen: `konmari://catalog` (alle aufrufbaren Produkte) und je Version `konmari://products/<id>/<version>` (Beschreibung) sowie `.../docs`.
- Grenzen in `connect.yaml`: `limits: {default_records: 100, max_records: 1000, calls_per_minute: 60, timeout_seconds: 10}`, `deprecation_days: 90`, `protocol_version: "2026-07-28"`, `fallback_protocol_version: "2025-11-25"` (auf `null` setzen, wenn kein älterer Agent mehr anfragt).

## 4. Werkzeuge prüfen und festschreiben

Jede Werkzeug-Beschreibung bekommt einen SHA-256-Fingerabdruck. Der Server zeigt ein Werkzeug nur, solange der Fingerabdruck dem freigegebenen entspricht. Ändert sich eine Beschreibung (neue Version, anderer Zweck, neues Feld), verschwindet das Werkzeug, bis jemand es neu freigibt. Das schützt vor unbemerkt veränderten Werkzeugen („rug pull“). Inhalte aus den Daten landen nie in einer Beschreibung.

## 5. Neue Versionen, Aktualisierung, Zurückrollen

- `konmari product diff products/support-faq` vergleicht mit der veröffentlichten Version: Feld entfernt oder Typ geändert = **Hauptversion** (2.0.0), neues Feld = **Nebenversion** (1.1.0), nur Beschreibung = **Patch** (1.0.1). Eine zu kleine Versionsnummer wird beim Veröffentlichen abgelehnt.
- Nach einer neuen Hauptversion bleibt die alte 90 Tage aufrufbar, als „deprecated“ mit Nachfolger und Abschaltdatum.
- Aktualisierung ohne Handarbeit: `konmari product schedule support-faq` (Cron-Zeile) oder `--format systemd` (Timer). Der Takt ist die halbe Frische-Zusage. Schlägt ein Qualitätstest fehl, bleiben die alten Daten und ihr `as_of` stehen; der Fehlversuch steht in `refresh_run` für die Überwachung (Schritt 9).
- Gelöschte Quellzeilen und in Schritt 4 archivierte oder gelöschte Datensätze verschwinden beim nächsten Lauf aus dem Produkt.
- Zurückrollen: `konmari product publish support-faq --rollback --reason "falsches Feld" --as ben`. Die vorige Version gilt wieder, die zurückgerollte ist „withdrawn“ und für Agenten weg, ohne Neustart.
- Agenten-Zugang ganz abschalten: `konmari serve` beenden. Die Daten bleiben unberührt.

## 6. HTTP mit OAuth (z. B. Keycloak)

Für mehrere Nutzer im Netz läuft der Server über HTTP als OAuth-2.1-Ressourcenserver nach der MCP-Spezifikation. In Keycloak: einen Realm für den Piloten, je Agent einen Client, Client-Scopes `product:<id>:read`, eine Audience-Zuordnung auf die Adresse des Servers. `connect.yaml`:

```yaml
http:
  resource: https://konmari.verein.example/mcp        # kanonische Adresse = erwartete Audience
  authorization_servers: [https://id.verein.example/realms/pilot]
  issuer: https://id.verein.example/realms/pilot
  jwks_url: https://id.verein.example/realms/pilot/protocol/openid-connect/certs
  # oder: introspection: {url: ..., client_id: konmari, secret_env: KONMARI_INTROSPECTION_SECRET}
```

```
konmari serve --http --host 127.0.0.1 --port 8765                       # ohne TLS nur lokal
konmari serve --http --host 0.0.0.0 --tls-cert cert.pem --tls-key key.pem
```

Der Server veröffentlicht `/.well-known/oauth-protected-resource/mcp`, antwortet ohne gültiges Token mit 401 und Verweis darauf, lehnt Tokens für andere Dienste, abgelaufene Tokens und Tokens in der URL ab, und antwortet mit 403 `insufficient_scope`, wenn der Scope des Produkts fehlt. Tokens werden nie an Quellsysteme weitergegeben.

## 7. Checkliste für die Datenschutzbeauftragte (vor der ersten Veröffentlichung)

- [ ] Die Zweckangabe (`description.purpose`) beschreibt einen Zweck der freigegebenen Charta und nichts darüber hinaus.
- [ ] `description.limitations` sagt, wofür das Produkt nicht gedacht ist (z. B. keine Einzelfallentscheidungen).
- [ ] Jedes Feld mit Schutzstufe `personal` oder `special-category` wird maskiert, oder die Begründung in `personalDataJustification` trägt (Datenminimierung, Art. 5 Abs. 1 lit. c DSGVO).
- [ ] Felder, die der Zweck nicht braucht, stehen nicht im Vertrag (sie werden dann auch nicht kopiert).
- [ ] Die Zeilen-Berechtigungen (`aclColumn` oder die Grundsätze der Quellen-Richtlinie) passen zu den Rollen im Verein.
- [ ] Das Produkt steht im Verzeichnis der Verarbeitungstätigkeiten (`konmari govern ropa`).

Schnittstelle für Schritt 9: [connect-contract.md](connect-contract.md).
