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
        return self.conn.total_changes > 0

    def remove_from_favorites(self, bvid: str) -> bool:
        """Remove a favorite. Returns True if a row was deleted."""
        self._execute_write(
            "DELETE FROM favorites WHERE bvid = ?",
            (bvid.strip(),),
        )
        return self.conn.total_changes > 0

    def is_in_favorites(self, bvid: str) -> bool:
        """Check whether a video is favorited."""
        row = self.conn.execute(
            "SELECT 1 FROM favorites WHERE bvid = ?",
            (bvid.strip(),),
        ).fetchone()
        return row is not None

    def count_favorites(self) -> int:
        """Return total number of favorited videos."""
        row = self.conn.execute("SELECT COUNT(*) FROM favorites").fetchone()
        return int(row[0]) if row else 0

    def list_favorites(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return favorited videos with content_cache metadata, newest first."""
        cursor = self.conn.execute(
            """
            SELECT
                f.bvid,
                f.added_at,
                f.note,
                COALESCE(c.title, '') AS title,
                COALESCE(c.up_name, '') AS up_name,
                COALESCE(c.cover_url, '') AS cover_url,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform
            FROM favorites AS f
            LEFT JOIN content_cache AS c ON c.bvid = f.bvid
            ORDER BY f.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_favorites_legacy(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0])
