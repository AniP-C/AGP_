"""Phase 4 orchestrator — hybrid retrieval → adjudication → ANSWERED_BY.

Decision policy (false-positive-averse):
  * inline answer (Phase 3)                → matched / present (no model call)
  * NO in-scope structural candidate       → not_in_document (no model call)   ← QR self-assessment
  * one dominant in-scope structural cand  → matched / present (deterministic)
  * otherwise                              → LLM adjudicates among in-scope cands
A positive association ALWAYS requires in-scope structural/reference evidence;
semantic/lexical similarity alone can never create an ANSWERED_BY edge.
"""
from __future__ import annotations

import collections
import logging
from typing import Optional

from ..discover import walk
from ..retrieve import CandidateBuilder
from ..schemas import (
    AnswerAssociation,
    AnswerSignals,
    CanonicalDocument,
    EdgeType,
)
from .llm_associate import adjudicate

log = logging.getLogger(__name__)

POSITIVE_RELS = {"inline", "same_worked_example", "same_container_enum",
                 "same_container", "adjacent_solution", "refers_to",
                 "continuation", "same_section",
                 # a worked example's shared "Solution" block set, reached from
                 # the container rather than from each sub-part individually
                 "container_solution_enum", "container_solution_run"}
_STATE = {"matched": "present", "partial": "partial",
          "ambiguous": "not_in_document", "not_in_document": "not_in_document"}


def _dominant(ins) -> bool:
    """A single structural candidate strictly stronger than all others (e.g. a
    unique enumerator match) — reliable enough to accept without the LLM."""
    if not ins:
        return False
    top = ins[0].structural_strength
    n_at_top = sum(1 for e in ins if e.structural_strength >= top - 1e-6)
    return top >= 0.9 and n_at_top == 1


def run_association(doc: CanonicalDocument, provider=None, cache_dir=None,
                    graph=None, use_llm: bool = True) -> dict:
    byid = {b.id: b for b in doc.blocks}
    builder = CandidateBuilder(doc, provider, cache_dir)
    leaves = [q for q in walk(doc.questions) if not q.sub_questions]
    builder.warm_queries([q.question_text or "" for q in leaves])   # 1 batched embed

    cand: dict[str, list] = {}
    inscope: dict[str, list] = {}
    need_llm: list[dict] = []

    for q in leaves:
        ev = builder.build(q, k=6)
        cand[q.id] = ev
        ins = [e for e in ev if e.scope == "in_scope" and e.relationship in POSITIVE_RELS]
        inscope[q.id] = ins
        if q.answer_inline or not ins:
            continue
        if _dominant(ins):                 # unique top-tier structural → deterministic
            continue
        need_llm.append(_item(q, ins, byid))

    llm_results = {}
    if use_llm and provider is not None and getattr(provider, "name", "null") != "null" and need_llm:
        llm_results = adjudicate(need_llm, provider, cache_dir)

    for q in leaves:
        q.answer_association = _finalize(q, cand[q.id], inscope[q.id],
                                         llm_results.get(q.id), byid)

    n_edges = _materialize(doc, leaves, graph)
    stats = _stats(doc, leaves, builder, need_llm, n_edges)
    doc.stats["phase4"] = stats
    return stats


def _item(q, ins, byid) -> dict:
    return {
        "question_id": q.id,
        "question_text": (q.question_text or "")[:400],
        "question_type": q.question_type,
        "candidates": [{
            "block_id": e.candidate_block_id, "type": e.block_type,
            "relationship": e.relationship, "page": e.page_index,
            "text": (byid[e.candidate_block_id].text
                     or byid[e.candidate_block_id].caption or "")[:220],
        } for e in ins[:6]],
    }


def _answer_kind(block_types: set[str]) -> str:
    if "table" in block_types:
        return "table"
    if block_types == {"equation"}:
        return "equation"
    return "mixed" if len(block_types) > 1 else "text"


def _finalize(q, ev, ins, llm, byid) -> AnswerAssociation:
    top = ins[0] if ins else (ev[0] if ev else None)

    if q.answer_inline:
        qb = q.provenance.source_blocks[0]
        return AnswerAssociation(
            status="matched", answer_state="present", method="inline",
            answer_block_ids=[qb], answer_kind="inline",
            answer_preview=(q.answer_inline.text or "")[:400], evidence=ev,
            signals=AnswerSignals(structural_strength=1.0, final_confidence=0.95))

    if not ins:
        return AnswerAssociation(
            status="not_in_document", answer_state="not_in_document",
            method="no_candidate", evidence=ev,
            signals=AnswerSignals(
                lexical_score=(ev[0].lexical_score if ev else 0.0),
                semantic_score=(ev[0].semantic_score if ev else 0.0),
                final_confidence=0.9))

    ids = {e.candidate_block_id for e in ins}
    if llm is None:
        strong = _dominant(ins)
        status = "matched" if strong else "ambiguous"
        chosen = [top.candidate_block_id] if strong else []
        method, llm_obj, llm_conf = "structural", None, None
        final = top.structural_strength if strong else round(top.structural_strength * 0.6, 3)
    else:
        valid = [b for b in llm.answer_block_ids if b in ids]
        status, llm_conf, llm_obj, method = llm.status, llm.llm_confidence, llm, "llm_adjudicated"
        if status == "matched" and not valid:      # LLM chose out-of-scope → guard
            status = "ambiguous"
        chosen = valid if status in ("matched", "partial") else []
        final = round(0.5 * (top.structural_strength if chosen else 0.0) + 0.5 * llm_conf, 3)

    types = {byid[b].type.value for b in chosen if b in byid}
    preview = " ".join((byid[b].text or byid[b].caption or "") for b in chosen if b in byid)
    return AnswerAssociation(
        status=status, answer_state=_STATE.get(status, "not_in_document"), method=method,
        answer_block_ids=chosen, answer_kind=_answer_kind(types) if chosen else None,
        answer_preview=preview[:400] or None, evidence=ev,
        signals=AnswerSignals(
            structural_strength=top.structural_strength, lexical_score=top.lexical_score,
            semantic_score=top.semantic_score, rerank_score=top.rerank_score,
            llm_confidence=llm_conf, final_confidence=final),
        llm=llm_obj)


def _materialize(doc, leaves, graph) -> int:
    if graph is None:
        return 0
    n = 0
    for q in leaves:
        a = q.answer_association
        if not a or a.status not in ("matched", "partial") or not a.answer_block_ids:
            continue
        qb = q.provenance.source_blocks[0] if q.provenance.source_blocks else None
        if not qb:
            continue
        rel = a.evidence[0].relationship if a.evidence else "none"
        for abid in a.answer_block_ids:
            if abid == qb:            # inline / same block → no self-edge
                continue
            graph.add_edge(qb, abid, EdgeType.ANSWERED_BY,
                           question_id=q.id, status=a.status, relationship=rel,
                           final_confidence=a.signals.final_confidence,
                           evidence=[{"source": c, } for c in (a.evidence[0].channels if a.evidence else [])])
            n += 1
    return n


def _stats(doc, leaves, builder, need_llm, n_edges) -> dict:
    assoc = [q.answer_association for q in leaves if q.answer_association]
    by_status = collections.Counter(a.status for a in assoc)
    by_state = collections.Counter(a.answer_state for a in assoc)
    by_method = collections.Counter(a.method for a in assoc)
    chan = collections.Counter()
    for a in assoc:
        if a.status in ("matched", "partial") and a.evidence:
            chan.update(a.evidence[0].channels)
    kinds = collections.Counter(a.answer_kind for a in assoc
                                if a.status in ("matched", "partial") and a.answer_kind)
    return {
        "answerable_leaf_questions": len(leaves),
        "by_status": dict(by_status),
        "by_answer_state": dict(by_state),
        "by_method": dict(by_method),
        "answered_by_edges": n_edges,
        "matched": by_status.get("matched", 0),
        "not_in_document": by_status.get("not_in_document", 0),
        "partial": by_status.get("partial", 0),
        "ambiguous": by_status.get("ambiguous", 0),
        "answer_kind_distribution": dict(kinds),
        "evidence_source_distribution": dict(chan),
        "llm_adjudicated_questions": len(need_llm),
        "semantic_channel_enabled": builder.semantic_enabled,
        "provenance_coverage_pct": round(
            100 * sum(1 for a in assoc if a.evidence or a.status == "not_in_document")
            / max(1, len(assoc)), 1),
    }
