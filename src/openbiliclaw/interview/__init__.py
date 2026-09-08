"""求职面试备战模块（interview）。

把「三层求职知识库」接入 OpenBiliClaw 作为统一入口：

- 数据源：外部求职知识库（默认项目内 ``求职知识库/``，见 ``[interview] root``），
  三层结构 = 01_原始资料库（底层只读源）/ 02_方向知识库（中层方法论）/
  03_岗位弹药库（上层按岗位备战包），由 _系统_知识库引擎（CSV 数据表 +
  knowledge.db）驱动。
- 引擎：``InterviewEngine`` 封装岗位/检索/数字/项目/速记卡/索引/日志等操作，
  CLI（``openbiliclaw interview``）与 API（``/api/interview``）共用同一引擎。

原始材料始终保留在原目录，模块只读检索；仅「面试日志追加」与「新岗位建档」
两类显式操作会写入引擎数据目录。
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
