# Phase 6 — Pre-implementation Audit

Repository inspection and test baseline, per the Phase-6 brief §29.
**No code changed.** Findings below are evidence-backed; every claim cites the
file, line, or measured output it came from.

---

## 1. Current pipeline

LangGraph, single linear graph in `pipeline/phase1.py` (`build_phase1_graph`):

```
ingest → preprocess → extract → assemble → structure → build_graph
       → build_evidence → discover_questions → associate_answers
       → validate → evaluate → persist
```

Mapped to the brief's target architecture in §4, the gaps are:

| Brief stage | Today |
|---|---|
| INPUT ROUTER | **absent** — every PDF is rasterised |
| DOCUMENT PARSER / OCR | `extract` — VLM-only, no abstraction |
| COVERAGE VALIDATION | **absent** (`IntegrityHook` reports, does not gate) |
| QUESTION CANDIDATE DISCOVERY | `discover_questions` |
| **QUESTION RECONSTRUCTION** | **absent as a stage** — folded into the builder |
| SUBQUESTION / OR TREE | `structure/grouping.py` (block-level only) |
| ANSWER ASSOCIATION | `associate_answers` — sound |
| CONFIDENCE GATE | `validate` — sound |
| TARGETED REPROCESSING | `validate/escalate.py` — sound |

## 2. Relevant files

| Concern | Files |
|---|---|
| Ingestion | `ingest/loader.py`, `ingest/preprocess.py` |
| Extraction | `extract/page_extractor.py`, `providers/{gemini,base}.py` |
| Structure | `structure/{reading_order,grouping,continuation,inline,crossref}.py` |
| Questions | `discover/{builder,classify,context,llm_refine,phase3}.py` |
| Answers | `retrieve/*`, `associate/*`, `schemas/answers.py` |
| Validation | `validate/*`, `eval/hooks.py`, `metrics.py` |
| Schemas | `schemas/{blocks,questions,structure,document,answers}.py` |

## 3. Existing question/answer logic

`discover/builder.py::_Builder.build_node()` constructs one `Question` per
**anchor block**, then extends it by exactly three mechanisms:

1. `b.structure.continues_to` — one appended block (builder.py:131-138)
2. `_stem_blocks()` — passage blocks between a container and its first subpart
3. `self.children[b.id]` — blocks with `part_of_id` pointing at the anchor

Roots come from `roots()`: any block whose `group_role == "container"`, or of
type `QUESTION`/`WORKED_EXAMPLE`/`ACTIVITY` with no `part_of_id`.

Answer association (Phase 4) is structural-first, false-positive-averse, and
requires in-scope structural evidence for any positive match. **It is sound and
should not be touched** — but it runs on whatever the builder produced, so a
truncated question yields a mis-scoped answer search.

## 4. Where the incomplete-question bug occurs

First, a correction to the brief's premise. The cited example is **not**
truncated in the committed `cl9ch11` output. `Q_0006` has a complete 798-char
stem ending `"...Therefore, the sound is a longitudinal wave."`, five
sub-questions (A)–(E), and five associated answers. The example reconstructs
correctly on that document.

The fragility is real, but the mechanism is different — and worse, because it is
invisible on `cl9ch11` and severe on `leph202`. Six concrete defects:

### 4.1 Continuation is followed one hop only — **truncation bug**

`builder.py:131-138` reads `continues_to` once, with no loop. A question spanning
A→B→C appends B and **silently drops C**. This is the literal
"question discovered but truncated" failure the brief describes in §23.

### 4.2 Continuation can never fire on question blocks — **dead feature**

`structure/continuation.py:14` restricts linking to
`_CONT_TYPES = {PARAGRAPH, TABLE, LIST}`, and line 47 additionally requires
`a.type == b.type`. So `QUESTION→QUESTION` and `QUESTION→PARAGRAPH` — the two
commonest real continuations — are structurally impossible to detect.

Measured: `continuation_links` = **2 across 562 blocks** on `cl9ch11`, and
**0** on `leph202`. Cross-page/cross-column question joining has never actually
run on either document. Success criterion §28.D is currently unmet and untested.

### 4.3 Continuation geometry is too strict

Requires `y1 > 0.80·h` on the predecessor *and* `y0 < 0.25·h` on the successor.
A question continuing mid-column is never linked.

### 4.4 No block splitting exists — **the leph202 failure**

Nothing ever splits an over-bundled block. On `leph202` every one of the 9
questions has exactly **one** `source_block`. Consequences:

- `Q_0005` (`10.2`, sub-parts (a)–(d)) → `sub_questions: []`. The tree is lost
  because sub-parts live *inside* one block, and `grouping.py` only relates
  *separate* blocks.
- The same block is then classified `mcq`, because `parse_options()` reads
  `(a)/(b)/(c)` as MCQ options. It is a short-answer question with sub-parts.

So reconstruction must work in **both** directions — join under-segmented blocks
*and* split over-bundled ones. The brief's §11 diagram implies only joining; the
splitting half is equally load-bearing and is currently 0 % implemented.

### 4.5 Publisher-specific regex produces false-positive questions

`grouping.py:20` — `("example", re.compile(r"^\s*Example\s+(\d+)", re.I))`
matches the NCERT **margin banner** "EXAMPLE 10.2", promoting it to a container
and therefore a root question. That is `leph202`'s `Q_0003`:
`question_text = "EXAMPLE 10.2"`, `question_type = unknown`. Pure junk, and it
inflates the question count.

### 4.6 Numbering regex misses NCERT numbering

`grouping.py:21` — `("numbered", re.compile(r"^\s*(\d{1,3})\.\s"))` matches
`"1. "` but **not** `"10.1 "`. Hence `question_number = None` for all six
`leph202` exercises while examples get `question_number = "10"`. This also
disables §9.C numbering-continuity checking on exactly the document that needs it.

### 4.7 Stem assembly drops non-prose

`_stem_blocks()` filters to `_STEM_TYPES = {PARAGRAPH, LIST, LIST_ITEM}`, so an
equation, table, or figure inside a case-study passage is excluded from the stem
(§12.G/H).

## 5. What can be reused

Substantial, and should be preserved:

- **`PageExtraction`/`RawBlock` is already the parser seam** — only ever produced
  by `LLMProvider` today. Lifting it into `DocumentParser` makes Step 3 a pure
  refactor with zero behaviour change.
- **Canonical model** (`ContentBlock`, `BlockProvenance`, `BBox`, graph edges,
  `AssetRef`) — sufficient; needs additive extension only.
- **Phase 4 answer association** — architecture matches the brief's §15/§16
  (structural → lexical → semantic, graph primary, LLM as adjudicator, never
  generator). Reuse as-is; it only needs correct question spans as input.
- **Phase 5 validation/calibration/escalation** — matches §19; extend, don't rebuild.
- **`reading_order.py`, `crossref.py`, `inline.py`** — sound.
- **Provider abstraction, cache store, metrics, eval hooks, Streamlit UI.**

## 6. What needs to change

| # | Change | Why |
|---|---|---|
| 1 | `DocumentParser` port + router | §5/§6; born-digital text layer currently discarded |
| 2 | Docling adapter (born-digital branch) | §7; measured win is structural, see `PHASE6_DESIGN.md` §M |
| 3 | Cache: never store an unvalidated result | §10; 3 poisoned entries on disk today |
| 4 | `finish_reason` / truncation capture | §9.D |
| 5 | Coverage validation (ink, zero-yield, numbering) | §9; confidence is 0.963 on a 16 %-empty page set |
| 6 | **`QuestionReconstructor` as a first-class stage** | §11; the central change |
| 7 | Span-based question model (join **and** split) | §4.1/§4.4 above |
| 8 | Continuation rewrite (transitive, type-agnostic) | §4.1/§4.2/§4.3 |
| 9 | Markers demoted to scored signals | §4.5/§4.6, §24 |
| 10 | Type classification decoupled from option parsing | §4.4, §13 |
| 11 | Gold eval: recall / completeness / tree accuracy | §22/§23 |
| 12 | Mistral OCR adapter, key-optional | §8 |

**Core design decision.** Replace block-anchored construction with a **span
model**: a logical question is a `(start_block, start_offset) → (end_block,
end_offset)` span over the reading-order sequence. This makes joining and
splitting the *same* operation — boundary placement — instead of two unrelated
mechanisms, and it makes "completeness" directly measurable against gold as span
overlap rather than string equality.

## 7. Implementation plan

Ordered per brief §26; each step independently verifiable, tests green throughout.

| Step | Work | Verification |
|---|---|---|
| 1–2 | Audit + baseline | **done** — this document; 16 tests pass |
| 3 | `parse/base.py` `DocumentParser`; wrap current path as `VlmPageParser`; `settings.parser` | pure refactor, all 16 tests + golds unchanged |
| 4 | `is_valid()` gate before `cache.put()`; `.cache/failures/`; purge script; bump `PROMPT_VERSION` | 3 poisoned entries gone; re-run recovers leph202 pp. 2/3/9 |
| 5 | `finish_reason` → `PageExtraction.truncated`; truncation = failure | unit test with a stubbed truncated response |
| 6 | `validate/coverage.py` — ink coverage, zero/low-yield, numbering continuity (per-series, advisory) | catches all 3 dead pages; no false positives on cl9ch11 |
| 7 | `parse/router.py` — per-page text-layer probe via PyMuPDF | probe already verified: cl9ch11 → 0/30 text pages |
| 8 | `parse/docling_parser.py` → `PageExtraction` | born-digital branch only; cl9ch11 path untouched |
| 9 | `parse/mistral_parser.py`, key-optional; benchmark harness | skips cleanly with no key |
| 10 | Normalise all parsers into `ContentBlock` at adapter boundary | golds stay green |
| 11 | **`reconstruct/` — `QuestionReconstructor`** | see below |
| 12 | Transitive, type-agnostic continuation | cross-page/column cases in gold |
| 13 | Sub-question tree: block-level **and** intra-block split | leph202 10.2 → 4 sub-questions |
| 14–15 | Re-point answer association at reconstructed spans | Phase-4 golds stay green |
| 16 | Confidence gate reads coverage, not self-reported confidence | — |
| 17 | Gold eval: recall, completeness, tree accuracy | needs user-supplied gold |
| 18–19 | Full run, both chapters, final JSON + Q&A PDF export | — |

### Step 11 — `QuestionReconstructor` design

Boundary-scoring over the reading-order sequence. For each adjacent pair, score
`P(same logical question)` from three independent signal families, kept separate
and individually inspectable (never collapsed into one opaque number):

- **Structural** — reading-order adjacency, same section/parent/example, column
  continuity, page continuation, geometric gap, indentation.
- **Marker** — `Example N`, `QN`, `N.`, `(a)`, `Ans.`, `Solution`, `OR`.
  Scored *evidence*, never a gate. A document with no markers must still work.
- **Semantic** — sentence-completion, dangling punctuation, whether the next
  block grammatically continues the previous. Deterministic first
  (punctuation/casing/connectives); batched text-only LLM **only** for pairs
  whose deterministic score falls in an ambiguous band.

Boundaries are placed where the score drops below threshold; spans between
boundaries become logical questions. Intra-block splitting uses the same scorer
over sentence/line offsets inside a block, which is what fixes leph202 §4.4.

Cost: deterministic for the overwhelming majority of pairs; LLM touches only the
ambiguous band, batched, cached — consistent with §20.

## 8. Test baseline

`python -m pytest tests/ -q` → **16 passed**, 1 unrelated warning, 18.87 s.

- `tests/test_acceptance.py` — 20 assertions over committed `cl9ch11` outputs
- `tests/test_frontend_regression.py` — 14 cases, offline `null` provider
- `tests/test_smoke.py`

Gold data present: `eval/gold/cl9ch11_{questions,answers,structure}.json`.
**Missing:** any gold for a second publisher.

These 16 tests are the regression net for Steps 3–10. They are **not** sufficient
for Steps 11–13, which need the gold sets in §22.

---

## Blockers

1. **`leph202.pdf` is not in the repository.** `find . -iname "*.pdf"` returns
   nothing; only `outputs/leph202/` exists. Brief §25 makes it the primary
   born-digital regression case, and it is the only evidence available for the
   born-digital branch (Steps 7–8) and the intra-block splitting fix (Step 13).
   Needed before Step 7 can be verified.
2. **Gold question sets** (§22) are user-supplied and gate Steps 17–19 and every
   claim in §23. Steps 3–16 can proceed without them.
3. **`MISTRAL_API_KEY`** — absent; Step 9 will ship the adapter and skip the live
   benchmark cleanly (§8 permits this).

## Note on §28 success criteria

Criteria A, B, I, K are achievable and verifiable in Steps 3–10. Criteria C–F
and L depend on the gold sets; per §30 I will not claim accuracy numbers the
gold evaluation has not actually produced.
