"""Provenance at three levels — block, source-file, and run/document.

The brief's non-negotiable is that *every* extracted item traces back to the
original document. We strengthen that beyond per-block bboxes:

* :class:`SourceFileProvenance` — content hash + dims of each original page, so
  the output is verifiable against the exact bytes it came from.
* :class:`RunProvenance` — who/what/when produced this: tool + prompt version,
  provider, the three model roles, library versions, host — reproducibility.
* :class:`BlockProvenance` — page anchor, confidence-aware bbox, column, global
  reading order, and which extractor/model emitted the block, with separate
  localization vs extraction confidences.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field

from .geometry import BBox


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SourceFileProvenance(BaseModel):
    """One original page file, hashed."""

    path: str
    filename: str
    media_type: str
    sha256: str
    bytes: int
    width: int
    height: int
    page_index: int                     # 0-based order within the document
    printed_page: Optional[int] = None  # number printed on the page (e.g. 316)


class RunProvenance(BaseModel):
    """Everything needed to reproduce/attribute this extraction run."""

    run_id: str
    tool: str = "agp-extract"
    tool_version: str
    prompt_version: str
    phase: str = "phase1"
    provider: str
    models: dict[str, str] = Field(default_factory=dict)  # role -> model id
    config_snapshot: dict = Field(default_factory=dict)
    library_versions: dict[str, str] = Field(default_factory=dict)
    environment: dict[str, str] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_now)
    finished_at: Optional[datetime] = None


class BlockProvenance(BaseModel):
    """Where a single content block came from."""

    page_index: int
    printed_page: Optional[int] = None
    source_image: str                     # relative path to the page file
    source_image_sha256: Optional[str] = None
    bbox: Optional[BBox] = None           # confidence-aware localization
    column: int = -1                      # 0=left, 1=right, -1=full-width/unknown
    reading_order: int = -1               # global order across the document

    extractor: str = "unknown"            # e.g. "vision:gemini-flash-latest" / "null:opencv"
    model: Optional[str] = None
    # NOTE: for the vision path this is the MODEL'S OWN self-reported number, not
    # a calibrated probability — it clusters at round values with a high floor and
    # can be high even for a wrong extraction. Treat as a weak prior only; the
    # Phase-5 confidence-gate must combine it with deterministic signals
    # (parse success, page yield, bbox sanity, OCR cross-check, numeric re-check).
    extraction_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    ocr_confidence: Optional[float] = None
    from_cache: bool = False

    def is_complete(self) -> bool:
        """Fully traceable = has a page anchor, a source image, and a box."""
        return bool(self.source_image) and self.bbox is not None and self.page_index >= 0
