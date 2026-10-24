# Anleitung Inventur und Kategorien (Schritt 2 des KonMari-Kits)

KonMari-Regel 4: nach Kategorien aufräumen, nicht nach Ablageort. In diesem Schritt werden alle Quellen aus der freigegebenen Charta erfasst. Jeder Container (Dokumentbibliothek, Ordner, Tabelle) bekommt einen Inventareintrag, die Einträge werden systemübergreifend Kategorien zugeordnet, und die Reihenfolge der Kategorien wird festgelegt. In den Quellen ändert sich nichts: Alle Konnektoren lesen nur.

Voraussetzung ist eine freigegebene Charta mit Ausgangswerten (Schritt 1). `konmari charter gate --step inventory` muss bestehen.

## 1. Lesezugriff vorbereiten

Checkliste für die IT (`data_custodian`):

- [ ] Je Quelle ein technisches Konto nur für das Kit, mit **Lese**rechten und sonst nichts.
- [ ] SQL: eine Rolle mit `SELECT` nur auf die Schemas im Umfang. Das Kit öffnet Postgres-Sitzungen zusätzlich schreibgeschützt.
- [ ] Dateiablage: ein Benutzer mit Leserecht auf den Pfad aus der Charta.
- [ ] SharePoint/M365: eine Entra-ID-App nur mit der Anwendungsberechtigung **`Sites.Selected`** (siehe unten). Hat das Token `Sites.Read.All` oder eine `*.ReadWrite.All`-Rolle, bricht der Scan ab.
- [ ] Passwörter und Tokens gehören nicht in die Charta, die Kommandozeile oder den Katalog. Legen Sie sie in eine Umgebungsvariable oder Datei und übergeben Sie nur den Verweis: `--secret-ref env:CRM_DB_PASSWORD` oder `--secret-ref file:/run/secrets/crm`. Bei Dateiablagen enthält das Geheimnis `benutzer:passwort`.

### Die Freigabe `Sites.Selected` (einmalig, durch eine Tenant-Administration)

1. In Entra ID eine App registrieren und die Microsoft-Graph-**Anwendungs**berechtigung `Sites.Selected` hinzufügen. Administratorzustimmung erteilen.
2. Der App Lesezugriff auf genau die Sites aus der Charta geben, z. B. per Microsoft Graph: `POST /sites/{site-id}/permissions` mit `{"roles": ["read"], "grantedToIdentities": [{"application": {"id": "<App-ID>", "displayName": "konmari"}}]}`. In PnP PowerShell geht das mit `Grant-PnPAzureADAppSitePermission -Permissions Read`.
3. Ein Zugriffstoken für die App holen (Client-Credentials-Ablauf) und als Geheimnis ablegen, z. B. `export SP_TOKEN=...`. Tokens laufen nach etwa einer Stunde ab, also direkt vor dem Scan holen.

Das Kit zählt Sites und Bibliotheken ausdrücklich auf und ruft nie die Graph-Suche auf, weil die Suche die `Sites.Selected`-Grenze nicht beachtet.

## 2. Quellen anmelden und scannen

```
konmari inventory source add --connector sharepoint --locator contoso.sharepoint.com/sites/support --secret-ref env:SP_TOKEN --as dirk
konmari inventory source add --connector sql --locator postgresql+psycopg://reader@db.example.org/crm --secret-ref env:CRM_DB_PASSWORD --as dirk
konmari inventory source add --connector fileshare --locator smb://nas.example.org/office/faq --secret-ref env:NAS_LOGIN --as dirk
konmari inventory scan --as dirk
```

Eine Quelle, die nicht in der freigegebenen Charta steht, wird abgelehnt. Erst die Charta ändern. Dateiablagen über SMB brauchen `uv sync --extra smb`.

Der Scan liest eine Stichprobe (Standard: die ersten 200 Elemente oder Zeilen je Container) und sucht lokal mit Presidio und dem deutschen spaCy-Modell nach personenbezogenen Daten. Das Modell einmal installieren: `uv run python -m spacy download de_core_news_lg`. Gespeichert werden nur Typ, Anzahl der Treffer und höchster Wert, nie die Werte selbst. Folgescans lesen nur Geändertes neu (Graph-Delta, Dateizeit und -größe, Tabellenschema). Ein verschwundener Container wird als `missing` markiert, nie gelöscht.

Systeme ohne Konnektor, z. B. ein SaaS-CRM, kommen per CSV- oder YAML-Datei mit denselben Spalten hinein. Die Vorlage ist `examples/verein-faq-assistant/exports/fee-rules.csv`. Die Datei als `csv-import`-Quelle anmelden, dann `konmari inventory import exports/fee-rules.csv --as ben`. Ungültige Zeilen werden mit Zeilennummer gemeldet, die übrigen werden übernommen.

## 3. Einträge vervollständigen

Ein Eintrag ist `complete`, wenn er eine benannte Person als Verantwortliche hat (kein Team, kein Sammelpostfach), eine Aktualisierungsfrequenz (ISO 8601 wie `P1W`, oder `continuous`, `irregular`), ein Format und eine bestätigte Schutzstufe.

```
konmari inventory set <asset_uri> --owner ben --steward clara --update-frequency P1M --as ben
konmari inventory tier <asset_uri> --confirm --as ben                          # Owner oder Datenschutz
konmari inventory tier <asset_uri> --set internal --reason "Testdaten, synthetisch" --as ben
konmari inventory set <asset_uri> --ropa-ref VVT-07 --as eva                   # oder --ropa-missing
konmari inventory report --csv inventar.csv                                     # Status, %, Stufen, VVT-Lücken
konmari inventory report --gate                                                 # Exit 1, solange Lücken bleiben
konmari inventory waive <asset_uri> --reason "..." --as ben                     # protokollierte Ausnahme
konmari inventory export -o inventar.jsonld                                     # DCAT 3 JSON-LD
```

Die Stufen sind `public`, `internal`, `confidential`, `personal` und `special-category` (Art. 9 DSGVO, z. B. Gesundheitsdaten). Das Kit schlägt eine Stufe aus den Hinweisen vor und nie eine niedrigere. Entscheiden muss ein Mensch. Die Hinweise sind Eingabe für Ihr Verzeichnis von Verarbeitungstätigkeiten (VVT) und die DSFA. Sie ersetzen beides nicht.

## 4. Kategorien und ihre Reihenfolge

```
konmari categories suggest --as ben               # regelbasiert, jeder Vorschlag nennt seine Regel
konmari categories accept <asset_uri> --as ben    # oder: assign <asset_uri> <kategorie>, reject, demote, revert
konmari categories show policies-and-contracts    # eine Kategorie über alle Systeme, mit Überschneidungshinweisen
konmari categories order --propose --as ben       # Risikowert mit Eingaben und Gewichten
konmari categories approve --as ben               # eine Kategorie-Freigabeperson aus der Charta
konmari categories handoff                        # Exit 0 = Schritt 2 fertig, zeigt die Reihenfolge
```

Die fünf Standardkategorien sind `kb-and-sops`, `customer-records`, `policies-and-contracts`, `financial-ops-metrics` und `support-history`. Freigabepersonen können Kategorien hinzufügen, umbenennen und stilllegen. Die Reihenfolge geht vom niedrigsten Risikowert aufwärts. Eine Kategorie mit bestätigten besonderen Daten (`special-category`) kommt immer zuletzt. Die Gewichte sind unsere Annahmen und lassen sich mit `--weight systems=0.7` ändern. Eine geänderte Reihenfolge (`--set a,b,c --reason ...`) wird eine neue Fassung und muss erneut freigegeben werden.

Spätere Schritte arbeiten Kategorie für Kategorie in dieser Reihenfolge. `konmari categories step <kategorie> <schritt>` zeigt, ob ein Schritt starten darf. Eine Kategorie darf früheren Kategorien nur mit einer Ausnahme einer Freigabeperson vorgreifen: `--waive --reason ...`.
