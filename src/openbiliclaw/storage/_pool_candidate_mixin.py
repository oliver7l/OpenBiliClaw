"""Database mixin: pool candidates (core).

从 ``storage/database.py`` 拆出的推荐池候选查询核心组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class PoolCandidateMixin:
    """推荐池候选查询核心方法。"""

    conn: Any  # 由 Database 提供
    _ensure_fresh_read: Any  # 由 Database 提供
    _pool_admission_min_score: Any  # 由 Database 提供
    _exclude_viewed_rows: Any  # 由 Database 提供
    get_recent_viewed_content_keys: Any  # 由 Database 提供
    _balance_pool_rows: Any  # 由 Database 提供
    _is_viewed_row: Any  # 由 Database 提供

    def get_pool_candidates(
        self,
        limit: int = 20,
        *,
        max_per_topic_group: int = 0,
        xhs_self_nickname: str = "",
        platform: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get fresh recommendation candidates directly from the discovery pool."""
        from openbiliclaw.storage.database import (
            _DELIGHT_CLAIM_GUARD_SQL,
            _POOL_NOT_RECENTLY_RECOMMENDED_SQL,
            _POOL_SERVABLE_STATUS_SQL,
            _xhs_self_author_guard_params,
            _xhs_self_author_guard_sql,
        )

        self._ensure_fresh_read()
        fetch_limit = max(limit * 8, 80)
        min_score = self._pool_admission_min_score()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_guard_sql = _DELIGHT_CLAIM_GUARD_SQL
        platform_clause = ""
        platform_term: str = (platform or "").strip().lower()
        if platform_term:
            platform_clause = " AND source_platform = ?"
        if max_per_topic_group <= 0:
            sql = f"""
                SELECT *
                FROM content_cache
                WHERE {_POOL_SERVABLE_STATUS_SQL}
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND COALESCE(style_key, '') != ''
                  AND COALESCE(topic_group, '') != ''
                  AND (
                    source_platform != 'xiaohongshu'
                    OR (content_url LIKE '%xsec_token=%' AND discovered_at >= '2026-08-15')
                  )
                  {guard_sql}
                  {delight_guard_sql}
                  {platform_clause}
                  {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
                ORDER BY
                    CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                    relevance_score DESC,
                    last_scored_at DESC,
                    view_count DESC,
                    bvid ASC
                LIMIT ?
            """
            params: tuple[Any, ...] = (min_score, *guard_params, fetch_limit)
            if platform_term:
                params = (min_score, *guard_params, platform_term, fetch_limit)
        else:
            sql = f"""
                WITH ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY topic_group
                               ORDER BY
                                   relevance_score DESC,
                                   last_scored_at DESC,
                                   view_count DESC,
                                   bvid ASC
                           ) AS group_rank
                    FROM content_cache
                    WHERE {_POOL_SERVABLE_STATUS_SQL}
                      AND COALESCE(feedback_type, '') != 'dislike'
                      AND COALESCE(relevance_score, 0.0) >= ?
                      AND COALESCE(pool_expression, '') != ''
                      AND COALESCE(pool_topic_label, '') != ''
                      AND COALESCE(style_key, '') != ''
                      AND COALESCE(topic_group, '') != ''
                      AND (
                        source_platform != 'xiaohongshu'
                        OR (content_url LIKE '%xsec_token=%' AND discovered_at >= '2026-08-15')
                      )
                      {guard_sql}
                      {delight_guard_sql}
                      {platform_clause}
                      {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
                )
                SELECT * FROM ranked
                WHERE group_rank <= ?
                ORDER BY
                    CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                    relevance_score DESC,
                    last_scored_at DESC,
                    view_count DESC,
                    bvid ASC
                LIMIT ?
            """
            params = (min_score, *guard_params, max_per_topic_group, fetch_limit)
            if platform_term:
                params = (min_score, *guard_params, platform_term, max_per_topic_group, fetch_limit)
        cursor = self.conn.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return self._balance_pool_rows(rows, limit=limit)

    def count_pool_candidates(
        self, *, max_per_topic_group: int = 0, xhs_self_nickname: str = ""
    ) -> int:
        """Return how many fresh candidates are immediately available for reshuffle."""
        return len(
            self._load_available_pool_candidate_rows(
                max_per_topic_group=max_per_topic_group,
                xhs_self_nickname=xhs_self_nickname,
            )
        )

    def _load_available_pool_candidate_rows(
        self, *, max_per_topic_group: int = 0, xhs_self_nickname: str = ""
    ) -> list[dict[str, Any]]:
        """Load rows counted by the frontend-visible pool availability gate."""
        from openbiliclaw.storage.database import (
            _DELIGHT_CLAIM_GUARD_SQL,
            _POOL_NOT_RECENTLY_RECOMMENDED_SQL,
            _POOL_SERVABLE_STATUS_SQL,
            _is_linkable_pool_source,
            _xhs_self_author_guard_params,
            _xhs_self_author_guard_sql,
        )

        self._ensure_fresh_read()
        min_score = self._pool_admission_min_score()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_guard_sql = _DELIGHT_CLAIM_GUARD_SQL
        if max_per_topic_group > 0:
            cursor = self.conn.execute(
                f"""
                WITH ranked AS (
                    SELECT bvid, source, source_platform, content_url,
                           ROW_NUMBER() OVER (
                               PARTITION BY topic_group
                               ORDER BY
                                   relevance_score DESC,
                                   last_scored_at DESC,
                                   view_count DESC,
                                   bvid ASC
                           ) AS group_rank
                    FROM content_cache
                    WHERE {_POOL_SERVABLE_STATUS_SQL}
                      AND COALESCE(feedback_type, '') != 'dislike'
                      AND COALESCE(relevance_score, 0.0) >= ?
                      AND COALESCE(pool_expression, '') != ''
                      AND COALESCE(pool_topic_label, '') != ''
                      AND COALESCE(style_key, '') != ''
                      AND COALESCE(topic_group, '') != ''
                      AND (
                        source_platform != 'xiaohongshu'
                        OR (content_url LIKE '%xsec_token=%' AND discovered_at >= '2026-08-15')
                      )
                      {guard_sql}
                      {delight_guard_sql}
                      {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
                )
                SELECT bvid, source, source_platform, content_url
                FROM ranked
                WHERE group_rank <= ?
                """,
                (min_score, *guard_params, max_per_topic_group),
            )
        else:
            cursor = self.conn.execute(
                f"""
                SELECT bvid, source, source_platform, content_url
                FROM content_cache
                WHERE {_POOL_SERVABLE_STATUS_SQL}
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND COALESCE(style_key, '') != ''
                  AND COALESCE(topic_group, '') != ''
                  AND (
                    source_platform != 'xiaohongshu'
                    OR (content_url LIKE '%xsec_token=%' AND discovered_at >= '2026-08-15')
                  )
                  {guard_sql}
                  {delight_guard_sql}
                  {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
                """,
                (min_score, *guard_params),
            )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            rows.append(row_dict)
        return rows

    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 0, xhs_self_nickname: str = ""
    ) -> dict[str, int]:
        """Return frontend-visible pool availability grouped by source family."""
        from openbiliclaw.storage.database import _pool_source_family

        rows = self._load_available_pool_candidate_rows(
            max_per_topic_group=max_per_topic_group,
            xhs_self_nickname=xhs_self_nickname,
        )
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)
