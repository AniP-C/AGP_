"""Parser selection — one place that knows which adapters exist.

Adapters are imported lazily so an uninstalled optional parser (docling,
mistral) never breaks a run that does not use it.
"""
from __future__ import annotations

import logging

from ..cache.store import ExtractionCache
from ..config import Settings
from ..providers.base import LLMProvider
from .base import DocumentParser
from .vlm_parser import VlmPageParser

log = logging.getLogger(__name__)


def build_parser(name: str, provider: LLMProvider, settings: Settings,
                 cache: ExtractionCache | None = None,
                 do_ocr: bool | None = None) -> DocumentParser:
    key = (name or "vlm").lower()

    if key == "vlm":
        return VlmPageParser(provider, cache)

    if key == "docling":
        from .docling_parser import DoclingParser
        # OCR must be OFF for a born-digital PDF. Forcing OCR there re-renders
        # the page and re-reads it with an OCR model, discarding a text layer
        # that was already exact — the same mistake the vision-only pipeline
        # made, and it corrupts the text ("slits are separated" → "sits are
        # separatei"). The router decides; docling_ocr is only the fallback.
        ocr = settings.docling_ocr if do_ocr is None else do_ocr
        return DoclingParser(do_ocr=ocr)

    if key == "mistral":
        from .mistral_parser import MistralOcrParser
        return MistralOcrParser(settings)

    log.warning("unknown parser %r; falling back to the vision parser", name)
    return VlmPageParser(provider, cache)
