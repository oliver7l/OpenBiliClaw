"""Library and URL processing routes for OpenBiliClaw API."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def register_library_routes(app: FastAPI, ctx: Any) -> None:
    """Register URL extraction and library endpoints on the FastAPI app."""


    # ── URL Content Extraction (单篇URL内容提取) ───────────────────
    # Inspired by Agent-SaveMark's processor pattern. Extracts structured
    # content from a single URL using platform-specific processors.
    # Supported: Zhihu, V2EX, Hupu, Bilibili, Xiaohongshu, YouTube,
    # Xiaoyuzhou, and generic web pages (fallback).

    @app.get("/api/url/processors")
    async def list_url_processors():
        """List all registered URL content processors."""
        from openbiliclaw.sources.url_processors import get_all_source_types, list_processors
        processors = list_processors()
        return {
            "processors": processors,
            "source_types": get_all_source_types(),
            "total": len(processors),
        }

    @app.post("/api/url/extract")
    async def extract_url_content(payload: dict[str, Any]):
        """Extract content from a single URL.

        Request body:
            url: The URL to extract content from.
            cookies: Optional dict of authentication cookies.
            save: Whether to save the extracted content to the articles table (default: false).
        """
        from openbiliclaw.sources.url_processors import match_processor
        from openbiliclaw.sources.url_processors.base import ProcessorStatus

        url = payload.get("url", "").strip()
        if not url:
            return JSONResponse({"error": "url is required"}, status_code=400)

        cookies = payload.get("cookies", {})
        should_save = payload.get("save", False)

        try:
            processor = match_processor(url)
            result = await processor.process(url, cookies=cookies)

            response_data = {
                "status": result.status.value,
                "source_type": result.source_type,
                "source_name": result.source_name,
                "title": result.title,
                "author": result.author,
                "summary": result.summary,
                "content_text": result.content_text,
                "content_html": result.content_html,
                "published_at": result.published_at,
                "tags": result.tags,
                "url": result.url,
                "metadata": result.metadata,
                "error": result.error,
            }

            # Save to articles table if requested
            if should_save and result.status == ProcessorStatus.success:
                article_id = await _save_extracted_article(result)
                response_data["saved"] = True
                response_data["article_id"] = article_id
            else:
                response_data["saved"] = False

            return response_data

        except Exception as e:
            logger.exception("URL extraction failed for %s", url)
            return JSONResponse({"error": str(e)}, status_code=500)

    async def _save_extracted_article(result) -> int | None:
        """Save extracted content to the articles table.

        Args:
            result: ProcessorResult with extracted content.

        Returns:
            The inserted article ID, or None if insertion failed.
        """
        import sqlite3

        # Get database path from config
        db_path = "data/openbiliclaw.db"
        try:
            cfg = getattr(ctx, "config", None)
            if cfg and hasattr(cfg, "storage") and cfg.storage:
                db_path = str(cfg.storage.db_path)
        except Exception:
            pass

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            article_dict = result.to_article_dict()

            # Check for duplicate by content_hash
            if article_dict.get("content_hash"):
                existing = cursor.execute(
                    "SELECT id FROM articles WHERE content_hash = ? LIMIT 1",
                    (article_dict["content_hash"],),
                ).fetchone()
                if existing:
                    logger.info("Article already exists (id=%s), skipping insert", existing[0])
                    conn.close()
                    return existing[0]

            # Insert article
            columns = ", ".join(article_dict.keys())
            placeholders = ", ".join(["?"] * len(article_dict))
            values = list(article_dict.values())

            cursor.execute(
                f"INSERT INTO articles ({columns}) VALUES ({placeholders})",
                values,
            )
            article_id = cursor.lastrowid

            # 保存离线存档（HTML 快照）
            if result.content_html or result.content_text:
                try:
                    cursor.execute(
                        """INSERT INTO article_snapshots
                           (article_id, url, content_html, content_text, fetch_source)
                           VALUES (?, ?, ?, ?, 'url_extractor')""",
                        (article_id, result.url, result.content_html or "", result.content_text or ""),
                    )
                    logger.info("Saved snapshot for article (id=%s)", article_id)
                except Exception as snap_err:
                    logger.warning("Failed to save snapshot for article %s: %s", article_id, snap_err)

            conn.commit()
            conn.close()

            # 自动注册到阅读调度系统
            try:
                from openbiliclaw.self_evolution.reading_schedule import ReadingScheduler
                scheduler = ReadingScheduler(db_path)
                scheduler.register_article(article_id, initial_delay_days=1.0)
            except Exception as sched_err:
                logger.warning("Failed to register article %s to reading schedule: %s", article_id, sched_err)

            logger.info("Saved extracted article (id=%s): %s", article_id, result.title)
            return article_id

        except Exception as e:
            logger.exception("Failed to save extracted article: %s", e)
            return None

