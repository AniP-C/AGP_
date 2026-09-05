"""Reading-order hardening via deterministic layout analysis.

Design note (from the Phase-1 audit): the vision model's per-page reading order
was semantically correct even where a bbox was wrong, so we KEEP it as the
authoritative logical order rather than replacing it with pure geometry (which a
bad bbox would corrupt). Phase 2 instead:

  * reconstructs columns / the gutter / full-width bands deterministically,
  * CORRECTS each block's ``column`` from geometry,
  * cross-checks the model order against a geometric order and records a
    per-page ``reading_order_confidence`` (low = a page to review),

so downstream gets a verified, structured order with explicit column/band
context — not a bare model sequence.
"""
from __future__ import annotations

from ..schemas import CanonicalDocument, ContentBlock, PageLayout


def _center_x(b: ContentBlock) -> float:
    bb = b.provenance.bbox
    return (bb.x0 + bb.x1) / 2 if bb else 0.0


def _y0(b: ContentBlock) -> float:
    return b.provenance.bbox.y0 if b.provenance.bbox else 0.0


def _is_full_width(b: ContentBlock, w: int) -> bool:
    bb = b.provenance.bbox
    if not bb:
        return False
    return (bb.x1 - bb.x0) > 0.62 * w and bb.x0 < 0.28 * w


def _band_index(b: ContentBlock, full_ys: list[float]) -> int:
    """How many full-width dividers sit strictly above this block's top."""
    y = _y0(b)
    return sum(1 for fy in full_ys if fy < y - 1e-6)


def _kendall_agreement(model_rank: list[int], geo_rank: list[int]) -> float:
    """1 - normalized Kendall-tau distance over the shared block set."""
    n = len(model_rank)
    if n < 2:
        return 1.0
    disc = 0
    for i in range(n):
        for j in range(i + 1, n):
            a = model_rank[i] - model_rank[j]
            b = geo_rank[i] - geo_rank[j]
            if a * b < 0:
                disc += 1
    total = n * (n - 1) / 2
    return round(1.0 - disc / total, 3)


def harden_reading_order(doc: CanonicalDocument) -> dict:
    dims = {p.page_index: (p.width, p.height) for p in doc.pages}
    by_page: dict[int, list[ContentBlock]] = {}
    for b in doc.blocks:
        by_page.setdefault(b.provenance.page_index, []).append(b)

    confidences, corrected_total, low_conf_pages = [], 0, []
    for p in doc.pages:
        blocks = [b for b in by_page.get(p.page_index, []) if b.provenance.bbox]
        w, h = dims.get(p.page_index, (p.width, p.height))
        if not blocks:
            p.layout = PageLayout(n_columns=1, reading_order_confidence=1.0)
            confidences.append(1.0)
            continue

        gutter = w / 2.0
        full = [b for b in blocks if _is_full_width(b, w)]
        non_full = [b for b in blocks if b not in full]
        lefts = [b for b in non_full if _center_x(b) < gutter]
        rights = [b for b in non_full if _center_x(b) >= gutter]
        n_columns = 2 if (len(lefts) >= 2 and len(rights) >= 2) else 1

        # correct column labels from geometry
        corrected = 0
        for b in blocks:
            col = -1 if b in full else (0 if _center_x(b) < gutter else 1)
            if n_columns == 1 and col != -1:
                col = 0
            if b.provenance.column != col:
                corrected += 1
            b.provenance.column = col
        corrected_total += corrected

        # geometric order: (band, column, y). full-width leads its band (col -1).
        full_ys = sorted(_y0(b) for b in full)

        def geo_key(b: ContentBlock):
            band = _band_index(b, full_ys)
            colrank = -1 if b in full else (0 if _center_x(b) < gutter else 1)
            return (band, colrank, _y0(b))

        geo_sorted = sorted(blocks, key=geo_key)
        geo_pos = {id(b): i for i, b in enumerate(geo_sorted)}
        model_sorted = sorted(blocks, key=lambda b: b.provenance.reading_order)
        model_pos = {id(b): i for i, b in enumerate(model_sorted)}
        ids = [id(b) for b in blocks]
        agreement = _kendall_agreement([model_pos[i] for i in ids],
                                       [geo_pos[i] for i in ids])
        confidences.append(agreement)
        if agreement < 0.6:
            low_conf_pages.append(p.page_index)

        p.layout = PageLayout(
            n_columns=n_columns, gutter_x=gutter if n_columns == 2 else None,
            n_bands=len(full) + 1, n_full_width=len(full),
            reading_order_confidence=agreement, reading_order_source="model",
            corrected_columns=corrected,
        )

    mean_conf = round(sum(confidences) / len(confidences), 3) if confidences else 1.0
    return {
        "mean_reading_order_confidence": mean_conf,
        "low_confidence_pages": low_conf_pages,
        "columns_corrected": corrected_total,
        "two_column_pages": sum(1 for p in doc.pages if p.layout and p.layout.n_columns == 2),
    }
