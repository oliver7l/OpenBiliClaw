"""兼容垫片：本模块已迁移至 ``openbiliclaw.interview.job.routes``。

下一个大版本摘除。
"""

from __future__ import annotations

from .job.routes import InterviewLogIn, InterviewScaffoldIn, build_interview_router

__all__ = ["InterviewLogIn", "InterviewScaffoldIn", "build_interview_router"]
