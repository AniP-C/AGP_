from __future__ import annotations

import streamlit as st

from ..components import download_artifact_buttons, metric_row
from ..data import RunBundle


def _render_extraction_integrity(report: dict):
    """How the document was read, and whether anything was missed.

    Kept visually separate from the model's own confidence score, because the
    two answer different questions: confidence says how sure the model was,
    coverage says how much of the page was actually captured. A page can report
    high confidence while being empty, so both are shown and neither is hidden
    behind the other.
    """
    extraction = report.get("extraction") or {}
    coverage = report.get("coverage") or {}
    if not extraction and not coverage:
        return

    st.subheader("How this document was read")
    routing = extraction.get("routing") or {}
    parser = extraction.get("parser", "—")
    if routing:
        kind = ("born-digital (text layer)" if routing.get("born_digital")
                else "mixed" if routing.get("mixed") else "scanned / image-only")
        st.caption(
            f"Detected as **{kind}** — {routing.get('text_layer_pages', 0)} page(s) "
            f"with a usable text layer, {routing.get('ocr_pages', 0)} needing OCR or "
            f"vision. Parser selected: **{parser}**"
            + (f" ({extraction['parser_version']})" if extraction.get("parser_version") else "")
            + "."
        )

    cols = []
    if coverage:
        cols.append((
            "Page content captured",
            f"{coverage.get('mean_ink_coverage', 0) * 100:.1f}%",
            "Share of the page's actual printed content enclosed by an extracted "
            "block. Measured from the page image — independent of what the model "
            "reported about itself.",
        ))
        cols.append((
            "Pages needing review",
            str(coverage.get("n_pages_to_escalate", 0)),
            "Pages whose coverage fell below threshold, or that yielded far fewer "
            "blocks than their neighbours.",
        ))
    failed = extraction.get("n_failed_pages")
    if failed is not None:
        cols.append((
            "Pages that failed to parse", str(failed),
            "A page returning nothing is recorded as a failure, never cached as a "
            "blank page, so it can be retried instead of silently lost.",
        ))
    if cols:
        metric_row(cols)

    flagged = coverage.get("pages_to_escalate") or []
    if flagged:
        st.caption("Pages flagged for review: " + ", ".join(str(p) for p in flagged))
    gaps = [g for s in (coverage.get("numbering") or []) for g in (s.get("gaps") or [])]
    if gaps:
        st.warning("Possible gaps in question numbering (a signal to investigate, "
                   "not proof of a missing question):\n\n- " + "\n- ".join(gaps[:8]))
    bad = extraction.get("failed_pages") or {}
    if bad:
        st.error("Pages that failed to parse: " + ", ".join(
            f"page {k} ({v})" for k, v in bad.items()))


def _render_reconstruction(report: dict):
    """What question reconstruction actually did to the raw blocks."""
    recon = ((report.get("questions") or {}).get("reconstruction")) or {}
    if not recon:
        return
    st.caption(
        f"Question reconstruction: {recon.get('joined_blocks', 0)} block(s) joined "
        f"into continuing questions · {recon.get('spans_multi_block', 0)} question(s) "
        f"spanning multiple blocks · {recon.get('intra_block_splits', 0)} block(s) "
        f"split into sub-question trees · {recon.get('llm_adjudications', 0)} "
        f"ambiguous boundary decision(s) sent to the model."
    )


def render(bundle: RunBundle):
    st.header("Overview")
    st.caption(
        "A recruiter-facing summary of what the pipeline extracted and validated "
        "for this document, read directly from the backend's own evaluation output."
    )

    report = bundle.report or {}
    summary = report.get("summary", {})

    if not summary:
        st.warning("No eval_report.json summary found for this run.")
        return

    meta = bundle.document.get("meta", {})
    st.markdown(
        f"**{meta.get('subject', '—')} · Grade {meta.get('grade', '—')} · "
        f"Chapter {meta.get('chapter', '—')}** ({meta.get('publisher', 'unknown publisher')})"
    )

    st.subheader("Extraction")
    metric_row([
        ("Pages processed", str(summary.get("pages", "—")), "Total pages ingested from the source."),
        ("Content blocks", str(summary.get("blocks", "—")), "Paragraphs, questions, tables, figures, equations, etc."),
        ("Assets preserved", f"{summary.get('pct_assets_preserved', 0):.0f}%", "Figures/diagrams/tables cropped and saved as files."),
        ("Provenance coverage", f"{summary.get('pct_blocks_fully_traceable', 0):.0f}%", "Every block traceable to a source page + bounding box."),
    ])

    _render_extraction_integrity(report)

    st.subheader("Questions & answers")
    q_by_type = summary.get("questions_by_source_type", {})
    metric_row([
        ("Questions discovered", str(summary.get("total_logical_questions", "—")), "Logical questions, including nested sub-parts."),
        ("Answers matched", str(summary.get("answers_matched", "—")), "Answers found and linked with supporting evidence."),
        ("Not in document", str(summary.get("answers_not_in_document", "—")), "Honestly reported as absent — never hallucinated."),
        ("False associations", str(summary.get("answers_false_associations", "—")), "Answers linked to the wrong question. Target: 0."),
    ])
    if q_by_type:
        st.caption("By source type: " + ", ".join(f"{k.replace('_', ' ')} = {v}" for k, v in q_by_type.items()))
    _render_reconstruction(report)

    st.subheader("Validation & reliability")
    metric_row([
        ("Accepted (high confidence)", str(summary.get("accepted", "—")), "Passed the confidence gate without intervention."),
        ("Escalated", str(summary.get("escalated", "—")), "Re-checked with a stronger model when confidence was borderline."),
        ("Abstained", str(summary.get("abstained", "—")), "Left unresolved rather than guessed, when evidence stayed weak."),
        ("Numeric inconsistencies caught", str(summary.get("numeric_inconsistencies", "—")), "Arithmetic in the source verified with SymPy; mismatches flagged, not corrected."),
    ])

    cost = summary.get("cost", {})
    if cost:
        st.caption(
            f"Run cost: {cost.get('api_calls_total', '—')} API call(s) "
            f"({cost.get('cache_hit_rate_pct', 0):.0f}% cache hit rate)."
        )

    st.subheader("Export")
    download_artifact_buttons(bundle)
