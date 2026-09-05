"""Targeted, cached LLM refinement — the only place Phase 3 uses a model.

Two bounded jobs the deterministic engine can't do reliably:
  1. reclassify questions the cascade left as ``unknown``,
  2. detect genuine in-text questions embedded in prose (a "?" block that the
     structure didn't already capture).

Everything is provider-agnostic, JSON-structured, and cached by prompt hash so
runs stay reproducible. Failures degrade to the deterministic result.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

from .. import PROMPT_VERSION
from ..prompts import load_prompt
from ..schemas import (
    BlockType,
    CanonicalDocument,
    Question,
    QuestionEval,
    QuestionProvenance,
)
from .classify import classify_question_type
from .phase3 import walk

log = logging.getLogger(__name__)

_ALLOWED = {"mcq", "assertion_reason", "fill_blank", "true_false", "matching",
            "numerical", "distinguish", "ordering", "case_study", "short_answer",
            "long_answer", "conceptual", "activity", "in_text", "unknown"}
_MAX_INTEXT_CANDIDATES = 60


def _cache_get(cache_dir: Optional[Path], key: str):
    if not cache_dir:
        return None
    f = Path(cache_dir) / "llm" / f"{key}.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _cache_put(cache_dir: Optional[Path], key: str, data) -> None:
    if not cache_dir or data is None:
        return
    d = Path(cache_dir) / "llm"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{key}.json").write_text(json.dumps(data), encoding="utf-8")


def _call(provider, prompt: str, cache_dir):
    key = hashlib.sha256((PROMPT_VERSION + prompt).encode()).hexdigest()[:24]
    cached = _cache_get(cache_dir, key)
    if cached is not None:
        return cached
    data = provider.generate_json(prompt)
    _cache_put(cache_dir, key, data)
    return data


def refine_with_llm(doc: CanonicalDocument, questions: list[Question],
                    provider, cache_dir=None) -> dict:
    nodes = list(walk(questions))
    reclassified = _reclassify_unknown(nodes, provider, cache_dir)
    intext_added = _detect_intext(doc, questions, provider, cache_dir)
    return {
        "status": "ok",
        "reclassified_unknown": reclassified,
        "unknown_remaining": sum(1 for q in nodes if q.question_type == "unknown"),
        "intext_added": intext_added,
    }


def _reclassify_unknown(nodes, provider, cache_dir) -> int:
    unknown = [q for q in nodes if q.question_type == "unknown"]
    if not unknown:
        return 0
    items = json.dumps([{"id": q.id, "text": (q.question_text or "")[:600]}
                        for q in unknown], ensure_ascii=False)
    data = _call(provider, load_prompt("question_classify").format(items=items), cache_dir)
    if not isinstance(data, dict):
        return 0
    mapping = {c["id"]: c["question_type"] for c in data.get("classifications", [])
               if isinstance(c, dict) and c.get("id")}
    n = 0
    for q in unknown:
        t = mapping.get(q.id)
        if t in _ALLOWED and t != "unknown":
            q.question_type = t
            q.evaluation.classification_source = "llm"
            q.evaluation.classification_confidence = 0.7
            q.evaluation.type_evidence.append("llm_reclassified")
            n += 1
    return n


def _detect_intext(doc, questions, provider, cache_dir) -> int:
    covered = {bid for q in walk(questions) for bid in q.provenance.source_blocks}
    cands = []
    for b in sorted(doc.blocks, key=lambda x: x.provenance.reading_order):
        if b.id in covered or b.type not in (BlockType.PARAGRAPH, BlockType.CALLOUT,
                                             BlockType.NOTE, BlockType.ACTIVITY):
            continue
        t = (b.text or "").strip()
        if "?" in t and 20 <= len(t) <= 600:
            cands.append(b)
    cands = cands[:_MAX_INTEXT_CANDIDATES]
    if not cands:
        return 0
    blocks = json.dumps([{"id": b.id, "text": (b.text or "")[:400]} for b in cands],
                        ensure_ascii=False)
    data = _call(provider, load_prompt("intext_detect").format(blocks=blocks), cache_dir)
    if not isinstance(data, dict):
        return 0
    verdict = {r["id"]: r for r in data.get("results", [])
               if isinstance(r, dict) and r.get("id")}
    byid = {b.id: b for b in cands}
    added = 0
    n = len(list(walk(questions)))
    for bid, r in verdict.items():
        if not r.get("is_question") or bid not in byid:
            continue
        b = byid[bid]
        qtype = r.get("question_type")
        if qtype not in _ALLOWED or qtype == "unknown":
            qtype = classify_question_type(b.text or "")[0]
        added += 1
        questions.append(Question(
            id=f"Q_IT_{added:03d}", source_type="in_text", question_type=qtype,
            bloom_level=b.tags.get("bloom"), question_text=(b.text or "").strip(),
            source_refs=list(b.tags.get("source", []) or []),
            provenance=QuestionProvenance(
                source_pages=[b.provenance.page_index],
                printed_pages=[b.provenance.printed_page] if b.provenance.printed_page else [],
                source_blocks=[b.id], reading_order=b.provenance.reading_order),
            evaluation=QuestionEval(discovery_confidence=0.55,
                                    classification_confidence=0.6,
                                    classification_source="llm",
                                    type_evidence=["llm_intext"]),
        ))
    return added
