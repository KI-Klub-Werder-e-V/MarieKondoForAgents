"""c02 category-map: scenarios from openspec/changes/c02-category-inventory/specs/category-map/spec.md."""

import json

import pytest

from konmari.categories import StepRefused, require_step
from konmari.categories import store as cs
from konmari.charter.store import scope
from konmari.inventory import store as inv
from konmari.inventory.connectors import Asset
from konmari.versions import Refused

CID = "verein-faq-assistant"
HASH = "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08"


def sc_(cat):
    return scope(CID, cat)


def assets(cat, source, *specs, tier="internal"):
    """Create complete inventory records for one source: specs are (uri, title, system_type, extra)."""
    out = []
    for uri, title, system, *extra in specs:
        kw = extra[0] if extra else {}
        meta = {"owner": "Ben Muster", "update_frequency": "P1M", "sensitivity_tier": kw.pop("tier", tier),
                "tier_state": "confirmed"}
        out.append(Asset(uri=uri, title=title, system_type=system, format=kw.pop("format", "text/plain"),
                         changed=False, meta=meta, **kw))
    inv.apply_assets(cat, sc_(cat), {"source_id": source}, out, "dirk", origin="manual")


def audits(cat, action):
    return [json.loads(r["details"]) | {"actor": r["actor"]}
            for r in cat.query("SELECT * FROM audit_event WHERE action = ? ORDER BY ts", (action,))]


def contracts(cat):
    assets(cat, "support-site", ("sharepoint://c/drives/v", "Verträge", "sharepoint"))
    assets(cat, "office-share", ("file:///share/Vertraege", "Vertraege", "fileshare"))
    assets(cat, "crm-db", ("sql://localhost/crm/public/contract_attachments", "crm.public.contract_attachments",
                           "sql", {"format": "table", "fields": [{"name": "id", "type": "INTEGER"}]}))
    return ["sharepoint://c/drives/v", "file:///share/Vertraege", "sql://localhost/crm/public/contract_attachments"]


# ---------------------------------------------------------------- default set

def test_defaults_present_on_new_pilot(pilot, cli, cat):
    code, out, _ = cli("categories", "show")
    rows = cs.categories(cat, sc_(cat))
    assert code == 0 and [r["category_id"] for r in rows] == sorted(cs.DEFAULT_ORDER)
    assert all(r["description"] and r["name"] for r in rows)
    assert all(c in out for c in cs.DEFAULT_ORDER)


def test_retire_non_empty_category_rejected(pilot, cli, cat):
    uris = [f"crm://tickets/{i}" for i in range(3)]
    assets(cat, "manual-crm", *[(u, f"Tickets {i}", "crm") for i, u in enumerate(uris)])
    for u in uris:
        cs.assign(cat, sc_(cat), u, "support-history", "ben")
    code, _, err = cli("categories", "retire", "support-history", "--as", "ben")
    assert code == 1 and "3 assigned asset(s)" in err and all(u in err for u in uris)
    assert cat.one("SELECT status FROM category WHERE category_id = 'support-history'")["status"] == "active"


def test_add_rename_retire_by_approver(pilot, cli, cat):
    assert cli("categories", "add", "event-plans", "--name", "Events", "--description", "Event plans",
               "--as", "finn")[0] == 1  # not an approver
    assert cli("categories", "add", "event-plans", "--name", "Events", "--description", "Event plans",
               "--as", "ben")[0] == 0
    assert cli("categories", "rename", "event-plans", "--name", "Club events", "--as", "ben")[0] == 0
    assert cli("categories", "retire", "event-plans", "--reason", "not needed", "--as", "ben")[0] == 0
    row = cat.one("SELECT * FROM category WHERE category_id = 'event-plans'")
    assert (row["name"], row["status"]) == ("Club events", "retired")
    for action in ("categories.add", "categories.rename", "categories.retire"):
        assert len(audits(cat, action)) == 1


# ---------------------------------------------------------------- assignment

def test_category_spans_systems(pilot, cli, cat):
    for u in contracts(cat):
        cs.assign(cat, sc_(cat), u, "policies-and-contracts", "ben")
    code, out, _ = cli("categories", "show", "policies-and-contracts")
    assert code == 0
    for system, uri in zip(["sharepoint", "fileshare", "sql"], contracts(cat)):
        block = out.split(f"{system}:\n")[1].split(":\n")[0]
        assert uri in block


def test_second_primary_rejected(pilot, cat):
    assets(cat, "manual-crm", ("crm://a", "A", "crm"))
    cs.assign(cat, sc_(cat), "crm://a", "customer-records", "ben")
    with pytest.raises(Refused, match="demote it first"):
        cs.assign(cat, sc_(cat), "crm://a", "support-history", "ben")
    cs.assign(cat, sc_(cat), "crm://a", "support-history", "ben", is_primary=False)
    rows = cs.assignments(cat, "crm://a")
    assert [(r["category_id"], r["is_primary"]) for r in rows] == [("customer-records", True),
                                                                   ("support-history", False)]
    cs.demote(cat, sc_(cat), "crm://a", "ben")
    cs.assign(cat, sc_(cat), "crm://a", "support-history", "ben")
    assert cs.primary(cat, "crm://a")["category_id"] == "support-history"


def test_keyword_rule_suggests_category(pilot, cli, cat):
    assets(cat, "office-share", ("file:///share/Vertraege", "Vertraege", "fileshare"),
           ("file:///share/x", "Ablage", "fileshare", {"path": "/share/contracts/2025"}))
    code, out, _ = cli("categories", "suggest", "--as", "ben")
    assert code == 0
    for uri, matched in [("file:///share/Vertraege", "Vertraege"), ("file:///share/x", "contracts")]:
        p = cs.primary(cat, uri)
        assert (p["category_id"], p["state"], p["rule_id"], p["matched"]) == (
            "policies-and-contracts", "suggested", "R-KW-01", matched)
    assert "rule R-KW-01, matched 'Vertraege'" in out
    assert len(audits(cat, "categories.suggest")) == 2


def test_table_media_type_and_hint_rules(pilot, cat):
    assets(cat, "crm-db", ("sql://h/crm/public/tickets", "crm.public.tickets", "sql", {"format": "table"}))
    assets(cat, "manual-crm", ("crm://notes", "Notizen", "crm", {"format": "text/markdown"}))
    cs.suggest(cat, sc_(cat), "ben")
    assert cs.primary(cat, "sql://h/crm/public/tickets")["rule_id"] == "R-TBL-05"
    assert cs.primary(cat, "crm://notes")["rule_id"] == "R-MT-01"


def test_suggestion_not_final_until_accepted(pilot, cli, cat):
    assets(cat, "office-share", ("file:///share/Vertraege", "Vertraege", "fileshare"))
    cs.suggest(cat, sc_(cat), "ben")
    order, reasons = cs.handoff(cat, sc_(cat))
    assert order is None
    assert ("unassigned: file:///share/Vertraege has no accepted primary category (only suggested: "
            "policies-and-contracts)") in reasons
    code, _, err = cli("categories", "handoff")
    assert code == 1 and "unassigned: file:///share/Vertraege" in err
    cs.accept(cat, sc_(cat), "file:///share/Vertraege", "ben")
    assert not any("unassigned" in r for r in cs.handoff(cat, sc_(cat))[1])


def test_reject_suggestion_is_not_suggested_again(pilot, cat):
    assets(cat, "office-share", ("file:///share/Vertraege", "Vertraege", "fileshare"))
    cs.suggest(cat, sc_(cat), "ben")
    cs.reject(cat, sc_(cat), "file:///share/Vertraege", "ben", "these are letters, not contracts")
    assert cs.primary(cat, "file:///share/Vertraege") is None
    assert cs.suggest(cat, sc_(cat), "ben") == []
    [a] = audits(cat, "categories.reject")
    assert a["reason"] == "these are letters, not contracts"


def test_revert_to_earlier_assignment(pilot, cli, cat):
    assets(cat, "manual-crm", ("crm://a", "A", "crm"))
    cs.assign(cat, sc_(cat), "crm://a", "customer-records", "ben")
    cs.demote(cat, sc_(cat), "crm://a", "ben")
    cs.assign(cat, sc_(cat), "crm://a", "support-history", "ben", reason="these are tickets")
    code, out, _ = cli("categories", "revert", "crm://a", "--to", "customer-records", "--reason", "mistake",
                       "--as", "ben")
    assert code == 0 and cs.primary(cat, "crm://a")["category_id"] == "customer-records"
    change = [a for a in audits(cat, "categories.assign") if a["new"]["primary"] == "support-history"]
    [rev] = audits(cat, "categories.revert")
    assert len(change) == 1 and change[0]["reason"] == "these are tickets"
    assert rev["previous"] == {"primary": "support-history"} and rev["new"]["primary"] == "customer-records"
    assert rev["actor"] == "ben" and rev["reason"] == "mistake"
    assert cli("categories", "revert", "crm://a", "--to", "kb-and-sops", "--as", "ben")[0] == 1


# ---------------------------------------------------------------- overlap hints

def test_same_document_in_two_systems(pilot, cli, cat):
    assets(cat, "support-site", ("sharepoint://c/drives/r", "Reisekostenrichtlinie_2025.pdf", "sharepoint",
                                 {"content_hash": HASH, "format": "application/pdf"}))
    assets(cat, "office-share", ("file:///share/Reisekostenrichtlinie_2025.pdf", "Reisekostenrichtlinie_2025.pdf",
                                 "fileshare", {"content_hash": HASH, "format": "application/pdf"}))
    before = {r["asset_uri"]: r["status"] for r in inv.records(cat, CID)}
    for u in before:
        cs.assign(cat, sc_(cat), u, "policies-and-contracts", "ben")
    hints = cs.overlap_hints(cat, sc_(cat), "policies-and-contracts")
    assert {h["kind"] for h in hints} == {"title", "content_hash"}
    assert all(h["assets"] == sorted(before) for h in hints)
    out = cli("categories", "show", "policies-and-contracts")[1]
    assert "overlap hint (content_hash): file:///share/Reisekostenrichtlinie_2025.pdf <-> sharepoint://c/drives/r" in out
    assert {r["asset_uri"]: r["status"] for r in inv.records(cat, CID)} == before


def test_same_columns_in_two_systems_hinted(pilot, cat):
    f = [{"name": "kundennr", "type": "TEXT"}, {"name": "email", "type": "TEXT"}]
    assets(cat, "crm-db", ("sql://a/crm/public/kunden", "crm.public.kunden", "sql", {"fields": f}))
    assets(cat, "manual-crm", ("crm://export/members", "Members export", "crm", {"fields": f}))
    for u in ("sql://a/crm/public/kunden", "crm://export/members"):
        cs.assign(cat, sc_(cat), u, "customer-records", "ben")
    assert [h["kind"] for h in cs.overlap_hints(cat, sc_(cat), "customer-records")] == ["columns"]


# ---------------------------------------------------------------- ordering

def _one_per_category(cat, special_in=None):
    for i, c in enumerate(cs.DEFAULT_ORDER):
        uri = f"crm://{c}"
        assets(cat, f"src-{i}", (uri, c, "crm"), tier="special-category" if c == special_in else "internal")
        cs.assign(cat, sc_(cat), uri, c, "ben")


def test_special_category_data_goes_last(pilot, cat):
    _one_per_category(cat, special_in="support-history")
    row = cs.propose(cat, sc_(cat), "ben", {"max_tier": 0, "personal_share": 0})  # scores alone would put it early
    assert row["categories"][-1] == "support-history"
    assert row["categories"][:-1] == [c for c in cs.DEFAULT_ORDER if c != "support-history"]  # ties: default order
    with pytest.raises(Refused, match="must come after all others"):
        cs.set_order(cat, sc_(cat), ["support-history", *row["categories"][:-1]], "ben", "try")


def test_score_inputs_are_explained(pilot, cli, cat):
    _one_per_category(cat)
    code, out, _ = cli("categories", "order", "--propose", "--weight", "systems=0.7", "--as", "ben")
    assert code == 0
    assert "weights (our assumptions, configurable): max_tier=1.0, personal_share=2.0, systems=0.7, overlaps=0.25" in out
    for c in cs.DEFAULT_ORDER:
        assert f"{c}: score 1.7 (max_tier=1, personal_share=0.0, systems=1, overlaps=0)" in out


def test_unapproved_order_not_used(pilot, cat):
    _one_per_category(cat)
    cs.propose(cat, sc_(cat), "ben")
    order, reasons = cs.handoff(cat, sc_(cat))
    assert order is None and reasons == ["category order not approved"]
    with pytest.raises(Refused, match="not an approver"):
        cs.approve(cat, sc_(cat), "finn")
    cs.approve(cat, sc_(cat), "ben")
    assert cs.handoff(cat, sc_(cat))[0] == cs.DEFAULT_ORDER


def test_order_change_versioned(pilot, cli, cat):
    _one_per_category(cat)
    cs.propose(cat, sc_(cat), "ben")
    cs.approve(cat, sc_(cat), "ben")
    v1 = cs.DEFAULT_ORDER
    new = ["kb-and-sops", "support-history", "customer-records", "policies-and-contracts", "financial-ops-metrics"]
    assert cli("categories", "order", "--set", ",".join(new), "--as", "ben")[0] == 1  # no reason
    assert cli("categories", "order", "--set", ",".join(new), "--reason", "CRM first: board request",
               "--as", "ben")[0] == 0
    assert cli("categories", "approve", "--as", "ben")[0] == 0
    rows = cs.order_versions(cat, CID)
    assert [(r["version"], r["status"]) for r in rows] == [(1, "approved"), (2, "approved")]
    active = cs.approved_order(cat, CID)
    assert active["version"] == 2 and active["categories"] == new and active["reason"] == "CRM first: board request"
    assert rows[0]["categories"] == v1 and rows[0]["approved_by"] == "ben" and rows[0]["approved_at"]
    out = cli("categories", "order", "--version", "1")[1]
    assert "version 1 (approved, approved by ben" in out


# ---------------------------------------------------------------- step order per category

def _ready(cat):
    _one_per_category(cat)
    cs.propose(cat, sc_(cat), "ben")
    cs.approve(cat, sc_(cat), "ben")
    order, reasons = cs.handoff(cat, sc_(cat), "ben")
    assert order and not reasons
    return order


def test_enrich_before_standardize_refused(pilot, cli, cat):
    _ready(cat)
    for step in ("score", "discard"):
        cs.set_step(cat, sc_(cat), "kb-and-sops", step, "complete", "ben")
    with pytest.raises(StepRefused) as e:
        require_step("kb-and-sops", "enrich", "charter.yaml", cat)
    assert "step 'standardize' is not complete for category 'kb-and-sops'" in e.value.reasons[0]
    code, _, err = cli("categories", "step", "kb-and-sops", "enrich", "--status", "in_progress", "--as", "ben")
    assert code == 1 and "standardize" in err
    cs.set_step(cat, sc_(cat), "kb-and-sops", "standardize", "complete", "ben")
    require_step("kb-and-sops", "enrich", "charter.yaml", cat)


def test_skipping_ahead_in_the_category_order_needs_a_waiver(pilot, cli, cat):
    order = _ready(cat)
    assert order.index("kb-and-sops") < order.index("customer-records")
    for c in order:
        cs.set_step(cat, sc_(cat), c, "score", "complete", "ben")
    with pytest.raises(StepRefused) as e:
        require_step("customer-records", "discard", "charter.yaml", cat)
    assert "have not finished 'discard': kb-and-sops" in e.value.reasons[0]
    assert cli("categories", "step", "customer-records", "discard", "--waive", "--reason", "CRM export deadline",
               "--as", "finn")[0] == 1
    assert cli("categories", "step", "customer-records", "discard", "--waive", "--reason", "CRM export deadline",
               "--as", "ben")[0] == 0
    require_step("customer-records", "discard", "charter.yaml", cat)
    [w] = audits(cat, "categories.step.waive")
    assert w["entity"] == "customer-records/discard" and w["reason"] == "CRM export deadline" and w["actor"] == "ben"
    code, out, _ = cli("categories", "step", "customer-records", "discard")
    assert code == 0 and "may start" in out


def test_inventory_step_completed_by_handoff(pilot, cat):
    _ready(cat)
    assert all(cs.step_status(cat, CID, c, "inventory")["status"] == "complete" for c in cs.DEFAULT_ORDER)
    assert len(audits(cat, "categories.step")) == 5
