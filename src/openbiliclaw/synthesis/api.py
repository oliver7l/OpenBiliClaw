"""迭代合成模块 API 路由。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .engine import SynthesisEngine
from .models import SynthesisConfig
from .store import SynthesisStore

logger = logging.getLogger(__name__)


def create_synthesis_router(
    db_path: str = "data/openbiliclaw.db",
    llm_service: Any = None,
) -> APIRouter:
    """创建迭代合成模块的 API 路由。"""
    router = APIRouter(prefix="/api/synthesis", tags=["synthesis"])

    def _get_store() -> SynthesisStore:
        return SynthesisStore(db_path)

    def _get_engine() -> SynthesisEngine:
        config = SynthesisConfig(main_db_path=db_path)
        return SynthesisEngine(
            config=config,
            llm_service=llm_service,
            store=SynthesisStore(db_path),
        )

    @router.get("/status")
    async def synthesis_status():
        """查询合成系统状态。"""
        store = _get_store()
        state = store.get_state()
        latest = store.get_latest_version()
        return JSONResponse(
            {
                "ok": True,
                "state": state.model_dump(),
                "latest_version": latest.model_dump() if latest else None,
            }
        )

    @router.get("/versions")
    async def list_versions(limit: int = 20):
        """列出所有合成版本。"""
        store = _get_store()
        versions = store.list_versions(limit=limit)
        return JSONResponse(
            {
                "ok": True,
                "versions": [v.model_dump() for v in versions],
            }
        )

    @router.get("/versions/{version}")
    async def get_version(version: int):
        """获取指定版本的合成结果。"""
        store = _get_store()
        v = store.get_version(version)
        if v is None:
            return JSONResponse({"ok": False, "error": "version not found"}, status_code=404)
        return JSONResponse({"ok": True, "version": v.model_dump()})

    @router.post("/run")
    async def run_synthesis():
        """触发一次迭代合成。

        增量运行：只处理上次合成以来的新增数据，并与前次结果融合。
        """
        engine = _get_engine()
        result = await engine.run()
        if result is None:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "synthesis skipped (no new data or no LLM service)",
                }
            )
        return JSONResponse(
            {
                "ok": True,
                "version": result.model_dump(),
            }
        )

    @router.get("/pending")
    async def pending_count():
        """查看待处理的新数据量。"""
        store = _get_store()
        state = store.get_state()
        diary_new = len(store.get_new_diary_analyses(state.last_diary_analysis_id, limit=500))
        chat_insight_new = len(store.get_new_chat_insights(state.last_chat_insight_id, limit=500))
        chat_topic_new = len(store.get_new_chat_topics(state.last_chat_topic_id, limit=500))
        return JSONResponse(
            {
                "ok": True,
                "pending": {
                    "diary_analyses": diary_new,
                    "chat_insights": chat_insight_new,
                    "chat_topics": chat_topic_new,
                    "total": diary_new + chat_insight_new + chat_topic_new,
                },
            }
        )

    return router
