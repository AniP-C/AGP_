"""Offline deterministic provider (no key, no network).

Purpose: keep the *whole pipeline* runnable end-to-end before a Gemini key is
added — so provenance, asset preservation, the graph, the evidence layer, and
the eval hooks can all be exercised and reviewed today.

It does NOT read text (no OCR engine is installed). It proposes coarse layout
regions with OpenCV — columns + text bands + figure-like regions — all at low
confidence, ``text=None``. High-fidelity content extraction is the Gemini path.
Every block is honestly marked ``extractor="null:opencv"`` and low-confidence so
the eval report shows exactly where real extraction is still needed.
"""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np

from .base import LLMProvider, PageExtraction, RawBlock

_MIN_AREA_FRAC = 0.004      # ignore specks below 0.4% of page area
_FIGURE_AREA_FRAC = 0.05    # regions above 5% + high variance → figure candidate


class NullProvider(LLMProvider):
    name = "null"

    def extract_page(
        self,
        image_bytes: bytes,
        media_type: str,
        page_index: int,
        width: int,
        height: int,
        hint: Optional[str] = None,
    ) -> PageExtraction:
        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
        blocks: list[RawBlock] = []
        order = 0

        # 0) always emit a full-page anchor block (guarantees coverage/provenance)
        blocks.append(RawBlock(
            type="other", subtype="page_image", text=None,
            bbox=[0, 0, float(width), float(height)], bbox_confidence=1.0,
            column=-1, reading_order=order, extraction_confidence=0.05,
            tags={"note": "offline geometry only; no text (add GEMINI_API_KEY)"},
        ))
        order += 1

        if img is None:
            return self._wrap(page_index, width, height, blocks)

        page_area = float(width * height)
        col_split = self._column_split(img, width)

        for (x0, y0, x1, y1) in self._regions(img):
            area = (x1 - x0) * (y1 - y0)
            if area < _MIN_AREA_FRAC * page_area:
                continue
            roi = img[y0:y1, x0:x1]
            is_figure = (
                area > _FIGURE_AREA_FRAC * page_area
                and float(np.std(roi)) > 55.0
            )
            column = -1 if (x0 < col_split < x1) else (0 if x1 <= col_split else 1)
            blocks.append(RawBlock(
                type="figure" if is_figure else "paragraph",
                subtype="figure_candidate" if is_figure else "text_region",
                text=None,
                bbox=[float(x0), float(y0), float(x1), float(y1)],
                bbox_confidence=0.4,
                column=column,
                reading_order=order,
                extraction_confidence=0.1,
            ))
            order += 1

        # order top-to-bottom, left-column-before-right (coarse reading order)
        body = blocks[1:]
        body.sort(key=lambda b: (0 if b.column == 0 else (1 if b.column == 1 else 0),
                                 b.bbox[1] if b.bbox else 0))
        for i, b in enumerate(body, start=1):
            b.reading_order = i
        return self._wrap(page_index, width, height, [blocks[0]] + body)

    # ── helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _column_split(img: np.ndarray, width: int) -> int:
        """Estimate the gutter x between two columns via the central valley of
        the vertical ink-projection; fall back to page midpoint."""
        inv = 255 - img
        col_ink = inv.sum(axis=0).astype(np.float64)
        lo, hi = int(width * 0.35), int(width * 0.65)
        if hi <= lo:
            return width // 2
        return lo + int(np.argmin(col_ink[lo:hi]))

    @staticmethod
    def _regions(img: np.ndarray) -> list[tuple[int, int, int, int]]:
        _, th = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 12))
        dilated = cv2.dilate(th, kernel, iterations=2)
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for c in contours:
            x, y, w, h = cv2.boundingRect(c)
            boxes.append((x, y, x + w, y + h))
        return boxes

    def _wrap(self, page_index, width, height, blocks) -> PageExtraction:
        return PageExtraction(
            page_index=page_index, width=width, height=height, blocks=blocks,
            extractor="null:opencv", model=None, printed_page=None,
        )
