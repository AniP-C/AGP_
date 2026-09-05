"""Deterministic question_type classification + option parsing.

A rule cascade over the (already structured) text. Specific/format types win over
generic descriptive ones; descriptive questions fall back to short/long by marks,
else conceptual. Anything the cascade can't decide returns ``unknown`` (0.0), a
signal for the optional LLM refinement pass.
"""
from __future__ import annotations

import re as _re

# ── question-bearing vs prose sections ─────────────────────────────────────
# A layout parser reports "text"/"list_item", not "question", so numbering alone
# cannot separate an exercise from a numbered SUMMARY bullet — both look like
# "N. <sentence>". The document's own section headings do separate them, and
# every publisher signposts these sections somehow, so we key off the heading
# rather than off a fixed template.
_QUESTION_SECTIONS = _re.compile(
    r"\b(exercise|question|problem|assessment|practice|activit|worksheet|"
    r"quiz|test|revision|competency|case[\s-]based|assignment)", _re.I)
_PROSE_SECTIONS = _re.compile(
    r"\b(summary|points? to ponder|key ?(terms|words|points)|glossary|"
    r"recap|conclusion|introduction|did you know|reference|bibliograph|"
    r"learning objective|index|content)", _re.I)

# Interrogative or instructional evidence that a sentence ASKS something.
_INTERROGATIVE = _re.compile(
    r"(\?)|^\s*(?:\(?[a-z0-9]{1,4}[.)]\s*)?"
    r"(what|why|how|when|where|which|who|whose|is|are|do|does|did|can|could|"
    r"will|would|should|state|define|explain|describe|calculate|find|"
    r"determine|derive|show|prove|compute|estimate|evaluate|list|name|"
    r"identify|compare|distinguish|differentiate|discuss|obtain|draw|"
    r"sketch|verify|justify|suggest|choose|select|fill|match|write)\b",
    _re.I)


def section_kind(title: str | None) -> str:
    """Classify a section heading as question-bearing, prose, or unknown."""
    t = (title or "").strip()
    if not t:
        return "unknown"
    if _PROSE_SECTIONS.search(t):
        return "prose"
    if _QUESTION_SECTIONS.search(t):
        return "question"
    return "unknown"


def looks_interrogative(text: str | None) -> bool:
    """Whether the text actually asks or instructs, rather than states."""
    t = (text or "").strip()
    if not t:
        return False
    # strip a leading enumerator so "10.2 What is..." is judged on "What is..."
    t = _re.sub(r"^\s*(?:Q\s*\.?\s*)?\(?\d{1,3}(?:\.\d{1,3})?\)?[.)]?\s+", "", t)
    return bool(_INTERROGATIVE.search(t))

import re

from ..schemas import QuestionOption

_OPT_A_D = re.compile(r"\(\s*([a-d])\s*\)")
_OPT_ROMAN = re.compile(r"\(\s*(i{1,3}|iv|v|vi{1,3}|ix|x)\s*\)", re.I)
_OPT_UP_ROMAN = re.compile(r"\(\s*(I{1,3}|IV|V|VI{1,3}|IX|X)\s*\)")

_ASSERTION = re.compile(r"assertion\s*\(?\s*a", re.I)
_REASON = re.compile(r"reason\s*\(?\s*r", re.I)
_BLANK = re.compile(r"…{2,}|\.{4,}|_{3,}|\bfill in the blank", re.I)
_TF = re.compile(r"true or false|state whether.*(true|false)", re.I)
_MATCH = re.compile(r"match the following|column\s*(i|a|1)\b", re.I)
_DISTINGUISH = re.compile(r"distinguish between|differentiate between|difference between|\bcompare\b", re.I)
_ORDER = re.compile(r"\barrange\b|ascending order|descending order|increasing order|decreasing order|in order of", re.I)
_NUM_CUE = re.compile(r"\bcalculate\b|\bcompute\b|\bfind the\b|\bdetermine the\b|how (far|long|much|many)", re.I)
_NUM_UNIT = re.compile(r"\d+(\.\d+)?\s*(hz|khz|m/s|ms\W|metres?|meters?|\bm\b|\bs\b|cm|km|kg|db|°?c)", re.I)
_DESCRIPTIVE = re.compile(r"\bdefine\b|\bexplain\b|\bwhy\b|what is|what are|\bhow\b|\bdescribe\b|give reasons?|\bstate\b|\blist\b|\bwrite\b", re.I)
_ACTIVITY = re.compile(r"^\s*activity\b|\btake\b .*\band\b .*\bobserve\b", re.I)


def parse_options(text: str) -> list[QuestionOption]:
    for pat in (_OPT_A_D, _OPT_ROMAN, _OPT_UP_ROMAN):
        markers = [(m.start(), m.end(), m.group(1)) for m in pat.finditer(text)]
        if len(markers) >= 2:
            opts = []
            for i, (s, e, key) in enumerate(markers):
                end = markers[i + 1][0] if i + 1 < len(markers) else len(text)
                seg = text[e:end].strip(" .:\n\t")
                # drop a trailing bloom/marks tag that bleeds into the last option
                seg = re.sub(r"\s*\((Remember|Understand|Apply|Analyse|Analyze|Evaluate|Create)\)\s*\d*\s*$", "", seg, flags=re.I).strip()
                opts.append(QuestionOption(key=key.lower(), text=seg[:200]))
            # only accept if options look real (short-ish, >=2)
            if opts and sum(len(o.text) for o in opts) / len(opts) < 160:
                return opts
    return []


def _looks_mcq(text: str) -> bool:
    if len(_OPT_A_D.findall(text)) >= 3:
        return True
    if len(_OPT_UP_ROMAN.findall(text)) >= 3 or len(_OPT_ROMAN.findall(text)) >= 3:
        return True
    if "options:" in text.lower() and (len(_OPT_A_D.findall(text)) >= 2):
        return True
    return False


def classify_question_type(
    text: str, *, has_subparts: bool = False, is_case: bool = False,
    has_equation: bool = False, marks: float | None = None, is_activity: bool = False,
) -> tuple[str, float, list[str]]:
    t = text or ""
    ev: list[str] = []

    if is_case or (has_subparts and re.search(r"case[\s-]*based|read the (given )?extract|passage", t, re.I)):
        return "case_study", 0.9, ["case_passage+subparts" if has_subparts else "case_marker"]
    if _ASSERTION.search(t) and _REASON.search(t):
        return "assertion_reason", 0.95, ["assertion+reason"]
    if _looks_mcq(t):
        return "mcq", 0.9, ["option_grid"]
    if _BLANK.search(t):
        return "fill_blank", 0.9, ["blank_marker"]
    if _TF.search(t):
        return "true_false", 0.9, ["true_false_cue"]
    if _MATCH.search(t):
        return "matching", 0.9, ["match_cue"]
    if _DISTINGUISH.search(t):
        return "distinguish", 0.9, ["distinguish_cue"]
    if _ORDER.search(t):
        return "ordering", 0.85, ["order_cue"]
    if has_equation or _NUM_CUE.search(t) or (_NUM_UNIT.search(t) and "?" in t):
        ev.append("equation" if has_equation else "numeric_cue")
        return "numerical", 0.85, ev
    if is_activity or _ACTIVITY.search(t):
        return "activity", 0.8, ["activity_cue"]
    if _DESCRIPTIVE.search(t):
        if marks is not None and marks >= 3:
            return "long_answer", 0.7, ["descriptive+marks>=3"]
        if marks is not None and marks <= 2:
            return "short_answer", 0.7, ["descriptive+marks<=2"]
        return "conceptual", 0.6, ["descriptive"]
    if "?" in t:
        return "conceptual", 0.4, ["has_question_mark"]
    return "unknown", 0.0, []
