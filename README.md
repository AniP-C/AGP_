# AGP — Question & Answer Extraction from Educational Chapters

Document-agnostic extraction of **questions and their answers** from textbook
chapters — scanned or born-digital, any publisher — preserving text, tables,
images, diagrams, equations, structure and full **provenance**. Not a chatbot:
a multimodal document-understanding pipeline that reconstructs complete
logical questions (with sub-parts, marks, linked figures) and finds their
answers only where the source actually contains them.

📊 **[Results dossier](docs/site/index.html)** — measured recall/completeness against
hand-transcribed gold, with the bugs that measurement caught.
📄 Sample output: [`cl9ch11` Q&A PDF](outputs/cl9ch11/questions_answers.pdf) ·
[`leph202` Q&A PDF](outputs/leph202/questions_answers.pdf)

> **Status.** Phases 1–5 (ingestion → structure → question discovery → answer
> association → validation) plus Phase 6 (parser abstraction, span-model
> question reconstruction, objective coverage checks, gold evaluation) are
> built and measured on two chapters. Design docs:
> [PHASE0](docs/PHASE0_ANALYSIS.md) · [PHASE2](docs/PHASE2_DESIGN.md) ·
> [PHASE3](docs/PHASE3_DESIGN.md) · [PHASE4](docs/PHASE4_DESIGN.md) ·
> [PHASE5](docs/PHASE5_RESULTS.md) · [PHASE6 audit](docs/PHASE6_AUDIT.md) ·
> [PHASE6 design](docs/PHASE6_DESIGN.md).

## See it work — a worked example

`leph202.pdf` (NCERT Physics XII, Wave Optics) is a born-digital PDF. Its
**Example 10.1** originally arrived as a bare heading with no content attached —
the vision-style reader had no way to tell "Example 10.1" the *label* from
"Example 10.1" the *question*, so it was dropped as noise.

**Before** — nothing:
```
Example 10.1
```

**After reconstruction** — the label, its three sub-parts (recovered from a run
that mixed inline text and separate blocks), and each part's answer, matched to
the exact blocks that justify it:

```
Example 10.1
├─ (a) When monochromatic light is incident on a surface separating two
│      media, the reflected and refracted light both have the same
│      frequency as the incident frequency. Explain why?
│      → matched: "(a) Reflection and refraction arise through
│                   interaction of incident light with the atomic
│                   constituents of matter..."
├─ (b) When light travels from a rarer to a denser medium, the speed
│      decreases. Does the reduction in speed imply a reduction in the
│      energy carried by the light wave?
│      → matched: "(b) No. Energy carried by a wave depends on the
│                   amplitude of the wave, not on the speed..."
└─ (c) In the wave picture of light, intensity is determined by the
       square of the amplitude of the wave. What determines intensity
       in the photon picture?
       → matched: "(c) For a given frequency, intensity of light in
                    the photon picture is determined by the number of
                    photons crossing a unit area per unit time."
```

Every line above is real output from `outputs/leph202/questions.json` — not
illustrative. See the [results dossier](docs/site/index.html) for four more
defects a hand-built gold set caught (a case-study passage silently cut to
nine words, a failed page cached as if it were valid, a perfect text layer
re-corrupted by unnecessary OCR) and how they were fixed.

## Measured results

Two chapters, two different reading problems — handled by the same pipeline
choosing its reader per page:

| | `cl9ch11` (Educart, Science IX) | `leph202` (NCERT, Physics XII) |
|---|---|---|
| Input | 30 scanned JPGs, no text layer | Born-digital PDF, 42.6k chars |
| Reader | Vision model | Text layer + layout parser (Docling) |
| Blocks | 562 | 298 |
| Logical questions | 142 | 18 |
| Answers matched / not in document | 84 / 31 | 3 / 10 |
| False associations | **0** | **0** |
| API calls | 21 | **3** (extraction is free — it reads the text layer) |

On the **7-page window measured against a hand-transcribed gold set** (20
top-level questions, 22 sub-questions — never derived from the pipeline's own
output, or the measurement would be circular): **100% question recall, 100%
completeness, 100% marks accuracy, 100% provenance accuracy, zero false
positives** on pages deliberately salted with numbered definitions and a
question-shaped heading. Sub-question and question-type accuracy are lower and
reported honestly — see the dossier. **23 of 30 pages remain unmeasured; no
document-wide accuracy figure is claimed.**

## Full pipeline (LangGraph)

```
ingest → preprocess → extract → verify_coverage → assemble → structure
  → build_graph → build_evidence → discover_questions → associate_answers
  → validate → evaluate → persist
```

- **`extract`** routes each page to a parser by whether it has a real text
  layer — a scan goes to a vision model, a born-digital PDF is read from its
  text layer with **zero** re-OCR (`parse/router.py`, `parse/docling_parser.py`).
- **`verify_coverage`** measures how much of the page's actual ink falls
  inside an extracted block, independently of what the model self-reports —
  a page can claim 96% confidence while being 16% empty; this catches that
  (`validate/coverage.py`).
- **`discover_questions`** reconstructs each logical question as a *span* —
  `(start_block, offset) → (end_block, offset)` — so joining a question spread
  across blocks/columns/pages and splitting an over-bundled block into its
  sub-parts are the same operation: placing a boundary
  (`reconstruct/reconstructor.py`).
- **`associate_answers`** is structural-first and false-positive-averse: a
  positive match requires in-scope structural evidence; the model adjudicates
  between candidates and never writes an answer. Absent answers are reported
  as `not_in_document`, never invented.

Outputs land in `outputs/<document_id>/`: `canonical_document.json` (blocks +
structure + `questions[]` with `answer_association` + `validation`),
`graph.json` / `graph.sqlite` (incl. `ANSWERED_BY` edges), `questions.json`,
`evidence.json`, `eval_report.json`, `confidence_report.json`,
`questions_answers.pdf` (human-reviewable export, page numbers on every
question), `assets/`.

## Reliability

- **Three separate confidences** — model self-reported, deterministic
  system-evidence (ink coverage, numbering continuity), and a calibrated
  combination. Never collapsed into one opaque average.
- **Confidence gate** — high → accept, medium → escalate (targeted
  reprocessing of just the affected page/question), low → abstain. A
  zero-block or low-coverage page forces escalation regardless of reported
  confidence.
- **Cache correctness** — a failed or truncated page read is never cached as
  valid data; it goes to a separate failure record with a reason, so a retry
  is cheap and the bad result can never be silently served again
  (`scripts/purge_invalid_cache.py` migrates old poisoned entries).
- **False-positive policy** — a positive `ANSWERED_BY` requires in-scope
  structural evidence; semantic similarity alone never creates an edge.
  Measured false associations across both chapters: **0**.
- **Independent numeric validation (sympy)** — flags printed arithmetic
  errors and preserves the source, never silently replacing it.
- **Bounded escalation** — re-adjudicate/re-extract only the affected
  region with a stronger model; retry count, previous/new result, and reason
  are all recorded.

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

Run a born-digital PDF (routes to Docling, no API calls for extraction):

```bash
python run.py phase1 --input leph202.pdf --document-id leph202
```

Check readiness anytime:

```bash
python run.py env
```

## Frontend (Streamlit)

A recruiter-facing web UI (`app.py`) sits on top of the pipeline above as a
pure presentation/integration layer — it does not reimplement or duplicate any
of the pipeline logic, only invokes `agp_extract.pipeline.phase1` and renders
its output objects/files.

```bash
streamlit run app.py
```

- **Demo Mode** — sidebar → "Load AGP Demo Sample" loads the already-processed
  `outputs/cl9ch11/` artifacts directly from disk. No API key, no pipeline call.
  Works out of the box because the processed runs are committed to this repo.
- **Upload Mode** — upload a PDF / JPG / JPEG / PNG / WEBP / TXT, or a **ZIP**
  archive (of images, a PDF, or a mix — unsupported files inside are ignored
  with a visible reason, not silently dropped). ZIP extraction is sandboxed
  (path-traversal, oversized-archive, zip-bomb, executable-content guards in
  `agp_extract/ingest/loader.py`); **Process Document** then runs the real
  pipeline with live per-stage progress, including the routing decision, the
  coverage check, and reconstruction stats.
- **Views**: Overview (metrics from `eval_report.json`, plus *how this
  document was read* — routing, parser, measured page coverage, pages
  flagged for review), Questions (filterable tree + detail), Document
  Explorer (page image with block bounding boxes overlaid), Evidence &
  Answers (per-signal evidence, and a dedicated "not in document" view
  proving no hallucinated answers), Validation (confidence gate, numeric
  catches, escalations). Export includes a one-click download of the full
  Q&A PDF.

See [DEPLOY.md](DEPLOY.md) for hosting (Streamlit Community Cloud, GitHub
Pages for the results dossier).

## Architecture

```
run.py → cli → pipeline/phase1 (LangGraph, 13 nodes — see diagram above)
```

- **`parse/`** — parser-agnostic document reading. `base.py` defines the
  `DocumentParser` contract; `vlm_parser.py` (vision model, any scan),
  `docling_parser.py` (born-digital PDFs, zero API cost), `mistral_parser.py`
  (optional OCR benchmark, key-optional). `router.py` decides per page by
  probing for a usable text layer. `factory.py` selects by config.
- **`reconstruct/`** — the span model. `spans.py` (the data model),
  `signals.py` (structural/marker/semantic boundary scoring, deterministic),
  `reconstructor.py` (join transitively across blocks, split an over-bundled
  block into sub-parts — never confusing a solution's repeated labels with
  new sub-questions).
- **`providers/`** — LLM-agnostic layer for the vision/reasoning/embedding
  roles. `base.py` defines the contract; `gemini.py` (default), `null.py`
  (offline OpenCV fallback), `anthropic.py` / `openai.py` (stubs). Prompts
  live in `prompts/`, not in code.
- **`schemas/`** — strict Pydantic contracts: confidence-aware `BBox`,
  three-level provenance, `ContentBlock`, `Question`, graph, evidence, document.
- **`discover/`** — deterministic question builder (`builder.py`) +
  targeted LLM refinement for ambiguous cases (`llm_refine.py`).
- **`retrieve/` + `associate/`** — hybrid answer retrieval (structural graph
  → lexical BM25 → semantic embeddings, in that priority) and false-positive-averse
  association (`phase4.py`).
- **`validate/`** — objective coverage checks (`coverage.py`), calibrated
  confidence (`calibration.py`), numeric verification (`numeric.py`),
  bounded escalation (`escalate.py`).
- **`export/`** — human-reviewable Q&A PDF (`qa_pdf.py`): page number on
  every question, marks only where the publisher prints them, `NOT IN
  DOCUMENT` shown honestly rather than left blank.
- **`eval/`** — evaluation hooks: intrinsic (coverage, provenance,
  confidence, integrity) and extrinsic gold comparison
  (`question_gold.py` — recall, completeness and tree accuracy reported
  separately, never averaged into one figure).
- **`graph/`** — knowledge graph as plain JSON + SQLite (no graph DB).
- **`cache/`** — page-hash keyed cache; only validated results are cached,
  failures go to a separate record.

### Provider / model independence

`.env` controls everything; nothing is hard-coded:

```
LLM_PROVIDER=gemini              # gemini | null | anthropic | openai
VISION_MODEL=gemini-flash-latest
REASONING_MODEL=gemini-flash-latest
EMBEDDING_MODEL=gemini-embedding-001
PARSER=auto                      # auto | vlm | docling | mistral
```

## Gold evaluation

`eval/gold/cl9ch11_gold_questions.json` is transcribed by hand from the source
page images — never generated from the pipeline's own output, which would
score 100% by construction and prove nothing. It deliberately includes two
false-positive traps (pages of numbered definitions; a section heading ending
in "?") and covers two very different regions: worked examples and the
complete self-assessment section (cross-page case study, OR internal choice,
printed marks, QR-gated absent answers).

```bash
python -m pytest tests/ -q     # 56 tests, no API key required
```

## Known limitations

- 23 of 30 pages in `cl9ch11` are outside the measured gold window; counts
  exist for them, accuracy does not.
- Sub-question tree recall (71% on the measured window) is weaker than
  question recall — some inline sub-parts still don't split.
- Question type accuracy (65%) is partly a taxonomy ambiguity between
  *format* types (short/long answer) and *content* types (numerical/MCQ/
  conceptual); see the dossier for the breakdown.
- Ink-coverage on the born-digital path (0.69) is lower than on the scanned
  path (0.98) — either real under-capture of figure regions, or a threshold
  calibrated for the wrong reader. Left flagging rather than silently relaxed.
- `docling` is commented out in `requirements.txt` for a lightweight hosted
  demo (it pulls in torch); without it the router falls back to the vision
  parser for born-digital input too.
