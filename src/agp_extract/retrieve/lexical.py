"""Minimal BM25 lexical index (no external dependency).

One of the hybrid channels. Retrieval scores are normalized to 0..1 and always
carried alongside the block's provenance — never used alone to assert an answer.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "to", "in", "is", "and", "or", "for", "on", "at",
         "by", "it", "as", "be", "are", "this", "that", "with", "which", "from"}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower())
            if t not in _STOP and len(t) > 1]


class BM25:
    def __init__(self, ids: list[str], docs: list[str], k1: float = 1.5, b: float = 0.75):
        self.ids = ids
        self.docs = [tokenize(d) for d in docs]
        self.k1, self.b = k1, b
        self.N = len(self.docs)
        self.dl = [len(d) for d in self.docs]
        self.avgdl = (sum(self.dl) / self.N) if self.N else 0.0
        self.tf = [Counter(d) for d in self.docs]
        df: Counter = Counter()
        for d in self.docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.N - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def _score(self, q: list[str], i: int) -> float:
        s = 0.0
        for t in q:
            if t not in self.tf[i]:
                continue
            idf = self.idf.get(t, 0.0)
            f = self.tf[i][t]
            denom = f + self.k1 * (1 - self.b + self.b * self.dl[i] / (self.avgdl or 1))
            s += idf * f * (self.k1 + 1) / (denom or 1)
        return s

    def topk(self, query: str, k: int = 8) -> list[tuple[str, float]]:
        q = tokenize(query)
        scores = [(i, self._score(q, i)) for i in range(self.N)]
        scores = [(i, s) for i, s in scores if s > 0]
        scores.sort(key=lambda x: -x[1])
        top = scores[:k]
        mx = top[0][1] if top else 1.0
        return [(self.ids[i], round(s / (mx or 1), 4)) for i, s in top]
