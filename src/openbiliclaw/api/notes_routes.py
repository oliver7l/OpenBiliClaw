"""笔记系统 API 路由。

包含笔记 CRUD、搜索、统计、导入、任务管理。
通过 ``register_notes_routes(app, ctx)`` 注册。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from openbiliclaw.notes import (
    NoteCreate,
    NoteListParams,
    NoteService,
    NoteUpdate,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

    from openbiliclaw.api.runtime_context import RuntimeContext

logger = logging.getLogger(__name__)

_notes_service: NoteService | None = None


def register_notes_routes(app: FastAPI, ctx: RuntimeContext) -> None:
    def _get_notes_service() -> NoteService | None:
        """获取或创建笔记服务实例（懒加载）。"""
        global _notes_service
        if _notes_service is not None:
            return _notes_service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        _notes_service = NoteService(database=database)
        return _notes_service

    # ── 笔记 CRUD ──

    @app.get("/api/notes")
    def notes_list(
        limit: int = 50,
        offset: int = 0,
        note_type: str | None = None,
        source_platform: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "DESC",
    ) -> JSONResponse:
        """列出笔记，支持筛选、搜索和排序。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        params = NoteListParams(
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            note_type=note_type,
            source_platform=source_platform,
            tag=tag,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        items = svc.list_notes(params)
        total = svc.count_notes(params)
        return JSONResponse(
            {
                "ok": True,
                "items": [n.model_dump(mode="json") for n in items],
                "total": total,
                "limit": params.limit,
                "offset": params.offset,
            }
        )

    @app.get("/api/notes/stats")
    def notes_stats() -> JSONResponse:
        """获取笔记统计信息。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = svc.get_stats()
        return JSONResponse({"ok": True, "stats": stats.model_dump(mode="json")})

    @app.get("/api/notes/{note_id:int}")
    def notes_get(note_id: int) -> JSONResponse:
        """获取单条笔记。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        note = svc.get_note(note_id)
        if note is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "item": note.model_dump(mode="json")})

    @app.post("/api/notes")
    def notes_create(body: dict[str, Any]) -> JSONResponse:
        """创建笔记。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = NoteCreate(
                title=body.get("title", ""),
                content_md=body.get("content_md", ""),
                note_type=body.get("note_type", "manual"),
                source_platform=body.get("source_platform", ""),
                source_url=body.get("source_url", ""),
                source_ref=body.get("source_ref", ""),
                author=body.get("author", ""),
                tags=body.get("tags", []),
                metadata=body.get("metadata", {}),
                raw_ref=body.get("raw_ref", ""),
                task_id=body.get("task_id", ""),
            )
            note = svc.create_note(data)
            return JSONResponse({"ok": True, "item": note.model_dump(mode="json")}, status_code=201)
        except Exception as e:
            logger.exception("创建笔记失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.put("/api/notes/{note_id:int}")
    def notes_update(note_id: int, body: dict[str, Any]) -> JSONResponse:
        """更新笔记。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = NoteUpdate(
                title=body.get("title"),
                content_md=body.get("content_md"),
                note_type=body.get("note_type"),
                source_platform=body.get("source_platform"),
                source_url=body.get("source_url"),
                source_ref=body.get("source_ref"),
                author=body.get("author"),
                tags=body.get("tags"),
                metadata=body.get("metadata"),
            )
            note = svc.update_note(note_id, data)
            if note is None:
                return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
            return JSONResponse({"ok": True, "item": note.model_dump(mode="json")})
        except Exception as e:
            logger.exception("更新笔记失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.delete("/api/notes/{note_id:int}")
    def notes_delete(note_id: int) -> JSONResponse:
        """删除笔记。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = svc.delete_note(note_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({"ok": True})

    @app.get("/api/notes/search")
    def notes_search(q: str, limit: int = 50) -> JSONResponse:
        """全文搜索笔记。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        params = NoteListParams(
            limit=max(1, min(int(limit), 200)),
            search=q,
        )
        items = svc.list_notes(params)
        return JSONResponse(
            {
                "ok": True,
                "query": q,
                "items": [n.model_dump(mode="json") for n in items],
                "total": len(items),
            }
        )

    # ── 笔记任务 ──

    @app.get("/api/notes/tasks")
    def notes_tasks_list(
        limit: int = 20, offset: int = 0, status: str | None = None
    ) -> JSONResponse:
        """列出笔记生成任务。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        tasks = svc.list_tasks(
            limit=max(1, min(int(limit), 100)), offset=max(0, int(offset)), status=status
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [t.model_dump(mode="json") for t in tasks],
            }
        )

    @app.get("/api/notes/tasks/{task_id}")
    def notes_tasks_get(task_id: str) -> JSONResponse:
        """获取笔记生成任务详情。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        task = svc.get_task(task_id)
        if task is None:
            return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
        return JSONResponse({"ok": True, "item": task.model_dump(mode="json")})

    @app.post("/api/notes/tasks")
    def notes_tasks_create(body: dict[str, Any]) -> JSONResponse:
        """创建笔记生成任务。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            task = svc.create_task(
                source_platform=body.get("source_platform", ""),
                source_ref=body.get("source_ref", ""),
                resume_key=body.get("resume_key"),
            )
            return JSONResponse({"ok": True, "item": task.model_dump(mode="json")}, status_code=201)
        except Exception as e:
            logger.exception("创建笔记任务失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ── 导入 ──

    @app.post("/api/notes/import/read-archive")
    def notes_import_read_archive(body: dict[str, Any]) -> JSONResponse:
        """从已读库目录导入笔记。"""
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        notes_dir = body.get("notes_dir", "")
        if not notes_dir:
            return JSONResponse({"ok": False, "error": "notes_dir is required"}, status_code=400)
        result = svc.import_from_read_archive(notes_dir)
        return JSONResponse({"ok": True, **result})

    # ── 视频转笔记 ──

    @app.post("/api/notes/from-video")
    async def notes_from_video(body: dict[str, Any]) -> JSONResponse:
        """将 B 站视频转为结构化笔记。

        字幕优先策略：先尝试获取 CC 字幕，失败则下载音频转录。
        """
        svc = _get_notes_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)

        bvid = body.get("bvid", "")
        if not bvid:
            return JSONResponse({"ok": False, "error": "bvid is required"}, status_code=400)

        cid = int(body.get("cid", 0) or 0)
        save_note = bool(body.get("save_note", True))
        prefer_subtitle = bool(body.get("prefer_subtitle", True))
        enable_asr_rectify = bool(body.get("enable_asr_rectify", True))
        content_type = str(body.get("content_type", "article"))
        whisper_model = str(body.get("whisper_model", "base"))

        # 获取 cookie（从 runtime context 或请求体）
        cookie = str(body.get("cookie", ""))
        if not cookie and hasattr(ctx, "config") and hasattr(ctx.config, "bilibili"):
            cookie = getattr(ctx.config.bilibili, "cookie", "") or ""

        # 注入 bilibili client（如果有 cookie）
        bilibili_client = None
        if cookie:
            from openbiliclaw.bilibili.api import BilibiliAPIClient

            bilibili_client = BilibiliAPIClient(cookie=cookie)
            svc._bilibili_client = bilibili_client

        try:
            result = await svc.video_to_note(
                bvid,
                cid=cid,
                save_note=save_note,
                prefer_subtitle=prefer_subtitle,
                enable_asr_rectify=enable_asr_rectify,
                content_type=content_type,
                whisper_model=whisper_model,
                cookie=cookie,
            )
            return JSONResponse(
                {
                    "ok": result.success,
                    "success": result.success,
                    "source": result.source,
                    "error": result.error,
                    "stages": result.stages,
                    "note": result.note.model_dump(mode="json") if result.note else None,
                }
            )
        except Exception as e:
            logger.exception("视频转笔记失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
