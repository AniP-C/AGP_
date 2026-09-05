"""Evidence layer — populated in Phase 1, retrieved from in Phase 4.

* ``build_evidence_units`` derives one normalized :class:`EvidenceUnit` per block
  (carrying the graph metadata needed for filtering).
* ``StructuralRetriever`` implements the deterministic, model-free channel:
  reading-order neighbours, same-section siblings, and the parent heading. This
  is the exact seam answer-association (Phase 4) will call, alongside the
  semantic/lexical channels that get wired in then.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..schemas import (
    ASSET_TYPES,
    CanonicalDocument,
    ContentBlock,
    DocumentGraph,
    EvidenceHit,
    EvidenceKind,
    EvidenceRetriever,
    EvidenceUnit,
    HEADING_TYPES,
)


def _kind_for(b: ContentBlock) -> EvidenceKind:
    if b.type in ASSET_TYPES:
        return EvidenceKind.ASSET
    if b.type in HEADING_TYPES:
        return EvidenceKind.STRUCTURE
    return EvidenceKind.TEXT


def build_evidence_units(doc: CanonicalDocument) -> list[EvidenceUnit]:
    units: list[EvidenceUnit] = []
    for b in doc.blocks:
        neighbors = [x for x in (b.prev_id, b.next_id) if x]
        units.append(EvidenceUnit(
            id=f"ev_{b.id}", block_id=b.id, kind=_kind_for(b),
            text=(b.text or b.caption or "").strip(),
            page_index=b.provenance.page_index,
            printed_page=b.provenance.printed_page,
            section_id=b.section_id, parent_id=b.parent_id,
            reading_order=b.provenance.reading_order,
            block_type=b.type.value, neighbor_ids=neighbors,
            ref_hints=[r.raw_text for r in b.refs], tags=b.tags,
        ))
    return units


class StructuralRetriever(EvidenceRetriever):
    """Model-free structural retrieval over the graph. (Semantic/lexical are
    intentionally left to Phase 4 via the base-class NotImplementedError.)"""

    def __init__(self, doc: CanonicalDocument, graph: DocumentGraph):
        self.doc = doc
        self.graph = graph
        self.blocks = {b.id: b for b in doc.blocks}
        self.by_section: dict[str, list[str]] = {}
        for b in doc.blocks:
            if b.section_id:
                self.by_section.setdefault(b.section_id, []).append(b.id)

    def structural(self, block_id: str, limit: int = 10) -> list[EvidenceHit]:
        b = self.blocks.get(block_id)
        if b is None:
            return []
        scored: dict[str, float] = {}

        def bump(bid: str | None, score: float) -> None:
            if bid and bid != block_id:
                scored[bid] = max(scored.get(bid, 0.0), score)

        bump(b.prev_id, 0.9)
        bump(b.next_id, 0.9)
        if b.section_id:
            for sib in self.by_section.get(b.section_id, []):
                bump(sib, 0.7)
            bump(b.parent_id, 0.6)

        hits = [EvidenceHit(unit_id=f"ev_{bid}", block_id=bid, score=s,
                            channel="structural")
                for bid, s in scored.items()]
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


def save_evidence(units: list[EvidenceUnit], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps([u.model_dump(exclude_none=True) for u in units], indent=2),
        encoding="utf-8",
    )
