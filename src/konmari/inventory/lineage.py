"""OpenLineage dataset events for inventoried assets (ownership and schema facets).

Transports are tiny on purpose: an in-memory list for tests and a JSONL file by default. Any
OpenLineage backend (Marquez, DataHub, OpenMetadata) can ingest the file.
"""

import json
import os
from pathlib import Path

from konmari.catalog import now

PRODUCER = "https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten"
SPEC = "https://openlineage.io/spec/2-0-2/OpenLineage.json#/definitions/DatasetEvent"
OWNERSHIP = "https://openlineage.io/spec/facets/1-0-1/OwnershipDatasetFacet.json"
SCHEMA = "https://openlineage.io/spec/facets/1-1-1/SchemaDatasetFacet.json"


class MemoryTransport:
    def __init__(self):
        self.events: list[dict] = []

    def emit(self, event: dict) -> None:
        self.events.append(event)


class FileTransport:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def emit(self, event: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, sort_keys=True) + "\n")


def default_transport() -> FileTransport:
    return FileTransport(os.environ.get("KONMARI_LINEAGE_FILE", ".konmari/lineage.jsonl"))


def namespace(asset_uri: str) -> str:
    scheme, _, rest = asset_uri.partition("://")
    return f"{scheme}://{rest.split('/')[0] or 'localhost'}"


def dataset_event(rec: dict) -> dict:
    facets = {"ownership": {"_producer": PRODUCER, "_schemaURL": OWNERSHIP,
                            "owners": [{"name": rec["owner"], "type": "owner"}] if rec.get("owner") else []}}
    if rec.get("schema_fields"):
        facets["schema"] = {"_producer": PRODUCER, "_schemaURL": SCHEMA,
                            "fields": json.loads(rec["schema_fields"])}
    return {"eventTime": now().isoformat() + "Z", "producer": PRODUCER, "schemaURL": SPEC,
            "dataset": {"namespace": namespace(rec["asset_uri"]), "name": rec["title"], "facets": facets}}
