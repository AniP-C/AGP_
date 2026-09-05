"""Google Gemini adapter (default provider) via the unified ``google-genai`` SDK.

Kept deliberately thin: build a multimodal request (page image + structuring
prompt), ask for strict JSON, parse defensively into :class:`RawBlock` s. All
model ids come from config; none are hard-coded here.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .. import metrics
from ..config import Settings
from ..prompts import load_prompt
from .base import LLMProvider, PageExtraction, RawBlock

log = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, settings: Settings):
        # imported lazily so google-genai stays an optional dependency
        from google import genai
        from google.genai import types

        self._genai = genai
        self._types = types
        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self.vision_model = settings.vision_model
        self.embedding_model = settings.embedding_model

    # ── Phase 1: page structuring ──────────────────────────────────────────
    def extract_page(
        self,
        image_bytes: bytes,
        media_type: str,
        page_index: int,
        width: int,
        height: int,
        hint: Optional[str] = None,
        model: Optional[str] = None,
    ) -> PageExtraction:
        types = self._types
        vision_model = model or self.vision_model
        prompt = load_prompt("page_structuring").format(
            width=width, height=height, hint=hint or "(none)"
        )
        # No artificial output cap: dense pages (solved-question banks) emit a
        # lot of JSON. If a page still truncates, _parse degrades to 0 blocks
        # for that page (crash-safe) and the eval hooks flag it for re-run.
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
        )
        metrics.call("vision")
        resp = self.client.models.generate_content(
            model=vision_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type=media_type),
                prompt,
            ],
            config=config,
        )
        raw_blocks, printed_page, parsed_ok = self._parse(getattr(resp, "text", "") or "")
        truncated = self._is_truncated(resp)
        pe = PageExtraction(
            page_index=page_index, width=width, height=height,
            blocks=raw_blocks, extractor=f"vision:{vision_model}",
            model=vision_model, printed_page=printed_page,
            coord_space="norm1000",  # Gemini returns 0..1000 normalized boxes
            truncated=truncated, parser="vlm", parser_version=vision_model,
        )
        # A page yielding nothing is a failed call, not a blank page. Say which.
        if truncated:
            pe.mark_failed("truncated")
        elif not parsed_ok:
            pe.mark_failed("unparseable")
        elif not raw_blocks:
            pe.mark_failed("empty")
        return pe

    @staticmethod
    def _is_truncated(resp) -> bool:
        """True when the model stopped because it hit the output cap.

        Without this, a response cut off mid-JSON is indistinguishable from a
        genuinely empty page — both arrive as zero blocks.
        """
        for cand in (getattr(resp, "candidates", None) or []):
            reason = getattr(cand, "finish_reason", None)
            name = getattr(reason, "name", None) or str(reason or "")
            if name.upper().endswith("MAX_TOKENS"):
                return True
        return False

    @staticmethod
    def _parse(text: str) -> tuple[list[RawBlock], Optional[int], bool]:
        """Returns (blocks, printed_page, parsed_ok).

        ``parsed_ok`` distinguishes "the model returned valid JSON describing an
        empty page" from "we could not read the response at all" — previously
        both collapsed to zero blocks with no way to tell them apart.
        """
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{"):] if "{" in text else text
        data = None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end != -1:
                try:
                    data = json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    data = None
        if not isinstance(data, dict):
            log.warning("Gemini returned unparseable output; page marked FAILED")
            return [], None, False

        printed_page = data.get("printed_page")
        blocks: list[RawBlock] = []
        for i, raw in enumerate(data.get("blocks", [])):
            try:
                if raw.get("reading_order") is None:
                    raw["reading_order"] = i
                blocks.append(RawBlock(**raw))
            except Exception as exc:  # skip malformed block, keep the rest
                log.debug("skip malformed block %s: %s", i, exc)
        return blocks, printed_page, True

    # ── generic seams (available for later phases) ─────────────────────────
    def vision(self, image_bytes: bytes, prompt: str, media_type: str = "image/jpeg", **kw) -> str:
        types = self._types
        resp = self.client.models.generate_content(
            model=self.vision_model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=media_type), prompt],
            config=types.GenerateContentConfig(temperature=0.0),
        )
        return getattr(resp, "text", "") or ""

    def generate(self, prompt: str, **kw) -> str:
        resp = self.client.models.generate_content(
            model=self.settings.reasoning_model, contents=[prompt]
        )
        return getattr(resp, "text", "") or ""

    def generate_json(self, prompt: str, model: Optional[str] = None, **kw):
        types = self._types
        metrics.call("llm")
        resp = self.client.models.generate_content(
            model=model or self.settings.reasoning_model, contents=[prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.0),
        )
        text = (getattr(resp, "text", "") or "").strip()
        if text.startswith("```"):
            text = text.strip("`")
            text = text[text.find("{") if "{" in text else 0:]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            s, e = text.find("{"), text.rfind("}")
            if s != -1 and e != -1:
                try:
                    return json.loads(text[s:e + 1])
                except json.JSONDecodeError:
                    return None
            return None

    def embed(self, texts: list[str]) -> list[list[float]]:
        metrics.call("embed")
        resp = self.client.models.embed_content(model=self.embedding_model, contents=texts)
        return [list(e.values) for e in resp.embeddings]
