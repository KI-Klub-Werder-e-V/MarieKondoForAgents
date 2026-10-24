"""Run report: counts per flag type and method, cluster sizes, duplicate rate, calibration status, warnings."""

import json
from collections import Counter
from pathlib import Path

from konmari.catalog import Catalog
from konmari.charter.store import Scope


def build(cat: Catalog, sc: Scope, run_id: str, stats: dict, clusters: list[dict], flags: list[dict],
          mismatches: list[dict]) -> dict:
    from konmari.discard.calibrate import describe

    non_canonical = {m for c in clusters for m in c["members"] if m != c["canonical"]}
    scanned = stats["items_scanned"]
    return {"run_id": run_id, "charter_id": sc.charter_id, "charter_version": sc.charter_version,
            "items_scanned": scanned, "outside_inventory": stats["outside_inventory"],
            "rejected_candidates": stats["rejected_candidates"], "non_canonical_items": len(non_canonical),
            "duplicate_rate": round(len(non_canonical) / scanned, 4) if scanned else 0.0,
            "flags_by_type": dict(sorted(Counter(f["flag_type"] for f in flags).items())),
            "flags_by_method": dict(sorted(Counter(f["method"] for f in flags).items())),
            "cluster_sizes": {fam: dict(sorted(Counter(len(c["members"]) for c in clusters
                                                       if c["family"] == fam).items()))
                              for fam in ("exact", "lexical", "semantic")},
            "calibration": describe(cat, sc.charter_id), "inventory_mismatches": mismatches}


def latest(cat: Catalog, charter_id: str, run_id: str | None = None) -> dict | None:
    row = cat.one("SELECT report FROM discard_run WHERE run_id = ?", (run_id,)) if run_id else cat.one(
        "SELECT report FROM discard_run WHERE charter_id = ? AND finished_at IS NOT NULL ORDER BY started_at DESC"
        " LIMIT 1", (charter_id,))
    return json.loads(row["report"]) if row and row["report"] else None


def render_md(rep: dict, warnings: list[str] = ()) -> str:
    lines = [f"# Discard run {rep['run_id']}", "",
             f"Charter {rep['charter_id']} v{rep['charter_version']}. Items scanned: {rep['items_scanned']}"
             f" (manifest entries outside the inventory, skipped: {rep['outside_inventory']}).", "",
             f"**Duplicate rate: {rep['duplicate_rate'] * 100:.1f}%** ({rep['non_canonical_items']} items in"
             f" non-canonical cluster positions / {rep['items_scanned']} items scanned).", ""]
    lines += [f"WARNING: {w}" for w in warnings] + ([""] if warnings else [])
    lines += ["## Flags by type", ""] + [f"- {k}: {v}" for k, v in rep["flags_by_type"].items()] + [""]
    lines += ["## Flags by method", ""] + [f"- {k}: {v}" for k, v in rep["flags_by_method"].items()]
    lines += [f"- rejected LSH candidates (verified below the threshold): {rep['rejected_candidates']}", ""]
    lines += ["## Cluster sizes", ""]
    for fam, sizes in rep["cluster_sizes"].items():
        lines.append(f"- {fam}: " + (", ".join(f"{n} cluster(s) of {s}" for s, n in sizes.items()) or "none"))
    lines += ["", "## Calibration", ""] + [f"- {d}: {s}" for d, s in rep["calibration"].items()]
    if rep.get("categories_waiting"):
        lines += ["", "## Categories waiting for their turn (no proposals yet)", ""]
        lines += [f"- {c}: {'; '.join(r)}" for c, r in rep["categories_waiting"].items()]
    if rep["inventory_mismatches"]:
        lines += ["", "## Inventory sensitivity mismatches", ""]
        lines += [f"- {m['item_id']}: inventory tier {m['inventory_tier']}, found "
                  + ", ".join(f"{t} x{n}" for t, n in m["entities"].items()) for m in rep["inventory_mismatches"]]
    return "\n".join(lines) + "\n"


def write(rep: dict, out_dir: str | Path, warnings: list[str] = ()) -> tuple[Path, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    md, js = d / f"discard-{rep['run_id']}.md", d / f"discard-{rep['run_id']}.json"
    md.write_text(render_md(rep, warnings), encoding="utf-8")
    js.write_text(json.dumps(rep | {"warnings": list(warnings)}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return md, js
