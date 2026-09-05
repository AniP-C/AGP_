"""Evaluation hooks — measure extraction quality from day one.

The brief demands an accuracy/validation posture, not a single blind LLM call.
Phase 1 ships *intrinsic* hooks (need no labels): coverage, provenance
completeness, confidence distribution, asset preservation, reading-order
integrity, hierarchy sanity. It also defines the *extrinsic* seam: drop a gold
file at ``eval/gold/<document_id>.json`` and the comparator scores block
detection precision/recall + type accuracy by bbox IoU.

Hooks are registered in a :class:`HookRegistry` so later phases add question /
answer hooks without touching the runner. The ``low_confidence_block_ids`` the
runner surfaces are exactly what the Phase-1→N confidence-gate will route on.
"""
from __future__ import annotations

import abc
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..schemas import ASSET_TYPES, CanonicalDocument, DocumentGraph

log = logging.getLogger(__name__)


@dataclass
class EvalContext:
    doc: CanonicalDocument
    graph: DocumentGraph
    settings: Settings
    gold_dir: Optional[Path] = None


class EvaluationHook(abc.ABC):
    name: str = "hook"

    @abc.abstractmethod
    def run(self, ctx: EvalContext) -> dict: ...


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


class CoverageHook(EvaluationHook):
    name = "coverage"

    def run(self, ctx: EvalContext) -> dict:
        b = ctx.doc.blocks
        n = len(b)
        by_type: dict[str, int] = {}
        for x in b:
            by_type[x.type.value] = by_type.get(x.type.value, 0) + 1
        return {
            "pages": len(ctx.doc.pages),
            "blocks": n,
            "blocks_per_page": round(n / max(1, len(ctx.doc.pages)), 1),
            "pct_blocks_with_text": _pct(sum(1 for x in b if (x.text or "").strip()), n),
            "pct_blocks_with_bbox": _pct(sum(1 for x in b if x.provenance.bbox), n),
            "type_distribution": dict(sorted(by_type.items(), key=lambda kv: -kv[1])),
        }


class ProvenanceHook(EvaluationHook):
    name = "provenance"

    def run(self, ctx: EvalContext) -> dict:
        b = ctx.doc.blocks
        n = len(b)
        complete = sum(1 for x in b if x.provenance.is_complete())
        missing_src_hash = sum(1 for x in b if not x.provenance.source_image_sha256)
        all_pages_hashed = all(p.source.sha256 for p in ctx.doc.pages)
        return {
            "pct_blocks_fully_traceable": _pct(complete, n),
            "blocks_missing_source_hash": missing_src_hash,
            "all_source_pages_hashed": all_pages_hashed,
            "has_run_provenance": bool(ctx.doc.run and ctx.doc.run.run_id),
            "content_sha256": ctx.doc.content_sha256,
        }


class ConfidenceHook(EvaluationHook):
    name = "confidence"

    def run(self, ctx: EvalContext) -> dict:
        b = ctx.doc.blocks
        n = len(b)
        bbox_conf = [x.provenance.bbox.confidence for x in b if x.provenance.bbox]
        ext_conf = [x.provenance.extraction_confidence for x in b]
        bt = ctx.settings.bbox_confidence_threshold
        et = ctx.settings.extraction_confidence_threshold
        low = sorted(
            (x for x in b if x.provenance.extraction_confidence < et),
            key=lambda x: x.provenance.extraction_confidence,
        )
        return {
            # IMPORTANT: these are the VISION MODEL'S OWN self-reported numbers,
            # not calibrated probabilities. They cluster at round values (0.75/
            # 0.85/0.9/0.95) with a ~0.70 floor, so "0 below threshold" mostly
            # reflects that floor, NOT extraction quality (a wrong extraction can
            # still be self-rated 0.95). Do NOT drive the Phase-5 retry/escalation
            # gate from this alone — combine with the deterministic signals in the
            # 'integrity' hook (parse/yield/bbox) + numeric checks. See DEFER notes.
            "confidence_source": "vision_model_self_reported_uncalibrated",
            "mean_bbox_confidence": round(sum(bbox_conf) / len(bbox_conf), 3) if bbox_conf else None,
            "mean_extraction_confidence": round(sum(ext_conf) / len(ext_conf), 3) if ext_conf else None,
            "reported_min_extraction_confidence": round(min(ext_conf), 3) if ext_conf else None,
            "pct_below_bbox_threshold": _pct(
                sum(1 for c in bbox_conf if c < bt), len(bbox_conf)),
            "pct_below_extraction_threshold": _pct(len(low), n),
            "low_confidence_block_ids": [x.id for x in low[:50]],
        }


class AssetHook(EvaluationHook):
    name = "assets"

    def run(self, ctx: EvalContext) -> dict:
        expected = [x for x in ctx.doc.blocks
                    if x.type in ASSET_TYPES and x.provenance.bbox is not None]
        saved = [x for x in expected if x.asset is not None]
        missing = [x.id for x in expected if x.asset is None]
        files_ok = sum(1 for x in saved if Path(x.asset.path).exists())
        return {
            "asset_blocks_expected": len(expected),
            "asset_crops_saved": len(saved),
            "asset_files_on_disk": files_ok,
            "pct_assets_preserved": _pct(len(saved), len(expected)) if expected else 100.0,
            "missing_asset_block_ids": missing[:50],
        }


class ReadingOrderHook(EvaluationHook):
    name = "reading_order"

    def run(self, ctx: EvalContext) -> dict:
        orders = sorted(x.provenance.reading_order for x in ctx.doc.blocks)
        n = len(orders)
        contiguous = orders == list(range(n))
        dupes = n - len(set(orders))
        chained = sum(1 for x in ctx.doc.blocks if x.next_id) + (1 if n else 0)
        return {
            "contiguous_0_to_n": contiguous,
            "duplicate_orders": dupes,
            "reading_chain_length": chained,
        }


class HierarchyHook(EvaluationHook):
    name = "hierarchy"

    def run(self, ctx: EvalContext) -> dict:
        def depth(node, d=0):
            return max([d] + [depth(c, d + 1) for c in node.children])
        sections = 0

        def count(node):
            nonlocal sections
            sections += 1
            for c in node.children:
                count(c)
        for r in ctx.doc.hierarchy:
            count(r)
        orphan = sum(1 for x in ctx.doc.blocks if not x.section_id)
        max_depth = max([depth(r) for r in ctx.doc.hierarchy], default=0)
        return {
            "hierarchy_nodes": sections,
            "max_depth": max_depth,
            "blocks_without_section": orphan,
        }


class IntegrityHook(EvaluationHook):
    """Deterministic extraction-failure signals — the ones self-reported
    confidence CANNOT see. A page that fails to parse yields ZERO blocks, not
    low-confidence blocks; this hook surfaces exactly that. These signals are the
    intended deterministic inputs to the Phase-5 confidence-gate/escalation."""

    name = "integrity"

    def run(self, ctx: EvalContext) -> dict:
        n_pages = len(ctx.doc.pages)
        per_page = {p.page_index: 0 for p in ctx.doc.pages}
        for x in ctx.doc.blocks:
            pi = x.provenance.page_index
            per_page[pi] = per_page.get(pi, 0) + 1
        counts = sorted(per_page.values())
        median = counts[len(counts) // 2] if counts else 0
        zero_pages = [pi for pi, c in per_page.items() if c == 0]
        bottom = sorted(per_page.items(), key=lambda kv: kv[1])[:3]

        # degenerate boxes: vanishingly small, or full-page on a non-visual block
        tiny, fullpage = [], []
        for x in ctx.doc.blocks:
            bb = x.provenance.bbox
            if not bb or not (bb.page_width and bb.page_height):
                continue
            frac = bb.area / float(bb.page_width * bb.page_height)
            if frac < 2e-4:
                tiny.append(x.id)
            elif frac > 0.95 and x.type not in ASSET_TYPES:
                fullpage.append(x.id)

        # ── Phase-5 content-integrity extensions ─────────────────────────
        node_ids = {n.id for n in ctx.graph.nodes}
        dangling = [f"{e.src}->{e.dst}" for e in ctx.graph.edges
                    if e.src not in node_ids or e.dst not in node_ids]
        ab_edges = [e for e in ctx.graph.edges if e.type.value == "answered_by"]
        dup_edges = len(ab_edges) - len({(e.src, e.dst) for e in ab_edges})
        prose_types = {"paragraph", "heading", "question", "answer", "solution",
                       "explanation", "list_item"}
        textless_prose = [b.id for b in ctx.doc.blocks
                          if b.type.value in prose_types and not (b.text or "").strip()]
        missing_assets = [b.id for b in ctx.doc.blocks
                          if b.type.value in ("figure", "diagram", "image", "table", "equation")
                          and b.provenance.bbox and b.asset is None]
        orphan = [b.id for b in ctx.doc.blocks if not b.section_id]
        unresolved_refs = sum(1 for b in ctx.doc.blocks for r in b.refs
                              if r.status != "resolved")

        return {
            "pages_with_zero_blocks": zero_pages,           # hard failure signal
            "n_pages_with_zero_blocks": len(zero_pages),
            "min_blocks_per_page": counts[0] if counts else 0,
            "median_blocks_per_page": median,
            "lowest_yield_pages": [{"page_index": pi, "blocks": c} for pi, c in bottom],
            "blocks_with_tiny_bbox": tiny[:50],
            "blocks_with_fullpage_bbox_nonasset": fullpage[:50],
            "dangling_graph_refs": len(dangling),
            "duplicate_answered_by_edges": dup_edges,
            "textless_prose_blocks": len(textless_prose),
            "missing_asset_crops": len(missing_assets),
            "orphan_blocks_no_section": len(orphan),
            "unresolved_references": unresolved_refs,
            "note": ("Zero-block pages / dangling refs / textless prose are failures "
                     "invisible to the self-reported confidence score."),
        }


class StructureHook(EvaluationHook):
    """Phase-2 structural metrics: reading-order confidence, grouping (part_of),
    OR groups, continuation, inline annotation, cross-ref resolution, plus the
    relationship-edge counts actually materialized in the graph."""

    name = "structure"

    def run(self, ctx: EvalContext) -> dict:
        p2 = (ctx.doc.stats or {}).get("phase2", {})
        edge_counts: dict[str, int] = {}
        for e in ctx.graph.edges:
            edge_counts[e.type.value] = edge_counts.get(e.type.value, 0) + 1
        return {
            "reading_order": p2.get("reading_order", {}),
            "grouping": p2.get("grouping", {}),
            "inline": p2.get("inline", {}),
            "continuation": p2.get("continuation", {}),
            "crossref": p2.get("crossref", {}),
            "relationship_edges": {
                k: edge_counts.get(k, 0)
                for k in ("part_of", "refers_to", "continues", "or_alternative")
            },
        }


class StructureGoldHook(EvaluationHook):
    """Sample-derived structural checks (eval/gold/<doc_id>_structure.json).
    Each check is a semantic assertion (found by text), robust to block-id churn."""

    name = "structure_gold"

    def run(self, ctx: EvalContext) -> dict:
        if not ctx.gold_dir:
            return {"status": "no_gold_dir"}
        path = Path(ctx.gold_dir) / f"{ctx.doc.document_id}_structure.json"
        if not path.exists():
            return {"status": "no_gold_file", "expected_at": str(path)}
        gold = json.loads(path.read_text(encoding="utf-8"))
        blocks = ctx.doc.blocks
        by_ro = sorted(blocks, key=lambda b: b.provenance.reading_order)

        def find(contains, page=None):
            for b in by_ro:
                if page is not None and b.provenance.page_index != page:
                    continue
                if contains.lower() in (b.text or "").lower():
                    return b
            return None

        results = []

        for c in gold.get("ordering", []):
            a = find(c["before_contains"], c.get("page_index"))
            ok = False
            if a and a.next_id:
                nxt = ctx.doc.block_by_id(a.next_id)
                ok = bool(nxt and nxt.type.value == c["after_type"])
            results.append({"check": c["desc"], "kind": "ordering", "pass": ok})

        for c in gold.get("part_of", []):
            cont = find(c["container_contains"])
            n = sum(1 for b in blocks if b.structure.part_of_id == cont.id) if cont else 0
            results.append({"check": c["desc"], "kind": "part_of",
                            "pass": n >= c["min_children"], "found_children": n})

        for c in gold.get("continuation", []):
            ok = any(
                b.type.value == c["type"]
                and b.provenance.page_index == c["from_page"]
                and b.structure.continues_to
                and (ctx.doc.block_by_id(b.structure.continues_to) or _N).provenance.page_index == c["to_page"]
                for b in blocks
            )
            results.append({"check": c["desc"], "kind": "continuation", "pass": ok})

        for c in gold.get("crossref", []):
            ok = False
            for b in blocks:
                for ref in b.refs:
                    if c["ref_contains"].lower() in ref.raw_text.lower():
                        tgt = ctx.doc.block_by_id(ref.resolved_block_id) if ref.resolved_block_id else None
                        ok = bool(ref.status == "resolved" and tgt
                                  and tgt.type.value in c.get("expect_target_type_in", [c.get("expect_target_type")])
                                  and (not c.get("expect_same_page")
                                       or tgt.provenance.page_index == b.provenance.page_index))
                        break
                if ok:
                    break
            results.append({"check": c["desc"], "kind": "crossref", "pass": ok})

        for c in gold.get("inline", []):
            b = find(c["block_contains"])
            ok = bool(b and b.structure.inline_answer == c["expect_inline_answer"])
            results.append({"check": c["desc"], "kind": "inline", "pass": ok})

        for c in gold.get("layout", []):
            pg = next((p for p in ctx.doc.pages if p.page_index == c["page_index"]), None)
            ok = bool(pg and pg.layout and pg.layout.n_columns == c["expect_columns"])
            results.append({"check": c["desc"], "kind": "layout", "pass": ok})

        passed = sum(1 for r in results if r["pass"])
        return {
            "status": "scored",
            "checks_total": len(results),
            "checks_passed": passed,
            "pass_rate_pct": _pct(passed, len(results)),
            "results": results,
        }


class _NBlock:
    class provenance:  # sentinel for a missing continuation target
        page_index = -1


_N = _NBlock()


def _walk_q(questions):
    for q in questions:
        yield q
        yield from _walk_q(q.sub_questions)


class QuestionHook(EvaluationHook):
    """Phase-3 discovery metrics + duplicate/missed checks + provenance coverage."""

    name = "questions"

    def run(self, ctx: EvalContext) -> dict:
        p3 = (ctx.doc.stats or {}).get("phase3", {})
        nodes = list(_walk_q(ctx.doc.questions))

        # duplicates: identical normalized question_text
        seen: dict[str, int] = {}
        for q in nodes:
            key = " ".join((q.question_text or "").lower().split())[:200]
            if key:
                seen[key] = seen.get(key, 0) + 1
        duplicates = sum(v - 1 for v in seen.values() if v > 1)

        # missed: every container / worked_example block should appear in some
        # question's source_blocks (i.e. no discoverable question was dropped)
        covered = {bid for q in nodes for bid in q.provenance.source_blocks}
        must = [b for b in ctx.doc.blocks
                if b.structure.group_role == "container"
                or b.type.value == "worked_example"]
        missed = [b.id for b in must if b.id not in covered]

        return {
            **{k: p3.get(k) for k in (
                "total_logical_questions", "root_questions", "with_sub_questions",
                "max_nesting_depth", "by_source_type", "by_question_type",
                "with_marks", "marks_by_source", "with_bloom", "with_source_refs",
                "with_inline_answer", "internal_choice_groups", "unknown_type",
                "provenance_coverage_pct")},
            "llm": p3.get("llm"),
            # what reconstruction actually did to the raw blocks — surfaced so
            # joins/splits are auditable rather than an invisible transform
            "reconstruction": p3.get("reconstruction"),
            "duplicate_question_texts": duplicates,
            "missed_container_blocks": len(missed),
            "missed_container_block_ids": missed[:20],
        }


class QuestionGoldHook(EvaluationHook):
    """Sample-derived question checks (eval/gold/<doc_id>_questions.json)."""

    name = "questions_gold"

    def run(self, ctx: EvalContext) -> dict:
        if not ctx.gold_dir:
            return {"status": "no_gold_dir"}
        path = Path(ctx.gold_dir) / f"{ctx.doc.document_id}_questions.json"
        if not path.exists():
            return {"status": "no_gold_file", "expected_at": str(path)}
        gold = json.loads(path.read_text(encoding="utf-8"))
        nodes = list(_walk_q(ctx.doc.questions))
        results = []

        def find(crit):
            for q in nodes:
                if "text_contains" in crit and crit["text_contains"].lower() not in (q.question_text or "").lower():
                    continue
                if "number" in crit and q.question_number != crit["number"]:
                    continue
                if "source_type" in crit and q.source_type != crit["source_type"]:
                    continue
                return q
            return None

        for c in gold.get("questions", []):
            q = find(c["find"])
            ok = q is not None
            exp = c.get("expect", {})
            if q:
                if "source_type" in exp:
                    ok = ok and q.source_type == exp["source_type"]
                if "question_type" in exp:
                    ok = ok and q.question_type == exp["question_type"]
                if "min_sub_questions" in exp:
                    ok = ok and len(q.sub_questions) >= exp["min_sub_questions"]
                if "inline_answer" in exp:
                    ok = ok and (q.answer_inline is not None) == exp["inline_answer"]
                if "marks" in exp:
                    ok = ok and q.marks is not None and q.marks.value == exp["marks"]
                if "bloom_level" in exp:
                    ok = ok and q.bloom_level == exp["bloom_level"]
            results.append({"check": c["desc"], "kind": "question", "pass": bool(ok)})

        for c in gold.get("exists", []):
            def match(q):
                if "question_type" in c and q.question_type != c["question_type"]:
                    return False
                if c.get("has_internal_choice") and not q.internal_choice:
                    return False
                if "marks_source" in c and not (q.marks and q.marks.source == c["marks_source"]):
                    return False
                return True
            cnt = sum(1 for q in nodes if match(q))
            results.append({"check": c["desc"], "kind": "exists",
                            "pass": cnt >= c.get("min", 1), "found": cnt})

        passed = sum(1 for r in results if r["pass"])
        return {"status": "scored", "checks_total": len(results),
                "checks_passed": passed, "pass_rate_pct": _pct(passed, len(results)),
                "results": results}


# Single source of truth: the evaluator must judge "in-scope" by exactly the
# same rule the associator applied. Keeping a second copy here meant adding a
# relationship in one place silently turned every use of it into a reported
# false association in the other.
from ..associate.phase4 import POSITIVE_RELS as _POSITIVE_RELS  # noqa: E402


class AnswerHook(EvaluationHook):
    """Phase-4 association metrics + false-association / unsupported-answer checks.

    A matched/partial answer MUST be justified by in-scope structural/reference
    evidence — any answer block outside that is a false association (the metric
    that matters most for the false-positive policy)."""

    name = "answers"

    def run(self, ctx: EvalContext) -> dict:
        p4 = (ctx.doc.stats or {}).get("phase4", {})
        leaves = [q for q in _walk_q(ctx.doc.questions) if not q.sub_questions]
        false_assoc, unsupported, positives = 0, 0, 0
        for q in leaves:
            a = q.answer_association
            if not a or a.status not in ("matched", "partial"):
                continue
            positives += 1
            if a.method == "inline":
                continue
            ok = {e.candidate_block_id for e in a.evidence
                  if e.scope == "in_scope" and e.relationship in _POSITIVE_RELS}
            for bid in a.answer_block_ids:
                if bid not in ok:
                    false_assoc += 1
            if not a.answer_preview:
                unsupported += 1
        answered_by = sum(1 for e in ctx.graph.edges if e.type.value == "answered_by")
        return {
            **{k: p4.get(k) for k in (
                "answerable_leaf_questions", "by_status", "by_answer_state", "by_method",
                "matched", "not_in_document", "partial", "ambiguous",
                "answer_kind_distribution", "evidence_source_distribution",
                "llm_adjudicated_questions", "semantic_channel_enabled")},
            "answered_by_edges": answered_by,
            "positive_associations": positives,
            "false_associations": false_assoc,          # must be 0
            "unsupported_or_generated_answers": unsupported,   # must be 0
        }


class AnswerGoldHook(EvaluationHook):
    """Sample-derived QA checks (eval/gold/<doc_id>_answers.json)."""

    name = "answers_gold"

    def run(self, ctx: EvalContext) -> dict:
        if not ctx.gold_dir:
            return {"status": "no_gold_dir"}
        path = Path(ctx.gold_dir) / f"{ctx.doc.document_id}_answers.json"
        if not path.exists():
            return {"status": "no_gold_file", "expected_at": str(path)}
        gold = json.loads(path.read_text(encoding="utf-8"))
        nodes = list(_walk_q(ctx.doc.questions))
        results = []

        def find(crit):
            for q in nodes:
                if "text_contains" in crit and crit["text_contains"].lower() not in (q.question_text or "").lower():
                    continue
                if "number" in crit and q.question_number != crit["number"]:
                    continue
                if "source_type" in crit and q.source_type != crit["source_type"]:
                    continue
                return q
            return None

        for c in gold.get("answers", []):
            q = find(c["find"])
            a = q.answer_association if q else None
            ok = a is not None
            exp = c.get("expect", {})
            if a:
                if "status" in exp:
                    ok = ok and a.status == exp["status"]
                if "method" in exp:
                    ok = ok and a.method == exp["method"]
                if "answer_kind" in exp:
                    ok = ok and a.answer_kind == exp["answer_kind"]
            results.append({"check": c["desc"], "kind": "answer", "pass": bool(ok)})

        for c in gold.get("counts", []):
            if c.get("no_false_associations"):
                ans = ctx.doc.stats.get("phase4", {})
                # recomputed by AnswerHook; here just assert via a fresh count
                leaves = [q for q in nodes if not q.sub_questions]
                bad = 0
                for q in leaves:
                    a = q.answer_association
                    if a and a.status in ("matched", "partial") and a.method != "inline":
                        ok_ids = {e.candidate_block_id for e in a.evidence
                                  if e.scope == "in_scope" and e.relationship in _POSITIVE_RELS}
                        bad += sum(1 for b in a.answer_block_ids if b not in ok_ids)
                results.append({"check": c["desc"], "kind": "count", "pass": bad == 0, "found": bad})
            else:
                cnt = sum(1 for q in nodes if not q.sub_questions
                          and q.answer_association
                          and q.source_type == c.get("source_type")
                          and q.answer_association.status == c.get("status"))
                results.append({"check": c["desc"], "kind": "count",
                                "pass": cnt >= c.get("min", 1), "found": cnt})

        passed = sum(1 for r in results if r["pass"])
        return {"status": "scored", "checks_total": len(results),
                "checks_passed": passed, "pass_rate_pct": _pct(passed, len(results)),
                "results": results}


class ValidationHook(EvaluationHook):
    """Phase-5 reliability: calibration/gate tiers, accuracy-vs-coverage tradeoff,
    errors caught/recovered, abstentions, retry/escalation, and the cost report."""

    name = "validation"

    def run(self, ctx: EvalContext) -> dict:
        p5 = (ctx.doc.stats or {}).get("phase5")
        if not p5:
            return {"status": "phase5_not_run"}
        leaves = [q for q in _walk_q(ctx.doc.questions) if not q.sub_questions]
        # distinct calibrated-confidence bands (proof it is not a single %)
        cal = [q.validation.calibrated_confidence for q in leaves if q.validation]
        bands = {"high>=0.85": sum(1 for c in cal if c >= 0.85),
                 "medium0.6-0.85": sum(1 for c in cal if 0.6 <= c < 0.85),
                 "low<0.6": sum(1 for c in cal if c < 0.6)}
        # confidences kept separate
        sample = next((q.validation for q in leaves
                       if q.validation and q.validation.model_self_reported is not None), None)
        return {
            "gate_tiers": p5.get("gate_tiers"),
            "gate_actions": p5.get("gate_actions"),
            "accuracy_vs_coverage": {
                "high_confidence_accepted": p5.get("high_confidence_accepted"),
                "escalated": p5.get("escalated"),
                "abstained": p5.get("abstained")},
            "calibrated_confidence_bands": bands,
            "validation_errors_caught": p5.get("validation_errors_caught"),
            "numeric_inconsistencies": p5.get("numeric_inconsistencies"),
            "errors_recovered": p5.get("errors_recovered"),
            "retry_rate_pct": p5.get("retry_rate_pct"),
            "escalation": p5.get("escalation"),
            "final_status": p5.get("final_status"),
            "confidence_signals_separate": bool(sample),
            "cost": p5.get("cost"),
            "calibration_note": p5.get("calibration_note"),
        }


class ExtrinsicGoldHook(EvaluationHook):
    """Real comparator, inert until a gold file exists. Matches predicted asset/
    text blocks to gold boxes by IoU and reports detection P/R + type accuracy."""

    name = "extrinsic_gold"

    def run(self, ctx: EvalContext) -> dict:
        if not ctx.gold_dir:
            return {"status": "no_gold_dir"}
        gold_path = Path(ctx.gold_dir) / f"{ctx.doc.document_id}.json"
        if not gold_path.exists():
            return {"status": "no_gold_file", "expected_at": str(gold_path)}
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        gold_boxes = gold.get("blocks", [])
        preds = [b for b in ctx.doc.blocks if b.provenance.bbox]
        matched, type_ok = 0, 0
        used: set[int] = set()
        for g in gold_boxes:
            gp = g.get("page_index")
            gb = g.get("bbox")
            best_i, best_iou = -1, 0.0
            for i, p in enumerate(preds):
                if i in used or p.provenance.page_index != gp:
                    continue
                iou = _iou(gb, p.provenance.bbox.as_pixel_tuple())
                if iou > best_iou:
                    best_iou, best_i = iou, i
            if best_iou >= 0.5 and best_i >= 0:
                used.add(best_i)
                matched += 1
                if preds[best_i].type.value == g.get("type"):
                    type_ok += 1
        precision = _pct(matched, len(preds))
        recall = _pct(matched, len(gold_boxes))
        return {
            "status": "scored",
            "gold_blocks": len(gold_boxes),
            "predicted_blocks": len(preds),
            "matched_iou>=0.5": matched,
            "detection_precision_pct": precision,
            "detection_recall_pct": recall,
            "type_accuracy_pct": _pct(type_ok, matched) if matched else 0.0,
        }


def _iou(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / union if union > 0 else 0.0


class HookRegistry:
    def __init__(self, hooks: Optional[list[EvaluationHook]] = None):
        self.hooks = hooks or DEFAULT_HOOKS()

    def register(self, hook: EvaluationHook) -> None:
        self.hooks.append(hook)

    def run_all(self, ctx: EvalContext) -> dict:
        report: dict = {"document_id": ctx.doc.document_id}
        for hook in self.hooks:
            try:
                report[hook.name] = hook.run(ctx)
            except Exception as exc:  # a bad hook must not crash the run
                log.warning("eval hook '%s' failed: %s", hook.name, exc)
                report[hook.name] = {"error": str(exc)}
        report["summary"] = _summary(report)
        return report


class QuestionRecallHook(EvaluationHook):
    """Recall AND completeness against a full gold question set (Phase 6).

    Separate from :class:`QuestionGoldHook`, which asserts a handful of
    sample-derived properties. This one answers the two questions that actually
    matter: did we find every question, and did we capture each one *whole*.
    """

    name = "question_recall"

    def run(self, ctx: EvalContext) -> dict:
        if not ctx.gold_dir:
            return {"status": "no_gold_dir"}
        from .question_gold import evaluate_questions
        path = Path(ctx.gold_dir) / f"{ctx.doc.document_id}_gold_questions.json"
        return evaluate_questions(ctx.doc.questions, path)


def DEFAULT_HOOKS() -> list[EvaluationHook]:
    return [CoverageHook(), ProvenanceHook(), ConfidenceHook(), AssetHook(),
            ReadingOrderHook(), HierarchyHook(), IntegrityHook(),
            StructureHook(), StructureGoldHook(),
            QuestionHook(), QuestionGoldHook(), QuestionRecallHook(),
            AnswerHook(), AnswerGoldHook(), ValidationHook(), ExtrinsicGoldHook()]


def _summary(report: dict) -> dict:
    cov = report.get("coverage", {})
    prov = report.get("provenance", {})
    conf = report.get("confidence", {})
    assets = report.get("assets", {})
    integ = report.get("integrity", {})
    struct = report.get("structure", {})
    sgold = report.get("structure_gold", {})
    out = {
        "pages": cov.get("pages"),
        "blocks": cov.get("blocks"),
        "pct_blocks_with_text": cov.get("pct_blocks_with_text"),
        "pct_blocks_fully_traceable": prov.get("pct_blocks_fully_traceable"),
        "mean_extraction_confidence_self_reported": conf.get("mean_extraction_confidence"),
        "pct_assets_preserved": assets.get("pct_assets_preserved"),
        # integrity signals (deterministic — the real health check)
        "pages_with_zero_blocks": integ.get("n_pages_with_zero_blocks"),
        "min_blocks_per_page": integ.get("min_blocks_per_page"),
        "n_degenerate_bboxes": len(integ.get("blocks_with_tiny_bbox", []))
                               + len(integ.get("blocks_with_fullpage_bbox_nonasset", [])),
    }
    if struct:
        out["mean_reading_order_confidence"] = struct.get("reading_order", {}).get("mean_reading_order_confidence")
        out["part_of_edges"] = struct.get("relationship_edges", {}).get("part_of")
        out["refers_to_edges"] = struct.get("relationship_edges", {}).get("refers_to")
        out["continuation_links"] = struct.get("continuation", {}).get("continuation_links")
        out["crossref_resolution_pct"] = struct.get("crossref", {}).get("resolution_rate_pct")
    if sgold.get("status") == "scored":
        out["structure_gold_pass_rate_pct"] = sgold.get("pass_rate_pct")
    q = report.get("questions", {})
    qgold = report.get("questions_gold", {})
    if q:
        out["total_logical_questions"] = q.get("total_logical_questions")
        out["questions_by_source_type"] = q.get("by_source_type")
        out["questions_unknown_type"] = q.get("unknown_type")
        out["questions_duplicates"] = q.get("duplicate_question_texts")
        out["questions_missed_containers"] = q.get("missed_container_blocks")
        out["questions_provenance_coverage_pct"] = q.get("provenance_coverage_pct")
    if qgold.get("status") == "scored":
        out["questions_gold_pass_rate_pct"] = qgold.get("pass_rate_pct")
    ans = report.get("answers", {})
    agold = report.get("answers_gold", {})
    if ans:
        out["answers_matched"] = ans.get("matched")
        out["answers_not_in_document"] = ans.get("not_in_document")
        out["answers_partial"] = ans.get("partial")
        out["answers_ambiguous"] = ans.get("ambiguous")
        out["answered_by_edges"] = ans.get("answered_by_edges")
        out["answers_false_associations"] = ans.get("false_associations")
        out["answers_unsupported"] = ans.get("unsupported_or_generated_answers")
    if agold.get("status") == "scored":
        out["answers_gold_pass_rate_pct"] = agold.get("pass_rate_pct")
    val = report.get("validation", {})
    if val and val.get("status") != "phase5_not_run":
        avc = val.get("accuracy_vs_coverage", {})
        out["accepted"] = avc.get("high_confidence_accepted")
        out["escalated"] = avc.get("escalated")
        out["abstained"] = avc.get("abstained")
        out["errors_caught"] = val.get("validation_errors_caught")
        out["errors_recovered"] = val.get("errors_recovered")
        out["numeric_inconsistencies"] = val.get("numeric_inconsistencies")
        out["retry_rate_pct"] = val.get("retry_rate_pct")
        out["cost"] = val.get("cost")
    return out


def run_evaluation(doc: CanonicalDocument, graph: DocumentGraph,
                   settings: Settings, gold_dir: Optional[Path] = None) -> dict:
    ctx = EvalContext(doc=doc, graph=graph, settings=settings, gold_dir=gold_dir)
    return HookRegistry().run_all(ctx)
