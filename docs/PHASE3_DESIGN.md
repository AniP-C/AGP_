# Phase 3 — Question Discovery & Normalization (design)

Goal: reliably discover and normalize **every logical question/problem/example**
in the whole chapter into a `questions[]` tree. `source_type` and `question_type`
are independent. **No cross-block answer association** — an answer is represented
only when it lives inside the question's own block/inline segment. No RAG.

## A. Design & data-flow
Inserted in the LangGraph pipeline after `build_evidence`:

```
build_evidence → discover_questions( context → build (deterministic) → LLM refine ) → evaluate
```

1. **Context** (`discover/context.py`) — one reading-order pass computes each
   block's *region* (→ source_type) and the *section-banner marks* in force
   (→ marks inheritance). Structural banners are evidence, not a section filter.
2. **Deterministic build** (`discover/builder.py`) — from the Phase-2 structure:
   - roots = `container` blocks + top-level `question`/`worked_example`/`activity`;
   - sub_questions = `part_of` children (recursive; excludes answer blocks);
   - stem/passage folded into a case-study root;
   - `source_type` decided at the root, inherited by subparts;
   - `question_type` via a rule cascade (`classify.py`): assertion_reason → mcq →
     fill_blank → true_false → matching → distinguish → ordering → numerical →
     activity → descriptive(→short/long by marks, else conceptual) → unknown;
   - options parsed for MCQs; `internal_choice` from Phase-2 OR groups;
   - marks (explicit tag → section banner), bloom, source refs kept independent;
   - inline `Ans.` split via Phase-2 `inline_segments` (same block only);
   - continuation + descendant pages aggregated → question spans multiple pages;
   - related_assets from resolved `refers_to` + child assets; full provenance.
3. **LLM refine** (`discover/llm_refine.py`, provider-agnostic, cached) — the only
   model use: (a) reclassify `unknown` types, (b) detect genuine in-text prose
   questions ("?" blocks the structure missed). Degrades to deterministic on
   absence/failure; cached by prompt hash for reproducibility.

## B. Rules vs LLM
Deterministic does the discovery + structure + metadata + ~89% of typing. The LLM
touches only the residue (13 `unknown` → 0) and in-text detection. Provider
abstraction unchanged; `NullProvider` path is fully deterministic (offline).

## C. Output schema (`schemas/questions.py`)
`Question`: id · source_type · question_type · bloom_level · question_number ·
question_text · options[] · **recursive** sub_questions[] · internal_choice ·
marks{value,source} · source_refs[] · related_assets[] · answer_inline (same-block
only) · provenance{source_pages, printed_pages, source_blocks, reading_order,
question_char_span} · evaluation{discovery_confidence, classification_confidence,
classification_source, type_evidence}. Emitted to `outputs/<doc>/questions.json`;
also on `CanonicalDocument.questions`.

## D. Expected failure cases
- **Blank + options** → classified `mcq` (the sample has no pure fill_blank).
- **Enumerator collisions** ((A)/(a)/(1)/(Understand)) → handled in Phase-2 parsing
  + bloom regex; options parsed in-block.
- **Shared A-R rubric** ("(a) Both (A) and (R)…") typed as `question` → surfaces as
  a duplicate (the eval flags it; DEFER a rubric filter).
- **In-text rhetorical questions** → deliberately conservative (LLM judges); risk of
  under-detection over false positives.
- **Orphaned subparts** whose container wasn't detected → become `in_text` roots.

## E. Evaluation
- `QuestionHook` — totals, by source_type/question_type, marks-by-source, bloom,
  source refs, inline answers, OR groups, nesting depth, **duplicate** &
  **missed-container** checks, provenance coverage.
- `QuestionGoldHook` — sample-derived checks in
  `eval/gold/cl9ch11_questions.json` (Example 1 A–E case_study; Ex 2/3 inline; Ex 6
  numerical; Ex 8 distinguish; Ex 10 case_study; SA Q1 mcq/1-mark; SA Q12 5-marks;
  the blank-as-MCQ item; existence of assertion_reason / numerical / OR /
  banner-marks).
