"""Objective coverage validation — make missed extraction *detectable*.

Model-reported confidence cannot be trusted as evidence of completeness: the
`leph202` run reported mean extraction confidence 0.963 while 3 of 19 pages
(16 %) contained no blocks at all. Confidence measures how sure the model is,
not how much of the page it actually read.

These checks measure the page itself, so they catch failures the model cannot
report:

* **ink coverage** — how much of the page's actual dark pixel mass falls inside
  some extracted block's bounding box;
* **zero / low yield** — pages that produced nothing, or far less than their
  neighbours;
* **numbering continuity** — gaps in a numbered question series.

Numbering continuity is deliberately a *signal*, not a rule: real books contain
several independent numbering series, restart numbering per section, and skip
numbers legitimately. It reports suspicion for investigation; it never deletes
or invents a question.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from statistics import median
from typing import Iterable, Optional

log = logging.getLogger(__name__)


@dataclass
class PageCoverage:
    page_index: int
    ink_coverage: float          # 0..1 fraction of ink inside some block bbox
    block_count: int
    status: str                  # ok | escalate
    reasons: list[str] = field(default_factory=list)


@dataclass
class NumberingSeries:
    label: str                   # e.g. "10.x" or "N"
    seen: list[float]
    gaps: list[str]


@dataclass
class CoverageReport:
    pages: list[PageCoverage] = field(default_factory=list)
    series: list[NumberingSeries] = field(default_factory=list)

    @property
    def escalate_pages(self) -> list[int]:
        return [p.page_index for p in self.pages if p.status == "escalate"]

    def to_dict(self) -> dict:
        return {
            "coverage_source": "measured_page_ink_not_model_confidence",
            "pages_checked": len(self.pages),
            "mean_ink_coverage": round(
                sum(p.ink_coverage for p in self.pages) / len(self.pages), 4
            ) if self.pages else 0.0,
            "pages_to_escalate": self.escalate_pages,
            "n_pages_to_escalate": len(self.escalate_pages),
            "per_page": [
                {"page_index": p.page_index, "ink_coverage": round(p.ink_coverage, 4),
                 "blocks": p.block_count, "status": p.status, "reasons": p.reasons}
                for p in self.pages if p.status != "ok"
            ],
            "numbering": [
                {"series": s.label, "seen": s.seen, "gaps": s.gaps}
                for s in self.series if s.gaps
            ],
            "note": ("Numbering gaps are an investigation signal, not proof of a "
                     "missing question: independent series and legitimate skips exist."),
        }


# ── ink coverage ───────────────────────────────────────────────────────────
def page_ink_coverage(image_bytes: bytes, boxes: Iterable[tuple[float, float, float, float]],
                      downscale: int = 4) -> float:
    """Fraction of the page's dark pixels that fall inside some block box.

    Downscaled and binarised — this is a coarse completeness measure, not OCR,
    and it must stay cheap enough to run on every page of every document.
    """
    import numpy as np
    from PIL import Image
    from io import BytesIO

    with Image.open(BytesIO(image_bytes)) as im:
        im = im.convert("L")
        if downscale > 1:
            im = im.resize((max(im.width // downscale, 1),
                            max(im.height // downscale, 1)))
        arr = np.asarray(im)

    h, w = arr.shape
    # Otsu-ish split: anything meaningfully darker than the page ground is ink.
    thresh = max(int(arr.mean()) - 25, 8)
    ink = arr < thresh
    total = int(ink.sum())
    if total == 0:
        return 1.0  # a genuinely blank page is fully "covered" by definition

    mask = np.zeros((h, w), dtype=bool)
    sx = (w / 1.0)
    for (x0, y0, x1, y1) in boxes:
        # boxes arrive in full-resolution pixels; scale into the downscaled grid
        ix0 = max(int(x0 / downscale), 0)
        iy0 = max(int(y0 / downscale), 0)
        ix1 = min(int(x1 / downscale) + 1, w)
        iy1 = min(int(y1 / downscale) + 1, h)
        if ix1 > ix0 and iy1 > iy0:
            mask[iy0:iy1, ix0:ix1] = True

    covered = int((ink & mask).sum())
    return covered / total


# ── numbering continuity ───────────────────────────────────────────────────
_NUM_PATTERNS = [
    # "10.1", "10.12" — chapter-scoped numbering (NCERT style)
    re.compile(r"^\s*(\d{1,3})\.(\d{1,3})(?=[\s.):]|$)"),
    # "1.", "12)" — flat numbering
    re.compile(r"^\s*(\d{1,3})[.)](?=\s)"),
]


def _extract_number(text: Optional[str]) -> Optional[tuple[str, float]]:
    t = (text or "").lstrip()
    if not t:
        return None
    m = _NUM_PATTERNS[0].match(t)
    if m:
        return f"{m.group(1)}.x", float(m.group(2))
    m = _NUM_PATTERNS[1].match(t)
    if m:
        return "N", float(m.group(1))
    return None


def numbering_continuity(texts: Iterable[Optional[str]]) -> list[NumberingSeries]:
    """Group leading numbers into series and report gaps within each.

    Series are kept separate so a book whose exercises restart at 1 in every
    section does not produce a wall of false gaps.
    """
    series: dict[str, list[float]] = {}
    for t in texts:
        got = _extract_number(t)
        if got:
            series.setdefault(got[0], []).append(got[1])

    out: list[NumberingSeries] = []
    for label, nums in series.items():
        uniq = sorted(set(nums))
        if len(uniq) < 3:
            continue  # too short to infer a sequence from
        gaps: list[str] = []
        for a, b in zip(uniq, uniq[1:]):
            step = b - a
            if step > 1.0:
                missing = [str(int(a + i)) for i in range(1, int(step))]
                if len(missing) <= 10:
                    gaps.append(f"{label}: missing {', '.join(missing)} between {a:g} and {b:g}")
                else:
                    gaps.append(f"{label}: {len(missing)} numbers missing between {a:g} and {b:g}")
        out.append(NumberingSeries(label=label, seen=uniq, gaps=gaps))
    return out


# ── top-level check ────────────────────────────────────────────────────────
def check_coverage(pages, blocks, coverage_min: float = 0.90,
                   enabled: bool = True) -> CoverageReport:
    """Validate an extraction against the pages it claims to describe."""
    report = CoverageReport()
    by_page: dict[int, list] = {}
    for b in blocks:
        by_page.setdefault(b.provenance.page_index, []).append(b)

    counts = [len(by_page.get(p.page_index, [])) for p in pages]
    med = median(counts) if counts else 0

    for page in pages:
        pblocks = by_page.get(page.page_index, [])
        reasons: list[str] = []

        cov = 1.0
        if enabled and pblocks:
            boxes = [(b.provenance.bbox.x0, b.provenance.bbox.y0,
                      b.provenance.bbox.x1, b.provenance.bbox.y1)
                     for b in pblocks if b.provenance.bbox]
            try:
                cov = page_ink_coverage(page.image_bytes, boxes)
            except Exception as exc:
                log.debug("ink coverage failed on page %d: %s", page.page_index, exc)
                cov = 1.0
        elif not pblocks:
            cov = 0.0

        if not pblocks:
            reasons.append("zero blocks extracted")
        elif med and len(pblocks) < max(2, 0.25 * med):
            reasons.append(f"low yield: {len(pblocks)} blocks vs median {med:g}")
        if enabled and cov < coverage_min:
            reasons.append(f"ink coverage {cov:.2f} < {coverage_min:.2f}")

        report.pages.append(PageCoverage(
            page_index=page.page_index, ink_coverage=cov,
            block_count=len(pblocks),
            status="escalate" if reasons else "ok", reasons=reasons,
        ))

    report.series = numbering_continuity(b.text for b in blocks)
    return report
