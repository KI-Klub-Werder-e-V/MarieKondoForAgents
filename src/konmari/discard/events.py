"""Decision log writer for the discard step and OpenLineage-style events for items that leave the corpus."""

import json

from konmari.catalog import Catalog, audit, now
from konmari.charter.store import Scope
from konmari.inventory.lineage import PRODUCER, SPEC, namespace

STEP = "discard"
FACET = "https://github.com/KI-Klub-Werder-e-V/MarieKondoFuerAgenten/facets/DiscardDatasetFacet.json"


def log(cat: Catalog, sc: Scope, event: str, item_id, prior_state, new_state, actor: str, result="recorded",
        **details) -> None:
    """One append-only, hash-chained event in audit_event (visible as decision_event)."""
    audit(cat, f"discard.{event}", actor=actor, charter_id=sc.charter_id, charter_version=sc.charter_version,
          step=STEP, result=result,
          details={"item_id": item_id, "prior_state": prior_state, "new_state": new_state, **details})


def history(cat: Catalog, item_id: str) -> list[dict]:
    out = []
    for r in cat.query("SELECT * FROM decision_event WHERE seq IS NOT NULL ORDER BY seq"):
        d = json.loads(r["details"] or "{}")
        if d.get("item_id") == item_id:
            out.append(r | {"details": d})
    return out


def lineage_event(item: dict, new_state: str, proposal_id: str | None, artifacts: list[str]) -> dict:
    return {"eventTime": now().isoformat() + "Z", "producer": PRODUCER, "schemaURL": SPEC,
            "dataset": {"namespace": namespace(item["asset_uri"]), "name": item["item_id"],
                        "facets": {"konmari_discard": {"_producer": PRODUCER, "_schemaURL": FACET,
                                                       "previous_state": item["state"], "state": new_state,
                                                       "proposal_id": proposal_id,
                                                       "derived_artifacts_marked": artifacts}}}}
