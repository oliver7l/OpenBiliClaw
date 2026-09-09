"""Article management API routes（从 app.py 提取）。

包含阅读库文章列表/详情/编辑、文章笔记、AI摘要、离线快照等端点。
通过 ``register_article_routes(app, ctx)`` 注册。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse

from openbiliclaw.api.utils import article_fit_score as _article_fit_score
from openbiliclaw.api.utils import article_tags_for_context as _article_tags_for_context

if TYPE_CHECKING:
    from fastapi import FastAPI

    from openbiliclaw.api.models import ArticleNoteIn, ArticleUpdateIn

logger = logging.getLogger(__name__)


def register_article_routes(app: FastAPI, ctx: Any) -> None:
    """Register article-related routes onto *app*."""

    @app.get("/api/articles")
    def list_articles(
        q: str = "",
        source_type: str = "",
        status: str = "",
        tag: str = "",
        limit: int = 50,
        offset: int = 0,
        random: bool = False,
        sort: str = "",
    ) -> JSONResponse:
        """List reading-library articles, optionally filtered.

        Supports keyword search (``q``) over the full-text index, plus
        filtering by source type, reading status and tag. When ``q`` is given
        the results are ranked by FTS relevance (bm25); otherwise they are
        ordered by recency. ``sort=relevance`` re-ranks by interest-profile
        fit (each item carries a ``fit_score`` 0-1); the re-rank samples the
        top ``max(limit*4, 200)`` recent rows so the per-request cost stays
        tiny while still surfacing the best matches.
        """
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"items": [], "total": 0})
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        if q and q.strip():
            items = database.search_articles(
                q=q.strip(),
                limit=limit,
                offset=offset,
                source_type=source_type or None,
                status=status or None,
                tag=tag or None,
            )
            total = len(items)
        else:
            items = database.get_recent_articles(
                limit=limit,
                offset=offset,
                source_type=source_type or None,
                status=status or None,
                tag=tag or None,
                random_order=random,
            )
            total = database.count_articles(
                source_type=source_type or None,
                status=status or None,
                tag=tag or None,
            )
            if sort and sort.strip().lower() == "relevance" and not random:
                candidate_limit = max(limit * 4, 200)
                cands = database.get_recent_articles(
                    limit=candidate_limit,
                    offset=0,
                    source_type=source_type or None,
                    status=status or None,
                    tag=tag or None,
                )
                for item in cands:
                    text = " ".join(
                        [
                            str(item.get("title") or ""),
                            str(item.get("summary") or ""),
                            str(item.get("tags") or ""),
                        ]
                    )
                    item["fit_score"] = _article_fit_score(text)
                cands.sort(key=lambda it: it.get("fit_score", 0.0), reverse=True)
                items = cands[offset : offset + limit]
        return JSONResponse({"items": items, "total": total, "query": q})

    @app.get("/api/articles/facets", response_model=None)
    def list_article_facets() -> JSONResponse:
        """Source-type distribution for the reading-library filter UI."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"source_types": [], "total": 0})
        try:
            # v0.4.0+: articles 表迁移到 content.db
            content_conn = getattr(database, "_content_conn", None) or database.conn
            rows = content_conn.execute(
                "SELECT source_type, COUNT(*) AS n FROM articles "
                "WHERE COALESCE(status, 'unread') != 'hidden' "
                "GROUP BY source_type ORDER BY n DESC"
            ).fetchall()
        except Exception:
            logger.exception("Failed to read article facets")
            return JSONResponse({"source_types": [], "total": 0})
        total = sum(int(r["n"]) for r in rows)
        return JSONResponse(
            {
                "source_types": [{"type": r["source_type"], "count": int(r["n"])} for r in rows],
                "total": total,
            }
        )

    @app.get("/api/articles/{article_id}")
    def get_article(article_id: int) -> JSONResponse:
        """Fetch a single article with its full body text for reading."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        row = database.get_article(article_id)
        if row is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        return JSONResponse({"ok": True, "article": row})

    @app.get("/api/read-archive/articles/{article_id}")
    def get_read_archive_article(article_id: int) -> JSONResponse:
        """Fetch a single read-archive article with its full body text."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        row = database.get_readarchive_article(article_id)
        if row is None:
            return JSONResponse(
                {"ok": False, "error": "read-archive article not found"}, status_code=404
            )
        return JSONResponse({"ok": True, "article": row})

    @app.patch("/api/articles/{article_id}")
    async def update_article(article_id: int, payload: ArticleUpdateIn) -> JSONResponse:
        """Update an article's reading status and/or tags."""
        # Mirrors Database.ARTICLE_STATUSES — keep the two in sync.
        valid = {"unread", "reading", "finished", "archived", "hidden"}
        if payload.status is not None and payload.status not in valid:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"invalid status: {payload.status}; expected one of {sorted(valid)}",
                },
                status_code=400,
            )
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = True
        pool_purged = 0
        pool_revived = 0
        previous_status: str | None = None
        if payload.status is not None:
            previous_row = database.get_article(article_id)
            previous_status = str(previous_row.get("status") or "") if previous_row else None
            ok = bool(database.update_article_status(article_id, payload.status))
            # 读完回流画像：finished 是强正向信号，插入事件由 soul 管道
            # 自然消费（classify_event_satisfaction 已将其归为 positive）。
            # 失败不阻塞主操作。
            if ok and payload.status == "finished":
                try:
                    row = database.get_article(article_id)
                    if row:
                        from openbiliclaw.sources.event_format import (
                            format_event_context,
                        )

                        # E2: fold the article's tags into the context so the
                        # preference analyzer sees topic-level evidence, not
                        # just the title (tags were previously metadata-only
                        # and never reached the LLM prompt).
                        _tags = _article_tags_for_context(row.get("tags"))
                        context = format_event_context(
                            event_type="article_finished",
                            source_platform=str(row.get("source_type") or "阅读库"),
                            title=str(row.get("title") or ""),
                            author=str(row.get("author") or ""),
                            extra=_tags,
                        )
                        database.insert_event(
                            "article_finished",
                            url=str(row.get("url") or ""),
                            title=str(row.get("title") or ""),
                            context=context,
                            metadata={
                                "article_id": article_id,
                                "source_type": str(row.get("source_type") or ""),
                                "source_name": str(row.get("source_name") or ""),
                                "author": str(row.get("author") or ""),
                                "tags": row.get("tags") or "[]",
                                "signal_strength": 0.8,
                            },
                        )
                except Exception:
                    logger.exception("Failed to record article_finished event")
            # 屏蔽回流画像：hidden 是用户主动表达的负向信号，插入事件由
            # soul 管道作为「避开这类内容」的证据消费。失败不阻塞主操作。
            if ok and payload.status == "hidden":
                blocked_url = ""
                try:
                    row = database.get_article(article_id)
                    if row:
                        from openbiliclaw.sources.event_format import (
                            format_event_context,
                        )

                        # E2: same tag-folding as article_finished so the
                        # negative signal carries topic-level evidence too.
                        _tags = _article_tags_for_context(row.get("tags"))
                        context = format_event_context(
                            event_type="article_dismissed",
                            source_platform=str(row.get("source_type") or "阅读库"),
                            title=str(row.get("title") or ""),
                            author=str(row.get("author") or ""),
                            extra=_tags,
                        )
                        database.insert_event(
                            "article_dismissed",
                            url=str(row.get("url") or ""),
                            title=str(row.get("title") or ""),
                            context=context,
                            metadata={
                                "article_id": article_id,
                                "source_type": str(row.get("source_type") or ""),
                                "source_name": str(row.get("source_name") or ""),
                                "author": str(row.get("author") or ""),
                                "tags": row.get("tags") or "[]",
                                "signal_strength": 0.8,
                            },
                        )
                        blocked_url = str(row.get("url") or "")
                except Exception:
                    logger.exception("Failed to record article_dismissed event")
                # 同步清洗候选池：被屏蔽文章若同时是推荐池里的 fresh 候选
                # （RSS 注入 / 推荐后存入库），立即置为 suppressed，让
                # 「屏蔽」当场生效而不是等 soul 管道异步学习。suppressed
                # 会在重新发现时自动复活，与「恢复」操作配对。
                try:
                    pool_purged = database.suppress_pool_rows_by_url(blocked_url)
                except Exception:
                    logger.exception("Failed to suppress pool rows for blocked article")
            elif ok and previous_status == "hidden":
                # 取消屏蔽：把此前被同一 URL 连坐抑制的候选放回 fresh。
                try:
                    row = database.get_article(article_id)
                    pool_revived = database.revive_suppressed_pool_rows_by_url(
                        str((row or {}).get("url") or "")
                    )
                except Exception:
                    logger.exception("Failed to revive suppressed pool rows")
        if payload.tags is not None and ok:
            ok = bool(database.update_article_tags(article_id, payload.tags))
        if (payload.percent is not None or payload.progress is not None) and ok:
            ok = bool(
                database.update_article_reading_progress(
                    article_id,
                    percent=payload.percent if payload.percent is not None else 0.0,
                    progress=payload.progress or "",
                )
            )
        if payload.favorited is not None and ok:
            ok = bool(database.set_article_favorited(article_id, payload.favorited))
        return JSONResponse(
            {
                "ok": ok,
                "id": article_id,
                "purged_pool_count": pool_purged,
                "revived_pool_count": pool_revived,
            }
        )

    @app.get("/api/articles/{article_id}/notes")
    def list_article_notes(article_id: int) -> JSONResponse:
        """List notes / highlights for an article."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if database.get_article(article_id) is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        notes = database.get_article_notes(article_id)
        return JSONResponse({"ok": True, "notes": notes})

    @app.post("/api/articles/{article_id}/notes")
    def add_article_note(article_id: int, payload: ArticleNoteIn) -> JSONResponse:
        """Add a note / highlight to an article."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if database.get_article(article_id) is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        note_id = database.add_article_note(
            article_id,
            quote=payload.quote,
            note=payload.note,
            color=payload.color,
        )
        if note_id is None:
            return JSONResponse({"ok": False, "error": "failed to save note"}, status_code=500)
        return JSONResponse({"ok": True, "id": note_id})

    @app.delete("/api/notes/{note_id}")
    def delete_article_note(note_id: int) -> JSONResponse:
        """Delete one note by its own id."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = database.delete_article_note(note_id)
        return JSONResponse({"ok": ok, "id": note_id})

    @app.post("/api/articles/{article_id}/summarize")
    async def summarize_article(article_id: int) -> JSONResponse:
        """Generate / refresh an AI summary (one-liner + 3 key points) via LLM.

        Idempotent: an existing summary is returned as-is unless ``force``
        is passed. Requires a body of at least 200 chars; short articles
        fall back to their stored ``summary`` field.
        """
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        row = database.get_article(article_id)
        if row is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        if row.get("ai_summary"):
            try:
                return JSONResponse(
                    {"ok": True, "summary": json.loads(row["ai_summary"]), "cached": True}
                )
            except Exception:
                pass  # 损坏则重新生成
        content = str(row.get("content_text") or "").strip()
        if len(content) < 200:
            return JSONResponse(
                {"ok": False, "error": "正文过短，无法生成摘要", "article_id": article_id},
                status_code=422,
            )
        try:
            from openbiliclaw.config import load_config as _sum_cfg
            from openbiliclaw.llm.registry import build_llm_registry as _build_reg

            registry = _build_reg(_sum_cfg())
            system = (
                "你是个人阅读助手。为下面这篇文章生成中文摘要，"
                "只输出 JSON，不要多余文字，格式："
                '{"one_liner":"不超过30字的一句话总结","points":["要点1，一句话","要点2，一句话","要点3，一句话"]}'
            )
            user = (
                f"标题：{row.get('title') or ''}\n"
                f"作者：{row.get('author') or ''}\n"
                f"来源：{row.get('source_name') or row.get('source_type') or ''}\n"
                f"标签：{row.get('tags') or '[]'}\n\n"
                f"正文（截取前 3000 字）：\n{content[:3000]}"
            )
            resp = await registry.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
                # sensenova-6.8 is a reasoning model: its thinking process
                # routinely eats 1000+ tokens, so a 700-token budget was
                # exhausted by reasoning alone and content came back empty
                # (finish_reason=length). 3000 leaves room for reasoning +
                # the actual summary.
                max_tokens=3000,
                json_mode=True,
            )
            raw = (resp.content or "").strip()
            summary = json.loads(raw)
            if not isinstance(summary, dict):
                raise ValueError("non-dict summary")
            summary.setdefault("one_liner", "")
            summary.setdefault("points", [])
            summary["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            database.update_article_ai_summary(article_id, json.dumps(summary, ensure_ascii=False))
            return JSONResponse({"ok": True, "summary": summary, "cached": False})
        except Exception:
            logger.exception("AI summary generation failed for article %d", article_id)
            return JSONResponse(
                {"ok": False, "error": "摘要生成失败，请稍后重试", "article_id": article_id},
                status_code=502,
            )

    @app.get("/api/articles/{article_id}/snapshot")
    async def get_article_snapshot(article_id: int):
        """Get the offline HTML snapshot for an article.

        Args:
            article_id: The article ID.

        Returns:
            JSON with snapshot data (content_html, content_text, fetched_at).

        """
        db_path = "data/openbiliclaw.db"
        try:
            cfg = getattr(ctx, "config", None)
            if cfg and hasattr(cfg, "storage") and cfg.storage:
                db_path = str(cfg.storage.db_path)
        except Exception:
            pass

        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.execute("PRAGMA busy_timeout=10000")
            cursor = conn.cursor()

            # 先查文章基本信息
            article = cursor.execute(
                "SELECT id, title, url, source_type FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()
            if not article:
                conn.close()
                return JSONResponse({"error": "Article not found"}, status_code=404)

            # 查快照
            snapshot = cursor.execute(
                """SELECT id, content_html, content_text, fetch_source, fetched_at
                   FROM article_snapshots WHERE article_id = ? ORDER BY id DESC LIMIT 1""",
                (article_id,),
            ).fetchone()
            conn.close()

            if not snapshot:
                return {
                    "article_id": article_id,
                    "title": article[1],
                    "url": article[2],
                    "source_type": article[3],
                    "has_snapshot": False,
                    "message": "No offline snapshot available for this article",
                }

            return {
                "article_id": article_id,
                "title": article[1],
                "url": article[2],
                "source_type": article[3],
                "has_snapshot": True,
                "snapshot_id": snapshot[0],
                "content_html": snapshot[1],
                "content_text": snapshot[2],
                "fetch_source": snapshot[3],
                "fetched_at": snapshot[4],
            }

        except Exception as e:
            logger.exception("Failed to get snapshot for article %s: %s", article_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/snapshots/stats")
    async def get_snapshots_stats():
        """Get statistics about offline snapshots."""
        db_path = "data/openbiliclaw.db"
        try:
            cfg = getattr(ctx, "config", None)
            if cfg and hasattr(cfg, "storage") and cfg.storage:
                db_path = str(cfg.storage.db_path)
        except Exception:
            pass

        try:
            conn = sqlite3.connect(db_path, timeout=10.0)
            conn.execute("PRAGMA busy_timeout=10000")
            cursor = conn.cursor()

            total = cursor.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0]
            with_html = cursor.execute(
                "SELECT COUNT(*) FROM article_snapshots"
                " WHERE content_html != '' AND content_html IS NOT NULL"
            ).fetchone()[0]
            sources = cursor.execute(
                "SELECT fetch_source, COUNT(*) FROM article_snapshots GROUP BY fetch_source"
            ).fetchall()
            conn.close()

            return {
                "total_snapshots": total,
                "with_html": with_html,
                "by_source": {s[0]: s[1] for s in sources},
            }

        except Exception as e:
            logger.exception("Failed to get snapshot stats: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)
