"""Semantic block typing for layout-only parsers.

A layout parser reports *what a block looks like* — text, list_item,
section_header — never *what it is for*. It has no notion of a question, an
answer, or a worked example. A vision model guesses those types directly, which
is why the vision path needed no step like this; the trade-off is that its
guesses are unverifiable.

Here the typing is derived from structure instead: an explicit "Solution" /
"Ans." heading marks everything that follows as answer content, up to the next
heading. That is evidence in the document, not a publisher-specific template,
and it is what lets a worked example's solution be found by answer association
(which requires an answer-eligible block type) rather than being reported as
absent while sitting on the same page.

Deliberately conservative: it only ever *promotes* generic prose types, never
overwrites a semantic type an extractor already asserted.
"""
from __future__ import annotations

import logging
import re

from ..schemas import BlockType, CanonicalDocument, ContentBlock

log = logging.getLogger(__name__)

# Types vague enough that a structural signal should be allowed to refine them.
_GENERIC = {BlockType.PARAGRAPH, BlockType.LIST, BlockType.LIST_ITEM,
            BlockType.OTHER}

_ANSWER_WORD = r"(?:Ans(?:wer)?s?|Sol(?:ution)?s?|Explanation|Working|Hint)"
# A heading whose entire text is the marker ("Solution" on its own line).
_ANSWER_HEADING = re.compile(rf"^\s*{_ANSWER_WORD}\b\s*[:.]?\s*$", re.IGNORECASE)
# A block that OPENS with the marker and then continues ("Solution Let I₀ be
# the intensity..."). Common when the solution is one flowing paragraph rather
# than a headed section, and invisible to the heading rule alone.
_ANSWER_OPENER = re.compile(rf"^\s*{_ANSWER_WORD}\b\s*[:.]?\s+\S", re.IGNORECASE)
# A heading that clearly ends the answer run by starting new material.
_SECTION_RESET = re.compile(
    r"^\s*(?:Example|Exercise|Question|Q\s*\.?\s*\d|Summary|Points? to ponder|"
    # a figure/table caption ends the solution — it belongs to the artwork, not
    # to the answer, and sweeping it in produces a bogus answer candidate
    r"(?:FIG(?:URE)?|TABLE|PLATE)\s*\.?\s*\d|"
    r"\d{1,3}(?:\.\d{1,3})?\s+[A-Z])", re.IGNORECASE)

MAX_RUN = 8   # an answer run longer than this is almost certainly body prose


def apply_semantic_types(doc: CanonicalDocument) -> dict:
    """Retype blocks that follow an explicit answer heading. In place."""
    ordered = sorted(doc.blocks, key=lambda b: b.provenance.reading_order)
    retyped = 0
    in_answer = 0

    for b in ordered:
        text = (b.text or "").strip()

        if b.is_heading() or b.type in (BlockType.SECTION, BlockType.SUBSECTION):
            # An answer heading opens a run; any other heading closes one.
            in_answer = MAX_RUN if _ANSWER_HEADING.match(text) else 0
            continue

        if in_answer and _SECTION_RESET.match(text):
            in_answer = 0

        # A block that opens with "Solution ..." is itself an answer and starts
        # a run for the paragraphs that continue it.
        if b.type in _GENERIC and text and _ANSWER_OPENER.match(text):
            b.type = BlockType.ANSWER
            b.tags.setdefault("semantic_type_source", "answer_opener")
            retyped += 1
            in_answer = MAX_RUN
            continue

        if in_answer and b.type in _GENERIC and text:
            b.type = BlockType.ANSWER
            b.tags.setdefault("semantic_type_source", "answer_heading_run")
            retyped += 1
            in_answer -= 1
        elif in_answer and b.type not in _GENERIC:
            # figures/equations inside a solution do not end it
            continue

    if retyped:
        log.info("[semantic] retyped %d block(s) as answers from an explicit "
                 "answer heading", retyped)
    return {"blocks_retyped_as_answer": retyped}
