# Phase 6 — Parser Substrate & Recall Guarantees (design)

Goal: replace the single-VLM-call-per-page reader in Phase 1 with a **swappable
document-parser substrate** (docling default), add a born-digital/scanned
**router**, and introduce **coverage invariants** that make missed content a
detectable failure rather than a silent one. Phases 2–5 are unchanged: their
contract (`PageExtraction` → `RawBlock[]`) is exactly the seam we build on.

Non-goal: changing how questions, answers, marks, or confidence are derived.
That logic is sound and stays where it is.

## A. Why — the measured failure

Phase 1 asks one vision call to do layout segmentation, bbox regression,
transcription, *and* semantic classification, and to return it as one JSON blob.
Measured on `outputs/leph202` (NCERT XII Physics, Wave Optics, 19 pp):

| Signal | Value |
|---|---|
| Pages yielding **zero blocks** | **3 / 19** (idx 2, 3, 9) |
| Blocks per page | 10.6 (vs 18.7 on `cl9ch11`) |
| Blocks typed `question` | 6 — all on the final page |
| Sub-questions detected | 0 (Ex 10.2 has (a)–(d), 10.3 (a)–(b); captured flat) |
| Reported mean extraction confidence | **0.963** |

Four defects, in severity order:

1. **Failed extractions are cached.** `page_extractor.py` calls `cache.put()`
   unconditionally on whatever `extract_page()` returned; `GeminiProvider._parse()`
   returns `([], None)` on any JSON decode failure — no raise, no retry. Three
   `.cache/extractions/*.json` entries on disk hold `"blocks": []`, matching the
   three dead pages exactly. The loss is frozen, re-runs reproduce it, and
   `content_sha256` stability reports it as success. **Phase 5 is certifying a
   corrupted input as stable.** This is the most serious bug in the codebase.
2. **Truncation is undetectable.** `finish_reason` is never read, so `MAX_TOKENS`
   is indistinguishable from a genuinely empty page.
3. **Recall is unbounded by design.** Nothing checks that returned blocks cover
   the page. A VLM summarising a dense two-column page silently drops items, and
   `extraction_confidence` is model-self-reported — hence 0.963 on a page set
   that is 16 % empty.
4. **The born-digital text layer is discarded.** `ingest/loader.py` rasterises
   every PDF and sends images to the VLM; its own docstring notes PyMuPDF "could
   harvest text+spans deterministically (cheap, exact) — a hook noted here for
   later". NCERT PDFs are born-digital. This is why the *easier* document scored
   worse than the hard scanned one: the pipeline threw away the easy path and
   paid ~100× more to do it badly.

## B. Architecture — the `DocumentParser` port

`PageExtraction`/`RawBlock` is already the right contract; it is simply only ever
produced by an `LLMProvider` today. Lift it into its own port (same pattern as
the existing provider abstraction), so parser choice is config, not a rewrite:

```python
# parse/base.py
class DocumentParser(abc.ABC):
    name: str
    def parse(self, pages: list[LoadedPage], settings: Settings)
        -> dict[int, PageExtraction]: ...
```

Document-level (not per-page) because docling/MinerU consume a whole document and
derive cross-page reading order from it. The existing VLM path is wrapped as
`VlmPageParser`, which loops pages and delegates to `provider.extract_page` — so
it keeps working byte-for-byte and stays available for escalation (§H).

```
ingest → preprocess → route → parse → verify_coverage → assemble → structure → …
```

`extract` becomes `parse`; `route` and `verify_coverage` are new. Everything from
`assemble` onward is untouched.

Adapters: `parse/docling_parser.py` (default), `parse/vlm_parser.py` (current
behaviour), with `parse/mineru_parser.py` and `parse/marker_parser.py` as future
drop-ins. Selected by `settings.parser: "docling" | "vlm" | ...`.

**Why docling for the born-digital branch** (the bake-off in §M kept the scanned
branch on the VLM), given docling is *not* the accuracy leader — OmniDocBench
puts MinerU2.5 at 90.67 overall:

- **MIT licence**, IBM Research / LF AI & Data — cleanest for publisher-owned
  production infra. MinerU's custom licence carries 100M-MAU / $20M-revenue
  thresholds; harmless here but a needless clause to inherit.
- **`DoclingDocument` provenance maps near 1:1 onto our schema** (§D) — the
  decisive factor. Minimal churn beats a few benchmark points.
- **Native born-digital path** — reads the text layer, never runs OCR.
- Pluggable OCR + an optional VLM pipeline (granite-docling) as a third mode.
- Already installed and verified in this environment (2.126.0, Py 3.13, Windows).

The port is what makes this defensible: if the bake-off or a future document
class favours MinerU, it is a config change.

## C. Input router (`parse/router.py`)

"Document-agnostic" concretely means: classify the input, then route.

Per page, PyMuPDF (already a dependency) measures text-layer coverage —
`len(page.get_text("text").strip())` plus the ratio of text-covered area to page
area. Decision per page, not per document, so hybrid scans route correctly:

| Condition | Route |
|---|---|
| chars > 100 and text area ≥ 40 % | docling **standard, `do_ocr=False`** — exact, ~free |
| otherwise (scanned) | **VLM path** (current behaviour) — see §M |
| coverage invariant fails (§F) | escalate to VLM (§H) |

The scanned branch stays on the VLM because the bake-off (§M) measured
docling+RapidOCR 4.1 % *behind* it on text recall and clearly worse on tables.
That branch is a config value, not a hard-coding: re-point it at docling once a
stronger OCR engine or the granite-docling VLM pipeline clears the bar in
`eval/bakeoff.py`.

Verified on the `cl9ch11` sample: probe reports `pages_with_text: 0/30`,
`mean_chars: 0.0` → correctly classified as fully scanned.

## D. Docling adapter — `DoclingDocument` → `PageExtraction`

Mapping is direct, and *upgrades* provenance:

| docling | ours | note |
|---|---|---|
| `ProvenanceItem.page_no` | `BlockProvenance.page_index` | 1-based → 0-based |
| `ProvenanceItem.bbox` (l,t,r,b) | `BBox(x0,y0,x1,y1)` | **must** convert `coord_origin=BOTTOMLEFT` → TOPLEFT, and scale PDF points → raster px by the render DPI |
| `ProvenanceItem.charspan` | *(new)* `meta.charspan` | exact char offsets into the source text layer — stronger than bbox, and exactly what Phase-3 inline splitting wants |
| `content_layer` (body/furniture) | `BlockType.NOISE` | native page-furniture separation, replaces 37 hand-typed `noise` blocks on `cl9ch11` |
| `TableItem` → `export_to_dataframe()` | table block + preserved crop | row-span handling comes free |
| `PictureItem` | asset block | `crop_asset()` path unchanged |
| `FormulaItem` (`do_formula_enrichment`) | `latex` | replaces equations-as-images where it succeeds |
| document order | `reading_order` | Phase-2 geometry cross-check still runs and still arbitrates |

`bbox_confidence` / `extraction_confidence`: docling reports no per-item
confidence. Emit a **fixed provenance-derived value** (`1.0` for born-digital
text-layer items, a configured constant for OCR items) and — importantly — set
`confidence_source: "deterministic_text_layer" | "ocr_engine"` so Phase 5's
calibration keeps treating self-reported and system-evidence confidence as
separate signals. **Do not** synthesise a fake per-item score.

## E. Semantic typing (`parse/semantic_type.py`)

`DocItemLabel` is layout-oriented — `text`, `list_item`, `section_header`,
`table`, `picture`, `formula`, `caption`, `code`, `chart`. It has **no
`question`, `answer`, `worked_example`, or `callout`**. So semantic typing
becomes an explicit step instead of a side effect of the VLM prompt:

1. **Deterministic first** — enumerator/marker regexes already proven in Phase 2/3
   (`Q.N`, `N.`, `Example N`, `Ans.`, `Solution`, `[N marks]`, Bloom tags), plus
   position and the section heading the block sits under.
2. **LLM only for the residue** — batched, cached, text-only (no images), using
   the existing `provider.generate_json`. Same pattern as `discover/llm_refine.py`.

This is strictly better than today, where one call does layout *and* semantics and
does neither reliably. It also makes the VLM a text-classifier over reliable
input, which is far cheaper and far more accurate than vision-over-pixels.

## F. Recall invariants (`validate/coverage.py`) — the near-100 % mechanism

Near-100 % accuracy is bought with **verifiable invariants**, not a better prompt.
Each runs post-parse and, on failure, escalates that page (§H) rather than
silently passing. All three catch failures the current confidence score cannot see:

1. **Ink coverage** — rasterise the page, binarise, compute the fraction of
   non-background pixels falling inside some block's bbox. Below
   `coverage_min` (default 0.90) ⇒ unextracted content on the page. This alone
   catches every one of the three `leph202` zero-block pages *and* the silent
   partial-skip case.
2. **Question-number continuity** — exercise numbering should form a contiguous
   run (10.1, 10.2, 10.3 …). A gap **is** a missed question. Highest-leverage
   domain-specific check available to us, and nearly free. Reported per
   numbering-series so a chapter with several independent series is handled.
3. **Zero-yield / low-yield page** — already in `IntegrityHook`; promoted from a
   reported metric to a **gating** one.

New `CoverageReport` on the eval output: per page `ink_coverage`,
`numbering_gaps`, `status ∈ ok | escalated | unresolved`. The Phase-5 gate reads
this instead of `extraction_confidence`.

## G. Cache correctness

Two changes, both small and both load-bearing:

- **Never cache an unvalidated result.** `cache.put()` moves behind a
  `PageExtraction.is_valid()` check (non-empty blocks, no truncation flag, passes
  §F.1). Failures are written to a separate `.cache/failures/` entry recording
  the reason, so a retry is cheap but the *bad data* is never served as good.
- **Record truncation.** Providers read `finish_reason` and set
  `PageExtraction.truncated`; a truncated page is a failure, not an empty page.

Migration: bump `PROMPT_VERSION` so existing poisoned entries are bypassed, and
ship a one-shot `scripts/purge_empty_cache.py` that deletes entries with
`blocks == []`.

## H. Targeted VLM escalation

The VLM stops being the primary reader and becomes the escalation path — which is
where its cost is actually justified:

| Trigger | Action |
|---|---|
| ink coverage < threshold | re-parse that page via `VlmPageParser` at `vision_escalation_model` |
| numbering gap | re-parse the pages spanning the gap |
| formula enrichment failed | crop-level VLM call for that region only |
| semantic type unresolved | text-only classification (§E.2) |

Bounded and accounted exactly like Phase 5's existing escalation: report
`recovered` vs `confirmed` vs `unchanged`. Reuses `escalate.py` machinery.

## I. Q&A export (`export/qa_pdf.py`)

Final-run deliverable alongside the existing JSON: a rendered **questions +
answers PDF**, built from `questions.json` + `AnswerAssociation`, so the output
is reviewable by a human without reading JSON.

- Question tree in document order, sub-questions nested, OR-groups shown as
  alternatives.
- Metadata per question: `question_type`, `marks` (omitted, not zeroed, when the
  publisher prints none — NCERT does not print marks; Educart does), `bloom`,
  `source`, printed page.
- Answers rendered **only as extracted**; `not_in_document` printed honestly as
  such (e.g. the QR-code self-assessments) — never blank, never generated.
- Preserved asset crops inlined for diagrams/equations/tables, so a
  figure-dependent question stays answerable on the page.
- Provenance footer per question (source page + block ids) to keep the audit
  trail intact in the human-readable artefact.

Exposed as `run.py export --format pdf` and a download button in the Streamlit UI.
ReportLab or WeasyPrint; no new model dependency.

## J. Evaluation

- `eval/gold/leph202_*.json` — build the missing gold set for a **second
  publisher** (currently only `cl9ch11` has one). Document-agnosticism is a claim
  we cannot evidence with one book.
- New `CoverageHook` asserting: zero pages below ink threshold, zero numbering
  gaps, zero cached-empty entries.
- Existing `structure_gold` / `q-gold` / `a-gold` must stay green through the
  swap — they are the regression net for "did replacing the parser break the
  semantics".
- Parser bake-off harness kept as `eval/bakeoff.py` so docling vs VLM vs MinerU
  is a reproducible measurement, not a one-off.

## K. Cost

Per 30-page chapter, cold:

| | today | Phase 6 |
|---|---|---|
| Born-digital | 30 vision calls | **0** (text layer, local, exact) |
| Scanned | 30 vision calls | 30 vision calls (unchanged — §M) |
| Semantic typing | (bundled) | ~8 text-only batched calls |
| Escalation | — | only flagged pages |

The cost win is therefore **concentrated entirely on born-digital input** — where
it is total, since that branch drops to zero API calls and zero OCR compute. That
is also the branch that is currently *most* broken (`leph202`), so the accuracy
and cost wins land together rather than trading off.

For reference if the scanned branch later moves to docling: 370 s / 30 pp on CPU
(measured, ~12.3 s/page), which parallelises and needs no API key; GPU cuts it
roughly 10×. That would make the scaling answer for "large and complex chapters"
"throw cores at it" rather than "pay per page".

## L. Migration & risks

Sequence — each step independently reviewable, none breaks the existing run:

1. `DocumentParser` port + `VlmPageParser` wrapping today's behaviour. **No
   behaviour change**; all golds stay green. Pure refactor.
2. Cache-correctness fix (§G) + purge script. Recovers the three dead `leph202`
   pages on re-run.
3. Docling adapter (§D) + router (§C), behind `settings.parser` — wired for the
   **born-digital branch only**. The scanned branch keeps its current path, so
   this cannot regress `cl9ch11`.
4. Coverage invariants (§F) + escalation rewiring (§H).
5. Semantic typing split (§E); re-run golds; build `leph202` gold.
6. Q&A PDF export (§I).

Risks, and how they are handled:

- **OCR quality on scanned input — confirmed insufficient, already mitigated.**
  §M measured docling+RapidOCR 4.1 % behind the VLM on text and clearly worse on
  tables, so the router (§C) leaves scanned documents on the VLM path. The
  coverage invariant catches drop-outs but *not* character errors, so §J's gold
  set remains the only real guard on OCR quality whenever that branch moves.
- **docling is not the benchmark leader.** Deliberately accepted for integration
  fit; §B's port is the hedge, and `eval/bakeoff.py` makes revisiting it cheap.
- **Windows deployment.** `huggingface_hub` requires
  `HF_HUB_DISABLE_SYMLINKS=1` without Developer Mode, else model fetch fails with
  `WinError 1314`. Set it in config and document it in the README.
- **Scope discipline.** Phases 2–5 must not be "improved" during this swap;
  their golds are the evidence that the parser change is isolated.

## M. Bake-off results — `cl9ch11`, 30 pp, scanned (measured)

| | current (Gemini VLM) | docling + RapidOCR |
|---|---|---|
| Items / blocks | 562 | **1,098** |
| Text chars | **89,539** | 85,878 (**−4.1 %**) |
| Items with bbox provenance | 100 % | 100 % |
| Zero-yield pages | 0 | 0 |
| Formulas / equations | 17 | **32** |
| Tables detected | 4 | 4 (but see below) |
| Discrete list items | 13 | **464** |
| Wall clock | ~30 API calls | 370 s local CPU, **0 API calls** |

**Docling does not win on the scanned sample.** It is 4.1 % behind on raw text
recall, and behind on 24 of 30 pages. Table extraction is materially worse: the
speed-of-sound table on p.9 yields 34 cells but only 204 chars, and a diagram on
p.5 is misdetected as a table with cell text `"0 0 e 0 0 0 0 0"` — RapidOCR's
small models are the bottleneck, not docling's layout model.

Where docling is genuinely better: **2× granularity** (1,098 vs 562 items, and
464 discrete list items vs 13) — the VLM collapses enumerated runs into single
blocks, and question boundaries *are* list boundaries, so finer items directly
help Phase 3; **32 formulas vs 17**; native page-furniture separation
(`page_footer` vs hand-typed `noise`); and zero API cost.

Two corrections to earlier readings, recorded so they are not repeated:
- A first pass measured docling at −4,672 chars. That metric ignored table cell
  text (which lives in `table.data.table_cells`, not `item.text`). Adding it
  back gives the −3,661 / −4.1 % above.
- The VLM was *not* truncating the p.8 learning objectives; it captured all three
  bullets inside one block. Granularity difference, not recall loss.

**Conclusion — this splits the routing decision (§C) rather than settling it:**

- **Born-digital → docling, no contest.** Untested here only because the
  `leph202` source PDF is unavailable, but this is where the VLM catastrophically
  failed (3 dead pages, silently cached) and where docling reads the text layer
  exactly, deterministically, at zero API cost. The argument is structural, not
  empirical.
- **Scanned → keep the VLM path for now.** docling+RapidOCR is not yet at parity.
  Revisit with a stronger OCR engine (EasyOCR / Tesseract / cloud OCR) or
  docling's granite-docling VLM pipeline, measured via `eval/bakeoff.py`.

This is exactly the outcome the `DocumentParser` port (§B) exists to absorb: the
router picks per document class, and neither answer is baked into the pipeline.
It also means step 3 of the migration (§L) ships **router + docling for the
born-digital branch only**, leaving the scanned branch on its current, working
path — a strictly smaller and safer change than a wholesale parser swap.
