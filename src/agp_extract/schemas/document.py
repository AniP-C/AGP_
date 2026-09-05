"""Top-level canonical document — the Phase 1 deliverable object."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .blocks import ContentBlock
from .provenance import RunProvenance, SourceFileProvenance
from .questions import Question
from .structure import PageLayout


class DocumentMeta(BaseModel):
    """Best-effort, inferred — never assumed. All fields nullable."""

    title: Optional[str] = None
    subject: Optional[str] = None
    grade: Optional[str] = None
    chapter: Optional[str] = None
    chapter_number: Optional[str] = None
    publisher: Optional[str] = None
    inferred: bool = True


class Page(BaseModel):
    page_index: int
    printed_page: Optional[int] = None
    source: SourceFileProvenance
    preprocessed_path: Optional[str] = None
    width: int
    height: int
    layout: Optional[PageLayout] = None      # filled by the Phase-2 structure pass


class HierarchyNode(BaseModel):
    """A node in the reconstructed chapter → topic → section tree."""

    id: str
    kind: str                       # chapter|topic|section|subsection
    title: Optional[str] = None
    level: int = 0
    heading_block_id: Optional[str] = None
    block_ids: list[str] = Field(default_factory=list)   # direct content blocks
    children: list["HierarchyNode"] = Field(default_factory=list)


class CanonicalDocument(BaseModel):
    """Typed blocks + provenance + hierarchy. No question layer (Phase 3+)."""

    document_id: str
    meta: DocumentMeta = Field(default_factory=DocumentMeta)

    # document-level provenance (strengthened): every source page hashed + a
    # full record of how this run was produced.
    source_files: list[SourceFileProvenance] = Field(default_factory=list)
    run: RunProvenance

    pages: list[Page] = Field(default_factory=list)
    blocks: list[ContentBlock] = Field(default_factory=list)
    hierarchy: list[HierarchyNode] = Field(default_factory=list)
    # Phase 3 deliverable (empty until the discovery pass runs)
    questions: list[Question] = Field(default_factory=list)

    content_sha256: Optional[str] = None
    stats: dict = Field(default_factory=dict)

    def block_by_id(self, bid: str) -> Optional[ContentBlock]:
        for b in self.blocks:
            if b.id == bid:
                return b
        return None


HierarchyNode.model_rebuild()
