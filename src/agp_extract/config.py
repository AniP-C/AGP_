"""Central configuration — the single place model/provider choices live.

Everything downstream reads :class:`Settings`; nothing hard-codes a provider or
model. Switching ``LLM_PROVIDER=gemini`` → ``anthropic`` (etc.) or swapping any
one of the three model roles (vision / reasoning / embedding) is a config edit,
never a code change.
"""
from __future__ import annotations

import platform
from pathlib import Path
from typing import Literal, Optional

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ProviderName = Literal["gemini", "null", "anthropic", "openai"]


class Settings(BaseSettings):
    """Loaded from environment / ``.env`` (case-insensitive)."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── provider + model roles (independent by design) ─────────────────────
    llm_provider: ProviderName = "gemini"
    # `-latest` aliases track the current Gemini flash/pro so pinned ids don't
    # 404 when a version is retired. Pin a specific id for reproducibility once
    # a run is finalized (the exact id is still recorded in run provenance).
    vision_model: str = "gemini-flash-latest"
    vision_escalation_model: str = "gemini-pro-latest"
    reasoning_model: str = "gemini-flash-latest"
    embedding_model: str = "gemini-embedding-001"

    # ── credentials ────────────────────────────────────────────────────────
    gemini_api_key: Optional[SecretStr] = Field(
        default=None,
        validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    )
    anthropic_api_key: Optional[SecretStr] = None
    openai_api_key: Optional[SecretStr] = None
    mistral_api_key: Optional[SecretStr] = None  # optional; benchmark only

    # ── paths ────────────────────────────────────────────────────────────
    input_path: Path = Path("cl9ch11")
    output_dir: Path = Path("outputs")
    cache_dir: Path = Path(".cache")

    # ── tunables ────────────────────────────────────────────────────────
    preprocess_upscale: float = 1.5
    bbox_confidence_threshold: float = 0.55
    extraction_confidence_threshold: float = 0.60
    max_pages: int = 0  # 0 == all
    use_cache: bool = True

    # ── parsing (Phase 6) ────────────────────────────────────────────────
    # "auto" routes per page: born-digital text layer → docling, scans → VLM.
    # Force a single parser with "vlm" | "docling" | "mistral".
    parser: str = "auto"
    docling_ocr: bool = True
    # Coverage gate: fraction of a page's ink that must fall inside some
    # extracted block's bbox before the page is trusted.
    coverage_min: float = 0.90
    coverage_enabled: bool = True
    # Question reconstruction: pair scores in [lo, hi] are genuinely ambiguous
    # and are the only ones worth spending an LLM call on.
    reconstruct_join_threshold: float = 0.55
    reconstruct_ambiguous_lo: float = 0.40
    reconstruct_ambiguous_hi: float = 0.70
    reconstruct_use_llm: bool = True

    # ── convenience ──────────────────────────────────────────────────────
    def has_gemini_key(self) -> bool:
        return self.gemini_api_key is not None and bool(
            self.gemini_api_key.get_secret_value()
        )

    def resolved_provider(self) -> ProviderName:
        """Auto-fall back to the offline provider if a key is missing.

        Keeps the pipeline runnable end-to-end (plumbing, provenance, assets,
        graph, eval) even before a key is added — the fidelity of *content*
        simply drops to geometry-only until Gemini is available.
        """
        if self.llm_provider == "gemini" and not self.has_gemini_key():
            return "null"
        return self.llm_provider

    def models_snapshot(self) -> dict[str, str]:
        return {
            "vision": self.vision_model,
            "vision_escalation": self.vision_escalation_model,
            "reasoning": self.reasoning_model,
            "embedding": self.embedding_model,
        }

    def env_snapshot(self) -> dict[str, str]:
        """Non-secret environment facts, recorded in run provenance."""
        return {
            "python": platform.python_version(),
            "platform": platform.platform(),
        }


_settings: Optional[Settings] = None


def get_settings(reload: bool = False) -> Settings:
    global _settings
    if _settings is None or reload:
        _settings = Settings()
    return _settings
