"""Per-question validators for accepted answer associations, and multimodal
(table/figure/equation) reference validation. "The model says it's correct" is
never sufficient — every check here is deterministic and structural.
"""
from __future__ import annotations

from ..schemas import BlockType, ValidationFinding

_POSITIVE = {"inline", "same_worked_example", "same_container_enum",
             "same_container", "adjacent_solution", "refers_to",
             "continuation", "same_section"}
_ASSET_ANSWER = {"table", "equation"}


def qa_validate(q, byid) -> list[ValidationFinding]:
    a = q.answer_association
    out: list[ValidationFinding] = []
    if not a or a.status not in ("matched", "partial"):
        return out
    if a.method == "inline":
        return out  # same-block; scope trivially holds
    qb = byid.get(q.provenance.source_blocks[0]) if q.provenance.source_blocks else None
    chosen = a.answer_block_ids
    chosen_ev = {e.candidate_block_id: e for e in a.evidence}

    for bid in chosen:
        ev = chosen_ev.get(bid)
        b = byid.get(bid)
        # source-grounded + in-scope structural
        if b is None:
            out.append(_f("qa_dangling_answer", "error", q.id,
                          f"answer block {bid} does not exist"))
            continue
        if ev is None or ev.scope != "in_scope" or ev.relationship not in _POSITIVE:
            out.append(_f("qa_out_of_scope", "error", q.id,
                          f"answer {bid} lacks in-scope structural evidence"))
        # answer isn't actually another question
        if b.type == BlockType.QUESTION:
            out.append(_f("qa_answer_is_question", "error", q.id,
                          f"chosen answer {bid} is itself a question block"))
        # provenance exists
        if not b.provenance.source_image:
            out.append(_f("qa_answer_no_provenance", "warn", q.id,
                          f"answer {bid} missing source provenance"))
        # belongs to expected section/example
        if qb is not None and b.section_id != qb.section_id \
                and b.structure.part_of_id != qb.structure.part_of_id:
            out.append(_f("qa_cross_section", "warn", q.id,
                          f"answer {bid} is outside the question's section/example"))

    # no competing candidate materially stronger than the chosen one
    chosen_strength = max((chosen_ev[b].structural_strength for b in chosen
                           if b in chosen_ev), default=0.0)
    for e in a.evidence:
        if e.candidate_block_id in chosen or e.scope != "in_scope":
            continue
        if e.relationship in _POSITIVE and e.structural_strength > chosen_strength + 0.05:
            out.append(_f("qa_competing_stronger", "warn", q.id,
                          f"candidate {e.candidate_block_id} ({e.relationship}) has "
                          f"stronger evidence than the chosen answer"))
    if not (a.answer_preview or "").strip():
        out.append(_f("qa_unsupported", "error", q.id, "no source-grounded answer text"))
    return out


def multimodal_validate(q, byid) -> list[ValidationFinding]:
    a = q.answer_association
    out: list[ValidationFinding] = []
    if not a or a.answer_kind not in _ASSET_ANSWER:
        return out
    qb = byid.get(q.provenance.source_blocks[0]) if q.provenance.source_blocks else None
    for bid in a.answer_block_ids:
        b = byid.get(bid)
        if b is None or b.type.value not in _ASSET_ANSWER:
            continue
        # the referenced/answer asset must be in the question's structural context
        in_ctx = qb is not None and (
            b.section_id == qb.section_id
            or b.structure.part_of_id == qb.structure.part_of_id
            or bid in q.related_assets
            or any(r.resolved_block_id == bid for r in qb.refs if r.status == "resolved"))
        if not in_ctx:
            out.append(_f("mm_asset_out_of_context", "warn", q.id,
                          f"{b.type.value} answer {bid} not in the question's structural context"))
    return out


def _f(check, severity, qid, msg) -> ValidationFinding:
    return ValidationFinding(check=check, severity=severity,
                             target_type="question", target_id=qid, message=msg)
