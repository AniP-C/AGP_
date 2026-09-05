"""Phase 2 orchestrator — deterministic structure/relationship reconstruction.

Runs, in order, over a Phase-1 CanonicalDocument (mutating it in place):
  1. reading-order hardening (columns/bands/confidence)
  2. structural grouping (container → subpart part_of, OR groups)
  3. inline annotation (folded Q + Ans. + explanation)
  4. continuation detection (page/column-spanning fragments, split tables)
  5. cross-reference resolution (figure/table/equation refs)

No LLM, no RAG, no questions[]. Nothing is fabricated: weak/ambiguous
relationships are left explicitly unresolved.
"""
from __future__ import annotations

from ..schemas import CanonicalDocument
from .continuation import detect_continuation
from .crossref import resolve_crossrefs
from .grouping import group_structure
from .inline import annotate_inline
from .reading_order import harden_reading_order


def run_structure(doc: CanonicalDocument) -> dict:
    stats = {
        "reading_order": harden_reading_order(doc),
        "grouping": group_structure(doc),
        "inline": annotate_inline(doc),
        "continuation": detect_continuation(doc),
        "crossref": resolve_crossrefs(doc),
    }
    doc.stats["phase2"] = stats
    return stats
