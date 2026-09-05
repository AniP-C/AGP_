"""Process-wide API/cache counters for the cost report. Reset per run."""
from __future__ import annotations

from collections import Counter

_CALLS: Counter = Counter()
_CACHE: Counter = Counter()


def reset() -> None:
    _CALLS.clear()
    _CACHE.clear()


def call(kind: str, n: int = 1) -> None:
    _CALLS[kind] += n


def cache(hit: bool) -> None:
    _CACHE["hit" if hit else "miss"] += 1


def snapshot() -> dict:
    total = _CACHE["hit"] + _CACHE["miss"]
    return {
        "api_calls": dict(_CALLS),
        "api_calls_total": sum(_CALLS.values()),
        "cache_hits": _CACHE["hit"],
        "cache_misses": _CACHE["miss"],
        "cache_hit_rate_pct": round(100 * _CACHE["hit"] / total, 1) if total else 0.0,
    }
