"""Structural grouping — materialize container → subpart (`part_of`) nesting and
OR internal-choice groups, deterministically, from enumerator prefixes + reading
order within a section. No question/answer semantics are decided here.

OR groups require an EXPLICIT "OR" separator block between two alternatives — we
do NOT infer OR from repeated enumerators (in case-based items the same label
appears once as a question subpart and again as its answer subpart; those are
Q/A pairs for Phase 4, not internal choices).
"""
from __future__ import annotations

import re
import uuid

from ..schemas import BlockType, CanonicalDocument, ContentBlock

# roman before lower so "(i)(ii)" are roman, not the letter i
_ROMAN = re.compile(r"^\s*\(?\s*(i{1,3}|iv|v|vi{1,3}|ix|x)\s*\)", re.I)
_PATTERNS = [
    # "Example 10.2" / "Example 7" — keep the whole label, not just the first
    # integer, so chapter-scoped example numbers survive intact.
    ("example", re.compile(r"^\s*Example\s+(\d{1,3}(?:\.\d{1,3})?)", re.I)),
    # "10.1 " — chapter-scoped exercise numbering (NCERT and many others).
    # Must precede the flat pattern, which would otherwise read "10" and drop
    # the sub-number, leaving every exercise in a chapter numbered the same.
    ("numbered", re.compile(r"^\s*(\d{1,3}\.\d{1,3})(?=[\s.):])")),
    ("numbered", re.compile(r"^\s*(\d{1,3})\.\s")),
    ("upper", re.compile(r"^\s*\(\s*([A-Z])\s*\)")),
    ("num", re.compile(r"^\s*\(\s*(\d+)\s*\)")),
    ("lower", re.compile(r"^\s*\(\s*([a-hj-z])\s*\)")),  # excludes 'i' (roman)
]
_LEVELS = {"example": 0, "numbered": 0, "upper": 1, "num": 1, "lower": 2, "roman": 2}
_CONTAINER_STYLES = {"example", "numbered"}
# block types that are document structure rather than question content
_HEADINGISH = {BlockType.HEADING, BlockType.SECTION, BlockType.SUBSECTION,
               BlockType.TOPIC, BlockType.CHAPTER, BlockType.BANNER}
_LEADING_OR = re.compile(r"^\s*OR\s*\n", re.I)


def parse_enumerator(text: str | None) -> tuple[str | None, str | None]:
    if not text:
        return None, None
    if _ROMAN.match(text):
        return "roman", _ROMAN.match(text).group(1).lower()
    for style, pat in _PATTERNS:
        m = pat.match(text)
        if m:
            return style, m.group(1)
    return None, None


def _is_or_marker(text: str | None) -> bool:
    return bool(text) and text.strip().upper() == "OR" and len(text.strip()) <= 3


def _is_container(b: ContentBlock, style: str | None) -> bool:
    if b.type == BlockType.WORKED_EXAMPLE:
        return True
    if style not in _CONTAINER_STYLES:
        return False
    # NCERT numbers its SECTIONS exactly like its exercises ("10.4 COHERENT AND
    # INCOHERENT ADDITION OF WAVES" vs "10.4 In a Young's double-slit..."), so
    # numbering alone cannot tell them apart. The block's own type can: a
    # section heading is document structure, not a question container.
    # "example" style is exempt — "Example 1. Case Based:" is legitimately typed
    # as a heading by vision models and IS a question container.
    if style == "numbered" and (b.is_heading() or b.type in _HEADINGISH):
        return False
    return True


def group_structure(doc: CanonicalDocument) -> dict:
    by_section: dict[str, list[ContentBlock]] = {}
    for b in doc.blocks:
        by_section.setdefault(b.section_id or "_none", []).append(b)

    n_containers = n_subparts = 0
    max_depth = 0
    or_group_ids: set[str] = set()

    for section_blocks in by_section.values():
        ordered = sorted(section_blocks, key=lambda b: b.provenance.reading_order)
        stack: list[tuple[int, ContentBlock]] = []
        prev_subpart: ContentBlock | None = None
        or_pending = False

        for b in ordered:
            text = b.text or ""

            if _is_or_marker(text):
                b.structure.group_role = "or_separator"
                or_pending = True
                continue

            leading_or = bool(_LEADING_OR.match(text))
            parse_text = text[text.find("\n") + 1:] if leading_or else text
            style, label = parse_enumerator(parse_text)

            if _is_container(b, style):
                b.structure.group_role = "container"
                b.structure.enumerator = label
                b.structure.enumerator_style = style or "example"
                stack = [(0, b)]
                prev_subpart = None
                or_pending = False
                n_containers += 1
                continue

            if style in ("upper", "num", "lower", "roman") and stack:
                lvl = _LEVELS[style]
                while len(stack) > 1 and stack[-1][0] >= lvl:
                    stack.pop()
                parent = stack[-1][1]
                b.structure.part_of_id = parent.id
                b.structure.group_role = "subpart"
                b.structure.enumerator = label
                b.structure.enumerator_style = style
                b.structure.depth = lvl
                n_subparts += 1
                max_depth = max(max_depth, lvl)

                # OR only with an explicit separator (or a leading "OR") between
                # this subpart and the previous subpart of the same container.
                if (or_pending or leading_or) and prev_subpart is not None \
                        and prev_subpart.structure.part_of_id == parent.id:
                    gid = prev_subpart.structure.or_group_id or f"or_{uuid.uuid4().hex[:8]}"
                    for m in (prev_subpart, b):
                        m.structure.or_group_id = gid
                        m.structure.group_role = "or_alternative"
                    or_group_ids.add(gid)
                or_pending = False
                prev_subpart = b
                stack.append((lvl, b))

            elif stack and b.type in (BlockType.ANSWER, BlockType.SOLUTION,
                                      BlockType.EXPLANATION):
                b.structure.part_of_id = stack[0][1].id   # containment, not Q↔A
                b.structure.group_role = "content"
                or_pending = False

    return {
        "containers": n_containers,
        "subparts": n_subparts,
        "max_subpart_depth": max_depth,
        "or_groups": len(or_group_ids),
    }
