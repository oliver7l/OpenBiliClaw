"""Database mixin: content quality scoring.

Contains methods for updating and fetching LLM-computed quality scores
and recommendation reasons for content_cache items.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class QualityMixin:
    """Database methods for content quality scoring."""

    conn: Any
    _execute_write: Any

    def update_content_quality_score(
        self, bvid: str, *, quality_score: float, quality_reason: str
    ) -> None:
        """Update LLM quality score and recommendation reason for a content item."""
        self._execute_write(
            """
            UPDATE content_cache
            SET quality_score = ?,
                quality_reason = ?,
                last_scored_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (quality_score, quality_reason, bvid),
        )

    def batch_update_content_quality_scores(self, scores: list[tuple[str, float, str]]) -> None:
        """Batch update quality scores for multiple content items."""
        cursor = self.conn.cursor()
        try:
            cursor.executemany(
                """
                UPDATE content_cache
                SET quality_score = ?,
                    quality_reason = ?,
                    last_scored_at = CURRENT_TIMESTAMP
                WHERE bvid = ?
                """,
                [(score, reason, bvid) for bvid, score, reason in scores],
            )
            self.conn.commit()
        except Exception:
            logger.exception("Failed to batch update quality scores")
            self.conn.rollback()

    def batch_get_quality_scores(self, bvids: list[str]) -> list[dict[str, object]]:
        """Fetch quality scores for a batch of bvids."""
        if not bvids:
            return []
        placeholders = ",".join("?" for _ in bvids)
        try:
            cursor = self.conn.execute(
                f"""
                SELECT bvid, quality_score, quality_reason
                FROM content_cache
                WHERE bvid IN ({placeholders})
                AND quality_score > 0.0
                """,
                bvids,
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to batch get quality scores")
            return []
