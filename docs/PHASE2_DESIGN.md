# Phase 2 — Structure & Relationship Reconstruction (design)

Scope: turn the flat, typed, provenance-rich blocks from Phase 1 into a
**structured, related** document — reading order, cross-references, container
nesting, continuation, and inline annotation. **Deterministic only** (no LLM, no
RAG, no `questions[]`). Nothing is fabricated: weak/ambiguous relationships are
left explicitly unresolved.

## A. Design
Phase 2 is a deterministic pass (`structure/`) inserted into the LangGraph
pipeline between `assemble` and `build_graph`:

```
assemble → structure( reading_order → grouping → inline → continuation → crossref ) → build_graph
```

1. **Reading-order hardening** (`reading_order.py`) — reconstruct columns / gutter
   / full-width bands deterministically; correct each block's `column` from
   geometry; cross-check the model's order against a geometric order and record a
   per-page `reading_order_confidence`. The model's (audited-correct) order stays
   authoritative; geometry validates it and flags disagreements.
2. **Cross-reference resolution** (`crossref.py`) — resolve "as shown in figure",
   "the table above", "diagram (c)" to a concrete asset by *label → direction →
   proximity*, scoped to same page/section. Ambiguous / no-candidate ⇒ left
   unresolved with a status, never guessed.
3. **Structural grouping** (`grouping.py`) — parse enumerators `(A)/(a)/(i)/(1)`
   and `Example N`; materialize `part_of` (container → subpart, nested); detect
   OR internal-choice groups **only around an explicit "OR" marker block**.
4. **Continuation** (`continuation.py`) — link a fragment continuing across a
   column/page break (paragraph cut at the foot; table split by row-number
   continuity), skipping page furniture. Both source blocks are preserved and
   joined by `continues_to`/`continuation_of` + a shared `logical_item_id`.
5. **Inline structures** (`inline.py`) — annotate a block that folds question +
   `Ans.` + explanation with role spans + exact offsets, so Phase 3 can split it.
   We annotate; we never split or extract.

## B. Data-model changes (additive; Phase-1 contract untouched)
- `schemas/structure.py` (new): `BlockStructure` (part_of_id, group_role,
  enumerator[_style], depth, or_group_id, continuation_of/continues_to,
  logical_item_id, inline_answer, inline_segments) · `InlineSegment` · `PageLayout`.
- `ContentBlock` += `structure: BlockStructure`.
- `UnresolvedRef` += `status`, `resolution_confidence`, `resolution_method`.
- `Page` += `layout: PageLayout`.
- `EdgeType` += `CONTINUES`, `OR_ALTERNATIVE`; `PART_OF`/`REFERS_TO` now
  materialized (were reserved). `ANSWERED_BY` stays reserved for Phase 4.

## C. Rules vs LLM
**100% deterministic** — geometry (columns/bands/continuation), regex
(enumerators, OR markers, inline markers, ref labels), and graph reads
(proximity/section scoping). No LLM call is made; the provider abstraction is
untouched. Rationale: structure is verifiable and reproducible, and per the
Phase-1 audit the model's reading order was already sound — so Phase 2 spends no
tokens and stays fully replayable. (An LLM disambiguator for ambiguous refs is a
possible *future* escalation; Phase 2 leaves them unresolved instead.)

## D. Expected failure cases (and handling)
- **Bad bbox** (audit's `y0=199` outlier) → a pure geometric order would
  misplace it; we keep the model order and only *flag* low agreement. 
- **Enumerator collisions** ((a) option vs (A) subpart vs (i) roman) → roman
  matched before lower; MCQ options live inside one block here, limiting risk.
- **Q vs A share labels** in case-based items → do NOT use duplicate labels for
  OR (that was a bug); require an explicit "OR" marker.
- **Ambiguous refs** ("diagrams (a) and (b)") → multiple label matches ⇒ left
  unresolved (`ambiguous`), never guessed.
- **Continuation false positives** → require type match + column/page crossing +
  bottom→top geometry + (mid-sentence | row-number continuity); furniture skipped.

## E. Evaluation
- `StructureHook` — reading-order confidence, grouping counts, OR groups,
  continuation links, cross-ref resolution rate, and materialized edge counts.
- `StructureGoldHook` — sample-derived semantic checks in
  `eval/gold/cl9ch11_structure.json` (text-matched, id-robust): Example 6/7 →
  solution ordering; Example 1 A–E `part_of`; Loudness table 320→321
  continuation; bell-jar ref resolution; Example 2 inline; page-320 two-column.
