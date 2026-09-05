"""Pydantic schemas — the strict contracts everything else speaks."""
from .blocks import (
    ASSET_TYPES,
    HEADING_TYPES,
    AssetRef,
    BlockType,
    ContentBlock,
    UnresolvedRef,
)
from .document import CanonicalDocument, DocumentMeta, HierarchyNode, Page
from .evidence import (
    EvidenceHit,
    EvidenceKind,
    EvidenceRetriever,
    EvidenceUnit,
)
from .geometry import BBox
from .graph import DocumentGraph, EdgeType, GraphEdge, GraphNode, NodeKind
from .answers import (
    AnswerAssociation,
    AnswerEvidence,
    AnswerSignals,
    LLMAssociation,
)
from .provenance import (
    BlockProvenance,
    RunProvenance,
    SourceFileProvenance,
)
from .questions import (
    InlineAnswer,
    InlineAnswerSegment,
    InternalChoice,
    Marks,
    Question,
    QuestionEval,
    QuestionOption,
    QuestionProvenance,
)
from .structure import BlockStructure, InlineSegment, PageLayout
from .validation import (
    NumericCheck,
    QuestionValidation,
    RetryRecord,
    ValidationFinding,
)

__all__ = [
    "BBox",
    "BlockProvenance", "RunProvenance", "SourceFileProvenance",
    "BlockType", "ContentBlock", "AssetRef", "UnresolvedRef",
    "BlockStructure", "InlineSegment", "PageLayout",
    "Question", "QuestionOption", "Marks", "InternalChoice", "InlineAnswer",
    "InlineAnswerSegment", "QuestionEval", "QuestionProvenance",
    "AnswerAssociation", "AnswerEvidence", "AnswerSignals", "LLMAssociation",
    "QuestionValidation", "ValidationFinding", "NumericCheck", "RetryRecord",
    "ASSET_TYPES", "HEADING_TYPES",
    "DocumentGraph", "GraphNode", "GraphEdge", "EdgeType", "NodeKind",
    "EvidenceUnit", "EvidenceHit", "EvidenceKind", "EvidenceRetriever",
    "CanonicalDocument", "DocumentMeta", "Page", "HierarchyNode",
]
