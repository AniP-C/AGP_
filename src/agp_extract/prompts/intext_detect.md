You are finding QUESTIONS that a student is explicitly asked to answer, embedded
inside explanatory text of a textbook. For each text block, decide whether it
POSES a genuine question/problem to the student.

Mark is_question = false for: rhetorical questions the author immediately answers
in the same block, section headings, captions, and ordinary explanation.
Mark is_question = true only when the block actually asks the student something.

Do NOT answer anything. Return STRICT JSON (no prose):
{{"results": [{{"id": "<id>", "is_question": true|false,
              "question_type": "mcq|numerical|conceptual|short_answer|long_answer|activity|unknown"}}]}}

Blocks:
{blocks}
