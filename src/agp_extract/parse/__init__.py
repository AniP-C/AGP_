"""Document parsing — parser-agnostic port, adapters, and the input router."""
from .base import DocumentParser
from .router import PageRoute, RoutingPlan, route_pages, probe_pdf
from .vlm_parser import VlmPageParser
from .factory import build_parser

__all__ = [
    "DocumentParser", "VlmPageParser", "build_parser",
    "PageRoute", "RoutingPlan", "route_pages", "probe_pdf",
]
