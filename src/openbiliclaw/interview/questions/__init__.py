"""兼容垫片：本包已迁移至 ``openbiliclaw.interview.study``。

下一个大版本摘除。
"""

from __future__ import annotations

from ..study import (
    DailyProgress,
    InterviewQuestionStore,
    MasteryLevel,
    Question,
    QuestionCategory,
    QuestionCreate,
    ReadingPlan,
    ReadingRecord,
)

__all__ = [
    "InterviewQuestionStore",
    "Question",
    "QuestionCreate",
    "ReadingRecord",
    "ReadingPlan",
    "DailyProgress",
    "MasteryLevel",
    "QuestionCategory",
]
