"""Resolve config → a concrete provider. The only place providers are chosen."""
from __future__ import annotations

import logging

from ..config import Settings
from .base import LLMProvider

log = logging.getLogger(__name__)


def get_provider(settings: Settings) -> LLMProvider:
    name = settings.resolved_provider()
    if name != settings.llm_provider:
        log.warning(
            "LLM_PROVIDER=%s but no key found → falling back to '%s' "
            "(offline geometry only). Add GEMINI_API_KEY for full extraction.",
            settings.llm_provider, name,
        )

    if name == "gemini":
        from .gemini import GeminiProvider
        return GeminiProvider(settings)
    if name == "null":
        from .null import NullProvider
        return NullProvider()
    if name == "anthropic":
        from .anthropic import AnthropicProvider
        return AnthropicProvider(settings)
    if name == "openai":
        from .openai import OpenAIProvider
        return OpenAIProvider(settings)
    raise ValueError(f"unknown provider: {name}")
