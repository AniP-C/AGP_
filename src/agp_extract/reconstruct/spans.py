"""The span model — what a *logical* question actually is.

A question is not a block. It is a contiguous span over the reading-order
sequence, from some offset in a start block to some offset in an end block:

    (start_block, start_offset) → (end_block, end_offset)

Modelling it this way makes the two failure modes the *same* operation —
boundary placement — instead of two unrelated mechanisms:

* **joining** an under-segmented question whose text was split across several
  blocks, columns, or pages (the old code followed exactly one continuation
  hop and silently dropped the rest);
* **splitting** an over-bundled block that contains a whole question *and* its
  sub-parts (the old code could not do this at all, which is why NCERT
  exercise 10.2's sub-parts (a)-(d) vanished).

It also makes completeness *measurable*: a reconstructed span can be compared
against gold as character overlap, rather than by exact string equality.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SpanSegment:
    """One block's contribution to a question — possibly a slice of it."""

    block_id: str
    start: int = 0                  # char offset into the block's text
    end: Optional[int] = None       # None ⇒ to end of block
    role: str = "stem"              # stem | continuation | passage | option | subpart

    def slice_of(self, text: Optional[str]) -> str:
        if not text:
            return ""
        return text[self.start:self.end if self.end is not None else len(text)]

    def is_whole_block(self, text: Optional[str]) -> bool:
        return self.start == 0 and (self.end is None or self.end >= len(text or ""))


@dataclass
class QuestionSpan:
    """A complete logical question, plus its sub-question tree."""

    anchor_block_id: str
    segments: list[SpanSegment] = field(default_factory=list)
    sub_spans: list["QuestionSpan"] = field(default_factory=list)
    enumerator: Optional[str] = None
    or_group_id: Optional[str] = None
    #: Solution text carved out of the SAME block, split by sub-part label where
    #: the solution is itself enumerated. Key "" holds an unlabelled solution
    #: belonging to the whole question. This is what lets a self-contained worked
    #: example ("Example 10.1 ... Solution ...") resolve its own answer without a
    #: separate answer-typed block to point at.
    answer_parts: dict = field(default_factory=dict)  # enumerator -> SpanSegment
    # how this span was built — kept for auditability, never collapsed away
    evidence: list[str] = field(default_factory=list)
    join_scores: list[float] = field(default_factory=list)

    @property
    def block_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.segments:
            seen.setdefault(s.block_id, None)
        for sub in self.sub_spans:
            for b in sub.block_ids:
                seen.setdefault(b, None)
        return list(seen)

    #: segment roles that are part of the QUESTION text. An ``answer`` segment
    #: is tracked for provenance but must never be concatenated into the
    #: question — a worked example's solution is not part of what was asked.
    QUESTION_ROLES = frozenset({"stem", "continuation", "passage", "option",
                                "subpart"})

    def text(self, byid: dict) -> str:
        """Render the span's own question text (excluding sub-questions)."""
        parts = []
        for seg in self.segments:
            if seg.role not in self.QUESTION_ROLES:
                continue
            blk = byid.get(seg.block_id)
            if blk is None:
                continue
            chunk = seg.slice_of(blk.text).strip()
            if chunk:
                parts.append(chunk)
        return "\n".join(parts).strip()

    def answer_text(self, byid: dict) -> str:
        """Text of any answer region carved out of the same block."""
        parts = []
        for seg in self.segments:
            if seg.role != "answer":
                continue
            blk = byid.get(seg.block_id)
            if blk is not None:
                chunk = seg.slice_of(blk.text).strip()
                if chunk:
                    parts.append(chunk)
        return "\n".join(parts).strip()

    def spans_multiple_blocks(self) -> bool:
        return len({s.block_id for s in self.segments}) > 1
