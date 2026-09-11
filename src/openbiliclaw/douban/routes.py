"""豆瓣书影音 API 路由。

Endpoints（prefix ``/api/douban``）：
- GET  /api/douban/items       — 书影音清单（?category=&status=&search=）
- GET  /api/douban/stats       — 分类×状态计数概览
- GET  /api/douban/analytics   — 统计画像（按年份/分类/分布聚合，纯数据）
- GET  /api/douban/insight     — 读缓存深度画像报告
- POST /api/douban/insight     — 生成（或 force 重生成）深度画像报告
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from openbiliclaw.douban.analytics import DoubanAnalytics
from openbiliclaw.douban.insight import generate_insight_report, load_cached_report
from openbiliclaw.douban.service import DoubanService
from openbiliclaw.douban.store import DoubanStore

CATEGORIES = ("movie", "book", "music")
STATUSES = ("collect", "wish", "do")


class InsightRequest(BaseModel):
    force: bool = False


def build_douban_router(*, config: Any, llm_service: object | None = None) -> APIRouter:
    """创建豆瓣路由。config 提供 ``[storage] douban_db_path``。

    ``llm_service`` 可选：提供后可生成深度画像报告（`POST /api/douban/insight`）。
    """
    router = APIRouter(prefix="/api/douban", tags=["douban"])

    _storage = getattr(config, "storage", None)
    db_path = getattr(_storage, "douban_db_path", "") or "data/douban.db"

    def _service() -> DoubanService:
        return DoubanService(db_path)

    def _analytics() -> DoubanAnalytics:
        return DoubanAnalytics(DoubanStore(db_path))

    @router.get("/items")
    def list_items(
        category: str = Query("", max_length=20),
        status: str = Query("", max_length=20),
        search: str = Query("", max_length=200),
        limit: int = Query(200, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ) -> dict[str, Any]:
        cat = category if category in CATEGORIES else None
        st = status if status in STATUSES else None
        return _service().items(cat, st, search.strip() or None, limit=limit, offset=offset)

    @router.get("/stats")
    def stats() -> dict[str, Any]:
        return _service().stats()

    @router.get("/analytics")
    def analytics() -> dict[str, Any]:
        """统计画像：纯数据聚合（不调 LLM）。"""
        return _analytics().full_report()

    @router.get("/insight")
    def get_insight() -> dict[str, Any]:
        """读缓存深度画像报告。"""
        return load_cached_report()

    @router.post("/insight")
    async def post_insight(body: InsightRequest) -> dict[str, Any]:
        """生成深度画像报告；force=true 时忽略缓存重生成。"""
        store = DoubanStore(db_path)
        return await generate_insight_report(llm_service, store, force=body.force)

    return router
