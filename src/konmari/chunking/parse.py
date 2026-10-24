"""Document parsers: one element list per document (headings, paragraphs, list items, tables) with locations.

The built-in parsers are local and model-free (DOCX via its XML, HTML via the standard library, PDF via pdfplumber,
Markdown/plain text). Docling (`uv sync --extra docling`) is an optional parser behind the same interface; its PDF
pipeline needs layout models, so it is never the default.
"""

import re
import zipfile
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET


class ParseError(Exception):
    pass


def element(kind, text="", loc=None, **kw) -> dict:
    """kind: heading (level) | paragraph | list_item | table (rows, header_rows, caption)."""
    return {"kind": kind, "text": text.strip(), "loc": loc or {}, **kw}


def parse(path: str | Path, parser: str = "lite") -> list[dict]:
    path = Path(path)
    if parser == "docling":
        return parse_docling(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".docx":
            return parse_docx(path)
        if suffix == ".pdf":
            return parse_pdf(path)
        if suffix in (".html", ".htm"):
            return parse_html(path.read_text(encoding="utf-8", errors="replace"))
        return parse_text(path.read_bytes().decode("utf-8", errors="replace"))
    except ParseError:
        raise
    except Exception as e:  # corrupt files must surface as needs-review, never as a crash or a silent skip
        raise ParseError(f"{type(e).__name__}: {e}") from None


def has_structure(elements: list[dict]) -> bool:
    return any(e["kind"] in ("heading", "table", "list_item") for e in elements)


# ---------------------------------------------------------------- Markdown / plain text

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_MD_LIST = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")


def _md_row(line: str) -> list[str]:
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def parse_text(text: str) -> list[dict]:
    out, para, table = [], [], []

    def flush(i):
        if para:
            out.append(element("paragraph", " ".join(para), {"elements": [i, i]}))
            para.clear()
        if table:
            rows = [_md_row(r) for r in table if not re.fullmatch(r"\|?\s*:?-{3,}.*", r.strip())]
            out.append(element("table", loc={"elements": [i, i]}, rows=rows, header_rows=1, caption=None))
            table.clear()

    for i, line in enumerate(text.splitlines()):
        if not line.strip():
            flush(i)
        elif line.lstrip().startswith("|"):
            if para:
                flush(i)
            table.append(line)
        elif m := _MD_HEADING.match(line):
            flush(i)
            out.append(element("heading", m[2], {"elements": [i, i]}, level=len(m[1])))
        elif m := _MD_LIST.match(line):
            flush(i)
            out.append(element("list_item", m[1], {"elements": [i, i]}))
        else:
            if table:
                flush(i)
            para.append(line.strip())
    flush(len(text.splitlines()))
    return out


# ---------------------------------------------------------------- DOCX (Office Open XML, no extra dependency)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_STYLE = re.compile(r"(?i)^(?:heading|berschrift|überschrift)\s*(\d)$")


def _wtext(node) -> str:
    return "".join(t.text or "" for t in node.iter(f"{W}t"))


def parse_docx(path: Path) -> list[dict]:
    with zipfile.ZipFile(path) as z:
        body = ET.fromstring(z.read("word/document.xml")).find(f"{W}body")
    out, caption = [], None
    for i, node in enumerate(body):
        loc = {"elements": [i, i]}
        if node.tag == f"{W}tbl":
            rows = [[_wtext(tc).strip() for tc in tr.findall(f"{W}tc")] for tr in node.findall(f"{W}tr")]
            header = sum(1 for tr in node.findall(f"{W}tr") if tr.find(f"{W}trPr/{W}tblHeader") is not None) or 1
            out.append(element("table", loc=loc, rows=rows, header_rows=header, caption=caption))
            caption = None
            continue
        if node.tag != f"{W}p" or not (text := _wtext(node).strip()):
            continue
        style = node.find(f"{W}pPr/{W}pStyle")
        style = style.get(f"{W}val", "") if style is not None else ""
        if m := _HEADING_STYLE.match(style):
            out.append(element("heading", text, loc, level=int(m[1])))
        elif style == "Title":
            out.append(element("heading", text, loc, level=1))
        elif style.lower() in ("caption", "beschriftung"):
            if out and out[-1]["kind"] == "table" and not out[-1]["caption"]:
                out[-1]["caption"] = text
            else:
                caption = text
        elif node.find(f"{W}pPr/{W}numPr") is not None or style.lower().startswith("list"):
            out.append(element("list_item", text, loc))
        else:
            out.append(element("paragraph", text, loc))
    return out


# ---------------------------------------------------------------- HTML

class _Html(HTMLParser):
    BLOCK = {"p": "paragraph", "li": "list_item", "dt": "paragraph", "dd": "paragraph", "blockquote": "paragraph"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.buf, self.cur, self.skip, self.n = [], [], None, 0, 0
        self.table = None  # {"rows": [...], "header_rows": n, "caption": str}

    def handle_starttag(self, tag, attrs):
        self.n += 1
        if tag in ("script", "style", "nav", "head"):
            self.skip += 1
        elif tag == "table":
            self.table = {"rows": [], "header_rows": 0, "caption": None, "start": self.n}
        elif self.table is not None and tag == "tr":
            self.table["rows"].append([])
            self.table["th_only"] = True
        elif self.table is not None and tag in ("td", "th"):
            self.cur, self.buf = tag, []
            if tag == "td":
                self.table["th_only"] = False
        elif self.table is not None and tag == "caption":
            self.cur, self.buf = "caption", []
        elif re.fullmatch(r"h[1-6]", tag) or tag in self.BLOCK:
            self.cur, self.buf, self.start = tag, [], self.n

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "head"):
            self.skip = max(0, self.skip - 1)
            return
        text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        t = self.table
        if t is not None and tag in ("td", "th") and self.cur == tag:
            t["rows"][-1].append(text)
            self.cur = None
        elif t is not None and tag == "tr":
            if t.get("th_only") and len(t["rows"]) == t["header_rows"] + 1:
                t["header_rows"] += 1
        elif t is not None and tag == "caption":
            t["caption"], self.cur = text, None
        elif tag == "table" and t is not None:
            rows = [r for r in t["rows"] if r]
            self.out.append(element("table", loc={"elements": [t["start"], self.n]}, rows=rows,
                                    header_rows=t["header_rows"] or 1, caption=t["caption"]))
            self.table = None
        elif tag == self.cur and text:
            loc = {"elements": [self.start, self.n]}
            if tag.startswith("h") and len(tag) == 2:
                self.out.append(element("heading", text, loc, level=int(tag[1])))
            else:
                self.out.append(element(self.BLOCK[tag], text, loc))
            self.cur = None

    def handle_data(self, data):
        if not self.skip and self.cur:
            self.buf.append(data)


def parse_html(text: str) -> list[dict]:
    p = _Html()
    p.feed(text)
    p.close()
    return p.out


# ---------------------------------------------------------------- PDF (pdfplumber, text layer only; no OCR)

_CAPTION = re.compile(r"^(?:Tabelle|Table|Tab\.)\s*\d+", re.I)


def parse_pdf(path: Path) -> list[dict]:
    import pdfplumber

    pages = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            tables = page.find_tables()
            boxes = [t.bbox for t in tables]

            def outside(obj, boxes=boxes):
                return not any(b[0] <= obj.get("x0", 0) <= b[2] and b[1] <= obj.get("top", 0) <= b[3] for b in boxes)

            lines = page.filter(outside).extract_text_lines(return_chars=True)
            items = [("line", ln["top"], ln) for ln in lines if ln["text"].strip()]
            items += [("table", t.bbox[1], t) for t in tables]
            pages.append((pno, sorted(items, key=lambda x: x[1])))
    sizes = Counter(round(c["size"]) for _, items in pages for k, _, ln in items if k == "line" for c in ln["chars"])
    if not sizes:
        raise ParseError("no text layer (scanned PDF?); OCR is out of scope, a human must decide")
    body = sizes.most_common(1)[0][0]
    heading_sizes = sorted({s for s in sizes if s >= body + 2}, reverse=True)
    out, prev_bottom = [], None
    for pno, items in pages:
        loc = {"pages": [pno]}
        for n, (kind, top, obj) in enumerate(items):
            if kind == "table":
                rows = [[(c or "").strip() for c in r] for r in obj.extract()]
                last = out[-1] if out else None
                # a table that opens a page and continues the previous page's table is one table
                if n == 0 and last and last["kind"] == "table" and len(last["rows"][0]) == len(rows[0]):
                    if rows[:last["header_rows"]] == last["rows"][:last["header_rows"]]:
                        rows = rows[last["header_rows"]:]
                    last["rows"] += rows
                    last["loc"]["pages"].append(pno)
                    continue
                caption = None
                if last and last["kind"] == "paragraph" and _CAPTION.match(last["text"]):
                    caption = out.pop()["text"]
                out.append(element("table", loc=dict(loc, pages=[pno]), rows=rows, header_rows=1, caption=caption))
                prev_bottom = None
                continue
            size = Counter(round(c["size"]) for c in obj["chars"]).most_common(1)[0][0]
            text = obj["text"].strip()
            if size in heading_sizes:
                out.append(element("heading", text, dict(loc), level=heading_sizes.index(size) + 1))
                prev_bottom = None
            elif (out and out[-1]["kind"] == "paragraph" and prev_bottom is not None and not _CAPTION.match(text)
                  and top - prev_bottom < size * 0.8 and out[-1]["loc"]["pages"][-1] == pno):
                out[-1]["text"] += " " + text
                prev_bottom = obj["bottom"]
            else:
                out.append(element("paragraph", text, dict(loc)))
                prev_bottom = obj["bottom"]
    return out


# ---------------------------------------------------------------- Docling (optional extra)

def parse_docling(path: Path) -> list[dict]:
    try:
        from docling.document_converter import DocumentConverter
        from docling_core.types.doc import ListItem, SectionHeaderItem, TableItem, TitleItem
    except ImportError:
        raise ParseError("parser 'docling' needs the optional extra: uv sync --extra docling") from None
    try:
        doc = DocumentConverter().convert(str(path)).document
    except Exception as e:
        raise ParseError(f"docling: {type(e).__name__}: {e}") from None
    out = []
    for item, _level in doc.iterate_items():
        loc = {"pages": sorted({p.page_no for p in getattr(item, "prov", [])})}
        if isinstance(item, TableItem):
            df = item.export_to_dataframe(doc)
            rows = [[str(c) for c in df.columns]] + [[str(v) for v in r] for r in df.itertuples(index=False)]
            out.append(element("table", loc=loc, rows=rows, header_rows=1, caption=item.caption_text(doc) or None))
        elif isinstance(item, (TitleItem, SectionHeaderItem)):
            out.append(element("heading", item.text, loc, level=getattr(item, "level", 0) + 1))
        elif isinstance(item, ListItem):
            out.append(element("list_item", item.text, loc))
        elif getattr(item, "text", "").strip():
            out.append(element("paragraph", item.text, loc))
    return out
