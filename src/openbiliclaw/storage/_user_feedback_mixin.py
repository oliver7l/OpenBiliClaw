"""Database mixin: user feedback.

从 ``storage/database.py`` 拆出的 user_feedback 表操作组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

from typing import Any


class UserFeedbackMixin:
    """用户反馈（点赞/点踩）的读写方法。"""

    conn: Any  # 由 Database 提供

    def insert_user_feedback(
        self,
        bvid: str,
        action: str,
        *,
        source_platform: str = "",
        title: str = "",
        topic_group: str = "",
        body_text: str = "",
    ) -> bool:
        """Record a like/dislike for a content item.  Returns True if
        inserted, False if the same (bvid, action) already exists
        (upsert-style: replace the existing row).
        """
        existing = self.conn.execute(
            "SELECT id FROM user_feedback WHERE bvid = ? AND action = ?",
            (bvid, action),
        ).fetchone()
        if existing:
            # Update timestamp
            self.conn.execute(
                "UPDATE user_feedback SET created_at = CURRENT_TIMESTAMP WHERE id = ?",
                (existing["id"],),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            """INSERT INTO user_feedback (bvid, action, source_platform, title, topic_group, body_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bvid, action, source_platform, title, topic_group, body_text),
        )
        self.conn.commit()
        return True

    def remove_user_feedback(self, bvid: str, action: str) -> bool:
        """Remove a specific feedback action for a content item."""
        cur = self.conn.execute(
            "DELETE FROM user_feedback WHERE bvid = ? AND action = ?",
            (bvid, action),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_user_feedback(self, bvid: str) -> list[dict[str, Any]]:
        """Get all feedback actions for a content item."""
        rows = self.conn.execute(
            "SELECT action, created_at FROM user_feedback WHERE bvid = ? ORDER BY created_at DESC",
            (bvid,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_user_feedback_batch(self, bvids: list[str]) -> dict[str, str]:
        """Get the latest feedback action for each bvid. Returns {bvid: action}."""
        if not bvids:
            return {}
        placeholders = ",".join("?" for _ in bvids)
        rows = self.conn.execute(
            f"""SELECT bvid, action FROM user_feedback
                WHERE bvid IN ({placeholders})
                GROUP BY bvid
                ORDER BY MAX(created_at) DESC""",
            bvids,
        ).fetchall()
        return {r["bvid"]: r["action"] for r in rows}

    def get_total_feedback_count(self) -> int:
        """Return total number of feedback entries (likes + dislikes)."""
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM user_feedback").fetchone()
        return row["cnt"] if row else 0

    def get_feedback_aggregated(self) -> list[dict[str, Any]]:
        """Aggregate feedback per topic_group with like/dislike counts."""
        rows = self.conn.execute("""
            SELECT topic_group,
                   SUM(CASE WHEN action = 'like' THEN 1 ELSE 0 END) as likes,
                   SUM(CASE WHEN action = 'dislike' THEN 1 ELSE 0 END) as dislikes
            FROM user_feedback
            WHERE topic_group != '' AND topic_group IS NOT NULL
            GROUP BY topic_group
            ORDER BY likes DESC
        """).fetchall()
        return [
            {
                "topic_group": str(r["topic_group"] or ""),
                "likes": int(r["likes"] or 0),
                "dislikes": int(r["dislikes"] or 0),
            }
            for r in rows
        ]
