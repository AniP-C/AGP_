"""Parser-agnostic document-parsing port.

The rest of the application must not know whether a page was read by a vision
model, a layout+OCR pipeline, or a PDF text layer. Every parser normalises to
the SAME contract that already exists for vision providers
(:class:`PageExtraction` of :class:`RawBlock`), so `page_extractor` and
everything downstream of it is untouched by the choice.

This mirrors the existing ``LLMProvider`` abstraction: swapping Docling → VLM →
Mistral → MinerU is implementing this interface, not editing call sites.
"""
from __future__ import annotations

import abc
from typing import Optional

from ..config import Settings
from ..ingest.loader import LoadedPage
from ..providers.base import PageExtraction


class DocumentParser(abc.ABC):
    """Turn loaded pages into per-page typed blocks.

    Document-level rather than page-level because layout parsers (Docling,
    MinerU) consume a whole document and derive cross-page reading order from
    it; a per-page-only port would throw that away.
    """

    name: str = "base"
    version: Optional[str] = None

    @abc.abstractmethod
    def parse(
        self,
        pages: list[LoadedPage],
        settings: Settings,
    ) -> dict[int, PageExtraction]:
        """Return one :class:`PageExtraction` per ``page.page_index``.

        A page that could not be read must be returned with ``status="failed"``
        and a ``failure_reason`` — never omitted, and never as a silently empty
        success. Callers rely on that distinction to escalate rather than to
        quietly lose the page.
        """

    def supports(self, pages: list[LoadedPage]) -> bool:
        """Whether this parser can handle the given pages at all."""
        return True
