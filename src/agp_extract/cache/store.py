"""Page-hash keyed cache for expensive extraction results (cost control).

Key = page-bytes SHA-256 + extractor model + prompt version. Same page + same
model + same prompt ⇒ reuse the cached blocks instead of re-calling the model.
Bumping ``PROMPT_VERSION`` (in the package __init__) invalidates everything.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from ..providers.base import PageExtraction

log = logging.getLogger(__name__)


class ExtractionCache:
    def __init__(self, cache_dir: str | Path, enabled: bool = True):
        self.dir = Path(cache_dir) / "extractions"
        self.failure_dir = Path(cache_dir) / "failures"
        self.enabled = enabled
        if enabled:
            self.dir.mkdir(parents=True, exist_ok=True)

    def _key(self, page_sha: str, model: str, prompt_version: str) -> Path:
        safe_model = model.replace("/", "_").replace(":", "_")
        return self.dir / f"{page_sha}.{safe_model}.{prompt_version}.json"

    def get(self, page_sha: str, model: str, prompt_version: str) -> Optional[PageExtraction]:
        from .. import metrics
        if not self.enabled:
            return None
        path = self._key(page_sha, model, prompt_version)
        if not path.exists():
            metrics.cache(hit=False)
            return None
        try:
            pe = PageExtraction.model_validate_json(path.read_text(encoding="utf-8"))
            pe.from_cache = True
            metrics.cache(hit=True)
            return pe
        except Exception as exc:
            log.debug("cache read failed (%s): %s", path.name, exc)
            metrics.cache(hit=False)
            return None

    def put(self, page_sha: str, model: str, prompt_version: str, pe: PageExtraction) -> None:
        """Store an extraction — **validated results only**.

        A failed/truncated/empty extraction is written to a separate failure
        record instead. Caching a failure as if it were data is how a single bad
        response becomes permanent silent data loss: the next run reads it back,
        reports the document as reproducible, and nothing ever flags the hole.
        """
        if not self.enabled:
            return
        if not pe.is_valid():
            self.put_failure(page_sha, model, prompt_version, pe)
            return
        path = self._key(page_sha, model, prompt_version)
        path.write_text(pe.model_dump_json(indent=0), encoding="utf-8")

    # ── failure records ────────────────────────────────────────────────────
    def _failure_key(self, page_sha: str, model: str, prompt_version: str) -> Path:
        safe_model = model.replace("/", "_").replace(":", "_")
        return self.failure_dir / f"{page_sha}.{safe_model}.{prompt_version}.json"

    def put_failure(self, page_sha: str, model: str, prompt_version: str,
                    pe: PageExtraction) -> None:
        """Record a failed extraction so it is diagnosable but never served."""
        if not self.enabled:
            return
        self.failure_dir.mkdir(parents=True, exist_ok=True)
        if pe.status == "ok":  # empty but unflagged — name the reason explicitly
            pe = pe.model_copy(deep=True).mark_failed(
                "truncated" if pe.truncated else "empty")
        self._failure_key(page_sha, model, prompt_version).write_text(
            pe.model_dump_json(indent=0), encoding="utf-8")
        log.warning("page extraction failed (%s); not cached as valid: %s",
                    pe.failure_reason, page_sha[:12])

    def get_failure(self, page_sha: str, model: str, prompt_version: str
                    ) -> Optional[PageExtraction]:
        path = self._failure_key(page_sha, model, prompt_version)
        if not path.exists():
            return None
        try:
            return PageExtraction.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── migration ──────────────────────────────────────────────────────────
    def purge_invalid(self, dry_run: bool = False) -> list[str]:
        """Delete already-poisoned entries written before this gate existed.

        Returns the filenames removed (or that would be, when ``dry_run``).
        """
        removed: list[str] = []
        if not self.dir.exists():
            return removed
        for path in sorted(self.dir.glob("*.json")):
            try:
                pe = PageExtraction.model_validate_json(path.read_text(encoding="utf-8"))
                ok = pe.is_valid()
            except Exception:
                ok = False  # unreadable entry is also not trustworthy
            if not ok:
                removed.append(path.name)
                if not dry_run:
                    path.unlink()
        if removed:
            log.warning("purged %d invalid cache entr%s", len(removed),
                        "y" if len(removed) == 1 else "ies")
        return removed
