"""面试域模块（interview）——三个相互独立的子系统。

期 2 URL 分区后三者各有前缀：A ``/api/interview/job``、B ``/api/interview/study``、
C ``/api/interview/review``（历史前缀 ``/api/interview`` 与 ``/api/interview/reviews``
作为双挂载别名保留一版）。

- **A 岗位备战**（``job/``）：读三层求职知识库——岗位/检索/数字/项目/速记卡/索引/日志/建档。
  数据 = ``求职知识库/`` 文件 + ``knowledge.db`` + ``interview.db`` 裸表族 + ``resume.db``。
- **B 题目研习**（``study/``）：题库/待看队列/阅读计划/掌握度/统计/反问话术/岗位题库。
  数据 = ``interview_questions.db``(iq_*) + ``interview.db``(interview_questions /
  ammo_* / interview_rebuttals)。
- **C 面试复盘**（``review/``）：转录/AI 评价/情绪复盘/技术复盘/行动计划。
  数据 = ``interview.db``(interview_reviews)。

- **A 引擎**：``InterviewEngine`` 封装检索能力，CLI（``openbiliclaw interview``）与
  API（``/api/interview/job``）共用同一引擎。
- **A 数据源**：外部三层求职知识库（默认项目内 ``求职知识库/``，见 ``[interview] root``）
  = 01_原始资料库（只读源）/ 02_方向知识库（方法论）/ 03_岗位弹药库（按岗位备战包），
  由 ``_系统_知识库引擎``（CSV 数据表 + ``knowledge.db``）驱动。
- **C 复盘**：``InterviewReviewService`` 管理面试后的结构化复盘记录。
- **统一 CLI 入口**：``cli.py``（跨 A + C）。

原始材料始终保留在原目录，模块只读检索；仅「面试日志追加」与「新岗位建档」
两类显式操作会写入引擎数据目录。复盘记录存储在 ``data/interview.db``。

架构总览见 ``docs/modules/interview-overview.md``。
"""

from __future__ import annotations

from .job.engine import (
    DEFAULT_INTERVIEW_ROOT,
    ENV_INTERVIEW_ROOT,
    InterviewEngine,
    resolve_root,
)
from .review.models import (
    InterviewReview,
    InterviewReviewCreate,
    InterviewReviewStats,
    InterviewReviewSummary,
    InterviewReviewUpdate,
)
from .review.routes import build_review_router, mount_review_router
from .review.service import InterviewReviewService
from .review.store import InterviewReviewStore

__all__ = [
    "DEFAULT_INTERVIEW_ROOT",
    "ENV_INTERVIEW_ROOT",
    "InterviewEngine",
    "resolve_root",
    # 复盘记录
    "InterviewReview",
    "InterviewReviewCreate",
    "InterviewReviewUpdate",
    "InterviewReviewSummary",
    "InterviewReviewStats",
    "InterviewReviewService",
    "InterviewReviewStore",
    "build_review_router",
    "mount_review_router",
]
