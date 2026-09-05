"""Evidence layer — designed in Phase 1, exercised in Phase 4.

Answer association (Phase 4) needs to retrieve *evidence* for a question across
five levels: local context, structural context, referenced assets, chapter
retrieval, and LLM reasoning. Rather than bolt retrieval on later, Phase 1
already emits a normalized :class:`EvidenceUnit` per block and defines the
:class:`EvidenceRetriever` contract.

Phase 1 implements ONLY the free, deterministic ``structural`` retrieval (graph
reads). ``semantic`` and ``lexical`` are declared and raise ``NotImplementedError``
until the RAG phase — no vectors/embeddings are built here.
"""
from __future__ import annotations

import abc
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EvidenceKind(str, Enum):
    TEXT = "text"                 # paragraph / list / heading text
    ASSET = "asset"               # figure / diagram / table / equation (+ caption/desc)
    STRUCTURE = "structure"       # topic/section markers


class EvidenceUnit(BaseModel):
    """A retrievable unit derived from a block, carrying its graph metadata so
    structural retrieval works without re-reading the whole graph."""

    id: str
    block_id: str
    kind: EvidenceKind
    text: str = ""                # searchable text (or asset caption/description)
    # structural metadata copied for cheap filtering / structural retrieval
    page_index: int
    printed_page: Optional[int] = None
    section_id: Optional[str] = None
    parent_id: Optional[str] = None
    reading_order: int = -1
    block_type: str = "other"
    neighbor_ids: list[str] = Field(default_factory=list)
    ref_hints: list[str] = Field(default_factory=list)
    tags: dict = Field(default_factory=dict)
    # placeholder for Phase 4; never populated in Phase 1
    embedding: Optional[list[float]] = None


class EvidenceHit(BaseModel):
    unit_id: str
    block_id: str
    score: float
    channel: str                  # structural | semantic | lexical


class EvidenceRetriever(abc.ABC):
    """The contract Phase 4 fills. Phase 1 ships a structural-only impl."""

    @abc.abstractmethod
    def structural(self, block_id: str, limit: int = 10) -> list[EvidenceHit]:
        """Graph-based neighbours: same section, reading-order, parent/children,
        referenced assets. Deterministic, no model needed."""

    def semantic(self, query: str, limit: int = 10) -> list[EvidenceHit]:  # noqa: D401
        raise NotImplementedError("semantic retrieval is wired in Phase 4 (RAG)")

    def lexical(self, query: str, limit: int = 10) -> list[EvidenceHit]:
        raise NotImplementedError("lexical retrieval is wired in Phase 4 (RAG)")

    def hybrid(self, query: str, block_id: Optional[str] = None,
               limit: int = 10) -> list[EvidenceHit]:
        raise NotImplementedError("hybrid rerank is wired in Phase 4 (RAG)")
