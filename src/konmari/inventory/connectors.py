"""Read-only source connectors. Each one returns the full list of container assets of one source.

Granularity is the container (document library, folder subtree, table/view), never the single file
(design decision 4). Incremental state (delta links, file mtime/size, schema hashes) lives in the
source's `state` JSON so unchanged containers are not re-read or re-sampled.
"""

import base64
import hashlib
import json
import mimetypes
import ntpath
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from urllib.request import Request, urlopen

from konmari.canonical import canonical_json
from konmari.versions import Refused

GRAPH = "https://graph.microsoft.com/v1.0"
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/x-yaml", "application/yaml")
SAMPLE_BYTES = 1_000_000  # per sampled item


@dataclass
class Asset:
    uri: str
    title: str
    system_type: str
    format: str | None = None
    size: int | None = None
    last_modified: datetime | None = None
    path: str = ""
    content_hash: str | None = None
    schema_hash: str | None = None
    fields: list[dict] | None = None
    changed: bool = True                    # False: unchanged since the last scan, skip sampling
    samples: Callable[[int], Iterable[str]] | None = None
    meta: dict = field(default_factory=dict)  # manual import: owner, steward, ... as given by a human


def resolve_secret(ref: str | None) -> str | None:
    """Resolve a secret reference at run time. Only `env:NAME` and `file:PATH` exist; values are never stored."""
    if not ref:
        return None
    kind, _, name = ref.partition(":")
    if kind == "env" and name:
        if name not in os.environ:
            raise Refused(f"secret reference '{ref}': environment variable {name} is not set")
        return os.environ[name]
    if kind == "file" and name:
        try:
            return Path(name).read_text(encoding="utf-8").strip()
        except OSError:
            raise Refused(f"secret reference '{ref}': file cannot be read") from None
    # never echo the reference: a pasted secret value would end up in logs
    raise Refused("unsupported secret reference; use env:NAME or file:PATH (the value is not shown)")


def _dominant(types) -> str | None:
    c = Counter(t for t in types if t)
    return c.most_common(1)[0][0] if c else None


def _agg_hash(hashes) -> str | None:
    hs = sorted(h for h in hashes if h)
    return hashlib.sha256("\n".join(hs).encode()).hexdigest() if hs else None


def _is_text(mime: str | None) -> bool:
    return bool(mime) and mime.startswith(TEXT_TYPES)


# ---------------------------------------------------------------- SharePoint / M365 (Graph, Sites.Selected)

FORBIDDEN_ROLE_HINT = "replace it with the application permission Sites.Selected plus a per-site grant"


def token_roles(token: str) -> list[str]:
    try:
        payload = token.split(".")[1]
        return list(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("roles", []))
    except (IndexError, ValueError):
        raise Refused("SharePoint token is not a JWT access token") from None


def check_token(token: str) -> None:
    """Abort before any site request when the token is broader than Sites.Selected (design decision 3)."""
    roles = token_roles(token)
    bad = [r for r in roles if r in ("Sites.Read.All", "Sites.Manage.All", "Sites.FullControl.All")
           or r.endswith(".ReadWrite.All")]
    if bad:
        raise Refused(f"SharePoint token has tenant-wide permission {', '.join(bad)}; {FORBIDDEN_ROLE_HINT}")
    if "Sites.Selected" not in roles:
        raise Refused(f"SharePoint token lacks Sites.Selected; {FORBIDDEN_ROLE_HINT}")


class GraphHttp:
    """The only Graph transport: GET requests with a bearer token. No other HTTP method exists here."""

    def __init__(self, token: str):
        self._auth = {"Authorization": f"Bearer {token}"}

    def get(self, url: str) -> bytes:
        if "/search" in url.lower():
            raise Refused("Graph Search is forbidden: it bypasses Sites.Selected scoping")
        with urlopen(Request(url, headers=self._auth, method="GET"), timeout=60) as r:
            return r.read()


def _graph_time(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None) if s else None


class SharePoint:
    """Locator: `<host>/sites/<name>`, e.g. contoso.sharepoint.com/sites/support. One asset per document library."""

    def __init__(self, locator: str, token: str, http=None):
        check_token(token)
        self.locator, self.http = locator.strip("/"), http or GraphHttp(token)

    def _json(self, url: str) -> dict:
        return json.loads(self.http.get(url))

    def scan(self, state: dict) -> tuple[list[Asset], dict]:
        host, _, path = self.locator.partition("/")
        site = self._json(f"{GRAPH}/sites/{host}:/{path}")
        out, new_state = [], {}
        for d in self._json(f"{GRAPH}/sites/{site['id']}/drives")["value"]:
            # shortcut: item-level state is kept in the source state JSON; move it to a table if a library exceeds ~50k items
            prev = state.get(d["id"], {})
            items, link, root_deleted, changed = dict(prev.get("items", {})), prev.get("delta"), False, False
            link = link or f"{GRAPH}/drives/{d['id']}/root/delta"
            delta = None
            while link:
                page = self._json(link)
                for it in page.get("value", []):
                    if "deleted" in it:
                        root_deleted |= "root" in it
                        changed |= items.pop(it["id"], None) is not None
                    elif "file" in it:
                        items[it["id"]] = {"mime": it["file"].get("mimeType"), "modified": it.get("lastModifiedDateTime"),
                                           "sha256": ((it["file"].get("hashes") or {}).get("sha256Hash") or "").lower() or None}
                        changed = True
                link, delta = page.get("@odata.nextLink"), page.get("@odata.deltaLink", delta)
            if root_deleted:
                continue  # library deleted: not returned, so the scanner marks it missing
            new_state[d["id"]] = {"items": items, "delta": delta}
            mods = [m for m in (_graph_time(i["modified"]) for i in items.values()) if m]
            out.append(Asset(
                uri=f"sharepoint://{host}/drives/{d['id']}", title=d["name"], system_type="sharepoint",
                format=_dominant(i["mime"] for i in items.values()), size=len(items),
                last_modified=max(mods) if mods else None, path=d.get("webUrl", ""),
                content_hash=_agg_hash(i["sha256"] for i in items.values()), changed=changed or not prev,
                samples=self._sampler(d["id"], items)))
        return out, new_state

    def _sampler(self, drive_id, items):
        def samples(n):
            for iid in [k for k, v in items.items() if _is_text(v["mime"])][:n]:
                yield self.http.get(f"{GRAPH}/drives/{drive_id}/items/{iid}/content")[:SAMPLE_BYTES].decode(
                    "utf-8", "ignore")
        return samples


# ---------------------------------------------------------------- SQL warehouses and CRM databases

_READ_ONLY = ("select", "show", "with", "pragma", "explain", "set transaction read only")


def _guard_read_only(conn, cursor, statement, *args):
    if not statement.lstrip().lower().startswith(_READ_ONLY):
        raise Refused(f"SQL connector is read-only; refused statement: {statement.split()[0]}")


class Sql:
    """Locator: an SQLAlchemy URL without password, e.g. postgresql+psycopg://reader@db.example.org/crm."""

    SKIP_SCHEMAS = {"information_schema", "pg_catalog", "pg_toast"}

    def __init__(self, locator: str, password: str | None = None):
        from sqlalchemy import create_engine, event
        from sqlalchemy.engine import make_url
        from sqlalchemy.pool import NullPool

        url = make_url(locator)
        if url.password:
            raise Refused("the SQL locator contains a password; put it in a secret reference (--secret-ref)")
        if password:
            url = url.set(password=password)
        self.url = url
        args = {"options": "-c default_transaction_read_only=on"} if url.get_backend_name() == "postgresql" else {}
        self.engine = create_engine(url, connect_args=args, poolclass=NullPool)
        event.listen(self.engine, "before_cursor_execute", _guard_read_only)

    def scan(self, state: dict) -> tuple[list[Asset], dict]:
        from sqlalchemy import inspect

        u = self.url
        db = Path(u.database).stem if u.get_backend_name() in ("sqlite", "duckdb") else u.database
        insp, out, new_state = inspect(self.engine), [], {}
        for schema in insp.get_schema_names():
            if schema in self.SKIP_SCHEMAS:
                continue
            for name, is_view in [(t, False) for t in insp.get_table_names(schema)] + \
                                 [(v, True) for v in insp.get_view_names(schema)]:
                fields = [{"name": c["name"], "type": str(c["type"])} for c in insp.get_columns(name, schema)]
                h = hashlib.sha256(canonical_json(fields).encode()).hexdigest()
                uri = f"sql://{u.host or 'localhost'}/{db}/{schema}/{name}"
                new_state[uri] = h
                out.append(Asset(uri=uri, title=f"{db}.{schema}.{name}", system_type="sql", format="table",
                                 size=None if is_view else self._rows(schema, name), path=f"{schema}.{name}",
                                 schema_hash=h, fields=fields, changed=state.get(uri) != h,
                                 samples=self._sampler(schema, name)))
        return out, new_state

    def _rows(self, schema, name) -> int:
        from sqlalchemy import func, select, table, text

        with self.engine.connect() as c:
            if self.engine.dialect.name == "postgresql":
                est = c.execute(text("SELECT c.reltuples::bigint FROM pg_class c JOIN pg_namespace n ON n.oid ="
                                     " c.relnamespace WHERE n.nspname = :s AND c.relname = :t"),
                                {"s": schema, "t": name}).scalar()
                if est is not None and est >= 0:
                    return int(est)
            return c.execute(select(func.count()).select_from(table(name, schema=schema))).scalar()

    def _sampler(self, schema, name):
        def samples(n):
            from sqlalchemy import select, table, text

            with self.engine.connect() as c:
                for row in c.execute(select(text("*")).select_from(table(name, schema=schema)).limit(n)):
                    yield " ".join("" if v is None else str(v) for v in row)
        return samples


# ---------------------------------------------------------------- file shares (SMB or local path)

class FileShare:
    """Locator: a local path, or smb://server/share/path / \\\\server\\share\\path (needs the `smb` extra).
    One asset per top-level folder subtree, plus one for files directly in the root."""

    def __init__(self, locator: str, username: str | None = None, password: str | None = None):
        self.locator = locator
        if locator.startswith(("smb://", "\\\\")):
            try:
                import smbclient  # optional extra: pip install konmari[smb]
            except ImportError:
                raise ValueError(f"{locator}: SMB shares need the smb extra (uv sync --extra smb)") from None

            self.root = "\\\\" + locator.removeprefix("smb://").lstrip("\\").replace("/", "\\")
            server = self.root.lstrip("\\").split("\\")[0]
            smbclient.register_session(server, username=username, password=password)
            self._scandir, self._open, self._join = smbclient.scandir, smbclient.open_file, ntpath.join
            self.scheme = "smb:"
        else:
            self.root = str(Path(locator).resolve())
            self._scandir, self._open, self._join, self.scheme = os.scandir, open, os.path.join, "file:"

    def _walk(self, top):
        for e in self._scandir(top):
            if e.is_dir():
                yield from self._walk(self._join(top, e.name))
            elif e.is_file():
                st = e.stat()
                yield self._join(top, e.name), st.st_mtime, st.st_size

    def _sha(self, p) -> str:
        h = hashlib.sha256()
        with self._open(p, mode="rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                h.update(block)
        return h.hexdigest()

    def scan(self, state: dict) -> tuple[list[Asset], dict]:
        groups: dict[str, list] = {}
        for e in self._scandir(self.root):
            if e.is_dir():
                groups[e.name] = list(self._walk(self._join(self.root, e.name)))
            elif e.is_file():
                st = e.stat()
                groups.setdefault("", []).append((self._join(self.root, e.name), st.st_mtime, st.st_size))
        out, new_state = [], {}
        for name, files in sorted(groups.items()):
            changed = set(state.get(name, {})) != {p for p, _, _ in files}
            entries = {}
            for p, mtime, size in files:
                old = state.get(name, {}).get(p)
                if old and old[0] == mtime and old[1] == size:
                    entries[p] = old
                else:
                    entries[p], changed = [mtime, size, self._sha(p)], True
            new_state[name] = entries
            mimes = {p: mimetypes.guess_type(p)[0] for p in entries}
            path = self._join(self.root, name) if name else self.root
            uri = "smb:" + path.replace("\\", "/") if self.scheme == "smb:" else f"file://{path}"
            out.append(Asset(
                uri=uri, title=name or Path(self.root).name, system_type="fileshare",
                format=_dominant(mimes.values()), size=len(entries),
                last_modified=datetime.fromtimestamp(max(e[0] for e in entries.values()), timezone.utc).replace(
                    tzinfo=None) if entries else None,
                path=path, content_hash=_agg_hash(e[2] for e in entries.values()), changed=changed,
                samples=self._sampler([p for p in sorted(entries) if _is_text(mimes[p])])))
        return out, new_state

    def _sampler(self, paths):
        def samples(n):
            for p in paths[:n]:
                with self._open(p, mode="rb") as f:
                    yield f.read(SAMPLE_BYTES).decode("utf-8", "ignore")
        return samples


def connector_for(src: dict, http=None):
    """Build the connector for a registered source; credentials are resolved here and kept in memory only."""
    secret = resolve_secret(src.get("credential_ref"))
    kind = src["connector"]
    if kind == "sharepoint":
        if not secret:
            raise Refused("the SharePoint connector needs --secret-ref pointing to a Sites.Selected access token")
        return SharePoint(src["locator"], secret, http)
    if kind == "sql":
        return Sql(src["locator"], secret)
    if kind == "fileshare":
        user, pw = secret.split(":", 1) if secret and ":" in secret else (None, secret)  # secret = user:password
        return FileShare(src["locator"], user, pw)
    raise Refused(f"connector '{kind}' has no scanner; use 'konmari inventory import'")
