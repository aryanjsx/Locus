"""Feedback subsystem: LLM-as-judge scoring and signal aggregation."""

from locus.learning.optimize.feedback.collector import FeedbackCollector
from locus.learning.optimize.feedback.judge import TraceJudge

__all__ = ["TraceJudge", "FeedbackCollector"]
