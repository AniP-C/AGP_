from __future__ import annotations

import streamlit as st

from ..components import image_bytes, page_with_bbox_overlay, resolve_asset_path
from ..data import RunBundle

_LEGEND = [
    ("question", "#2563eb", "Question"),
    ("answer/solution/explanation", "#16a34a", "Answer"),
    ("table", "#d97706", "Table"),
    ("figure/image/diagram", "#db2777", "Figure / Diagram"),
    ("equation", "#7c3aed", "Equation"),
    ("heading", "#64748b", "Heading"),
]


def render(bundle: RunBundle):
    st.header("Document Explorer")
    st.caption("Browse each page as extracted, with block boundaries overlaid on the original image.")

    pages = bundle.document.get("pages", [])
    if not pages:
        st.info("No pages found for this run.")
        return

    default_idx = st.session_state.get("explorer_page_index", pages[0]["page_index"])
    page_options = [p["page_index"] for p in pages]
    if default_idx not in page_options:
        default_idx = page_options[0]

    page_index = st.selectbox(
        "Page", page_options,
        index=page_options.index(default_idx),
        format_func=lambda i: f"Page {i + 1}" + (f" (printed p.{bundle.page(i).get('printed_page')})" if bundle.page(i).get("printed_page") else ""),
    )
    st.session_state["explorer_page_index"] = page_index

    highlight = set()
    if st.session_state.get("explorer_highlight_block"):
        highlight = {st.session_state["explorer_highlight_block"]}

    col_img, col_blocks = st.columns([3, 2])

    with col_img:
        show_boxes = st.checkbox("Show block bounding boxes", value=True)
        if show_boxes:
            img = page_with_bbox_overlay(bundle, page_index, highlight_block_ids=highlight)
            if img is not None:
                st.image(image_bytes(img), use_container_width=True)
                st.caption("Legend: " + " · ".join(label for _, _, label in _LEGEND))
            else:
                st.warning("Source page image not found on disk.")
        else:
            from ..components import source_page_image
            img = source_page_image(bundle, page_index)
            if img is not None:
                st.image(img, use_container_width=True)
            else:
                st.warning("Source page image not found on disk.")

    with col_blocks:
        st.markdown("**Blocks on this page** (reading order)")
        page_blocks = sorted(
            (b for b in bundle.document.get("blocks", [])
             if (b.get("provenance") or {}).get("page_index") == page_index),
            key=lambda b: (b.get("provenance") or {}).get("reading_order", 0),
        )
        st.caption(f"{len(page_blocks)} block(s)")
        for b in page_blocks:
            prov = b.get("provenance", {})
            is_highlighted = b["id"] in highlight
            title = f"{'➡️ ' if is_highlighted else ''}`{b['id']}` — {b['type']}"
            with st.expander(title, expanded=is_highlighted):
                if b.get("text"):
                    st.write(b["text"][:400] + ("…" if len(b["text"]) > 400 else ""))
                st.caption(
                    f"reading_order={prov.get('reading_order')} · "
                    f"column={prov.get('column')} · "
                    f"extraction_confidence={prov.get('extraction_confidence')} · "
                    f"model={prov.get('model')}"
                )
                if b.get("asset"):
                    asset_path = resolve_asset_path(bundle, b["asset"]["path"])
                    if asset_path:
                        st.image(str(asset_path), width=220)
                if st.button("View full source page", key=f"jump_{b['id']}"):
                    st.session_state["explorer_page_index"] = page_index
                    st.session_state["explorer_highlight_block"] = b["id"]
                    st.rerun()
                st.caption("Block JSON")
                st.json(b, expanded=False)
