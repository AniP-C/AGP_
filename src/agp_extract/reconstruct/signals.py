"""Boundary scoring — does block B continue the question that block A started?

Three independent signal families, kept separate and individually inspectable
so a decision can always be explained. They are never collapsed into one opaque
number before the decision is made.

* **structural** — reading order, section/parent identity, column and page
  continuity, geometry.
* **marker** — ``Example N``, ``Q1``, ``(a)``, ``Ans.``, ``OR``. These are
  scored *evidence*, never gates. A book that numbers nothing must still work,
  and a publisher's private convention must never become load-bearing.
* **semantic** — sentence completion, dangling punctuation, casing. Determined
  deterministically here; only genuinely ambiguous pairs are worth an LLM call.

Design rule: any single family can veto (a hard stop such as "B is an answer
block"), but no single family can force a join on its own.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..schemas import BlockType

# ── markers (optional signals, never requirements) ─────────────────────────
_ENUM_START = re.compile(
    r"""^\s*(?:
        (?:Example|Illustration|Activity|Exercise)\s+\d+     # Example 12
      | Q\s*\.?\s*\d+                                        # Q3 / Q.3
      | \d{1,3}\.\d{1,3}(?=[\s.):])                          # 10.2  (NCERT)
      | \d{1,3}\s*[.)](?=\s)                                 # 7.  /  7)
      | \(\s*[A-Za-z]\s*\)                                   # (a) / (A)
      | \(\s*(?:i{1,3}|iv|v|vi{1,3}|ix|x)\s*\)               # (iv)
    )""",
    re.VERBOSE | re.IGNORECASE,
)
_ANSWER_START = re.compile(
    r"^\s*(?:Ans(?:wer)?\s*\.?|Sol(?:ution)?\s*\.?|Explanation\s*:?|Hint\s*:?)",
    re.IGNORECASE)
_OR_MARKER = re.compile(r"^\s*OR\s*$", re.IGNORECASE | re.MULTILINE)

_SENTENCE_END = re.compile(r"[.?!…]['\")\]”]?\s*$")
_DANGLING_END = re.compile(r"(?:[,;:–—-]|\b(?:and|or|of|the|a|an|to|in|for|with|that|which|is|are|be)\b)\s*$",
                           re.IGNORECASE)
_CONTINUES_WORD = re.compile(r"^\s*(?:and|or|but|so|then|which|that|because|where|when|while|hence|therefore|thus)\b",
                             re.IGNORECASE)

# Types that can plausibly continue a question's body.
_CONTINUABLE = {
    BlockType.PARAGRAPH, BlockType.LIST, BlockType.LIST_ITEM, BlockType.QUESTION,
    BlockType.EQUATION, BlockType.TABLE, BlockType.OTHER,
}
# Types that unambiguously end a question.
_HARD_STOP = {
    BlockType.ANSWER, BlockType.SOLUTION, BlockType.EXPLANATION,
    BlockType.HEADING, BlockType.SECTION, BlockType.SUBSECTION, BlockType.TOPIC,
    BlockType.CHAPTER, BlockType.BANNER, BlockType.LEARNING_OBJECTIVES,
    BlockType.WORKED_EXAMPLE, BlockType.ACTIVITY, BlockType.TOC,
}


@dataclass
class JoinScore:
    score: float
    hard_stop: bool = False
    evidence: list[str] = field(default_factory=list)

    @property
    def decision(self) -> str:
        if self.hard_stop:
            return "stop"
        return "join" if self.score >= 0.55 else "stop"


def has_enumerator(text: Optional[str]) -> bool:
    return bool(_ENUM_START.match((text or "").lstrip()))


def leading_enumerator(text: Optional[str]) -> Optional[str]:
    m = _ENUM_START.match((text or "").lstrip())
    return m.group(0).strip() if m else None


def is_answer_start(text: Optional[str]) -> bool:
    return bool(_ANSWER_START.match((text or "").lstrip()))


def is_or_marker(text: Optional[str]) -> bool:
    t = (text or "").strip()
    return bool(t) and bool(_OR_MARKER.match(t))


def score_join(a, b, *, page_dims: dict | None = None) -> JoinScore:
    """Score whether ``b`` continues the logical question containing ``a``."""
    ev: list[str] = []

    # ── hard stops (any one is decisive) ───────────────────────────────────
    if b.type in _HARD_STOP:
        return JoinScore(0.0, True, [f"stop:block_type={b.type.value}"])
    if is_answer_start(b.text):
        return JoinScore(0.0, True, ["stop:starts_with_answer_marker"])
    if is_or_marker(b.text):
        return JoinScore(0.0, True, ["stop:or_separator"])
    if b.type not in _CONTINUABLE:
        return JoinScore(0.0, True, [f"stop:non_continuable={b.type.value}"])
    # A new top-level enumerator starts a new question — unless it is a
    # sub-part enumerator, which the reconstructor handles as nesting.
    if has_enumerator(b.text):
        return JoinScore(0.0, True, [f"stop:new_enumerator={leading_enumerator(b.text)!r}"])

    score = 0.0

    # ── structural ─────────────────────────────────────────────────────────
    same_section = (a.section_id or None) == (b.section_id or None)
    if same_section:
        score += 0.15
        ev.append("same_section")
    else:
        score -= 0.25
        ev.append("different_section")

    if a.structure.part_of_id and a.structure.part_of_id == b.structure.part_of_id:
        score += 0.15
        ev.append("same_parent")

    same_page = a.provenance.page_index == b.provenance.page_index
    same_col = a.provenance.column == b.provenance.column
    if same_page and same_col:
        score += 0.15
        ev.append("same_page_and_column")
    else:
        # Crossing a column or page is normal for a long question, and is
        # exactly the case the old geometry-gated code refused to consider.
        score += 0.05
        ev.append("crosses_column_or_page")

    if same_page and same_col and a.provenance.bbox and b.provenance.bbox:
        gap = b.provenance.bbox.y0 - a.provenance.bbox.y1
        h = (page_dims or {}).get(a.provenance.page_index, (0, 1))[1] or 1
        if 0 <= gap < 0.04 * h:
            score += 0.15
            ev.append(f"tight_vertical_gap={gap:.0f}px")
        elif gap > 0.12 * h:
            score -= 0.15
            ev.append("large_vertical_gap")

    # ── semantic (deterministic) ───────────────────────────────────────────
    atext = (a.text or "").rstrip()
    btext = (b.text or "").lstrip()

    if atext and not _SENTENCE_END.search(atext):
        score += 0.35
        ev.append("predecessor_ends_mid_sentence")
    if atext and _DANGLING_END.search(atext):
        score += 0.15
        ev.append("predecessor_ends_on_connective")
    if btext[:1].islower():
        score += 0.25
        ev.append("successor_starts_lowercase")
    if _CONTINUES_WORD.match(btext):
        score += 0.10
        ev.append("successor_starts_with_connective")
    if atext and _SENTENCE_END.search(atext) and btext[:1].isupper():
        score -= 0.20
        ev.append("clean_sentence_boundary")

    # An asset or equation immediately inside a question body belongs to it.
    if b.type in (BlockType.EQUATION, BlockType.TABLE):
        score += 0.10
        ev.append(f"embedded_{b.type.value}")

    return JoinScore(max(0.0, min(score, 1.0)), False, ev)


# ── intra-block splitting ──────────────────────────────────────────────────
_SUBPART_LINE = re.compile(
    r"(?m)^[ \t]*(\(\s*[a-zA-Z]\s*\)|\(\s*(?:i{1,3}|iv|v|vi{1,3}|ix|x)\s*\))\s+")

# Any parenthesised single letter / roman numeral followed by real text. Where
# it is ALLOWED to count as a sub-part marker is decided separately, below.
_SUBPART_ANY = re.compile(
    r"(\(\s*[a-zA-Z]\s*\)|\(\s*(?:i{1,3}|iv|v|vi{1,3}|ix|x)\s*\))\s+(?=\S)")

# A leading question number, so "10.3 (a) ..." counts as (a) starting the run
# even though it is not at the start of a line.
_LEADING_QNUM = re.compile(
    r"^\s*(?:Q\s*\.?\s*)?(?:\d{1,3}(?:\.\d{1,3})?|[A-Z])\s*[.):]?\s*$")
_SENTENCE_BEFORE = re.compile(r"[.?!:;]['\")\]]?\s*$")
_ROMAN_VALUES = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5,
                 "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


def _rank_alpha(core: str) -> Optional[int]:
    return ord(core) - ord("a") + 1 if len(core) == 1 and core.isalpha() else None


def _rank_roman(core: str) -> Optional[int]:
    return _ROMAN_VALUES.get(core)


def _is_consecutive(labels: list[str]) -> bool:
    """Markers must form a run (a,b,c / i,ii,iii), not scattered parentheticals.

    This is the guard that lets us relax WHERE a marker may appear without
    turning every "(Speed of light in vacuum is ...)" aside into a sub-question.

    Evaluated under BOTH the alphabetic and roman readings, because "(i)" is
    genuinely ambiguous — letter nine, or roman one — and which it is only
    becomes clear from the rest of the run.
    """
    cores = [x.strip("() \t").lower() for x in labels]
    for rank in (_rank_alpha, _rank_roman):
        ranks = [rank(c) for c in cores]
        if any(r is None for r in ranks):
            continue
        if all(b - a == 1 for a, b in zip(ranks, ranks[1:])):
            return True
    return False

# An answer marker inside a block ends the QUESTION region. Everything after it
# is the worked solution — which frequently repeats the same (a)/(b)/(c) labels.
# Without this bound, splitting a solved example produces one sub-question per
# part AND one per solution part, doubling the tree with answers masquerading
# as questions.
_ANSWER_BOUNDARY = re.compile(
    # MUST be at the start of a line. Matching mid-sentence made the ordinary
    # word "answer" a boundary: "Read the given extract and answer the
    # following questions" was cut to "Read the given extract and", silently
    # truncating a whole case-study passage.
    r"^[ \t]*(?:"
    # a LABEL: the marker word followed immediately by ':' or '.' — this is
    # what distinguishes "Ans." / "Answer:" from "Answer the following:"
    r"(?:Ans(?:wer)?s?|Sol(?:ution)?s?|Explanation|Working|Hint)\s*[:.]"
    # or the marker alone on its own line
    r"|(?:Ans(?:wer)?s?|Sol(?:ution)?s?|Explanation|Working|Hint)\s*$"
    # or "Solution <content>" running straight on — unlike "answer", the word
    # "solution" is not used as a verb, so this stays unambiguous
    r"|Sol(?:ution)?s?\s+(?=\S)"
    r")",
    re.IGNORECASE | re.MULTILINE)


def answer_boundary_offset(text: Optional[str]) -> Optional[int]:
    """Offset where a block stops being question and starts being answer."""
    if not text:
        return None
    m = _ANSWER_BOUNDARY.search(text)
    return m.start() if m else None


def is_bare_question_number(text: Optional[str]) -> bool:
    """True when ``text`` is only a question label ("10.3", "7.", "Q3")."""
    return bool(text is not None and _LEADING_QNUM.match(text or ""))


def _marker_position_ok(text: str, pos: int) -> bool:
    """Whether a marker at ``pos`` is in a position that can start a sub-part.

    Three admissible positions, each of which a human reader also treats as the
    start of a new part:

    * the start of a line;
    * the start of the block, after an optional question number — this is the
      "10.3 (a) ..." case, where (a) opens the run on the same line;
    * immediately after a sentence ends.

    Anything else — a parenthetical mid-sentence — is not a marker.
    """
    before = text[:pos]
    line_start = before.rfind("\n") + 1
    if not before[line_start:].strip():
        return True                              # start of a line
    if _LEADING_QNUM.match(before):
        return True                              # "10.3 " then (a)
    return bool(_SENTENCE_BEFORE.search(before))


def is_consecutive_run(labels: list[str]) -> bool:
    """Public form of the run check, for callers with external corroboration."""
    return _is_consecutive(labels)


# Multi-character roman numerals are unambiguous ("ii", "iii", "iv", ...). A
# single "i", "v" or "x" is genuinely ambiguous on its own — it reads as
# either a roman one/five/ten or an ordinary letter — and is resolved by
# context in `_group_markers` below: it joins the roman group only when an
# unambiguous roman marker also appears among the candidates.
_ROMAN_UNAMBIGUOUS = {"ii", "iii", "iv", "vi", "vii", "viii", "ix"}
_ROMAN_AMBIGUOUS = {"i", "v", "x"}


def _classify_marker(label: str) -> str:
    core = label.strip("() \t").lower()
    if core in _ROMAN_UNAMBIGUOUS:
        return "roman"
    if core in _ROMAN_AMBIGUOUS:
        return "romanish"          # resolved below once the whole list is known
    if len(core) == 1 and label.strip("() \t").isupper():
        return "upper"
    if len(core) == 1 and label.strip("() \t").islower():
        return "lower"
    return "other"


def _group_markers(cands: list[tuple[int, str]]) -> dict[str, list[tuple[int, str]]]:
    """Split flat candidates into same-level enumeration groups.

    A block frequently nests two enumeration levels in flat text with no
    indentation to tell them apart — e.g. "(A) ... (i) ... (ii) ... (iii) ...
    (B) ..." — where (A)/(B) are the real sub-questions and (i)/(ii)/(iii) are
    a sub-list belonging to (A). Feeding every marker to one consecutiveness
    check mixes ranks 1,2,3 (roman) with 1,2 (upper) and the whole run fails,
    so the block never splits at all. Grouping by case/kind first lets each
    level be judged on its own.
    """
    classes = [_classify_marker(core) for _, core in cands]
    has_unambiguous_roman = "roman" in classes
    groups: dict[str, list[tuple[int, str]]] = {}
    for (pos, core), cls in zip(cands, classes):
        if cls == "romanish":
            cls = "roman" if has_unambiguous_roman else "lower"
        groups.setdefault(cls, []).append((pos, core))
    return groups


def find_subpart_offsets(text: Optional[str], min_markers: int = 2
                         ) -> list[tuple[int, str]]:
    """Offsets of sub-part markers inside one block.

    Recovers sub-question trees that live entirely inside a single block —
    NCERT's 10.2 (a)-(d) and 10.3 (a)-(b), which block-level grouping could
    never see because there is only one block to group.

    Two guards keep this from shredding ordinary prose: a marker must sit in an
    admissible position (:func:`_marker_position_ok`), and the markers must form
    a consecutive run (:func:`_is_consecutive`), so a stray "(a)" or an aside
    like "(Speed of light in vacuum is ...)" cannot manufacture sub-questions.

    When several groups are all internally consecutive (a nested list), the
    one spanning the most of the block wins — that is the outer, question-level
    enumeration; a nested sub-list is judged again, one level down, when its own
    parent sub-question is split in turn.
    """
    if not text:
        return []
    cands = [(m.start(), m.group(1).strip()) for m in _SUBPART_ANY.finditer(text)
             if _marker_position_ok(text, m.start())]
    if len(cands) < max(min_markers, 1):
        return []

    best: Optional[list[tuple[int, str]]] = None
    best_span = -1
    for group in _group_markers(cands).values():
        if len(group) < max(min_markers, 1):
            continue
        if len(group) >= 2 and not _is_consecutive([c[1] for c in group]):
            continue
        span = group[-1][0] - group[0][0]
        if span > best_span:
            best, best_span = group, span
    return best or []


def looks_like_mcq_options(text: Optional[str]) -> bool:
    """True when the (a)/(b)/(c) run is an OPTION list, not a sub-question tree.

    Distinguishing these matters: the old code ran option parsing on any
    enumerated run and therefore classified NCERT 10.2 — a short-answer
    question with sub-parts — as an MCQ.
    """
    marks = find_subpart_offsets(text)
    if len(marks) < 2:
        return False
    t = text or ""
    bodies = []
    for i, (pos, _) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(t)
        bodies.append(t[pos:end].strip())
    # Options are short and rarely contain sentence-ending punctuation.
    short = sum(1 for x in bodies if len(x) <= 60)
    unpunctuated = sum(1 for x in bodies if not _SENTENCE_END.search(x))
    return short >= max(2, int(0.75 * len(bodies))) and unpunctuated >= len(bodies) - 1
