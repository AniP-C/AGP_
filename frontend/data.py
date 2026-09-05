"""Loads a processed run (from disk, or straight from a `run_phase1` result)
into a single in-memory bundle the views read from. No business logic here —
just reading the backend's own output files/objects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class RunBundle:
    document_id: str
    out_dir: Path
    document: dict  # CanonicalDocument, as a plain dict (model_dump or reloaded JSON)
    questions: list  # flat list of top-level Question dicts (with nested sub_questions)
    graph: Optional[dict] = None
    evidence: Optional[list] = None
    report: Optional[dict] = None
    confidence: Optional[dict] = None
    outputs: dict = field(default_factory=dict)  # artifact name -> path, if known

    # ── convenience lookups built once, reused across views ────────────────
    _block_index: dict = field(default_factory=dict, repr=False)
    _page_index: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self._block_index = {b["id"]: b for b in self.document.get("blocks", [])}
        self._page_index = {p["page_index"]: p for p in self.document.get("pages", [])}

    def block(self, block_id: str) -> Optional[dict]:
        return self._block_index.get(block_id)

    def page(self, page_index: int) -> Optional[dict]:
        return self._page_index.get(page_index)

    def all_leaf_questions(self):
        """Flatten the question tree — leaf nodes are what carry answers/validation."""
        def walk(qs):
            for q in qs:
                subs = q.get("sub_questions") or []
                if subs:
                    yield from walk(subs)
                else:
                    yield q
        yield from walk(self.questions)

    def all_questions_flat(self):
        """Every question node, root and nested, depth-first."""
        def walk(qs):
            for q in qs:
                yield q
                yield from walk(q.get("sub_questions") or [])
        yield from walk(self.questions)

    def artifact_path(self, name: str) -> Optional[Path]:
        candidates = {
            "questions_answers.pdf": self.out_dir / "questions_answers.pdf",
            "questions.json": self.out_dir / "questions.json",
            "canonical_document.json": self.out_dir / "canonical_document.json",
            "graph.json": self.out_dir / "graph.json",
            "evidence.json": self.out_dir / "evidence.json",
            "eval_report.json": self.out_dir / "eval_report.json",
            "confidence_report.json": self.out_dir / "confidence_report.json",
        }
        p = candidates.get(name)
        return p if p and p.exists() else None

    def ensure_qa_pdf(self) -> Optional[Path]:
        """Return the Q&A PDF, rendering it on demand if it is not on disk.

        Runs persisted before the export existed (and the bundled demo) have no
        PDF beside them; generating it here means the download is always
        available rather than silently missing for older runs.
        """
        existing = self.artifact_path("questions_answers.pdf")
        if existing:
            return existing
        try:
            from agp_extract.schemas import CanonicalDocument
            from agp_extract.export import export_qa_pdf

            doc = CanonicalDocument.model_validate(self.document)
            if not doc.questions and self.questions:
                from agp_extract.schemas import Question
                doc.questions = [Question.model_validate(q) for q in self.questions]
            return export_qa_pdf(
                doc, self.out_dir / "questions_answers.pdf",
                assets_dir=self.out_dir / "assets")
        except Exception:  # never let an export problem break the page
            return None


def _read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_run_from_disk(document_id: str, output_dir: Path = REPO_ROOT / "outputs") -> RunBundle:
    """Load a previously persisted run (e.g. the `cl9ch11` demo) purely from
    its output JSON files — no pipeline call, no API key needed.
    """
    out_dir = Path(output_dir) / document_id
    doc_path = out_dir / "canonical_document.json"
    if not doc_path.exists():
        raise FileNotFoundError(
            f"no processed run found for '{document_id}' under {out_dir}")

    document = _read_json(doc_path)
    questions = _read_json(out_dir / "questions.json") if (out_dir / "questions.json").exists() else document.get("questions", [])
    graph = _read_json(out_dir / "graph.json") if (out_dir / "graph.json").exists() else None
    evidence = _read_json(out_dir / "evidence.json") if (out_dir / "evidence.json").exists() else None
    report = _read_json(out_dir / "eval_report.json") if (out_dir / "eval_report.json").exists() else None
    confidence = _read_json(out_dir / "confidence_report.json") if (out_dir / "confidence_report.json").exists() else None

    return RunBundle(
        document_id=document_id, out_dir=out_dir, document=document,
        questions=questions, graph=graph, evidence=evidence,
        report=report, confidence=confidence,
    )


def load_run_from_result(document_id: str, result: dict, out_dir: Path) -> RunBundle:
    """Build a bundle straight from an in-memory `run_phase1(...)` result,
    avoiding a disk round-trip right after a fresh run.
    """
    doc = result["document"]
    document = json.loads(doc.model_dump_json())
    graph_obj = result.get("graph")
    graph = json.loads(graph_obj.model_dump_json()) if graph_obj is not None else None
    report = result.get("report")
    confidence = (doc.stats or {}).get("phase5") if hasattr(doc, "stats") else None
    evidence = None
    ev_path = Path(result.get("outputs", {}).get("evidence", ""))
    if ev_path.exists():
        evidence = _read_json(ev_path)

    return RunBundle(
        document_id=document_id, out_dir=Path(out_dir), document=document,
        questions=[q.model_dump(exclude_none=True) for q in doc.questions],
        graph=graph, evidence=evidence, report=report, confidence=confidence,
        outputs=result.get("outputs", {}),
    )
