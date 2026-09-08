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

    # ── Raw material & readiness ────────────────────────────────────

    def _load_pool_raw_material_rows(self) -> list[dict[str, Any]]:
        """Load raw fresh material rows governed by the raw ceiling."""
        self._ensure_fresh_read()
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT
                bvid,
                source,
                source_platform,
                content_url,
                relevance_score,
                last_scored_at,
                pool_expression,
                pool_topic_label,
                style_key,
                topic_group
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            (min_score,),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            rows.append(row_dict)
        return rows

    def count_pool_raw_material_candidates(self) -> int:
        """Return raw fresh material count used for raw-ceiling headroom."""
        return (
            len(self._load_pool_raw_material_rows()) + self._count_pending_discovery_raw_material()
        )

    def count_pool_raw_material_by_source(self) -> dict[str, int]:
        """Return raw fresh material grouped by source family."""
        from openbiliclaw.storage.database import _pool_source_family

        counts: dict[str, int] = defaultdict(int)
        for row in self._load_pool_raw_material_rows():
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        cursor = self.conn.execute(
            """
            SELECT source_platform, source_strategy, COUNT(*) AS count
            FROM discovery_candidates
            WHERE status IN ('pending_eval', 'evaluating', 'evaluated')
            GROUP BY source_platform, source_strategy
            """
        )
        for row in cursor.fetchall():
            source_family = _pool_source_family(row["source_strategy"], row["source_platform"])
            counts[source_family] += int(row["count"])
        return dict(counts)

    def count_pool_readiness(
        self, *, xhs_self_nickname: str = "", allow_stale: bool = False
    ) -> dict[str, int]:
        """Return pool inventory split by immediately servable and pending rows.

        结果缓存 300 秒，避免频繁重复计算。``allow_stale=True`` 时缓存过期仍先
        返回旧值，并在后台线程重算（stale-while-revalidate）。
        """
        import time as _time

        from openbiliclaw.storage.database import (
            _POOL_NOT_RECENTLY_RECOMMENDED_SQL,
            _POOL_SERVABLE_STATUS_SQL,
            _xhs_self_author_guard_params,
            _xhs_self_author_guard_sql,
        )

        if self._pool_readiness_cache is not None:
            cached_at, cached_result = self._pool_readiness_cache
            if _time.time() - cached_at < self._pool_readiness_cache_ttl:
                return dict(cached_result)
            if allow_stale:
                if not self._pool_readiness_refreshing:
                    self._pool_readiness_refreshing = True
                    import threading

                    def _recalc() -> None:
                        try:
                            self.count_pool_readiness(xhs_self_nickname=xhs_self_nickname)
                        except Exception:
                            pass
                        finally:
                            self._pool_readiness_refreshing = False

                    threading.Thread(target=_recalc, daemon=True).start()
                return dict(cached_result)

        self._ensure_fresh_read()
        min_score = self._pool_admission_min_score()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        raw_cursor = self.conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM content_cache
            WHERE {_POOL_SERVABLE_STATUS_SQL}
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              {guard_sql}
              {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
            """,
            (min_score, *guard_params),
        )
        raw_count = int(raw_cursor.fetchone()["count"])
        pending_cursor = self.conn.execute(
            rf"""
            SELECT bvid, content_id, source, source_platform
            FROM content_cache
            WHERE {_POOL_SERVABLE_STATUS_SQL}
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              {guard_sql}
              {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
              AND (
                  COALESCE(pool_expression, '') = ''
                  OR COALESCE(pool_topic_label, '') = ''
                  OR COALESCE(style_key, '') = ''
                  OR COALESCE(topic_group, '') = ''
                  OR (
                      (
                          LOWER(COALESCE(source_platform, '')) IN ('xiaohongshu', 'xhs')
                          OR LOWER(COALESCE(source, '')) LIKE 'xhs-%'
                          OR LOWER(COALESCE(source, '')) LIKE 'xhs\_%'
                          OR LOWER(COALESCE(source, '')) LIKE 'xiaohongshu%'
                      )
                      AND COALESCE(content_url, '') NOT LIKE '%xsec_token=%'
                  )
              )
            """,
            (min_score, *guard_params),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        pending_count = 0
        for row in pending_cursor.fetchall():
            if self._is_viewed_row(dict(row), viewed_content_keys):
                continue
            pending_count += 1

        status_counts = self.count_discovery_candidates_by_status()
        pending_eval_count = int(status_counts.get("pending_eval", 0)) + int(
            status_counts.get("evaluating", 0)
        )
        evaluated_pending_count = int(status_counts.get("evaluated", 0))
        discovery_pending_count = pending_eval_count + evaluated_pending_count

        result = {
            "available": self.count_pool_candidates(xhs_self_nickname=xhs_self_nickname),
            "raw": raw_count + discovery_pending_count,
            "pending": pending_count + discovery_pending_count,
            "pending_eval": pending_eval_count,
            "evaluated_pending": evaluated_pending_count,
        }
        self._pool_readiness_cache = (_time.time(), dict(result))
        return result

    def count_pool_candidates_by_source(self) -> dict[str, int]:
        """Return fresh pool counts grouped by discovery source family."""
        from openbiliclaw.storage.database import _is_linkable_pool_source, _pool_source_family

        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            (min_score,),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)

    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]:
        """Return fresh pool counts grouped by topic, style, and franchise."""
        from openbiliclaw.storage.database import _is_linkable_pool_source

        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT bvid, topic_group, style_key, franchise_key, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            (min_score,),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, dict[str, int]] = {
            "topic_group": defaultdict(int),
            "style_key": defaultdict(int),
            "franchise_key": defaultdict(int),
        }
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            for axis in ("topic_group", "style_key", "franchise_key"):
                value = str(row[axis] or "").strip()
                if value:
                    counts[axis][value] += 1
        return {axis: dict(axis_counts) for axis, axis_counts in counts.items()}

    # ── Pool trimming & source balance ──────────────────────────────

    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Suppress overflow fresh items so the pool does not exceed *target*."""
        from openbiliclaw.storage.database import _pool_source_family, logger

        if target <= 0:
            return 0

        rows = self._load_pool_raw_material_rows()
        if len(rows) <= target:
            return 0

        ranked = sorted(rows, key=self._pool_trim_keep_key)

        if source_share_quotas:
            counts_per_source: dict[str, int] = defaultdict(int)
            for row in rows:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                counts_per_source[source_family] += 1

            protected: list[dict[str, Any]] = []
            negotiable_tracked: list[dict[str, Any]] = []
            negotiable_untracked: list[dict[str, Any]] = []
            seen: dict[str, int] = defaultdict(int)
            for row in ranked:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                quota = source_share_quotas.get(source_family)
                if quota is None:
                    negotiable_untracked.append(row)
                    continue
                if counts_per_source[source_family] <= quota:
                    protected.append(row)
                else:
                    if seen[source_family] < quota:
                        protected.append(row)
                        seen[source_family] += 1
                    else:
                        negotiable_tracked.append(row)
            ranked = protected + negotiable_untracked + negotiable_tracked

        overflow_rows = ranked[target:]
        overflow_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_to_target_count: target=%d, before=%d, "
            "suppressed=%d, by-source: %s",
            target,
            len(rows),
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def trim_pool_source_overflow(self, *, source_share_quotas: dict[str, int]) -> int:
        """Suppress fresh rows that exceed platform-family pool quotas."""
        from openbiliclaw.storage.database import _pool_source_family, logger

        clean_quotas: dict[str, int] = {}
        for source_family, quota in source_share_quotas.items():
            try:
                clean_quotas[str(source_family)] = max(0, int(quota))
            except (TypeError, ValueError):
                continue
        if not clean_quotas:
            return 0

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._load_pool_raw_material_rows():
            source_family = _pool_source_family(row["source"], row["source_platform"])
            if source_family in clean_quotas:
                grouped[source_family].append(row)

        overflow_rows: list[dict[str, Any]] = []
        for source_family, rows in grouped.items():
            quota = clean_quotas[source_family]
            if len(rows) <= quota:
                continue
            ranked = sorted(rows, key=self._pool_trim_keep_key)
            overflow_rows.extend(ranked[quota:])

        clean_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in clean_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_source_overflow: suppressed=%d, by-source: %s",
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Move suppressed candidates back to fresh for under-quota source families."""
        from openbiliclaw.storage.database import _is_linkable_pool_source, _pool_source_family

        if target <= 0 or not source_share_quotas:
            return 0

        current_counts = self.count_pool_available_candidates_by_source()
        raw_counts = self.count_pool_raw_material_by_source()
        raw_quotas = raw_source_share_quotas or source_share_quotas
        deficits = {
            source_family: min(
                min(target, max(0, int(quota))) - int(current_counts.get(source_family, 0)),
                max(
                    0,
                    int(raw_quotas.get(source_family, quota))
                    - int(raw_counts.get(source_family, 0)),
                ),
            )
            for source_family, quota in source_share_quotas.items()
            if int(quota) > 0
        }
        deficits = {source: deficit for source, deficit in deficits.items() if deficit > 0}
        if not deficits:
            return 0

        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'suppressed'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                bvid ASC
            """,
            (min_score,),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        selected_bvids: list[str] = []
        selected_counts: dict[str, int] = defaultdict(int)
        target_selection_count = sum(deficits.values())

        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            deficit = deficits.get(source_family, 0)
            if deficit <= 0 or selected_counts[source_family] >= deficit:
                continue
            selected_bvids.append(bvid)
            selected_counts[source_family] += 1
            if len(selected_bvids) >= target_selection_count:
                break

        if not selected_bvids:
            return 0

        placeholders = ", ".join("?" for _ in selected_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'fresh'
            WHERE bvid IN ({placeholders})
            """,
            selected_bvids,
        )
        return len(selected_bvids)
