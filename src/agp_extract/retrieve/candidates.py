"""Hybrid candidate assembly: merge structural + lexical + semantic channels into
a single, deduplicated, reranked evidence list per question.

Rerank keeps structural evidence strictly above pure lexical/semantic hits, so a
cross-section semantic match can never outrank an in-scope structural one.
"""
from __future__ import annotations

from ..schemas import AnswerEvidence, CanonicalDocument
from .lexical import BM25
from .semantic import VectorIndex
from .structural import ANSWER_ELIGIBLE, StructuralAnswerRetriever


class CandidateBuilder:
    def __init__(self, doc: CanonicalDocument, provider=None, cache_dir=None):
        self.doc = doc
        self.byid = {b.id: b for b in doc.blocks}
        self.struct = StructuralAnswerRetriever(doc)
        pool = [b for b in doc.blocks
                if b.type in ANSWER_ELIGIBLE and (b.text or b.caption)]
        self.pool_ids = [b.id for b in pool]
        texts = [f"{b.text or ''} {b.caption or ''}".strip() for b in pool]
        self.bm25 = BM25(self.pool_ids, texts)
        self.vec = None
        if provider is not None and getattr(provider, "name", "null") != "null":
            self.vec = VectorIndex(provider, self.pool_ids, texts, cache_dir)

    @property
    def semantic_enabled(self) -> bool:
        return bool(self.vec and self.vec.enabled)

    def warm_queries(self, texts: list[str]) -> None:
        """Pre-embed query texts in one batch so per-question queries hit cache."""
        if self.semantic_enabled:
            from .semantic import _embed
            _embed(self.vec.provider, texts, self.vec.cache_dir)

    def build(self, q, k: int = 6) -> list[AnswerEvidence]:
        qtext = q.question_text or ""
        qb = self.byid.get(q.provenance.source_blocks[0]) if q.provenance.source_blocks else None
        qsection = qb.section_id if qb else None

        struct = self.struct.candidates(q)
        lex = dict(self.bm25.topk(qtext, 8))
        sem = dict(self.vec.query_by_text(qtext, 8)) if self.semantic_enabled else {}

        evs: list[AnswerEvidence] = []
        for bid in set(struct) | set(lex) | set(sem):
            b = self.byid.get(bid)
            if not b:
                continue
            s = struct.get(bid)
            channels = set(s["channels"]) if s else set()
            if bid in lex:
                channels.add("lexical")
            if bid in sem:
                channels.add("semantic")
            sstrength = s["structural_strength"] if s else 0.0
            lexs, sems = lex.get(bid, 0.0), sem.get(bid, 0.0)
            in_scope = s is not None or b.section_id == qsection
            # structural dominates; non-structural capped strictly below it
            rerank = sstrength if sstrength > 0 else round((0.5 * sems + 0.3 * lexs) * 0.7, 4)
            evs.append(AnswerEvidence(
                candidate_block_id=bid, block_type=b.type.value,
                page_index=b.provenance.page_index, printed_page=b.provenance.printed_page,
                section_id=b.section_id, channels=sorted(channels),
                relationship=(s["relationship"] if s else "none"),
                scope="in_scope" if in_scope else "cross_section",
                structural_strength=sstrength, lexical_score=lexs,
                semantic_score=sems, rerank_score=rerank,
            ))
        evs.sort(key=lambda e: -e.rerank_score)
        return evs[:k]
