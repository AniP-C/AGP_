"""LLM adjudication — batched, cached, strictly structured.

The model is asked *which candidate block(s) contain the answer*, never to
produce an answer. Results are cached by payload hash for reproducibility.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from .. import PROMPT_VERSION
from ..prompts import load_prompt
from ..schemas import LLMAssociation

log = logging.getLogger(__name__)
_BATCH = 4


def _cache(cache_dir, key):
    if not cache_dir:
        return None
    f = Path(cache_dir) / "assoc" / f"{key}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def _cache_put(cache_dir, key, data):
    if not cache_dir or data is None:
        return
    d = Path(cache_dir) / "assoc"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")


def adjudicate(items: list[dict], provider, cache_dir=None, model=None) -> dict[str, LLMAssociation]:
    """items: [{question_id, question_text, question_type, candidates:[{block_id,type,relationship,page,text}]}]"""
    out: dict[str, LLMAssociation] = {}
    for s in range(0, len(items), _BATCH):
        batch = items[s:s + _BATCH]
        payload = json.dumps(batch, ensure_ascii=False)
        prompt = load_prompt("answer_associate").format(payload=payload)
        key = hashlib.sha256((PROMPT_VERSION + (model or "") + prompt).encode()).hexdigest()[:24]
        data = _cache(cache_dir, key)
        if data is None:
            data = provider.generate_json(prompt, model=model) if model else provider.generate_json(prompt)
            _cache_put(cache_dir, key, data)
        if not isinstance(data, dict):
            continue
        for a in data.get("associations", []):
            if not isinstance(a, dict) or not a.get("question_id"):
                continue
            try:
                out[a["question_id"]] = LLMAssociation(
                    status=a.get("status", "ambiguous"),
                    answer_block_ids=a.get("answer_block_ids", []) or [],
                    evidence_block_ids=a.get("evidence_block_ids", []) or [],
                    reason=a.get("reason", "")[:300],
                    llm_confidence=float(a.get("llm_confidence", 0.0) or 0.0),
                )
            except Exception as exc:
                log.debug("bad association entry: %s", exc)
    return out
