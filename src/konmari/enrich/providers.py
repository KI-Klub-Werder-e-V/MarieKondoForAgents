"""External systems used by enrichment, each behind a small interface with a local adapter.

- ACL capture lives in `konmari.governance.acl` (c07 access-control); re-exported here for c06 callers.
- Language models (opt-in only): `OpenAICompatible` for a self-hosted server (Ollama, vLLM, LocalAI) or an
  approved processor. Nothing here is called in the default profile.
"""

import json
from urllib.request import Request, urlopen

from konmari.governance.acl import AclUnavailable, SharePointAcl, manifest_acl, normalize_acl  # noqa: F401


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
