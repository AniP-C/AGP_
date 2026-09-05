"""Anthropic (Claude) adapter — stub.

Demonstrates provider interchangeability: implement ``extract_page`` against the
Anthropic SDK's multimodal messages API (image block + JSON-instructed prompt),
returning the same :class:`PageExtraction` contract. Left unimplemented because
the SDK is not installed in this POC environment and Gemini is the chosen path.
"""
from __future__ import annotations

from typing import Optional

from ..config import Settings
from .base import LLMProvider, PageExtraction


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(self, settings: Settings):
        self.settings = settings

    def extract_page(self, image_bytes: bytes, media_type: str, page_index: int,
                     width: int, height: int, hint: Optional[str] = None) -> PageExtraction:
        raise NotImplementedError(
            "AnthropicProvider is a stub. Install `anthropic`, set ANTHROPIC_API_KEY, "
            "and mirror GeminiProvider.extract_page using the messages API."
        )
