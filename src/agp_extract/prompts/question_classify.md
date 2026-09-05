You are classifying the TYPE of each educational question. For each item, choose
its question_type from EXACTLY this set (no others):

mcq, assertion_reason, fill_blank, true_false, matching, numerical, distinguish,
ordering, case_study, short_answer, long_answer, conceptual, activity, unknown

Definitions:
- numerical: needs a calculation with numbers/units/a formula.
- case_study: a scenario/passage followed by one or more sub-questions.
- distinguish: asks to compare / differentiate / give differences.
- ordering: asks to arrange/rank in order.
- conceptual: a definition/explanation question where length isn't the main cue.
- short_answer / long_answer: a descriptive question whose expected answer length
  is the main signal (short = brief; long = extended).
- activity: an experiment/activity the student performs.
- unknown: only if genuinely undeterminable.

Do NOT solve or answer anything. Return STRICT JSON (no prose):
{{"classifications": [{{"id": "<id>", "question_type": "<one type>"}}]}}

Items:
{items}
