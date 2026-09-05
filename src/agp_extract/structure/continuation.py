"""Continuation handling — detect one logical item continuing across a column or
page break (a paragraph cut at the column foot, a table split over two pages).

Conservative by design: only link on strong geometric + textual evidence, and
NEVER merge the fragments — both source blocks are preserved, joined by
``continues_to`` / ``continuation_of`` and a shared ``logical_item_id``.
"""
from __future__ import annotations

import re
import uuid

from ..schemas import BlockType, CanonicalDocument, ContentBlock

_CONT_TYPES = {BlockType.PARAGRAPH, BlockType.TABLE, BlockType.LIST}
_ROW_NUM = re.compile(r"\(\s*(\d+)\s*\)")
_SENTENCE_END = re.compile(r"[.?!:;]['\")\]]?\s*$")


def _dims(doc: CanonicalDocument) -> dict[int, tuple[int, int]]:
    return {p.page_index: (p.width, p.height) for p in doc.pages}


def _row_numbers(text: str | None) -> list[int]:
    return [int(n) for n in _ROW_NUM.findall(text or "")]


def _table_continues(a: ContentBlock, b: ContentBlock) -> bool:
    ra, rb = _row_numbers(a.text), _row_numbers(b.text)
    if ra and rb and max(ra) + 1 == min(rb):   # row (2) -> (3)
        return True
    # same column count in the pipe rendering (header repeats or continues)
    ca = (a.text or "").splitlines()[0].count("|") if a.text else -1
    cb = (b.text or "").splitlines()[0].count("|") if b.text else -2
    return ca == cb and ca >= 1


def detect_continuation(doc: CanonicalDocument) -> dict:
    dims = _dims(doc)
    # skip page furniture (footers/headers/QR) so a fragment at a column/page foot
    # is compared with the real content that resumes it, not the running footer.
    ordered = sorted(
        (b for b in doc.blocks if b.provenance.bbox and b.type != BlockType.NOISE),
        key=lambda b: b.provenance.reading_order,
    )
    links = 0
    for a, b in zip(ordered, ordered[1:]):
        if a.type != b.type or a.type not in _CONT_TYPES:
            continue
        wa, ha = dims.get(a.provenance.page_index, (1, 1))
        wb, hb = dims.get(b.provenance.page_index, (1, 1))
        a_bottom = a.provenance.bbox.y1 > 0.80 * ha
        b_top = b.provenance.bbox.y0 < 0.25 * hb
        crosses = (a.provenance.page_index != b.provenance.page_index
                   or a.provenance.column != b.provenance.column)
        if not (a_bottom and b_top and crosses):
            continue

        if a.type == BlockType.TABLE:
            ok = _table_continues(a, b)
        else:  # paragraph / list: previous fragment ends mid-sentence
            ok = bool(a.text) and not _SENTENCE_END.search(a.text.strip())
        if not ok:
            continue

        lid = a.structure.logical_item_id or f"item_{uuid.uuid4().hex[:8]}"
        a.structure.logical_item_id = lid
        b.structure.logical_item_id = lid
        a.structure.continues_to = b.id
        b.structure.continuation_of = a.id
        links += 1

    spanning = {b.structure.logical_item_id for b in doc.blocks
                if b.structure.logical_item_id}
    return {"continuation_links": links, "logical_items_spanning": len(spanning)}
