from __future__ import annotations

import streamlit as st

from ..components import status_badge
from ..data import RunBundle

_SIGNAL_LABELS = {
    "structural_strength": "Structural relationship",
    "lexical_score": "Lexical retrieval",
    "semantic_score": "Semantic retrieval",
    "rerank_score": "Rerank",
    "llm_confidence": "LLM adjudication",
}


def render(bundle: RunBundle):
    st.header("Evidence & Answers")
    st.caption(
        "How each answer association was decided. Evidence signals are shown "
        "separately — never collapsed into one unexplained score."
    )

    leaves = list(bundle.all_leaf_questions())
    matched = [q for q in leaves if (q.get("answer_association") or {}).get("status") in ("matched", "partial", "ambiguous")]
    not_found = [q for q in leaves if (q.get("answer_association") or {}).get("status") == "not_in_document"]

    tab_matched, tab_not_found = st.tabs([f"Answered ({len(matched)})", f"Not in document ({len(not_found)})"])

    with tab_matched:
        if not matched:
            st.info("No matched/partial/ambiguous answers in this run.")
        for q in matched:
            _render_matched(bundle, q)

    with tab_not_found:
        st.markdown(
            "These questions have **no answer in the supplied source** — most commonly "
            "self-assessment items whose answers live behind a QR code or external key. "
            "The system reports this honestly instead of generating a plausible-looking answer."
        )
        for q in not_found:
            _render_not_found(q)


def _render_matched(bundle: RunBundle, q: dict):
    assoc = q["answer_association"]
    with st.expander(f"{q['id']} — {status_badge(assoc['status'])} — {(q.get('question_text') or '')[:80]}"):
        signals = assoc.get("signals") or {}
        final_confidence = signals.get("final_confidence")
        present = [(_SIGNAL_LABELS[k], v) for k, v in signals.items() if v and k in _SIGNAL_LABELS]
        if present:
            st.write("**Evidence signals present:**")
            for label, val in present:
                st.write(f"✓ {label} — `{val:.2f}`" if isinstance(val, float) else f"✓ {label} — `{val}`")
        if final_confidence is not None:
            st.caption(f"Combined confidence: `{final_confidence:.2f}`")

        st.write(f"**Answer block(s):** {', '.join(assoc.get('answer_block_ids', [])) or '—'}")

        for ev in assoc.get("evidence", []):
            st.caption(
                f"`{ev.get('candidate_block_id')}` — page {ev.get('page_index')}"
                + (f" (printed p.{ev.get('printed_page')})" if ev.get("printed_page") else "")
                + f" — relationship: `{ev.get('relationship')}` — channels: {', '.join(ev.get('channels') or [])}"
            )

        llm = assoc.get("llm") or {}
        if llm.get("reason"):
            st.caption(f"Adjudicator reasoning: {llm['reason']}")

        st.caption("Raw answer_association")
        st.json(assoc, expanded=False)


def _render_not_found(q: dict):
    reason = "Answer is not present in the supplied source."
    if q.get("source_type") == "self_assessment":
        reason = (
            "Self-assessment question — the answer key is typically referenced "
            "through an external source (e.g. a QR code) not included in the supplied pages."
        )
    with st.container(border=True):
        st.markdown(f"**{q['id']}** — {(q.get('question_text') or '')[:140]}")
        st.error("Answer status: **NOT IN DOCUMENT**")
        st.write(f"Reason: {reason}")
        st.caption("No generated answer was substituted.")
