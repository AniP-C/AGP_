"""Upload handling: Streamlit UploadedFile(s) -> a temp path `load_document`
can consume, plus the input-summary panel. All extraction/safety logic lives
in `agp_extract.ingest.loader` — this module only stages uploaded bytes.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Optional

import streamlit as st

from agp_extract.ingest.loader import IngestReport, UnsupportedInputError, load_document_with_report

_MAX_UPLOAD_BYTES = 500 * 1024 * 1024  # matches the ZIP extraction cap


def stage_uploaded_files(files: list) -> Path:
    """Writes 1..N uploaded files into a fresh temp dir and returns its path
    (a single ZIP/PDF/image/txt) or, for multiple files, the directory itself
    (treated as a folder input).
    """
    tmp_dir = Path(tempfile.mkdtemp(prefix="agp_upload_"))
    for f in files:
        data = f.getbuffer()
        if len(data) > _MAX_UPLOAD_BYTES:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise UnsupportedInputError(
                f"'{f.name}' is {len(data) / (1024*1024):.0f} MB — exceeds the "
                f"{_MAX_UPLOAD_BYTES // (1024*1024)} MB upload limit")
        (tmp_dir / f.name).write_bytes(data)

    if len(files) == 1:
        return tmp_dir / files[0].name
    return tmp_dir


def resolve_and_report(staged_path: Path) -> tuple[Optional[Path], Optional[IngestReport], Optional[str]]:
    """Runs the real ingestion loader against staged input purely to validate
    it and produce the Input Summary — returns (path_to_pass_to_pipeline,
    report, error_message). Does not run the vision/LLM phases.
    """
    try:
        _pages, report = load_document_with_report(staged_path)
        return staged_path, report, None
    except UnsupportedInputError as exc:
        return None, None, str(exc)
    except FileNotFoundError as exc:
        return None, None, str(exc)
    except Exception as exc:  # malformed image/pdf/etc — never show a raw traceback
        return None, None, f"could not read input: {exc}"


def render_input_summary(report: IngestReport):
    st.markdown("**Input Summary**")
    cols = st.columns(4)
    cols[0].metric("Input type", report.input_type.upper())
    cols[1].metric("Files discovered", report.files_discovered)
    cols[2].metric("Supported", report.files_supported)
    cols[3].metric("Pages", report.page_count)

    if report.ignored:
        with st.expander(f"⚠️ {len(report.ignored)} file(s) ignored"):
            for name, reason in report.ignored:
                st.write(f"- `{name}` — {reason}")
