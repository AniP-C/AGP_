"""Input router — decide how each page should be read.

"Document-agnostic" concretely means: classify the input, then route. A
born-digital PDF carries an exact text layer; rasterising it and paying a vision
model to re-read pixels is both worse and far more expensive. A scanned page has
no such layer and needs OCR or a vision model.

The decision is made PER PAGE, because real books are mixed: a born-digital
chapter often contains scanned inserts, and a scanned chapter may have a
digitally generated exercise section.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# A page needs a usable amount of real text before we trust its text layer.
# Below this we assume it is a scan with stray metadata text.
MIN_CHARS = 100
MIN_TEXT_AREA_RATIO = 0.02   # text boxes must cover a plausible fraction of the page


@dataclass
class PageRoute:
    page_index: int
    route: str                  # "text_layer" | "ocr"
    chars: int = 0
    text_area_ratio: float = 0.0
    reason: str = ""


@dataclass
class RoutingPlan:
    routes: dict[int, PageRoute] = field(default_factory=dict)

    @property
    def text_layer_pages(self) -> list[int]:
        return sorted(p for p, r in self.routes.items() if r.route == "text_layer")

    @property
    def ocr_pages(self) -> list[int]:
        return sorted(p for p, r in self.routes.items() if r.route == "ocr")

    def summary(self) -> dict:
        n = len(self.routes)
        return {
            "pages": n,
            "text_layer_pages": len(self.text_layer_pages),
            "ocr_pages": len(self.ocr_pages),
            "born_digital": bool(n) and len(self.text_layer_pages) == n,
            "mixed": bool(self.text_layer_pages) and bool(self.ocr_pages),
        }


def probe_pdf(pdf_path: str | Path) -> RoutingPlan:
    """Measure the text layer of each PDF page with PyMuPDF (cheap, local)."""
    import fitz

    plan = RoutingPlan()
    doc = fitz.open(str(pdf_path))
    try:
        for i, page in enumerate(doc):
            text = page.get_text("text").strip()
            chars = len(text)
            page_area = abs(page.rect.width * page.rect.height) or 1.0
            covered = 0.0
            for blk in page.get_text("blocks") or []:
                x0, y0, x1, y1 = blk[:4]
                covered += abs((x1 - x0) * (y1 - y0))
            ratio = min(covered / page_area, 1.0)

            if chars >= MIN_CHARS and ratio >= MIN_TEXT_AREA_RATIO:
                plan.routes[i] = PageRoute(i, "text_layer", chars, ratio,
                                           "usable text layer")
            else:
                plan.routes[i] = PageRoute(
                    i, "ocr", chars, ratio,
                    f"no usable text layer (chars={chars}, area={ratio:.3f})")
    finally:
        doc.close()
    return plan


def route_pages(input_path: str | Path, page_count: int) -> RoutingPlan:
    """Routing plan for any supported input.

    Non-PDF inputs (image folders, ZIPs of scans) have no text layer by
    definition, so every page routes to OCR without probing.
    """
    p = Path(input_path)
    if p.is_file() and p.suffix.lower() == ".pdf":
        try:
            return probe_pdf(p)
        except Exception as exc:
            log.warning("text-layer probe failed (%s); routing all pages to OCR", exc)

    plan = RoutingPlan()
    for i in range(page_count):
        plan.routes[i] = PageRoute(i, "ocr", 0, 0.0, "not a PDF; no text layer")
    return plan
