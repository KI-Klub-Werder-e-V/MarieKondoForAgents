"""Synthetic c08 set-up: the c07 governed catalog (two approved categories, gate passed), a SQLite source with an
FAQ table and a support-ticket table, accepted `keep` verdicts for both source assets, a registered agent, and
descriptors for the products `support-faq` (kb-and-sops) and `support-tickets` (support-history). No real data."""

import copy
import json
import sqlite3
from datetime import timedelta

from gov_fixture import KB, SUPPORT, add_doc, agent, approve_all, govern, scope
from konmari.canonical import load_yaml
from konmari.catalog import new_id, now
from konmari.connect import descriptor as dsc
from konmari.connect import products as prd
from konmari.connect import refresh as rf
from konmari.connect import tools as tls
from konmari.governance import gate
from konmari.scorecard.engine import add_review

from conftest import EXAMPLE

SUITE = {"principals": [{"id": "support", "user": "test-support", "groups": ["support-team"]},
                        {"id": "outsider", "user": "test-outsider", "groups": []}]}
FAQ_URI, TICKETS_URI = "manual://kb/faq", "manual://support/tickets"
MASKING = {"default": "replace-with-type", "EMAIL_ADDRESS": "pseudonymize"}
T0 = now().replace(microsecond=0) - timedelta(hours=2)


def charter_doc() -> dict:
    return load_yaml(EXAMPLE / "charter.yaml")


def keep(cat, uri: str, category: str, to: str = "keep") -> None:
    """An accepted c03 verdict for a source asset."""
    vid = new_id()
    cat.execute("INSERT INTO verdict VALUES (?, 'run', ?, 'verein-faq-assistant', ?, 'v1', ?, NULL, NULL, '[]', false,"
                " 'h', ?)", (vid, uri, category, to, now()))
    add_review(cat, uri, vid, "propose", "konmari", None, to, None, "v1")
    add_review(cat, uri, vid, "accept", "ben", to, to, None, "v1")


def governed(cat, sc) -> None:
    """c07 done: ACL-carrying chunks in both categories, approved policies with an EMAIL rule, gate passed."""
    add_doc(cat, "public-faq", SUPPORT, ["Der Mitgliedsbeitrag ist im Januar fällig."], allow=["group:support-team"])
    add_doc(cat, "kb-doc", KB, ["Hallenzeiten im Winter."], allow=["group:staff"])
    approve_all(cat, sc, masking=MASKING)
    govern(cat, sc)
    assert {r["category_id"] for r in gate.check(cat, sc, "dirk", SUITE)["sources"]
            if r["status"] == "approved"} == {KB, SUPPORT}
    agent(cat, sc, "faq-bot")
    keep(cat, FAQ_URI, KB)
    keep(cat, TICKETS_URI, SUPPORT)


def source_db(path) -> str:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE faq (id TEXT PRIMARY KEY, question TEXT, answer TEXT, topic TEXT, author_email TEXT,
                          internal_note TEXT, acl TEXT, modified_at TEXT);
        CREATE TABLE tickets (id TEXT PRIMARY KEY, region TEXT, summary TEXT, acl TEXT, modified_at TEXT);
    """)
    for n in range(12):
        con.execute("INSERT INTO faq VALUES (?, ?, ?, ?, ?, ?, 'group:everyone', ?)",
                    (f"faq-{n:03d}", f"Frage {n} zum Mitgliedsbeitrag?", f"Antwort {n}: Beitrag im Januar.",
                     "fees" if n % 2 else "training", f"helfer{n}@example.org", "nur intern: Kassenstand",
                     (T0 + timedelta(minutes=n)).isoformat()))
    for n, region in enumerate(["Brandenburg", "Brandenburg", "Berlin", "Brandenburg", "Berlin"]):
        con.execute("INSERT INTO tickets VALUES (?, ?, ?, ?, ?)",
                    (f"t-{n}", region, f"Rückfrage {n} zur Hallenbelegung", f"group:region-{region.lower()}",
                     (T0 + timedelta(minutes=n)).isoformat()))
    con.commit()
    con.close()
    return f"sqlite:///{path}"


def touch(locator: str, sql: str, params=()) -> None:
    con = sqlite3.connect(locator.removeprefix("sqlite:///"))
    con.execute(sql, params)
    con.commit()
    con.close()


def faq_desc(locator: str, version: str = "1.0.0") -> dict:
    prop = lambda name, desc, cls="public", **kw: {"name": name, "logicalType": kw.pop("t", "string"),  # noqa: E731
                                                    "description": desc, "classification": cls, **kw}
    product = {
        "apiVersion": "v1.0.0", "kind": "DataProduct", "id": "support-faq", "name": "Support FAQ",
        "version": version, "status": "active", "domain": KB,
        "description": {"purpose": "Answer recurring member questions about fees, training times and forms.",
                        "usage": "Look up the approved answer before replying to a member.",
                        "limitations": "Not for decisions on individual fee reductions or legal advice."},
        "team": {"members": [{"username": "ben", "role": "owner"}]},
        "outputPorts": [{"name": "faq", "version": version, "type": "mcp", "contractId": "support-faq-contract"}],
        "customProperties": [
            {"property": "charterUseCase", "value": "Member FAQ assistant"},
            {"property": "category", "value": KB},
            {"property": "scopes", "value": ["product:support-faq:read"]},
            {"property": "maskingPolicy", "value": KB},
            {"property": "sources", "value": [{"id": "office-faq", "assetUri": FAQ_URI, "locator": locator,
                                               "table": "faq", "key": "id", "cursor": "modified_at",
                                               "sourceOfTruth": "primary", "aclColumn": "acl"}]},
            {"property": "personalDataJustification", "value": {}},
        ]}
    contract = {
        "apiVersion": "v3.1.0", "kind": "DataContract", "id": "support-faq-contract", "name": "Support FAQ",
        "version": version, "status": "active", "domain": KB,
        "schema": [{"name": "faq", "physicalType": "table", "description": "Approved FAQ entries of the club office",
                    "properties": [
                        prop("id", "Stable FAQ entry ID", primaryKey=True, required=True),
                        prop("question", "The member question as asked", required=True),
                        prop("answer", "The approved answer of the club office"),
                        prop("topic", "FAQ topic (fees, training)",
                             customProperties=[{"property": "filterable", "value": True}]),
                        prop("author_email", "Volunteer who wrote the entry", "personal",
                             customProperties=[{"property": "piiEntity", "value": "EMAIL_ADDRESS"}]),
                    ]}],
        "slaProperties": [{"property": "latency", "value": 1, "unit": "d"}],
    }
    return {"product": product, "contract": contract}


def tickets_desc(locator: str, version: str = "1.0.0") -> dict:
    d = copy.deepcopy(faq_desc(locator, version))
    p, c = d["product"], d["contract"]
    p.update(id="support-tickets", name="Support tickets", domain=SUPPORT)
    p["outputPorts"][0].update(name="tickets", contractId="support-tickets-contract")
    cp = {x["property"]: x for x in p["customProperties"]}
    cp["category"]["value"], cp["maskingPolicy"]["value"] = SUPPORT, SUPPORT
    cp["scopes"]["value"] = ["product:support-tickets:read"]
    cp["sources"]["value"] = [{"id": "support-db", "assetUri": TICKETS_URI, "locator": locator, "table": "tickets",
                               "key": "id", "cursor": "modified_at", "sourceOfTruth": "primary", "aclColumn": "acl"}]
    c.update(id="support-tickets-contract", name="Support tickets", domain=SUPPORT)
    c["schema"] = [{"name": "tickets", "physicalType": "table", "description": "Support tickets by region",
                    "properties": [
                        {"name": "id", "logicalType": "string", "primaryKey": True, "required": True,
                         "description": "Ticket ID", "classification": "internal"},
                        {"name": "region", "logicalType": "string", "description": "Region of the member",
                         "classification": "internal", "customProperties": [{"property": "filterable",
                                                                             "value": True}]},
                        {"name": "summary", "logicalType": "string", "description": "What the ticket is about",
                         "classification": "internal"}]}]
    return d


def ship(cat, sc, desc: dict, owner: str = "ben", approve_tools: bool = True, at=None) -> dict:
    """lint -> register -> refresh -> test -> approve -> publish -> pin the tools."""
    assert dsc.lint(desc, cat, sc, charter_doc()) == []
    i = dsc.info(desc)
    prd.register(cat, sc, desc, owner)
    run = rf.refresh(cat, i["id"], owner)
    assert run["status"] == "ok", run["error"]
    prd.run_tests(cat, i["id"], i["version"])
    prd.approve(cat, sc, i["id"], i["version"], owner)
    out = prd.publish(cat, sc, i["id"], i["version"], owner, at=at)
    if approve_tools:
        for d, _ in tls.generated(cat):
            tls.approve(cat, sc, d["name"], "clara")
    return out


def events(cat, action: str) -> list[dict]:
    return [r | {"details": json.loads(r["details"])} for r in
            cat.query("SELECT * FROM audit_event WHERE action = ? ORDER BY seq", (action,))]


# ---------------------------------------------------------------- MCP and a test OIDC issuer

ISSUER = "https://id.example.org/realms/pilot"


class Issuer:
    """A local test OIDC issuer: one RSA key, its JWKS, RS256 access tokens."""

    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from jwt.algorithms import RSAAlgorithm

        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(RSAAlgorithm.to_jwk(self.key.public_key()))
        self.jwks = {"keys": [{**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}]}

    def token(self, aud: str, scope: str = "product:support-faq:read", user: str = "finn", agent: str = "faq-bot",
              groups=("support-team",), exp_in: int = 300, iss: str = ISSUER) -> str:
        import time

        import jwt

        claims = {"iss": iss, "aud": aud, "sub": user, "act": {"sub": agent}, "groups": list(groups),
                  "scope": scope, "azp": "test-client", "exp": int(time.time()) + exp_in, "iat": int(time.time())}
        return jwt.encode(claims, self.key, "RS256", headers={"kid": "k1"})


def identity(cat, scopes=("product:support-faq:read", "product:support-tickets:read"), user="finn",
             groups=("support-team",), agent="faq-bot"):
    from konmari.connect.server import Identity
    from konmari.governance import access

    return Identity(access.context(cat, agent, user, list(groups)), list(scopes), "test-client")


def mcp(srv, fn, mode="2026-07-28"):
    """Run `await fn(client)` against an in-process MCP server."""
    import anyio
    from mcp import Client

    async def main():
        async with Client(srv, mode=mode) as c:
            return await fn(c)
    return anyio.run(main)


__all__ = ["scope", "KB", "SUPPORT"]
