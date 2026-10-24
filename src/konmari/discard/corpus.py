"""The pilot corpus: items (documents, files, records) inside inventoried assets, their state and their text.

Items come from local file-share assets (walked directly) or from an item manifest (JSONL or YAML) for systems
whose items are exported to files first (SharePoint, CRM). Source locations are only ever read.
"""

import hashlib
import json
import os
import re
import unicodedata
from pathlib import Path

import yaml

from konmari.catalog import Catalog, now
from konmari.categories import store as cats
from konmari.charter.store import Scope
from konmari.versions import Refused

NORM_VERSION = "v1-nfc-whitespace"  # NFC, whitespace collapsed, case preserved
STATES = ["active", "archived", "excluded", "awaiting-masking", "restricted"]


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(item: dict) -> bytes:
    return Path(item["content_ref"]).read_bytes()


def read_text(item: dict) -> str:
    # shortcut: non-text formats are decoded as UTF-8 unless the manifest gives text_path; add PDF/DOCX extraction later
    return Path(item.get("text_ref") or item["content_ref"]).read_bytes().decode("utf-8", "replace")


def meta(item: dict) -> dict:
    return item["meta"] if isinstance(item["meta"], dict) else json.loads(item["meta"])


def load_manifest(path: str | Path) -> list[dict]:
    """One entry per item: asset_uri, path, optional item_id, title, text_path and metadata
    (version, logical_id, effective_date, valid_until, source_of_truth, last_entry, category, ...)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    rows = yaml.safe_load(text) if p.suffix in (".yaml", ".yml") else [json.loads(x) for x in text.splitlines()
                                                                         if x.strip()]
    out = []
    for i, r in enumerate(rows or [], 1):
        if not isinstance(r, dict) or not r.get("asset_uri") or not r.get("path"):
            raise Refused(f"{path} entry {i}: needs asset_uri and path")
        r = dict(r)
        for k in ("path", "text_path"):
            if r.get(k) and not Path(r[k]).is_absolute():
                r[k] = str(p.parent / r[k])
        out.append(r)
    return out


def _local_dir(asset: dict) -> Path | None:
    if asset["asset_uri"].startswith("file://") and asset.get("path") and Path(asset["path"]).is_dir():
        return Path(asset["path"])
    return None


def collect(cat: Catalog, sc: Scope, manifest: str | Path | None = None) -> tuple[list[dict], int]:
    """Items of the charter's inventoried assets, and the number of manifest entries outside the inventory."""
    assets = {a["asset_uri"]: a for a in cat.query("SELECT * FROM source_asset WHERE charter_id = ? AND status <>"
                                                   " 'missing'", (sc.charter_id,))}
    found, outside = [], 0
    for e in load_manifest(manifest) if manifest else []:
        if e["asset_uri"] not in assets:
            outside += 1
            continue
        rest = {k: v for k, v in e.items() if k not in ("asset_uri", "path", "item_id", "title", "text_path", "meta")}
        found.append({"item_id": e.get("item_id") or f"{e['asset_uri']}/{Path(e['path']).name}",
                      "asset_uri": e["asset_uri"], "title": e.get("title") or Path(e["path"]).name,
                      "content_ref": e["path"], "text_ref": e.get("text_path"),
                      "meta": {**rest, **(e.get("meta") or {})}})
    for uri, a in assets.items():
        if (root := _local_dir(a)) is None:
            continue
        for dirpath, _, names in os.walk(root):
            for n in sorted(names):
                f = Path(dirpath) / n
                found.append({"item_id": f"{uri}/{f.relative_to(root).as_posix()}", "asset_uri": uri, "title": n,
                              "content_ref": str(f), "text_ref": None, "meta": {}})
    return found, outside


def sync(cat: Catalog, sc: Scope, items: list[dict]) -> list[dict]:
    """Register new items as active and refresh metadata of known ones; returns the catalog rows.
    Erased items stay out even while their source copy still exists (deleting it is the source owner's open action)."""
    t = now()
    erased = {r["item_id"] for r in cat.query("SELECT item_id FROM erasure_record")}
    items = [it for it in items if it["item_id"] not in erased]
    for it in items:
        row = cat.one("SELECT state FROM corpus_item WHERE item_id = ?", (it["item_id"],))
        if row is None:
            cat.execute("INSERT INTO corpus_item (item_id, asset_uri, charter_id, title, content_ref, text_ref, meta,"
                        " state, state_changed_at, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?,"
                        " ?, ?)", (it["item_id"], it["asset_uri"], sc.charter_id, it["title"], it["content_ref"],
                                   it["text_ref"], json.dumps(it["meta"], sort_keys=True, default=str), t, t, t))
        else:
            cat.execute("UPDATE corpus_item SET title = ?, content_ref = ?, text_ref = ?, meta = ?, last_seen = ?"
                        " WHERE item_id = ?", (it["title"], it["content_ref"], it["text_ref"],
                                               json.dumps(it["meta"], sort_keys=True, default=str), t, it["item_id"]))
    return [get(cat, it["item_id"]) for it in items]


def get(cat: Catalog, item_id: str) -> dict | None:
    row = cat.one("SELECT * FROM corpus_item WHERE item_id = ?", (item_id,))
    if row:
        row["meta"] = json.loads(row["meta"])
    return row


def require(cat: Catalog, item_id: str) -> dict:
    row = get(cat, item_id)
    if row is None:
        raise Refused(f"unknown item '{item_id}'")
    return row


def kept_items(cat: Catalog, charter_id: str) -> list[dict]:
    """What c05, c06 and c08 read: only active items (never archived, excluded, awaiting-masking or restricted)."""
    return cat.query("SELECT * FROM kept_item WHERE charter_id = ? ORDER BY item_id", (charter_id,))


def category(cat: Catalog, item: dict) -> str | None:
    """Item-level category from source metadata, else the accepted primary category of its asset."""
    if c := meta(item).get("category"):
        return c
    p = cats.primary(cat, item["asset_uri"])
    return p["category_id"] if p and p["state"] == "accepted" else None


def register_derived(cat: Catalog, item_id: str, artifact_id: str, kind: str) -> None:
    """c06/c08 register chunks, embeddings and data-product rows so discards can be propagated."""
    cat.execute("INSERT INTO derived_artifact (artifact_id, item_id, kind, registered_at) VALUES (?, ?, ?, ?)",
                (artifact_id, item_id, kind, now()))
