"""Database mixin: data pruning / retention cleanup.

Contains methods that delete old rows from append-only tables to bound
growth: events, task history, recommendations, and llm_usage.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class PruneMixin:
    """Database methods for pruning old data by retention policy."""

    conn: Any
    _execute_write: Any

    # ── Events ─────────────────────────────────────────────────────

    def prune_events_by_retention(
        self,
        *,
        retention_days: int,
        low_value_types: tuple[str, ...] = ("view", "scroll", "hover", "snapshot"),
        batch_size: int = 5000,
    ) -> int:
        """Delete old low-value behavior events to bound ``events`` growth."""
        if retention_days <= 0 or not low_value_types:
            return 0
        placeholders = ", ".join("?" for _ in low_value_types)
        cutoff = f"-{int(retention_days)} days"
        total = 0
        while True:
            cursor = self._execute_write(
                f"""
                DELETE FROM act.events
                WHERE id IN (
                    SELECT id FROM act.events
                    WHERE event_type IN ({placeholders})
                      AND created_at < datetime('now', ?)
                    LIMIT ?
                )
                """,
                (*low_value_types, cutoff, batch_size),
            )
            deleted = cursor.rowcount
            total += deleted
            if deleted < batch_size:
                break
            time.sleep(0.02)
        if total:
            logger.info(
                "Pruned %d old low-value events (types=%s, older_than=%dd)",
                total,
                low_value_types,
                retention_days,
            )
        return total

    # ── Task history ───────────────────────────────────────────────

    def prune_task_history(
        self,
        *,
        retention_days: int = 30,
        batch_size: int = 2000,
    ) -> dict[str, int]:
        """Delete old terminal rows from the producer task/candidate tables."""
        if retention_days <= 0:
            return {}
        cutoff = f"-{int(retention_days)} days"
        targets: tuple[tuple[str, str, tuple[object, ...]], ...] = (
            (
                "zhihu_tasks",
                "DELETE FROM zhihu_tasks WHERE id IN ("
                "SELECT id FROM zhihu_tasks WHERE status IN ('completed','failed') "
                "AND created_at < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            ),
            (
                "dy_tasks",
                "DELETE FROM dy_tasks WHERE id IN ("
                "SELECT id FROM dy_tasks WHERE status IN ('completed','failed') "
                "AND created_at < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            ),
            (
                "discovery_candidates",
                "DELETE FROM discovery_candidates WHERE id IN ("
                "SELECT id FROM discovery_candidates "
                "WHERE status LIKE 'rejected%' "
                "AND last_seen_at < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            ),
        )
        results: dict[str, int] = {}
        for table, sql, params in targets:
            total = 0
            while True:
                cursor = self._execute_write(sql, params)
                deleted = cursor.rowcount
                total += deleted
                if deleted < batch_size:
                    break
                time.sleep(0.02)
            if total:
                logger.info(
                    "Pruned %d terminal rows from %s (older_than=%dd)",
                    total,
                    table,
                    retention_days,
                )
                results[table] = total
        return results

    # ── Recommendations ────────────────────────────────────────────

    def prune_recommendations(
        self,
        *,
        retention_days: int = 7,
        batch_size: int = 2000,
    ) -> int:
        """Delete old ``recommendations`` rows past their de-dup window."""
        if retention_days <= 0:
            return 0
        cutoff = f"-{int(retention_days)} days"
        total = 0
        while True:
            cursor = self._execute_write(
                "DELETE FROM recommendations WHERE id IN ("
                "SELECT id FROM recommendations "
                "WHERE created_at < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            )
            deleted = cursor.rowcount
            total += deleted
            if deleted < batch_size:
                break
            time.sleep(0.02)
        if total:
            logger.info(
                "Pruned %d old recommendation rows (older_than=%dd)",
                total,
                retention_days,
            )
        return total

    # ── LLM usage ──────────────────────────────────────────────────

    def prune_llm_usage(
        self,
        *,
        retention_days: int = 90,
        batch_size: int = 2000,
    ) -> int:
        """Delete old ``llm_usage`` accounting rows.

        v0.4.0+: 优先从 llm.db 删除，双写期间同时删除主库旧数据。
        """
        if retention_days <= 0:
            return 0
        cutoff = f"-{int(retention_days)} days"
        total = 0
        llm_conn = getattr(self, "_llm_conn", None)
        target_conn = llm_conn if llm_conn is not None else self.conn
        while True:
            cursor = target_conn.execute(
                "DELETE FROM llm_usage WHERE id IN ("
                "SELECT id FROM llm_usage "
                "WHERE timestamp < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            )
            target_conn.commit()
            deleted = cursor.rowcount
            total += deleted
            if deleted < batch_size:
                break
            time.sleep(0.02)
        # 双写期间同时清理主库旧数据
        if llm_conn is not None:
            try:
                while True:
                    cursor = self._execute_write(
                        "DELETE FROM llm_usage WHERE id IN ("
                        "SELECT id FROM llm_usage "
                        "WHERE timestamp < datetime('now', ?) LIMIT ?)",
                        (cutoff, batch_size),
                    )
                    if cursor.rowcount < batch_size:
                        break
            except Exception:
                pass
        if total:
            logger.info(
                "Pruned %d old llm_usage rows (older_than=%dd)",
                total,
                retention_days,
            )
        return total
