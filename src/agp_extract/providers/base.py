"""Provider-agnostic LLM interface + the vision page-extraction I/O contract.

Nothing outside ``providers/`` imports a vendor SDK. Swapping Gemini → Claude →
OpenAI → local is implementing this interface, not editing call sites.

Three model *roles* (vision / reasoning / embedding) are exposed as separate
methods so they can be pointed at different models independently.
"""
from __future__ import annotations

import abc
from typing import Optional

from pydantic import BaseModel, Field


# ── vision page-extraction contract (same shape for every provider) ─────────
class RawBlock(BaseModel):
    """A block exactly as a vision model reports it (pre-provenance)."""

    type: str = "other"
    subtype: Optional[str] = None
    text: Optional[str] = None
    latex: Optional[str] = None
    caption: Optional[str] = None
    heading_level: Optional[int] = None
    bbox: Optional[list[float]] = None          # [x0,y0,x1,y1] in image pixels
    bbox_confidence: float = 0.7
    column: int = -1                            # 0=left,1=right,-1=full/unknown
    reading_order: Optional[int] = None
    tags: dict = Field(default_factory=dict)    # bloom/source/marks observed
    refs: list[str] = Field(default_factory=list)  # raw textual cross-refs
    extraction_confidence: float = 0.7


class PageExtraction(BaseModel):
    page_index: int
    width: int
    height: int
    blocks: list[RawBlock] = Field(default_factory=list)
    extractor: str = "unknown"
    model: Optional[str] = None
    printed_page: Optional[int] = None
    from_cache: bool = False
    # coordinate space of every RawBlock.bbox: "pixel" or "norm1000" (0..1000
    # normalized, Gemini's native convention). page_extractor converts to pixels.
    coord_space: str = "pixel"

    # ── extraction status (Phase 6) ────────────────────────────────────────
    # A page that produced nothing is a FAILURE, not an empty page. Recording
    # that distinction is what stops a failed call being cached as good data.
    status: str = "ok"                       # ok | failed
    failure_reason: Optional[str] = None      # truncated | unparseable | empty | error
    truncated: bool = False                   # provider finish_reason said so
    parser: str = "vlm"                       # which DocumentParser produced this
    parser_version: Optional[str] = None

    def is_valid(self) -> bool:
        """Only a valid extraction may enter the normal cache.

        Deliberately strict: no blocks, or a truncated/failed response, means we
        do not know what was on the page — and a wrong answer cached forever is
        far worse than paying for one retry.
        """
        return self.status == "ok" and not self.truncated and bool(self.blocks)

    def mark_failed(self, reason: str) -> "PageExtraction":
        self.status = "failed"
        self.failure_reason = reason
        return self


class LLMProvider(abc.ABC):
    """Provider contract. Only ``extract_page`` is required in Phase 1."""

    name: str = "base"

    @abc.abstractmethod
    def extract_page(
        self,
        image_bytes: bytes,
        media_type: str,
        page_index: int,
        width: int,
        height: int,
        hint: Optional[str] = None,
    ) -> PageExtraction:
        """Return typed blocks with pixel bboxes for one page image."""

    # ── generic seams used by later phases (optional to implement now) ─────
    def vision(self, image_bytes: bytes, prompt: str, **kw) -> str:
        raise NotImplementedError

    def generate(self, prompt: str, **kw) -> str:
        raise NotImplementedError

    def generate_json(self, prompt: str, **kw):
        """Text-in, parsed-JSON-out (dict/list) or None if unavailable."""
        raise NotImplementedError

    def generate_structured(self, prompt: str, schema: type[BaseModel], **kw):
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
