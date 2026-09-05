"""Confidence-aware geometry.

A bounding box is never just coordinates: it carries how confident we are in
the *localization* (``confidence``) independently of how confident we are in the
*content* (which lives on the block). This lets the eval hooks and the later
confidence-gate reason about spatial vs semantic uncertainty separately.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

CoordSpace = Literal["pixel", "normalized"]


class BBox(BaseModel):
    """Axis-aligned box in page-image coordinates (origin = top-left)."""

    x0: float
    y0: float
    x1: float
    y1: float

    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="localization confidence"
    )
    coord_space: CoordSpace = "pixel"
    # page dims travel with the box so it can be normalized/denormalized and
    # rendered resolution-independently later.
    page_width: Optional[int] = None
    page_height: Optional[int] = None

    @model_validator(mode="after")
    def _order(self) -> "BBox":
        if self.x1 < self.x0:
            self.x0, self.x1 = self.x1, self.x0
        if self.y1 < self.y0:
            self.y0, self.y1 = self.y1, self.y0
        return self

    # ── derived ──────────────────────────────────────────────────────────
    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    def as_pixel_tuple(self) -> tuple[int, int, int, int]:
        return int(round(self.x0)), int(round(self.y0)), int(round(self.x1)), int(round(self.y1))

    def normalized(self) -> "BBox":
        """Return a copy in 0..1 space (requires page dims)."""
        if self.coord_space == "normalized":
            return self.model_copy()
        if not (self.page_width and self.page_height):
            raise ValueError("page_width/page_height required to normalize")
        w, h = self.page_width, self.page_height
        return BBox(
            x0=self.x0 / w, y0=self.y0 / h, x1=self.x1 / w, y1=self.y1 / h,
            confidence=self.confidence, coord_space="normalized",
            page_width=w, page_height=h,
        )

    def iou(self, other: "BBox") -> float:
        ix0, iy0 = max(self.x0, other.x0), max(self.y0, other.y0)
        ix1, iy1 = min(self.x1, other.x1), min(self.y1, other.y1)
        inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
        union = self.area + other.area - inter
        return inter / union if union > 0 else 0.0

    @classmethod
    def full_page(cls, width: int, height: int, confidence: float = 1.0) -> "BBox":
        return cls(x0=0, y0=0, x1=width, y1=height, confidence=confidence,
                   page_width=width, page_height=height)
