"""OPTIONAL publisher heuristics — confidence boosters, never load-bearing.

Per the brief, publisher-specific rules must be isolated and optional. These
regexes reflect *this* Educart book's conventions. They only:
  (a) capture bloom/source/marks tokens VERBATIM into tags, and
  (b) hint heading level from "TOPIC n" style markers.

They do NOT classify question types or discover questions — that is Phase 3.
Disable by not calling ``enrich_block`` (the pipeline has a flag).
"""
from __future__ import annotations

import re
from typing import Optional

_BLOOM = re.compile(r"\((Remember|Understand|Apply|Analyse|Analyze|Evaluate|Create)\)", re.I)
_SOURCE = re.compile(r"\[(NCERT Exemplar|NCERT|DIKSHA|CBSE[^\]]*)\]", re.I)
# a marks integer printed right after a bloom tag, or a "[ n marks ]" banner
_MARKS_AFTER_BLOOM = re.compile(r"\((?:Remember|Understand|Apply|Analyse|Analyze|Evaluate|Create)\)\s*(\d+)", re.I)
_MARKS_BANNER = re.compile(r"\[\s*(\d+)\s*marks?\s*\]", re.I)
_TOPIC = re.compile(r"^\s*TOPIC\s+\d+\b", re.I)


def extract_tags(text: Optional[str]) -> dict:
    """Return {bloom, source, marks} found verbatim in the text (may be empty)."""
    if not text:
        return {}
    tags: dict = {}
    if (m := _BLOOM.search(text)):
        tags["bloom"] = m.group(1).title()
    if (srcs := _SOURCE.findall(text)):
        tags["source"] = sorted({s.strip() for s in srcs})
    marks = _MARKS_AFTER_BLOOM.search(text) or _MARKS_BANNER.search(text)
    if marks:
        tags["marks"] = marks.group(1)
    return tags


def heading_level_hint(text: Optional[str]) -> Optional[int]:
    if text and _TOPIC.match(text):
        return 1  # TOPIC n → topic level
    return None
