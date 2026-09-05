"""Phase 2 structure/relationship schemas.

Kept in one place and attached to blocks via a single ``structure`` sub-object so
the Phase-1 contract stays untouched. Nothing here does question discovery or
answer association — these are *structural* relationships and annotations only.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class InlineSegment(BaseModel):
    """A span inside ONE block that mixes roles (e.g. a worked example holding
    question + ``Ans.`` + explanation). Offsets let Phase 3 split reliably —
    Phase 2 only annotates; it never splits or extracts."""

    role: str                 # question | passage | answer | solution | explanation
    start: int
    end: int
    marker: Optional[str] = None      # the literal marker that opened it ("Ans.")
    text_preview: Optional[str] = None


class BlockStructure(BaseModel):
    # ── enumeration / grouping ────────────────────────────────────────────
    part_of_id: Optional[str] = None       # container block (Example/question) this belongs to
    group_role: Optional[str] = None        # container | subpart | sub_subpart | or_alternative | content
    enumerator: Optional[str] = None        # "A", "B", "i", "1", ...
    enumerator_style: Optional[str] = None  # example | numbered | upper | lower | roman | num
    depth: int = 0
    # ── OR internal choice (structural only, not a Q/A association) ────────
    or_group_id: Optional[str] = None
    # ── multi-page / multi-column continuation ────────────────────────────
    continuation_of: Optional[str] = None   # previous fragment this continues
    continues_to: Optional[str] = None       # next fragment
    logical_item_id: Optional[str] = None    # shared id across a continuation chain
    # ── inline structure annotation ───────────────────────────────────────
    inline_answer: bool = False
    inline_segments: list[InlineSegment] = Field(default_factory=list)


class PageLayout(BaseModel):
    """Deterministic layout analysis for one page."""

    n_columns: int = 1
    gutter_x: Optional[float] = None
    n_bands: int = 1                          # full-width interruptions + 1
    n_full_width: int = 0
    # geometry-vs-model reading-order agreement (0..1). Low → flagged page.
    reading_order_confidence: float = 1.0
    reading_order_source: str = "model"       # authoritative order kept from the model
    corrected_columns: int = 0                # how many block columns geometry corrected
