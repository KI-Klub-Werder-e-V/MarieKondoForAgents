"""Versioned readiness rubric: load and validate, publish (immutable), fetch."""

import json
from importlib.resources import files
from pathlib import Path

from konmari.canonical import canonical_hash, canonical_json, load_yaml
from konmari.catalog import Catalog, audit, now
from konmari.schema import schema_errors
from konmari.versions import Refused

CRITERIA = ["authoritative_current", "owned_maintained", "meaning_clear", "retrievable_precise", "conflicts_resolved",
            "permissions_explicit"]


class RubricImmutable(Refused):
    """A published rubric version cannot be changed; create a new rubric_version instead."""


def load(path: str | Path | None = None) -> dict:
    """The rubric from `path`, or the shipped default; validated against schemas/rubric.schema.json."""
    text = Path(path).read_text(encoding="utf-8") if path else \
        (files("konmari") / "scorecard" / "rubric.default.yaml").read_text(encoding="utf-8")
    doc = load_yaml(text)
    errors = schema_errors(doc, "rubric")
    if not errors and sorted(c["id"] for c in doc["criteria"]) != sorted(CRITERIA):
        errors.append("criteria: must list each of the six joy criteria exactly once")
    if errors:
        raise Refused([f"rubric {path or 'default'}: {e}" for e in errors])
    return doc


def gates(doc: dict) -> set[str]:
    return {c["id"] for c in doc["criteria"] if c["gate"]}


def get(cat: Catalog, version: str) -> dict | None:
    row = cat.one("SELECT * FROM rubric_version WHERE version = ?", (version,))
    if row:
        row["doc"] = json.loads(row["content"])
    return row


def publish(cat: Catalog, doc: dict, actor: str) -> dict:
    """Store `doc` as a published version. Re-publishing identical content is a no-op; changed content
    under an already published version is refused."""
    ver, h = doc["rubric_version"], canonical_hash(doc)
    row = get(cat, ver)
    if row:
        if row["content_hash"] != h:
            raise RubricImmutable(f"rubric version '{ver}' is published and immutable; give the changed rubric "
                                  "a new rubric_version")
        return row
    cat.execute("INSERT INTO rubric_version VALUES (?, ?, ?, ?, ?)", (ver, canonical_json(doc), h, now(), actor))
    audit(cat, "scorecard.rubric.publish", actor=actor, step="score", result="published",
          details={"rubric_version": ver, "content_hash": h})
    return get(cat, ver)
