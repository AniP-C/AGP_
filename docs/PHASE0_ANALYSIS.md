# Phase 0 — Sample Inspection & Architecture Analysis

**Sample:** `cl9ch11/` — Educart *Science Class IX*, **Chapter 11 "Sound"**, physical pages 314–342, delivered as **30 JPG page renders** (`task_page-0001..0030.jpg`), each **1200 × 1688 px** RGB (~145 DPI), born-digital rasterized (clean, no skew/scan noise), **no embedded text layer** (image-only input).

This document is the source of truth for the POC. Everything below is grounded in what the sample actually contains, not in assumptions. Publisher-specific patterns are explicitly isolated as *optional heuristics*, never load-bearing.

---

## Observed content inventory (pages actually inspected: 1–9, 22, 24–25, 27–30)

| Region | Where | What it demonstrates |
|---|---|---|
| Chapter opener | p1 | Hero image + banner caption + "Topic Notes" (mini-TOC) |
| `TOPIC n` dividers | p2 (T1), p9 (T2) | Chapter → Topic hierarchy, not just "sections" |
| Learning Objectives / Outcomes | p2, p9 | Boxed non-question pedagogical lists |
| 2-column body prose | most pages | Reading-order hazard (see below) |
| Sidebar ("Real Life Application") | p2 | Narrow left-column aside with its own image |
| Callout boxes | p3 "Caution", p4/5/8 "Important", p8 "Mnemonics", p27 "Related Theory" | `note`/`callout` blocks, not questions |
| Captioned figures & diagrams | p3, p5, p6, p7, p27, p28, p30 | Italic caption below asset; labelled physics diagrams (Bell-jar, ray diagram, waveforms) |
| Equations as raster images | p6 (ν=1/T), p8 (v=λν, T=1/500), p24 (λ=1500×2×10⁻⁶), p28 (s=ut+½gt²) | Must be recovered as LaTeX; multi-line derivations |
| Data table w/ merged cells | p9 (State/Medium/Speed, "Gases" spans 5 rows) | Row-span tables |
| Answer that **is** a table | p8 Ex.8 (Loudness vs Intensity), p9 | Answer payload can be tabular |
| Pseudo-table from dot-images | p5 (Vacuum/Gas/Liquid/Solid) | "Table" cells are pictures, not text |
| **Worked Examples** (Ex. 1–8) | p4–p8 | `source_type=worked_example`; inline `Ans.`+`Explanation` |
| Case-based Example w/ sub-parts (A)–(E) | p4 Ex.1 | One example → many typed sub-questions; answers appear **later** & flow across columns |
| **Solved question bank** (numbered ~22–47) | p22, p24, p27, p28 | Inline `Ans.`; typed section banners; internal `OR` choices |
| Typed section banners w/ marks | p25 "SHORT ANSWER Type-II (SA-II) **[ 3 marks ]**" | Marks encoded at *section* level |
| **SELF ASSESSMENT** (Q1–12) | p29–p30 | MCQ / A-R / Case / SA-I / SA-II / LA; **per-question marks**; **no in-doc answers** (behind QR) |
| Metadata tokens | throughout | Bloom `(Remember/Understand/Apply/Analyse)`, source `[NCERT]/[DIKSHA]/[NCERT Exemplar]/[CBSE Question Bank 2022]`, marks (trailing int + banner), `OR` |
| Noise glyphs | p30 QR, logos, page furniture | Must be filtered from content |

Question **types actually present:** MCQ (incl. 2×2 option grids), Assertion-Reason, fill-in-the-blank (with MCQ options), numerical (with worked derivations), case/passage-based, define/short-answer, distinguish (answer = comparison table), ordering (a>b>c…), activity/experiment description, long-answer conceptual. → the `other/unknown` bucket must exist for match-the-following/true-false etc. not seen here.

---

## 1. Document characteristics observed

- **Digitally-generated, then rasterized.** Vector-clean glyphs, perfectly horizontal baselines, consistent font family, no scanner artefacts. But we receive **images**, so *there is no text layer to read* — we must OCR/vision every page regardless of the "born-digital" origin.
- **Regular but multi-region layout:** dominant 2-column grid interrupted by full-width headers, banners, tables, figures, and a narrow sidebar. Reading order is **not** simple top-to-bottom.
- **Heavy pedagogical scaffolding:** topics, objectives/outcomes, callouts, worked examples, a large *solved* question bank, and a *final unsolved* self-assessment — i.e. questions are **everywhere**, exactly as the brief warns.
- **Two answer regimes coexist:** (a) inline-answered (Examples + solved bank), (b) answer-absent (self-assessment → QR). The system must represent "answer not present in document" as a first-class state.
- **Two marks encodings:** a trailing integer after the Bloom tag (`(Understand) 1`) and a section banner (`[ 3 marks ]`). Marks are **partial** — present in assessment/solved-bank sections, absent on early worked examples.

## 2. Content types present
text · headings (chapter/topic/section/subsection) · paragraphs · ordered/unordered lists · nested sub-part enumerations `(A)/(a)/(i)` · callouts (note/caution/important/mnemonic/related-theory) · figures/photos · labelled diagrams · equations (raster → LaTeX) · data tables (with row-spans) · comparison-table *answers* · pseudo-tables (image cells) · worked examples · solved questions · self-assessment questions · answers/solutions/explanations · metadata tags (bloom/source/marks) · QR/logos/page-furniture (noise).

## 3. Difficult cases identified
1. **2-column reading order** with full-width interruptions — naive OCR interleaves question text and unrelated column content, scrambling Q↔A adjacency.
2. **Answer flows across columns/pages** (Ex.1's `Ans.(A)` starts bottom-left, continues top-right; questions like p29→p30 span the page break).
3. **Answer sometimes absent** (self-assessment → QR): must not hallucinate an answer; mark `answer_status = not_in_document`.
4. **Nested multi-part questions** with *per-part* answers and *internal `OR` choices* — one logical item → tree of sub-questions + alternates.
5. **`source_type` ≠ `question_type`** (e.g. worked_example × numerical; self_assessment × mcq) — two independent axes, as the interviewer stressed.
6. **Enumerator collisions:** MCQ option `(a)` vs sub-part `(A)` vs list `(1)` vs Bloom `(Understand)` all share parenthesis syntax.
7. **Equations only as images** + multi-line derivations → need vision→LaTeX and a parse for numeric verification.
8. **Tables in 3 forms:** real row-span data table, answer-as-table, and dot-image pseudo-table.
9. **Figure/table references** ("in the following figure", "refer to the table above", "the given graph") → must resolve to a specific asset via structure, not just text.
10. **Marks partial + dual-encoded**; Bloom tag is not marks (a frequent conflation to avoid).
11. **Noise:** QR code, publisher logos, decorative diamonds, page numbers — must be excluded from content and from question discovery.
12. **Image-only input + no Tesseract installed + no API key in env** → the ingestion path cannot assume either a text layer or a ready OCR engine; must degrade gracefully.

## 4. Recommended extraction / OCR / layout approach
**Hybrid, cheap-first, escalate-on-ambiguity** — matches the brief's cost directive.

- **Ingest:** accept a folder of page images *or* a PDF. If a **real PDF with a text layer** is ever supplied, harvest text+spans deterministically via PyMuPDF (free, exact) and skip OCR. For this image-only sample, that path yields nothing, so OCR/vision is required.
- **Preprocess (deterministic, OpenCV):** grayscale, light denoise, optional binarize + 1.5–2× upscale to lift ~145 DPI toward OCR-friendly ~300 DPI, deskew (near-noop here). Compute a **page hash** for caching.
- **OCR / word geometry (deterministic when available):** Tesseract via `pytesseract` for word-level text + bbox + confidence. *Not currently installed* → either (a) install Tesseract, or (b) for the POC, obtain text+geometry from the vision model and treat OCR as an optional deterministic accelerator added later. rapidfuzz (present) anchors vision output back to any OCR words for provenance/confidence.
- **Layout / blocks:** ideal heavy stack (PP-Structure / LayoutParser / docTR) is painful on Windows and overkill for a POC. **Recommended POC path:** one **vision-LLM structuring pass per page** returning typed blocks with bboxes + text + column/reading-order hints, *reconciled against deterministic word boxes* for provenance. Column segmentation and reading order can also be recovered deterministically from word-box x-histograms as a cross-check / no-key fallback.
- **Assets:** crop figure/diagram/table/equation regions to `assets/` from returned bboxes; keep the original pixels (provenance) alongside any generated description/LaTeX.
- **Escalation:** only low-confidence or ambiguous blocks go to the strongest (most expensive) multimodal/reasoning model; everything cacheable is cached by page-hash.

## 5. What should be deterministic (cheap, no LLM)
Page hashing & all caching · image preprocessing · PDF text-layer harvest when present · Tesseract OCR word boxes/confidence (when installed) · column detection & reading-order from geometry · **optional Educart heuristics** (regex for `TOPIC n`, `Example n.`, `Ans.`, `Explanation:`, `\d+\.` question numbers, `(A)/(a)/(i)` enumerators, MCQ `(a)…(d)`, Bloom tags, `[SOURCE]` tags, trailing marks, `[ n marks ]` banners, `OR`) — used only to **boost confidence / pre-segment**, never as a hard requirement · asset cropping · **numeric re-computation** of numerical answers via `sympy` · duplicate detection (rapidfuzz) · schema validation (Pydantic) · JSON/SQLite persistence.

## 6. What should use an LLM / vision model
Per-page block typing & reading-order on ambiguous pages · figure/diagram **description** + caption association · **equation→LaTeX** · **table structure** incl. merged/row-span & answer-tables · **question discovery in prose** (the embedded/in-text questions that no regex will catch) · **classification** (`source_type`, `question_type`, Bloom, marks parse, options split) · **answer association** reasoning across the 5 evidence levels · validation judgments (relevance/completeness). All LLM calls behind the provider abstraction; all prompts stored separately from logic.

## 7. Canonical content-block schema (proposed)
```json
{
  "id": "blk_000142",
  "type": "question",                     // enum incl. document/chapter/topic/section/subsection/heading/
                                          // paragraph/list/callout/figure/image/diagram/table/equation/
                                          // caption/worked_example/question/answer/solution/activity/note/other
  "subtype": "case_based",                // optional finer label
  "text": "…normalized text…",
  "html": null,                            // optional richer rendering (tables/lists)
  "latex": null,                           // for type=equation
  "provenance": {
    "page_index": 3,                       // 0-based file index
    "printed_page": 316,                   // number printed on the page
    "bbox": [120, 450, 920, 620],          // pixel coords in the page image
    "column": 0,                           // 0=left,1=right,-1=full-width/sidebar
    "reading_order": 42,                   // global order across the document
    "source_image": "cl9ch11/task_page-0004.jpg",
    "ocr_confidence": 0.94,                // when available
    "extractor": "vision:gemini-1.5-pro",  // which stage produced it
    "extraction_confidence": 0.91
  },
  "asset_path": "assets/blk_000142.png",   // cropped original pixels for figure/table/equation
  "parent_id": "blk_000140",               // structural parent (topic/section/example)
  "section_id": "sec_11_1",
  "prev_id": "blk_000141",                 // reading-order neighbours (fast local context)
  "next_id": "blk_000143",
  "refs": ["fig_12", "tbl_3"],             // outbound "refers_to" targets (resolved later)
  "tags": { "bloom": "Understand", "source": ["NCERT"], "marks": null },
  "meta": {}                                // free-form, forward-compatible
}
```
Everything is optional-friendly and forward-compatible (`meta`), so it generalizes beyond Educart. Provenance is mandatory on every block.

## 8. Document relationship / knowledge-graph structure (proposed)
**Nodes** = content blocks (typed as above) + a synthetic `Document` root and `Topic`/`Section` nodes.
**Edges (typed, directed):**
- `contains` / `child_of` — hierarchy (Document→Topic→Section→…→block)
- `reading_next` — linear reading order
- `part_of` — sub-question → parent question/example; alternate → `OR`-group
- `answered_by` — question → answer/solution/explanation block(s)
- `refers_to` — question/text → figure/table/equation/caption
- `has_caption` — figure/table ↔ caption
- `derived_from` — answer/table generated from another block
- `located_on` — block → page

Graph is the **primary** representation for structure + provenance; the vector store is a **secondary index** over block text/descriptions. The book is never reduced to "the vectors."

## 9. Final question schema (proposed — extends the brief)
```json
{
  "id": "Q_017",
  "source_type": "self_assessment",        // worked_example|solved_question|self_assessment|in_text|activity|…
  "question_type": "numerical",            // mcq|assertion_reason|fill_blank|numerical|short|long|case_study|
                                           // conceptual|matching|true_false|activity|unknown
  "bloom_level": "Apply",                  // pedagogical tag, SEPARATE from marks
  "question_number": "7(B)",               // as printed
  "question_text": "…",
  "options": [ {"key":"a","text":"…"} ],   // [] when N/A
  "sub_questions": [ { /* recursive: same schema */ } ],
  "internal_choice": { "type": "OR", "group_id": "Q_017_C" },
  "answer": {
    "status": "present",                   // present | not_in_document | partial | derived
    "text": "…",
    "explanation": "…",
    "answer_table": null,                   // when the answer is tabular
    "answer_assets": ["fig_31"]
  },
  "marks": { "value": 1, "source": "trailing_int" },   // null when absent; source: trailing_int|section_banner|inferred
  "source_refs": ["NCERT Exemplar"],
  "related_assets": ["fig_31", "eq_12"],
  "provenance": {
    "source_pages": [12, 13], "printed_pages": [329, 330],
    "source_blocks": ["blk_134","blk_135"],
    "answer_blocks": ["blk_140"], "evidence": ["local","structural"]
  },
  "verification": { "numeric_check": "passed", "checks": {…} },
  "confidence": 0.93
}
```
Key deltas vs the brief's draft: explicit `answer.status` (handles QR-only answers), `bloom_level` separated from `marks`, `marks.source`, recursive `sub_questions` + `internal_choice`, `answer_table`, and provenance that distinguishes question blocks from answer blocks + which evidence levels fired.

## 10. Where LangGraph orchestrates
LangGraph owns the **document-level pipeline** as a stateful graph with conditional routing/retries:
`ingest → preprocess → (page fan-out) extract_page_blocks → assemble_document → build_graph → discover_questions → classify → retrieve_context → associate_answers → validate → confidence_gate ─(low)→ reprocess ─┐ / ─(high)→ finalize`. The gate loops low-confidence items back through targeted reprocessing (stronger model / more evidence) with a bounded retry count. State = the growing block store + graph + question list + confidence ledger. **Phase 1 uses only the first three nodes**; the rest are stubs we fill in later phases.

## 11. Where RAG is introduced
**Phase 4**, as a *supporting* retriever for **answer association**, not the backbone. Hybrid = semantic (Chroma over block text/descriptions) + lexical (BM25/keyword) + **structural** (graph queries: same section, `part_of` parent, `refers_to` target, reading-order neighbours) → rerank → evidence set handed to the LLM associator. Structural retrieval is what resolves "the table above / the following figure." The vector DB indexes blocks *with their graph metadata*, never replacing the graph.

## 12. Assumptions & risks
- **A:** Input may be image-only (this sample) *or* a real PDF; design for both, don't assume a text layer.
- **A:** Educart formatting is a *convenience*, not a contract — all publisher regexes are optional confidence boosters.
- **A:** Some answers legitimately don't exist in-document (QR) → represent, don't fabricate.
- **R:** ~145 DPI raster may soften small sub/superscripts & equation glyphs → mitigate via upscaling + vision; verify numerics with sympy.
- **R:** Reading-order errors in 2-column zones → dual recovery (geometry + vision) and cross-check.
- **R:** Merged-cell / pseudo-image tables are error-prone → keep original crop as provenance so nothing is silently lost.
- **R:** No API key currently in env & no Tesseract → Phase 1 must run in a **deterministic/mock mode** and light up vision when a key is provided.
- **R:** LLM cost/nondeterminism → page-hash caching + escalate-only-ambiguous + strict Pydantic outputs.
- **R:** Vendor SDK drift (google-generativeai vs google-genai; anthropic absent) → thin provider adapters isolate this.

## 13. Recommended Phase 1 folder structure
```
AGP/
├── cl9ch11/                      # the sample (given)
├── docs/
│   ├── PHASE0_ANALYSIS.md        # this file
│   └── architecture.md           # (later phases)
├── src/agp_extract/
│   ├── config.py                 # pydantic-settings; LLM_PROVIDER, *_MODEL, paths
│   ├── schemas/
│   │   ├── blocks.py             # ContentBlock, BBox, Provenance, BlockType
│   │   └── document.py           # Document, Page, hierarchy
│   ├── providers/                # LLM-agnostic layer
│   │   ├── base.py               # LLMProvider: generate / generate_structured / vision / embed
│   │   ├── gemini.py             # google-genai adapter
│   │   ├── anthropic.py          # stub adapter (SDK not yet installed)
│   │   ├── openai.py             # stub
│   │   └── null.py               # no-key deterministic fallback (runs offline)
│   ├── ingest/
│   │   ├── loader.py             # folder-of-images OR pdf → pages; page hashing
│   │   └── preprocess.py         # OpenCV grayscale/denoise/upscale/deskew
│   ├── ocr/
│   │   └── ocr.py                # pytesseract wrapper (optional) → word boxes+conf
│   ├── extract/
│   │   ├── page_blocks.py        # vision structuring → typed blocks (+ geometry fallback)
│   │   └── heuristics.py         # OPTIONAL Educart regex markers (confidence boosters)
│   ├── assemble/
│   │   └── document_builder.py   # order blocks, build chapter→topic→section hierarchy
│   ├── cache/
│   │   └── store.py              # page-hash keyed cache (json/sqlite)
│   ├── prompts/                  # prompt templates (separate from logic)
│   │   └── page_structuring.md
│   └── cli.py                    # `phase1` command → canonical JSON + asset crops
├── outputs/                      # generated canonical_document.json, assets/
├── .env.example                  # LLM_PROVIDER=..., VISION_MODEL=..., REASONING_MODEL=..., EMBEDDING_MODEL=...
├── requirements.txt
└── README.md
```

**Phase 1 deliverable:** run the CLI over `cl9ch11/` and emit a `canonical_document.json` of typed content blocks with full provenance + cropped assets + a chapter/topic/section hierarchy — *without* yet doing question discovery/answers (Phases 3–5). It must run offline (null provider) and, when a key is set, use vision for block typing.
