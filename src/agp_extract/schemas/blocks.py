"""Canonical typed content block — the atom of the document representation.

The document is *not* reduced to page text. Each meaningful item becomes a
typed block with mandatory provenance. The taxonomy is deliberately broad and
carries an ``OTHER`` escape hatch so it generalizes beyond this publisher.

NOTE on scope: a block's ``type`` may be ``QUESTION`` / ``WORKED_EXAMPLE`` /
``ANSWER`` because that is a *structural observation about content*. Phase 1
stops there. Turning those blocks into classified Question records (type,
marks, options) and associating answers is Phase 3–4 and is not done here.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from .provenance import BlockProvenance
from .structure import BlockStructure


class BlockType(str, Enum):
    # structure
    DOCUMENT = "document"
    CHAPTER = "chapter"
    TOPIC = "topic"
    SECTION = "section"
    SUBSECTION = "subsection"
    HEADING = "heading"
    # prose
    PARAGRAPH = "paragraph"
    LIST = "list"
    LIST_ITEM = "list_item"
    CALLOUT = "callout"           # note / caution / important / mnemonic / related-theory
    NOTE = "note"
    LEARNING_OBJECTIVES = "learning_objectives"
    TOC = "toc"                   # e.g. "Topic Notes"
    BANNER = "banner"             # section banners like "SHORT ANSWER (SA-II) [3 marks]"
    # multimodal assets
    FIGURE = "figure"
    IMAGE = "image"
    DIAGRAM = "diagram"
    TABLE = "table"
    EQUATION = "equation"
    CAPTION = "caption"
    # pedagogical content (typed here, *not* discovered/classified in Phase 1)
    WORKED_EXAMPLE = "worked_example"
    QUESTION = "question"
    ANSWER = "answer"
    SOLUTION = "solution"
    EXPLANATION = "explanation"
    ACTIVITY = "activity"
    # fallback
    NOISE = "noise"               # QR codes, logos, page furniture
    OTHER = "other"


ASSET_TYPES = {BlockType.FIGURE, BlockType.IMAGE, BlockType.DIAGRAM,
               BlockType.TABLE, BlockType.EQUATION}
HEADING_TYPES = {BlockType.CHAPTER, BlockType.TOPIC, BlockType.SECTION,
                 BlockType.SUBSECTION, BlockType.HEADING}


class AssetRef(BaseModel):
    """A preserved crop of the original pixels for an asset block."""

    path: str
    sha256: str
    media_type: str = "image/png"
    width: int
    height: int
    kind: str                      # figure/diagram/table/equation/image
    derived_from_source_image: str


class UnresolvedRef(BaseModel):
    """A textual cross-reference ("the table above"). Captured in Phase 1;
    RESOLVED in Phase 2 by deterministic rules — or left explicitly unresolved
    (never guessed) when evidence is insufficient."""

    raw_text: str
    target_hint: Optional[str] = None   # figure|table|equation|graph|image
    direction: Optional[str] = None     # above|below|following|previous|none
    resolved_block_id: Optional[str] = None
    status: str = "unresolved"          # unresolved | resolved | ambiguous | no_candidate
    resolution_confidence: float = 0.0
    resolution_method: Optional[str] = None  # label | direction | proximity


class ContentBlock(BaseModel):
    id: str
    type: BlockType = BlockType.OTHER
    subtype: Optional[str] = None

    text: Optional[str] = None
    latex: Optional[str] = None          # for EQUATION
    caption: Optional[str] = None        # for asset blocks
    heading_level: Optional[int] = None  # 0=chapter,1=topic,2=section,3=subsection...

    provenance: BlockProvenance

    # hierarchy + reading-order links (filled by the document builder)
    parent_id: Optional[str] = None
    section_id: Optional[str] = None
    prev_id: Optional[str] = None
    next_id: Optional[str] = None
    child_ids: list[str] = Field(default_factory=list)

    # multimodal + references
    asset: Optional[AssetRef] = None
    refs: list[UnresolvedRef] = Field(default_factory=list)

    # Phase 2 structural relationships/annotations (empty until the structure pass)
    structure: BlockStructure = Field(default_factory=BlockStructure)

    # informational tags observed during extraction (bloom / source / marks).
    # These are captured verbatim; they are NOT the Phase-3 question layer.
    tags: dict = Field(default_factory=dict)
    meta: dict = Field(default_factory=dict)

    def is_asset(self) -> bool:
        return self.type in ASSET_TYPES

    def is_heading(self) -> bool:
        return self.type in HEADING_TYPES or self.heading_level is not None
