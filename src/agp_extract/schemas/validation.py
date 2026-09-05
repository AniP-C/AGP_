"""Phase 5 validation / calibration / gating schemas.

Three confidence notions are kept explicitly separate (per the brief):
  * model_self_reported  — the LLM's own number (weak prior)
  * system_evidence_score — deterministic, from structural/retrieval evidence
  * calibrated_confidence — the principled combination the gate acts on
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ValidationFinding(BaseModel):
    check: str
    severity: str                # info | warn | error
    target_type: str             # question | answer | block | page | edge | document
    target_id: Optional[str] = None
    message: str


class NumericCheck(BaseModel):
    status: str                  # passed | failed_source | na
    checks: list[dict] = Field(default_factory=list)   # [{expr, computed, printed, ok}]


class RetryRecord(BaseModel):
    attempt: int
    kind: str                    # re_adjudicate | re_extract
    reason: str
    previous_status: Optional[str] = None
    new_status: Optional[str] = None
    model: Optional[str] = None
    decision: str                # recovered | unchanged | abstained | flagged


class QuestionValidation(BaseModel):
    model_self_reported: Optional[float] = None
    system_evidence_score: float = 0.0
    calibrated_confidence: float = 0.0
    gate_tier: str = "n/a"       # high | medium | low | n/a
    gate_action: str = "n/a"     # accept | escalate | abstain
    findings: list[ValidationFinding] = Field(default_factory=list)
    numeric: Optional[NumericCheck] = None
    escalations: list[RetryRecord] = Field(default_factory=list)
