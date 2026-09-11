"""Database mixin: LLM usage tracking.

从 ``storage/database.py`` 拆出的 llm_usage 表操作组。
``Database`` 类继承本 mixin，调用方代码无需修改。

v0.4.0+: llm_usage 表迁移到独立的 llm.db，与主库锁域隔离。
当前为双写过渡期：同时写主库和 llm.db，读取优先从 llm.db。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class LLMUsageMixin:
    """LLM 调用用量记录的读写方法。"""

    conn: Any  # 由 Database 提供（主库连接）
    _llm_conn: Any  # 由 Database 提供（llm.db 连接）
    _execute_write: Any  # 由 Database 提供

    @property
    def _llm(self) -> Any:
        """获取 llm.db 连接，回退到主库连接。"""
        if self._llm_conn is not None:
            return self._llm_conn
        return self.conn

    def insert_llm_usage(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_cny: float,
        caller: str = "",
        success: bool = True,
        cached_input_tokens: int = 0,
    ) -> int:
        """Append one LLM-call usage record.

        ``cached_input_tokens`` (v0.3.28+) is the portion of
        ``prompt_tokens`` served from provider-side prompt cache —
        always ``<= prompt_tokens``. 0 means no cache use. Used by
        ``cost --by caller`` to compute hit rates and by
        ``estimate_cost`` to discount cached tokens correctly.

        v0.4.0+: 主写 llm.db，主库旧表已随 sharding 收尾移除。
        """
        total = max(0, prompt_tokens) + max(0, completion_tokens)
        params = (
            provider or "",
            model or "",
            caller or "",
            int(max(0, prompt_tokens)),
            int(max(0, completion_tokens)),
            int(total),
            int(max(0, cached_input_tokens)),
            float(estimated_cost_cny),
            1 if success else 0,
        )
        sql = """INSERT INTO llm_usage
               (provider, model, caller, prompt_tokens, completion_tokens,
                total_tokens, cached_input_tokens, estimated_cost_cny,
                success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"""

        # 主写：llm.db
        lastrowid = 0
        try:
            cursor = self._llm.execute(sql, params)
            self._llm.commit()
            lastrowid = cursor.lastrowid or 0
        except Exception as e:
            logger.debug("llm.db 写入失败，回退主库: %s", e)

        return lastrowid

    def query_llm_usage_by_day(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-day aggregates for the last ``days`` days.

        Each row: {day, calls, prompt_tokens, completion_tokens,
        total_tokens, cost_cny}. Days with zero usage are omitted —
        the CLI fills gaps for display.
        """
        cursor = self._llm.execute(
            """
            SELECT date(timestamp, 'localtime') AS day,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY day
            ORDER BY day DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_provider(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-(provider, model) totals over the last ``days`` days."""
        cursor = self._llm.execute(
            """
            SELECT provider,
                   model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY provider, model
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_caller(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-caller totals over the last ``days`` days.

        ``caller`` is a free-form string the LLM service tags into each
        row (e.g. ``discovery.evaluate`` / ``recommendation.write`` /
        ``soul.profile``). Untagged calls land under ``""`` which the
        CLI renders as ``(untagged)``. Result is sorted by cost so the
        first row is the most expensive caller.

        v0.3.28+ also returns ``cached_input_tokens`` so the CLI can
        compute and surface per-caller cache hit rates — a low rate
        (< 30%) signals prompt-prefix instability worth investigating.
        """
        cursor = self._llm.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_total(self, *, days: int = 7) -> dict[str, Any]:
        """Return a single-row total for the last ``days`` days."""
        cursor = self._llm.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            """,
            (max(1, int(days)),),
        )
        row = cursor.fetchone()
        return (
            dict(row)
            if row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

    def max_llm_usage_id(self) -> int:
        """Return the highest currently-stored ``llm_usage.id`` (0 if empty).

        Used as a checkpoint for "what's been billed since this point"
        queries — the init / discovery cycle wrappers snapshot it on
        entry and pass it to ``query_llm_usage_since_id`` on exit to
        scope the cost summary to that single phase.
        """
        cursor = self._llm.execute("SELECT COALESCE(MAX(id), 0) AS m FROM llm_usage")
        row = cursor.fetchone()
        return int(row["m"]) if row else 0

    def query_llm_usage_since_id(self, *, since_id: int) -> dict[str, Any]:
        """Return per-caller breakdown + totals for rows ``id > since_id``.

        Output: ``{"total": {calls, prompt_tokens, completion_tokens,
        cost_cny}, "by_caller": [{caller, calls, ...}, ...]}``. Bound
        to a single phase by passing ``max_llm_usage_id()`` taken at
        the phase entry.
        """
        total_cursor = self._llm.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            """,
            (int(since_id),),
        )
        total_row = total_cursor.fetchone()
        total = (
            dict(total_row)
            if total_row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

        caller_cursor = self._llm.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (int(since_id),),
        )
        return {
            "total": total,
            "by_caller": [dict(row) for row in caller_cursor.fetchall()],
        }
