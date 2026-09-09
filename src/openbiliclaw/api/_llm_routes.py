"""LLM 配额监控路由。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_llm_routes(app: Any, ctx: Any) -> None:
    """注册 LLM 配额监控路由。"""

    @app.get("/api/llm/quota")
    async def llm_quota_status(hours: int = 5):
        """查询 LLM 配额使用情况。

        Args:
            hours: 查询时间窗口（小时），默认 5 小时（与商汤 API 限流窗口对齐）。

        """
        try:
            import sqlite3
            from datetime import datetime, timedelta

            _quota_db_path = "data/openbiliclaw.db"
            try:
                cfg = getattr(ctx, "config", None)
                if cfg and hasattr(cfg, "storage") and cfg.storage:
                    _quota_db_path = str(cfg.storage.db_path)
            except Exception:
                pass

            conn = sqlite3.connect(_quota_db_path, timeout=10.0)
            conn.execute("PRAGMA busy_timeout=10000")
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

            # 总调用次数
            total_calls = conn.execute(
                "SELECT COUNT(*) FROM llm_usage WHERE timestamp >= ?", (cutoff,)
            ).fetchone()[0]

            # 总 token 数
            total_tokens = conn.execute(
                "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM llm_usage WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()[0]

            # 按模型分组
            by_model = [
                {"model": r[0], "calls": r[1], "tokens": r[2]}
                for r in conn.execute(
                    "SELECT model, COUNT(*), SUM(prompt_tokens + completion_tokens) "
                    "FROM llm_usage WHERE timestamp >= ? GROUP BY model ORDER BY COUNT(*) DESC",
                    (cutoff,),
                ).fetchall()
            ]

            # 按模块分组
            by_module = [
                {"module": r[0], "calls": r[1], "tokens": r[2]}
                for r in conn.execute(
                    "SELECT "
                    "  CASE "
                    "    WHEN caller LIKE 'soul.%' THEN 'soul' "
                    "    WHEN caller LIKE 'discovery.%' THEN 'discovery' "
                    "    WHEN caller LIKE 'recommendation.%' THEN 'recommendation' "
                    "    WHEN caller LIKE 'diary.%' THEN 'diary' "
                    "    WHEN caller LIKE 'chat_analysis%' THEN 'chat_analysis' "
                    "    WHEN caller LIKE 'notes.%' THEN 'notes' "
                    "    WHEN caller LIKE 'self_evolution.%' OR caller LIKE '%tldr%' OR caller LIKE '%knowledge_card%' THEN 'self_evolution' "
                    "    ELSE 'other' "
                    "  END as module, "
                    "  COUNT(*), SUM(prompt_tokens + completion_tokens) "
                    "FROM llm_usage WHERE timestamp >= ? GROUP BY module ORDER BY COUNT(*) DESC",
                    (cutoff,),
                ).fetchall()
            ]

            # 商汤日日新配额估算（假设 1 point ≈ 1000 tokens）
            sum(m["calls"] for m in by_model if "sensenova" in m["model"])
            sensenova_tokens = sum(m["tokens"] for m in by_model if "sensenova" in m["model"])
            estimated_points = max(1, sensenova_tokens // 1000)
            quota_limit = 60000
            quota_usage_pct = round(estimated_points / quota_limit * 100, 1)

            conn.close()

            from fastapi.responses import JSONResponse

            return JSONResponse(
                {
                    "ok": True,
                    "window_hours": hours,
                    "total_calls": total_calls,
                    "total_tokens": total_tokens,
                    "estimated_sensenova_points": estimated_points,
                    "sensenova_quota_limit": quota_limit,
                    "sensenova_quota_usage_pct": quota_usage_pct,
                    "by_model": by_model,
                    "by_module": by_module,
                }
            )
        except Exception as e:
            logger.exception("LLM quota query failed")
            from fastapi.responses import JSONResponse

            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
