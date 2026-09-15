"""Database mixin: watch_later bookmarks.

从 ``storage/database.py`` 拆出的 watch_later（稍后再看）书签操作组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

from typing import Any


class WatchLaterMixin:
    """稍后再看书签的读写方法。"""

    _content_conn: Any  # 由 Database 提供（content.db）

    @property
    def _content(self) -> Any:
        """content.db 连接，缺省回退主库。"""
        return getattr(self, "_content_conn", None) or self.conn

    def _content_write(self, sql: str, params: tuple | None = None) -> None:
        """写入 content.db 并自动 commit。"""
        self._content.execute(sql, params or ())
        self._content.commit()

    def add_to_watch_later(self, bvid: str, note: str = "") -> bool:
        """Bookmark a video. Returns True if newly inserted, False if updated."""
        before = self._content.total_changes
        self._content_write(
            """
            INSERT INTO watch_later (bvid, note)
            VALUES (?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note
            """,
            (bvid.strip(), note),
        )
        return self._content.total_changes > before

    def remove_from_watch_later(self, bvid: str) -> bool:
        """Remove a bookmark. Returns True if a row was deleted."""
        before = self._content.total_changes
        self._content_write(
            "DELETE FROM watch_later WHERE bvid = ?",
            (bvid.strip(),),
        )
        return self._content.total_changes > before

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
        """Return bookmarked videos with metadata, newest first.

        ``watch_later`` 在 content.db，``content_cache`` 在 pool.db。主连接（以及每线程
        连接 / open_connection）都同时 ATTACH 了这两个子库，故走 ``self.conn`` 做跨库
        LEFT JOIN 取回标题/UP/封面等元数据。

        注意：不要再退回 ``self._content`` —— 那是 content.db 的独立连接，未 ATTACH pool，
        曾导致本方法只能返回空字符串元数据（v0.4.0 拆库遗留的功能回退）。
        """
        cursor = self.conn.execute(
            """
            SELECT
                w.bvid,
                w.added_at,
                w.note,
                COALESCE(c.title, '')           AS title,
                COALESCE(c.up_name, '')         AS up_name,
                COALESCE(c.cover_url, '')       AS cover_url,
                COALESCE(c.content_url, '')     AS content_url,
                COALESCE(c.source_platform, '') AS source_platform
            FROM content.watch_later AS w
            LEFT JOIN pool.content_cache AS c ON c.bvid = w.bvid
            ORDER BY w.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_watch_later_legacy(self) -> int:
        return int(self._content.execute("SELECT COUNT(*) FROM watch_later").fetchone()[0])
