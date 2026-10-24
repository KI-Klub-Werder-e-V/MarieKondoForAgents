"""access-control: source ACL capture (the only implementation; c06 `acl_tags` reads it), propagation to chunks,
records and the agent index, and the ACL sync with revocations and lag. Unknown ACL = nobody (fail closed)."""

import grp
import json
import logging
import pwd
import stat
from pathlib import Path

from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.discard import corpus
from konmari.governance import policy as pol
from konmari.governance.audit import write_event
from konmari.inventory.connectors import GRAPH

log = logging.getLogger("konmari.governance")


class AclUnavailable(Exception):
    """The source could not be asked for permissions in this run (connector failure)."""


class AclMissing(ValueError):
    """An index write without ACL fields."""


def normalize_acl(allow, deny=()) -> dict:
    """Normalized principals: '<type>:<name>' with type user|group|role|service; emails lower-cased. Group grants
    stay groups (never expanded to members), so membership changes need no re-sync of every chunk."""
    def norm(p):
        kind, _, name = str(p).partition(":")
        if not name:
            kind, name = "group", kind
        kind = kind.strip().lower()
        return f"{kind}:{name.strip().lower() if kind == 'user' else name.strip()}"
    return {"allow": sorted({norm(p) for p in allow}), "deny": sorted({norm(p) for p in deny})}


# ---------------------------------------------------------------- providers: (item, asset) -> acl | None (unknown)

def manifest_acl(item: dict | None, asset: dict) -> dict | None:
    """Permissions exported with the item (manifest key `acl`: {allow, deny, version}); CSV/CRM imports use this."""
    acl = corpus.meta(item).get("acl") if item else None
    if not acl:
        return None
    out = normalize_acl(acl.get("allow", []), acl.get("deny", []))
    return {**out, "version": str(acl["version"])} if acl.get("version") else out


class SharePointAcl:
    """Item permissions from Microsoft Graph (`/drives/{drive}/items/{item}/permissions`); the item manifest carries
    `drive_id` and `sp_item_id`. `http` is the read-only GraphHttp transport (a recorded fake in tests)."""

    def __init__(self, http):
        self.http = http

    def __call__(self, item: dict | None, asset: dict) -> dict | None:
        meta = corpus.meta(item) if item else {}
        if not meta.get("drive_id") or not meta.get("sp_item_id"):
            return manifest_acl(item, asset)
        try:
            body = json.loads(self.http.get(f"{GRAPH}/drives/{meta['drive_id']}/items/{meta['sp_item_id']}/permissions"))
        except Exception as e:  # any transport error: keep the previous ACL and flag it stale
            raise AclUnavailable(f"{type(e).__name__}: {e}") from None
        allow = []
        for p in body.get("value", []):
            if not set(p.get("roles", [])) & {"read", "write", "owner"}:
                continue
            for g in [p.get("grantedToV2") or {}] + list(p.get("grantedToIdentitiesV2") or []):
                if group := (g.get("group") or g.get("siteGroup")):
                    allow.append(f"group:{group.get('displayName')}")
                elif usr := g.get("user"):
                    allow.append(f"user:{usr.get('email') or usr.get('displayName')}")
        return normalize_acl(allow) if allow else None


def fileshare_acl(item: dict | None, asset: dict) -> dict | None:
    """Local or mounted share: POSIX group read bit -> the file's group; owner only when no group grant exists.
    shortcut: SMB/NTFS ACLs are not read (unknown); add an smbprotocol security-descriptor reader when needed."""
    p = Path(item["content_ref"]) if item else None
    if p is None or not p.exists():
        return manifest_acl(item, asset)
    st, allow = p.stat(), []
    if st.st_mode & stat.S_IRGRP:
        allow.append(f"group:{grp.getgrgid(st.st_gid).gr_name}")
    if st.st_mode & stat.S_IROTH:
        allow.append("group:everyone")
    if not allow and st.st_mode & stat.S_IRUSR:
        allow.append(f"user:{pwd.getpwuid(st.st_uid).pw_name}")
    return {**normalize_acl(allow), "version": str(st.st_ctime_ns)} if allow else None


class SqlAcl:
    """Table grants (SELECT) from Postgres `information_schema.role_table_grants` as `role:<grantee>`; other
    databases expose no ACL (unknown). `engine` is the read-only SQLAlchemy engine of the inventory connector."""

    def __init__(self, engine):
        self.engine = engine

    def __call__(self, item: dict | None, asset: dict) -> dict | None:
        if self.engine.dialect.name != "postgresql":
            return manifest_acl(item, asset)
        from sqlalchemy import text

        schema, table = asset["asset_uri"].rstrip("/").rsplit("/", 2)[-2:]
        try:
            with self.engine.connect() as c:
                rows = c.execute(text("SELECT grantee FROM information_schema.role_table_grants WHERE table_schema"
                                      " = :s AND table_name = :t AND privilege_type = 'SELECT'"),
                                 {"s": schema, "t": table}).scalars().all()
        except Exception as e:
            raise AclUnavailable(f"{type(e).__name__}: {e}") from None
        return normalize_acl([f"role:{g}" for g in rows]) if rows else None


def provider_for(src: dict, http=None):
    """The ACL provider of one registered inventory source; credentials resolved in memory only."""
    from konmari.inventory.connectors import GraphHttp, Sql, resolve_secret

    kind = src["connector"]
    if kind == "sharepoint":
        secret = resolve_secret(src.get("credential_ref"))
        return SharePointAcl(http or GraphHttp(secret)) if (http or secret) else manifest_acl
    if kind == "fileshare":
        return fileshare_acl
    if kind == "sql":
        return SqlAcl(Sql(src["locator"], resolve_secret(src.get("credential_ref"))).engine)
    return manifest_acl


# ---------------------------------------------------------------- propagation

def _service(doc: dict | None) -> list[str]:
    return [f"service:{a}" for a in (doc or {}).get("service_principals") or []]


def propagate(cat: Catalog, item_id: str) -> None:
    """Copy the item ACL to all its chunks; apply it (and revocations) to the item's agent-index rows."""
    it = cat.one("SELECT * FROM corpus_item WHERE item_id = ?", (item_id,))
    if it is None:
        return
    allow, deny = list(it["allowed_principals"] or []), list(it["denied_principals"] or [])
    cat.execute("UPDATE chunk SET allowed_principals = ?, denied_principals = ?, acl_version = ?, acl_status = ?"
                " WHERE item_id = ?", (allow, deny, it["acl_version"], it["acl_status"], item_id))
    if not allow:
        cat.execute("DELETE FROM agent_index WHERE item_id = ?", (item_id,))
        return
    for r in cat.query("SELECT chunk_id, category_id FROM agent_index WHERE item_id = ?", (item_id,)):
        p = pol.active(cat, r["category_id"])
        cat.execute("UPDATE agent_index SET allowed_principals = ?, denied_principals = ?, acl_version = ?"
                    " WHERE chunk_id = ?", (sorted(set(allow) | set(_service(p and p["doc"]))), deny,
                                            it["acl_version"], r["chunk_id"]))


def propagate_records(cat: Catalog, entity: str) -> None:
    """Golden records (c05) take the explicit principals of the approved policy that lists their entity."""
    for pid in {r["policy_id"] for r in cat.query("SELECT policy_id FROM source_policy WHERE status = 'approved'")}:
        doc = pol.active(cat, pid)["doc"]
        if entity in (doc.get("entities") or []) and doc.get("principals"):
            acl = normalize_acl(doc["principals"]["allow"], doc["principals"].get("deny", []))
            cat.execute("UPDATE golden_attribute SET allowed_principals = ?, denied_principals = ?, acl_version = ?,"
                        " acl_status = 'policy' WHERE entity = ?", (acl["allow"], acl["deny"], f"{pid}:v"
                                                                     f"{pol.active(cat, pid)['version']}", entity))
            return
    cat.execute("UPDATE golden_attribute SET allowed_principals = ?, denied_principals = ?, acl_version = NULL,"
                " acl_status = 'unknown' WHERE entity = ?", ([], [], entity))


def require_acl(row: dict, source: str | None) -> None:
    """No agent-index entry without ACL fields: the insert fails and the chunk and source are logged."""
    if not row.get("allowed_principals"):
        msg = f"index insert refused: chunk {row['chunk_id']} from source {source or row.get('item_id')} has no ACL"
        log.error(msg)
        raise AclMissing(msg)


# ---------------------------------------------------------------- sync

def items_of(cat: Catalog, sc: Scope, category: str) -> list[dict]:
    return [i for i in cat.query("SELECT * FROM corpus_item WHERE charter_id = ? AND state = 'active' ORDER BY"
                                 " item_id", (sc.charter_id,)) if corpus.category(cat, i) == category]


def last_ok(cat: Catalog, pid: str) -> dict | None:
    return cat.one("SELECT * FROM acl_sync WHERE policy_id = ? AND status = 'ok' ORDER BY started_at DESC LIMIT 1",
                   (pid,))


def cadence(cat: Catalog, pid: str):
    p = pol.active(cat, pid) or pol.latest(cat, pid)
    return pol.duration((p["doc"] if p else pol.DEFAULT)["acl_sync_cadence"])


def stale(cat: Catalog, pid: str, at=None) -> bool:
    """The last successful sync is older than the policy cadence (or there was none)."""
    last = last_ok(cat, pid)
    return last is None or (at or now()) - last["started_at"] > cadence(cat, pid)


def _connector(cat: Catalog, asset_uri: str) -> str | None:
    r = cat.one("SELECT s.connector FROM source_asset a JOIN inventory_source s ON s.source_id = a.source_id"
                " WHERE a.asset_uri = ?", (asset_uri,))
    return r and r["connector"]


def sync(cat: Catalog, sc: Scope, actor: str, providers: dict | None = None, force: bool = False,
         at=None, only: str | None = None) -> dict:
    """Re-read ACLs per category when its cadence is due; apply them and every revocation to all derived rows."""
    providers, at, rep = providers or {}, at or now(), {}
    for pid in [only] if only else sc.categories:
        last = last_ok(cat, pid)
        if not force and last and at - last["started_at"] < cadence(cat, pid):
            rep[pid] = {"skipped": "not due"}
            continue
        p = pol.active(cat, pid)
        doc = p["doc"] if p else {}
        try:
            found = []
            for it in items_of(cat, sc, pid):
                asset = cat.one("SELECT * FROM source_asset WHERE asset_uri = ?", (it["asset_uri"],)) or {}
                fn = providers.get(_connector(cat, it["asset_uri"]), providers.get("*", manifest_acl))
                found.append((it, fn(it, asset)))
        except AclUnavailable as e:
            cat.execute("INSERT INTO acl_sync VALUES (?, ?, ?, 'failed', NULL, 0, 0, 0, ?)",
                        (new_id(), pid, at, str(e)))
            write_event(cat, "govern.acl_sync", actor=actor, result="failed", charter_id=sc.charter_id, policy=pid,
                        error=str(e))
            rep[pid] = {"failed": str(e)}
            continue
        unknown = revoked = 0
        for it, acl in found:
            status = "known"
            if acl is None and doc.get("principals"):
                acl, status = {**normalize_acl(doc["principals"]["allow"], doc["principals"].get("deny", [])),
                               "version": f"policy:v{p['version']}"}, "policy"
            if acl is None:
                acl, status = {"allow": [], "deny": [], "version": None}, "unknown"
                unknown += 1
            removed = sorted(set(it["allowed_principals"] or []) - set(acl["allow"]))
            denied = sorted(set(acl["deny"]) - set(it["denied_principals"] or []))
            if it["acl_status"] is not None and (removed or denied):
                revoked += 1
                write_event(cat, "govern.acl_revoke", actor=actor, charter_id=sc.charter_id, policy=pid,
                            item_id=it["item_id"], removed=removed, denied=denied)
            cat.execute("UPDATE corpus_item SET allowed_principals = ?, denied_principals = ?, acl_version = ?,"
                        " acl_status = ? WHERE item_id = ?", (acl["allow"], acl["deny"],
                                                              acl.get("version") or at.isoformat(), status,
                                                              it["item_id"]))
            propagate(cat, it["item_id"])
        lag = (at - last["started_at"]).total_seconds() if last else None
        cat.execute("INSERT INTO acl_sync VALUES (?, ?, ?, 'ok', ?, ?, ?, ?, NULL)",
                    (new_id(), pid, at, lag, len(found), unknown, revoked))
        write_event(cat, "govern.acl_sync", actor=actor, charter_id=sc.charter_id, policy=pid, items=len(found),
                    unknown=unknown, revocations=revoked, lag_seconds=lag)
        rep[pid] = {"items": len(found), "unknown": unknown, "revocations": revoked, "lag_seconds": lag}
    return rep
