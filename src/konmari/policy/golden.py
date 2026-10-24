"""Golden records: apply the active policy to the canonical values of each entity cluster, with per-attribute
provenance, conflict detection, OpenLineage columnLineage and the agent-facing read contract."""

from konmari.catalog import Catalog, now
from konmari.charter.store import Scope
from konmari.normalize.store import current_values
from konmari.policy import conflicts as cf
from konmari.policy.store import active_policy, coverage
from konmari.resolve.linkage import crosswalk, published
from konmari.standardize import lineage_event, log, metric
from konmari.versions import Refused


def _rank(strategy: str, cands: list[dict], rule: dict) -> list[dict]:
    """The candidates a strategy cannot tell apart (all of them for owner_decision)."""
    if strategy == "source_priority":
        order = rule["sources"]
        key = lambda c: -(order.index(c["source"]) if c["source"] in order else len(order))  # noqa: E731
    elif strategy == "most_recent":
        if not any(c["ts"] for c in cands):
            return cands
        key = lambda c: c["ts"] or ""  # noqa: E731
    elif strategy == "most_complete":
        key = lambda c: c["complete"]  # noqa: E731
    else:
        return cands
    best = max(key(c) for c in cands)
    return [c for c in cands if key(c) == best]


def _distinct(cands):
    return {(c["value"], c["unit"]) for c in cands}


def decide(rule: dict, cands: list[dict]) -> tuple[dict | None, str, bool]:
    """(winner or None, rule label, disagreement). Values equal after normalization are no disagreement."""
    s, tb = rule["strategy"], rule["tie_breaker"]
    tied = _rank(s, cands, rule)
    if len(_distinct(cands)) == 1:
        return tied[0], s, False
    if s == "owner_decision":
        return None, s, True
    if len(_distinct(tied)) == 1:
        return tied[0], s, True
    if tb != s and tb != "owner_decision":
        tied = _rank(tb, tied, rule)
        if len(_distinct(tied)) == 1:
            return tied[0], f"{s}>{tb}", True
    return None, f"{s}>{tb}", True


def compute(cat: Catalog, entity: str, doc: dict) -> list[dict]:
    """One result per (cluster, field) with candidates; pure (writes nothing)."""
    values = current_values(cat, entity)
    out = []
    for cid, members in sorted(crosswalk(cat, entity).items()):
        for field, rule in sorted(doc["rules"].items()):
            cands = []
            for m in members:
                rec = values.get(f"{m['source']}:{m['record_id']}", {})
                if field in rec:
                    ts = rec.get(rule.get("timestamp_field") or "", {}).get("canonical")
                    cands.append({"source": m["source"], "record_id": m["record_id"],
                                  "value": rec[field]["canonical"], "unit": rec[field]["unit"],
                                  "source_field": rec[field]["source_field"], "ts": ts, "complete": len(rec)})
            if cands:
                winner, label, disagree = decide(rule, cands)
                out.append({"cluster_id": cid, "field": field, "rule": rule, "label": label, "winner": winner,
                            "disagree": disagree, "candidates": cands})
    return out


def _attributes(cat: Catalog, entity: str, results: list[dict], version: int) -> list[dict]:
    decided = {(c["cluster_id"], c["field"]): c for c in cf.entries(cat, entity)
               if c["status"] in ("resolved", "accepted_deviation")}
    rows, ts = [], now()
    for r in results:
        w, origin = r["winner"], "rule"
        if w is None and (c := decided.get((r["cluster_id"], r["field"]))):
            pick = next((x for x in r["candidates"] if x["source"] == c["decision_src"]), None)
            w = {"value": c["decision"], "unit": pick["unit"] if pick else None, "source": c["decision_src"],
                 "record_id": pick and pick["record_id"], "source_field": pick and pick["source_field"],
                 "ts": pick and pick["ts"]}
            origin = "owner_decision"
        rows.append({"entity": entity, "cluster_id": r["cluster_id"], "field": r["field"],
                     "value": w and w["value"], "unit": w and w["unit"],
                     "status": "resolved" if w else "unresolved", "source": w and w["source"],
                     "record_id": w and w["record_id"], "source_field": w and w["source_field"], "rule": r["label"],
                     "origin": origin if w else None, "policy_version": version, "source_ts": w and w["ts"],
                     "selected_at": ts})
    return rows


def impact(cat: Catalog, entity: str, doc: dict, version: int) -> list[dict]:
    """Dry run: golden values a policy version would change compared with the current golden records."""
    if not crosswalk(cat, entity):
        return []
    cur = {(g["cluster_id"], g["field"]): g for g in cat.query("SELECT * FROM golden_attribute WHERE entity = ?",
                                                                (entity,))}
    out = []
    for a in _attributes(cat, entity, compute(cat, entity, doc), version):
        old = cur.get((a["cluster_id"], a["field"]))
        if not old or (old["value"], old["status"]) != (a["value"], a["status"]):
            out.append({"cluster_id": a["cluster_id"], "field": a["field"], "from": old and old["value"],
                        "to": a["value"], "status": a["status"]})
    return out


def build(cat: Catalog, sc: Scope | None, entity: str, actor: str, exposed: dict | None = None,
          transport=None) -> dict:
    pol = active_policy(cat, entity)
    if pol is None:
        raise Refused(f"no approved policy for '{entity}'; golden records are not built without one")
    if exposed and (missing := coverage(cat, {entity: exposed.get(entity, [])})[entity]):
        raise Refused([f"exposed field {m} has no precedence rule; golden records for '{entity}' are not published"
                       for m in missing])
    if not published(cat, entity):
        raise Refused(f"entity resolution for '{entity}' is not published (konmari resolve publish)")
    results = compute(cat, entity, pol["doc"])
    cf.sync(cat, sc, entity, results, pol["doc"], actor)
    rows = _attributes(cat, entity, results, pol["version"])
    cat.execute("DELETE FROM golden_attribute WHERE entity = ?", (entity,))
    if rows:
        cat.many(f"INSERT INTO golden_attribute ({', '.join(rows[0])}) VALUES (" + ", ".join("?" * len(rows[0])) + ")",
                 [tuple(r.values()) for r in rows])
    from konmari.governance import acl  # c07: records inherit the ACL of their source policy

    acl.propagate_records(cat, entity)
    columns = {}
    for r in rows:
        if r["source_field"]:
            src, obj, col = r["source_field"].split(".", 2)
            columns.setdefault(r["field"], set()).add((src, obj, col, "IDENTITY"))
    event = lineage_event(f"golden.{entity}", f"golden.{entity}", {f: sorted(c) for f, c in columns.items()},
                          transport)
    stats = {"clusters": len({r["cluster_id"] for r in rows}), "attributes": len(rows),
             "unresolved": sum(r["status"] == "unresolved" for r in rows)}
    metric(cat, "conflicts", "unresolved_attributes", stats["unresolved"], entity=entity)
    cf.record_metrics(cat, entity)
    log(cat, sc, "policy.golden_build", f"golden:{entity}", None, stats, actor, policy_version=pol["version"])
    return stats | {"policy_version": pol["version"], "lineage": event}


def rebuild_if_ready(cat: Catalog, sc: Scope | None, entity: str, actor: str) -> dict | None:
    """Recompute golden records that exist already; the first publication goes through `build` with the coverage
    check (konmari policy build), so an approval or split never publishes an entity whose exposed fields lack rules."""
    exists = cat.one("SELECT 1 AS x FROM golden_attribute WHERE entity = ? LIMIT 1", (entity,))
    if exists and active_policy(cat, entity) and published(cat, entity):
        return build(cat, sc, entity, actor)
    return None


def record(cat: Catalog, entity: str, cluster_id: str) -> list[dict]:
    return cat.query("SELECT * FROM golden_attribute WHERE entity = ? AND cluster_id = ? ORDER BY field",
                     (entity, cluster_id))


def serve(cat: Catalog, entity: str, cluster_id: str, field: str) -> dict:
    """Agent-facing contract for data-products / mcp-exposure (c08): value + provenance + status. An unresolved
    attribute carries no asserted value, so the agent answers 'unknown / unresolved'."""
    g = cat.one("SELECT * FROM golden_attribute WHERE entity = ? AND cluster_id = ? AND field = ?",
                (entity, cluster_id, field))
    if g is None:
        return {"entity": entity, "cluster_id": cluster_id, "field": field, "status": "unresolved", "value": None,
                "candidate_sources": [], "reason": "no golden value"}
    if g["status"] == "unresolved":
        c = next((c for c in cf.entries(cat, entity) if (c["cluster_id"], c["field"]) == (cluster_id, field)), None)
        return {"entity": entity, "cluster_id": cluster_id, "field": field, "status": "unresolved", "value": None,
                "candidate_sources": sorted({x["source"] for x in c["candidates"]}) if c else [],
                "conflict_id": c and c["conflict_id"], "conflict_status": c and c["status"]}
    return {"entity": entity, "cluster_id": cluster_id, "field": field, "status": "resolved", "value": g["value"],
            "unit": g["unit"], "source": g["source"], "record_id": g["record_id"], "rule": g["rule"],
            "origin": g["origin"], "policy_version": g["policy_version"], "source_ts": g["source_ts"],
            "selected_at": g["selected_at"]}
