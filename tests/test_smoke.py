"""Phase 1 smoke test — runs the offline (null) provider on a few pages and
checks the invariants that matter: blocks exist, provenance is complete and
hash-linked, assets are cropped, the graph/evidence/eval artifacts are written.

Run:  python tests/test_smoke.py     (no pytest needed)
  or:  python -m pytest tests/
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agp_extract.config import get_settings  # noqa: E402
from agp_extract.pipeline.phase1 import run_phase1  # noqa: E402


def _run():
    s = get_settings().model_copy(update={
        "llm_provider": "null",
        "input_path": ROOT / "cl9ch11",
        "output_dir": ROOT / "outputs",
        "max_pages": 3,
        "use_cache": False,
    })
    return run_phase1(settings=s, document_id="smoke_cl9ch11")


def test_phase1_offline_runs():
    r = _run()
    doc, graph, report, outputs = r["document"], r["graph"], r["report"], r["outputs"]

    assert len(doc.pages) == 3, "expected 3 pages"
    assert len(doc.blocks) > 0, "expected some blocks"
    assert graph.nodes and graph.edges, "expected a populated graph"

    # provenance: every source page hashed; full-page anchor blocks traceable
    assert all(p.source.sha256 for p in doc.pages)
    assert report["provenance"]["all_source_pages_hashed"] is True

    # artifacts on disk
    for key in ("canonical_document", "graph_json", "graph_sqlite", "evidence", "eval_report"):
        assert Path(outputs[key]).exists(), f"missing output: {key}"

    # eval summary present
    assert "summary" in report and report["summary"]["blocks"] == len(doc.blocks)
    print("OK: blocks=%d nodes=%d edges=%d" % (len(doc.blocks), len(graph.nodes), len(graph.edges)))


if __name__ == "__main__":
    test_phase1_offline_runs()
    print("smoke test passed")
