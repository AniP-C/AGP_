"""Phase 3 orchestrator — question discovery & normalization.

Deterministic structural discovery first; an optional, targeted LLM pass then
refines ``unknown`` types and finds in-text questions in prose. No cross-block
answer association (that is Phase 4).
"""
from __future__ import annotations

import collections
import logging
from typing import Iterator, Optional

from ..schemas import CanonicalDocument, Question
from .builder import build_questions
from .context import build_context

log = logging.getLogger(__name__)


def walk(questions: list[Question]) -> Iterator[Question]:
    for q in questions:
        yield q
        yield from walk(q.sub_questions)


def _link_or_groups(questions: list[Question]) -> int:
    groups: dict[str, list[Question]] = collections.defaultdict(list)
    for q in walk(questions):
        if q.internal_choice:
            groups[q.internal_choice.group_id].append(q)
    for members in groups.values():
        ids = [m.id for m in members]
        for m in members:
            m.internal_choice.alternative_question_ids = [i for i in ids if i != m.id]
    return len(groups)


def _stats(questions: list[Question]) -> dict:
    nodes = list(walk(questions))
    by_src = collections.Counter(q.source_type for q in nodes)
    by_type = collections.Counter(q.question_type for q in nodes)
    with_marks = [q for q in nodes if q.marks and q.marks.value is not None]
    marks_src = collections.Counter(q.marks.source for q in with_marks)
    return {
        "root_questions": len(questions),
        "total_logical_questions": len(nodes),
        "with_sub_questions": sum(1 for q in nodes if q.sub_questions),
        "max_nesting_depth": _depth(questions),
        "by_source_type": dict(by_src.most_common()),
        "by_question_type": dict(by_type.most_common()),
        "with_marks": len(with_marks),
        "marks_by_source": dict(marks_src),
        "with_bloom": sum(1 for q in nodes if q.bloom_level),
        "with_source_refs": sum(1 for q in nodes if q.source_refs),
        "with_inline_answer": sum(1 for q in nodes if q.answer_inline),
        "with_related_assets": sum(1 for q in nodes if q.related_assets),
        "internal_choice_groups": len({q.internal_choice.group_id
                                       for q in nodes if q.internal_choice}),
        "unknown_type": sum(1 for q in nodes if q.question_type == "unknown"),
        "provenance_coverage_pct": round(
            100 * sum(1 for q in nodes if q.provenance.source_blocks) / max(1, len(nodes)), 1),
    }


def _depth(questions: list[Question], d: int = 1) -> int:
    return max([d] + [_depth(q.sub_questions, d + 1) for q in questions if q.sub_questions],
               default=d)


def run_discovery(doc: CanonicalDocument, provider=None, cache_dir=None,
                  use_llm: bool = True, settings=None) -> dict:
    region, banner_marks = build_context(doc)
    questions, recon_stats = build_questions(
        doc, region, banner_marks, settings=settings)
    _link_or_groups(questions)

    llm_stats: Optional[dict] = None
    if use_llm and provider is not None and getattr(provider, "name", "null") != "null":
        try:
            from .llm_refine import refine_with_llm
            llm_stats = refine_with_llm(doc, questions, provider, cache_dir)
            _link_or_groups(questions)   # in-text additions may join groups
        except Exception as exc:            # never let the LLM step break discovery
            log.warning("LLM refinement skipped: %s", exc)
            llm_stats = {"status": "error", "error": str(exc)}

    doc.questions = questions
    stats = _stats(questions)
    stats["reconstruction"] = recon_stats
    if llm_stats is not None:
        stats["llm"] = llm_stats
    doc.stats["phase3"] = stats
    return stats
