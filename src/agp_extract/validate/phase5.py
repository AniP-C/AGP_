"""Phase 5 orchestrator — validation → calibration → gate → bounded escalation.

Builds only on existing artifacts (blocks, graph, questions, evidence,
ANSWERED_BY). Prefers abstention over unsupported association.
"""
from __future__ import annotations

import collections
import logging

from .. import metrics
from ..discover import walk
from ..schemas import EdgeType, NumericCheck, QuestionValidation, ValidationFinding
from .calibration import calibrate, gate
from .checks import multimodal_validate, qa_validate
from .escalate import escalate
from .numeric import validate_numeric

log = logging.getLogger(__name__)


def run_validation(doc, provider=None, cache_dir=None, graph=None, settings=None,
                   pages_by_index=None) -> dict:
    byid = {b.id: b for b in doc.blocks}
    leaves = [q for q in walk(doc.questions) if not q.sub_questions]

    numeric_findings, block_checks = validate_numeric(doc)
    numeric_by_block = {
        bid: NumericCheck(status="failed_source" if any(not c["ok"] for c in checks) else "passed",
                          checks=checks)
        for bid, checks in block_checks.items()}
    nf_by_block: dict[str, list] = collections.defaultdict(list)
    for f in numeric_findings:
        nf_by_block[f.target_id].append(f)

    for q in leaves:
        a = q.answer_association
        v = QuestionValidation()
        findings = qa_validate(q, byid) + multimodal_validate(q, byid)
        num = None
        if a and a.answer_block_ids:
            for bid in a.answer_block_ids:
                if bid in numeric_by_block:
                    num = numeric_by_block[bid]
                for f in nf_by_block.get(bid, []):
                    findings.append(ValidationFinding(
                        check=f.check, severity=f.severity, target_type="question",
                        target_id=q.id, message=f.message))
        sysv, model, cal = calibrate(a) if a else (0.0, None, 0.0)
        v.system_evidence_score, v.model_self_reported, v.calibrated_confidence = sysv, model, cal
        v.findings = findings
        v.numeric = num
        v.gate_tier, v.gate_action = gate(cal, findings, a.status if a else "not_in_document")
        q.validation = v

    esc = escalate(doc, leaves, byid, provider, cache_dir, settings,
                   pages_by_index or {}, numeric_findings)
    _materialize_recovered(doc, leaves, graph)

    stats = _stats(doc, leaves, numeric_findings, esc)
    stats["cost"] = metrics.snapshot()
    doc.stats["phase5"] = stats
    return stats


def _materialize_recovered(doc, leaves, graph) -> None:
    if graph is None:
        return
    existing = {(e.src, e.dst) for e in graph.edges if e.type == EdgeType.ANSWERED_BY}
    for q in leaves:
        a = q.answer_association
        if not a or a.status not in ("matched", "partial") or not a.answer_block_ids:
            continue
        qb = q.provenance.source_blocks[0] if q.provenance.source_blocks else None
        if not qb:
            continue
        for abid in a.answer_block_ids:
            if abid == qb or (qb, abid) in existing:
                continue
            graph.add_edge(qb, abid, EdgeType.ANSWERED_BY, question_id=q.id,
                           status=a.status, relationship="recovered",
                           final_confidence=q.validation.calibrated_confidence)
            existing.add((qb, abid))


def _stats(doc, leaves, numeric_findings, esc) -> dict:
    tiers = collections.Counter(q.validation.gate_tier for q in leaves)
    actions = collections.Counter(q.validation.gate_action for q in leaves)
    all_findings = [f for q in leaves for f in q.validation.findings]
    errors = [f for f in all_findings if f.severity == "error"]
    escalated = [q for q in leaves if q.validation.escalations]
    status = collections.Counter(q.answer_association.status for q in leaves if q.answer_association)
    n = len(leaves)
    return {
        "leaf_questions": n,
        "gate_tiers": dict(tiers),
        "gate_actions": dict(actions),
        # accuracy vs coverage tradeoff
        "high_confidence_accepted": actions.get("accept", 0),
        "escalated": len(escalated),
        "abstained": actions.get("abstain", 0) + status.get("ambiguous", 0),
        "final_status": dict(status),
        # validation outcomes
        "validation_errors_caught": len(errors),
        "numeric_inconsistencies": len(numeric_findings),
        "errors_recovered": esc.get("recovered", 0),
        "retry_rate_pct": round(100 * len(escalated) / max(1, n), 1),
        "escalation": esc,
        "calibration_note": "transparent deterministic prototype (structural backbone; "
                            "LLM moderates only; not a learned calibrator)",
    }
