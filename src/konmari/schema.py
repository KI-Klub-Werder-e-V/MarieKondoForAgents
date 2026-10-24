"""JSON Schema validation with field-level messages."""

import json
from functools import cache
from importlib.resources import files

from jsonschema import Draft202012Validator

PLACEHOLDER = "<FILL"


@cache
def load_schema(name: str) -> dict:
    return json.loads((files("konmari") / "schemas" / f"{name}.schema.json").read_text())


def fmt_path(parts) -> str:
    out = ""
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else (f".{p}" if out else str(p))
    return out


def schema_errors(doc, name: str, custom: dict | None = None) -> list[str]:
    """Return 'path: message' strings. `custom` maps (path, validator) to a replacement message."""
    custom = custom or {}
    out = []
    for e in Draft202012Validator(load_schema(name)).iter_errors(doc):
        path = fmt_path(e.absolute_path)
        if e.validator == "required":
            field = e.message.split("'")[1]
            path = f"{path}.{field}" if path else field
            msg = "required field is missing"
        elif e.validator == "minItems" and e.validator_value == 1:
            msg = "must not be empty"
        else:
            msg = e.message
        out.append(f"{path or '(root)'}: {custom.get((path, e.validator), msg)}")
    return sorted(set(out))


def placeholder_errors(doc, path=()) -> list[str]:
    if isinstance(doc, dict):
        return [e for k, v in doc.items() for e in placeholder_errors(v, (*path, k))]
    if isinstance(doc, list):
        return [e for i, v in enumerate(doc) for e in placeholder_errors(v, (*path, i))]
    if isinstance(doc, str) and PLACEHOLDER in doc:
        return [f"{fmt_path(path)}: placeholder not filled ({doc})"]
    return []
