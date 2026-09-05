"""Cross-reference resolution.

Resolve phrases like "as shown in the figure", "the table above", "diagram (c)",
"equation (2)" to a concrete asset block, using deterministic rules:
label match → direction → proximity, scoped to the same page/section. When the
evidence is weak or ambiguous, the ref is left EXPLICITLY unresolved (status set,
``resolved_block_id`` stays None) — never guessed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import ASSET_TYPES, CanonicalDocument, ContentBlock

_WINDOW = 8  # max reading-order distance for an off-page proximity match

# which asset types satisfy a given textual hint
_HINT_TYPES = {
    "figure": {"figure", "image", "diagram"},
    "image": {"image", "figure"},
    "diagram": {"diagram", "figure"},
    "graph": {"figure", "diagram"},
    "table": {"table"},
    "equation": {"equation"},
    None: {"figure", "image", "diagram", "table", "equation"},
}
_LABEL = re.compile(r"\b(\d+\.\d+)\b|\(\s*([a-zA-Z0-9]{1,3})\s*\)|\bno\.?\s*(\d+)", re.I)


@dataclass
class _Asset:
    id: str
    type: str
    ro: int
    page: int
    section: str | None
    label_text: str


def _assets(doc: CanonicalDocument) -> list[_Asset]:
    out = []
    for b in doc.blocks:
        if b.type in ASSET_TYPES:
            out.append(_Asset(
                b.id, b.type.value, b.provenance.reading_order,
                b.provenance.page_index, b.section_id,
                ((b.caption or "") + " " + (b.text or "")).lower(),
            ))
    return out


def _label_token(raw: str) -> str | None:
    m = _LABEL.search(raw)
    if not m:
        return None
    return (m.group(1) or m.group(2) or m.group(3) or "").lower() or None


def resolve_crossrefs(doc: CanonicalDocument) -> dict:
    assets = _assets(doc)
    total = resolved = ambiguous = no_cand = 0

    for b in doc.blocks:
        if not b.refs:
            continue
        for ref in b.refs:
            total += 1
            want = _HINT_TYPES.get(ref.target_hint, _HINT_TYPES[None])
            cands = [a for a in assets if a.type in want and a.id != b.id]

            # 1) explicit label/number match ("diagram (c)", "equation (2)")
            token = _label_token(ref.raw_text)
            if token:
                labeled = [a for a in cands if f"({token})" in a.label_text
                           or f" {token} " in a.label_text or a.label_text.strip().endswith(token)]
                if len(labeled) == 1:
                    _set(ref, labeled[0].id, "resolved", 0.9, "label"); resolved += 1; continue
                if len(labeled) > 1:
                    _set(ref, None, "ambiguous", 0.0, "label"); ambiguous += 1; continue

            # 2) direction / proximity, scoped to same page first
            pool = [a for a in cands if a.page == b.provenance.page_index] or cands
            d = (ref.direction or "").lower()
            if d in ("above", "previous"):
                pool = [a for a in pool if a.ro < b.provenance.reading_order] or pool
            elif d in ("below", "following", "next"):
                pool = [a for a in pool if a.ro > b.provenance.reading_order] or pool
            if not pool:
                _set(ref, None, "no_candidate", 0.0, None); no_cand += 1; continue

            pool.sort(key=lambda a: abs(a.ro - b.provenance.reading_order))
            best = pool[0]
            dist = abs(best.ro - b.provenance.reading_order)
            same_page = best.page == b.provenance.page_index
            if not same_page and dist > _WINDOW and best.section != b.section_id:
                _set(ref, None, "no_candidate", 0.0, None); no_cand += 1; continue

            ties = [a for a in pool if abs(a.ro - b.provenance.reading_order) == dist]
            if len(ties) > 1:
                _set(ref, None, "ambiguous", 0.0, "proximity"); ambiguous += 1; continue

            method = "direction" if d else "proximity"
            conf = 0.75 if d else (0.7 if same_page else 0.55)
            _set(ref, best.id, "resolved", conf, method); resolved += 1

    return {
        "refs_total": total, "refs_resolved": resolved,
        "refs_ambiguous": ambiguous, "refs_no_candidate": no_cand,
        "resolution_rate_pct": round(100 * resolved / total, 1) if total else 0.0,
    }


def _set(ref, block_id, status, conf, method):
    ref.resolved_block_id = block_id
    ref.status = status
    ref.resolution_confidence = conf
    ref.resolution_method = method
