"""Phase 6 — parser port, cache correctness, coverage validation, reconstruction.

These cover the failures the previous design could not detect at all: a page
that silently produced nothing, a truncated response cached as good data, and a
logical question that was split across blocks or bundled inside one.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agp_extract.providers.base import PageExtraction, RawBlock  # noqa: E402
from agp_extract.cache.store import ExtractionCache  # noqa: E402
from agp_extract.validate.coverage import (  # noqa: E402
    numbering_continuity, page_ink_coverage,
)
from agp_extract.parse.router import route_pages  # noqa: E402
from agp_extract.reconstruct import signals  # noqa: E402


# ── extraction status ──────────────────────────────────────────────────────
def _pe(**kw) -> PageExtraction:
    base = dict(page_index=0, width=100, height=100)
    base.update(kw)
    return PageExtraction(**base)


def test_empty_extraction_is_not_valid():
    assert not _pe(blocks=[]).is_valid()


def test_truncated_extraction_is_not_valid_even_with_blocks():
    pe = _pe(blocks=[RawBlock(type="paragraph", text="hi")], truncated=True)
    assert not pe.is_valid(), "a truncated page is incomplete, not merely short"


def test_populated_extraction_is_valid():
    assert _pe(blocks=[RawBlock(type="paragraph", text="hi")]).is_valid()


# ── cache correctness ──────────────────────────────────────────────────────
def test_failed_extraction_is_never_cached_as_valid(tmp_path):
    cache = ExtractionCache(tmp_path)
    cache.put("sha_a", "m", "v1", _pe(blocks=[]))
    assert cache.get("sha_a", "m", "v1") is None, "an empty page must not be served"
    assert cache.get_failure("sha_a", "m", "v1") is not None
    assert cache.get_failure("sha_a", "m", "v1").failure_reason == "empty"


def test_truncated_extraction_goes_to_the_failure_record(tmp_path):
    cache = ExtractionCache(tmp_path)
    pe = _pe(blocks=[RawBlock(type="paragraph", text="x")], truncated=True)
    cache.put("sha_b", "m", "v1", pe)
    assert cache.get("sha_b", "m", "v1") is None
    assert cache.get_failure("sha_b", "m", "v1").truncated is True


def test_valid_extraction_round_trips(tmp_path):
    cache = ExtractionCache(tmp_path)
    cache.put("sha_c", "m", "v1", _pe(blocks=[RawBlock(type="paragraph", text="ok")]))
    got = cache.get("sha_c", "m", "v1")
    assert got is not None and got.from_cache and len(got.blocks) == 1


def test_purge_removes_poisoned_entries_only(tmp_path):
    cache = ExtractionCache(tmp_path)
    good = _pe(blocks=[RawBlock(type="paragraph", text="ok")])
    cache.put("sha_good", "m", "v1", good)
    # simulate a pre-gate poisoned entry written straight to disk
    cache._key("sha_bad", "m", "v1").write_text(
        _pe(blocks=[]).model_dump_json(), encoding="utf-8")

    removed = cache.purge_invalid()
    assert len(removed) == 1 and "sha_bad" in removed[0]
    assert cache.get("sha_good", "m", "v1") is not None


# ── coverage validation ────────────────────────────────────────────────────
def test_numbering_continuity_finds_a_gap():
    series = numbering_continuity(["10.1 a", "10.2 b", "10.3 c", "10.5 e"])
    gaps = [g for s in series for g in s.gaps]
    assert any("10.4" in g or "missing 4" in g for g in gaps)


def test_numbering_continuity_keeps_independent_series_separate():
    """Two chapters numbering from 1 must not look like one broken sequence."""
    series = numbering_continuity(
        ["10.1 a", "10.2 b", "10.3 c", "11.1 x", "11.2 y", "11.3 z"])
    labels = {s.label for s in series}
    assert labels == {"10.x", "11.x"}
    assert not [g for s in series for g in s.gaps]


def test_numbering_continuity_ignores_too_short_a_run():
    assert numbering_continuity(["1. only", "5. one"]) == []


def test_ink_coverage_full_and_empty():
    from io import BytesIO
    from PIL import Image, ImageDraw

    img = Image.new("L", (200, 200), 255)
    ImageDraw.Draw(img).rectangle([20, 20, 80, 80], fill=0)
    buf = BytesIO(); img.save(buf, format="PNG")
    data = buf.getvalue()

    assert page_ink_coverage(data, [(0, 0, 200, 200)]) > 0.95
    assert page_ink_coverage(data, []) < 0.05, "no boxes ⇒ nothing is covered"


def test_blank_page_counts_as_covered():
    from io import BytesIO
    from PIL import Image
    buf = BytesIO(); Image.new("L", (50, 50), 255).save(buf, format="PNG")
    assert page_ink_coverage(buf.getvalue(), []) == 1.0


# ── router ─────────────────────────────────────────────────────────────────
def test_non_pdf_input_routes_everything_to_ocr(tmp_path):
    plan = route_pages(tmp_path / "images", page_count=3)
    assert plan.summary()["ocr_pages"] == 3
    assert plan.summary()["born_digital"] is False


def test_born_digital_pdf_routes_to_text_layer(tmp_path):
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "digital.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # a realistic page of flowed body text, not one clipped line
    page.insert_textbox(
        fitz.Rect(72, 72, 540, 720),
        "Wave optics describes the propagation of light as waves. " * 30,
        fontsize=11)
    doc.save(pdf); doc.close()

    plan = route_pages(pdf, page_count=1)
    assert plan.summary()["text_layer_pages"] == 1, \
        "a real text layer must not be sent to an expensive vision model"
    assert plan.summary()["born_digital"] is True


def test_sparse_page_falls_back_to_ocr(tmp_path):
    """A near-empty text layer is treated as a scan, not as a read page.

    This is the conservative direction: paying for OCR on a sparse page costs
    money, whereas trusting a stray text fragment loses the page's content.
    """
    fitz = pytest.importorskip("fitz")
    pdf = tmp_path / "sparse.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 300), "12", fontsize=11)  # just a page number
    doc.save(pdf); doc.close()

    assert route_pages(pdf, page_count=1).summary()["ocr_pages"] == 1


# ── reconstruction signals ─────────────────────────────────────────────────
@pytest.mark.parametrize("text", [
    "Example 12 A body moves...", "Q3. Define sound", "10.2 What is the shape",
    "7. Two waves", "(a) first part", "(iv) roman part",
])
def test_enumerator_detected_across_publisher_conventions(text):
    assert signals.has_enumerator(text)


@pytest.mark.parametrize("text", [
    "sound travels through a medium", "The speed of sound depends on",
])
def test_plain_prose_is_not_an_enumerator(text):
    assert not signals.has_enumerator(text)


def test_answer_markers_detected():
    for t in ("Ans. (a) 5", "Answer: 12", "Solution", "Explanation: because"):
        assert signals.is_answer_start(t)


def test_subparts_found_inside_one_block():
    """The NCERT case: (a)-(d) all live inside a single block."""
    text = ("10.2 What is the shape of the wavefront in each of the following cases:\n"
            "(a) Light diverging from a point source.\n"
            "(b) Light emerging out of a convex lens.\n"
            "(c) The portion of the wavefront of light from a distant star.\n")
    marks = signals.find_subpart_offsets(text)
    assert [m[1] for m in marks] == ["(a)", "(b)", "(c)"]
    assert not signals.looks_like_mcq_options(text), \
        "sub-questions must not be mistaken for MCQ options"


def test_mcq_options_are_not_treated_as_subquestions():
    text = ("Half of the wavelength in the given curve is:\n"
            "(a) AC\n(b) BC\n(c) BD\n(d) DE\n")
    assert signals.looks_like_mcq_options(text)


def test_single_marker_is_not_a_subpart_tree():
    assert signals.find_subpart_offsets("Some prose.\n(a) only one marker") == []


def test_inline_subpart_after_question_number():
    """NCERT 10.3: (a) opens the run on the SAME line as the question number."""
    text = ("10.3 (a) The refractive index of glass is 1.5. What is the speed of "
            "light in glass? (Speed of light in vacuum is 3.0 x 10^8 m/s)\n"
            "(b) Is the speed of light in glass independent of the colour of light?")
    marks = signals.find_subpart_offsets(text)
    assert [m[1] for m in marks] == ["(a)", "(b)"], \
        "an inline (a) after the question number must open the sub-part run"


def test_parenthetical_aside_is_not_a_subpart():
    """The guard that lets the inline rule exist safely."""
    text = ("The speed of light in vacuum is 3.0 (a) and the value (b) is used "
            "here only as an aside inside running prose.")
    assert signals.find_subpart_offsets(text) == []


def test_non_consecutive_markers_rejected():
    assert signals.find_subpart_offsets("Question.\n(a) first\n(c) third") == []


def test_roman_subparts_detected():
    """(i) is ambiguous — letter nine or roman one — so the run decides."""
    text = ("What type of wave is represented by:\n"
            "(i) Density-distance graph?\n(ii) Displacement-distance graph?")
    assert [m[1] for m in signals.find_subpart_offsets(text)] == ["(i)", "(ii)"]


@pytest.mark.parametrize("text", [
    # the bug the gold set caught: "answer" as an ordinary verb truncated a
    # whole case-study passage to "7. Read the given extract and"
    "7. Read the given extract and answer the following questions. Thunder and "
    "lighting are natural phenomena.",
    "Explain your answer with a reason.",
    "Answer the following questions:",
    "Give a reason for your answer.",
])
def test_prose_use_of_answer_is_not_a_boundary(text):
    assert signals.answer_boundary_offset(text) is None


@pytest.mark.parametrize("text", [
    "Q1 What is sound?\nAns. (A) (d) oscillation",
    "Example 10.2 Discuss the intensity\nSolution Let I0 be the intensity",
    "Example 10.1\n(a) one\n(b) two\nSolution\n(a) because",
    "What is X?\nAnswer: because Y",
])
def test_real_answer_labels_are_boundaries(text):
    assert signals.answer_boundary_offset(text) is not None


def test_answer_boundary_bounds_the_split():
    """A worked example repeats (a)/(b)/(c) in its solution."""
    text = ("Example 10.1\n(a) Why do frequencies match?\n(b) Does speed drop "
            "reduce energy?\nSolution\n(a) Reflection arises through interaction.\n"
            "(b) No. Energy depends on amplitude.")
    cut = signals.answer_boundary_offset(text)
    assert cut is not None
    q_marks = signals.find_subpart_offsets(text[:cut])
    assert [m[1] for m in q_marks] == ["(a)", "(b)"], \
        "solution parts must not become sub-questions"
