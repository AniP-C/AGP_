"""Bounded, targeted escalation — reprocess only the affected question/block.

Three moves, each capped and recorded (attempt / reason / prev / new / decision):
  1. deterministic recovery of a `not_in_document` whose in-scope container answer
     block literally contains the subpart's enumerated answer (Phase-4 LLM miss);
  2. pro-model re-adjudication of medium-confidence associations (expanded evidence);
  3. pro-vision re-extraction of a numeric-flagged equation region (records a
     correction candidate; never overwrites the source block).
No book-wide reruns.
"""
from __future__ import annotations

import logging
import re

from ..associate.llm_associate import adjudicate
from ..schemas import RetryRecord
from .calibration import calibrate, gate

log = logging.getLogger(__name__)
_MAX_READJUDICATE = 20
_MAX_REEXTRACT_PAGES = 2


def _revalidate(q, findings):
    sysv, model, cal = calibrate(q.answer_association)
    v = q.validation
    v.system_evidence_score, v.model_self_reported, v.calibrated_confidence = sysv, model, cal
    v.gate_tier, v.gate_action = gate(cal, findings, q.answer_association.status)


def escalate(doc, leaves, byid, provider, cache_dir, settings, pages_by_index,
             numeric_findings) -> dict:
    n_readj = n_recovered = n_reextract = n_unchanged = n_confirmed = 0

    # ── 1 & 2: association escalation ────────────────────────────────────
    esc = [q for q in leaves if q.validation and q.validation.gate_action == "escalate"]
    readjudicate_items, readjudicate_qs = [], []
    for q in esc:
        a = q.answer_association
        # (1) deterministic recovery: enum answer present in an in-scope block
        if a.status == "not_in_document" and q.question_number:
            hit = None
            for e in a.evidence:
                if e.scope == "in_scope" and e.relationship in _POS:
                    b = byid.get(e.candidate_block_id)
                    if b and re.search(rf"\(\s*{re.escape(q.question_number)}\s*\)", b.text or ""):
                        hit = (b, e)
                        break
            if hit:
                b, e = hit
                prev = a.status
                a.status, a.answer_state, a.method = "matched", "present", "structural"
                a.answer_block_ids = [b.id]
                a.answer_kind = "table" if b.type.value == "table" else "text"
                a.answer_preview = (b.text or b.caption or "")[:400]
                q.validation.escalations.append(RetryRecord(
                    attempt=1, kind="re_adjudicate", reason="not_in_document but enum answer present in in-scope block",
                    previous_status=prev, new_status="matched", model="deterministic",
                    decision="recovered"))
                _revalidate(q, q.validation.findings)
                n_recovered += 1
                continue
        # (2) queue for pro re-adjudication (bounded)
        if len(readjudicate_items) < _MAX_READJUDICATE and a.status in ("matched", "partial", "ambiguous", "not_in_document"):
            ins = [e for e in a.evidence if e.scope == "in_scope" and e.relationship in _POS]
            if ins:
                readjudicate_items.append({
                    "question_id": q.id, "question_text": (q.question_text or "")[:400],
                    "question_type": q.question_type,
                    "candidates": [{"block_id": e.candidate_block_id, "type": e.block_type,
                                    "relationship": e.relationship, "page": e.page_index,
                                    "text": (byid[e.candidate_block_id].text
                                             or byid[e.candidate_block_id].caption or "")[:220]}
                                   for e in ins[:8]]})
                readjudicate_qs.append(q)

    if readjudicate_items and provider is not None and getattr(provider, "name", "null") != "null":
        pro = settings.vision_escalation_model
        results = adjudicate(readjudicate_items, provider, cache_dir, model=pro)
        for q in readjudicate_qs:
            r = results.get(q.id)
            a = q.answer_association
            ins_ids = {e.candidate_block_id for e in a.evidence
                       if e.scope == "in_scope" and e.relationship in _POS}
            prev = a.status
            if r and r.status == "matched" and any(b in ins_ids for b in r.answer_block_ids):
                valid = [b for b in r.answer_block_ids if b in ins_ids]
                a.status, a.answer_state, a.method = "matched", "present", "llm_adjudicated"
                a.answer_block_ids = valid
                a.answer_preview = " ".join((byid[b].text or byid[b].caption or "") for b in valid)[:400] or a.answer_preview
                if a.llm:
                    a.llm.status, a.llm.llm_confidence = "matched", r.llm_confidence
                # honest accounting: status IMPROVED = recovered; already matched = confirmed
                if prev != "matched":
                    decision = "recovered"; n_recovered += 1
                else:
                    decision = "confirmed"; n_confirmed += 1
            else:
                decision = "unchanged"
                n_unchanged += 1
            q.validation.escalations.append(RetryRecord(
                attempt=1, kind="re_adjudicate", reason="medium-confidence → pro re-adjudication",
                previous_status=prev, new_status=a.status, model=pro, decision=decision))
            _revalidate(q, q.validation.findings)
            n_readj += 1

    # ── 3: numeric re-extraction (pro vision) ────────────────────────────
    reext = _numeric_reextract(doc, byid, provider, settings, pages_by_index, numeric_findings)
    n_reextract = reext

    return {
        "escalated_questions": len(esc),
        "re_adjudicated": n_readj,
        "recovered": n_recovered,         # status genuinely improved
        "confirmed": n_confirmed,         # already matched; pro re-confirmed
        "unchanged": n_unchanged,
        "numeric_reextractions": n_reextract,
        "max_readjudicate_cap": _MAX_READJUDICATE,
    }


def _numeric_reextract(doc, byid, provider, settings, pages_by_index, numeric_findings) -> int:
    if provider is None or getattr(provider, "name", "null") == "null":
        return 0
    pages = []
    for f in numeric_findings:
        b = byid.get(f.target_id)
        if b and b.provenance.page_index not in pages:
            pages.append(b.provenance.page_index)
    done = 0
    doc.stats.setdefault("phase5_numeric_reextract", [])
    from .numeric import _check_equalities
    for pi in pages[:_MAX_REEXTRACT_PAGES]:
        lp = pages_by_index.get(pi)
        if lp is None:
            continue
        # operands from the flagged expression(s) on this page
        flagged = " ".join(f.message for f in numeric_findings
                           if byid.get(f.target_id) and byid[f.target_id].provenance.page_index == pi)
        operands = set(re.findall(r"\d+\.?\d*", flagged))
        pe = provider.extract_page(lp.image_bytes, lp.media_type, pi, lp.width, lp.height,
                                   model=settings.vision_escalation_model)
        recovered = []
        for rb in pe.blocks:
            for c in _check_equalities(" ".join(x for x in (rb.latex, rb.text) if x)):
                if c["ok"] and any(o in c["expr"] for o in operands):
                    recovered.append({"expr": c["expr"], "computed": c["computed"]})
        doc.stats["phase5_numeric_reextract"].append(
            {"page_index": pi, "escalation_model": settings.vision_escalation_model,
             "recovered_consistent_expressions": recovered[:3],
             "note": "source block preserved; recovered value recorded as correction candidate only"})
        done += 1
    return done


_POS = {"inline", "same_worked_example", "same_container_enum", "same_container",
        "adjacent_solution", "refers_to", "continuation", "same_section"}
