"""OpenLineage run events (START, COMPLETE, FAIL) for refresh and publish (design D5): stored in the catalog and
sent to the default file transport; set KONMARI_LINEAGE_URL to also POST them to Marquez."""

import json
import os
import uuid
from urllib.request import Request, urlopen

from konmari.catalog import Catalog, new_id, now
from konmari.enrich.store import RUN_EVENT
from konmari.inventory.lineage import PRODUCER, default_transport


class HttpTransport:
    """POST each event to an OpenLineage HTTP endpoint (Marquez: <url>/api/v1/lineage)."""

    def __init__(self, url: str, urlopen_=urlopen):
        self.url, self.urlopen = url, urlopen_

    def emit(self, event: dict) -> None:
        req = Request(self.url, data=json.dumps(event).encode(), headers={"Content-Type": "application/json"})
        with self.urlopen(req, timeout=10):
            pass


def transports(transport=None) -> list:
    if transport is not None:
        return [transport]
    out = [default_transport()]
    if url := os.environ.get("KONMARI_LINEAGE_URL"):
        out.append(HttpTransport(url))
    return out


def _dataset(name: str) -> dict:
    ns, _, rest = name.partition(":")
    return {"namespace": "konmari://products", "name": name} if not rest else {"namespace": f"konmari://{ns}",
                                                                              "name": rest}


def _emit(cat: Catalog, run: dict, event_type: str, transport, error: str | None = None) -> dict:
    ev = {"eventType": event_type, "eventTime": now().isoformat() + "Z", "producer": PRODUCER,
          "schemaURL": RUN_EVENT, "run": {"runId": run["run_id"]},
          "job": {"namespace": "konmari", "name": run["job"]},
          "inputs": [_dataset(n) for n in run["inputs"]], "outputs": [_dataset(n) for n in run["outputs"]]}
    if error:
        ev["run"]["facets"] = {"errorMessage": {"_producer": PRODUCER, "message": error, "programmingLanguage": "python",
                                                "_schemaURL": "https://openlineage.io/spec/facets/1-0-1/"
                                                              "ErrorMessageRunFacet.json"}}
    cat.execute("INSERT INTO lineage_event VALUES (?, ?, ?, ?, ?, ?)",
                (new_id(), run["run_id"], run["job"], event_type, now(), json.dumps(ev, sort_keys=True)))
    for t in transports(transport):
        t.emit(ev)
    return ev


def start(cat: Catalog, job: str, inputs: list[str], outputs: list[str], transport=None) -> dict:
    """inputs/outputs: 'sources:<source>.<table>' for source datasets, '<product>@<version>' for products."""
    run = {"run_id": str(uuid.uuid4()), "job": job, "inputs": inputs, "outputs": outputs}
    _emit(cat, run, "START", transport)
    return run


def finish(cat: Catalog, run: dict, event_type: str, transport=None, error: str | None = None) -> dict:
    return _emit(cat, run, event_type, transport, error)


def events(cat: Catalog, run_id: str) -> list[dict]:
    return [json.loads(r["event"]) for r in cat.query("SELECT event FROM lineage_event WHERE run_id = ?"
                                                      " ORDER BY event_time, event_type DESC", (run_id,))]
