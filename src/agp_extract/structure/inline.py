"""Annotate inline structure — a single block holding question + ``Ans.`` +
explanation (e.g. folded worked Examples 2/3). We record role spans with exact
offsets so Phase 3 can split reliably. We DO NOT split or extract here.
"""
from __future__ import annotations

import re

from ..schemas import BlockType, CanonicalDocument, InlineSegment

_MARKER = re.compile(r"(Ans\.|Answer\s*:|Solution\s*:|Explanation\s*:)", re.I)
_INLINE_TYPES = {BlockType.WORKED_EXAMPLE, BlockType.QUESTION,
                 BlockType.ANSWER, BlockType.SOLUTION}


def _role(marker: str) -> str:
    m = marker.lower()
    if m.startswith("expl"):
        return "explanation"
    if m.startswith("sol"):
        return "solution"
    return "answer"


def annotate_inline(doc: CanonicalDocument) -> dict:
    n_inline = 0
    for b in doc.blocks:
        if b.type not in _INLINE_TYPES or not b.text:
            continue
        marks = list(_MARKER.finditer(b.text))
        # inline only if a marker appears AFTER some leading content (the question)
        marks = [m for m in marks if m.start() > 0]
        if not marks:
            continue
        b.structure.inline_answer = True
        n_inline += 1
        segs = [InlineSegment(role="question", start=0, end=marks[0].start(),
                              text_preview=b.text[:marks[0].start()].strip()[:80])]
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(b.text)
            segs.append(InlineSegment(
                role=_role(m.group(1)), start=m.start(), end=end,
                marker=m.group(1), text_preview=b.text[m.start():end].strip()[:80],
            ))
        b.structure.inline_segments = segs
    return {"inline_answer_blocks": n_inline}
