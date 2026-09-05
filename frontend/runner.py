"""Invokes the existing 5-phase backend and reports *real* per-node progress.

`run_phase1` (agp_extract.pipeline.phase1) only exposes `.invoke()` — a single
blocking call. Its own `build_phase1_graph()` is a plain LangGraph graph, so
we drive it here with `.stream()` instead to surface genuine node-by-node
completion to the UI, then hand back the same {"document","graph","report",
"outputs"} shape `run_phase1` would have returned. No pipeline logic is
duplicated — only the thin invocation wrapper.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

from agp_extract import PROMPT_VERSION, __version__, metrics
from agp_extract.config import Settings
from agp_extract.pipeline.phase1 import build_phase1_graph
from agp_extract.providers.factory import get_provider
from agp_extract.schemas import RunProvenance

# Node id -> label shown in the UI stepper, in pipeline order.
STAGE_LABELS: dict[str, str] = {
    "ingest": "Ingest",
    "preprocess": "Preprocess",
    "extract": "Extract",
    "verify_coverage": "Verify Coverage",
    "assemble": "Assemble",
    "structure": "Build Structure",
    "build_graph": "Build Graph",
    "build_evidence": "Retrieve Evidence",
    "discover_questions": "Discover Questions",
    "associate_answers": "Associate Answers",
    "validate": "Validate",
    "evaluate": "Evaluate",
    "persist": "Persist",
}


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
        library_versions={},
        environment=settings.env_snapshot(),
    )


def run_with_progress(
    settings: Settings,
    document_id: str,
    gold_dir: Optional[Path] = None,
    on_stage: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """Runs the full pipeline, calling `on_stage(node_id, "running"|"done")`
    as each LangGraph node starts/finishes. Returns the same dict shape as
    `agp_extract.pipeline.phase1.run_phase1`.
    """
    metrics.reset()
    provider = get_provider(settings)
    out_dir = Path(settings.output_dir) / document_id

    initial = {
        "settings": settings,
        "provider": provider,
        "run": _run_provenance(settings, provider.name),
        "document_id": document_id,
        "out_dir": out_dir,
        "assets_dir": out_dir / "assets",
        "preprocess_dir": out_dir / "preprocessed",
        "gold_dir": gold_dir or (Path("eval") / "gold"),
    }
    assets_dir = out_dir / "assets"
    if assets_dir.exists():
        for f in assets_dir.glob("*.png"):
            f.unlink()
    assets_dir.mkdir(parents=True, exist_ok=True)

    app = build_phase1_graph()
    final_state: dict = {}
    for update in app.stream(initial, stream_mode="updates"):
        for node_id, partial in update.items():
            if on_stage:
                on_stage(node_id, "done")
            final_state.update(partial)

    return {
        "document": final_state["document"],
        "graph": final_state["graph"],
        "report": final_state["report"],
        "outputs": final_state["outputs"],
    }
