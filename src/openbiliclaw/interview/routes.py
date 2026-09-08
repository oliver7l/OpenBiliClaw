"""求职面试备战 API 路由。

Endpoints（prefix ``/api/interview``）：
- GET  /api/interview/status      — 根目录配置与系统总览
- GET  /api/interview/jobs        — 岗位列表（?keyword=）
- GET  /api/interview/search      — 全文检索（?q=&limit=）
- GET  /api/interview/numbers     — 真实数字表（?keyword=）
- GET  /api/interview/projects    — 项目库（?keyword=）
- GET  /api/interview/card/{company} — 某公司速记卡
- GET  /api/interview/directions  — 方向知识库清单
- GET  /api/interview/index       — 全库文件索引（?keyword=&layer=）
- GET  /api/interview/logs        — 面试日志（最新在前）
- POST /api/interview/logs        — 追加面试日志 {company, round, points}
- POST /api/interview/scaffold    — 新岗位建档 {company, role}

数据目录由 ``[interview] root`` 配置（见 config.toml），引擎只读检索；
仅日志追加与新岗位建档两个 POST 会写入引擎数据目录。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from openbiliclaw.interview.engine import InterviewEngine, resolve_root

logger = logging.getLogger(__name__)


class InterviewLogIn(BaseModel):
    company: str = Field(..., min_length=1, max_length=100)
    round: str = Field(..., min_length=1, max_length=50)
    points: str = Field(..., min_length=1, max_length=2000)


class InterviewScaffoldIn(BaseModel):
    company: str = Field(..., min_length=1, max_length=100)
    role: str = Field(..., min_length=1, max_length=100)


def build_interview_router(*, root: str | None = None) -> APIRouter:
    """创建 interview 路由。root 为空时引擎按默认路径解析。"""
    router = APIRouter(prefix="/api/interview", tags=["interview"])
    engine = InterviewEngine(resolve_root(root))

    def _ready() -> None:
        if not engine.is_configured:
            raise HTTPException(
                status_code=404,
                detail=f"求职知识库未配置或目录不存在: {engine.root}（请检查 [interview] root）",
            )

    @router.get("/status")
    def interview_status() -> dict[str, Any]:
        """系统总览：根目录、是否配置、岗位/项目/数字/日志统计。"""
        return engine.overview()

    @router.get("/jobs")
    def interview_jobs(keyword: str | None = None) -> dict[str, Any]:
        """岗位列表，支持公司/岗位/主打方向关键词过滤。"""
        _ready()
        rows = engine.jobs(keyword)
        return {"total": len(rows), "items": rows}

    @router.get("/search")
    def interview_search(q: str, limit: int = 40) -> dict[str, Any]:
        """全文检索（02/03 + 腾讯文档资料 + 解码文本）。"""
        _ready()
        if not q.strip():
            raise HTTPException(status_code=422, detail="检索关键词不能为空")
        limit = max(1, min(limit, 200))
        hits = engine.search(q, max_hits=limit)
        return {"keyword": q, "total": len(hits), "items": hits}

    @router.get("/numbers")
    def interview_numbers(keyword: str | None = None) -> dict[str, Any]:
        """真实数字表（口径权威源）。"""
        _ready()
        rows = engine.numbers(keyword)
        return {"total": len(rows), "items": rows}

    @router.get("/projects")
    def interview_projects(keyword: str | None = None) -> dict[str, Any]:
        """项目库。"""
        _ready()
        rows = engine.projects(keyword)
        return {"total": len(rows), "items": rows}

    @router.get("/card/{company}")
    def interview_card(company: str) -> dict[str, Any]:
        """某公司面试速记卡（岗位+数字+项目+题库入口）。"""
        _ready()
        try:
            return engine.speed_card(company)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/directions")
    def interview_directions() -> dict[str, Any]:
        """02_方向知识库 方向及文档清单。"""
        _ready()
        rows = engine.directions()
        return {"total": len(rows), "items": rows}

    @router.get("/index")
    def interview_index(keyword: str | None = None, layer: str | None = None) -> dict[str, Any]:
        """全库文件索引查询（knowledge.db / CSV 回退）。"""
        _ready()
        if layer is not None and layer not in ("01", "02", "03"):
            raise HTTPException(status_code=422, detail="layer 仅支持 01/02/03")
        rows = engine.index(keyword, layer)
        return {"total": len(rows), "items": rows}

    @router.get("/logs")
    def interview_logs() -> dict[str, Any]:
        """面试日志列表（最新在前）。"""
        _ready()
        rows = engine.logs()
        return {"total": len(rows), "items": rows}

    @router.post("/logs", status_code=201)
    def interview_log_add(payload: InterviewLogIn) -> dict[str, Any]:
        """追加一条面试日志（只追加，不修改历史行）。"""
        _ready()
        try:
            new_id = engine.add_log(payload.company, payload.round, payload.points)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"写入面试日志失败: {exc}") from exc
        return {"id": new_id, "company": payload.company, "round": payload.round}

    @router.post("/scaffold", status_code=201)
    def interview_scaffold(payload: InterviewScaffoldIn) -> dict[str, Any]:
        """按统一规范新建岗位备战包目录（01/02/03 三件套）。"""
        _ready()
        try:
            result = engine.scaffold(payload.company, payload.role)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=500, detail=f"新建岗位目录失败: {exc}") from exc
        return result

    return router


__all__ = [
    "build_interview_router",
    "InterviewLogIn",
    "InterviewScaffoldIn",
]
