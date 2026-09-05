"""Build the typed knowledge graph from a CanonicalDocument.

Phase 1 emits the structural + provenance edges only:
  CONTAINS / CHILD_OF (hierarchy), READING_NEXT, LOCATED_ON, DERIVED_FROM,
  HAS_CAPTION. PART_OF / ANSWERED_BY / REFERS_TO are reserved for later phases.
"""
from __future__ import annotations

from ..schemas import (
    ASSET_TYPES,
    BlockType,
    CanonicalDocument,
    DocumentGraph,
    EdgeType,
    GraphNode,
    HierarchyNode,
    NodeKind,
)


def build_graph(doc: CanonicalDocument) -> DocumentGraph:
    g = DocumentGraph()

    # document root
    g.add_node(GraphNode(id=doc.document_id, kind=NodeKind.DOCUMENT,
                         node_type="document",
                         label=doc.meta.chapter or doc.document_id,
                         data={"meta": doc.meta.model_dump(exclude_none=True)}))

    # pages
    for p in doc.pages:
        pid = f"page_{p.page_index}"
        g.add_node(GraphNode(id=pid, kind=NodeKind.PAGE, node_type="page",
                             label=f"p.{p.printed_page or p.page_index}",
                             data={"page_index": p.page_index,
                                   "printed_page": p.printed_page,
                                   "source": p.source.filename}))

    # hierarchy nodes + CONTAINS edges
    for root in doc.hierarchy:
        g.add_edge(doc.document_id, root.id, EdgeType.CONTAINS)
        _walk_hierarchy(g, root)

    # blocks
    blocks_by_id = {b.id: b for b in doc.blocks}
    for b in doc.blocks:
        g.add_node(GraphNode(
            id=b.id, kind=NodeKind.BLOCK, node_type=b.type.value,
            label=(b.text or b.caption or b.type.value)[:80],
            data={"page_index": b.provenance.page_index,
                  "printed_page": b.provenance.printed_page,
                  "reading_order": b.provenance.reading_order,
                  "column": b.provenance.column,
                  "section_id": b.section_id,
                  "extraction_confidence": b.provenance.extraction_confidence,
                  "bbox_confidence": (b.provenance.bbox.confidence
                                      if b.provenance.bbox else None),
                  "has_asset": b.asset is not None,
                  "tags": b.tags},
        ))
        # located_on / derived_from
        pid = f"page_{b.provenance.page_index}"
        g.add_edge(b.id, pid, EdgeType.LOCATED_ON)
        if b.type in ASSET_TYPES:
            g.add_edge(b.id, pid, EdgeType.DERIVED_FROM)
        # section containment (block ↔ its section)
        if b.section_id:
            g.add_edge(b.section_id, b.id, EdgeType.CONTAINS)
            g.add_edge(b.id, b.section_id, EdgeType.CHILD_OF)

    # reading order chain
    for b in doc.blocks:
        if b.next_id and b.next_id in blocks_by_id:
            g.add_edge(b.id, b.next_id, EdgeType.READING_NEXT)

    # has_caption: an asset immediately followed by a caption block
    for b in doc.blocks:
        if b.type in ASSET_TYPES and b.next_id:
            nxt = blocks_by_id.get(b.next_id)
            if nxt and nxt.type == BlockType.CAPTION:
                g.add_edge(b.id, nxt.id, EdgeType.HAS_CAPTION)

    # ── Phase 2 relationship edges (only where reliably determined) ────────
    _add_structure_edges(g, doc, blocks_by_id)
    return g


def _add_structure_edges(g: DocumentGraph, doc: CanonicalDocument, blocks_by_id) -> None:
    or_members: dict[str, list[str]] = {}
    for b in doc.blocks:
        st = b.structure
        # part_of: subpart/content → container
        if st.part_of_id and st.part_of_id in blocks_by_id:
            g.add_edge(b.id, st.part_of_id, EdgeType.PART_OF,
                       role=st.group_role, enumerator=st.enumerator)
        # continues: fragment → its continuation
        if st.continues_to and st.continues_to in blocks_by_id:
            g.add_edge(b.id, st.continues_to, EdgeType.CONTINUES,
                       logical_item_id=st.logical_item_id)
        # refers_to: only RESOLVED refs become edges
        for ref in b.refs:
            if ref.status == "resolved" and ref.resolved_block_id in blocks_by_id:
                g.add_edge(b.id, ref.resolved_block_id, EdgeType.REFERS_TO,
                           raw_text=ref.raw_text, method=ref.resolution_method,
                           confidence=ref.resolution_confidence)
        if st.or_group_id:
            or_members.setdefault(st.or_group_id, []).append(b.id)
    # or_alternative: connect members of each OR group pairwise (bidirectional)
    for members in or_members.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                g.add_edge(members[i], members[j], EdgeType.OR_ALTERNATIVE)
                g.add_edge(members[j], members[i], EdgeType.OR_ALTERNATIVE)


def _walk_hierarchy(g: DocumentGraph, node: HierarchyNode) -> None:
    kind = NodeKind.TOPIC if node.kind in ("topic", "chapter") else NodeKind.SECTION
    g.add_node(GraphNode(id=node.id, kind=kind, node_type=node.kind,
                         label=node.title, data={"level": node.level,
                                                  "heading_block_id": node.heading_block_id}))
    for child in node.children:
        g.add_edge(node.id, child.id, EdgeType.CONTAINS)
        g.add_edge(child.id, node.id, EdgeType.CHILD_OF)
        _walk_hierarchy(g, child)
