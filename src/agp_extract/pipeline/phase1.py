"""Phase 1 pipeline, orchestrated with LangGraph.

    START → ingest → preprocess → extract → assemble → build_graph
          → build_evidence → evaluate → persist → END

Nodes are thin wrappers over the module functions, so each is independently
testable and the graph stays declarative. Later phases append nodes
(discover_questions, classify, retrieve, associate, validate) and the
confidence-gate loop — the seam is already here.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from langgraph.graph import END, START, StateGraph

from .. import PROMPT_VERSION, __version__, metrics
from ..assemble.document_builder import build_document
from ..cache.store import ExtractionCache
from ..associate import run_association
from ..validate import run_validation
from ..config import Settings, get_settings
from ..discover import run_discovery
from ..eval.hooks import run_evaluation
from ..evidence.layer import build_evidence_units, save_evidence
from ..extract.page_extractor import extract_pages
from ..graph.builder import build_graph
from ..graph.store import save_json as save_graph_json
from ..graph.store import save_sqlite as save_graph_sqlite
from ..ingest.loader import load_document
from ..ingest.preprocess import preprocess_page
from ..providers.factory import get_provider
from ..schemas import RunProvenance
from ..structure import run_structure
from .state import Phase1State

log = logging.getLogger(__name__)


# ── nodes ───────────────────────────────────────────────────────────────────
def node_ingest(state: Phase1State) -> dict:
    s: Settings = state["settings"]
    pages = load_document(s.input_path, max_pages=s.max_pages)
    log.info("[ingest] %d page(s)", len(pages))
    return {"pages": pages}


def node_preprocess(state: Phase1State) -> dict:
    s: Settings = state["settings"]
    out: dict[int, str] = {}
    for p in state["pages"]:
        try:
            res = preprocess_page(
                p.image_bytes, state["preprocess_dir"] / f"page_{p.page_index:04d}.png",
                upscale=s.preprocess_upscale,
            )
            out[p.page_index] = res.preprocessed_path
        except Exception as exc:
            log.warning("[preprocess] page %d failed: %s", p.page_index, exc)
    log.info("[preprocess] %d page(s)", len(out))
    return {"preprocessed": out}


def node_extract(state: Phase1State) -> dict:
    s: Settings = state["settings"]
    cache = ExtractionCache(s.cache_dir, enabled=s.use_cache)

    # Route first: a born-digital page carries an exact text layer, and paying a
    # vision model to re-read its pixels is both worse and far more expensive.
    from ..parse import route_pages
    from ..parse.factory import build_parser

    plan = route_pages(s.input_path, len(state["pages"]))
    routing = plan.summary()

    parser_name = s.parser
    if parser_name == "auto":
        parser_name = "docling" if routing["born_digital"] else "vlm"
        if routing["mixed"]:
            # Mixed documents keep the vision path for now: splitting one
            # document across two parsers would break global reading order,
            # which every downstream structural signal depends on.
            parser_name = "vlm"
            routing["note"] = "mixed text-layer/scanned document; using the vision parser"
    # A page with a real text layer must never be re-OCR'd: that throws away
    # exact characters and replaces them with a model's guess.
    do_ocr = None if parser_name != "docling" else not routing["born_digital"]
    try:
        parser = build_parser(parser_name, state["provider"], s, cache, do_ocr=do_ocr)
    except Exception as exc:
        log.warning("parser %r unavailable (%s); falling back to the vision parser",
                    parser_name, exc)
        parser = build_parser("vlm", state["provider"], s, cache)

    log.info("[route] %s → parser=%s", routing, getattr(parser, "name", "?"))
    blocks, printed, report = extract_pages(
        state["pages"], state["provider"], s, cache, state["assets_dir"],
        parser=parser,
    )
    report["routing"] = routing
    log.info("[extract] %d block(s) via %s", len(blocks), report["parser"])
    return {"blocks": blocks, "printed_pages": printed, "extract_report": report}


def node_assemble(state: Phase1State) -> dict:
    doc = build_document(
        document_id=state["document_id"], blocks=state["blocks"],
        pages=state["pages"], printed_pages=state["printed_pages"],
        run=state["run"], preprocessed=state.get("preprocessed"),
    )
    # A layout parser reports appearance, not purpose: it has no `answer` type.
    # Derive that from the document's own "Solution"/"Ans." headings, so a
    # worked example's solution is findable instead of being reported absent
    # while sitting on the same page.
    from ..parse.semantic_type import apply_semantic_types
    sem = apply_semantic_types(doc)

    log.info("[assemble] hierarchy roots=%d meta.chapter=%s | semantic: %s",
             len(doc.hierarchy), doc.meta.chapter, sem)
    return {"document": doc}


def node_verify_coverage(state: Phase1State) -> dict:
    """Measure what was actually read, independently of what the model claimed.

    Self-reported confidence cannot detect its own blind spots: the leph202 run
    reported 0.963 mean confidence while 16 % of its pages were empty. These
    checks look at the page, not at the model.
    """
    s: Settings = state["settings"]
    from ..validate.coverage import check_coverage

    rep = check_coverage(state["pages"], state["blocks"],
                         coverage_min=s.coverage_min, enabled=s.coverage_enabled)
    d = rep.to_dict()
    if rep.escalate_pages:
        log.warning("[coverage] %d page(s) below threshold → %s",
                    len(rep.escalate_pages), rep.escalate_pages)
    else:
        log.info("[coverage] all %d page(s) pass (mean ink coverage %.3f)",
                 len(rep.pages), d["mean_ink_coverage"])
    return {"coverage": d}


def node_structure(state: Phase1State) -> dict:
    """Phase 2: deterministic structure/relationship reconstruction (in place)."""
    stats = run_structure(state["document"])
    log.info("[structure] reading-order conf=%.2f | subparts=%d or_groups=%d | "
             "continuations=%d | refs %d/%d resolved",
             stats["reading_order"]["mean_reading_order_confidence"],
             stats["grouping"]["subparts"], stats["grouping"]["or_groups"],
             stats["continuation"]["continuation_links"],
             stats["crossref"]["refs_resolved"], stats["crossref"]["refs_total"])
    return {"document": state["document"]}


def node_build_graph(state: Phase1State) -> dict:
    g = build_graph(state["document"])
    log.info("[graph] %d node(s) %d edge(s)", len(g.nodes), len(g.edges))
    return {"graph": g}


def node_build_evidence(state: Phase1State) -> dict:
    units = build_evidence_units(state["document"])
    log.info("[evidence] %d unit(s)", len(units))
    return {"evidence_units": units}


def node_discover_questions(state: Phase1State) -> dict:
    """Phase 3: discover + normalize questions (deterministic + targeted LLM)."""
    s: Settings = state["settings"]
    provider = state["provider"]
    stats = run_discovery(
        state["document"], provider=provider,
        cache_dir=s.cache_dir if s.use_cache else None,
        use_llm=provider.name != "null", settings=s,
    )
    r = stats.get("reconstruction", {})
    log.info("[questions] %d logical (%d roots) | types=%s | unknown=%d | "
             "reconstruction: %d block(s) joined, %d multi-block span(s), %d split(s)",
             stats["total_logical_questions"], stats["root_questions"],
             len(stats["by_question_type"]), stats["unknown_type"],
             r.get("joined_blocks", 0), r.get("spans_multi_block", 0),
             r.get("intra_block_splits", 0))
    return {"document": state["document"]}


def node_associate_answers(state: Phase1State) -> dict:
    """Phase 4: hybrid retrieval + answer/solution association → ANSWERED_BY."""
    s: Settings = state["settings"]
    provider = state["provider"]
    stats = run_association(
        state["document"], provider=provider,
        cache_dir=s.cache_dir if s.use_cache else None,
        graph=state["graph"], use_llm=provider.name != "null",
    )
    log.info("[answers] matched=%d not_in_document=%d partial=%d ambiguous=%d | "
             "ANSWERED_BY=%d | llm_adjudicated=%d | semantic=%s",
             stats["matched"], stats["not_in_document"], stats["partial"],
             stats["ambiguous"], stats["answered_by_edges"],
             stats["llm_adjudicated_questions"], stats["semantic_channel_enabled"])
    return {"document": state["document"], "graph": state["graph"]}


def node_validate(state: Phase1State) -> dict:
    """Phase 5: validation, calibrated confidence, gate, bounded escalation."""
    s: Settings = state["settings"]
    provider = state["provider"]
    pages_by_index = {p.page_index: p for p in state["pages"]}
    stats = run_validation(
        state["document"], provider=provider,
        cache_dir=s.cache_dir if s.use_cache else None, graph=state["graph"],
        settings=s, pages_by_index=pages_by_index,
    )
    log.info("[validate] accepted=%d escalated=%d abstained=%d | errors_caught=%d "
             "recovered=%d numeric_flags=%d | retry_rate=%.0f%%",
             stats["high_confidence_accepted"], stats["escalated"], stats["abstained"],
             stats["validation_errors_caught"], stats["errors_recovered"],
             stats["numeric_inconsistencies"], stats["retry_rate_pct"])
    return {"document": state["document"], "graph": state["graph"]}


def node_evaluate(state: Phase1State) -> dict:
    report = run_evaluation(state["document"], state["graph"],
                            state["settings"], gold_dir=state.get("gold_dir"))
    # Measured extraction facts sit alongside the model's own self-assessment,
    # never replacing it — the two disagreeing is itself the useful signal.
    if state.get("extract_report"):
        report["extraction"] = state["extract_report"]
    if state.get("coverage"):
        report["coverage"] = state["coverage"]
    return {"report": report}


def node_persist(state: Phase1State) -> dict:
    s: Settings = state["settings"]
    out_dir: Path = state["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = state["document"]
    doc.run.finished_at = datetime.now(timezone.utc)

    outputs: dict[str, str] = {}
    (out_dir / "canonical_document.json").write_text(
        doc.model_dump_json(indent=2), encoding="utf-8")
    outputs["canonical_document"] = str(out_dir / "canonical_document.json")

    save_graph_json(state["graph"], out_dir / "graph.json")
    save_graph_sqlite(state["graph"], out_dir / "graph.sqlite")
    outputs["graph_json"] = str(out_dir / "graph.json")
    outputs["graph_sqlite"] = str(out_dir / "graph.sqlite")

    save_evidence(state["evidence_units"], out_dir / "evidence.json")
    outputs["evidence"] = str(out_dir / "evidence.json")

    (out_dir / "questions.json").write_text(
        __import__("json").dumps(
            [q.model_dump(exclude_none=True) for q in doc.questions], indent=2),
        encoding="utf-8")
    outputs["questions"] = str(out_dir / "questions.json")

    (out_dir / "eval_report.json").write_text(
        __import__("json").dumps(state["report"], indent=2), encoding="utf-8")
    outputs["eval_report"] = str(out_dir / "eval_report.json")

    # Human-reviewable Q&A export. Optional: a missing reportlab must never
    # fail a run whose real deliverables (the JSON artifacts) already succeeded.
    try:
        from ..export import export_qa_pdf
        pdf = export_qa_pdf(doc, out_dir / "questions_answers.pdf",
                            assets_dir=out_dir / "assets")
        outputs["questions_answers_pdf"] = str(pdf)
    except ImportError:
        log.info("[persist] reportlab not installed; skipping the Q&A PDF export")
    except Exception as exc:
        log.warning("[persist] Q&A PDF export failed: %s", exc)

    p5 = (doc.stats or {}).get("phase5")
    if p5 is not None:
        (out_dir / "confidence_report.json").write_text(
            __import__("json").dumps(p5, indent=2), encoding="utf-8")
        outputs["confidence_report"] = str(out_dir / "confidence_report.json")
    outputs["assets_dir"] = str(state["assets_dir"])

    log.info("[persist] wrote %d artifact(s) → %s", len(outputs), out_dir)
    return {"outputs": outputs}


# ── graph wiring ────────────────────────────────────────────────────────────
def build_phase1_graph():
    g = StateGraph(Phase1State)
    g.add_node("ingest", node_ingest)
    g.add_node("preprocess", node_preprocess)
    g.add_node("extract", node_extract)
    g.add_node("verify_coverage", node_verify_coverage)
    g.add_node("assemble", node_assemble)
    g.add_node("structure", node_structure)
    g.add_node("build_graph", node_build_graph)
    g.add_node("build_evidence", node_build_evidence)
    g.add_node("discover_questions", node_discover_questions)
    g.add_node("associate_answers", node_associate_answers)
    g.add_node("validate", node_validate)
    g.add_node("evaluate", node_evaluate)
    g.add_node("persist", node_persist)

    g.add_edge(START, "ingest")
    g.add_edge("ingest", "preprocess")
    g.add_edge("preprocess", "extract")
    g.add_edge("extract", "verify_coverage")
    g.add_edge("verify_coverage", "assemble")
    g.add_edge("assemble", "structure")
    g.add_edge("structure", "build_graph")
    g.add_edge("build_graph", "build_evidence")
    g.add_edge("build_evidence", "discover_questions")
    g.add_edge("discover_questions", "associate_answers")
    g.add_edge("associate_answers", "validate")
    g.add_edge("validate", "evaluate")
    g.add_edge("evaluate", "persist")
    g.add_edge("persist", END)
    return g.compile()


def _run_provenance(settings: Settings, provider_name: str) -> RunProvenance:
    return RunProvenance(
        run_id=uuid.uuid4().hex[:12],
        tool_version=__version__, prompt_version=PROMPT_VERSION,
        provider=provider_name, models=settings.models_snapshot(),
        config_snapshot={
            "llm_provider": settings.llm_provider,
            "resolved_provider": provider_name,
            "input_path": str(settings.input_path),
            "preprocess_upscale": settings.preprocess_upscale,
            "use_cache": settings.use_cache,
            "max_pages": settings.max_pages,
        },
        library_versions=_lib_versions(),
        environment=settings.env_snapshot(),
    )


def _lib_versions() -> dict[str, str]:
    import importlib.metadata as m
    out = {}
    for pkg in ("langgraph", "google-genai", "opencv-python", "pydantic", "Pillow", "numpy"):
        try:
            out[pkg] = m.version(pkg)
        except Exception:
            pass
    return out


def run_phase1(settings: Optional[Settings] = None,
               document_id: Optional[str] = None,
               gold_dir: Optional[Path] = None) -> dict:
    """Execute Phase 1 end-to-end; returns {"document","graph","report","outputs"}."""
    settings = settings or get_settings()
    metrics.reset()
    provider = get_provider(settings)
    document_id = document_id or Path(settings.input_path).stem

    out_dir = Path(settings.output_dir) / document_id
    initial: Phase1State = {
        "settings": settings,
        "provider": provider,
        "run": _run_provenance(settings, provider.name),
        "document_id": document_id,
        "out_dir": out_dir,
        "assets_dir": out_dir / "assets",
        "preprocess_dir": out_dir / "preprocessed",
        "gold_dir": gold_dir or (Path("eval") / "gold"),
    }
    # start each run with a clean assets dir (block ids are positional, so a
    # re-run with a different block count would otherwise leave orphan crops)
    assets_dir = out_dir / "assets"
    if assets_dir.exists():
        for f in assets_dir.glob("*.png"):
            f.unlink()
    assets_dir.mkdir(parents=True, exist_ok=True)

    app = build_phase1_graph()
    final = app.invoke(initial)
    return {
        "document": final["document"],
        "graph": final["graph"],
        "report": final["report"],
        "outputs": final["outputs"],
    }
