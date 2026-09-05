# Phase 4 — Hybrid Retrieval & Answer/Solution Association (design)

Goal: for each normalized question, identify the corresponding answer/solution
**from the document** — or report `not_in_document`. The LLM is an evidence
*adjudicator*, never an answering engine. No Phase-5 calibration/retry. No graph DB.

## A. Architecture
Pipeline node `associate_answers` (after `discover_questions`):
`candidate generation → merge/rerank → decision (deterministic | LLM adjudication) → ANSWERED_BY`.
Runs over **leaf** questions (the unit that has an answer); case-study roots carry
none directly (their subparts do).

## B. Retrieval design (hybrid, structural-first)
Five channels merged per question (`retrieve/`):
1. **inline** — Phase-3 `answer_inline` (same block).
2. **structural** (`structural.py`, graph): same-container enumerator match (case A→answer A), a combined `Ans.(A)…(D)` block for every subpart, the adjacent-solution run (skips a referenced figure between Q and Ans., stops at the next question), referenced table/equation, same-section fallback, continuation-expansion.
3. **continuation** — Phase-2 `CONTINUES` (page-spanning answers).
4. **lexical** — in-house BM25 (`lexical.py`).
5. **semantic** — Chroma vector index over blocks, embeddings via `provider.embed` (model-agnostic), cached by text hash (`semantic.py`).
The graph stays primary; Chroma is only an index. Every candidate keeps block id, type, page, section, provenance, channels, and per-channel scores.

## C. Candidate scoring / reranking
`rerank = structural_strength` when structural, else `(0.5·semantic + 0.3·lexical)·0.7`
— so a cross-section semantic hit can never outrank an in-scope structural one.
Top-k (6) go forward. **Decision policy (false-positive-averse):**
- inline → matched/present (no model call);
- no in-scope structural candidate → `not_in_document` (no model call) ← QR self-assessment;
- a **unique dominant** structural candidate (e.g. enumerator match) → matched deterministically;
- otherwise → LLM adjudicates among the in-scope candidates.
A positive association ALWAYS requires in-scope structural/reference evidence (guard).

## D. Evidence schema (`schemas/answers.py`)
Signals are kept SEPARATE, never collapsed: `AnswerEvidence`{channels, relationship,
scope, structural_strength, lexical_score, semantic_score, rerank_score};
`AnswerSignals`{structural, lexical, semantic, rerank, llm_confidence, final_confidence};
`AnswerAssociation`{status, answer_state, method, answer_block_ids, answer_kind,
answer_preview (extracted, never generated), evidence[], signals, llm}.

## E. LLM association (`prompts/answer_associate.md`, strict Pydantic)
The model is told it is an evidence-association component, given the question +
metadata + candidate blocks (id/type/relationship/page/text), and asked *which
candidate block(s), if any, contain the answer*. Returns `{status ∈
matched|partial|ambiguous|not_in_document, answer_block_ids, evidence_block_ids,
reason, llm_confidence}`. It never writes an answer. Batched (4/call), cached by
payload hash. A `matched` whose blocks fall outside the in-scope set is downgraded
to `ambiguous` (guard).

## F. Validation
- Guard: matched/partial answer blocks must be in-scope structural/reference → else counted as a **false association** (target 0).
- `answer_preview` is extracted block text → **unsupported/generated answers** target 0.
- `ANSWERED_BY` edges carry the justifying evidence.

## G. Evaluation
`AnswerHook` (by_status/state/method, evidence-source distribution, answered_by
edges, false_associations, unsupported) + `AnswerGoldHook` over
`eval/gold/cl9ch11_answers.json` (Ex 2/3 inline; Ex 6 solution; Ex 8 answer-as-table;
Ex 1(A); SA Q1 not_in_document; self-assessment→not_in_document count; solved→matched
count; zero false associations).

## H. Cost on the 30-page sample (measured/estimated)
Incremental Phase-4 API calls: ~4–5 embedding calls (≈220 short texts, batched 100)
+ ~9 adjudication calls (34 questions ÷ 4/batch). ≈ 40–60k tokens total; the 30
vision calls are Phase-1 (cached). All retrieval embeddings and LLM adjudications
are cached by hash → re-runs issue **0** new calls. Bounded: ≤6 candidates/question,
LLM only for genuinely ambiguous ones (~1/3 of leaves).
