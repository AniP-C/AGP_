"""Reconstruct the chapter → topic → section → subsection tree from headings,
and infer light document metadata. Nothing publisher-specific is required; we
use heading *types/levels*, which any well-typed extraction provides.
"""
from __future__ import annotations

import re
from typing import Optional

from ..schemas import BlockType, ContentBlock, DocumentMeta, HierarchyNode

_LEVEL_BY_TYPE = {
    BlockType.CHAPTER: 0, BlockType.TOPIC: 1,
    BlockType.SECTION: 2, BlockType.SUBSECTION: 3,
}
_KIND_BY_LEVEL = {0: "chapter", 1: "topic", 2: "section", 3: "subsection"}


def _is_choice_marker(b: ContentBlock) -> bool:
    """A bare internal-choice marker ("OR") standing on its own line.

    Vision models routinely type this as a heading because it is short, bold and
    centred. Letting it open a section is destructive: it severs a question from
    the answers that follow it, which then look like they belong to a different
    section and become unreachable to structural answer retrieval. No real
    section is titled "OR", so this is safe and publisher-agnostic.
    """
    return (b.text or "").strip().upper() in {"OR", "OR:"}


def _heading_level(b: ContentBlock) -> Optional[int]:
    if _is_choice_marker(b):
        return None
    if b.type in _LEVEL_BY_TYPE:
        return _LEVEL_BY_TYPE[b.type]
    if b.heading_level is not None:
        return int(b.heading_level)
    if b.type == BlockType.HEADING:
        return 2
    return None


def build_hierarchy(blocks: list[ContentBlock], chapter_title: Optional[str]) -> list[HierarchyNode]:
    """Attach each block to a section; return the hierarchy roots.

    Mutates ``parent_id`` / ``section_id`` on the blocks in place.
    """
    ordered = sorted(blocks, key=lambda b: b.provenance.reading_order)
    root = HierarchyNode(id="h_root", kind="chapter",
                         title=chapter_title or "Document", level=0)
    roots = [root]
    stack: list[tuple[int, HierarchyNode]] = [(0, root)]
    counter = 0

    for b in ordered:
        lvl = _heading_level(b)
        is_heading = b.is_heading() and lvl is not None
        if is_heading and lvl > 0:
            while len(stack) > 1 and stack[-1][0] >= lvl:
                stack.pop()
            parent = stack[-1][1]
            counter += 1
            node = HierarchyNode(
                id=f"h_{counter:03d}", kind=_KIND_BY_LEVEL.get(lvl, "section"),
                title=(b.text or b.caption or "").strip()[:120] or None,
                level=lvl, heading_block_id=b.id,
            )
            parent.children.append(node)
            b.parent_id = parent.heading_block_id or parent.id
            b.section_id = node.id
            stack.append((lvl, node))
        else:
            cur = stack[-1][1]
            cur.block_ids.append(b.id)
            b.section_id = cur.id
            b.parent_id = cur.heading_block_id or cur.id
    return roots


def link_reading_order(blocks: list[ContentBlock]) -> None:
    ordered = sorted(blocks, key=lambda b: b.provenance.reading_order)
    for i, b in enumerate(ordered):
        b.prev_id = ordered[i - 1].id if i > 0 else None
        b.next_id = ordered[i + 1].id if i < len(ordered) - 1 else None


def infer_meta(blocks: list[ContentBlock]) -> DocumentMeta:
    meta = DocumentMeta(inferred=True)
    texts = [(b.text or "") for b in blocks if b.text]
    blob = "\n".join(texts)

    if re.search(r"educart", blob, re.I):
        meta.publisher = "Educart"
    if (m := re.search(r"Class\s+(IX|X|VI|VII|VIII|IX|XI|XII|\d{1,2})", blob)):
        meta.grade = m.group(1)
    if re.search(r"\bScience\b", blob):
        meta.subject = "Science"

    # chapter title: prefer an explicit chapter/topic heading near the start
    for b in sorted(blocks, key=lambda b: b.provenance.reading_order):
        if b.type in (BlockType.CHAPTER, BlockType.TOPIC) and (b.text or "").strip():
            title = b.text.strip()
            meta.chapter = title
            if (mm := re.search(r"\b(\d{1,2})\b", title)):
                meta.chapter_number = mm.group(1)
            break
    return meta
