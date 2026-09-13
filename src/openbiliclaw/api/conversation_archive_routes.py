"""对话归档 API 路由：列出 / 查看 / 统计 / 导入 用户与 AI 的对话内容。

通过 ``register_conversation_archive_routes(app, ctx)`` 注册。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

from openbiliclaw.conversation_archive.store import ConversationArchiveStore

# 收藏库原始文件目录（单一数据源）
LIBRARY_DIR = Path(__file__).resolve().parents[3] / "notes" / "阅读收藏库"

if TYPE_CHECKING:
    from fastapi import FastAPI

    from openbiliclaw.api.runtime_context import RuntimeContext

logger = logging.getLogger(__name__)

_service: ConversationArchiveStore | None = None


def register_conversation_archive_routes(app: FastAPI, ctx: RuntimeContext) -> None:
    def _get_service() -> ConversationArchiveStore | None:
        """获取或创建归档存储实例（懒加载）。"""
        global _service
        if _service is not None:
            return _service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        _service = ConversationArchiveStore(database=database)
        return _service

    @app.get("/api/conversation-archive")
    def conversation_archive_list(
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "seq",
        sort_order: str = "ASC",
    ) -> JSONResponse:
        """列出对话归档条目，支持全文搜索与排序。"""
        svc = _get_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_items(
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": items,
                "total": svc.count_items(),
                "limit": max(1, min(int(limit), 200)),
                "offset": max(0, int(offset)),
            }
        )

    @app.get("/api/conversation-archive/{item_id:int}/raw-md")
    def conversation_archive_raw_md(item_id: int) -> FileResponse:
        """返回该条目对应的收藏库原始 md 文件（三件套闭环：前端→API→md）。"""
        svc = _get_service()
        if svc is None:
            raise HTTPException(status_code=503, detail="database unavailable")
        item = svc.get_item(item_id)
        md_file = (item or {}).get("md_file") or ""
        if not md_file:
            raise HTTPException(status_code=404, detail="no source md")
        base = LIBRARY_DIR.resolve()
        target = (base / md_file).resolve()
        if target.parent != base or not target.is_file():
            raise HTTPException(status_code=404, detail="md file not found")
        return FileResponse(target, media_type="text/markdown; charset=utf-8", filename=md_file)

    @app.get("/api/conversation-archive/stats")
    def conversation_archive_stats() -> JSONResponse:
        """归档统计。"""
        svc = _get_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        return JSONResponse({"ok": True, "stats": svc.get_stats()})

    @app.get("/api/conversation-archive/{item_id:int}")
    def conversation_archive_get(item_id: int) -> JSONResponse:
        """获取单条归档。"""
        svc = _get_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        item = svc.get_item(item_id)
        if item is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "item": item})

    @app.post("/api/conversation-archive")
    def conversation_archive_create(body: dict[str, Any]) -> JSONResponse:
        """写入 / 更新单条归档（按 seq 幂等）。"""
        svc = _get_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if "seq" not in body:
            return JSONResponse({"ok": False, "error": "seq is required"}, status_code=400)
        try:
            item_id = svc.upsert_item(body)
            return JSONResponse(
                {"ok": True, "id": item_id, "item": svc.get_item(item_id)},
                status_code=201,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("写入对话归档失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/conversation-archive/import")
    def conversation_archive_import(body: dict[str, Any]) -> JSONResponse:
        """批量导入归档条目。body: {"items": [...]}。"""
        svc = _get_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = body.get("items") or []
        if not isinstance(items, list):
            return JSONResponse({"ok": False, "error": "items must be a list"}, status_code=400)
        try:
            count = svc.upsert_many(items)
            return JSONResponse({"ok": True, "imported": count, "total": svc.count_items()})
        except Exception as e:  # noqa: BLE001
            logger.exception("批量导入对话归档失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
