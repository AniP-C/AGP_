"""Shared, presentation-only UI building blocks used across views."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Optional

import streamlit as st
from PIL import Image, ImageDraw

from .data import RunBundle

REPO_ROOT = Path(__file__).resolve().parent.parent

# block type -> overlay color, for the Document Explorer bbox view
_BLOCK_COLORS = {
    "question": "#2563eb",
    "answer": "#16a34a",
    "solution": "#16a34a",
    "explanation": "#16a34a",
    "table": "#d97706",
    "figure": "#db2777",
    "image": "#db2777",
    "diagram": "#db2777",
    "equation": "#7c3aed",
    "heading": "#64748b",
}
_DEFAULT_COLOR = "#94a3b8"


def metric_row(items: list[tuple[str, str, str]]):
    """items: (label, value, help_text)."""
    cols = st.columns(len(items))
    for col, (label, value, help_text) in zip(cols, items):
        col.metric(label, value, help=help_text or None)


def status_badge(status: str) -> str:
    colors = {
        "matched": "🟢", "present": "🟢",
        "partial": "🟡",
        "ambiguous": "🟠",
        "not_in_document": "🔴",
        "accept": "🟢", "escalate": "🟡", "abstain": "🔴",
        "high": "🟢", "medium": "🟡", "low": "🔴",
        "passed": "🟢", "failed_source": "🟠",
    }
    icon = colors.get(status, "⚪")
    return f"{icon} {status.replace('_', ' ').title()}"


def resolve_asset_path(bundle: RunBundle, raw_path: str) -> Optional[Path]:
    """Backend paths are written relative to the repo root (may use `\\`)."""
    if not raw_path:
        return None
    p = Path(raw_path.replace("\\", "/"))
    if p.is_absolute() and p.exists():
        return p
    candidate = REPO_ROOT / p
    return candidate if candidate.exists() else None


def source_page_image(bundle: RunBundle, page_index: int) -> Optional[Image.Image]:
    page = bundle.page(page_index)
    if not page:
        return None
    path = resolve_asset_path(bundle, page["source"]["path"])
    if not path:
        return None
    try:
        return Image.open(path).convert("RGB")
    except Exception:
        return None


def page_with_bbox_overlay(
    bundle: RunBundle, page_index: int, highlight_block_ids: Optional[set[str]] = None,
) -> Optional[Image.Image]:
    """Draws every block's bbox for a page (colored by type), without resizing
    or otherwise distorting the source image.
    """
    img = source_page_image(bundle, page_index)
    if img is None:
        return None
    img = img.copy()
    draw = ImageDraw.Draw(img)
    highlight_block_ids = highlight_block_ids or set()

    for block in bundle.document.get("blocks", []):
        prov = block.get("provenance") or {}
        if prov.get("page_index") != page_index:
            continue
        bbox = prov.get("bbox")
        if not bbox:
            continue
        color = _BLOCK_COLORS.get(block.get("type"), _DEFAULT_COLOR)
        width = 5 if block["id"] in highlight_block_ids else 2
        draw.rectangle(
            [bbox["x0"], bbox["y0"], bbox["x1"], bbox["y1"]],
            outline=color, width=width,
        )
    return img


def image_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def json_viewer(data, label: str = "Raw JSON"):
    with st.expander(label):
        st.json(data, expanded=False)


def error_banner(message: str, detail: Optional[str] = None):
    st.error(message)
    if detail:
        with st.expander("Technical details"):
            st.code(detail)


def download_artifact_buttons(bundle: RunBundle):
    # The reviewable Q&A document comes first and on its own row — it is the
    # deliverable a human actually reads, as opposed to the machine artifacts.
    pdf = bundle.ensure_qa_pdf()
    if pdf is not None:
        n_q = sum(1 for _ in bundle.all_questions_flat())
        st.download_button(
            label=f"⬇  Download all {n_q} questions & answers (PDF)",
            data=pdf.read_bytes(),
            file_name=f"{bundle.document_id}_questions_answers.pdf",
            mime="application/pdf", use_container_width=True, type="primary",
            key=f"dl_{bundle.document_id}_qa_pdf",
            help="Complete question tree with page numbers, marks, linked figures, "
                 "answers as extracted, and anything absent marked NOT IN DOCUMENT.",
        )
    else:
        st.caption("Q&A PDF unavailable for this run (install `reportlab` to enable it).")

    names = [
        "questions.json", "canonical_document.json", "graph.json",
        "evidence.json", "eval_report.json", "confidence_report.json",
    ]
    available = [(n, p) for n, p in ((n, bundle.artifact_path(n)) for n in names)
                 if p is not None]
    if not available:
        return
    st.caption("Machine-readable artifacts")
    for row_start in range(0, len(available), 3):
        row = available[row_start:row_start + 3]
        cols = st.columns(3)
        for col, (name, path) in zip(cols, row):
            with col:
                st.download_button(
                    label=name, data=path.read_bytes(), file_name=name,
                    mime="application/json", use_container_width=True,
                    key=f"dl_{bundle.document_id}_{name}",
                )
