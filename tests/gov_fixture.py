"""Synthetic c07 set-up written straight into the catalog: two in-scope categories, items with chunks, ACLs from
item manifests, approved source policies. No real data."""

import hashlib
import json

from konmari.canonical import load_yaml
from konmari.catalog import now
from konmari.charter.store import Scope
from konmari.governance import access, acl, pii
from konmari.governance import config as gcfg
from konmari.governance import policy as pol

from conftest import EXAMPLE

SUPPORT, KB = "support-history", "kb-and-sops"
CATS = {SUPPORT: {"approver": "ben", "reviewers": ["clara"]}, KB: {"approver": "clara", "reviewers": ["ben"]}}


def scope() -> Scope:
    doc = load_yaml(EXAMPLE / "charter.yaml")
    return Scope(doc["charter_id"], 1, [], {k: dict(v) for k, v in CATS.items()},
                 {s["id"]: s for s in doc["stakeholders"]})


def cfg(**pii_overrides) -> dict:
    c = gcfg.load("does-not-exist.yaml")
    c["pii"].update(pii_overrides)
    return c


def add_doc(cat, item_id: str, category: str, texts: list[str], allow=None, deny=None) -> list[str]:
    meta = {"category": category}
    if allow or deny:
        meta["acl"] = {"allow": allow or [], "deny": deny or [], "version": "src-v1"}
    t = now()
    cat.execute("INSERT INTO corpus_item (item_id, asset_uri, charter_id, title, content_ref, text_ref, meta, state,"
                " state_changed_at, first_seen, last_seen) VALUES (?, ?, 'verein-faq-assistant', ?, ?, NULL, ?,"
                " 'active', ?, ?, ?)", (item_id, f"manual://{category}", item_id, f"/nonexistent/{item_id}",
                                        json.dumps(meta), t, t, t))
    ids = []
    for i, text in enumerate(texts):
        cid = f"{item_id}#{i}"
        cat.execute("INSERT INTO chunk (chunk_id, item_id, source_version, first_run, heading_path, ordinal, kind,"
                    " method, content, content_hash, tokens, location, position, status, superseded_by, created_at)"
                    " VALUES (?, ?, 'v1', 'run', '[]', 0, 'text', 'structure', ?, ?, ?, '{}', ?, 'active', NULL, ?)",
                    (cid, item_id, text, hashlib.sha256(text.encode()).hexdigest(), len(text.split()), i, t))
        ids.append(cid)
    return ids


def set_manifest_acl(cat, item_id: str, allow, deny=()) -> None:
    it = cat.one("SELECT meta FROM corpus_item WHERE item_id = ?", (item_id,))
    meta = json.loads(it["meta"])
    meta["acl"] = {"allow": list(allow), "deny": list(deny)}
    cat.execute("UPDATE corpus_item SET meta = ? WHERE item_id = ?", (json.dumps(meta), item_id))


def approve_all(cat, sc, exposure="allowed", **changes) -> None:
    pol.seed(cat, sc, "gina")
    for c in sc.categories:
        row = pol.edit(cat, sc, c, {"exposure": exposure, **changes}, "gina", "fixture")
        pol.approve(cat, sc, c, row["version"], sc.approver(c), "fixture")


def govern(cat, sc, **pii_cfg) -> dict:
    """ACL sync, PII scan and index build for every category with an approved policy."""
    acl.sync(cat, sc, "dirk", force=True)
    pii.scan(cat, sc, "dirk", cfg(**pii_cfg))
    return pii.build_index(cat, sc, "dirk")


def agent(cat, sc, name="faq-bot", scopes=None, service=False):
    return access.register_agent(cat, sc, name, scopes or [f"read:{c}" for c in sc.categories], "gina", service)


def events(cat, action: str) -> list[dict]:
    return [r | {"details": json.loads(r["details"])} for r in
            cat.query("SELECT * FROM audit_event WHERE action = ? ORDER BY seq", (action,))]
