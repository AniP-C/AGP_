"""Docling adapter — layout/OCR/text-layer parsing normalised at the boundary.

Docling produces a ``DoclingDocument``; the rest of this application must never
see one. Everything is converted here into the existing
:class:`PageExtraction` / :class:`RawBlock` contract, so `page_extractor` and
all of Phases 2-5 are unaffected by the parser choice.

Two things Docling gives us that the vision path could not:

* a **born-digital text layer read exactly**, with no OCR and no API cost;
* ``charspan`` provenance — exact character offsets into the source text — which
  is strictly stronger than a bounding box for downstream span reconstruction.

Docling's labels are *layout* labels (text, list_item, formula, ...). They carry
no notion of a question, an answer, or a worked example: that semantic typing
stays where it belongs, in Phases 2-3.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

# A list marker worth restoring: a real number or letter label, not a bullet.
_MARKER_RE = re.compile(r"^\(?\s*(?:\d{1,3}(?:\.\d{1,3})?|[A-Za-z]|[ivxIVX]{1,4})\s*[.)]?\s*$")

from ..config import Settings
from ..ingest.loader import LoadedPage
from ..providers.base import PageExtraction, RawBlock
from .base import DocumentParser

log = logging.getLogger(__name__)

# Docling layout label → our BlockType value. Intentionally conservative:
# anything unmapped becomes "other" rather than being guessed into a semantic
# type it has not earned.
_LABEL_MAP = {
    "text": "paragraph",
    "paragraph": "paragraph",
    "list_item": "list_item",
    "section_header": "heading",
    "title": "chapter",
    "page_header": "noise",
    "page_footer": "noise",
    "footnote": "note",
    "caption": "caption",
    "picture": "figure",
    "chart": "figure",
    "table": "table",
    "formula": "equation",
    "code": "other",
    "reference": "other",
    "document_index": "toc",
    "handwritten_text": "paragraph",
}

_HEADING_LEVEL = {"title": 0, "section_header": 2}


def _norm_label(item: Any) -> str:
    raw = getattr(item, "label", None)
    return str(getattr(raw, "value", raw) or "text").lower()


def _table_text(item: Any) -> Optional[str]:
    """Render a table's cells as pipe-delimited rows.

    Downstream code reads ``block.text``; a table whose content lives only in a
    structured cell list would look like an empty block to every consumer.
    """
    data = getattr(item, "data", None)
    cells = getattr(data, "table_cells", None) or []
    if not cells:
        return None
    rows: dict[int, list[tuple[int, str]]] = {}
    for c in cells:
        r = getattr(c, "start_row_offset_idx", 0) or 0
        col = getattr(c, "start_col_offset_idx", 0) or 0
        rows.setdefault(r, []).append((col, (getattr(c, "text", "") or "").strip()))
    out = []
    for r in sorted(rows):
        out.append(" | ".join(t for _, t in sorted(rows[r])))
    return "\n".join(x for x in out if x.strip()) or None


class DoclingParser(DocumentParser):
    name = "docling"

    def __init__(self, do_ocr: bool = True, ocr_engine: str = "rapidocr",
                 do_formula: bool = False):
        self.do_ocr = do_ocr
        self.ocr_engine = ocr_engine
        self.do_formula = do_formula
        try:
            import docling
            self.version = getattr(docling, "__version__", None)
        except Exception:
            self.version = None

    # ── conversion ─────────────────────────────────────────────────────────
    def _converter(self):
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions

        opts = PdfPipelineOptions()
        opts.do_ocr = self.do_ocr
        opts.do_table_structure = True
        opts.do_formula_enrichment = self.do_formula
        if self.do_ocr:
            try:
                from docling.datamodel.pipeline_options import RapidOcrOptions
                opts.ocr_options = RapidOcrOptions(force_full_page_ocr=True)
            except Exception:  # fall back to whatever docling defaults to
                log.debug("RapidOCR options unavailable; using docling default OCR")
        return DocumentConverter(
            format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
        )

    def parse_path(self, path: str, pages: list[LoadedPage], settings: Settings
                   ) -> dict[int, PageExtraction]:
        """Convert one document file and split the result per page."""
        conv = self._converter()
        result = conv.convert(path)
        doc = result.document
        return self._to_extractions(doc, pages)

    def parse(self, pages: list[LoadedPage], settings: Settings
              ) -> dict[int, PageExtraction]:
        src = str(settings.input_path)
        return self.parse_path(src, pages, settings)

    # ── DoclingDocument → PageExtraction ───────────────────────────────────
    def _to_extractions(self, doc: Any, pages: list[LoadedPage]
                        ) -> dict[int, PageExtraction]:
        by_index = {p.page_index: p for p in pages}
        # docling page numbers are 1-based; ours are 0-based
        page_sizes: dict[int, tuple[float, float]] = {}
        for pno, pitem in (getattr(doc, "pages", {}) or {}).items():
            size = getattr(pitem, "size", None)
            if size is not None:
                page_sizes[int(pno) - 1] = (float(size.width), float(size.height))

        collected: dict[int, list[RawBlock]] = {i: [] for i in by_index}
        order: dict[int, int] = {i: 0 for i in by_index}

        for item, _level in doc.iterate_items():
            prov = getattr(item, "prov", None) or []
            if not prov:
                continue
            pno0 = int(getattr(prov[0], "page_no", 1)) - 1
            if pno0 not in by_index:
                continue
            blk = self._to_raw_block(item, prov[0], pno0, by_index[pno0],
                                     page_sizes.get(pno0), order[pno0])
            if blk is None:
                continue
            collected[pno0].append(blk)
            order[pno0] += 1

        out: dict[int, PageExtraction] = {}
        for idx, page in by_index.items():
            blocks = collected.get(idx, [])
            pe = PageExtraction(
                page_index=idx, width=page.width, height=page.height,
                blocks=blocks, extractor=f"docling:{self.version or 'unknown'}",
                model=None, printed_page=None, coord_space="pixel",
                parser=self.name, parser_version=self.version,
            )
            if not blocks:
                # An empty page is a failure to read, not a blank page. Coverage
                # validation decides whether to escalate; we never claim success.
                pe.mark_failed("empty")
            out[idx] = pe
        return out

    def _to_raw_block(self, item: Any, prov: Any, page_index: int,
                      page: LoadedPage, pdf_size: Optional[tuple[float, float]],
                      reading_order: int) -> Optional[RawBlock]:
        label = _norm_label(item)
        btype = _LABEL_MAP.get(label, "other")

        text = getattr(item, "text", None)
        if btype == "table":
            text = _table_text(item)
        elif label == "list_item":
            # Docling moves a list's number into `marker` and out of `text`, so
            # "10.1 Monochromatic light..." arrives as just "Monochromatic
            # light...". Question numbering is load-bearing downstream — it is
            # how exercises are identified, numbered and continuity-checked —
            # so put it back.
            marker = (getattr(item, "marker", "") or "").strip()
            if marker and text and not text.lstrip().startswith(marker):
                text = f"{marker} {text.lstrip()}" if _MARKER_RE.match(marker) else text

        bbox = self._bbox_px(prov, page, pdf_size)
        # A block with neither text nor a box carries no information at all.
        if not (text or "").strip() and bbox is None:
            return None

        tags: dict = {"docling_label": label}
        charspan = getattr(prov, "charspan", None)
        if charspan is not None:
            try:
                tags["charspan"] = [int(charspan[0]), int(charspan[1])]
            except Exception:
                pass
        # Page furniture is identified structurally by docling, not guessed.
        layer = getattr(item, "content_layer", None)
        layer_val = str(getattr(layer, "value", layer) or "").lower()
        if layer_val == "furniture":
            btype = "noise"

        return RawBlock(
            type=btype,
            subtype=label if btype == "other" else None,
            text=(text or None),
            latex=(text if btype == "equation" else None),
            heading_level=_HEADING_LEVEL.get(label),
            bbox=bbox,
            # Docling reports no per-item confidence. Rather than invent one, we
            # emit a fixed provenance-derived value and record its source, so
            # Phase-5 calibration keeps model-reported and system-evidence
            # confidence as separate signals.
            bbox_confidence=0.99 if bbox else 0.0,
            extraction_confidence=1.0 if not self.do_ocr else 0.90,
            column=-1,
            reading_order=reading_order,
            tags=tags,
        )

    @staticmethod
    def _bbox_px(prov: Any, page: LoadedPage,
                 pdf_size: Optional[tuple[float, float]]) -> Optional[list[float]]:
        """Docling bbox (PDF points, possibly bottom-left origin) → image pixels.

        Getting this wrong silently corrupts every downstream geometric
        signal — reading order, column detection, continuation, asset crops —
        so the origin conversion is explicit rather than assumed.
        """
        bb = getattr(prov, "bbox", None)
        if bb is None:
            return None
        try:
            l, t, r, b = float(bb.l), float(bb.t), float(bb.r), float(bb.b)
        except Exception:
            return None

        pw, ph = pdf_size or (page.width, page.height)
        if not pw or not ph:
            return None

        origin = str(getattr(getattr(bb, "coord_origin", None), "value",
                             getattr(bb, "coord_origin", "")) or "").upper()
        if "BOTTOM" in origin:
            t, b = ph - t, ph - b   # flip to top-left origin

        y0, y1 = (t, b) if t <= b else (b, t)
        x0, x1 = (l, r) if l <= r else (r, l)

        sx, sy = page.width / pw, page.height / ph
        return [x0 * sx, y0 * sy, x1 * sx, y1 * sy]
