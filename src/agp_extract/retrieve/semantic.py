"""Semantic channel — a Chroma vector index over canonical blocks.

The vector DB is ONLY a retrieval index; the knowledge graph stays the primary
structural representation. Embeddings come from the provider abstraction
(``provider.embed``) so the model stays swappable, and are cached by text hash for
reproducibility. Degrades to disabled if embeddings/Chroma are unavailable.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
_CHUNK = 100


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _embed(provider, texts: list[str], cache_dir: Optional[Path]) -> Optional[list[list[float]]]:
    cdir = Path(cache_dir) / "emb" if cache_dir else None
    if cdir:
        cdir.mkdir(parents=True, exist_ok=True)
    out: list[Optional[list[float]]] = [None] * len(texts)
    todo_idx, todo_txt = [], []
    for i, t in enumerate(texts):
        f = cdir / f"{_hash(t)}.json" if cdir else None
        if f and f.exists():
            try:
                out[i] = json.loads(f.read_text())
                continue
            except Exception:
                pass
        todo_idx.append(i)
        todo_txt.append(t)
    for s in range(0, len(todo_txt), _CHUNK):
        chunk = todo_txt[s:s + _CHUNK]
        try:
            vecs = provider.embed(chunk)
        except Exception as exc:
            log.warning("embedding failed (%s); semantic channel disabled", exc)
            return None
        for j, v in enumerate(vecs):
            gi = todo_idx[s + j]
            out[gi] = list(v)
            if cdir:
                (cdir / f"{_hash(texts[gi])}.json").write_text(json.dumps(out[gi]))
    if any(v is None for v in out):
        return None
    return out  # type: ignore


class VectorIndex:
    def __init__(self, provider, ids: list[str], texts: list[str], cache_dir=None):
        self.enabled = False
        self.provider = provider
        self.cache_dir = cache_dir
        if not ids:
            return
        embs = _embed(provider, texts, cache_dir)
        if embs is None:
            return
        try:
            import chromadb
            client = chromadb.EphemeralClient()
            self.col = client.get_or_create_collection(
                "blocks", metadata={"hnsw:space": "cosine"})
            self.col.add(ids=ids, embeddings=embs)
            self.enabled = True
        except Exception as exc:
            log.warning("Chroma unavailable (%s); semantic channel disabled", exc)

    def query_by_text(self, text: str, k: int = 8) -> list[tuple[str, float]]:
        if not self.enabled:
            return []
        emb = _embed(self.provider, [text], self.cache_dir)
        if not emb:
            return []
        res = self.col.query(query_embeddings=emb, n_results=k)
        ids = res.get("ids", [[]])[0]
        dists = res.get("distances", [[]])[0]
        return [(i, round(max(0.0, 1.0 - d), 4)) for i, d in zip(ids, dists)]
