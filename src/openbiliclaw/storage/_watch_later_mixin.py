"""Database mixin: watch_later bookmarks.

从 ``storage/database.py`` 拆出的 watch_later（稍后再看）书签操作组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

from typing import Any


class WatchLaterMixin:
    """稍后再看书签的读写方法。"""

    conn: Any  # 由 Database 提供
    _execute_write: Any  # 由 Database 提供

    def add_to_watch_later(self, bvid: str, note: str = "") -> bool:
        """Bookmark a video. Returns True if newly inserted, False if updated."""
        self._execute_write(
            """
            INSERT INTO watch_later (bvid, note)
            VALUES (?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note
            """,
            (bvid.strip(), note),
        )
        return self._content.total_changes > 0

    def remove_from_watch_later(self, bvid: str) -> bool:
        """Remove a bookmark. Returns True if a row was deleted."""
        self._execute_write(
            "DELETE FROM watch_later WHERE bvid = ?",
            (bvid.strip(),),
        )
        return self._content.total_changes > 0

    def is_in_watch_later(self, bvid: str) -> bool:
        """Check whether a video is bookmarked."""
        row = self._content.execute(
            "SELECT 1 FROM watch_later WHERE bvid = ?",
            (bvid.strip(),),
        ).fetchone()
        return row is not None

    def count_watch_later(self) -> int:
        """Return total number of bookmarked videos."""
        row = self._content.execute("SELECT COUNT(*) FROM watch_later").fetchone()
        return int(row[0]) if row else 0

    def list_watch_later(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return bookmarked videos, newest first.

        v0.4.0+: watch_later 表迁移到 content.db，content_cache 在 pool.db，
        暂不跨库 JOIN，只返回 watch_later 基础字段。
        """
        cursor = self._content.execute(
            """
            SELECT
                w.bvid,
                w.added_at,
                w.note,
                '' AS title,
                '' AS up_name,
                '' AS cover_url,
                '' AS content_url,
                '' AS source_platform
            FROM watch_later AS w
            ORDER BY w.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_watch_later_legacy(self) -> int:
        return int(self._content.execute("SELECT COUNT(*) FROM watch_later").fetchone()[0])
