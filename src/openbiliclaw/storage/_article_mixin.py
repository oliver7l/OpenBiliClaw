"""Database mixin: articles (read/write core).

从 ``storage/database.py`` 拆出的 articles 表核心操作组。
``Database`` 类继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

import json
import random
from typing import Any


class ArticleMixin:
    """文章库的核心读写方法。"""

    conn: Any  # 由 Database 提供

    def upsert_article(
        self,
        source_type: str,
        source_name: str,
        title: str,
        url: str,
        author: str = "",
        summary: str = "",
        content_text: str = "",
        published_at: str = "",
        tags: list[str] | None = None,
    ) -> int | None:
        """Insert or update an article. Returns row id or None on failure.

        ``tags`` defaults to the source name so every entry is filterable by
        origin; existing rows keep whatever tags they already have.
        """
        from datetime import datetime as _dt
        from datetime import timedelta as _td
        from datetime import timezone as _tz

        from openbiliclaw.storage.database import logger

        # 北京时间(UTC+8): articles 表所有时间字段统一存本地时间字符串
        cn_tz = _tz(_td(hours=8))
        if not published_at:
            published_at = _dt.now(cn_tz).strftime("%Y-%m-%d %H:%M:%S")

        # Knowledge Forge 任务 1.0：入库时同步调用正文清理器，
        # 生成 content_cleaned 及清理质量/验证标记（规则清理，确定性且快速）。
        # 清理失败不阻断入库，仅降级为新列留空，由批量清理管线后补。
        content_cleaned: str | None = None
        content_clean_score: float | None = None
        content_clean_log: str | None = None
        content_verified: int | None = None
        content_verify_result: str | None = None
        if content_text and content_text.strip():
            try:
                from openbiliclaw.knowledge_forge.content_cleaner import ContentCleaner

                cr = ContentCleaner().clean(content_text, title=title, source_type=source_type)
                content_cleaned = cr.cleaned_text or None
                content_clean_score = cr.clean_score
                content_clean_log = json.dumps(cr.operations, ensure_ascii=False, default=str)
                content_verified = 1 if cr.verified else 0
                content_verify_result = json.dumps(
                    cr.verify_issues, ensure_ascii=False, default=str
                )
            except Exception:
                logger.exception("Knowledge Forge clean failed for article: %s", title)

        tag_value = json.dumps(
            tags if tags else ([source_name] if source_name else []),
            ensure_ascii=False,
        )
        try:
            cursor = self.conn.execute(
                """INSERT INTO articles (source_type, source_name, title, url,
                    author, summary, content_text, published_at, tags,
                    content_cleaned, content_clean_score, content_clean_log,
                    content_verified, content_verify_result,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           datetime('now','localtime'), datetime('now','localtime'))
                   ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title, summary=excluded.summary,
                    content_text=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_text
                      ELSE articles.content_text END,
                    content_cleaned=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_cleaned
                      ELSE articles.content_cleaned END,
                    content_clean_score=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_clean_score
                      ELSE articles.content_clean_score END,
                    content_clean_log=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_clean_log
                      ELSE articles.content_clean_log END,
                    content_verified=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_verified
                      ELSE articles.content_verified END,
                    content_verify_result=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_verify_result
                      ELSE articles.content_verify_result END,
                    tags=CASE
                      WHEN articles.tags IS NULL OR articles.tags IN ('', '[]')
                        THEN excluded.tags ELSE articles.tags END,
                    updated_at=datetime('now','localtime')""",
                (
                    source_type,
                    source_name,
                    title,
                    url,
                    author,
                    summary,
                    content_text,
                    published_at,
                    tag_value,
                    content_cleaned,
                    content_clean_score,
                    content_clean_log,
                    content_verified,
                    content_verify_result,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid
        except Exception:
            logger.exception("Failed to upsert article: %s", title)
            return None

    def inject_article_to_pool(
        self,
        bvid: str,
        title: str,
        url: str,
        author: str,
        source_name: str,
        description: str,
        published_at: str,
        source_platform: str = "rss",
    ) -> None:
        """Inject an article into the recommendation pool (content_cache).

        Uses a simplified INSERT that sets only the fields relevant to
        text-based articles — all numeric / video-only fields are
        zeroed.  ``ON CONFLICT(bvid) DO NOTHING`` prevents re-insertion
        on subsequent polling cycles.

        Args:
            source_platform: Platform identifier, defaults to "rss".

        """
        from openbiliclaw.storage.database import logger

        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, up_mid, duration, tags,
                    topic_key, style_key, franchise_key, description,
                    cover_url, view_count, like_count, favorite_count,
                    collect_count, comment_count, share_count, danmaku_count,
                    reply_count, retweet_count, bookmark_count,
                    relevance_score, relevance_reason, pool_expression,
                    pool_topic_label, candidate_tier, source, content_id,
                    content_url, source_platform, author_name, body_text,
                    content_type
                ) VALUES (
                    ?, ?, ?, 0, 0, '[]',
                    '', '', '', ?,
                    '', 0, 0, 0,
                    0, 0, 0, 0,
                    0, 0, 0,
                    0.0, '', '',
                    '', 'primary', 'rss_polling', ?,
                    ?, ?, ?,
                    '',
                    'article'
                )""",
                (
                    bvid,
                    title,
                    source_name,
                    (description or "")[:500],
                    bvid,
                    url,
                    source_platform,
                    author,
                ),
            )
            self.conn.commit()
        except Exception:
            logger.exception("Failed to inject article to pool: %s", title)

    def get_recent_articles(
        self,
        limit: int = 50,
        offset: int = 0,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        random_order: bool = False,
    ) -> list[dict[str, Any]]:
        """Get recent articles, optionally filtered by source_type, status, or tag."""
        from openbiliclaw.storage.database import logger

        try:
            conditions: list[str] = []
            params: list[Any] = []
            if source_type:
                conditions.append("source_type = ?")
                params.append(source_type)
            if status:
                conditions.append("status = ?")
                params.append(status)
            else:
                # 屏蔽位终态：无显式 status 过滤时永不再出现（见 ARTICLE_STATUSES）。
                conditions.append("status != 'hidden'")
            if tag:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            if random_order:
                # Do NOT use "ORDER BY RANDOM()": it materializes every
                # matching row (all selected columns) into a sorter B-tree,
                # which on a ~80k-row library blows the SQLite page cache and
                # makes the first shuffle pay a full cold read (~1s). Instead
                # scan only the rowid (PK) — a few MB at most — sample ids in
                # Python, then fetch just the sampled rows by PK.
                id_rows = self.conn.execute(
                    f"SELECT id FROM articles {where}", tuple(params)
                ).fetchall()
                ids = [row["id"] for row in id_rows]
                if not ids:
                    return []
                sample = random.sample(ids, limit) if len(ids) > limit else ids
                placeholders = ",".join("?" * len(sample))
                cursor = self.conn.execute(
                    f"""SELECT id, source_type, source_name, title, url, author,
                               summary, published_at, tags, status, created_at,
                               reading_percent, favorited, ai_summary
                        FROM articles
                        WHERE id IN ({placeholders})""",
                    tuple(sample),
                )
                return [dict(row) for row in cursor.fetchall()]
            order_sql = "ORDER BY published_at DESC, created_at DESC"
            cursor = self.conn.execute(
                f"""SELECT id, source_type, source_name, title, url, author,
                           summary, published_at, tags, status, created_at,
                           reading_percent, favorited, ai_summary
                    FROM articles
                    {where}
                    {order_sql}
                    LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to query articles")
            return []

    def count_articles(
        self,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> int:
        """Count articles matching the same filters as :meth:`get_recent_articles`."""
        from openbiliclaw.storage.database import logger

        try:
            conditions: list[str] = []
            params: list[Any] = []
            if source_type:
                conditions.append("source_type = ?")
                params.append(source_type)
            if status:
                conditions.append("status = ?")
                params.append(status)
            else:
                conditions.append("status != 'hidden'")
            if tag:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            cursor = self.conn.execute(f"SELECT COUNT(*) AS cnt FROM articles {where}", tuple(params))
            row = cursor.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            logger.exception("Failed to count articles")
            return 0

    # ── 文章详情与搜索 ──────────────────────────────────────────────

    def get_readarchive_article(self, article_id: int) -> dict[str, Any] | None:
        """Fetch a single read_archive article including its full body."""
        from openbiliclaw.storage.database import logger

        try:
            cursor = self.conn.execute(
                """SELECT id, source_type, source_name, title, url, author,
                           summary, content_text, published_at, tags,
                           created_at, updated_at
                    FROM read_archive WHERE id = ?""",
                (article_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception:
            logger.exception("Failed to fetch read_archive article %d", article_id)
            return None

    def search_articles(
        self,
        q: str,
        limit: int = 30,
        offset: int = 0,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search over the reading library.

        Uses the ``articles_fts`` trigram index for queries of 3+ characters
        (Chinese substring friendly) and falls back to ``LIKE`` for shorter
        queries or when FTS fails. Respects the same source/status/tag filters
        as :meth:`get_recent_articles`. Returns the same column shape.
        """
        from openbiliclaw.storage.database import logger

        q = (q or "").strip()
        if not q:
            return []
        cols = (
            "a.id, a.source_type, a.source_name, a.title, a.url, a.author, "
            "a.summary, a.published_at, a.tags, a.status, a.created_at, "
            "a.reading_percent, a.favorited, a.ai_summary"
        )
        filters: list[str] = []
        fparams: list[Any] = []
        if source_type:
            filters.append("a.source_type = ?")
            fparams.append(source_type)
        if status:
            filters.append("a.status = ?")
            fparams.append(status)
        else:
            filters.append("a.status != 'hidden'")
        if tag:
            filters.append("a.tags LIKE ?")
            fparams.append(f'%"{tag}"%')
        fsql = (" AND " + " AND ".join(filters)) if filters else ""

        if len(q) >= 3:
            try:
                fts_q = '"' + q.replace('"', '""') + '"'
                cursor = self.conn.execute(
                    f"""SELECT {cols} FROM articles a
                        JOIN articles_fts f ON a.id = f.rowid
                        WHERE articles_fts MATCH ? {fsql}
                        ORDER BY bm25(articles_fts)
                        LIMIT ? OFFSET ?""",
                    (fts_q, *fparams, limit, offset),
                )
                return [dict(row) for row in cursor.fetchall()]
            except Exception:
                logger.exception("FTS search failed, falling back to LIKE")

        like = f"%{q}%"
        try:
            cursor = self.conn.execute(
                f"""SELECT {cols} FROM articles a
                    WHERE (a.title LIKE ? OR a.content_text LIKE ?
                          OR a.tags LIKE ? OR a.author LIKE ? OR a.summary LIKE ?)
                          {fsql}
                    ORDER BY a.published_at DESC, a.created_at DESC
                    LIMIT ? OFFSET ?""",
                (like, like, like, like, like, *fparams, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to search articles")
            return []

    def get_article(self, article_id: int) -> dict[str, Any] | None:
        """Fetch a single article including its full body text."""
        from openbiliclaw.storage.database import logger

        try:
            cursor = self.conn.execute(
                """SELECT id, source_type, source_name, title, url, author,
                           summary, content_text, published_at, tags, status,
                           reading_percent, reading_progress, favorited,
                           ai_summary,
                           created_at, updated_at
                    FROM articles WHERE id = ?""",
                (article_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception:
            logger.exception("Failed to fetch article %d", article_id)
            return None

    def update_article_tags(self, article_id: int, tags: list[str]) -> bool:
        """Update tags for an article. Returns True on success."""
        from openbiliclaw.storage.database import logger

        try:
            self.conn.execute(
                "UPDATE articles SET tags = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (json.dumps(tags, ensure_ascii=False), article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to update tags for article %d", article_id)
            return False

    #: Canonical reading-library states. Kept in sync with the
    #: ``/api/articles/{id}`` PATCH endpoint validation.
    ARTICLE_STATUSES = ("unread", "reading", "finished", "archived", "hidden")

    def update_article_status(self, article_id: int, status: str) -> bool:
        """Update reading status for an article. Returns True on success."""
        from openbiliclaw.storage.database import logger

        if status not in self.ARTICLE_STATUSES:
            return False
        try:
            self.conn.execute(
                "UPDATE articles SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (status, article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to update status for article %d", article_id)
            return False

    # ── 阅读辅助：进度 / 收藏 / 笔记 / AI 摘要 ────────────────────────

    def update_article_reading_progress(
        self, article_id: int, *, percent: float, progress: str = ""
    ) -> bool:
        """Persist reading position (percent 0-100 + optional scroll anchor).

        ``percent`` is clamped to [0, 100]; a finished article keeps its
        own status but the progress is still recorded for the stats panel.
        """
        from openbiliclaw.storage.database import logger

        try:
            percent = max(0.0, min(100.0, float(percent)))
            self.conn.execute(
                "UPDATE articles SET reading_percent = ?, reading_progress = ?, "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (percent, (progress or "")[:4000], article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to save reading progress for article %d", article_id)
            return False

    def set_article_favorited(self, article_id: int, favorited: bool) -> bool:
        """Mark / unmark an article as favorited. Returns True on success."""
        from openbiliclaw.storage.database import logger

        try:
            self.conn.execute(
                "UPDATE articles SET favorited = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (1 if favorited else 0, article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to set favorited for article %d", article_id)
            return False

    def add_article_note(
        self,
        article_id: int,
        *,
        quote: str = "",
        note: str = "",
        color: str = "",
    ) -> int | None:
        """Add a note / highlight to an article. Returns note id or None."""
        from openbiliclaw.storage.database import logger

        try:
            cursor = self.conn.execute(
                "INSERT INTO article_notes (article_id, quote, note, color) VALUES (?, ?, ?, ?)",
                (article_id, (quote or "")[:2000], (note or "")[:4000], (color or "")[:20]),
            )
            self.conn.commit()
            return cursor.lastrowid or None
        except Exception:
            logger.exception("Failed to add note for article %d", article_id)
            return None

    def get_article_notes(self, article_id: int) -> list[dict[str, Any]]:
        """Return all notes/highlights for an article, oldest first."""
        from openbiliclaw.storage.database import logger

        try:
            cursor = self.conn.execute(
                """SELECT id, article_id, quote, note, color, created_at, updated_at
                   FROM article_notes WHERE article_id = ? ORDER BY id ASC""",
                (article_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to load notes for article %d", article_id)
            return []

    def delete_article_note(self, note_id: int) -> bool:
        """Delete one note by its own id. Returns True on success."""
        from openbiliclaw.storage.database import logger

        try:
            self.conn.execute("DELETE FROM article_notes WHERE id = ?", (note_id,))
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to delete note %d", note_id)
            return False

    def update_article_ai_summary(self, article_id: int, ai_summary: str) -> bool:
        """Store the generated AI summary JSON for an article."""
        from openbiliclaw.storage.database import logger

        try:
            self.conn.execute(
                "UPDATE articles SET ai_summary = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                ((ai_summary or "")[:6000], article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to store AI summary for article %d", article_id)
            return False

    def get_articles_missing_summary(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent articles that have body text but no AI summary yet."""
        from openbiliclaw.storage.database import logger

        try:
            cursor = self.conn.execute(
                """SELECT id, source_type, source_name, title, url, author,
                          summary, content_text, tags, status, created_at,
                          ai_summary
                   FROM articles
                   WHERE length(content_text) > 200
                     AND (ai_summary IS NULL OR ai_summary = '')
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to list articles missing AI summary")
            return []

    def get_article_reading_stats(self) -> dict[str, Any]:
        """Reading-library statistics for the dashboard.

        Returns totals by status, source-type distribution, notes count,
        finished counts by month (UTC), the top-10 most-read tags, a weekly
        finished trend (``by_week``) and a last-60-day daily timeline
        (``timeline``).
        """
        from openbiliclaw.storage.database import logger

        stats: dict[str, Any] = {
            "by_status": {},
            "by_source": {},
            "notes": 0,
            "by_month": {},
            "top_tags": [],
            "by_week": {},
            "timeline": [],
        }
        try:
            row = self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM articles GROUP BY status"
            ).fetchall()
            stats["by_status"] = {str(r["status"]): int(r["n"]) for r in row}
            row = self.conn.execute(
                "SELECT source_type, COUNT(*) AS n FROM articles GROUP BY source_type ORDER BY n DESC"
            ).fetchall()
            stats["by_source"] = {str(r["source_type"]): int(r["n"]) for r in row}
            stats["notes"] = int(
                self.conn.execute("SELECT COUNT(*) AS n FROM article_notes").fetchone()["n"]
            )
            row = self.conn.execute(
                """SELECT substr(published_at, 1, 7) AS ym, COUNT(*) AS n
                   FROM articles WHERE status = 'finished' AND published_at != ''
                   GROUP BY ym ORDER BY ym DESC LIMIT 12"""
            ).fetchall()
            stats["by_month"] = {str(r["ym"]): int(r["n"]) for r in row}
            tag_counter: dict[str, int] = {}
            for r in self.conn.execute(
                "SELECT tags FROM articles WHERE tags IS NOT NULL AND tags != '[]' LIMIT 2000"
            ).fetchall():
                try:
                    for t in json.loads(r["tags"]):
                        tag_counter[str(t)] = tag_counter.get(str(t), 0) + 1
                except Exception:
                    continue
            stats["top_tags"] = sorted(tag_counter.items(), key=lambda kv: kv[1], reverse=True)[:10]
            try:
                row = self.conn.execute(
                    """SELECT strftime('%Y-W%W', updated_at) AS yw, COUNT(*) AS n
                       FROM articles
                       WHERE status = 'finished' AND updated_at != ''
                       GROUP BY yw ORDER BY yw DESC LIMIT 14"""
                ).fetchall()
                stats["by_week"] = {str(r["yw"]): int(r["n"]) for r in reversed(row)}
            except Exception:
                logger.exception("Failed to compute weekly reading trend")
            try:
                row = self.conn.execute(
                    """SELECT date(updated_at) AS d, COUNT(*) AS n
                       FROM articles
                       WHERE status = 'finished'
                         AND date(updated_at) >= date('now', '-60 days')
                       GROUP BY d ORDER BY d ASC"""
                ).fetchall()
                stats["timeline"] = [{"date": str(r["d"]), "count": int(r["n"])} for r in row]
            except Exception:
                logger.exception("Failed to compute reading timeline")
            return stats
        except Exception:
            logger.exception("Failed to compute reading stats")
            return stats

    def get_articles_for_reading_stats(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Recent finished/reading articles with percent & timestamps."""
        from openbiliclaw.storage.database import logger

        try:
            cursor = self.conn.execute(
                """SELECT id, source_type, source_name, title, url, author,
                          tags, status, reading_percent, published_at,
                          created_at, updated_at
                   FROM articles
                   WHERE status IN ('reading', 'finished')
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to load articles for reading stats")
            return []

    def iter_articles_for_tagging(
        self,
        *,
        limit: int = 500,
        status: str | None = None,
        only_sparse: bool = True,
    ) -> list[dict[str, Any]]:
        """Lightweight rows for the auto-tag backfill."""
        from openbiliclaw.storage.database import logger

        try:
            limit = max(1, min(int(limit), 2000))
            conditions = ["COALESCE(status, 'unread') != 'hidden'"]
            params: list[Any] = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            sparse_clause = (
                " AND (tags IS NULL OR tags = '' OR json_array_length(tags) <= 1)"
                if only_sparse
                else ""
            )
            where = " AND ".join(conditions)
            cursor = self.conn.execute(
                f"""SELECT id, title, substr(content_text, 1, 4000) AS content_text,
                           summary, tags
                    FROM articles
                    WHERE {where}{sparse_clause}
                    ORDER BY updated_at DESC
                    LIMIT ?""",
                (*params, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to scan articles for tagging")
            return []

    def get_daily_reading_summary(self, *, day: str) -> dict[str, Any]:
        """当日已读回顾（每日简报「今日阅读回顾」板块的数据源）。"""
        from openbiliclaw.storage.database import logger

        summary: dict[str, Any] = {
            "finished_today": 0,
            "by_source": {},
            "top_topics": [],
        }
        try:
            rows = self.conn.execute(
                """SELECT source_type, tags FROM articles
                   WHERE status = 'finished' AND date(updated_at) = ?""",
                (day,),
            ).fetchall()
        except Exception:
            logger.exception("Failed to load daily reading summary for %s", day)
            return summary
        tag_counter: dict[str, int] = {}
        for row in rows:
            source = str(row["source_type"] or "其他")
            summary["by_source"][source] = summary["by_source"].get(source, 0) + 1
            try:
                tags = json.loads(row["tags"]) if row["tags"] else []
            except Exception:
                tags = []
            if isinstance(tags, list):
                for tag in tags:
                    text = str(tag).strip()
                    if text:
                        tag_counter[text] = tag_counter.get(text, 0) + 1
        summary["finished_today"] = len(rows)
        summary["top_topics"] = [
            tag for tag, _ in sorted(tag_counter.items(), key=lambda kv: kv[1], reverse=True)[:6]
        ]
        return summary
