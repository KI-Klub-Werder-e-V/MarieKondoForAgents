"""Local, sample-based personal-data hints with Presidio and a German spaCy model (design decision 5).

Only entity type, hit count and maximum score leave this module; matched values never do.
"""

import os
import re
from functools import cache
from pathlib import Path

from konmari.versions import Refused

DEFAULT_MODEL = "de_core_news_lg"
DEFAULT_THRESHOLD = 0.5  # our assumption (R §4)
DEFAULT_SAMPLE = 200     # items or rows per asset, our assumption (R §4)
TIERS = ["public", "internal", "confidential", "personal", "special-category"]

# entity type -> personal-data category; only these entities are kept as hints
ENTITIES = {
    "PERSON": "name", "EMAIL_ADDRESS": "contact", "PHONE_NUMBER": "contact", "IBAN_CODE": "financial",
    "IP_ADDRESS": "online-identifier", "DE_TAX_ID": "national-identifier", "DE_TAX_NUMBER": "national-identifier",
    "DE_ID_CARD": "national-identifier", "DE_PASSPORT": "national-identifier",
    "DE_SOCIAL_SECURITY": "national-identifier", "DE_FUEHRERSCHEIN": "national-identifier",
    "DE_HEALTH_INSURANCE": "health", "DE_PLZ": "address",
}
SPECIAL = {"DE_HEALTH_INSURANCE"}  # GDPR Art. 9 health data
# Postal codes alone are years, prices, order numbers; count them only next to an address word.
_ADDRESS = re.compile(r"(stra(ss|ß)e|str\.|weg\b|platz\b|allee\b|gasse\b|ring\b|anschrift|adresse|wohnort|\bplz\b)", re.I)


def model_name() -> str:
    return os.environ.get("KONMARI_NLP_MODEL", DEFAULT_MODEL)


@cache
def _analyzer(model: str):
    import spacy
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer import predefined_recognizers as pr
    from presidio_analyzer.nlp_engine import SpacyNlpEngine

    # Presidio would otherwise download a missing model: that is an outbound call, so refuse instead.
    if not (spacy.util.is_package(model) or Path(model).exists()):
        raise Refused(f"spaCy model '{model}' is not installed; run 'uv run python -m spacy download {model}' "
                     "once, or set KONMARI_NLP_MODEL to an installed model")
    nlp = SpacyNlpEngine(models=[{"lang_code": "de", "model_name": model}])
    nlp.load()
    reg = RecognizerRegistry(supported_languages=["de"])
    reg.load_predefined_recognizers(languages=["de"], nlp_engine=nlp)
    for name in dir(pr):  # the German recognizers ship disabled by default
        if name.startswith("De") and name.endswith("Recognizer"):
            reg.add_recognizer(getattr(pr, name)())
    return AnalyzerEngine(nlp_engine=nlp, registry=reg, supported_languages=["de"])


def analyze(texts, threshold: float = DEFAULT_THRESHOLD, model: str | None = None) -> dict[str, dict]:
    """Return {entity_type: {"count": n, "max_score": s}} over the sampled texts."""
    an = _analyzer(model or model_name())
    out: dict[str, dict] = {}
    for text in texts:
        for r in an.analyze(text=text, language="de", entities=list(ENTITIES)):
            score = r.score
            if r.entity_type == "DE_PLZ":
                if not _ADDRESS.search(text[max(0, r.start - 60):r.end + 30]):
                    continue
                score = max(score, threshold)  # shortcut: address context lifts the 0.05 base score to the threshold
            if score < threshold:
                continue
            h = out.setdefault(r.entity_type, {"count": 0, "max_score": 0.0})
            h["count"] += 1
            h["max_score"] = max(h["max_score"], round(score, 4))
    return out


def hint_tier(entity_types) -> str | None:
    """The lowest tier the hints support; proposals never go below it."""
    types = set(entity_types)
    if types & SPECIAL:
        return "special-category"
    return "personal" if types else None


def categories(entity_types) -> list[str]:
    return sorted({ENTITIES[t] for t in entity_types if t in ENTITIES})


def rank(tier: str | None) -> int:
    return TIERS.index(tier) if tier in TIERS else -1
