"""Gold evaluation for question extraction — recall AND completeness.

A single "question extraction accuracy" number hides the failure that matters
most here: a question that is *found* but *truncated* counts as a success under
naive matching, while being useless downstream. So this reports separate axes:

* **recall** — did we find every gold question at all?
* **completeness** — did we capture its FULL text? Measured as token coverage of
  the gold text by the extracted text, so a stem cut off halfway scores ~0.5
  rather than passing.
* **tree accuracy** — did we preserve the sub-question structure?
* **type / marks / provenance / asset-link accuracy** — reported separately,
  never averaged into one figure.

Answer association is evaluated elsewhere, so question quality can be judged
without answer ground truth (which the gold sets deliberately do not carry).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

_WORD = re.compile(r"[a-z0-9]+")

# Matching threshold for "this extracted question IS this gold question".
# Deliberately low: identification only needs the stem's opening to line up;
# how much of the rest we captured is what `completeness` measures.
MATCH_THRESHOLD = 0.35
COMPLETE_THRESHOLD = 0.90


def _tokens(text: Optional[str]) -> list[str]:
    return _WORD.findall((text or "").lower())


def _coverage(gold: str, got: str) -> float:
    """Fraction of gold's tokens present in got (multiset-aware)."""
    g = _tokens(gold)
    if not g:
        return 1.0
    from collections import Counter
    have = Counter(_tokens(got))
    found = 0
    for tok in g:
        if have.get(tok, 0) > 0:
            have[tok] -= 1
            found += 1
    return found / len(g)


def _walk(questions: Iterable) -> list:
    out = []
    for q in questions:
        out.append(q)
        out.extend(_walk(getattr(q, "sub_questions", None) or []))
    return out


def _full_text(q) -> str:
    """A question's own text plus its sub-questions'.

    A logical question includes its parts. Where the printed stem is only a
    number ("8. (A) ... (B) ..."), a correct extractor stores "8." on the parent
    and the content on the sub-parts — comparing gold against the parent's own
    text alone would score that as half-captured, penalising the extractor for
    doing the right thing.
    """
    parts = [getattr(q, "question_text", "") or ""]
    for s in (getattr(q, "sub_questions", None) or []):
        parts.append(_full_text(s))
    return " ".join(p for p in parts if p)


@dataclass
class QuestionMatch:
    gold_id: str
    matched_id: Optional[str] = None
    completeness: float = 0.0
    sub_recall: Optional[float] = None
    type_ok: Optional[bool] = None
    marks_ok: Optional[bool] = None
    page_ok: Optional[bool] = None
    assets_ok: Optional[bool] = None
    notes: list[str] = field(default_factory=list)


def evaluate_questions(questions: list, gold_path: str | Path) -> dict:
    """Compare an extracted question tree against a gold file."""
    path = Path(gold_path)
    if not path.exists():
        return {"status": "no_gold_file", "expected_at": str(path)}

    gold = json.loads(path.read_text(encoding="utf-8"))
    gold_items = gold.get("questions", gold if isinstance(gold, list) else [])
    flat = _walk(questions)

    matches: list[QuestionMatch] = []
    used: set[int] = set()

    for g in gold_items:
        gid = str(g.get("id") or g.get("question_number") or len(matches))
        gtext = g.get("question_text") or ""
        m = QuestionMatch(gold_id=gid)

        # best unclaimed candidate by token coverage
        best_i, best_cov = None, 0.0
        for i, q in enumerate(flat):
            if i in used:
                continue
            cov = _coverage(gtext, _full_text(q))
            if cov > best_cov:
                best_i, best_cov = i, cov

        if best_i is None or best_cov < MATCH_THRESHOLD:
            m.notes.append("not found")
            matches.append(m)
            continue

        used.add(best_i)
        q = flat[best_i]
        m.matched_id = getattr(q, "id", None)
        m.completeness = round(best_cov, 4)
        if best_cov < COMPLETE_THRESHOLD:
            m.notes.append(f"incomplete: {best_cov:.0%} of gold text captured")

        # sub-question recall
        gsubs = g.get("sub_questions") or []
        if gsubs:
            got_subs = getattr(q, "sub_questions", None) or []
            found = 0
            for gs in gsubs:
                gs_text = gs.get("question_text") if isinstance(gs, dict) else str(gs)
                if any(_coverage(gs_text, getattr(s, "question_text", "") or "")
                       >= MATCH_THRESHOLD for s in got_subs):
                    found += 1
            m.sub_recall = round(found / len(gsubs), 4)
            if found < len(gsubs):
                m.notes.append(f"sub-questions {found}/{len(gsubs)}")

        if g.get("question_type"):
            m.type_ok = (getattr(q, "question_type", None) == g["question_type"])
        if g.get("marks") is not None:
            got = getattr(q, "marks", None)
            m.marks_ok = bool(got) and float(got.value) == float(g["marks"])
        if g.get("page") is not None:
            pages = getattr(q, "provenance", None)
            got_pages = (pages.source_pages if pages else []) or []
            printed = (pages.printed_pages if pages else []) or []
            m.page_ok = int(g["page"]) in set(got_pages) | set(printed)
        if g.get("related_assets") is not None:
            want = len(g["related_assets"] or [])
            m.assets_ok = len(getattr(q, "related_assets", []) or []) >= want

        matches.append(m)

    found = [m for m in matches if m.matched_id]
    n = len(matches) or 1
    complete = [m for m in found if m.completeness >= COMPLETE_THRESHOLD]

    def _rate(vals: list[Optional[bool]]) -> Optional[float]:
        got = [v for v in vals if v is not None]
        return round(sum(got) / len(got), 4) if got else None

    subs = [m.sub_recall for m in found if m.sub_recall is not None]
    return {
        "status": "ok",
        "gold_file": str(path),
        "gold_questions": len(matches),
        # the headline pair — reported together, never averaged
        "question_recall": round(len(found) / n, 4),
        "question_completeness": round(len(complete) / n, 4),
        "mean_completeness": round(
            sum(m.completeness for m in found) / len(found), 4) if found else 0.0,
        "found_but_incomplete": len(found) - len(complete),
        "not_found": [m.gold_id for m in matches if not m.matched_id],
        "subquestion_recall": round(sum(subs) / len(subs), 4) if subs else None,
        "type_accuracy": _rate([m.type_ok for m in found]),
        "marks_accuracy": _rate([m.marks_ok for m in found]),
        "provenance_accuracy": _rate([m.page_ok for m in found]),
        "asset_link_accuracy": _rate([m.assets_ok for m in found]),
        "incomplete_detail": [
            {"gold_id": m.gold_id, "completeness": m.completeness, "notes": m.notes}
            for m in matches if m.notes
        ][:40],
        "note": ("question_recall counts questions FOUND; question_completeness "
                 "counts those whose full text was captured. A found-but-truncated "
                 "question is not a correct extraction."),
    }
