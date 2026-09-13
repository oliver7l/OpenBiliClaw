"""面试复盘记录 API 路由。

Endpoints（prefix ``/api/interview/reviews``）：
- GET    /api/interview/reviews          — 列表（?company=&result=&limit=&offset=）
- GET    /api/interview/reviews/{id}     — 详情
- POST   /api/interview/reviews          — 创建
- PATCH  /api/interview/reviews/{id}     — 更新
- DELETE /api/interview/reviews/{id}     — 删除
- GET    /api/interview/reviews/search   — 全文检索（?q=）
- GET    /api/interview/reviews/stats    — 统计
- GET    /api/interview/reviews/companies — 公司列表
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from openbiliclaw.interview.review_models import (
    InterviewReview,
    InterviewReviewCreate,
    InterviewReviewStats,
    InterviewReviewSummary,
    InterviewReviewUpdate,
)
from openbiliclaw.interview.review_service import InterviewReviewService

logger = logging.getLogger(__name__)


def _default_db_path() -> str:
    """默认数据库路径：data/interview.db（面试复盘子库，独立锁域）。"""
    project_root = Path(__file__).resolve().parents[3]
    try:
        from openbiliclaw.config import load_config

        settings = load_config()
        if settings.storage.interview_db_path:
            p = Path(settings.storage.interview_db_path)
            return str(p if p.is_absolute() else project_root / p)
    except Exception:  # noqa: BLE001 - 配置加载失败时回退默认路径
        pass
    return str(project_root / "data" / "interview.db")


def build_review_router(db_path: str | None = None) -> APIRouter:
    """创建面试复盘路由。"""
    router = APIRouter(prefix="/api/interview/reviews", tags=["interview-review"])
    svc = InterviewReviewService(db_path or _default_db_path())

    @router.get("", response_model=list[InterviewReviewSummary])
    @router.get("/", response_model=list[InterviewReviewSummary], include_in_schema=False)
    def list_reviews(
        company: str | None = Query(None, description="按公司过滤"),
        result: str | None = Query(None, description="按结果过滤"),
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> list[InterviewReviewSummary]:
        """列出面试复盘记录（摘要）。"""
        return svc.list_reviews(company=company, result=result, limit=limit, offset=offset)

    @router.get("/search", response_model=list[InterviewReviewSummary])
    def search_reviews(
        q: str = Query(..., min_length=1, description="检索关键词"),
        limit: int = Query(20, ge=1, le=100),
    ) -> list[InterviewReviewSummary]:
        """全文检索面试复盘记录。"""
        return svc.search(q, limit=limit)

    @router.get("/stats", response_model=InterviewReviewStats)
    def review_stats() -> InterviewReviewStats:
        """面试复盘统计。"""
        return svc.stats()

    @router.get("/companies", response_model=list[str])
    def review_companies() -> list[str]:
        """所有面试过的公司。"""
        return svc.get_companies()

    @router.get("/{review_id}", response_model=InterviewReview)
    def get_review(review_id: int) -> InterviewReview:
        """获取面试复盘详情。"""
        r = svc.get(review_id)
        if r is None:
            raise HTTPException(status_code=404, detail=f"面试复盘 #{review_id} 不存在")
        return r

    @router.post("", response_model=InterviewReview, status_code=201)
    @router.post("/", response_model=InterviewReview, status_code=201, include_in_schema=False)
    def create_review(payload: InterviewReviewCreate) -> InterviewReview:
        """创建面试复盘记录。"""
        return svc.create(payload)

    @router.patch("/{review_id}", response_model=InterviewReview)
    def update_review(
        review_id: int, payload: InterviewReviewUpdate
    ) -> InterviewReview:
        """更新面试复盘记录（部分更新）。"""
        r = svc.update(review_id, payload)
        if r is None:
            raise HTTPException(status_code=404, detail=f"面试复盘 #{review_id} 不存在")
        return r

    @router.delete("/{review_id}")
    def delete_review(review_id: int) -> dict[str, Any]:
        """删除面试复盘记录。"""
        if not svc.delete(review_id):
            raise HTTPException(status_code=404, detail=f"面试复盘 #{review_id} 不存在")
        return {"deleted": True, "id": review_id}

    return router


__all__ = ["build_review_router"]
