"""External systems used by enrichment, each behind a small interface with a local adapter.

- ACL capture: `manifest_acl` (permissions exported with the item manifest) and `SharePointAcl` (Graph, read-only).
  shortcut: c07 access-control owns ACL capture and principal normalization; move these adapters there with c07.
- Language models (opt-in only): `OpenAICompatible` for a self-hosted server (Ollama, vLLM, LocalAI) or an
  approved processor. Nothing here is called in the default profile.
"""

import json
from urllib.request import Request, urlopen

from konmari.inventory.connectors import GRAPH


class AclUnavailable(Exception):
    """The source could not be asked for permissions in this run (connector failure)."""


def normalize_acl(allow, deny=()) -> dict:
    """Normalized principals: '<type>:<name>' with type user|group|role; emails lower-cased."""
    def norm(p):
        kind, _, name = str(p).partition(":")
        if not name:
            kind, name = "group", kind
        return f"{kind.strip().lower()}:{name.strip().lower() if kind == 'user' else name.strip()}"
    return {"allow": sorted({norm(p) for p in allow}), "deny": sorted({norm(p) for p in deny})}


def manifest_acl(item: dict | None, asset: dict) -> dict | None:
    """Permissions exported with the item (manifest key `acl`: {allow: [...], deny: [...]}). None = unknown."""
    acl = (item or {}).get("meta", {}).get("acl")
    if not acl:
        return None
    return normalize_acl(acl.get("allow", []), acl.get("deny", []))


class SharePointAcl:
    """Item permissions from Microsoft Graph (`/drives/{drive}/items/{item}/permissions`); the item manifest carries
    `drive_id` and `sp_item_id`. `http` is the read-only GraphHttp transport (a recorded fake in tests)."""

    def __init__(self, http):
        self.http = http

    def __call__(self, item: dict | None, asset: dict) -> dict | None:
        meta = (item or {}).get("meta", {})
        if not meta.get("drive_id") or not meta.get("sp_item_id"):
            return manifest_acl(item, asset)
        try:
            body = json.loads(self.http.get(f"{GRAPH}/drives/{meta['drive_id']}/items/{meta['sp_item_id']}/permissions"))
        except Exception as e:  # any transport error: keep the previous tags and flag them acl_stale
            raise AclUnavailable(f"{type(e).__name__}: {e}") from None
        allow = []
        for p in body.get("value", []):
            if not set(p.get("roles", [])) & {"read", "write", "owner"}:
                continue
            for g in [p.get("grantedToV2") or {}] + list(p.get("grantedToIdentitiesV2") or []):
                if grp := (g.get("group") or g.get("siteGroup")):
                    allow.append(f"group:{grp.get('displayName')}")
                elif usr := g.get("user"):
                    allow.append(f"user:{usr.get('email') or usr.get('displayName')}")
        return normalize_acl(allow) if allow else None


class ContextModel:
    """Interface for language models: `name`, `self_hosted`, `processor` and `complete(prompt) -> str`."""
    name = "none"
    self_hosted = True
    processor = None

    def complete(self, prompt: str) -> str:
        raise NotImplementedError


class OpenAICompatible(ContextModel):
    """POST {url}/v1/chat/completions. `processor` names the external processor when the server is not ours."""

    def __init__(self, url: str, model: str, processor: str | None = None):
        self.url, self.name, self.processor = url.rstrip("/"), model, processor
        self.self_hosted = processor is None

    def complete(self, prompt: str) -> str:
        body = json.dumps({"model": self.name, "messages": [{"role": "user", "content": prompt}],
                           "temperature": 0}).encode()
        req = Request(f"{self.url}/v1/chat/completions", data=body, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=120) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"].strip()


def model_refusal(model: ContextModel, approved_processors: list[str]) -> str | None:
    """Why this model may not receive pilot content (None: it may). DSGVO Art. 28/44: self-hosted or approved."""
    if model.self_hosted:
        return None
    if model.processor and model.processor in approved_processors:
        return None
    return (f"model '{model.name}' is neither self-hosted nor run by an approved processor "
            f"(approved: {', '.join(approved_processors) or 'none'})")
