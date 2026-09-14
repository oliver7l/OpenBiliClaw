"""兼容垫片：本模块已迁移至 ``openbiliclaw.interview.job.engine``。

期 3「代码分层」把 interview 拆为 job / study / review 三个子包；
本文件仅为旧导入路径保活，下一个大版本摘除。
"""

from __future__ import annotations

from .job.engine import (
    DEFAULT_INTERVIEW_ROOT,
    ENV_INTERVIEW_ROOT,
    MAX_SEARCH_BYTES,
    SEARCH_EXTS,
    SEARCH_REL_DIRS,
    InterviewEngine,
    resolve_root,
)

__all__ = [
    "DEFAULT_INTERVIEW_ROOT",
    "ENV_INTERVIEW_ROOT",
    "SEARCH_REL_DIRS",
    "MAX_SEARCH_BYTES",
    "SEARCH_EXTS",
    "InterviewEngine",
    "resolve_root",
]
