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
