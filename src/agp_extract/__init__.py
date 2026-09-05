"""AGP multimodal document-understanding & Q&A extraction (POC).

Phase 1 scope only: ingestion, preprocessing, multimodal block extraction,
provenance, asset preservation, and document-hierarchy reconstruction.

Question discovery, RAG, and answer association are intentionally NOT part of
Phase 1 — the evidence layer and graph are designed so those slot in later
without reworking the core.
"""

__version__ = "0.1.0"
PROMPT_VERSION = "p1-2026-09-03b"  # bump to invalidate the extraction cache
