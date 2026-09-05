"""Calibrated confidence + the confidence gate.

This is a TRANSPARENT, DETERMINISTIC prototype calibration (a learned calibrator
would need labelled data we don't have for a POC). It is NOT an average of the
signals: structural relationship reliability is the backbone, retrieval
corroboration and the competing-evidence margin adjust it within bands, and the
LLM can only *moderate* (never dominate) a decision it was asked to adjudicate.

Three confidences are returned separately:
  model_self_reported (LLM), system_evidence_score (deterministic), calibrated.
"""
from __future__ import annotations

# deterministic reliability of each structural relationship (the backbone)
_REL_BASE = {
    "inline": 0.98, "same_worked_example": 0.95, "same_container_enum": 0.95,
    "adjacent_solution": 0.90, "continuation": 0.85, "same_container": 0.82,
    "refers_to": 0.80, "same_section": 0.60,
}
_POSITIVE = set(_REL_BASE)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, round(x, 3)))


def calibrate(assoc) -> tuple[float, float | None, float]:
    """Return (system_evidence_score, model_self_reported, calibrated_confidence)."""
    model = assoc.llm.llm_confidence if assoc.llm else None

    if assoc.status == "not_in_document":
        # confidence in the ABSTENTION: high when there was no in-scope candidate
        sys = 0.9 if assoc.method == "no_candidate" else 0.65
        return sys, model, sys
    if assoc.status == "ambiguous" or not assoc.answer_block_ids:
        return 0.4, model, 0.4

    chosen = set(assoc.answer_block_ids)
    chosen_ev = [e for e in assoc.evidence if e.candidate_block_id in chosen]
    rel = chosen_ev[0].relationship if chosen_ev else assoc.evidence[0].relationship if assoc.evidence else "none"
    base = _REL_BASE.get(rel, 0.5)

    # corroboration: does an independent channel also point at the chosen block?
    corrob = 0.0
    if chosen_ev:
        chans = set().union(*[set(e.channels) for e in chosen_ev])
        corrob += 0.03 if "semantic" in chans else 0.0
        corrob += 0.02 if "lexical" in chans else 0.0

    # competing-evidence margin: a rival in-scope candidate of similar strength
    rivals = [e.structural_strength for e in assoc.evidence
              if e.candidate_block_id not in chosen and e.scope == "in_scope"
              and e.relationship in _POSITIVE]
    margin = base - (max(rivals) if rivals else 0.0)
    margin_adj = 0.0 if margin >= 0.1 else -0.15 * (0.1 - margin) / 0.1

    sys = _clamp(base + corrob + margin_adj)

    if assoc.method in ("inline", "structural"):        # deterministic decision
        final = sys
    else:                                                # LLM adjudicated → moderate
        final = _clamp(0.6 * sys + 0.4 * (model if model is not None else sys))
    return sys, model, final


def gate(calibrated: float, findings, status: str) -> tuple[str, str]:
    """Map calibrated confidence + validation findings → (tier, action)."""
    has_error = any(f.severity == "error" for f in findings)

    if status == "not_in_document":
        # accept a *confident* abstention; a shaky one is escalated to double-check
        if has_error:
            return "low", "escalate"
        return ("high", "accept") if calibrated >= 0.8 else ("medium", "escalate")

    if has_error:
        return "low", "escalate"
    if calibrated >= 0.85:
        return "high", "accept"
    if calibrated >= 0.60:
        return "medium", "escalate"
    return "low", "abstain"
