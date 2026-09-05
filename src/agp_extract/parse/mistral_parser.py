"""Mistral OCR adapter — optional, and never required to run the project.

Per the Phase-6 brief §8 this exists so OCR quality can be *measured* against
alternatives, not so the architecture bends around one vendor. With no
``MISTRAL_API_KEY`` the adapter reports itself unavailable and the benchmark
skips cleanly; nothing else changes.
"""
from __future__ import annotations

import base64
import logging
from typing import Any, Optional

from ..config import Settings
from ..ingest.loader import LoadedPage
from ..providers.base import PageExtraction, RawBlock
from .base import DocumentParser

log = logging.getLogger(__name__)

# Mistral OCR returns markdown per page rather than typed layout blocks, so the
# mapping is coarser than docling's by nature. We record that honestly instead
# of inventing block types the response does not actually support.
_DEFAULT_MODEL = "mistral-ocr-latest"


class MistralOcrParser(DocumentParser):
    name = "mistral"

    def __init__(self, settings: Settings, model: str | None = None):
        self.settings = settings
        self.model = model or _DEFAULT_MODEL
        self.version = self.model

    @staticmethod
    def available(settings: Settings) -> bool:
        key = getattr(settings, "mistral_api_key", None)
        return bool(key and key.get_secret_value())

    def _client(self):
        from mistralai import Mistral
        return Mistral(api_key=self.settings.mistral_api_key.get_secret_value())

    def parse(self, pages: list[LoadedPage], settings: Settings
              ) -> dict[int, PageExtraction]:
        if not self.available(settings):
            raise RuntimeError(
                "MISTRAL_API_KEY is not set; MistralOcrParser is unavailable.")
        client = self._client()
        out: dict[int, PageExtraction] = {}
        for page in pages:
            out[page.page_index] = self._parse_page(client, page)
        return out

    def _parse_page(self, client: Any, page: LoadedPage) -> PageExtraction:
        pe = PageExtraction(
            page_index=page.page_index, width=page.width, height=page.height,
            extractor=f"mistral:{self.model}", model=self.model,
            coord_space="pixel", parser=self.name, parser_version=self.model,
        )
        try:
            b64 = base64.b64encode(page.image_bytes).decode("ascii")
            resp = client.ocr.process(
                model=self.model,
                document={"type": "image_url",
                          "image_url": f"data:{page.media_type};base64,{b64}"},
            )
            md = self._page_markdown(resp)
        except Exception as exc:
            log.warning("mistral OCR failed on page %d: %s", page.page_index, exc)
            return pe.mark_failed("error")

        blocks = self._markdown_to_blocks(md)
        pe.blocks = blocks
        if not blocks:
            pe.mark_failed("empty")
        return pe

    @staticmethod
    def _page_markdown(resp: Any) -> str:
        pages = getattr(resp, "pages", None) or []
        return "\n\n".join((getattr(p, "markdown", "") or "") for p in pages).strip()

    @staticmethod
    def _markdown_to_blocks(md: str) -> list[RawBlock]:
        """Split page markdown into coarse typed blocks.

        No bounding boxes: the OCR response is text-first. Those blocks are
        therefore usable for text-recall benchmarking but NOT for geometric
        signals — which is exactly why this stays a benchmark adapter rather
        than a default parser.
        """
        blocks: list[RawBlock] = []
        for i, chunk in enumerate(p.strip() for p in (md or "").split("\n\n")):
            if not chunk:
                continue
            if chunk.startswith("#"):
                btype, level = "heading", min(chunk.count("#", 0, 6), 3)
            elif chunk.startswith(("|", "- ", "* ")):
                btype, level = ("table" if chunk.startswith("|") else "list"), None
            elif chunk.startswith("$$") or chunk.startswith("\\["):
                btype, level = "equation", None
            else:
                btype, level = "paragraph", None
            blocks.append(RawBlock(
                type=btype, text=chunk.lstrip("# ").strip() or None,
                latex=chunk if btype == "equation" else None,
                heading_level=level, bbox=None, bbox_confidence=0.0,
                reading_order=i, extraction_confidence=0.85,
                tags={"parser": "mistral", "no_geometry": True},
            ))
        return blocks
