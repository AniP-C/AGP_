"""AGP Document Intelligence — recruiter-facing frontend.

Presentation/integration layer only: it stages uploads, calls the existing
5-phase backend (`agp_extract.pipeline.phase1.run_phase1`, driven here via
`frontend.runner` for real per-stage progress), and renders the backend's own
output objects/files. No extraction, retrieval, or validation logic lives here.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent / "src"))

from agp_extract.config import get_settings
from agp_extract.ingest.loader import UnsupportedInputError

from frontend.data import REPO_ROOT, load_run_from_disk, load_run_from_result
from frontend.ingestion_ui import render_input_summary, resolve_and_report, stage_uploaded_files
from frontend.runner import STAGE_LABELS, run_with_progress
from frontend.views import evidence, explorer, overview, questions, validation

log = logging.getLogger("agp_frontend")
logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="AGP Document Intelligence", layout="wide", page_icon="📘")

DEMO_DOCUMENT_ID = "cl9ch11"
DEMO_INPUT_DIR = REPO_ROOT / "cl9ch11"


def _init_state():
    st.session_state.setdefault("bundle", None)
    st.session_state.setdefault("input_name", None)
    st.session_state.setdefault("status", "idle")  # idle | staged | running | done | error
    st.session_state.setdefault("staged_path", None)
    st.session_state.setdefault("ingest_report", None)
    st.session_state.setdefault("error", None)


def _sidebar():
    st.sidebar.title("📘 AGP Document Intelligence")
    st.sidebar.caption("Multimodal Q&A extraction with full provenance")

    st.sidebar.divider()
    st.sidebar.subheader("Demo Sample")
    if st.sidebar.button("Load AGP Demo Sample", use_container_width=True):
        _load_demo()

    st.sidebar.divider()
    st.sidebar.subheader("Upload Files")
    uploaded = st.sidebar.file_uploader(
        "PDF, JPG, JPEG, PNG, WEBP, TXT, or ZIP",
        type=["pdf", "jpg", "jpeg", "png", "webp", "txt", "zip"],
        accept_multiple_files=True,
    )
    if uploaded:
        _stage_upload(uploaded)

    if st.session_state["ingest_report"] is not None:
        render_input_summary(st.session_state["ingest_report"])

    st.sidebar.divider()
    st.sidebar.subheader("Process Document")
    can_process = st.session_state["status"] == "staged"
    has_key = get_settings().has_gemini_key()
    if not has_key:
        st.sidebar.caption("⚠️ No Gemini API key configured — set GEMINI_API_KEY in `.env` to process uploads.")
    if st.sidebar.button("Process Document", disabled=not can_process, use_container_width=True):
        _run_pipeline()

    st.sidebar.divider()
    st.sidebar.markdown(f"**Current input:** {st.session_state['input_name'] or '—'}")
    st.sidebar.markdown(f"**Status:** {st.session_state['status']}")


def _load_demo():
    try:
        bundle = load_run_from_disk(DEMO_DOCUMENT_ID)
    except Exception as exc:
        st.session_state["status"] = "error"
        st.session_state["error"] = f"Could not load the demo sample: {exc}"
        log.exception("demo load failed")
        return
    st.session_state["bundle"] = bundle
    st.session_state["input_name"] = f"{DEMO_DOCUMENT_ID} (demo sample, pre-processed)"
    st.session_state["status"] = "done"
    st.session_state["error"] = None


def _stage_upload(uploaded_files):
    try:
        staged_path = stage_uploaded_files(uploaded_files)
        resolved_path, report, err = resolve_and_report(staged_path)
    except UnsupportedInputError as exc:
        st.session_state["status"] = "error"
        st.session_state["error"] = str(exc)
        return
    if err:
        st.session_state["status"] = "error"
        st.session_state["error"] = err
        st.session_state["ingest_report"] = None
        return
    st.session_state["staged_path"] = resolved_path
    st.session_state["ingest_report"] = report
    st.session_state["input_name"] = ", ".join(f.name for f in uploaded_files)
    st.session_state["status"] = "staged"
    st.session_state["error"] = None


def _run_pipeline():
    staged_path = st.session_state["staged_path"]
    document_id = Path(st.session_state["input_name"].split(",")[0]).stem or "upload"
    settings = get_settings().model_copy(update={
        "input_path": staged_path,
        "output_dir": REPO_ROOT / "outputs",
    })

    st.session_state["status"] = "running"
    status_box = st.sidebar.status("Running pipeline…", expanded=True)
    try:
        def on_stage(node_id: str, phase: str):
            label = STAGE_LABELS.get(node_id, node_id)
            status_box.write(f"✓ {label}")

        result = run_with_progress(settings, document_id=document_id, on_stage=on_stage)
        status_box.update(label="Pipeline complete", state="complete")
        bundle = load_run_from_result(document_id, result, out_dir=REPO_ROOT / "outputs" / document_id)
        st.session_state["bundle"] = bundle
        st.session_state["status"] = "done"
        st.session_state["error"] = None
    except Exception as exc:
        status_box.update(label="Pipeline failed", state="error")
        st.session_state["status"] = "error"
        st.session_state["error"] = f"Processing failed: {exc}"
        log.exception("pipeline run failed")
        with st.sidebar.expander("Technical details"):
            st.code(traceback.format_exc())


def _main():
    _init_state()
    _sidebar()

    if st.session_state["error"]:
        st.error(st.session_state["error"])

    bundle = st.session_state["bundle"]
    if bundle is None:
        st.title("AGP Document Intelligence")
        st.markdown(
            "Upload a chapter (PDF, images, ZIP, or TXT) in the sidebar and click "
            "**Process Document** — or click **Load AGP Demo Sample** to explore a "
            "fully processed chapter immediately, with no API key required."
        )
        return

    tabs = st.tabs(["Overview", "Questions", "Document Explorer", "Evidence & Answers", "Validation"])
    with tabs[0]:
        overview.render(bundle)
    with tabs[1]:
        questions.render(bundle)
    with tabs[2]:
        explorer.render(bundle)
    with tabs[3]:
        evidence.render(bundle)
    with tabs[4]:
        validation.render(bundle)


if __name__ == "__main__":
    _main()
