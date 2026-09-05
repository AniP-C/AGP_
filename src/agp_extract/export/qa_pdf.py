"""Render extracted questions + answers as a reviewable PDF.

The JSON outputs are the machine contract; this is the human one. A reviewer
should be able to check the extraction without reading JSON, which means the
PDF has to be honest about what is *not* there:

* an answer that is absent from the source prints as ``NOT IN DOCUMENT`` with
  the reason, never as a blank space and never invented;
* marks are omitted when the publisher prints none (NCERT prints no marks;
  Educart does) rather than defaulted to zero;
* every question carries its source pages and block ids, so any line can be
  traced back to the page it came from.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

_STATUS_LABEL = {
    "matched": "Answer",
    "partial": "Answer (partial)",
    "ambiguous": "Answer (ambiguous - needs review)",
    "not_in_document": "NOT IN DOCUMENT",
}


# The built-in PDF fonts have no glyphs for Unicode sub/superscripts, which show
# up as hollow boxes — and science text is full of them (I₀, P₁, 10⁸, m s⁻¹).
# Map them to real <sub>/<super> markup, which ReportLab renders properly.
_SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₒₓₕₖₗₘₙₚₛₜ",
                            "0123456789+-=()aeoxhklmnpst")
_SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿⁱ", "0123456789+-=()ni")


def _mark_scripts(text: str) -> str:
    """Wrap runs of sub/superscript characters in ReportLab markup."""
    out, buf, mode = [], [], None
    for ch in text:
        # maketrans() keys are ordinals, not characters
        kind = ("sub" if ord(ch) in _SUBSCRIPTS else
                "super" if ord(ch) in _SUPERSCRIPTS else None)
        if kind != mode:
            if mode:
                out.append(f"<{mode}>{''.join(buf)}</{mode}>")
                buf = []
            mode = kind
        if kind:
            buf.append(ch.translate(_SUBSCRIPTS if kind == "sub" else _SUPERSCRIPTS))
        else:
            out.append(ch)
    if mode:
        out.append(f"<{mode}>{''.join(buf)}</{mode}>")
    return "".join(out)


def _esc(text: Optional[str]) -> str:
    from xml.sax.saxutils import escape
    return _mark_scripts(escape(text or "")).replace("\n", "<br/>")


def export_qa_pdf(doc, out_path: str | Path, assets_dir: Optional[Path] = None,
                  title: Optional[str] = None) -> Path:
    """Write a questions-and-answers PDF for ``doc``. Returns the path."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    ss = getSampleStyleSheet()
    st_q = ParagraphStyle("Q", parent=ss["BodyText"], fontSize=10.5, leading=14,
                          spaceAfter=4, alignment=TA_LEFT)
    st_sub = ParagraphStyle("Sub", parent=st_q, leftIndent=14)
    st_ans = ParagraphStyle("A", parent=st_q, leftIndent=14,
                            textColor=colors.HexColor("#1a5c2a"))
    st_missing = ParagraphStyle("Miss", parent=st_ans,
                                textColor=colors.HexColor("#9a3412"))
    st_meta = ParagraphStyle("Meta", parent=ss["BodyText"], fontSize=7.5,
                             leading=10, textColor=colors.HexColor("#666666"))
    st_h = ParagraphStyle("H", parent=ss["Heading2"], fontSize=13, spaceBefore=10)

    meta = getattr(doc, "meta", None)
    head = title or " · ".join(
        str(x) for x in [getattr(meta, "subject", None), getattr(meta, "grade", None),
                         f"Ch. {getattr(meta, 'chapter', '')}".strip(),
                         getattr(meta, "publisher", None)] if x)

    story = [Paragraph(f"<b>{_esc(head or doc.document_id)}</b>", ss["Title"]),
             Paragraph(f"{_esc(doc.document_id)} — extracted questions &amp; answers",
                       st_meta), Spacer(1, 6)]

    questions = list(getattr(doc, "questions", None) or [])
    story.append(Paragraph(_summary_line(questions), st_meta))
    story.append(Spacer(1, 10))

    for i, q in enumerate(questions, 1):
        story.extend(_render_question(
            q, i, None, assets_dir,
            (st_q, st_sub, st_ans, st_missing, st_meta, st_h),
            Paragraph, Spacer, Image, Table, TableStyle, colors, mm))

    SimpleDocTemplate(str(out_path), pagesize=A4,
                      leftMargin=18 * mm, rightMargin=18 * mm,
                      topMargin=16 * mm, bottomMargin=16 * mm,
                      title=head or doc.document_id).build(story)
    log.info("[export] wrote %s", out_path)
    return out_path


def _summary_line(questions: list) -> str:
    def walk(qs):
        for q in qs:
            yield q
            yield from walk(getattr(q, "sub_questions", None) or [])

    allq = list(walk(questions))
    n_ans = sum(1 for q in allq
                if _status(q) in ("matched", "partial"))
    n_missing = sum(1 for q in allq if _status(q) == "not_in_document")
    return (f"{len(questions)} top-level · {len(allq)} logical questions · "
            f"{n_ans} with an answer in the document · "
            f"{n_missing} explicitly not in the document")


def _status(q) -> Optional[str]:
    aa = getattr(q, "answer_association", None)
    return getattr(aa, "status", None) if aa else None


def _marks_text(q) -> str:
    m = getattr(q, "marks", None)
    if not m or m.value is None:
        return ""      # absent, not zero — do not invent a weightage
    v = int(m.value) if float(m.value).is_integer() else m.value
    src = getattr(m, "source", None)
    return f" · {v} mark{'s' if v != 1 else ''}" + (f" ({src})" if src else "")


def _render_question(q, num, parent_label, assets_dir, styles, Paragraph,
                     Spacer, Image, Table, TableStyle, colors, mm) -> list:
    st_q, st_sub, st_ans, st_missing, st_meta, st_h = styles
    out = []
    label = getattr(q, "question_number", None) or str(num)
    style = st_q if parent_label is None else st_sub

    bits = [f"<b>{_esc(str(label))}.</b> {_esc(getattr(q, 'question_text', ''))}"]
    out.append(Paragraph(" ".join(bits), style))

    # Page first: it is the thing a reviewer needs in order to look the question
    # up in the physical book, so it leads the metadata line rather than
    # trailing it.
    prov = getattr(q, "provenance", None)
    pages = (prov.printed_pages or prov.source_pages or []) if prov else []
    page_bit = (f"<b>p. {', '.join(str(p) for p in pages)}</b>" if pages else "")

    tags = [getattr(q, "question_type", None), getattr(q, "source_type", None)]
    rest = " · ".join(_esc(str(t)) for t in tags if t) + _marks_text(q)
    if prov and prov.source_blocks:
        rest += f" · blocks: {', '.join(prov.source_blocks[:4])}"
    meta_bits = " · ".join(x for x in (page_bit, rest.strip(" ·")) if x)
    if meta_bits:
        out.append(Paragraph(meta_bits, st_meta))

    # Options are parsed out of the stem, so they are usually already visible in
    # the text above. Repeating them adds noise; only render when they add
    # something the reader cannot already see.
    opts = list(getattr(q, "options", None) or [])
    qtext = getattr(q, "question_text", "") or ""
    if opts and not all((getattr(o, "text", "") or "") in qtext for o in opts):
        for i, opt in enumerate(opts):
            lab = (getattr(opt, "label", "") or "").strip() or chr(ord("a") + i)
            out.append(Paragraph(
                f"({_esc(lab)}) {_esc(getattr(opt, 'text', ''))}", st_sub))

    # linked figures/diagrams/equations, so a figure-dependent question stays answerable
    if assets_dir:
        for aid in (getattr(q, "related_assets", None) or [])[:3]:
            p = Path(assets_dir) / f"{aid}.png"
            if p.exists():
                try:
                    out.append(Image(str(p), width=70 * mm, height=45 * mm,
                                     kind="proportional"))
                except Exception:
                    pass

    status = _status(q)
    if status:
        aa = q.answer_association
        head = _STATUS_LABEL.get(status, status)
        if status == "not_in_document":
            reason = getattr(aa, "reason", None) or "no answer present in the source"
            out.append(Paragraph(f"<b>{head}</b> — {_esc(reason)}", st_missing))
        else:
            body = getattr(aa, "answer_preview", "") or ""
            out.append(Paragraph(f"<b>{head}:</b> {_esc(body)}", st_ans))
            if getattr(aa, "answer_block_ids", None):
                out.append(Paragraph(
                    "source blocks: " + ", ".join(aa.answer_block_ids[:4]), st_meta))

    for j, sub in enumerate(getattr(q, "sub_questions", None) or [], 1):
        out.extend(_render_question(sub, j, label, assets_dir, styles, Paragraph,
                                    Spacer, Image, Table, TableStyle, colors, mm))

    out.append(Spacer(1, 7))
    return out
