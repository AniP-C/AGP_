"""Question reconstruction — complete logical questions from fragmented blocks."""
from .spans import QuestionSpan, SpanSegment
from .reconstructor import QuestionReconstructor
from . import signals

__all__ = ["QuestionSpan", "SpanSegment", "QuestionReconstructor", "signals"]
