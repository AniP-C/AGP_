"""Knowledge-graph schema — persisted as JSON/SQLite, NOT a graph database.

The graph is the *primary* structural representation (the vector store, added in
Phase 4, is only a secondary index). Nodes are content blocks plus a few
synthetic nodes (document / page / topic / section). Edges are typed and
directed.

Phase 1 populates the structural + provenance edges. ``PART_OF`` /
``ANSWERED_BY`` / ``REFERS_TO`` are defined now but only *resolved* in later
phases — keeping the contract stable so nothing downstream is reworked.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class EdgeType(str, Enum):
    CONTAINS = "contains"          # parent → child (hierarchy)
    CHILD_OF = "child_of"          # child → parent (inverse, convenience)
    READING_NEXT = "reading_next"  # linear reading order
    HAS_CAPTION = "has_caption"    # asset ↔ caption
    LOCATED_ON = "located_on"      # block → page
    DERIVED_FROM = "derived_from"  # asset crop → source page image
    # materialized in Phase 2 (structure/relationships)
    PART_OF = "part_of"            # subpart → container (Example/question)
    REFERS_TO = "refers_to"        # text → resolved figure/table/equation
    CONTINUES = "continues"        # fragment → its continuation (next page/column)
    OR_ALTERNATIVE = "or_alternative"  # one internal-choice alternative → the other
    # reserved for Phase 4 (answer association)
    ANSWERED_BY = "answered_by"    # question → answer/solution


class NodeKind(str, Enum):
    DOCUMENT = "document"
    PAGE = "page"
    TOPIC = "topic"
    SECTION = "section"
    BLOCK = "block"


class GraphNode(BaseModel):
    id: str
    kind: NodeKind
    node_type: Optional[str] = None    # block type, or role for synthetic nodes
    label: Optional[str] = None
    data: dict = Field(default_factory=dict)


class GraphEdge(BaseModel):
    src: str
    dst: str
    type: EdgeType
    data: dict = Field(default_factory=dict)


class DocumentGraph(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def add_node(self, node: GraphNode) -> None:
        self.nodes.append(node)

    def add_edge(self, src: str, dst: str, etype: EdgeType, **data) -> None:
        self.edges.append(GraphEdge(src=src, dst=dst, type=etype, data=data))

    # small convenience readers (structural retrieval builds on these later)
    def neighbors(self, node_id: str, etype: Optional[EdgeType] = None) -> list[str]:
        return [
            e.dst for e in self.edges
            if e.src == node_id and (etype is None or e.type == etype)
        ]

    def edge_count_by_type(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for e in self.edges:
            out[e.type.value] = out.get(e.type.value, 0) + 1
        return out
