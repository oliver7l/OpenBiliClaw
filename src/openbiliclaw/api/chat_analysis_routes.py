"""聊天记录分析系统 API 路由。

通过 ``register_chat_analysis_routes(app, ctx)`` 注册。
使用独立数据库文件 data/chat_analysis.db 存储数据。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from openbiliclaw.chat_analysis import ChatAnalysisService

if TYPE_CHECKING:
    from fastapi import FastAPI


logger = logging.getLogger(__name__)

# 按解析后的库路径缓存 service：项目根被（测试）重指向时不会串用到上一个库的实例。
_chat_analysis_service: dict[str, ChatAnalysisService] = {}


def _chat_db_path() -> Path:
    """聊天分析库路径（延迟解析）。

    早先这里用 ``Path(__file__).resolve().parent.parent.parent.parent`` 往上数
    四级取项目根——一旦本文件换个目录层级就静默指错位置，且**不认**
    ``OPENBILICLAW_PROJECT_ROOT``，测试无法隔离。统一改走 ``config._project_root()``
    （唯一支持化的项目根锚点）。做成函数而非模块级常量，是因为常量在 import 时就
    固化路径，测试设了 env 也不会生效。
    """
    from openbiliclaw.config import _project_root

    return _project_root() / "data" / "chat_analysis.db"


def _resolve_llm_service(ctx: Any) -> Any:
    """从 ctx 取 LLM service —— 兼容两种调用形态。

    历史上调用方传的是 ``app.state``（故走 ``ctx.runtime_context.llm_service``），
    现在统一入口 ``_route_registry`` 传的是 ``RuntimeContext``（其自身就有
    ``llm_service``）。两种都支持，避免哪一边被悄悄降级成 None。
    """
    if ctx is None:
        return None
    service = getattr(ctx, "llm_service", None)
    if service is not None:
        return service
    runtime_ctx = getattr(ctx, "runtime_context", None)
    return getattr(runtime_ctx, "llm_service", None) if runtime_ctx is not None else None


def _resolve_openbiliclaw_db(ctx: Any) -> str | None:
    """同上：两种 ctx 形态都要能拿到主库路径。"""
    database = getattr(ctx, "database", None)
    if database is None:
        runtime_ctx = getattr(ctx, "runtime_context", None)
        database = getattr(runtime_ctx, "database", None) if runtime_ctx is not None else None
    return str(database.db_path) if database is not None and hasattr(database, "db_path") else None


def register_chat_analysis_routes(app: FastAPI, ctx: Any) -> None:
    # Health check endpoint to verify routes are registered
    @app.get("/api/chat-analysis/health")
    def chat_analysis_health() -> JSONResponse:
        import os

        db_path = _chat_db_path()
        return JSONResponse(
            {
                "ok": True,
                "db_exists": db_path.exists(),
                "db_path": str(db_path),
                "cwd": os.getcwd(),
            }
        )

    def _get_svc() -> ChatAnalysisService | None:
        db_path = _chat_db_path()
        cached = _chat_analysis_service.get(str(db_path))
        if cached is not None:
            return cached
        if not db_path.exists():
            logger.warning("聊天分析数据库不存在: %s", db_path)
            return None
        cached = ChatAnalysisService(db_path=db_path, llm_service=_resolve_llm_service(ctx))
        _chat_analysis_service[str(db_path)] = cached
        return cached

    # ── 全局统计 ──

    @app.get("/api/chat-analysis/stats")
    def chat_analysis_stats() -> JSONResponse:
        """全局统计概览。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = svc.get_global_stats()
        return JSONResponse({"ok": True, **stats})

    # ── 会话管理 ──

    @app.get("/api/chat-analysis/sessions")
    def chat_analysis_sessions(
        limit: int = 50,
        offset: int = 0,
        chat_type: str | None = None,
    ) -> JSONResponse:
        """列出所有聊天会话。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        sessions = svc.list_sessions(
            offset=max(0, int(offset)),
            limit=max(1, min(int(limit), 200)),
            chat_type=chat_type,
        )
        total = svc.count_sessions(chat_type=chat_type)
        return JSONResponse(
            {
                "ok": True,
                "items": [s.model_dump(mode="json") for s in sessions],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/chat-analysis/sessions/{session_id}")
    def chat_analysis_session_detail(session_id: int) -> JSONResponse:
        """会话详情及统计。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        session = svc.get_session(session_id)
        if not session:
            return JSONResponse({"ok": False, "error": "session not found"}, status_code=404)
        stats = svc.get_session_stats(session_id)
        return JSONResponse(
            {
                "ok": True,
                "session": session.model_dump(mode="json"),
                "stats": stats,
            }
        )

    @app.delete("/api/chat-analysis/sessions/{session_id}")
    def chat_analysis_session_delete(session_id: int) -> JSONResponse:
        """删除会话及所有关联数据。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = svc.delete_session(session_id)
        return JSONResponse({"ok": ok})

    # ── 消息 ──

    @app.get("/api/chat-analysis/sessions/{session_id}/messages")
    def chat_analysis_messages(
        session_id: int,
        limit: int = 100,
        offset: int = 0,
        sender: str | None = None,
        message_type: str | None = None,
    ) -> JSONResponse:
        """获取会话消息列表。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        messages = svc.get_messages(
            session_id,
            offset=max(0, int(offset)),
            limit=max(1, min(int(limit), 500)),
            sender=sender,
            message_type=message_type,
        )
        total = svc.count_messages(session_id)
        return JSONResponse(
            {
                "ok": True,
                "items": [m.model_dump(mode="json") for m in messages],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/chat-analysis/sessions/{session_id}/senders")
    def chat_analysis_senders(session_id: int) -> JSONResponse:
        """获取会话的活跃发送者统计。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        senders = svc.get_top_senders(session_id)
        return JSONResponse({"ok": True, "senders": senders})

    # ── 搜索 ──

    @app.get("/api/chat-analysis/search")
    def chat_analysis_search(
        q: str,
        session_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """全文搜索聊天消息。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if not q.strip():
            return JSONResponse({"ok": False, "error": "query is required"}, status_code=400)
        result = svc.search(
            query=q.strip(),
            session_id=session_id,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "total": result.total,
                "results": [r.model_dump(mode="json") for r in result.results],
                "took_ms": result.took_ms,
            }
        )

    # ── AI 分析片段 ──

    @app.get("/api/chat-analysis/analysis")
    def chat_analysis_chunks(
        session_title: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """获取 AI 分析片段列表。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        chunks = svc.get_analysis_chunks(
            session_title=session_title,
            offset=max(0, int(offset)),
            limit=max(1, min(int(limit), 200)),
        )
        total = svc.count_analysis_chunks(session_title=session_title)
        return JSONResponse(
            {
                "ok": True,
                "items": [c.model_dump(mode="json") for c in chunks],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/chat-analysis/analysis/groups")
    def chat_analysis_analysis_groups() -> JSONResponse:
        """获取已分析会话的分组统计。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        groups = svc.get_analysis_distinct_sessions()
        return JSONResponse(
            {
                "ok": True,
                "groups": [{"session_title": g[0], "chunk_count": g[1]} for g in groups],
            }
        )

    # ── LLM 分析 ──

    @app.post("/api/chat-analysis/sessions/{session_id}/analyze")
    async def chat_analysis_session_analyze(session_id: int) -> JSONResponse:
        """对会话执行 LLM 完整分析：话题提取 + 洞察生成 + 摘要。

        消耗 LLM 配额（约 3 次调用），配额不足时返回 429。
        """
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if not svc.get_session(session_id):
            return JSONResponse({"ok": False, "error": "session not found"}, status_code=404)
        if svc.quota.is_exhausted:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "LLM quota exhausted",
                    "remaining": svc.quota.remaining,
                    "max_calls_per_window": svc.quota.max_calls_per_window,
                },
                status_code=429,
            )
        result = await svc.analyze_session(session_id)
        return JSONResponse(
            {
                "ok": True,
                "session_id": session_id,
                "quota_remaining": svc.quota.remaining,
                "analysis": result,
            }
        )

    # ── LLM 配额 ──

    @app.get("/api/chat-analysis/quota")
    def chat_analysis_quota():
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "服务未就绪"}, status_code=503)
        q = svc.quota
        return JSONResponse(
            {
                "ok": True,
                "used_calls": q.used,
                "max_calls_per_window": q.max_calls_per_window,
                "window_seconds": q.window_seconds,
                "remaining": q.remaining,
                "is_exhausted": q.is_exhausted,
            }
        )

    # ── 导入 ──

    @app.post("/api/chat-analysis/import/sqlite")
    def chat_analysis_import_sqlite(
        db_path: str,
        max_sessions: int = 0,
        max_messages: int = 0,
    ) -> JSONResponse:
        """从 SQLite 数据库导入聊天消息。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            stats = svc.import_from_sqlite(
                db_path,
                max_sessions=max_sessions,
                max_messages=max_messages,
            )
            return JSONResponse({"ok": True, "stats": stats.model_dump(mode="json")})
        except FileNotFoundError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except Exception as e:
            logger.exception("导入失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/chat-analysis/import/deepseek")
    def chat_analysis_import_deepseek(
        analysis_dir: str | None = None,
    ) -> JSONResponse:
        """从 DeepSeek 分析目录导入分析结果。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            results = svc.import_from_deepseek_analysis(analysis_dir)
            return JSONResponse(
                {
                    "ok": True,
                    "results": [
                        {
                            "session_title": r.session_title,
                            "chunks_imported": r.chunks_imported,
                            "total_lines": r.total_lines,
                            "errors": r.errors,
                        }
                        for r in results
                    ],
                }
            )
        except FileNotFoundError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
        except Exception as e:
            logger.exception("导入失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/chat-analysis/import/all")
    def chat_analysis_import_all(
        mindback_root: str | None = None,
        max_sessions: int = 0,
        max_messages: int = 0,
    ) -> JSONResponse:
        """从所有来源批量导入数据。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            openbiliclaw_db = _resolve_openbiliclaw_db(ctx)
            results = svc.import_all(
                mindback_root=mindback_root,
                openbiliclaw_db=openbiliclaw_db,
                max_sessions=max_sessions,
                max_messages=max_messages,
            )
            resp: dict[str, Any] = {"ok": True, "results": {}}
            for key, val in results.items():
                if hasattr(val, "model_dump"):
                    resp["results"][key] = val.model_dump(mode="json")
                elif isinstance(val, list):
                    resp["results"][key] = [
                        {
                            "session_title": r.session_title,
                            "chunks_imported": r.chunks_imported,
                            "total_lines": r.total_lines,
                            "errors": r.errors,
                        }
                        for r in val
                    ]
                else:
                    resp["results"][key] = str(val)
            return JSONResponse(resp)
        except Exception as e:
            logger.exception("批量导入失败")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # ── 标签 ──

    @app.get("/api/chat-analysis/tags")
    def chat_analysis_tags(category: str | None = None) -> JSONResponse:
        """获取标签列表。"""
        svc = _get_svc()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        tags = svc.list_tags(category=category)
        return JSONResponse(
            {
                "ok": True,
                "items": [t.model_dump(mode="json") for t in tags],
            }
        )
