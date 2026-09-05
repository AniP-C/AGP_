"""Vision-model parser — the existing Phase-1 behaviour, behind the port.

Deliberately a thin wrapper: it loops pages and delegates to
``provider.extract_page``, which is exactly what ``extract_pages`` did inline.
Keeping it byte-for-byte equivalent is the point — it is the regression anchor
for the parser refactor, and it stays the escalation path for pages another
parser cannot read.
"""
from __future__ import annotations

import logging

from ..cache.store import ExtractionCache
from ..config import Settings
from ..ingest.loader import LoadedPage
from ..providers.base import LLMProvider, PageExtraction
from .base import DocumentParser

log = logging.getLogger(__name__)


class VlmPageParser(DocumentParser):
    name = "vlm"

    def __init__(self, provider: LLMProvider, cache: ExtractionCache | None = None,
                 prompt_version: str | None = None):
        self.provider = provider
        self.cache = cache
        from .. import PROMPT_VERSION
        self.prompt_version = prompt_version or PROMPT_VERSION
        self.version = getattr(provider, "vision_model", None) or provider.name

    def _model_key(self, settings: Settings) -> str:
        return (settings.vision_model if self.provider.name == "gemini"
                else self.provider.name)

    def parse(self, pages: list[LoadedPage], settings: Settings
              ) -> dict[int, PageExtraction]:
        model_key = self._model_key(settings)
        out: dict[int, PageExtraction] = {}
        for page in pages:
            out[page.page_index] = self.parse_page(page, settings, model_key)
        return out

    def parse_page(self, page: LoadedPage, settings: Settings,
                   model_key: str | None = None) -> PageExtraction:
        """Single page — also the entry point for targeted escalation."""
        model_key = model_key or self._model_key(settings)
        if self.cache is not None:
            hit = self.cache.get(page.source.sha256, model_key, self.prompt_version)
            if hit is not None:
                return hit
        pe = self.provider.extract_page(
            image_bytes=page.image_bytes, media_type=page.media_type,
            page_index=page.page_index, width=page.width, height=page.height,
        )
        pe.parser = self.name
        if self.cache is not None:
            # put() routes invalid results to the failure record itself
            self.cache.put(page.source.sha256, model_key, self.prompt_version, pe)
        return pe
