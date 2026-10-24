"""Judges for the model-judged part of the evaluation (design D1, D2, D4): claim splitting and entailment.
The built-in lexical judge is deterministic and offline; the Ragas judge uses Ragas' faithfulness metric on a
self-hosted or contracted OpenAI-compatible endpoint (`uv sync --extra ragas`). Calibration: Cohen's kappa."""

import json
import os
import re
from collections import Counter

from konmari.catalog import Catalog, new_id, now
from konmari.charter.store import Scope
from konmari.evaluate.golden import event
from konmari.versions import Refused

MIN_CALIBRATION, MIN_KAPPA = 50, 0.6  # assumptions (design D4); a charter may raise them
_SENTENCE = re.compile(r"(?<=[^\d\s][.!?])\s+|\n+")  # "31. Januar": no split after an ordinal


def claims(answer: str) -> list[str]:
    """One claim per sentence. shortcut: no LLM claim decomposition; compound sentences count as one claim."""
    return [s.strip() for s in _SENTENCE.split(answer or "") if s.strip() and re.search(r"\w", s)]


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.casefold()) if len(w) >= 4 or w.isdigit()}


class LexicalJudge:
    """A claim is supported when every content word (4+ letters, or a number) occurs in the passages."""
    provider, model, version = "lexical", "konmari-lexical", "1"

    def supports(self, statement: str, passages: list[str]) -> bool:
        need = _words(statement)
        return bool(passages) and need <= _words(" ".join(passages))


class RagasJudge:
    """Entailment with Ragas' faithfulness metric: a statement is supported when its faithfulness against the
    passages is 1.0. The model is pinned by `model` and `version` in eval.yaml."""
    provider = "ragas"

    def __init__(self, model: str, version: str, base_url: str | None = None, api_key_env: str = "KONMARI_JUDGE_KEY"):
        from openai import AsyncOpenAI
        from ragas.llms import llm_factory
        from ragas.metrics.collections import Faithfulness

        self.model, self.version = model, version
        client = AsyncOpenAI(base_url=base_url, api_key=os.environ.get(api_key_env) or "not-needed")
        self.metric = Faithfulness(llm=llm_factory(model, client=client))

    def supports(self, statement: str, passages: list[str]) -> bool:
        import anyio

        if not passages:
            return False

        async def go():
            return await self.metric.ascore(user_input="", response=statement, retrieved_contexts=passages)
        return float(anyio.run(go).value) >= 0.999


def make(cfg: dict | None):
    cfg = cfg or {}
    provider = cfg.get("provider", "lexical")
    if provider == "lexical":
        return LexicalJudge()
    if provider == "ragas":
        for k in ("model", "version"):
            if not cfg.get(k):
                raise Refused(f"judge.{k} is required for the ragas judge (pin the judge model)")
        return RagasJudge(cfg["model"], str(cfg["version"]), cfg.get("base_url"), cfg.get("api_key_env",
                                                                                        "KONMARI_JUDGE_KEY"))
    raise Refused(f"unknown judge provider '{provider}' (lexical, ragas)")


# ---------------------------------------------------------------- calibration

def kappa(pairs: list[tuple[str, str]]) -> tuple[float, dict]:
    """Cohen's kappa of (human, judge) label pairs and the confusion matrix {human: {judge: count}}."""
    n = len(pairs)
    labels = sorted({x for p in pairs for x in p})
    conf = {h: {j: 0 for j in labels} for h in labels}
    for h, j in pairs:
        conf[h][j] += 1
    po = sum(conf[x][x] for x in labels) / n
    hc, jc = Counter(h for h, _ in pairs), Counter(j for _, j in pairs)
    pe = sum(hc[x] * jc[x] for x in labels) / (n * n)
    return (1.0 if po == 1 else 0.0) if pe == 1 else round((po - pe) / (1 - pe), 4), conf


def calibrate(cat: Catalog, sc: Scope, rows: list[dict], model: str, version: str, actor: str,
              tuning_ids: set[str] = frozenset()) -> dict:
    """rows: [{item_id, human, judge}] on a held-out subset never used to tune the judge prompt."""
    ids = [r["item_id"] for r in rows]
    if len(set(ids)) < MIN_CALIBRATION:
        raise Refused(f"{len(set(ids))} calibration items, at least {MIN_CALIBRATION} human-labelled items are "
                      "required")
    if overlap := sorted(set(ids) & set(tuning_ids)):
        raise Refused(f"calibration items were used to tune the judge prompt: {', '.join(overlap)}")
    k, conf = kappa([(str(r["human"]).strip(), str(r["judge"]).strip()) for r in rows])
    status = "calibrated" if k >= MIN_KAPPA else "uncalibrated"
    cid = new_id()
    cat.execute("INSERT INTO judge_calibration VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (cid, sc.charter_id, model, version, len(rows), k, json.dumps(conf), status, actor, now()))
    event(cat, "eval.judge.calibrate", actor, sc, calibration_id=cid, judge_model=model, judge_version=version,
          items=len(rows), kappa=k, status=status)
    return {"calibration_id": cid, "kappa": k, "confusion": conf, "status": status, "items": len(rows)}


def status(cat: Catalog, charter_id: str, model: str, version: str) -> str:
    """`calibrated` only when the latest calibration used this judge model and version and reached the kappa."""
    r = cat.one("SELECT * FROM judge_calibration WHERE charter_id = ? ORDER BY created_at DESC LIMIT 1",
                (charter_id,))
    ok = r and r["judge_model"] == model and r["judge_version"] == version and r["kappa"] >= MIN_KAPPA
    return "calibrated" if ok else "uncalibrated"
