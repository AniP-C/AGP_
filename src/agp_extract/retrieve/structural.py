"""Structural channel — graph/structure-derived answer candidates.

Highest-priority evidence and the ONLY channel that can license a positive
association (see phase4). Everything it returns is in-scope: same worked
example / container (enumerator-matched), the adjacent solution run, the same
section, a referenced table/equation, or a continuation of one of those.
"""
from __future__ import annotations

import re
from collections import defaultdict

from ..schemas import BlockType, CanonicalDocument, ContentBlock

ANSWER_ELIGIBLE = {BlockType.ANSWER, BlockType.SOLUTION, BlockType.EXPLANATION,
                   BlockType.TABLE, BlockType.EQUATION}
_ASSET_ANSWER = {BlockType.TABLE, BlockType.EQUATION}
# skipped while scanning forward for the adjacent solution (they can sit between
# a question and its answer without ending it — e.g. a referenced figure)
_SKIP_FWD = {BlockType.FIGURE, BlockType.IMAGE, BlockType.DIAGRAM,
             BlockType.CAPTION, BlockType.NOISE, BlockType.BANNER}
_STOP_FWD = {BlockType.QUESTION, BlockType.WORKED_EXAMPLE}

# A short, fully-bracketed line sitting between a question and its answer is a
# source/attribution tag ("[NCERT Exemplar]", "[CBSE 2022]"), not the start of
# new prose. Treating it as the end of the answer run loses the answer entirely.
# Deliberately narrow — fully bracketed AND short AND at most one per run — so
# it cannot start swallowing real content and manufacture false associations.
_SOURCE_TAG = re.compile(r"^\s*[\[(][^\[\]()]{0,60}[\])]\s*$")
_MAX_TAG_SKIPS = 1


def _is_source_tag(b: ContentBlock) -> bool:
    return bool(_SOURCE_TAG.match((b.text or "").strip()))


# A heading that opens the answer rather than closing the question.
_ANSWER_HEADING = re.compile(
    r"^(?:Ans(?:wer)?s?|Sol(?:ution)?s?|Explanation|Working|Hint)\b\s*[:.]?$",
    re.IGNORECASE)


def _enum_matches(b: ContentBlock, e: str) -> bool:
    if b.structure.enumerator and b.structure.enumerator.lower() == e.lower():
        return True
    t = (b.text or "").strip()
    return bool(re.match(rf"(Ans\.?\s*)?\(\s*{re.escape(e)}\s*\)", t, re.I))


class StructuralAnswerRetriever:
    def __init__(self, doc: CanonicalDocument):
        self.doc = doc
        self.byid = {b.id: b for b in doc.blocks}
        self.children: dict[str, list[ContentBlock]] = defaultdict(list)
        self.by_section: dict[str, list[ContentBlock]] = defaultdict(list)
        for b in doc.blocks:
            if b.structure.part_of_id:
                self.children[b.structure.part_of_id].append(b)
            self.by_section[b.section_id or "_none"].append(b)
        for v in self.by_section.values():
            v.sort(key=lambda b: b.provenance.reading_order)

    def _forward_answer_run(self, qb: ContentBlock, limit: int = 5) -> list[ContentBlock]:
        """Collect the run of answer blocks following a question, skipping
        visual/furniture blocks (a referenced figure between Q and its Ans.) and
        stopping at the next question/heading/container."""
        out, cur, steps, tag_skips = [], qb, 0, 0
        while cur and cur.next_id and steps < 14 and len(out) < limit:
            nxt = self.byid.get(cur.next_id)
            steps += 1
            cur = nxt
            if nxt is None:
                break
            # A heading reading "Solution" / "Ans." INTRODUCES the answer; it
            # must not end the search for it. Any other heading does.
            if nxt.is_heading() and _ANSWER_HEADING.match((nxt.text or "").strip()):
                continue
            if nxt.type in _STOP_FWD or nxt.structure.group_role == "container" or nxt.is_heading():
                break
            if nxt.type in ANSWER_ELIGIBLE:
                out.append(nxt)
            elif nxt.type in _SKIP_FWD:
                continue
            elif not out and tag_skips < _MAX_TAG_SKIPS and _is_source_tag(nxt):
                # only before the run has started, and only once
                tag_skips += 1
                continue
            else:                              # paragraph/list/other → end of answer run
                break
        return out

    def candidates(self, q) -> dict[str, dict]:
        results: dict[str, dict] = {}

        def add(bid: str, rel: str, strength: float, channel: str) -> None:
            b = self.byid.get(bid)
            if not b:
                return
            if b.type not in ANSWER_ELIGIBLE:
                return
            cur = results.get(bid)
            if cur is None or strength > cur["structural_strength"]:
                results[bid] = {"relationship": rel, "structural_strength": strength,
                                "channels": set(cur["channels"]) | {channel} if cur else {channel}}
            else:
                cur["channels"].add(channel)

        if not q.provenance.source_blocks:
            return results
        qb = self.byid.get(q.provenance.source_blocks[0])
        if qb is None:
            return results

        # 1) same container answer blocks (case-based). Enumerator-matched → strong;
        #    a combined "Ans.(A)…(D)" block → still a candidate for every subpart.
        cont = qb.structure.part_of_id
        if cont:
            is_example = bool(self.byid.get(cont) and
                              self.byid[cont].structure.enumerator_style == "example")
            for b in self.children.get(cont, []):
                if b.type not in ANSWER_ELIGIBLE:
                    continue
                if q.question_number and _enum_matches(b, q.question_number):
                    add(b.id, "same_worked_example" if is_example else "same_container_enum",
                        0.95, "structural")
                else:
                    add(b.id, "same_worked_example" if is_example else "same_container",
                        0.85, "structural")

        # 1b) the container's shared solution run. A worked example often puts
        # one "Solution" block set AFTER all its sub-parts, so from sub-part (a)
        # the forward scan hits sibling (b) and stops — only the last sub-part
        # could ever reach the answers. Scan forward from the container's LAST
        # child instead and enumerator-match into that run, so (a) finds "(a)".
        if cont:
            kids = sorted(self.children.get(cont, []),
                          key=lambda b: b.provenance.reading_order)
            if kids:
                for b in self._forward_answer_run(kids[-1], limit=8):
                    if q.question_number and _enum_matches(b, q.question_number):
                        add(b.id, "container_solution_enum", 0.92, "structural")
                    else:
                        add(b.id, "container_solution_run", 0.7, "structural")

        # 2) adjacent solution run (split worked example / solved bank)
        for b in self._forward_answer_run(qb):
            add(b.id, "adjacent_solution", 0.9, "structural")

        # 3) referenced table/equation used as the answer (e.g. Distinguish → table)
        for aid in list(q.related_assets) + [r.resolved_block_id for r in qb.refs
                                             if r.status == "resolved" and r.resolved_block_id]:
            tb = self.byid.get(aid)
            if tb and tb.type in _ASSET_ANSWER:
                add(tb.id, "refers_to", 0.75, "reference")

        # 4) same-section answer-eligible fallback (lower strength)
        for b in self.by_section.get(qb.section_id or "_none", []):
            if b.type in ANSWER_ELIGIBLE and b.id not in results:
                add(b.id, "same_section", 0.6, "structural")

        # 5) continuation expansion (page-spanning answer)
        for bid in list(results.keys()):
            b = self.byid[bid]
            nxt = b.structure.continues_to
            if nxt and nxt in self.byid and self.byid[nxt].type in ANSWER_ELIGIBLE:
                add(nxt, "continuation", max(0.8, results[bid]["structural_strength"] - 0.05),
                    "continuation")
        return results
