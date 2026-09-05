# AGP — Multimodal Q&A Extraction (POC)

Document-agnostic extraction of questions **and** answers from educational
chapters, preserving text, tables, images, diagrams, equations, structure, and
**provenance**. Built provider-agnostic (Gemini → Claude → OpenAI → local is a
config change), orchestrated with LangGraph, strict Pydantic schemas throughout.

> **Status: Phases 1–5 complete.** Ingestion → preprocessing → multimodal block
> extraction (Gemini vision) → provenance/assets → hierarchy → structure &
> relationships → question discovery/normalization → hybrid retrieval + answer
> association (`ANSWERED_BY`) → validation, calibrated confidence, gate &
> bounded escalation. Design docs: [PHASE0_ANALYSIS](docs/PHASE0_ANALYSIS.md),
> [PHASE2](docs/PHASE2_DESIGN.md) · [PHASE3](docs/PHASE3_DESIGN.md) ·
> [PHASE4](docs/PHASE4_DESIGN.md) · [PHASE5 results](docs/PHASE5_RESULTS.md).

## Full pipeline (LangGraph)

```
ingest → preprocess → extract → assemble → structure → build_graph
  → build_evidence → discover_questions → associate_answers → validate → evaluate → persist
```

Outputs (`outputs/cl9ch11/`): `canonical_document.json` (blocks + structure +
`questions[]` with `answer_association` + `validation`), `graph.json`/`graph.sqlite`
(incl. `ANSWERED_BY`), `questions.json`, `evidence.json`, `eval_report.json`,
`confidence_report.json`, `assets/`.

## Reliability (Phase 5)

The system approaches high accuracy through **provenance + structural evidence +
abstention + targeted reprocessing**, not by trusting a model to be perfect:

- **Three separate confidences** — model self-reported, deterministic system-evidence,
  and a calibrated combination (structural backbone; the LLM only moderates). Never a
  single opaque average.
- **Confidence gate** — high → accept, medium → escalate (targeted reprocess), low →
  abstain. Integrity failures (zero-block pages, dangling refs, bad geometry, missing
  provenance) force escalation regardless of confidence.
- **False-positive policy** — a positive `ANSWERED_BY` requires in-scope structural
  evidence; semantic similarity alone never creates an edge. Measured false
  associations: **0**. QR-only self-assessment answers → `not_in_document`, never
  hallucinated.
- **Independent numeric validation (sympy)** — flags printed arithmetic errors
  (e.g. `344 × 0.1 = 344.4`) and **preserves the source**, never replacing it.
- **Bounded escalation** — re-adjudicate/re-extract only the affected question/block
  with a stronger model; retry count, previous/new result, reason, and decision are
  all recorded. Recoveries (status improved) are counted separately from confirmations.

Regression: `python tests/test_acceptance.py` checks the 10 required cases + invariants.

## Quick start

```bash
pip install -r requirements.txt      # core + google-genai (Gemini)
```

Run **offline** (no key needed — proves the whole pipeline; geometry-only, no text):

```bash
python run.py phase1 --provider null --max-pages 3
```

Run with **Gemini** (full multimodal extraction). Put your key in `.env`:

```bash
cp .env.example .env      # then set GEMINI_API_KEY=...
python run.py phase1 --input cl9ch11
```

Check readiness anytime:

```bash
python run.py env
```

## Frontend (Streamlit)

A recruiter-facing web UI (`app.py`) sits on top of the pipeline above as a pure
presentation/integration layer — it does not reimplement or duplicate any of the
5-phase logic, only invokes `agp_extract.pipeline.phase1` and renders its output
objects/files.

```bash
pip install -r requirements.txt   # includes streamlit
streamlit run app.py
```

- **Demo Mode** — sidebar → "Load AGP Demo Sample" loads the already-processed
  `outputs/cl9ch11/` artifacts directly from disk. No API key, no pipeline call.
- **Upload Mode** — upload a PDF / JPG / JPEG / PNG / WEBP / TXT, or a **ZIP**
  archive (of images, a PDF, or a mix — unsupported files inside are ignored with
  a visible reason, not silently dropped). ZIP extraction is sandboxed to a
  temp directory with path-traversal, oversized-archive, zip-bomb, and
  executable-content guards (`agp_extract/ingest/loader.py`); clicking
  **Process Document** then runs the real pipeline with live per-stage progress
  (driven by the same LangGraph nodes the CLI runs, not a fake progress bar).
- **Views**: Overview (dashboard, metrics read from `eval_report.json`),
  Questions (filterable table + detail), Document Explorer (page image with
  block bounding boxes overlaid), Evidence & Answers (per-signal evidence, and
  a dedicated "not in document" view proving no hallucinated answers),
  Validation (confidence gate, numeric-inconsistency catches, escalations).

**Configuring Gemini for Upload Mode**: copy `.env.example` to `.env` and set
`GEMINI_API_KEY` — the key is read server-side via `agp_extract.config.get_settings()`
and is never displayed in the UI or written to any output file. Demo Mode needs no
key at all. Do not commit `.env`.

Deploying: any host that runs `streamlit run app.py` works (Streamlit Community
Cloud, a container, or a plain VM) — set `GEMINI_API_KEY` as a platform secret/env
var rather than committing `.env`. `.streamlit/config.toml` sets the theme and
upload size limit; no other infra is required.

## What Phase 1 produces

For input `cl9ch11/`, everything lands in `outputs/cl9ch11/`:

| Artifact | Contents |
|---|---|
| `canonical_document.json` | Typed content blocks + full provenance + pages + reconstructed **chapter→topic→section** hierarchy + document/run provenance |
| `graph.json` / `graph.sqlite` | Knowledge graph (nodes = blocks + document/page/section; typed edges) — JSON **and** SQLite, no graph DB |
| `evidence.json` | One normalized evidence unit per block (the seam Phase 4 retrieves from) |
| `eval_report.json` | Intrinsic quality metrics from the evaluation hooks |
| `assets/*.png` | Cropped **original pixels** for every figure/diagram/table/equation block |
| `preprocessed/*.png` | Deterministic OpenCV-preprocessed page images |

## Architecture (Phase 1)

```
run.py → cli → pipeline/phase1 (LangGraph)
  START → ingest → preprocess → extract → assemble → build_graph
        → build_evidence → evaluate → persist → END
```

- **`providers/`** — LLM-agnostic layer. `base.py` defines the contract
  (`extract_page` + `vision`/`generate`/`generate_structured`/`embed`); adapters:
  `gemini.py` (default), `null.py` (offline OpenCV fallback), `anthropic.py` /
  `openai.py` (stubs). Chosen by config in `factory.py`. **Prompts live in
  `prompts/`, not in code.**
- **`schemas/`** — strict Pydantic contracts: confidence-aware `BBox`,
  three-level provenance, `ContentBlock`, graph, evidence, document.
- **`ingest/`** — folder-of-images **or** PDF → hashed pages; OpenCV preprocessing.
- **`ocr/`** — optional Tesseract accelerator (degrades gracefully if absent).
- **`extract/`** — provider output → provenance-rich blocks; optional publisher
  heuristics (confidence boosters only); asset cropping.
- **`assemble/`** — reading order, hierarchy, metadata inference.
- **`graph/`** — build + persist the knowledge graph (JSON/SQLite).
- **`evidence/`** — evidence units + structural retriever (RAG seam).
- **`eval/`** — evaluation hooks (intrinsic now; extrinsic gold seam ready).
- **`cache/`** — page-hash keyed cache so vision calls aren't repeated.

### Provider / model independence

`.env` controls everything; nothing is hard-coded:

```
LLM_PROVIDER=gemini            # gemini | null | anthropic | openai
VISION_MODEL=gemini-2.0-flash  # the three roles are independent
REASONING_MODEL=gemini-2.0-flash
EMBEDDING_MODEL=text-embedding-004
```

## The five architecture changes (where each lives)

| Change | Where |
|---|---|
| **Evaluation hooks** | `eval/hooks.py` — `HookRegistry` + intrinsic hooks (coverage, provenance, confidence, assets, reading-order, hierarchy) + extrinsic gold comparator seam; run as a pipeline node, written to `eval_report.json` |
| **Stronger document-level provenance** | `schemas/provenance.py` — `SourceFileProvenance` (per-page SHA-256 + dims), `RunProvenance` (tool/prompt versions, provider, model roles, library versions, host), plus `content_sha256` over the whole document |
| **Confidence-aware bounding boxes** | `schemas/geometry.py` — `BBox.confidence` (localization) tracked separately from block `extraction_confidence`; both surfaced by the eval hooks |
| **Graph as JSON/SQLite (no graph DB)** | `graph/store.py` — plain JSON + SQLite tables/indexes; `graph/builder.py` emits typed edges |
| **Evidence layer for later RAG** | `schemas/evidence.py` + `evidence/layer.py` — `EvidenceUnit` + `EvidenceRetriever` contract; deterministic `structural` channel implemented, `semantic`/`lexical` reserved for Phase 4 |

## Explicitly deferred (not in Phase 1)

Question discovery/classification, hybrid RAG retrieval, answer association,
numeric/equation validation, and the confidence-gate retry loop. The block
taxonomy already *types* questions/answers/examples structurally; turning those
into classified Question records with associated answers is Phase 3–4.

## Tests

```bash
python tests/test_smoke.py                # offline pipeline invariants (no key)
python tests/test_acceptance.py            # regression over persisted cl9ch11 outputs
python tests/test_frontend_regression.py   # frontend ingestion adapter + runner (offline, 14 cases)
```
