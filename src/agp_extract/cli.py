"""Command-line entry point.

    python run.py phase1 --input cl9ch11 [--provider gemini|null] [--max-pages N]
    python run.py env        # environment / readiness check
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .config import get_settings
from .ocr.ocr import tesseract_available


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )


def cmd_phase1(args: argparse.Namespace) -> int:
    from .pipeline.phase1 import run_phase1

    s = get_settings()
    overrides = {}
    if args.input:
        overrides["input_path"] = Path(args.input)
    if args.output:
        overrides["output_dir"] = Path(args.output)
    if args.provider:
        overrides["llm_provider"] = args.provider
    if args.max_pages is not None:
        overrides["max_pages"] = args.max_pages
    if args.no_cache:
        overrides["use_cache"] = False
    s = s.model_copy(update=overrides)

    print(f"-> provider requested={s.llm_provider}  resolved={s.resolved_provider()}")
    if s.llm_provider == "gemini" and not s.has_gemini_key():
        print("  WARNING: no GEMINI_API_KEY found -> running OFFLINE (geometry only). "
              "Add it to .env for full multimodal extraction.")

    result = run_phase1(
        settings=s,
        document_id=args.document_id,
        gold_dir=Path(args.gold_dir) if args.gold_dir else None,
    )
    _print_summary(result)
    return 0


def _print_summary(result: dict) -> None:
    doc = result["document"]
    rep = result["report"]
    summ = rep.get("summary", {})
    cov = rep.get("coverage", {})
    print("\n" + "=" * 60)
    print(f"  DOCUMENT   {doc.document_id}")
    print(f"  meta       {doc.meta.subject or '?'} | {doc.meta.grade or '?'} | "
          f"ch={doc.meta.chapter or '?'} | {doc.meta.publisher or '?'}")
    print(f"  provider   {doc.run.provider}  models={doc.run.models.get('vision')}")
    print(f"  pages      {summ.get('pages')}")
    print(f"  blocks     {summ.get('blocks')}   "
          f"(with text {summ.get('pct_blocks_with_text')}%, "
          f"traceable {summ.get('pct_blocks_fully_traceable')}%)")
    print(f"  assets     preserved {summ.get('pct_assets_preserved')}%")
    print(f"  confidence mean(self-reported) {summ.get('mean_extraction_confidence_self_reported')}  "
          f"[uncalibrated - see integrity signals]")
    print(f"  integrity  zero-block pages {summ.get('pages_with_zero_blocks')}  "
          f"min blocks/page {summ.get('min_blocks_per_page')}  "
          f"degenerate bboxes {summ.get('n_degenerate_bboxes')}")
    print(f"  graph      {len(result['graph'].nodes)} nodes / "
          f"{len(result['graph'].edges)} edges  "
          f"{result['graph'].edge_count_by_type()}")
    td = cov.get("type_distribution", {})
    if td:
        top = ", ".join(f"{k}:{v}" for k, v in list(td.items())[:10])
        print(f"  block types {top}")
    if summ.get("total_logical_questions") is not None:
        print(f"  questions  {summ.get('total_logical_questions')} logical  "
              f"src={summ.get('questions_by_source_type')}  "
              f"unknown={summ.get('questions_unknown_type')} dup={summ.get('questions_duplicates')} "
              f"missed={summ.get('questions_missed_containers')}")
        print(f"  q-gold     {summ.get('questions_gold_pass_rate_pct')}%  "
              f"prov {summ.get('questions_provenance_coverage_pct')}%")
    if summ.get("answers_matched") is not None:
        print(f"  answers    matched={summ.get('answers_matched')} "
              f"not_in_doc={summ.get('answers_not_in_document')} "
              f"partial={summ.get('answers_partial')} ambiguous={summ.get('answers_ambiguous')}  "
              f"ANSWERED_BY={summ.get('answered_by_edges')}")
        print(f"  a-guard    false_assoc={summ.get('answers_false_associations')} "
              f"unsupported={summ.get('answers_unsupported')}  "
              f"a-gold {summ.get('answers_gold_pass_rate_pct')}%")
    if summ.get("accepted") is not None:
        print(f"  validate   accepted={summ.get('accepted')} escalated={summ.get('escalated')} "
              f"abstained={summ.get('abstained')}  errors_caught={summ.get('errors_caught')} "
              f"recovered={summ.get('errors_recovered')} numeric_flags={summ.get('numeric_inconsistencies')}")
        c = summ.get("cost") or {}
        print(f"  cost       api_calls={c.get('api_calls')} cache_hit={c.get('cache_hit_rate_pct')}%")
    print("  outputs:")
    for name, path in result["outputs"].items():
        print(f"     - {name:20s} {path}")
    print("=" * 60)


def cmd_env(args: argparse.Namespace) -> int:
    s = get_settings()
    print("Environment / readiness")
    print(f"  LLM_PROVIDER (requested) : {s.llm_provider}")
    print(f"  provider (resolved)      : {s.resolved_provider()}")
    print(f"  GEMINI_API_KEY present   : {s.has_gemini_key()}")
    print(f"  models                   : {s.models_snapshot()}")
    print(f"  input_path               : {s.input_path}")
    print(f"  tesseract available      : {tesseract_available()}")
    for name in ("langgraph", "google.genai", "cv2", "fitz", "pydantic"):
        try:
            __import__(name)
            print(f"  import {name:16s}   : ok")
        except Exception as exc:
            print(f"  import {name:16s}   : MISSING ({exc.__class__.__name__})")
    return 0


def _force_utf8_stdout() -> None:
    # Windows consoles default to cp1252 and crash on λ/ν/→ etc. in extracted text.
    for stream in ("stdout", "stderr"):
        try:
            getattr(__import__("sys"), stream).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> None:
    _force_utf8_stdout()
    parser = argparse.ArgumentParser(prog="agp", description="AGP extraction (Phase 1)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("phase1", help="run ingestion→blocks→provenance→hierarchy")
    p1.add_argument("--input", help="folder of page images or a PDF")
    p1.add_argument("--output", help="output directory")
    p1.add_argument("--provider", choices=["gemini", "null", "anthropic", "openai"])
    p1.add_argument("--max-pages", type=int, default=None)
    p1.add_argument("--document-id", default=None)
    p1.add_argument("--gold-dir", default=None)
    p1.add_argument("--no-cache", action="store_true")
    p1.set_defaults(func=cmd_phase1)

    pe = sub.add_parser("env", help="print environment / readiness")
    pe.set_defaults(func=cmd_env)

    args = parser.parse_args()
    _setup_logging(getattr(args, "verbose", False))
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
