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

    # ── View event extraction & filtering ──────────────────────────

    @classmethod
    def _extract_content_keys_from_view_event(cls, row: dict[str, Any]) -> set[str]:
        from openbiliclaw.storage.database import (
            _VIEW_CONTENT_ID_METADATA_KEYS,
            _BILIBILI_SOURCE_FAMILY,
            _normalize_source_platform_key,
        )

        metadata = cls._decode_event_metadata(row)
        url = str(row.get("url", "")).strip()

        platform = _normalize_source_platform_key(metadata.get("source_platform", ""))
        if not platform:
            platform = cls._infer_source_platform_from_url(url)

        content_ids: set[str] = set()
        for key in _VIEW_CONTENT_ID_METADATA_KEYS:
            raw_value = metadata.get(key, "")
            if isinstance(raw_value, (str, int)):
                value = str(raw_value).strip()
                if value:
                    content_ids.add(value)

        url_content_id = cls._extract_content_id_from_url(platform, url)
        if url_content_id:
            content_ids.add(url_content_id)

        bvid = cls._extract_bvid_from_view_event(row)
        if bvid:
            content_ids.add(bvid)
            platform = platform or _BILIBILI_SOURCE_FAMILY

        keys: set[str] = set()
        for content_id in content_ids:
            if content_id.startswith("BV"):
                keys.add(content_id)
            if platform:
                keys.add(f"{platform}:{content_id}")
        return keys

    @staticmethod
    def _infer_source_platform_from_url(url: str) -> str:
        from urllib.parse import urlparse

        from openbiliclaw.storage.database import (
            _BILIBILI_SOURCE_FAMILY,
            _XHS_SOURCE_FAMILY,
            _DOUYIN_SOURCE_FAMILY,
            _YOUTUBE_SOURCE_FAMILY,
            _TWITTER_SOURCE_FAMILY,
        )

        if not url:
            return ""
        host = urlparse(url).netloc.lower()
        if "bilibili.com" in host or host == "b23.tv":
            return _BILIBILI_SOURCE_FAMILY
        if "xiaohongshu.com" in host or "xhslink.com" in host:
            return _XHS_SOURCE_FAMILY
        if "douyin.com" in host:
            return _DOUYIN_SOURCE_FAMILY
        if "youtube.com" in host or host == "youtu.be":
            return _YOUTUBE_SOURCE_FAMILY
        if (
            host == "x.com"
            or host.endswith(".x.com")
            or host == "twitter.com"
            or host.endswith(".twitter.com")
        ):
            return _TWITTER_SOURCE_FAMILY
        return ""

    @staticmethod
    def _extract_content_id_from_url(platform: str, url: str) -> str:
        from urllib.parse import urlparse, parse_qs

        from openbiliclaw.storage.database import (
            _XHS_SOURCE_FAMILY,
            _DOUYIN_SOURCE_FAMILY,
            _YOUTUBE_SOURCE_FAMILY,
            _BILIBILI_SOURCE_FAMILY,
            _BVID_PATTERN,
        )

        if not url:
            return ""
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if platform == _XHS_SOURCE_FAMILY:
            if len(path_parts) >= 2 and path_parts[0] == "explore":
                return path_parts[1]
            if len(path_parts) >= 3 and path_parts[:2] == ["discovery", "item"]:
                return path_parts[2]
        if platform == _DOUYIN_SOURCE_FAMILY and "video" in path_parts:
            video_index = path_parts.index("video")
            if len(path_parts) > video_index + 1:
                return path_parts[video_index + 1]
        if platform == _YOUTUBE_SOURCE_FAMILY:
            query_video_id = parse_qs(parsed.query).get("v", [""])[0].strip()
            if query_video_id:
                return query_video_id
            if parsed.netloc.lower() == "youtu.be" and path_parts:
                return path_parts[0]
            for prefix in ("shorts", "embed", "live"):
                if prefix in path_parts:
                    prefix_index = path_parts.index(prefix)
                    if len(path_parts) > prefix_index + 1:
                        return path_parts[prefix_index + 1]
        if platform == _BILIBILI_SOURCE_FAMILY:
            match = _BVID_PATTERN.search(url)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_bvid_from_view_event(row: dict[str, Any]) -> str:
        from openbiliclaw.storage.database import Database, _BVID_PATTERN

        metadata = Database._decode_event_metadata(row)
        bvid = str(metadata.get("bvid", "")).strip()
        if bvid:
            return bvid

        url = str(row.get("url", "")).strip()
        match = _BVID_PATTERN.search(url)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _content_row_view_keys(row: dict[str, Any]) -> set[str]:
        from openbiliclaw.storage.database import _normalize_source_platform_key, _pool_source_family

        platform = _normalize_source_platform_key(row.get("source_platform", ""))
        if not platform:
            platform = _pool_source_family(row.get("source", ""), row.get("source_platform", ""))
            if platform == "unknown":
                platform = ""

        keys: set[str] = set()
        raw_bvid = str(row.get("bvid", "") or "").strip()
        content_id = str(row.get("content_id", "") or "").strip() or raw_bvid
        for value in {raw_bvid, content_id}:
            if not value:
                continue
            if value.startswith("BV"):
                keys.add(value)
            if platform:
                keys.add(f"{platform}:{value}")
        return keys

    @staticmethod
    def _is_viewed_row(row: dict[str, Any], viewed_content_keys: set[str]) -> bool:
        from openbiliclaw.storage.database import Database

        if not viewed_content_keys:
            return False
        return bool(Database._content_row_view_keys(row) & viewed_content_keys)

    @staticmethod
    def _exclude_viewed_rows(
        rows: list[dict[str, Any]],
        viewed_content_keys: set[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        from openbiliclaw.storage.database import Database

        if not viewed_content_keys:
            return rows[:limit]
        filtered = [row for row in rows if not Database._is_viewed_row(row, viewed_content_keys)]
        return filtered[:limit]
