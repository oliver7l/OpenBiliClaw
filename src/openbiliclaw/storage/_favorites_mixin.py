"""Database mixin: favorites.

从 ``storage/database.py`` 拆出的 favorites 表操作组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

from typing import Any


class FavoritesMixin:
    """收藏夹的读写方法。"""

    conn: Any  # 由 Database 提供
    _execute_write: Any  # 由 Database 提供

    def add_to_favorites(self, bvid: str, note: str = "") -> bool:
        """Save a video to favorites. Returns True if newly inserted."""
        self._execute_write(
            """
            INSERT INTO favorites (bvid, note)
            VALUES (?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note
            """,
            (bvid.strip(), note),
        )
        return self._content.total_changes > 0

    def remove_from_favorites(self, bvid: str) -> bool:
        """Remove a favorite. Returns True if a row was deleted."""
        self._execute_write(
            "DELETE FROM favorites WHERE bvid = ?",
            (bvid.strip(),),
        )
        return self._content.total_changes > 0

    def is_in_favorites(self, bvid: str) -> bool:
        """Check whether a video is favorited."""
        row = self._content.execute(
            "SELECT 1 FROM favorites WHERE bvid = ?",
            (bvid.strip(),),
        ).fetchone()
        return row is not None

    def count_favorites(self) -> int:
        """Return total number of favorited videos."""
        row = self._content.execute("SELECT COUNT(*) FROM favorites").fetchone()
        return int(row[0]) if row else 0

    def list_favorites(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return favorited videos, newest first.

        v0.4.0+: favorites 表迁移到 content.db，content_cache 在 pool.db，
        暂不跨库 JOIN，只返回 favorites 基础字段。
        """
        cursor = self._content.execute(
            """
            SELECT
                f.bvid,
                f.added_at,
                f.note,
                '' AS title,
                '' AS up_name,
                '' AS cover_url,
                '' AS content_url,
                '' AS source_platform
            FROM favorites AS f
            ORDER BY f.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_favorites_legacy(self) -> int:
        return int(self._content.execute("SELECT COUNT(*) FROM favorites").fetchone()[0])
