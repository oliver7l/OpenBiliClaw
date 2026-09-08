"""Database mixin: view history and dwell-based interest signals.

Contains methods for recording content views, aggregating dwell-time
interest scores, and fetching recent view history for recommendation
de-dup and interest centroid computation.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class ViewHistoryMixin:
    """Database methods for view history and dwell signals."""

    conn: Any
    _execute_write: Any

    def insert_view_history(self, item: dict[str, Any]) -> None:
        """Record a content view / click."""
        self.conn.execute(
            """INSERT INTO view_history
               (bvid, title, source_platform, topic_group, content_url, up_name, quality_score, fit_score, dwell_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(item.get("bvid", "")),
                str(item.get("title", "") or ""),
                str(item.get("source_platform", "") or ""),
                str(item.get("topic_group", "") or ""),
                str(item.get("content_url", "") or ""),
                str(item.get("up_name", "") or item.get("author_name", "") or ""),
                float(item.get("quality_score", 0) or 0),
                float(item.get("fit_score", 0) or 0),
                float(item.get("dwell_seconds", 0) or 0),
            ),
        )
        self.conn.commit()

    def update_view_dwell(self, bvid: str, dwell_seconds: float) -> bool:
        """Attach dwell seconds to the most recent view of bvid."""
        row = self.conn.execute(
            "SELECT id FROM view_history WHERE bvid = ? ORDER BY id DESC LIMIT 1",
            (bvid,),
        ).fetchone()
        if not row:
            return False
        self.conn.execute(
            "UPDATE view_history SET dwell_seconds = ? WHERE id = ?",
            (float(dwell_seconds), row["id"]),
        )
        self.conn.commit()
        return True

    def get_dwell_scores(self, days: int = 14) -> dict[str, float]:
        """Aggregate dwell-weighted interest per topic_group (implicit feedback)."""
        import datetime

        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        try:
            rows = self.conn.execute(
                """SELECT topic_group,
                          SUM(MIN(dwell_seconds, 600)) AS dwell_sum,
                          COUNT(*) AS views,
                          SUM(CASE WHEN dwell_seconds >= 60 THEN 1 ELSE 0 END) AS deep_views,
                          SUM(CASE WHEN dwell_seconds > 0 AND dwell_seconds < 15 THEN 1 ELSE 0 END) AS quick_exits
                   FROM view_history
                   WHERE viewed_at >= ? AND COALESCE(topic_group, '') != ''
                   GROUP BY topic_group""",
                (cutoff,),
            ).fetchall()
        except Exception:
            return {}
        scores: dict[str, float] = {}
        for r in rows:
            dwell_sum = float(r["dwell_sum"] or 0)
            deep = int(r["deep_views"] or 0)
            quick = int(r["quick_exits"] or 0)
            base = min(1.0, dwell_sum / 1800.0)
            penalty = 0.05 * quick
            boost = 0.1 * deep
            scores[str(r["topic_group"])] = max(0.0, min(1.0, base + boost - penalty))
        return scores

    def get_total_view_count(self, days: int = 30) -> int:
        """Count views recorded in the last N days (implicit feedback volume)."""
        import datetime

        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) AS cnt FROM view_history WHERE viewed_at >= ?",
                (cutoff,),
            ).fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            return 0

    def get_interest_centroid_sources(
        self,
        *,
        days: int = 30,
        min_dwell: float = 60.0,
    ) -> list[dict[str, Any]]:
        """Recent positive-signal rows backing the RankAgent interest centroids."""
        import datetime

        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        try:
            rows = self.conn.execute(
                """
                SELECT uf.topic_group AS topic_group,
                       uf.title       AS title,
                       COALESCE(cc.description, '') AS description,
                       uf.created_at  AS signaled_at
                FROM user_feedback uf
                LEFT JOIN content_cache cc ON cc.bvid = uf.bvid
                WHERE uf.action = 'like'
                  AND uf.created_at >= ?
                  AND COALESCE(uf.topic_group, '') != ''
                UNION ALL
                SELECT vh.topic_group AS topic_group,
                       vh.title       AS title,
                       COALESCE(cc.description, '') AS description,
                       vh.viewed_at   AS signaled_at
                FROM view_history vh
                LEFT JOIN content_cache cc ON cc.bvid = vh.bvid
                WHERE vh.viewed_at >= ?
                  AND vh.dwell_seconds >= ?
                  AND COALESCE(vh.topic_group, '') != ''
                ORDER BY signaled_at DESC
                """,
                (cutoff, cutoff, float(min_dwell)),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to load interest centroid sources")
            return []

    def get_recent_views(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get the most recent view history."""
        rows = self.conn.execute(
            """SELECT * FROM view_history
               ORDER BY viewed_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_view_count(self, bvid: str) -> int:
        """Get how many times a content item has been viewed."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM view_history WHERE bvid = ?",
            (bvid,),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_viewed_bvids(self, days: int = 30) -> set[str]:
        """Get bvids viewed in the last N days."""
        import datetime

        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT DISTINCT bvid FROM view_history WHERE viewed_at >= ?",
            (cutoff,),
        ).fetchall()
        return {r["bvid"] for r in rows}
