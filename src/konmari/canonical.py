"""YAML loading and canonical-content hashing (parsed YAML, sorted keys, JSON, SHA-256)."""

import hashlib
import json
from pathlib import Path

import yaml


class _Loader(yaml.SafeLoader):
    pass


# Keep dates as plain strings so content stays JSON-serializable and hashes are stable.
_Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def load_yaml(path_or_text) -> object:
    text = Path(path_or_text).read_text(encoding="utf-8") if isinstance(path_or_text, Path) else path_or_text
    return yaml.load(text, Loader=_Loader)


def dump_yaml(obj) -> str:
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True)


def canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_hash(obj) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()
