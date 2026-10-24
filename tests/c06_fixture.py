"""Synthetic c06 fixture corpus, generated at test time: DOCX, PDF with a multi-page table, HTML FAQ, plain text,
scanned PDF without text layer and a CRM sample table. No real data."""

import csv
import io
import zipfile
from pathlib import Path

from konmari.categories import store as cats
from konmari.charter.store import scope
from konmari.discard import corpus

CHARTER = "verein-faq-assistant"


# ---------------------------------------------------------------- DOCX

def _p(text, style=None, num=False):
    ppr = ""
    if style or num:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              ('<w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr>' if num else "") + "</w:pPr>"
    return f'<w:p>{ppr}<w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p>'


def _tbl(rows):
    def tr(r, head):
        pr = "<w:trPr><w:tblHeader/></w:trPr>" if head else ""
        return f"<w:tr>{pr}" + "".join(f"<w:tc>{_p(c)}</w:tc>" for c in r) + "</w:tr>"
    return "<w:tbl>" + "".join(tr(r, i == 0) for i, r in enumerate(rows)) + "</w:tbl>"


def docx(blocks) -> bytes:
    """blocks: ("h1", text) | ("p", text) | ("li", text) | ("caption", text) | ("table", rows)."""
    body = []
    for kind, v in blocks:
        if kind.startswith("h"):
            body.append(_p(v, f"Heading{kind[1]}"))
        elif kind == "li":
            body.append(_p(v, "ListParagraph", num=True))
        elif kind == "caption":
            body.append(_p(v, "Caption"))
        elif kind == "table":
            body.append(_tbl(v))
        else:
            body.append(_p(v))
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats'
           '.org/wordprocessingml/2006/main"><w:body>' + "".join(body) + "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override '
                   'PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
                   'wordprocessingml.document.main+xml"/></Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/relationships"><Relationship Id="r1" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


# ---------------------------------------------------------------- PDF (hand-written, Helvetica, ruled tables)

def pdf(pages: list[str]) -> bytes:
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None,
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    kids = []
    for c in pages:
        data = c.encode("cp1252")
        objs.append(f"<< /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R"
                    f" /F2 4 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + (o if isinstance(o, bytes) else o.encode("latin-1")) + b"\nendobj\n"
    x = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF\n".encode()
    return bytes(out)


def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def text(x, y, s, size=10, bold=False):
    return f"BT /{'F2' if bold else 'F1'} {size} Tf {x} {y} Td ({_esc(s)}) Tj ET\n"


def table(x, y, widths, rows, h=16):
    c, total = "", sum(widths)
    for i, row in enumerate(rows):
        cx = x
        for w, v in zip(widths, row):
            c += text(cx + 3, y - i * h - 12, v, 9)
            cx += w
    for i in range(len(rows) + 1):
        c += f"{x} {y - i * h} m {x + total} {y - i * h} l S\n"
    cx = x
    for w in [0] + widths:
        cx += w
        c += f"{cx} {y} m {cx} {y - len(rows) * h} l S\n"
    return c


PRICE_HEADER = ["Artikel", "Preis (EUR)", "Einheit"]
PRICE_ROWS = [[f"Artikel {i}", f"{i * 1.5:.2f}", "Stück"] for i in range(1, 61)]


def price_pdf() -> bytes:
    p1 = (text(50, 800, "Preisliste 2026", 18, True) + text(50, 770, "Diese Liste gilt ab 1. Januar 2026.")
          + text(50, 750, "Tabelle 1: Preise je Artikel", 9) + table(50, 740, [200, 100, 100],
                                                                     [PRICE_HEADER] + PRICE_ROWS[:40]))
    p2 = (table(50, 800, [200, 100, 100], [PRICE_HEADER] + PRICE_ROWS[40:]) + text(50, 400, "Hinweise", 14, True)
          + text(50, 380, "Alle Preise verstehen sich inklusive Mehrwertsteuer und gelten für Mitglieder."))
    return pdf([p1, p2])


def scanned_pdf() -> bytes:
    # an inline grey image and a frame, no text layer at all
    return pdf(["q 200 0 0 100 50 600 cm BI /W 2 /H 2 /CS /G /BPC 8 ID \x00\xff\xff\x00 EI Q\n"
                "40 590 m 260 590 l 260 710 l 40 710 l h S\n"])


# ---------------------------------------------------------------- HTML, text, CRM table

FAQ_HTML = """<html><head><title>FAQ</title><style>p {color: red}</style></head><body>
<nav>Startseite | Kontakt</nav>
<h1>Häufige Fragen</h1>
<h2>Wann ist der Mitgliedsbeitrag fällig?</h2>
<p>Der Beitrag ist am 31. Januar jedes Jahres fällig. Das MRR-Dashboard im Büro zeigt den Stand.</p>
<h2>Gibt es einen Familienrabatt?</h2>
<p>Ja, Familien zahlen höchstens 250 EUR im Jahr.</p>
<table><caption>Beiträge</caption><tr><th>Gruppe</th><th>Beitrag</th></tr>
<tr><td>Erwachsene</td><td>120 EUR</td></tr><tr><td>Kinder</td><td>60 EUR</td></tr></table>
</body></html>"""

NOTES_TXT = ("Notizen aus der Vorstandssitzung. Die Hallenzeiten werden im Winter angepasst und das Büro ist "
             "dienstags geschlossen.\n\nDie Kündigungsquote lag im letzten Jahr niedrig, die Churn-Rate wird "
             "jetzt monatlich betrachtet. Neue Mitglieder erhalten eine Begrüßungsmappe.\n")

CRM_ROWS = [{"kd_nr": "K-001", "kd_stat": "aktiv", "umsatz": "1200", "geaendert": "2026-03-01T10:00:00Z"},
            {"kd_nr": "K-002", "kd_stat": "inaktiv", "umsatz": "0", "geaendert": "2026-02-11T08:30:00Z"}]


def policy_docx() -> bytes:
    return docx([
        ("h1", "Policies"),
        ("h2", "Returns"),
        ("p", "Mitglieder können Vereinskleidung innerhalb von 14 Tagen zurückgeben. Der Kassenbon ist vorzulegen."),
        ("h3", "Exceptions"),
        ("p", "Bedruckte Trikots sind vom Umtausch ausgeschlossen."),
        ("li", "Sonderanfertigungen mit Namen"),
        ("li", "Getragene Schuhe"),
        ("h2", "Warranty"),
        ("p", "Die Gewährleistung beträgt zwei Jahre ab Kauf. Mängel meldet man der Geschäftsstelle."),
        ("caption", "Tabelle 2: Fristen"),
        ("table", [["Fall", "Frist"], ["Rückgabe", "14 Tage"], ["Gewährleistung", "2 Jahre"]]),
    ])


def write(d: Path) -> dict[str, Path]:
    d.mkdir(parents=True, exist_ok=True)
    files = {"docx": d / "policies.docx", "pdf": d / "prices.pdf", "html": d / "faq.html", "txt": d / "notes.txt",
             "scan": d / "scan.pdf", "crm": d / "customers.csv"}
    files["docx"].write_bytes(policy_docx())
    files["pdf"].write_bytes(price_pdf())
    files["html"].write_text(FAQ_HTML, encoding="utf-8")
    files["txt"].write_text(NOTES_TXT, encoding="utf-8")
    files["scan"].write_bytes(scanned_pdf())
    with files["crm"].open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, CRM_ROWS[0].keys())
        w.writeheader()
        w.writerows(CRM_ROWS)
    return files


# ---------------------------------------------------------------- pilot set-up on top of the c04 `dp` fixture

ACL = {"allow": ["group:Vorstand", "group:Geschäftsstelle"]}
DOCS = [  # (fixture key, asset_uri, extra manifest metadata)
    ("docx", "manual://policies/contracts", {"source_system": "sharepoint", "valid_until": "2027-12-31"}),
    ("pdf", "manual://kb/sops", {"source_system": "erp"}),
    ("html", "manual://kb/faq-export", {"source_system": "website"}),
    ("txt", "manual://support/tickets", {"source_system": "crm"}),
    ("scan", "manual://kb/sops", {"source_system": "fileshare"}),
]


def entries(files: dict[str, Path], acl=ACL) -> list[dict]:
    out = []
    for key, uri, extra in DOCS:
        e = {"asset_uri": uri, "path": str(files[key]), "item_id": f"{uri}/{files[key].name}",
             "created_at": "2025-01-10T09:00:00+01:00", "modified_at": "2026-03-02T12:00:00Z", **extra}
        if acl:
            e["acl"] = acl
        out.append(e)
    return out


def write_manifest(path: Path, rows: list[dict]) -> Path:
    import json

    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    return path


def ready_for_enrich(cat, categories=None) -> None:
    """Mark discard and standardize complete (with order waivers) for the in-scope categories."""
    sc = scope(CHARTER, cat)
    for c in categories or sc.categories:
        appr = sc.approver(c)
        for step in ("standardize", "enrich"):
            cats.waive_step(cat, sc, c, step, appr, "fixture: all categories in one pass")
        for step in ("discard", "standardize"):
            if cats.step_status(cat, sc.charter_id, c, step)["status"] != "complete":
                cats.set_step(cat, sc, c, step, "complete", appr)


def register(cat, manifest: Path) -> list[dict]:
    sc = scope(CHARTER, cat)
    found, _ = corpus.collect(cat, sc, manifest)
    return corpus.sync(cat, sc, found)
