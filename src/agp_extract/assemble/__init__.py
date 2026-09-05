"""Document assembly: reading order, hierarchy, metadata."""
from .document_builder import build_document
from .hierarchy import build_hierarchy, infer_meta, link_reading_order

__all__ = ["build_document", "build_hierarchy", "infer_meta", "link_reading_order"]
