#!/usr/bin/env python
"""Remove poisoned extraction-cache entries written before the validity gate.

Entries holding zero blocks (or otherwise unreadable) were cached as if they
were real data. Because the cache is keyed by page hash, every later run read
the empty result back and reported the document as reproducible — the loss was
frozen and invisible. This deletes those entries so the next run re-extracts
the affected pages.

    python scripts/purge_invalid_cache.py --dry-run
    python scripts/purge_invalid_cache.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agp_extract.cache.store import ExtractionCache  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", default=".cache")
    ap.add_argument("--dry-run", action="store_true",
                    help="list what would be removed, change nothing")
    args = ap.parse_args()

    cache = ExtractionCache(args.cache_dir, enabled=True)
    removed = cache.purge_invalid(dry_run=args.dry_run)

    verb = "would remove" if args.dry_run else "removed"
    if not removed:
        print("no invalid cache entries found")
        return 0
    print(f"{verb} {len(removed)} invalid entr{'y' if len(removed) == 1 else 'ies'}:")
    for name in removed:
        print(f"  {name}")
    if args.dry_run:
        print("\nre-run without --dry-run to delete them")
    else:
        print("\nnext run will re-extract the affected pages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
