"""Phase 4 answer-association schemas.

Evidence provenance is NOT collapsed into one number: structural strength,
lexical score, semantic score, reranker score, LLM judgment, and the final
association confidence are all kept as separate signals (for Phase-5 calibration).
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AnswerSignals(BaseModel):
    structural_strength: float = 0.0
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    rerank_score: float = 0.0
    llm_confidence: Optional[float] = None
    final_confidence: float = 0.0


class AnswerEvidence(BaseModel):
    """One candidate answer block and why it's a candidate."""

    candidate_block_id: str
    block_type: str
    page_index: int
    printed_page: Optional[int] = None
    section_id: Optional[str] = None
    channels: list[str] = Field(default_factory=list)   # structural|lexical|semantic|inline|continuation|reference
    relationship: str = "none"       # inline|same_worked_example|same_container_enum|
                                     # adjacent_solution|same_section|refers_to|continuation|none
    scope: str = "in_scope"          # in_scope | cross_section
    structural_strength: float = 0.0
    lexical_score: float = 0.0
    semantic_score: float = 0.0
    rerank_score: float = 0.0


class LLMAssociation(BaseModel):
    """Strict structured output of the adjudication step (never an answer)."""

    status: str                      # matched|partial|ambiguous|not_in_document
    answer_block_ids: list[str] = Field(default_factory=list)
    evidence_block_ids: list[str] = Field(default_factory=list)
    reason: str = ""
    llm_confidence: float = 0.0


class AnswerAssociation(BaseModel):
    status: str                      # matched|partial|ambiguous|not_in_document
    answer_state: str                # present|partial|not_in_document|derived
    method: str                      # inline|structural|llm_adjudicated|no_candidate
    answer_block_ids: list[str] = Field(default_factory=list)
    answer_kind: Optional[str] = None       # text|table|equation|mixed|inline
    answer_preview: Optional[str] = None    # EXTRACTED from blocks, never generated
    evidence: list[AnswerEvidence] = Field(default_factory=list)
    signals: AnswerSignals = Field(default_factory=AnswerSignals)
    llm: Optional[LLMAssociation] = None
