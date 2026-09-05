"""Assemble typed blocks + pages + provenance into a CanonicalDocument."""
from __future__ import annotations

import hashlib
from typing import Optional

from ..ingest.loader import LoadedPage
from ..schemas import (
    CanonicalDocument,
    ContentBlock,
    Page,
    RunProvenance,
)
from .hierarchy import build_hierarchy, infer_meta, link_reading_order


def build_document(
    document_id: str,
    blocks: list[ContentBlock],
    pages: list[LoadedPage],
    printed_pages: dict[int, Optional[int]],
    run: RunProvenance,
    preprocessed: Optional[dict[int, str]] = None,
) -> CanonicalDocument:
    preprocessed = preprocessed or {}

    link_reading_order(blocks)
    meta = infer_meta(blocks)
    hierarchy = build_hierarchy(blocks, chapter_title=meta.chapter)

    page_models = [
        Page(
            page_index=p.page_index,
            printed_page=printed_pages.get(p.page_index) or p.source.printed_page,
            source=p.source,
            preprocessed_path=preprocessed.get(p.page_index),
            width=p.width, height=p.height,
        )
        for p in pages
    ]
    # keep printed_page on the source records in sync
    for p in page_models:
        if p.printed_page is not None:
            p.source.printed_page = p.printed_page

    doc = CanonicalDocument(
        document_id=document_id, meta=meta,
        source_files=[p.source for p in pages], run=run,
        pages=page_models, blocks=blocks, hierarchy=hierarchy,
    )
    doc.content_sha256 = _content_hash(blocks)
    doc.stats = _basic_stats(blocks, page_models)
    return doc


def _content_hash(blocks: list[ContentBlock]) -> str:
    h = hashlib.sha256()
    for b in sorted(blocks, key=lambda b: b.provenance.reading_order):
        h.update(b.id.encode())
        h.update((b.text or "").encode("utf-8", "ignore"))
        if b.provenance.bbox:
            h.update(str(b.provenance.bbox.as_pixel_tuple()).encode())
    return h.hexdigest()


def _basic_stats(blocks: list[ContentBlock], pages: list[Page]) -> dict:
    by_type: dict[str, int] = {}
    for b in blocks:
        by_type[b.type.value] = by_type.get(b.type.value, 0) + 1
    return {
        "pages": len(pages),
        "blocks": len(blocks),
        "blocks_by_type": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        "assets": sum(1 for b in blocks if b.asset is not None),
    }
