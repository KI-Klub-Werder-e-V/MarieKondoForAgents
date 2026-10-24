"""Versioned documents (charter_version, metric_set_version): drafts are editable, approved versions are frozen."""

import json

from konmari.canonical import canonical_hash, canonical_json
from konmari.catalog import Catalog, now

TABLES = {"charter_version", "metric_set_version"}


class Refused(Exception):
    """A command was refused; args[0] is a list of reasons."""

    def __init__(self, reasons):
        super().__init__(reasons if isinstance(reasons, list) else [reasons])

    @property
    def reasons(self) -> list[str]:
        return self.args[0]


def _row(r: dict | None) -> dict | None:
    if r:
        r["doc"] = json.loads(r["content"])
    return r


def latest(cat: Catalog, table: str, charter_id: str) -> dict | None:
    assert table in TABLES
    return _row(cat.one(f"SELECT * FROM {table} WHERE charter_id = ? ORDER BY version DESC LIMIT 1", (charter_id,)))


def latest_approved(cat: Catalog, table: str, charter_id: str) -> dict | None:
    assert table in TABLES
    return _row(cat.one(f"SELECT * FROM {table} WHERE charter_id = ? AND status = 'approved'"
                        " ORDER BY version DESC LIMIT 1", (charter_id,)))


def get(cat: Catalog, table: str, charter_id: str, version: int) -> dict | None:
    assert table in TABLES
    return _row(cat.one(f"SELECT * FROM {table} WHERE charter_id = ? AND version = ?", (charter_id, version)))


def insert(cat: Catalog, table: str, charter_id: str, version: int, doc, created_by: str, reason=None) -> None:
    assert table in TABLES
    cat.execute(f"INSERT INTO {table} (charter_id, version, status, content, content_hash, created_at, created_by,"
                " reason) VALUES (?, ?, 'draft', ?, ?, ?, ?, ?)",
                (charter_id, version, canonical_json(doc), canonical_hash(doc), now(), created_by, reason))


def update_draft(cat: Catalog, table: str, charter_id: str, version: int, doc) -> None:
    assert table in TABLES
    cat.execute(f"UPDATE {table} SET content = ?, content_hash = ? WHERE charter_id = ? AND version = ?"
                " AND status = 'draft'", (canonical_json(doc), canonical_hash(doc), charter_id, version))


def set_status(cat: Catalog, table: str, charter_id: str, version: int, status: str) -> None:
    assert table in TABLES
    cat.execute(f"UPDATE {table} SET status = ? WHERE charter_id = ? AND version = ?", (status, charter_id, version))


def sync_draft(cat: Catalog, table: str, charter_id: str, doc, actor: str, what: str) -> tuple[dict, bool]:
    """Register `doc` as version 1 or refresh the open draft. Returns (row, changed)."""
    row = latest(cat, table, charter_id)
    h = canonical_hash(doc)
    if row is None:
        insert(cat, table, charter_id, 1, doc, actor, "initial version")
        return latest(cat, table, charter_id), True
    if row["content_hash"] == h:
        return row, False
    if row["status"] == "approved":
        raise Refused(f"the {what} file's content hash no longer matches approved version {row['version']}; "
                      f"change it only through 'konmari {what} amend --reason ...'")
    update_draft(cat, table, charter_id, row["version"], doc)
    return latest(cat, table, charter_id), True


def flatten(doc, prefix="") -> dict:
    if isinstance(doc, dict):
        return {k: v for key, val in doc.items() for k, v in flatten(val, f"{prefix}.{key}" if prefix else key).items()}
    if isinstance(doc, list) and any(isinstance(x, (dict, list)) for x in doc):
        return {k: v for i, val in enumerate(doc) for k, v in flatten(val, f"{prefix}[{i}]").items()}
    return {prefix: doc}


def diff(a, b) -> list[str]:
    fa, fb = flatten(a), flatten(b)
    out = []
    for k in sorted(set(fa) | set(fb)):
        if fa.get(k, "<absent>") != fb.get(k, "<absent>"):
            out.append(f"{k}: {json.dumps(fa.get(k, '<absent>'), ensure_ascii=False)} -> "
                       f"{json.dumps(fb.get(k, '<absent>'), ensure_ascii=False)}")
    return out
