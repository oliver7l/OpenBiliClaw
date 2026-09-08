"""Database mixin: delight scoring.

从 ``storage/database.py`` 拆出的 delight 惊喜分计算与候选查询组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

from typing import Any


class DelightMixin:
    """Delight 惊喜分计算与候选查询方法。"""

    conn: Any  # 由 Database 提供
    _execute_write: Any  # 由 Database 提供
    _admission_predicate_sql: Any  # 由 Database 提供

    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 1,
    ) -> dict[str, Any] | None:
        """Return one un-notified pool item with the highest delight_score.

        Backwards-compatible: ``limit=1`` returns a single dict (or None);
        callers that want multiple candidates should call
        ``get_delight_candidates`` instead.
        """
        rows = self.get_delight_candidates(
            min_delight_score=min_delight_score,
            limit=max(1, int(limit)),
        )
        return rows[0] if rows else None

    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 20,
        include_liked: bool = False,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` un-notified delight candidates ordered by score.

        Restricts to ``pool_status IN ('fresh', 'shown')`` — suppressed items
        have been trimmed out of the active pool and shouldn't reappear as
        delights.

        ``include_liked`` keeps ``feedback_type='like'`` rows in the result.
        """
        feedback_clause = (
            "COALESCE(feedback_type, '') IN ('', 'like')"
            if include_liked
            else "COALESCE(feedback_type, '') = ''"
        )
        admission_sql, admission_params = self._admission_predicate_sql()
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND {admission_sql}
              AND COALESCE(delight_notified, 0) = 0
              AND COALESCE(delight_reason, '') != ''
              AND COALESCE(delight_hook, '') != ''
              AND {feedback_clause}
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
            ORDER BY delight_score DESC, relevance_score DESC, discovered_at DESC
            LIMIT ?
            """,
            (min_delight_score, *admission_params, max(1, int(limit))),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_delight_notified(self, bvid: str) -> None:
        """Mark one content item as delight-notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_notified = 1,
                delight_notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def update_delight_score(
        self,
        bvid: str,
        *,
        delight_score: float,
        delight_reason: str,
        delight_hook: str = "",
    ) -> None:
        """Persist the computed delight score and explanation for a pool item."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_score = ?,
                delight_reason = ?,
                delight_hook = ?
            WHERE bvid = ?
            """,
            (delight_score, delight_reason, delight_hook, bvid),
        )

    def count_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> int:
        """Return the number of un-notified delight candidates."""
        admission_sql, admission_params = self._admission_predicate_sql()
        cursor = self.conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND {admission_sql}
              AND COALESCE(delight_notified, 0) = 0
              AND COALESCE(delight_reason, '') != ''
              AND COALESCE(delight_hook, '') != ''
              AND COALESCE(feedback_type, '') = ''
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
            """,
            (min_delight_score, *admission_params),
        )
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def get_pool_candidates_needing_delight_score(
        self,
        limit: int = 30,
        *,
        min_delight_score_for_reason: float | None = None,
        min_relevance_score: float = 0.55,
        xhs_self_nickname: str = "",
    ) -> list[dict[str, Any]]:
        """Return pool candidates that still need delight evaluation or copy.

        Two-stage retrieval: ``relevance_score >= min_relevance_score`` is the
        cheap pre-filter, then the caller runs the expensive LLM delight scorer
        only on this shortlist.
        """
        from openbiliclaw.storage.database import (
            _normalize_admission_min_score,
            _xhs_self_author_guard_params,
            _xhs_self_author_guard_sql,
        )

        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        effective_min_relevance_score = _normalize_admission_min_score(min_relevance_score)
        if min_delight_score_for_reason is None:
            cursor = self.conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(delight_score, 0.0) = 0.0
                  AND COALESCE(relevance_score, 0.0) >= ?
                  {guard_sql}
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                ORDER BY relevance_score DESC, discovered_at DESC
                LIMIT ?
                """,
                (effective_min_relevance_score, *guard_params, limit),
            )
        else:
            cursor = self.conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND (
                    COALESCE(delight_score, 0.0) = 0.0
                    OR (
                      COALESCE(delight_score, 0.0) >= ?
                      AND (
                        COALESCE(delight_reason, '') = ''
                        OR COALESCE(delight_hook, '') = ''
                      )
                    )
                  )
                  {guard_sql}
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                ORDER BY
                    CASE WHEN COALESCE(delight_score, 0.0) > 0.0 THEN 0 ELSE 1 END ASC,
                    delight_score DESC,
                    relevance_score DESC,
                    discovered_at DESC
                LIMIT ?
                """,
                (
                    effective_min_relevance_score,
                    min_delight_score_for_reason,
                    *guard_params,
                    limit,
                ),
            )
        return [dict(row) for row in cursor.fetchall()]
