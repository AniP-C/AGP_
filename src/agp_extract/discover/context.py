"""Reading-order context: which region a block sits in (for source_type) and the
section-banner marks in force (for marks inheritance). Uses structural banners as
evidence — it does NOT restrict discovery to named sections.
"""
from __future__ import annotations

import re

from ..schemas import CanonicalDocument

_SELF_ASSESS = re.compile(r"self[\s-]*assessment", re.I)
_MARKS_BANNER = re.compile(r"\[\s*(\d+)\s*marks?\s*\]", re.I)
# section banners → default marks per question
_BANNER_MARKS = [
    (re.compile(r"very short answer|\bvsa\b", re.I), 1),
    (re.compile(r"short answer.*(ii|2)|\bsa[\s-]*ii\b", re.I), 3),
    (re.compile(r"short answer.*(i|1)|\bsa[\s-]*i\b", re.I), 2),
    (re.compile(r"long answer|\bla\b", re.I), 5),
    (re.compile(r"multiple choice", re.I), 1),
    (re.compile(r"assertion", re.I), 1),
]


def build_context(doc: CanonicalDocument) -> tuple[dict[str, str], dict[str, int]]:
    """Return (region_by_block_id, banner_marks_by_block_id)."""
    region: dict[str, str] = {}
    banner_marks: dict[str, int] = {}
    cur_region = "body"
    cur_marks: int | None = None

    for b in sorted(doc.blocks, key=lambda x: x.provenance.reading_order):
        text = (b.text or "").strip()
        low = text.lower()

        # region transition (Self Assessment section → to end of chapter)
        if _SELF_ASSESS.search(low) and b.type.value in ("banner", "heading"):
            cur_region = "self_assessment"

        # banner marks in force
        if b.type.value in ("banner", "heading") or _MARKS_BANNER.search(text):
            m = _MARKS_BANNER.search(text)
            if m:
                cur_marks = int(m.group(1))
            else:
                for pat, val in _BANNER_MARKS:
                    if pat.search(low):
                        cur_marks = val
                        break

        region[b.id] = cur_region
        if cur_marks is not None:
            banner_marks[b.id] = cur_marks
    return region, banner_marks
