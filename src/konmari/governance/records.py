"""DSGVO Art. 30 record entry for the pilot agent, prefilled from the catalog (a template, not legal advice)."""

import json

from konmari.catalog import Catalog
from konmari.charter.store import Scope
from konmari.governance import policy as pol


def ropa_entry(cat: Catalog, sc: Scope, charter: dict, cfg: dict) -> dict:
    uc = (charter.get("use_cases") or [{}])[0]
    comp = charter.get("compliance", {})
    assets = cat.query("SELECT asset_uri, personal_data_categories, ropa_ref FROM source_asset WHERE charter_id = ?",
                       (sc.charter_id,))
    cats_ = sorted({c for a in assets for c in json.loads(a["personal_data_categories"] or "[]")})
    agents = cat.query("SELECT agent_id, scopes, service_principal FROM agent_client ORDER BY agent_id")
    owner = next((s for s, v in sc.stakeholders.items() if "compliance" in v.get("roles", [])), None)
    return {
        "art30_entry": {
            "processing_activity": f"AI agent pilot: {uc.get('name', charter.get('title'))}",
            "controller_contact": "<FILL: name and address of the controller>",
            "dpo_contact": sc.stakeholders.get(owner, {}).get("contact") if owner else "<FILL>",
            "purpose": comp.get("purpose_of_processing") or uc.get("purpose"),
            "legal_basis": "<FILL: Art. 6(1) letter and justification; Art. 9(2) letter for special categories>",
            "data_subjects": "<FILL: e.g. members, customers, employees>",
            "personal_data_categories": cats_ or comp.get("personal_data_categories"),
            "special_categories": [p for p in sc.categories
                                   if (pol.active(cat, p) or {}).get("doc", {}).get("legal_basis")],
            "recipients": [{"agent": a["agent_id"], "scopes": json.loads(a["scopes"]),
                            "autonomous": bool(a["service_principal"])} for a in agents]
            + ["<FILL: processors, e.g. hosting provider, model provider with DPA>"],
            "third_country_transfers": "<FILL: none, or the Art. 44 ff. safeguard>",
            "retention": {"audit_log_months": cfg["audit"]["retention_months"],
                          "audit_log_max_months": cfg["audit"]["max_retention_months"]},
            "technical_and_organisational_measures": [
                "source ACLs enforced inside retrieval (Postgres row-level security)",
                "agent and on-behalf-of identity on every request; read-only default; expiring write grants",
                "PII detection before indexing; masking or HMAC pseudonymisation; key in a separate schema",
                "append-only, hash-chained audit log without payload content",
            ],
            "existing_ropa_refs": sorted({a["ropa_ref"] for a in assets if a["ropa_ref"]}),
            "source_policies": pol.active_versions(cat),
        }
    }
