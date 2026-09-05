"""Turn provider output into provenance-rich canonical blocks + preserved assets.

For each page: check cache → call the provider → convert every RawBlock into a
:class:`ContentBlock` with a confidence-aware bbox and full block provenance,
capture cross-references as unresolved refs (resolved later), optionally enrich
tags with publisher heuristics, and crop/save any asset's original pixels.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .. import PROMPT_VERSION
from ..config import Settings
from ..ingest.loader import LoadedPage
from ..providers.base import LLMProvider, RawBlock
from ..cache.store import ExtractionCache
from ..schemas import (
    ASSET_TYPES,
    BBox,
    BlockProvenance,
    BlockType,
    ContentBlock,
    UnresolvedRef,
)
from . import heuristics
from .assets import crop_asset

log = logging.getLogger(__name__)

_VALID_TYPES = {t.value for t in BlockType}


def _to_block_type(raw: str) -> BlockType:
    return BlockType(raw) if raw in _VALID_TYPES else BlockType.OTHER


def _parse_ref(raw_text: str) -> UnresolvedRef:
    low = raw_text.lower()
    direction = next((d for d in ("above", "below", "following", "previous", "next")
                      if d in low), None)
    target = None
    for key, hint in (("fig", "figure"), ("diagram", "diagram"), ("table", "table"),
                      ("graph", "graph"), ("equation", "equation"), ("formula", "equation"),
                      ("image", "image")):
        if key in low:
            target = hint
            break
    return UnresolvedRef(raw_text=raw_text, target_hint=target, direction=direction)


def _clamp_bbox(raw: Optional[list[float]], conf: float, w: int, h: int,
                coord_space: str = "pixel") -> Optional[BBox]:
    if not raw or len(raw) != 4:
        return None
    x0, y0, x1, y1 = (float(v) for v in raw)
    if coord_space == "norm1000":
        # 0..1000 normalized (Gemini) → pixels. Clamp to the grid first so a
        # stray >1000 value maps to the page edge rather than off-page.
        sx, sy = w / 1000.0, h / 1000.0
        x0, x1 = min(x0, 1000.0) * sx, min(x1, 1000.0) * sx
        y0, y1 = min(y0, 1000.0) * sy, min(y1, 1000.0) * sy
    x0 = max(0.0, min(x0, w)); x1 = max(0.0, min(x1, w))
    y0 = max(0.0, min(y0, h)); y1 = max(0.0, min(y1, h))
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1, confidence=max(0.0, min(conf, 1.0)),
                coord_space="pixel", page_width=w, page_height=h)


def extract_pages(
    pages: list[LoadedPage],
    provider: LLMProvider,
    settings: Settings,
    cache: ExtractionCache,
    assets_dir: Path,
    use_heuristics: bool = True,
    parser=None,
) -> tuple[list[ContentBlock], dict[int, Optional[int]], dict]:
    """Parse every page and convert the result into canonical blocks.

    ``parser`` is a :class:`DocumentParser`; when omitted the vision parser is
    used, which is the original behaviour. The rest of this function is
    parser-agnostic by construction — it only ever sees ``PageExtraction``.
    """
    if parser is None:
        from ..parse.vlm_parser import VlmPageParser
        parser = VlmPageParser(provider, cache)

    extractions = parser.parse(pages, settings)

    blocks: list[ContentBlock] = []
    printed_pages: dict[int, Optional[int]] = {}
    failures: dict[int, str] = {}
    counter = 0
    reading_order = 0

    for page in pages:
        pe = extractions.get(page.page_index)
        if pe is None:
            failures[page.page_index] = "no_result"
            printed_pages[page.page_index] = page.source.printed_page
            continue
        # A failed page is recorded, never silently treated as a blank page.
        if pe.status != "ok":
            failures[page.page_index] = pe.failure_reason or "failed"
        printed_pages[page.page_index] = pe.printed_page or page.source.printed_page

        raw_sorted = sorted(pe.blocks, key=lambda b: (b.reading_order if b.reading_order is not None else 0))
        for raw in raw_sorted:
            block = _build_block(
                raw, page, pe, counter, reading_order,
                printed_pages[page.page_index], settings, assets_dir, use_heuristics,
            )
            blocks.append(block)
            counter += 1
            reading_order += 1

    report = {
        "parser": getattr(parser, "name", "unknown"),
        "parser_version": getattr(parser, "version", None),
        "pages_parsed": len(extractions),
        "failed_pages": failures,
        "n_failed_pages": len(failures),
    }
    if failures:
        log.warning("[extract] %d page(s) FAILED to parse: %s",
                    len(failures), sorted(failures))
    return blocks, printed_pages, report


def _build_block(
    raw: RawBlock, page: LoadedPage, pe, counter: int, reading_order: int,
    printed_page: Optional[int], settings: Settings, assets_dir: Path,
    use_heuristics: bool,
) -> ContentBlock:
    bid = f"blk_{counter:06d}"
    btype = _to_block_type(raw.type)
    bbox = _clamp_bbox(raw.bbox, raw.bbox_confidence, page.width, page.height,
                       coord_space=getattr(pe, "coord_space", "pixel"))

    tags = dict(raw.tags or {})
    heading_level = raw.heading_level
    if use_heuristics:
        for k, v in heuristics.extract_tags(raw.text).items():
            tags.setdefault(k, v)  # never overwrite what the model reported
        if heading_level is None:
            heading_level = heuristics.heading_level_hint(raw.text)

    prov = BlockProvenance(
        page_index=page.page_index,
        printed_page=printed_page,
        source_image=page.source.path,
        source_image_sha256=page.source.sha256,
        bbox=bbox,
        column=raw.column,
        reading_order=reading_order,
        extractor=pe.extractor,
        model=pe.model,
        extraction_confidence=max(0.0, min(raw.extraction_confidence, 1.0)),
        from_cache=pe.from_cache,
    )

    block = ContentBlock(
        id=bid, type=btype, subtype=raw.subtype,
        text=raw.text, latex=raw.latex, caption=raw.caption,
        heading_level=heading_level, provenance=prov,
        refs=[_parse_ref(r) for r in (raw.refs or [])],
        tags=tags,
    )

    # asset preservation: keep original pixels for asset blocks with a box
    if btype in ASSET_TYPES and bbox is not None:
        ref = crop_asset(
            page.image_bytes, bbox, assets_dir / f"{bid}.png",
            kind=btype.value, source_image=page.source.path,
        )
        if ref:
            block.asset = ref
    return block
