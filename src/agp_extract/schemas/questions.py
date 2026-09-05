"""Normalized question schema (Phase 3 output).

`source_type` and `question_type` are independent axes. An answer is represented
ONLY when it is part of the SAME source block / inline segment (folded worked
examples); cross-block answer association is Phase 4 and is never done here.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .answers import AnswerAssociation
from .validation import QuestionValidation


class QuestionOption(BaseModel):
    key: str            # "a", "b", "I", "II", ...
    text: str


class Marks(BaseModel):
    value: Optional[float] = None
    source: Optional[str] = None       # explicit | section_banner | inherited


class InternalChoice(BaseModel):
    type: str = "OR"
    group_id: str
    alternative_question_ids: list[str] = Field(default_factory=list)


class InlineAnswerSegment(BaseModel):
    role: str                          # answer | solution | explanation
    text: str
    char_start: int
    char_end: int
    source_block_id: str


class InlineAnswer(BaseModel):
    """An answer that is physically inside the question's own block/segment.
    NOT a cross-block association (that is Phase 4)."""

    present: bool = True
    source: str = "inline_segment"     # inline_segment | same_block
    text: Optional[str] = None
    segments: list[InlineAnswerSegment] = Field(default_factory=list)


class QuestionProvenance(BaseModel):
    source_pages: list[int] = Field(default_factory=list)
    printed_pages: list[int] = Field(default_factory=list)
    source_blocks: list[str] = Field(default_factory=list)
    reading_order: int = -1
    question_char_span: Optional[list[int]] = None   # [start,end] within the block


class QuestionEval(BaseModel):
    discovery_confidence: float = 0.0
    classification_confidence: float = 0.0
    classification_source: str = "deterministic"     # deterministic | llm
    type_evidence: list[str] = Field(default_factory=list)


class Question(BaseModel):
    id: str
    source_type: str                   # worked_example|solved_question|self_assessment|in_text|activity|other
    question_type: str                 # mcq|assertion_reason|fill_blank|true_false|matching|numerical|
                                       # distinguish|ordering|case_study|short_answer|long_answer|
                                       # conceptual|activity|in_text|unknown
    bloom_level: Optional[str] = None
    question_number: Optional[str] = None
    question_text: str = ""
    options: list[QuestionOption] = Field(default_factory=list)
    sub_questions: list["Question"] = Field(default_factory=list)
    internal_choice: Optional[InternalChoice] = None
    marks: Optional[Marks] = None
    source_refs: list[str] = Field(default_factory=list)      # NCERT / CBSE / DIKSHA
    related_assets: list[str] = Field(default_factory=list)   # asset block ids
    answer_inline: Optional[InlineAnswer] = None
    # Phase 4: document-grounded answer association (None until Phase 4 runs)
    answer_association: Optional[AnswerAssociation] = None
    # Phase 5: validation / calibrated confidence / gate decision (None until Phase 5)
    validation: Optional[QuestionValidation] = None
    provenance: QuestionProvenance = Field(default_factory=QuestionProvenance)
    evaluation: QuestionEval = Field(default_factory=QuestionEval)


Question.model_rebuild()
