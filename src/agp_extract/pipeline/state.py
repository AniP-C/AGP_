"""Shared LangGraph state for the Phase 1 pipeline.

State holds live Python objects (page bytes, provider, document, graph). No
checkpointer is attached, so nothing is serialized mid-run — keys are simply
overwritten by each node's returned partial dict.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, TypedDict


class Phase1State(TypedDict, total=False):
    # inputs / context
    settings: Any
    provider: Any
    run: Any
    document_id: str
    out_dir: Path
    assets_dir: Path
    preprocess_dir: Path
    gold_dir: Optional[Path]

    # flowing artifacts
    pages: list           # list[LoadedPage]
    preprocessed: dict     # page_index -> preprocessed path
    blocks: list           # list[ContentBlock]
    printed_pages: dict
    extract_report: dict   # parser used, routing plan, failed pages
    coverage: dict         # measured ink coverage / numbering continuity
    document: Any          # CanonicalDocument
    graph: Any             # DocumentGraph
    evidence_units: list
    report: dict
    outputs: dict          # name -> written path
