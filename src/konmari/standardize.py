"""Shared helpers for KonMari step 'standardize' (c05): audit, metrics, masking and OpenLineage run events."""

import json
import os
import uuid
from pathlib import Path

from konmari.catalog import Catalog, audit_change, new_id, now
from konmari.charter.store import Scope
from konmari.inventory.lineage import PRODUCER, default_transport

STEP = "standardize"
MASK = "***"
PERSONAL_TIERS = ("personal", "special-category")


def log(cat: Catalog, sc: Scope | None, action: str, entity: str, previous, new, actor: str, reason=None,
        result="recorded", **extra) -> None:
    """Every approval, rollback, review, quarantine and conflict decision: actor, time, before/after, reason."""
    audit_change(cat, action, entity, previous, new, actor=actor, reason=reason,
                 charter_id=sc.charter_id if sc else None, step=STEP, result=result, **extra)


def metric(cat: Catalog, kind: str, name: str, value: float, run_id=None, entity=None, field=None) -> None:
    """Metrics read by readiness-scorecard (c03) and freshness-monitoring (c09) from standardize_metric."""
    cat.execute("INSERT INTO standardize_metric VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (new_id(), run_id, kind, entity, field, name, float(value), now()))


def latest_metrics(cat: Catalog, kind: str | None = None) -> list[dict]:
    rows = cat.query("SELECT * FROM standardize_metric ORDER BY recorded_at")
    out = {}
    for r in rows:
        if kind in (None, r["kind"]):
            out[(r["kind"], r["entity"], r["field"], r["name"])] = r
    return list(out.values())


def tier_of(cat: Catalog, asset_uri: str | None) -> str | None:
    if not asset_uri:
        return None
    row = cat.one("SELECT sensitivity_tier FROM source_asset WHERE asset_uri = ?", (asset_uri,))
    return row["sensitivity_tier"] if row else None


def may_view(sc: Scope | None, viewer: str | None, owner: str) -> bool:
    # shortcut: owner or compliance role sees personal values; replace with c07 access-control once it exists
    if not viewer or sc is None:
        return False
    return viewer == owner or "compliance" in sc.stakeholders.get(viewer, {}).get("roles", [])


def mask(value, personal: bool, allowed: bool):
    return MASK if personal and not allowed and value is not None else value


def notify(owner: str, subject: str, **details) -> None:
    """Owner notification outbox (JSONL); a mail or chat adapter reads it."""
    path = Path(os.environ.get("KONMARI_NOTIFY_FILE", ".konmari/notifications.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"to": owner, "subject": subject, "at": now().isoformat(), **details}, default=str) + "\n")


def lineage_event(job: str, output: str, columns: dict[str, list[tuple[str, str, str, str]]],
                  transport=None) -> dict:
    """OpenLineage COMPLETE run event with a columnLineage facet. columns: output field -> [(source, object,
    column, subtype)], subtype TRANSFORMATION (normalization) or IDENTITY (survivorship)."""
    from openlineage.client.event_v2 import InputDataset, Job, OutputDataset, Run, RunEvent, RunState
    from openlineage.client.facet_v2 import column_lineage_dataset as cl
    from openlineage.client.serde import Serde

    inputs = sorted({(s, o) for refs in columns.values() for s, o, _, _ in refs})
    facet = cl.ColumnLineageDatasetFacet(fields={
        f: cl.Fields(inputFields=[cl.InputField(namespace=f"konmari://{s}", name=o, field=c,
                                                transformations=[cl.Transformation(type="DIRECT", subtype=sub)])
                                  for s, o, c, sub in refs])
        for f, refs in sorted(columns.items())})
    facet._producer = PRODUCER
    ev = RunEvent(eventType=RunState.COMPLETE, eventTime=now().isoformat() + "Z", producer=PRODUCER,
                  run=Run(runId=str(uuid.uuid4())), job=Job(namespace="konmari", name=job),
                  inputs=[InputDataset(namespace=f"konmari://{s}", name=o) for s, o in inputs],
                  outputs=[OutputDataset(namespace="konmari://catalog", name=output,
                                         facets={"columnLineage": facet})])
    event = Serde.to_dict(ev)
    (transport or default_transport()).emit(event)
    return event
