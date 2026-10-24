# Anleitung Strukturieren und Anreichern (Schritt 6 des KonMari-Kits)

Nach dem Aussortieren (Schritt 4) und Vereinheitlichen (Schritt 5) sind die Pilotdaten sauber, aber noch für Menschen gemacht: ganze Dokumente, rohe Tabellen, kurze Spaltennamen. Dieser Schritt zerlegt Dokumente in Abschnitte, die ein Agent finden und zitieren kann (**Chunks**), versieht jeden Chunk und jede Tabelle mit einem Etikett (Quelle, Datum, verantwortliche Person, führende Quelle, Berechtigungen, Schutzstufe: der **Metadaten-Umschlag**) und legt fest, was Fachbegriffe bedeuten (**Glossar**). Jede Entscheidung landet im Prüfprotokoll und lässt sich zurücknehmen.

Standardmäßig verlässt nichts Ihr Haus. Ein Sprachmodell wird nur aufgerufen, wenn Sie es einschalten (Abschnitt 6).

Voraussetzung sind die Schritte 1 bis 5: freigegebene Charta, Inventur (Verantwortliche, Aktualisierung, Schutzstufe), Aussortieren (nur behaltene Elemente werden zerlegt) und der Schritt `standardize` je Kategorie als erledigt markiert (`konmari categories step <kategorie> standardize --status complete --as <id>`).

## 1. Wer entscheidet was

| Entscheidung | Wer |
|---|---|
| Chunking-Profil einer Kategorie (Größe, Tokenizer, LLM-Kontext an/aus) | jede Person aus der Charta, mit Begründung |
| Manueller Metadatenwert (Verantwortliche, ACL-Tags, gültig bis, ...) | die Datenverantwortliche, mit Begründung |
| Tabellen- und Spaltenbeschreibungen | die Datenverantwortliche der Tabelle |
| Glossar-Stewards | Sponsor oder Use-Case-Verantwortliche |
| Begriff freigeben, ablehnen, abkündigen oder zurücksetzen; Glossar-Konflikt entscheiden | ein Steward, der diese Fassung **nicht** selbst geschrieben hat (Vier-Augen-Prinzip) |

## 2. Zerlegen und den Bericht prüfen

```
konmari chunk run --dry-run --as <id>   # rechnet alles durch, schreibt nichts
konmari chunk run --as <id>
```

So lesen Sie den Bericht:

- **refused**: Elemente, die nicht zerlegt werden, mit Grund: `not-kept` (in Schritt 4 archiviert oder ausgeschlossen), `out-of-pilot-scope` (Quelle oder Kategorie nicht in der Charta), `not-normalized` (Schritt 5 für die Kategorie nicht erledigt).
- **token sizes**: kleinster, mittlerer, 95-%- und größter Chunk. Standard-Obergrenze: 400 Tokens.
- **table chunks**: Tabellen werden nie mit Text gemischt. Lange Tabellen werden zwischen Zeilen geteilt, jeder Teil wiederholt Kopfzeile und Beschriftung.
- **chunks above the flag size** (800 Tokens): bitte ansehen, meist ein sehr langer Listenpunkt oder eine lange Tabellenzeile.
- **plain-text fallback**: Dokumente ohne Überschriften, Listen oder Tabellen wurden schlicht geteilt. Überschriften in der Quelle ergeben bessere Chunks.
- **parse errors**: z. B. ein gescanntes PDF ohne Textebene. Es bekommt den Status `needs-review`; entscheiden Sie, ob es außerhalb des Kits per OCR erfasst, ersetzt oder aussortiert wird.
- **full-context feasible**: Der ganze Pilotbestand hat weniger als 200.000 Tokens. Ein Modell mit langem Kontext könnte ihn ganz lesen, statt Chunks zu suchen.

Ein erneuter Lauf auf unveränderten Dokumenten erzeugt keine neuen Chunks. Ändert sich ein Dokument, behalten unveränderte Abschnitte ihre Chunk-ID, entfernte Abschnitte werden als `superseded` markiert.

## 3. Ein Profil anpassen

```
konmari chunk profile show
konmari chunk profile set policies-and-contracts --max-tokens 600 --model-max-tokens 1024 --reason "lange Klauseln" --as <id>
```

Alter und neuer Wert, Ihre ID und die Begründung kommen ins Prüfprotokoll. Damit Tokens genau wie im Embedding-Modell gezählt werden, setzen Sie dessen Tokenizer: `--tokenizer hf:/pfad/zu/tokenizer.json`. Der Standard `regex-v1` ist eine Näherung ohne Netz.

## 4. Anreichern und Lücken schließen

```
konmari enrich schema load schema.yaml --as <id>   # Tabellen, die Agenten lesen dürfen, mit Spaltenbeschreibungen
konmari enrich run --as <id>
```

Der Bericht nennt jeden Chunk und jede Tabelle mit `needs-review` und den Grund. Verantwortliche und Berechtigungen werden nie automatisch eingesetzt: Ohne Verantwortliche oder ohne ACL-Tags bleibt ein Chunk `needs-review`, bis ein Mensch entscheidet.

```
konmari enrich override <element-oder-chunk-oder-tabelle> --field owner --value clara --reason "pflegt die FAQ" --as <id>
konmari enrich override <element> --field acl_tags --value "group:Vorstand,group:Büro" --reason "Ablage ohne ACL-Schnittstelle" --as <id>
konmari enrich lineage <chunk-id>                  # woher ein Chunk stammt, Schritt für Schritt
konmari enrich schema report                       # Spalten ohne Beschreibung oder Glossar-Verweis (Exit 1)
```

Manuelle Werte bleiben bei späteren Läufen erhalten. `acl_stale` heißt: Die Quelle war in diesem Lauf nicht erreichbar; die bisherigen Tags bleiben, und die Zugriffskontrolle (Schritt 7) sperrt diese Chunks bis zum nächsten erfolgreichen Lauf. `expired` heißt: `valid_until` liegt in der Vergangenheit.

Beispiel `schema.yaml`:

```
tables:
  - table: manual://crm/invoices
    created_at: 2024-05-01T00:00:00Z
    acl: {allow: ["group:Geschäftsstelle"]}
    columns:
      - {name: kd_nr, description: Kundennummer, type: text, nullable: false}
      - {name: kd_stat, type: text, nullable: false, term: Kundenstatus}
      - {name: umsatz, description: Jahresumsatz, type: decimal, unit: EUR, nullable: true}
```

## 5. Glossarbegriffe freigeben

```
konmari glossary stewards --steward clara --steward ben --as anna
konmari glossary harvest --as <id>            # schlägt häufige Abkürzungen und Wörter als Entwürfe vor
konmari glossary add --file begriff.yaml --as <id>
konmari glossary submit <begriff> --as <id>
konmari glossary approve <begriff> --comment "passt" --as <anderer steward>
konmari glossary report                       # Abdeckung, Begriffe je Status, offene Konflikte
konmari glossary export --format skos -o glossar.ttl
```

Eine Begriffsdatei:

```
labels:
  pref: {de: Kündigungsquote, en: churn rate}
  alt: {de: [Churn-Rate]}
definition: Anteil der Mitglieder, die im Jahr kündigen.
owner: ben
category: customer-records
metric: {formula: "gekündigt / Mitglieder", grain: year, unit: "%", owner: ben}   # nur für Kennzahlen
```

Nur freigegebene Begriffe werden mit Chunks und Spalten verknüpft und exportiert. Wer einen freigegebenen Begriff ändert, erzeugt eine neue Fassung in Prüfung; die freigegebene bleibt sichtbar, bis die neue freigegeben ist. Konflikte (dieselbe Bezeichnung in zwei Begriffen, Spalten mit verschiedenen Einheiten am selben Begriff) werden nie automatisch gelöst: `konmari glossary conflict <id> --decision "..." --as <steward>`.

## 6. Sprachmodelle (nur auf Wunsch)

LLM-Kontext für Chunks und KI-Entwürfe für Definitionen sind standardmäßig aus. Wenn Sie sie einschalten, nutzen Sie ein selbst betriebenes Modell oder einen Auftragsverarbeiter mit Vertrag (DSGVO Art. 28):

```
konmari chunk profile set kb-and-sops --llm-context on --reason "Kontext-Retrieval testen" --as <id>
konmari enrich run --llm-url http://localhost:11434 --llm-model llama3 --as <id>
```

Chunks mit Schutzstufe `personal` oder `special-category` werden nie gesendet, solange sie nicht maskiert sind (Schritt 7). Erzeugter Text trägt den Modellnamen, KI-Entwürfe bleiben Entwürfe, bis ein Steward sie freigibt.

## 7. Zurücknehmen

```
konmari chunk rollback <lauf-id> --reason "Profil zu klein" --as <id>
konmari enrich rollback <lauf-id> --reason "..." --as <id>
konmari glossary revert <begriff> --to 1 --reason "..." --as <steward>
konmari glossary deprecate <begriff> --replaced-by <neuer-begriff> --comment "zusammengelegt" --as <steward>
```

Ein Chunk-Rollback macht die vorherigen Chunks wieder aktiv. Ein Anreicherungs-Rollback stellt die vorherigen Metadatenwerte wieder her. Abgekündigte Begriffe bleiben auffindbar und verweisen auf ihren Nachfolger.
