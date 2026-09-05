"""QuestionReconstructor — build complete logical questions from blocks.

A first-class pipeline stage, not a side effect of question discovery. Given an
anchor block that looks like a question start, it decides where that question
actually *ends*, which requires two symmetric operations:

* **join forward** across blocks, columns and pages, following the boundary
  scorer transitively until a stop. The previous implementation followed one
  ``continues_to`` hop and dropped everything after it, so a question spanning
  three blocks kept only two.
* **split inward** when a single block contains a question *and* its sub-parts.
  The previous implementation had no way to do this at all, so every NCERT
  exercise arrived as one flat block with an empty ``sub_questions`` list.

Both are boundary placement over the same span model, which is why they live in
one component rather than two unrelated ones.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..schemas import BlockType, CanonicalDocument, ContentBlock
from . import signals
from .spans import QuestionSpan, SpanSegment

log = logging.getLogger(__name__)

MAX_JOIN_BLOCKS = 12   # a question longer than this is almost certainly a runaway join


class QuestionReconstructor:
    def __init__(self, doc: CanonicalDocument, join_threshold: float = 0.55,
                 ambiguous: tuple[float, float] = (0.40, 0.70),
                 llm_refine=None):
        self.doc = doc
        self.byid: dict[str, ContentBlock] = {b.id: b for b in doc.blocks}
        self.join_threshold = join_threshold
        self.ambiguous_lo, self.ambiguous_hi = ambiguous
        self.llm_refine = llm_refine     # optional callable for the ambiguous band
        self.page_dims = {p.page_index: (p.width, p.height) for p in doc.pages}

        # reading-order sequence, page furniture removed so a question at a
        # column foot is compared with the content that resumes it rather than
        # with the running footer between them
        self.ordered = [b for b in sorted(
            doc.blocks, key=lambda b: b.provenance.reading_order)
            if b.type != BlockType.NOISE]
        self.pos = {b.id: i for i, b in enumerate(self.ordered)}
        self.stats = {"joined_blocks": 0, "spans_multi_block": 0,
                      "intra_block_splits": 0, "llm_adjudications": 0,
                      "ambiguous_pairs": 0}

    # ── joining ────────────────────────────────────────────────────────────
    def _join_forward(self, anchor: ContentBlock, stop_ids: set[str]
                      ) -> tuple[list[SpanSegment], list[str], list[float]]:
        """Walk forward from ``anchor`` while the boundary scorer says continue."""
        segs = [SpanSegment(anchor.id, role="stem")]
        evidence: list[str] = []
        scores: list[float] = []

        i = self.pos.get(anchor.id)
        if i is None:
            return segs, evidence, scores

        prev = anchor
        for nxt in self.ordered[i + 1:]:
            if nxt.id in stop_ids:
                evidence.append(f"stop:{nxt.id}=another_question_anchor")
                break
            if len(segs) >= MAX_JOIN_BLOCKS:
                evidence.append("stop:max_join_length")
                break

            js = signals.score_join(prev, nxt, page_dims=self.page_dims)
            if js.hard_stop:
                evidence.append(f"{nxt.id}:{js.evidence[0] if js.evidence else 'stop'}")
                break

            score = js.score
            if self.ambiguous_lo <= score <= self.ambiguous_hi:
                self.stats["ambiguous_pairs"] += 1
                verdict = self._adjudicate(prev, nxt)
                if verdict is not None:
                    score = 1.0 if verdict else 0.0
                    js.evidence.append(f"llm_adjudicated={verdict}")

            if score < self.join_threshold:
                evidence.append(f"{nxt.id}:boundary(score={score:.2f})")
                break

            segs.append(SpanSegment(nxt.id, role="continuation"))
            evidence.append(f"{nxt.id}:joined(score={score:.2f};"
                            f"{','.join(js.evidence[:3])})")
            scores.append(score)
            self.stats["joined_blocks"] += 1
            prev = nxt

        return segs, evidence, scores

    def _adjudicate(self, a: ContentBlock, b: ContentBlock) -> Optional[bool]:
        """Ask the LLM only about genuinely ambiguous pairs (cost control)."""
        if self.llm_refine is None:
            return None
        try:
            self.stats["llm_adjudications"] += 1
            return self.llm_refine(a, b)
        except Exception as exc:
            log.debug("join adjudication failed: %s", exc)
            return None

    # ── splitting ──────────────────────────────────────────────────────────
    def _split_inward(self, span: QuestionSpan,
                      taken: set[str] | None = None) -> QuestionSpan:
        """Split an over-bundled block into stem + sub-question spans.

        Only applies when the enumerated run reads as sub-questions rather than
        MCQ options — otherwise an MCQ's (a)-(d) would be shredded into four
        bogus sub-questions.
        """
        if span.sub_spans or len(span.segments) != 1:
            return span
        seg = span.segments[0]
        blk = self.byid.get(seg.block_id)
        if blk is None or not blk.text:
            return span

        text = blk.text
        # Split only the QUESTION region. A solved example repeats (a)/(b)/(c)
        # in its solution; without this bound those answer parts would become
        # sub-questions of their own question.
        boundary = signals.answer_boundary_offset(text)
        q_region = text[:boundary] if boundary else text

        taken = taken or set()
        marks = signals.find_subpart_offsets(q_region)
        if not marks and taken:
            # A lone inline "(a)" is credible when this block's OWN sub-part
            # blocks continue the run — docling commonly leaves the first
            # sub-part inline in the parent and promotes the rest to blocks,
            # which would otherwise lose (a) entirely.
            lone = signals.find_subpart_offsets(q_region, min_markers=1)
            if lone and signals.is_consecutive_run(
                    sorted({lone[0][1].strip("() ").lower(), *taken})):
                marks = lone
        if not marks or signals.looks_like_mcq_options(q_region):
            # No sub-parts — but a self-contained worked example still needs its
            # solution separated from its question, or the answer text ends up
            # inside question_text and the question reports having no answer.
            if boundary and len(q_region.strip()) >= 12:
                span.segments = [SpanSegment(blk.id, 0, boundary, role="stem"),
                                 SpanSegment(blk.id, boundary, len(text), role="answer")]
                span.answer_parts = {
                    "": SpanSegment(blk.id, boundary, len(text), role="answer")}
                span.evidence.append(f"answer_region_split@{boundary}")
                self.stats["answer_regions_split"] = \
                    self.stats.get("answer_regions_split", 0) + 1
            return span

        stem_end = marks[0][0]
        # A short stem is fine when it is just the question's own number
        # ("10.3 (a) ... (b) ..."), which is a real and common shape. It is NOT
        # fine when the block simply opens mid-content, where splitting would
        # invent a parent that was never there.
        if stem_end < 12 and not signals.is_bare_question_number(q_region[:stem_end]):
            return span

        q_end = boundary if boundary else len(text)
        # Sub-parts that already exist as their own blocks must not be created
        # a second time from the parent's inline text.
        marks = [(pos, enum) for pos, enum in marks
                 if enum.strip("() ").lower() not in taken]
        if not marks:
            return span

        span.segments = [SpanSegment(blk.id, 0, stem_end, role="stem")]
        if boundary:
            # keep the solution attached to the parent, not lost and not
            # promoted into a fake sub-question
            span.segments.append(
                SpanSegment(blk.id, boundary, len(text), role="answer"))
            span.evidence.append(f"answer_region_kept_from@{boundary}")
            span.answer_parts = self._split_answer_region(blk.id, text, boundary)
        for i, (pos, enum) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else q_end
            span.sub_spans.append(QuestionSpan(
                anchor_block_id=blk.id,
                segments=[SpanSegment(blk.id, pos, end, role="subpart")],
                enumerator=enum.strip("() "),
                evidence=[f"intra_block_split@{pos}"],
            ))
        span.evidence.append(f"split_into_{len(marks)}_subparts")
        self.stats["intra_block_splits"] += 1
        return span

    @staticmethod
    def _split_answer_region(block_id: str, text: str, boundary: int) -> dict:
        """Map each sub-part label in the solution to its own slice.

        A worked example's solution mirrors the question's labels — "(a) ...
        (b) ..." — so each sub-question can be given exactly its own answer
        rather than the whole solution blob. Unlabelled solutions are stored
        under "" and belong to the question as a whole.
        """
        region = text[boundary:]
        marks = signals.find_subpart_offsets(region)
        if not marks:
            return {"": SpanSegment(block_id, boundary, len(text), role="answer")}
        parts: dict = {}
        # text between the answer marker and the first label (e.g. "Solution")
        # is a preamble, not an answer to any single part
        for i, (pos, enum) in enumerate(marks):
            end = marks[i + 1][0] if i + 1 < len(marks) else len(region)
            key = enum.strip("() ").lower()
            parts[key] = SpanSegment(block_id, boundary + pos, boundary + end,
                                     role="answer")
        return parts

    # ── entry point ────────────────────────────────────────────────────────
    def reconstruct(self, anchors: list[ContentBlock],
                    child_map: dict[str, list[ContentBlock]] | None = None
                    ) -> dict[str, QuestionSpan]:
        """Reconstruct one span per anchor block, keyed by anchor id."""
        child_map = child_map or {}
        anchor_ids = {a.id for a in anchors}
        # blocks already claimed as someone's sub-part must not be swallowed
        claimed = {c.id for kids in child_map.values() for c in kids}
        stop_ids = anchor_ids | claimed

        out: dict[str, QuestionSpan] = {}
        for a in anchors:
            segs, ev, scores = self._join_forward(a, stop_ids - {a.id})
            span = QuestionSpan(
                anchor_block_id=a.id, segments=segs,
                enumerator=a.structure.enumerator,
                or_group_id=a.structure.or_group_id,
                evidence=ev, join_scores=scores,
            )
            # Split the parent's own text too, skipping any sub-part that
            # already exists as its own block. A block can hold "(a)" inline
            # while "(b)" was promoted to a sibling block — both belong to the
            # same tree, so both paths must run.
            taken = {(c.structure.enumerator or "").strip("() ").lower()
                     for c in child_map.get(a.id, [])
                     if c.structure.enumerator}
            span = self._split_inward(span, taken=taken)
            if span.spans_multiple_blocks():
                self.stats["spans_multi_block"] += 1
            out[a.id] = span
        return out
