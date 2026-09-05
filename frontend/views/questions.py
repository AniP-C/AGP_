from __future__ import annotations

import pandas as pd
import streamlit as st

from ..components import (download_artifact_buttons, json_viewer, page_with_bbox_overlay,
                           resolve_asset_path, status_badge)
from ..data import RunBundle


def _rows(bundle: RunBundle):
    for q in bundle.all_questions_flat():
        assoc = q.get("answer_association") or {}
        marks = (q.get("marks") or {}).get("value")
        conf = (q.get("validation") or {}).get("calibrated_confidence")
        if conf is None:
            conf = assoc.get("signals", {}).get("final_confidence")
        yield {
            "ID": q["id"],
            "Question": (q.get("question_text") or "")[:110],
            "Source type": q.get("source_type") or "—",
            "Question type": q.get("question_type") or "—",
            "Marks": marks if marks is not None else "—",
            "Bloom": q.get("bloom_level") or "—",
            "Answer status": assoc.get("status") or ("has sub-parts" if q.get("sub_questions") else "—"),
            "Confidence": round(conf, 2) if isinstance(conf, (int, float)) else "—",
        }


def render(bundle: RunBundle):
    st.header("Questions")
    st.caption("Every question the pipeline discovered, with its answer-association status.")

    df = pd.DataFrame(_rows(bundle))
    if df.empty:
        st.info("No questions found for this run.")
        return

    with st.expander("Filters", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        src = c1.multiselect("Source type", sorted(df["Source type"].unique()))
        qtype = c2.multiselect("Question type", sorted(df["Question type"].unique()))
        status = c3.multiselect("Answer status", sorted(df["Answer status"].unique()))
        bloom = c4.multiselect("Bloom level", sorted(df["Bloom"].unique()))
        search = st.text_input("Search question text")

    filtered = df.copy()
    if src:
        filtered = filtered[filtered["Source type"].isin(src)]
    if qtype:
        filtered = filtered[filtered["Question type"].isin(qtype)]
    if status:
        filtered = filtered[filtered["Answer status"].isin(status)]
    if bloom:
        filtered = filtered[filtered["Bloom"].isin(bloom)]
    if search:
        filtered = filtered[filtered["Question"].str.contains(search, case=False, na=False)]

    st.caption(f"{len(filtered)} of {len(df)} questions")
    st.dataframe(filtered, use_container_width=True, hide_index=True, height=360)

    st.divider()
    st.subheader("Question detail")
    options = filtered["ID"].tolist() or df["ID"].tolist()
    if not options:
        return
    selected_id = st.selectbox("Select a question to inspect", options)
    q = next((x for x in bundle.all_questions_flat() if x["id"] == selected_id), None)
    if q:
        _render_detail(bundle, q)


def _render_detail(bundle: RunBundle, q: dict):
    left, right = st.columns(2)

    with left:
        st.markdown("#### Question")
        st.write(f"**{q.get('question_number', '')}.** {q.get('question_text', '')}")
        st.write(
            f"Source type: `{q.get('source_type')}` · Question type: `{q.get('question_type')}` · "
            f"Marks: `{(q.get('marks') or {}).get('value', '—')}` · Bloom: `{q.get('bloom_level', '—')}`"
        )
        if q.get("options"):
            for opt in q["options"]:
                st.write(f"({opt['key']}) {opt['text']}")
        refs = q.get("source_refs") or []
        if refs:
            st.caption("Reference tags: " + ", ".join(str(r) for r in refs))

    assoc = q.get("answer_association") or {}
    with right:
        st.markdown("#### Answer")
        status = assoc.get("status")
        if not status:
            st.info("This question has sub-parts — see each sub-part for its own answer.")
        elif status == "not_in_document":
            st.error("🔴 **NOT IN DOCUMENT**")
            st.write(
                "No answer could be found in the supplied source, and the system "
                "did **not** generate or substitute one."
            )
        else:
            st.markdown(status_badge(status))
            st.write(assoc.get("answer_preview") or "_(no preview text)_")
        if assoc.get("answer_kind"):
            st.caption(f"Answer kind: `{assoc['answer_kind']}`")

    if status and status != "not_in_document":
        st.divider()
        st.markdown("#### Relationship")
        st.code("Question\n  ↓  ANSWERED_BY\nAnswer / Solution", language=None)

        ev_cols = st.columns(4)
        ev_cols[0].metric("Evidence blocks", len(assoc.get("evidence", [])))
        signals = assoc.get("signals") or {}
        ev_cols[1].metric("Structural strength", f"{signals.get('structural_strength', 0):.2f}" if signals.get("structural_strength") is not None else "—")
        ev_cols[2].metric("Semantic score", f"{signals.get('semantic_score', 0):.2f}" if signals.get("semantic_score") is not None else "—")
        ev_cols[3].metric("Lexical score", f"{signals.get('lexical_score', 0):.2f}" if signals.get("lexical_score") is not None else "—")

        for bid in assoc.get("answer_block_ids", []):
            block = bundle.block(bid)
            if not block:
                continue
            prov = block.get("provenance", {})
            page_idx = prov.get("page_index")
            btn_key = f"view_src_{q['id']}_{bid}"
            if st.button(f"View source — page {page_idx} · block `{bid}`", key=btn_key):
                st.session_state["explorer_page_index"] = page_idx
                st.session_state["explorer_highlight_block"] = bid
                st.info("Open the **Document Explorer** tab — it's now focused on this page.")

    if q.get("validation"):
        json_viewer(q["validation"], "Validation detail (raw)")
    json_viewer(q, f"Full question object — {q['id']}")
