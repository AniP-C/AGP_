"""OpenAI / Azure adapter — stub.

Same interchangeability point as the Anthropic stub: implement ``extract_page``
with the Chat Completions / Responses vision API and JSON mode, returning the
shared :class:`PageExtraction` contract.
"""
from __future__ import annotations

from typing import Optional

from ..config import Settings
from .base import LLMProvider, PageExtraction


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings

    def extract_page(self, image_bytes: bytes, media_type: str, page_index: int,
                     width: int, height: int, hint: Optional[str] = None) -> PageExtraction:
        raise NotImplementedError(
            "OpenAIProvider is a stub. Install `openai`, set OPENAI_API_KEY, and "
            "mirror GeminiProvider.extract_page using vision + JSON mode."
        )
