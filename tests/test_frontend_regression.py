"""Regression tests for the Streamlit frontend's ingestion adapter and
backend-invocation plumbing (frontend/ingestion_ui.py, frontend/runner.py,
frontend/data.py). Uses the offline `null` provider throughout — no Gemini
API key needed, no network calls, no cost. Run with:

    python tests/test_frontend_regression.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from PIL import Image

from agp_extract.config import get_settings
from agp_extract.ingest.loader import UnsupportedInputError, load_document_with_report
from frontend.data import load_run_from_disk
from frontend.runner import run_with_progress

passed, failed = [], []


def check(name: str, fn):
    try:
        fn()
        passed.append(name)
        print(f"[PASS] {name}")
    except Exception as exc:  # noqa: BLE001 — this is a test runner
        failed.append((name, exc))
        print(f"[FAIL] {name}: {exc}")


def _tmp() -> Path:
    return Path(tempfile.mkdtemp(prefix="agp_regtest_"))


def _make_jpgs(dir_: Path, n: int) -> list[Path]:
    paths = []
    for i in range(n):
        p = dir_ / f"page_{i:02d}.jpg"
        Image.new("RGB", (300, 400), "white").save(p)
        paths.append(p)
    return paths


def _make_pdf(dir_: Path, n_pages: int = 2) -> Path:
    imgs = [Image.new("RGB", (300, 400), "white") for _ in range(n_pages)]
    p = dir_ / "doc.pdf"
    imgs[0].save(p, save_all=True, append_images=imgs[1:])
    return p


def _null_settings(input_path: Path, output_dir: Path, max_pages: int = 0):
    return get_settings().model_copy(update={
        "llm_provider": "null", "input_path": input_path,
        "output_dir": output_dir, "max_pages": max_pages, "use_cache": False,
    })


# ── 1. Demo mode ──────────────────────────────────────────────────────────
def test_demo_mode():
    bundle = load_run_from_disk("cl9ch11")
    assert bundle.document["stats"]  # non-empty
    assert len(bundle.document["pages"]) == 30
    assert len(list(bundle.all_leaf_questions())) > 0
    assert bundle.report is not None
    assert bundle.confidence is not None


# ── 2. Single JPG ─────────────────────────────────────────────────────────
def test_single_jpg():
    d = _tmp()
    try:
        [jpg] = _make_jpgs(d, 1)
        pages, report = load_document_with_report(jpg)
        assert len(pages) == 1 and report.input_type == "image"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 3. Multiple JPGs ──────────────────────────────────────────────────────
def test_multiple_jpgs():
    d = _tmp()
    try:
        _make_jpgs(d, 3)
        pages, report = load_document_with_report(d)
        assert len(pages) == 3 and report.input_type == "folder"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 4. PDF ────────────────────────────────────────────────────────────────
def test_pdf():
    d = _tmp()
    try:
        pdf = _make_pdf(d, n_pages=2)
        pages, report = load_document_with_report(pdf)
        assert len(pages) == 2 and report.input_type == "pdf"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 5. ZIP containing JPGs ────────────────────────────────────────────────
def test_zip_of_jpgs():
    d = _tmp()
    try:
        src = d / "src"
        src.mkdir()
        _make_jpgs(src, 3)
        zpath = d / "chapter.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            for f in src.iterdir():
                zf.write(f, f.name)
        pages, report = load_document_with_report(zpath)
        assert len(pages) == 3 and report.input_type == "zip" and report.files_supported == 3
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 6. ZIP containing PDF ─────────────────────────────────────────────────
def test_zip_of_pdf():
    d = _tmp()
    try:
        src = d / "src"
        src.mkdir()
        _make_pdf(src, n_pages=2)
        zpath = d / "chapter.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            for f in src.iterdir():
                zf.write(f, f.name)
        pages, report = load_document_with_report(zpath)
        assert len(pages) == 2 and report.input_type == "zip"
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 7. Mixed ZIP (images + txt + unsupported) ────────────────────────────
def test_mixed_zip():
    d = _tmp()
    try:
        src = d / "src"
        src.mkdir()
        _make_jpgs(src, 2)
        (src / "notes.txt").write_text("hello", encoding="utf-8")
        (src / "readme.docx").write_bytes(b"not supported")
        zpath = d / "chapter.zip"
        with zipfile.ZipFile(zpath, "w") as zf:
            for f in src.iterdir():
                zf.write(f, f.name)
        pages, report = load_document_with_report(zpath)
        # images take precedence; txt + docx both end up ignored
        assert len(pages) == 2
        assert any("docx" in name for name, _ in report.ignored)
        assert any("notes.txt" in name for name, _ in report.ignored)
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 8. TXT ────────────────────────────────────────────────────────────────
def test_txt():
    d = _tmp()
    try:
        p = d / "notes.txt"
        p.write_text("Chapter 1. What is sound?\nSound is a form of energy.", encoding="utf-8")
        pages, report = load_document_with_report(p)
        assert len(pages) == 1 and report.input_type == "txt"
        assert pages[0].width > 0 and len(pages[0].image_bytes) > 0
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 9. Malformed ZIP ──────────────────────────────────────────────────────
def test_malformed_zip():
    d = _tmp()
    try:
        p = d / "bad.zip"
        p.write_bytes(b"this is not a zip file")
        try:
            load_document_with_report(p)
            raise AssertionError("expected UnsupportedInputError")
        except UnsupportedInputError:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 9b. Empty ZIP ─────────────────────────────────────────────────────────
def test_empty_zip():
    d = _tmp()
    try:
        p = d / "empty.zip"
        with zipfile.ZipFile(p, "w"):
            pass
        try:
            load_document_with_report(p)
            raise AssertionError("expected UnsupportedInputError")
        except UnsupportedInputError:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 10. Unsupported file ─────────────────────────────────────────────────
def test_unsupported_file():
    d = _tmp()
    try:
        p = d / "notes.docx"
        p.write_bytes(b"not supported")
        try:
            load_document_with_report(p)
            raise AssertionError("expected UnsupportedInputError")
        except UnsupportedInputError:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 10b. Path traversal in ZIP ────────────────────────────────────────────
def test_zip_path_traversal_blocked():
    d = _tmp()
    try:
        p = d / "evil.zip"
        with zipfile.ZipFile(p, "w") as zf:
            zf.writestr("../../evil.jpg", b"x")
        try:
            load_document_with_report(p)
            raise AssertionError("expected UnsupportedInputError")
        except UnsupportedInputError:
            pass
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ── 11. Full pipeline via frontend.runner (offline), through app.py's own
#        invocation path — proves the Streamlit "Process Document" wiring
#        produces the same shape run_phase1 would, and that persisted
#        cl9ch11 outputs (Phase 1-5) still validate against test_acceptance.
def test_runner_end_to_end_offline():
    d = _tmp()
    try:
        _make_jpgs(d, 2)
        out_dir = d / "out"
        settings = _null_settings(d, out_dir)
        seen_stages = []
        result = run_with_progress(
            settings, document_id="frontend_regtest",
            on_stage=lambda node_id, phase: seen_stages.append(node_id),
        )
        assert result["document"].pages and len(result["document"].pages) == 2
        assert "persist" in seen_stages and "ingest" in seen_stages
        assert (out_dir / "frontend_regtest" / "canonical_document.json").exists()
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_existing_cl9ch11_outputs_still_valid():
    import subprocess
    r = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "test_acceptance.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    assert r.returncode == 0, f"test_acceptance.py failed:\n{r.stdout}\n{r.stderr}"


if __name__ == "__main__":
    tests = [
        ("Demo mode", test_demo_mode),
        ("Single JPG", test_single_jpg),
        ("Multiple JPGs", test_multiple_jpgs),
        ("PDF", test_pdf),
        ("ZIP of JPGs", test_zip_of_jpgs),
        ("ZIP of PDF", test_zip_of_pdf),
        ("Mixed ZIP", test_mixed_zip),
        ("TXT", test_txt),
        ("Malformed ZIP", test_malformed_zip),
        ("Empty ZIP", test_empty_zip),
        ("Unsupported file", test_unsupported_file),
        ("ZIP path traversal blocked", test_zip_path_traversal_blocked),
        ("Runner end-to-end (offline)", test_runner_end_to_end_offline),
        ("Existing cl9ch11 outputs still valid", test_existing_cl9ch11_outputs_still_valid),
    ]
    for name, fn in tests:
        check(name, fn)

    print(f"\n{len(passed)}/{len(tests)} passed")
    if failed:
        print("Failures:")
        for name, exc in failed:
            print(f"  - {name}: {exc}")
        sys.exit(1)
