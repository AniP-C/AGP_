"""Deterministic question builder — turn the Phase-2 enriched blocks into a
normalized, recursive ``questions[]`` tree. No cross-block answer association.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..schemas import (
    ASSET_TYPES,
    BlockType,
    CanonicalDocument,
    ContentBlock,
    InlineAnswer,
    InlineAnswerSegment,
    InternalChoice,
    Marks,
    Question,
    QuestionEval,
    QuestionOption,
    QuestionProvenance,
)
from .classify import (
    classify_question_type, looks_interrogative, parse_options, section_kind,
)

_ANSWER_TYPES = {BlockType.ANSWER, BlockType.SOLUTION, BlockType.EXPLANATION}
_STEM_TYPES = {BlockType.PARAGRAPH, BlockType.LIST, BlockType.LIST_ITEM}


class _Builder:
    def __init__(self, doc, region, banner_marks, spans=None, recon_stats=None):
        self.doc = doc
        self.byid = {b.id: b for b in doc.blocks}
        self.region = region
        self.banner_marks = banner_marks
        # Reconstructed spans, keyed by anchor block id (Phase 6). When absent
        # the builder falls back to its original single-block behaviour, so the
        # reconstructor is additive rather than a rewrite.
        self.spans = spans or {}
        self.recon_stats = recon_stats if recon_stats is not None else {}
        self.children: dict[str, list[ContentBlock]] = defaultdict(list)
        for b in doc.blocks:
            if b.structure.part_of_id:
                self.children[b.structure.part_of_id].append(b)
        self.by_section: dict[str, list[ContentBlock]] = defaultdict(list)
        for b in doc.blocks:
            self.by_section[b.section_id or "_none"].append(b)
        self._sec_titles = self._section_titles()
        self._n = 0

    def _nid(self) -> str:
        self._n += 1
        return f"Q_{self._n:04d}"

    # ── source_type / marks / metadata ────────────────────────────────────
    def _source_type(self, b: ContentBlock) -> str:
        st = b.structure
        if b.type == BlockType.WORKED_EXAMPLE or st.enumerator_style == "example":
            return "worked_example"
        if self.region.get(b.id) == "self_assessment":
            return "self_assessment"
        if st.enumerator_style == "numbered":
            return "solved_question"
        if b.type == BlockType.ACTIVITY:
            return "activity"
        if b.type == BlockType.QUESTION:
            return "in_text"
        return "other"

    def _marks(self, b: ContentBlock) -> Marks | None:
        explicit = b.tags.get("marks")
        if explicit is not None:
            try:
                return Marks(value=float(explicit), source="explicit")
            except (TypeError, ValueError):
                pass
        if b.id in self.banner_marks:
            return Marks(value=float(self.banner_marks[b.id]), source="section_banner")
        return None

    # ── stems (case passage) + continuation ──────────────────────────────
    def _stem_blocks(self, container: ContentBlock, subparts: list[ContentBlock]) -> list[ContentBlock]:
        sec = self.by_section.get(container.section_id or "_none", [])
        c_ro = container.provenance.reading_order
        first_sub = min((s.provenance.reading_order for s in subparts), default=10**9)
        # next container in the same section bounds the span
        nxt = min((x.provenance.reading_order for x in sec
                   if x.structure.group_role == "container"
                   and x.provenance.reading_order > c_ro), default=10**9)
        end = min(first_sub, nxt)
        return [x for x in sorted(sec, key=lambda x: x.provenance.reading_order)
                if c_ro < x.provenance.reading_order < end
                and x.type in _STEM_TYPES and not x.structure.part_of_id]

    def _related_assets(self, blocks: list[ContentBlock]) -> list[str]:
        out: list[str] = []
        for b in blocks:
            for ref in b.refs:
                if ref.status == "resolved" and ref.resolved_block_id:
                    out.append(ref.resolved_block_id)
            # assets grouped under this block as children
            for ch in self.children.get(b.id, []):
                if ch.type in ASSET_TYPES:
                    out.append(ch.id)
        return list(dict.fromkeys(out))

    # ── sub-question recovered by splitting inside a single block ─────────
    @staticmethod
    def _sub_sort_key(q: Question):
        """Order sub-questions by their label (a, b, c / i, ii, iii), then page."""
        from ..reconstruct.signals import _rank_alpha, _rank_roman

        core = (q.question_number or "").strip("() ").lower()
        rank = _rank_roman(core) if len(core) > 1 else _rank_alpha(core)
        return (rank if rank is not None else 999,
                q.provenance.reading_order if q.provenance else 0)

    def _inline_from_segment(self, seg) -> InlineAnswer | None:
        """Turn a carved answer slice into an InlineAnswer with exact offsets."""
        blk = self.byid.get(seg.block_id)
        if blk is None or not blk.text:
            return None
        text = seg.slice_of(blk.text).strip()
        if not text:
            return None
        end = seg.end if seg.end is not None else len(blk.text)
        return InlineAnswer(
            source="same_block", text=text,
            segments=[InlineAnswerSegment(
                role="solution", text=text, char_start=seg.start, char_end=end,
                source_block_id=seg.block_id)],
        )

    def _build_split_sub(self, span, stype: str, parent: ContentBlock,
                         answer_parts: dict | None = None) -> Question:
        """A sub-question whose text is a character slice of the parent block.

        Provenance still points at a real block and a real char span, so the
        audit trail survives the split — the sub-question is traceable to the
        exact offsets it came from.
        """
        text = span.text(self.byid)
        seg = span.segments[0] if span.segments else None
        # the solution slice carrying this sub-part's own label, if there is one
        key = (span.enumerator or "").strip("() ").lower()
        answer_seg = (answer_parts or {}).get(key)
        inline = self._inline_from_segment(answer_seg) if answer_seg else None
        qtype, cconf, ev = classify_question_type(
            text, has_subparts=False, is_case=False,
            has_equation=any(x in text for x in ("=", "λ", "ν")),
            marks=None, is_activity=False,
        )
        return Question(
            id=self._nid(),
            source_type=stype,
            question_type=qtype,
            question_number=span.enumerator,
            question_text=text,
            options=parse_options(text) if qtype == "mcq" else [],
            sub_questions=[],
            marks=None,
            related_assets=[],
            answer_inline=inline,
            provenance=QuestionProvenance(
                source_pages=[parent.provenance.page_index],
                printed_pages=([parent.provenance.printed_page]
                               if parent.provenance.printed_page else []),
                source_blocks=[span.anchor_block_id],
                reading_order=parent.provenance.reading_order,
                question_char_span=([seg.start, seg.end] if seg and seg.end is not None
                                    else None),
            ),
            evaluation=QuestionEval(
                discovery_confidence=0.80,
                classification_confidence=cconf,
                classification_source="reconstructed_intra_block",
                type_evidence=ev,
            ),
        )

    # ── node construction ─────────────────────────────────────────────────
    def build_node(self, b: ContentBlock, is_root: bool,
                   source_type: str | None = None) -> Question:
        # source_type is decided at the root and INHERITED by sub-questions
        stype = self._source_type(b) if is_root else (source_type or self._source_type(b))
        source_blocks = [b.id]
        pages = {b.provenance.page_index}
        printed = {b.provenance.printed_page} if b.provenance.printed_page else set()

        # inline Q/A segmentation (same-block only)
        qtext = b.text or ""
        qspan = None
        answer_inline = None
        segs = b.structure.inline_segments
        if b.structure.inline_answer and segs:
            qsegs = [s for s in segs if s.role in ("question", "passage")]
            asegs = [s for s in segs if s.role in ("answer", "solution", "explanation")]
            if qsegs:
                qtext = (b.text or "")[qsegs[0].start:qsegs[0].end].strip()
                qspan = [qsegs[0].start, qsegs[0].end]
            if asegs:
                answer_inline = InlineAnswer(
                    source="inline_segment",
                    text=" ".join((b.text or "")[s.start:s.end].strip() for s in asegs),
                    segments=[InlineAnswerSegment(
                        role=s.role, text=(b.text or "")[s.start:s.end].strip(),
                        char_start=s.start, char_end=s.end, source_block_id=b.id)
                        for s in asegs],
                )

        # ── continuation: the reconstructed span decides where this question
        # actually ends. Falls back to a TRANSITIVE continues_to walk when no
        # span is available — the old code followed exactly one hop and
        # silently truncated any question spanning three or more blocks.
        span = self.spans.get(b.id)
        span_subs: list = []
        if span is not None:
            stem = span.text(self.byid)
            if stem:
                qtext = stem
            for seg in span.segments[1:]:
                cont = self.byid.get(seg.block_id)
                if cont is None or cont.id in source_blocks:
                    continue
                source_blocks.append(cont.id)
                pages.add(cont.provenance.page_index)
                if cont.provenance.printed_page:
                    printed.add(cont.provenance.printed_page)
            span_subs = list(span.sub_spans)
        else:
            seen = {b.id}
            cur = b
            while cur.structure.continues_to and cur.structure.continues_to in self.byid:
                cont = self.byid[cur.structure.continues_to]
                if cont.id in seen:
                    break
                seen.add(cont.id)
                qtext = f"{qtext} {cont.text or ''}".strip()
                source_blocks.append(cont.id)
                pages.add(cont.provenance.page_index)
                if cont.provenance.printed_page:
                    printed.add(cont.provenance.printed_page)
                cur = cont

        # child subparts that are questions (recursive) → sub_questions
        # A sub-question is identified by its STRUCTURAL role, not its block
        # type. A vision model types sub-parts as `question`; a layout parser
        # types the same lines as `list_item`. Requiring the semantic type
        # silently dropped every sub-question on the layout path.
        sub_blocks = [
            c for c in self.children.get(b.id, [])
            if c.type in (BlockType.QUESTION, BlockType.WORKED_EXAMPLE)
            or (c.structure.group_role in ("subpart", "or_alternative")
                and c.type not in _ANSWER_TYPES and c.type not in ASSET_TYPES)
        ]
        sub_blocks.sort(key=lambda c: c.provenance.reading_order)
        sub_qs = [self.build_node(c, is_root=False, source_type=stype) for c in sub_blocks]
        # Sub-parts recovered by splitting INSIDE one block (e.g. an NCERT
        # exercise whose (a)-(d) all live in a single block). Structural
        # sub-blocks take precedence; these only fill a genuine gap.
        if span_subs:
            answer_parts = (span.answer_parts if span is not None else {}) or {}
            have = {(q.question_number or "").strip("() ").lower() for q in sub_qs}
            extra = [self._build_split_sub(s, stype, b, answer_parts)
                     for s in span_subs
                     if (s.enumerator or "").strip("() ").lower() not in have]
            # Merge inline-split parts with structurally-linked ones and restore
            # label order, so "(a)" carved out of the parent sits before the
            # "(b)" that arrived as its own block.
            sub_qs = sorted(sub_qs + extra, key=self._sub_sort_key)
        # a logical question spans every page its sub-questions touch
        for sq in sub_qs:
            pages.update(sq.provenance.source_pages)
            printed.update(sq.provenance.printed_pages)

        # stem/passage for a container with subparts
        stem_prefix = ""
        if is_root and b.structure.group_role == "container" and sub_blocks:
            # Blocks the span already absorbed must not be added again, or the
            # passage text would appear twice in the question.
            already = set(source_blocks)
            stems = [s for s in self._stem_blocks(b, sub_blocks) if s.id not in already]
            if stems:
                stem_prefix = " ".join(s.text or "" for s in stems)
                source_blocks.extend(s.id for s in stems)
                for s in stems:
                    pages.add(s.provenance.page_index)

        # A self-contained worked example ("... Solution ...") carries its own
        # answer in the same block. Without this it would be reported
        # not_in_document even though the solution is right there.
        if answer_inline is None and span is not None and span.answer_parts and not sub_qs:
            whole = span.answer_parts.get("") or next(iter(span.answer_parts.values()), None)
            if whole is not None:
                answer_inline = self._inline_from_segment(whole)

        full_q = f"{qtext} {stem_prefix}".strip()
        is_case = bool(stem_prefix) and bool(sub_qs)
        has_eq = any(c.type == BlockType.EQUATION for c in self.children.get(b.id, [])) \
            or any(a in (b.text or "") for a in ("=", "λ", "ν"))
        marks = self._marks(b)
        qtype, cconf, ev = classify_question_type(
            full_q, has_subparts=bool(sub_qs), is_case=is_case,
            has_equation=has_eq, marks=marks.value if marks else None,
            is_activity=b.type == BlockType.ACTIVITY,
        )
        options = parse_options(qtext) if qtype == "mcq" else []

        internal = None
        if b.structure.or_group_id:
            internal = InternalChoice(type="OR", group_id=b.structure.or_group_id)

        disc_conf = 0.95 if (b.structure.group_role == "container"
                             or b.type == BlockType.WORKED_EXAMPLE) else (
            0.85 if b.type == BlockType.QUESTION else 0.6)

        return Question(
            id=self._nid(),
            source_type=stype,
            question_type=qtype,
            bloom_level=b.tags.get("bloom"),
            question_number=b.structure.enumerator,
            question_text=full_q,
            options=options,
            sub_questions=sub_qs,
            internal_choice=internal,
            marks=marks,
            source_refs=list(b.tags.get("source", []) or []),
            related_assets=self._related_assets([b] + sub_blocks),
            answer_inline=answer_inline,
            provenance=QuestionProvenance(
                source_pages=sorted(pages),
                printed_pages=sorted(x for x in printed if x),
                source_blocks=list(dict.fromkeys(source_blocks)),
                reading_order=b.provenance.reading_order,
                question_char_span=qspan,
            ),
            evaluation=QuestionEval(
                discovery_confidence=disc_conf,
                classification_confidence=cconf,
                classification_source="deterministic",
                type_evidence=ev,
            ),
        )

    def _is_bare_marker(self, b: ContentBlock) -> bool:
        """A block that is only a label, with no question in it.

        Publishers print running margin banners ("EXAMPLE 10.2") beside the
        example they belong to. The enumerator regex matches those exactly as it
        matches a real "Example 10.2 ..." stem, so without this they become
        contentless questions of type `unknown`. Requiring actual text after the
        label is publisher-agnostic — a real question always has some.
        """
        if self.children.get(b.id):
            return False           # it has sub-parts, so it is a real container
        text = (b.text or "").strip()
        enum = b.structure.enumerator or ""
        # strip a leading "Example N" / "N." style label and see what remains
        rest = re.sub(r"^\s*(?:Example\s+)?" + re.escape(enum) + r"[\s.):-]*", "",
                      text, count=1, flags=re.IGNORECASE) if enum else text
        return len(re.sub(r"\W+", "", rest)) < 8

    def _section_titles(self) -> dict[str, str]:
        titles: dict[str, str] = {}

        def walk(nodes):
            for n in nodes:
                if n.title:
                    titles[n.id] = n.title
                walk(n.children)

        walk(self.doc.hierarchy or [])
        return titles

    def _is_question_candidate(self, b: ContentBlock) -> bool:
        """Does this numbered block actually ask something?

        Needed because a layout parser types everything as text: a numbered
        SUMMARY bullet ("1. Huygens' principle tells us that...") is
        indistinguishable from an exercise ("10.1 Monochromatic light...") by
        numbering alone. When the block carries a semantic type from the
        extractor we trust that; otherwise the enclosing section decides, and
        failing that the sentence has to read as a question.
        """
        if b.type in (BlockType.QUESTION, BlockType.WORKED_EXAMPLE, BlockType.ACTIVITY):
            return True
        # "Example N" is a worked-example marker in its own right; its stem is
        # often just the label, with the actual asking done by its sub-parts.
        if b.structure.enumerator_style == "example":
            return True
        kind = section_kind(self._sec_titles.get(b.section_id or ""))
        if kind == "question":
            return True
        if kind == "prose":
            return False
        if looks_interrogative(b.text):
            return True
        # A container whose own stem is not interrogative ("Example 10.1",
        # "Answer the following:") is still a question when the parts hanging
        # off it are — judge the whole, not just the header line.
        return any(looks_interrogative(c.text)
                   for c in self.children.get(b.id, [])
                   if c.structure.group_role in ("subpart", "or_alternative"))

    def roots(self) -> list[ContentBlock]:
        out = []
        for b in self.doc.blocks:
            role = b.structure.group_role
            if self._is_bare_marker(b):
                continue
            if not self._is_question_candidate(b):
                continue
            if role == "container":
                out.append(b)
            elif role in ("subpart", "or_alternative", "content", "or_separator"):
                continue
            elif b.type in (BlockType.QUESTION, BlockType.WORKED_EXAMPLE, BlockType.ACTIVITY) \
                    and not b.structure.part_of_id:
                out.append(b)
        return sorted(out, key=lambda b: b.provenance.reading_order)


def build_questions(doc: CanonicalDocument, region, banner_marks,
                    settings=None, llm_refine=None) -> tuple[list[Question], dict]:
    """Reconstruct spans, then materialise the question tree from them.

    Returns ``(questions, reconstruction_stats)``. Reconstruction runs first
    because where a question *ends* determines what its text, its sub-tree and
    its answer-search scope are — deciding any of those from a single anchor
    block is what produced truncated questions.
    """
    from ..reconstruct import QuestionReconstructor

    probe = _Builder(doc, region, banner_marks)
    anchors = probe.roots()

    join_t = getattr(settings, "reconstruct_join_threshold", 0.55)
    lo = getattr(settings, "reconstruct_ambiguous_lo", 0.40)
    hi = getattr(settings, "reconstruct_ambiguous_hi", 0.70)
    if settings is not None and not getattr(settings, "reconstruct_use_llm", True):
        llm_refine = None

    recon = QuestionReconstructor(doc, join_threshold=join_t,
                                  ambiguous=(lo, hi), llm_refine=llm_refine)
    spans = recon.reconstruct(anchors, child_map=probe.children)

    b = _Builder(doc, region, banner_marks, spans=spans, recon_stats=recon.stats)
    return [b.build_node(r, is_root=True) for r in anchors], recon.stats
