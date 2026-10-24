"""sensitive-data-masking (design D5, D6): German and English PII detection with Presidio before indexing, findings
without values, sensitivity classification, masking operators, HMAC pseudonyms with the key in `konmari_keys`,
audited re-identification, human review, and the measured recall gate."""

import csv
import hashlib
import hmac
import json
import os
import random
import re
import secrets
from functools import cache
from importlib.metadata import version as pkg_version
from importlib.resources import files
from pathlib import Path

from konmari.canonical import load_yaml
from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.chunking import store as cst
from konmari.enrich.store import language
from konmari.governance import acl as gacl
from konmari.governance import policy as pol
from konmari.governance.access import AccessDenied, Context
from konmari.governance.audit import write_event
from konmari.inventory.sensitivity import rank
from konmari.versions import Refused

RECOGNIZERS_VERSION = "1"  # bump when the custom recognizers below change (outdates recall gate results)
ENTITIES = ["PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN_CODE", "IP_ADDRESS", "CREDIT_CARD", "LOCATION",
            "DE_TAX_ID", "DE_TAX_NUMBER", "DE_ID_CARD", "DE_PASSPORT", "DE_SOCIAL_SECURITY", "DE_FUEHRERSCHEIN",
            "DE_HEALTH_INSURANCE", "DE_PLZ_ORT"]
SPECIAL = {"DE_HEALTH_INSURANCE"}  # DSGVO Art. 9 health data
DIRECT = ("PERSON", "EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN_CODE")
REVIEWER_ROLES = {"data_steward", "data_owner", "compliance"}
_TOKEN = re.compile(r"^<([A-Z_]+)_([0-9a-f]{8})>$")


def _config_text() -> str:
    return (files("konmari") / "governance" / "languages-config.yml").read_text(encoding="utf-8")


def models() -> dict[str, str]:
    cfg = load_yaml(_config_text())
    names = {m["lang_code"]: m["model_name"] for m in cfg["models"]}
    names["de"] = os.environ.get("KONMARI_NLP_MODEL", names["de"])
    names["en"] = os.environ.get("KONMARI_NLP_MODEL_EN", names["en"])
    return names


def detector_version() -> str:
    m = models()
    cfg = hashlib.sha256(_config_text().encode()).hexdigest()[:12]
    return f"presidio-analyzer {pkg_version('presidio-analyzer')}; de={Path(m['de']).name}; en={Path(m['en']).name};" \
           f" config {cfg}; recognizers v{RECOGNIZERS_VERSION}"


@cache
def _analyzer(de_model: str, en_model: str):
    import spacy
    from presidio_analyzer import AnalyzerEngine, EntityRecognizer, Pattern, PatternRecognizer, RecognizerRegistry
    from presidio_analyzer import RecognizerResult
    from presidio_analyzer import predefined_recognizers as pr
    from presidio_analyzer.nlp_engine import SpacyNlpEngine

    for m in (de_model, en_model):  # Presidio would download a missing model: an outbound call, so refuse
        if not (spacy.util.is_package(m) or Path(m).exists()):
            raise Refused(f"spaCy model '{m}' is not installed; run 'uv run python -m spacy download {m}'")

    class Salutation(EntityRecognizer):
        """PERSON after a salutation (Frau/Herr, Mr/Mrs/Ms): deterministic recall without the NER model."""
        WORDS = {"de": r"Frau|Herrn?|Fr\.|Hr\.", "en": r"Mrs?\.?|Ms\.?|Miss"}

        def __init__(self, lang):
            self.rx = re.compile(rf"\b(?:{self.WORDS[lang]})\s+((?:Dr\.\s+)?[A-ZÄÖÜ][a-zäöüß]+(?:(?:[ \t]+|-)"
                                 rf"[A-ZÄÖÜ][a-zäöüß]+)?)")
            super().__init__(["PERSON"], name=f"Salutation{lang}", supported_language=lang)

        def load(self):
            pass

        def analyze(self, text, entities, nlp_artifacts=None):
            return [RecognizerResult("PERSON", m.start(1), m.end(1), 0.85) for m in self.rx.finditer(text)]

    nlp = SpacyNlpEngine(models=[{"lang_code": "de", "model_name": de_model},
                                 {"lang_code": "en", "model_name": en_model}])
    nlp.load()
    reg = RecognizerRegistry(supported_languages=["de", "en"])
    reg.load_predefined_recognizers(languages=["de", "en"], nlp_engine=nlp)
    for name in dir(pr):  # the German recognizers ship disabled; DE_PLZ alone is too noisy (years, prices)
        if name.startswith("De") and name.endswith("Recognizer") and name != "DePlzRecognizer":
            reg.add_recognizer(getattr(pr, name)())
    reg.add_recognizer(pr.CreditCardRecognizer(supported_language="de"))
    reg.add_recognizer(PatternRecognizer("DE_PLZ_ORT", supported_language="de", patterns=[Pattern(
        "plz_ort", r"\b\d{5}\s+[A-ZÄÖÜ][a-zäöüß]+(?:[- ][A-ZÄÖÜ][a-zäöüß]+)?\b", 0.6)]))
    for lang in ("de", "en"):
        reg.add_recognizer(Salutation(lang))
    ctx = load_yaml(_config_text()).get("context", {})
    for r in reg.recognizers:
        words = [w for e in r.supported_entities for w in ctx.get(r.supported_language, {}).get(e, [])]
        if words:
            r.context = sorted(set(words) | set(getattr(r, "context", None) or []))
    return AnalyzerEngine(nlp_engine=nlp, registry=reg, supported_languages=["de", "en"])


def analyze(text: str, lang: str, low: float) -> list[dict]:
    m = models()
    out = []
    for r in _analyzer(m["de"], m["en"]).analyze(text=text, language=lang, entities=ENTITIES):
        if r.score >= low:
            out.append({"entity_type": r.entity_type, "start": r.start, "end": r.end, "score": round(r.score, 4)})
    return out


# ---------------------------------------------------------------- scan

def pilot_chunks(cat: Catalog, sc: Scope, category: str | None = None) -> list[dict]:
    items = {i["item_id"]: c for c in ([category] if category else sc.categories)
             for i in gacl.items_of(cat, sc, c)}
    return [{**c, "category_id": items[c["item_id"]]} for c in cst.active_chunks(cat) if c["item_id"] in items]


def scan(cat: Catalog, sc: Scope, actor: str, cfg: dict) -> dict:
    """Scan every active pilot chunk not yet scanned with this detector version; findings keep no values.
    Reviewer decisions on the same span survive a rescan."""
    dv, low, high = detector_version(), cfg["pii"]["low"], cfg["pii"]["high"]
    rep = {"detector_version": dv, "scanned": 0, "unchanged": 0, "findings": 0, "pending_review": 0}
    for c in pilot_chunks(cat, sc):
        s = cat.one("SELECT * FROM pii_scan WHERE chunk_id = ?", (c["chunk_id"],))
        if s and s["content_hash"] == c["content_hash"] and s["detector_version"] == dv:
            rep["unchanged"] += 1
            continue
        lang = language(c["content"]) or "de"
        kept = {(f["entity_type"], f["start_pos"], f["end_pos"]): f for f in cat.query(
            "SELECT * FROM pii_finding WHERE chunk_id = ? AND review_state IN ('confirmed', 'rejected')",
            (c["chunk_id"],))} if s and s["content_hash"] == c["content_hash"] else {}
        cat.execute("DELETE FROM pii_finding WHERE chunk_id = ?", (c["chunk_id"],))
        found = analyze(c["content"], lang, low)
        for f in found:
            old = kept.get((f["entity_type"], f["start"], f["end"]))
            state = old["review_state"] if old else "auto" if f["score"] >= high else "pending"
            rep["pending_review"] += state == "pending"
            cat.execute("INSERT INTO pii_finding VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (new_id(), c["chunk_id"], f["entity_type"], f["start"], f["end"], f["score"], dv, state,
                         old and old["reviewed_by"], old and old["reviewed_at"]))
        cat.execute("DELETE FROM pii_scan WHERE chunk_id = ?", (c["chunk_id"],))
        cat.execute("INSERT INTO pii_scan VALUES (?, ?, ?, ?, ?, ?)",
                    (c["chunk_id"], c["content_hash"], lang, dv, len(found), now()))
        rep["scanned"] += 1
        rep["findings"] += len(found)
    write_event(cat, "govern.pii_scan", actor=actor, charter_id=sc.charter_id, **rep)
    return rep


def findings(cat: Catalog, chunk_id: str, include_rejected=False) -> list[dict]:
    cond = "" if include_rejected else " AND review_state <> 'rejected'"
    return cat.query(f"SELECT * FROM pii_finding WHERE chunk_id = ?{cond} ORDER BY start_pos", (chunk_id,))


def classify(found: list[dict], policy_sensitivity: str) -> str:
    """The higher of the policy class and the findings (Art. 9 entities -> special-category)."""
    tiers = [policy_sensitivity] + ["special-category" if f["entity_type"] in SPECIAL else "personal" for f in found]
    return max(tiers, key=rank)


# ---------------------------------------------------------------- keys and masking

def _keyholder(cat: Catalog):
    if cat.dialect == "postgres":
        cat.execute("SET LOCAL ROLE konmari_keyholder")


def key(cat: Catalog) -> bytes:
    """The active HMAC key from schema konmari_keys (created on first use), read under the keyholder role."""
    with cat.transaction():
        _keyholder(cat)
        r = cat.one("SELECT secret FROM konmari_keys.pseudonym_key WHERE active ORDER BY created_at DESC LIMIT 1")
        if r is None:
            r = {"secret": secrets.token_hex(32)}
            cat.execute("INSERT INTO konmari_keys.pseudonym_key VALUES (?, ?, true, ?)", (new_id(), r["secret"], now()))
    return bytes.fromhex(r["secret"])


def token(k: bytes, entity: str, value: str) -> str:
    norm = " ".join(value.casefold().split())
    return f"<{entity}_{hmac.new(k, norm.encode(), hashlib.sha256).hexdigest()[:8]}>"


def apply_mask(text: str, found: list[dict], rules: dict, k: bytes) -> str:
    spans, end = [], -1
    for f in sorted(found, key=lambda f: (f["start_pos"], -f["end_pos"])):
        if f["start_pos"] >= end:  # overlapping detections: the earlier, longer span wins
            spans.append(f)
            end = f["end_pos"]
    for f in reversed(spans):
        s, e, ent = f["start_pos"], f["end_pos"], f["entity_type"]
        op = rules.get(ent, rules.get("default", "replace-with-type"))
        rep = {"redact": "", "mask": "*" * (e - s), "replace-with-type": f"<{ent}>"}.get(op) \
            if op != "pseudonymize" else token(k, ent, text[s:e])
        text = text[:s] + rep + text[e:]
    return text


def build_index(cat: Catalog, sc: Scope, actor: str) -> dict:
    """`konmari govern mask`: (re)build the agent index from the approved policies. A chunk without a completed
    scan fails as `pii_unscanned`, one without ACL as `acl_missing`; special-category data without an approved
    legal basis is excluded. Only masked content is written."""
    dv, k = detector_version(), key(cat)
    rep = {"indexed": 0, "failed": [], "excluded": [], "skipped": {}}
    for cid in sc.categories:
        p = pol.active(cat, cid)
        chunks = pilot_chunks(cat, sc, cid)
        live = {c["chunk_id"] for c in chunks} if p else set()
        for r in cat.query("SELECT chunk_id FROM agent_index WHERE category_id = ?", (cid,)):
            if r["chunk_id"] not in live:
                cat.execute("DELETE FROM agent_index WHERE chunk_id = ?", (r["chunk_id"],))
        if p is None:
            rep["skipped"][cid] = "no approved source policy"
            continue
        doc = p["doc"]
        for c in chunks:
            cid_ = c["chunk_id"]
            cat.execute("DELETE FROM agent_index WHERE chunk_id = ?", (cid_,))
            s = cat.one("SELECT * FROM pii_scan WHERE chunk_id = ?", (cid_,))
            if not s or s["content_hash"] != c["content_hash"] or s["detector_version"] != dv:
                rep["failed"].append({"chunk_id": cid_, "item_id": c["item_id"], "reason": "pii_unscanned"})
                continue
            found = findings(cat, cid_)
            sens = classify(found, doc["sensitivity"])
            if sens == "special-category" and not doc.get("legal_basis"):
                rep["excluded"].append({"chunk_id": cid_, "reason": "special_category"})
                continue
            row = {"chunk_id": cid_, "item_id": c["item_id"], "category_id": cid,
                   "content": apply_mask(c["content"], found, doc.get("masking") or {}, k), "sensitivity": sens,
                   "allowed_principals": sorted(set(c["allowed_principals"] or []) | set(gacl._service(doc)))
                   if c["allowed_principals"] else [],
                   "denied_principals": list(c["denied_principals"] or []), "acl_version": c["acl_version"] or "",
                   "policy_version": p["version"], "indexed_at": now()}
            try:
                gacl.require_acl(row, c["item_id"])
            except gacl.AclMissing:
                rep["failed"].append({"chunk_id": cid_, "item_id": c["item_id"], "reason": "acl_missing"})
                continue
            cat.execute(f"INSERT INTO agent_index ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                        tuple(row.values()))
            rep["indexed"] += 1
    write_event(cat, "govern.index", actor=actor, charter_id=sc.charter_id, indexed=rep["indexed"],
                failed=[f"{f['chunk_id']}:{f['reason']}" for f in rep["failed"]],
                excluded=[e["chunk_id"] for e in rep["excluded"]], detector_version=dv)
    return rep


# ---------------------------------------------------------------- re-identification

def _roles(sc: Scope, sid: str) -> set[str]:
    return set((sc.stakeholders.get(sid) or {}).get("roles", []))


def reidentify(cat: Catalog, sc: Scope, tok: str, requester: str, purpose: str) -> list[str]:
    """Compliance only, purpose mandatory, always audited. No reverse map exists: the token is matched against
    the HMAC of the detected spans in the (non agent-facing) chunk store."""
    if not (purpose or "").strip():
        raise Refused("a purpose is mandatory for re-identification")
    if "compliance" not in _roles(sc, requester):
        write_event(cat, "govern.reidentify", actor=requester, result="deny", charter_id=sc.charter_id,
                    token=tok, purpose=purpose, decision="deny", reason="not_compliance")
        raise AccessDenied(403, "not_compliance")
    m = _TOKEN.match(tok)
    if not m:
        raise Refused(f"'{tok}' is not a pseudonym token like <PERSON_7f3a9c01>")
    k, values = key(cat), set()
    for f in cat.query("SELECT f.start_pos, f.end_pos, c.content FROM pii_finding f JOIN chunk c ON c.chunk_id ="
                       " f.chunk_id WHERE f.entity_type = ?", (m[1],)):
        v = f["content"][f["start_pos"]:f["end_pos"]]
        if token(k, m[1], v) == tok:
            values.add(" ".join(v.split()))
    write_event(cat, "govern.reidentify", actor=requester, charter_id=sc.charter_id, token=tok, purpose=purpose,
                decision="allow", matches=len(values))
    return sorted(values)


def agent_key_lookup(cat: Catalog, ctx: Context) -> list:
    """An agent identity querying the key schema. Postgres refuses it by role; the denial is audited.
    shortcut: DuckDB has no roles, so the refusal there is in code; use Postgres for shared deployments."""
    try:
        if cat.dialect != "postgres":
            raise PermissionError("konmari_keys is not accessible to agent identities")
        with cat.transaction():
            cat.execute("SET LOCAL ROLE konmari_agent")
            return cat.query("SELECT * FROM konmari_keys.pseudonym_key")
    except Exception as e:
        if not isinstance(e, PermissionError) and "permission denied" not in str(e):
            raise
        write_event(cat, "agent.key_access", actor=ctx.agent, result="deny", agent=ctx.agent,
                    on_behalf_of=ctx.on_behalf_of or "none", decision="deny", reason="keys_schema")
        raise AccessDenied(403, "keys_schema") from None


# ---------------------------------------------------------------- review queue

def queue(cat: Catalog, sc: Scope) -> list[dict]:
    """Mid-score findings awaiting a decision, with the span shown to the reviewer (never stored)."""
    ids = {c["chunk_id"]: c for c in pilot_chunks(cat, sc)}
    out = []
    for f in cat.query("SELECT * FROM pii_finding WHERE review_state = 'pending' ORDER BY chunk_id, start_pos"):
        if c := ids.get(f["chunk_id"]):
            out.append({**f, "span": c["content"][f["start_pos"]:f["end_pos"]],
                        "context": c["content"][max(0, f["start_pos"] - 40):f["end_pos"] + 40]})
    return out


def review(cat: Catalog, sc: Scope, finding_id: str, decision: str, reviewer: str, reason: str | None = None) -> dict:
    """confirm | reject | revert (back to pending, which is masked). Takes effect on the next mask run."""
    if not _roles(sc, reviewer) & REVIEWER_ROLES:
        raise Refused(f"'{reviewer}' holds none of the reviewer roles ({', '.join(sorted(REVIEWER_ROLES))})")
    f = cat.one("SELECT * FROM pii_finding WHERE finding_id = ?", (finding_id,))
    if f is None:
        raise Refused(f"unknown finding '{finding_id}'")
    new = {"confirm": "confirmed", "reject": "rejected", "revert": "pending"}[decision]
    if decision == "revert" and f["review_state"] not in ("confirmed", "rejected"):
        raise Refused(f"finding {finding_id} has no decision to revert")
    cat.execute("UPDATE pii_finding SET review_state = ?, reviewed_by = ?, reviewed_at = ? WHERE finding_id = ?",
                (new, reviewer, now(), finding_id))
    write_event(cat, "govern.pii_review", actor=reviewer, charter_id=sc.charter_id, finding=finding_id,
                chunk_id=f["chunk_id"], entity_type=f["entity_type"], previous=f["review_state"], new=new,
                reason=reason)
    return {**f, "review_state": new}


# ---------------------------------------------------------------- recall gate

def export_sample(cat: Catalog, sc: Scope, category: str, path: str | Path, actor: str, size: int = 200,
                  seed: int = 0) -> dict:
    """CSV for human labelling: one row per chunk; labellers fill `labels` as `PERSON=Petra Schulz; IBAN_CODE=...`.
    The file holds pilot text: keep it inside the organization and delete it after import."""
    chunks = sorted(pilot_chunks(cat, sc, category), key=lambda c: c["chunk_id"])
    picked = random.Random(seed).sample(chunks, min(size, len(chunks)))
    sample_id = new_id()
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, ["sample_id", "category_id", "chunk_id", "text", "labels"])
        w.writeheader()
        for c in picked:
            w.writerow({"sample_id": sample_id, "category_id": category, "chunk_id": c["chunk_id"],
                        "text": c["content"], "labels": ""})
    write_event(cat, "govern.pii_label_export", actor=actor, charter_id=sc.charter_id, policy=category,
                sample_id=sample_id, size=len(picked))
    return {"sample_id": sample_id, "size": len(picked)}


def import_labels(cat: Catalog, sc: Scope, path: str | Path, actor: str, cfg: dict) -> dict:
    """Store labels as offsets (values are located in the chunk, never stored) and evaluate the gate."""
    rows = list(csv.DictReader(Path(path).open(encoding="utf-8")))
    if not rows:
        raise Refused(f"{path}: no labelled rows")
    sample_id, category = rows[0]["sample_id"], rows[0]["category_id"]
    errs, labels = [], []
    for r in rows:
        c = cat.one("SELECT content FROM chunk WHERE chunk_id = ?", (r["chunk_id"],))
        if c is None:
            errs.append(f"unknown chunk {r['chunk_id']}")
            continue
        for part in filter(None, (p.strip() for p in (r.get("labels") or "").split(";"))):
            ent, _, value = part.partition("=")
            hits = [m.start() for m in re.finditer(re.escape(value.strip()), c["content"])] if value.strip() else []
            if not hits:
                errs.append(f"{r['chunk_id']}: label {ent.strip()} not found in the chunk text")
            labels += [(sample_id, r["chunk_id"], ent.strip(), h, h + len(value.strip())) for h in hits]
    if errs:
        raise Refused(errs)
    cat.execute("DELETE FROM pii_label WHERE sample_id = ?", (sample_id,))
    if labels:
        cat.many("INSERT INTO pii_label VALUES (?, ?, ?, ?, ?)", labels)
    return evaluate(cat, sc, category, sample_id, [r["chunk_id"] for r in rows], actor, cfg)


def evaluate(cat: Catalog, sc: Scope, category: str, sample_id: str, chunk_ids: list[str], actor: str,
             cfg: dict) -> dict:
    dv, thr, min_n = detector_version(), cfg["pii"]["recall_threshold"], cfg["pii"]["min_sample"]
    unscanned = [c for c in chunk_ids if (cat.one("SELECT detector_version FROM pii_scan WHERE chunk_id = ?", (c,))
                                          or {}).get("detector_version") != dv]
    if unscanned:
        raise Refused(f"{len(unscanned)} sample chunk(s) are not scanned with the current detector; run "
                      "'konmari govern scan' first")
    stats, missed = {}, []
    for lb in cat.query("SELECT * FROM pii_label WHERE sample_id = ?", (sample_id,)):
        hit = any(f["entity_type"] == lb["entity_type"] and f["start_pos"] < lb["end_pos"] and
                  lb["start_pos"] < f["end_pos"] for f in findings(cat, lb["chunk_id"]))
        s = stats.setdefault(lb["entity_type"], {"labelled": 0, "detected": 0})
        s["labelled"] += 1
        s["detected"] += hit
        if not hit:
            missed.append(f"{lb['chunk_id']}:{lb['start_pos']}-{lb['end_pos']}:{lb['entity_type']}")
    for s in stats.values():
        s["recall"] = round(s["detected"] / s["labelled"], 4)
    reasons = [f"sample has {len(chunk_ids)} items; at least {min_n} are needed"] if len(chunk_ids) < min_n else []
    reasons += [f"{e} recall {stats[e]['recall']} < {thr}" for e in DIRECT if e in stats and stats[e]["recall"] < thr]
    res = {"result_id": new_id(), "policy_id": category, "sample_id": sample_id, "sample_size": len(chunk_ids),
           "detector_version": dv, "recall": json.dumps(stats, sort_keys=True), "threshold": thr,
           "passed": not reasons, "missed": json.dumps(missed), "created_by": actor, "created_at": now()}
    cat.execute(f"INSERT INTO pii_gate_result VALUES ({', '.join('?' * len(res))})", tuple(res.values()))
    write_event(cat, "govern.pii_gate", actor=actor, charter_id=sc.charter_id, policy=category, sample_id=sample_id,
                passed=res["passed"], reasons=reasons, detector_version=dv)
    return {**res, "recall": stats, "missed": missed, "reasons": reasons}


def gate_state(cat: Catalog, category: str) -> str:
    """passed | failed | outdated (detector changed) | missing."""
    r = cat.one("SELECT * FROM pii_gate_result WHERE policy_id = ? ORDER BY created_at DESC LIMIT 1", (category,))
    if r is None:
        return "missing"
    if r["detector_version"] != detector_version():
        return "outdated"
    return "passed" if r["passed"] else "failed"
