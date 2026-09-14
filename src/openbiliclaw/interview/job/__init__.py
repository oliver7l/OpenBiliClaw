"""A · 岗位备战（job-prep）。

把「三层求职知识库」接入 OpenBiliClaw：岗位信息、全文检索、真实数字、项目库、
面试速记卡、全库索引、面试日志、新岗位建档。CLI 与 API 共用同一引擎。

数据源：``求职知识库/`` 文件 + ``knowledge.db``（L2 加工层）+
``interview.db`` 裸表族 + ``resume.db``（投递域）。

- 引擎：``engine.InterviewEngine``
- 路由：``routes.build_interview_router``（无 prefix 本体）+ ``routes.mount_interview_router``
  （挂到 ``/api/interview/job``，并兼容旧 ``/api/interview``）
- 文档：``docs/modules/interview.md``
"""

from __future__ import annotations

from .engine import (
    DEFAULT_INTERVIEW_ROOT,
    ENV_INTERVIEW_ROOT,
    InterviewEngine,
    resolve_root,
)

__all__ = [
    "DEFAULT_INTERVIEW_ROOT",
    "ENV_INTERVIEW_ROOT",
    "InterviewEngine",
    "resolve_root",
]
