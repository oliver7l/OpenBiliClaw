"""离线内容填充管线 — 正文、字幕、AI 摘要。

自进化循环调度调用，按配额控制频率，增量处理内容库中缺失的信息。

处理管线：
1. ``body_fetch`` — 对 RSS/Web 文章增量拉取正文（使用 URL 处理器）
2. ``youtube_transcript`` — 提取 YouTube 字幕作为 content_text
3. ``bilibili_subtitle`` — 提取 Bilibili 字幕作为 content_text
4. ``ai_summary`` — 对有正文无 AI 摘要的文章生成总结/讲解
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger("self_evolution.content_filler")

# 每次 tick 最多处理数
_BODY_FETCH_BATCH = 30
_YT_TRANSCRIPT_BATCH = 10
_BILI_SUBTITLE_BATCH = 10
_AI_SUMMARY_BATCH = 20
_AI_SUMMARY_CONCURRENCY = 3

# 最大失败重试次数
_MAX_BODY_RETRIES = 3
_MAX_YT_RETRIES = 2
_MAX_BILI_RETRIES = 2


class ContentFiller:
    """离线内容填充器，提供四条独立管线。"""

    def __init__(
        self,
        db_path: str = "data/openbiliclaw.db",
        llm_service: object | None = None,
    ) -> None:
        self._db_path = db_path
        self._llm_service = llm_service

    # ── 辅助 ───────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ═══════════════════════════════════════════════════════════════
    # 管线 1: 正文拉取
    # ═══════════════════════════════════════════════════════════════

    async def fetch_bodies(self, limit: int = _BODY_FETCH_BATCH) -> dict[str, int]:
        """增量拉取 RSS/Web 文章正文。

        选择条件：
        - content_text 为空
        - body_fetch_attempts < 最大重试次数
        - 非视频来源（youtube/bilibili 走字幕管线）
        """
        conn = self._conn()
        rows = conn.execute(
            """SELECT id, url, source_type, body_fetch_attempts
               FROM articles
               WHERE (content_text IS NULL OR content_text = '')
                 AND body_fetch_attempts < ?
                 AND source_type NOT IN ('youtube','bilibili','bili','yt')
               ORDER BY body_fetch_attempts ASC, id ASC
               LIMIT ?""",
            (_MAX_BODY_RETRIES, limit),
        ).fetchall()
        conn.close()

        if not rows:
            return {"fetched": 0, "total": 0}

        results = {"fetched": 0, "failed": 0, "total": len(rows)}
        for row in rows:
            try:
                article_id = row["id"]
                url = row["url"]
                content = await self._fetch_single_body(url)
                if content:
                    self._save_body(article_id, content)
                    results["fetched"] += 1
                else:
                    self._increment_attempts(article_id)
                    results["failed"] += 1
            except Exception as exc:
                logger.debug("body_fetch: article %d failed: %s", row["id"], exc)
                self._increment_attempts(row["id"])
                results["failed"] += 1

        logger.info("content_filler: body_fetch %d/%d", results["fetched"], results["total"])
        return results

    async def _fetch_single_body(self, url: str) -> str | None:
        """通过 URL 处理器拉取单篇文章正文。"""
        try:
            from openbiliclaw.sources.url_processors.registry import match_processor

            processor = match_processor(url)
            result = await processor.process(url)
            if result.content_text and len(result.content_text) > 200:
                return result.content_text
            return None
        except Exception:
            return None

    def _save_body(self, article_id: int, content: str) -> None:
        conn = self._conn()
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:32]
        conn.execute(
            """UPDATE articles
               SET content_text = ?, content_hash = ?, body_fetch_attempts = body_fetch_attempts + 1,
                   updated_at = ?
               WHERE id = ?""",
            (content, content_hash, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), article_id),
        )
        conn.commit()
        conn.close()

    def _increment_attempts(self, article_id: int) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE articles SET body_fetch_attempts = body_fetch_attempts + 1, updated_at = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), article_id),
        )
        conn.commit()
        conn.close()

    # ═══════════════════════════════════════════════════════════════
    # 管线 2: YouTube 字幕提取
    # ═══════════════════════════════════════════════════════════════

    async def fetch_youtube_transcripts(
        self, limit: int = _YT_TRANSCRIPT_BATCH,
    ) -> dict[str, int]:
        """提取 YouTube 视频字幕作为 content_text。

        选择条件：
        - source_type=youtube 且 content_text 为空
        - 最多重试 _MAX_YT_RETRIES 次
        """
        conn = self._conn()
        rows = conn.execute(
            """SELECT id, url, body_fetch_attempts
               FROM articles
               WHERE source_type IN ('youtube','yt')
                 AND (content_text IS NULL OR content_text = '')
                 AND body_fetch_attempts < ?
               ORDER BY body_fetch_attempts ASC, id ASC
               LIMIT ?""",
            (_MAX_YT_RETRIES, limit),
        ).fetchall()
        conn.close()

        if not rows:
            return {"fetched": 0, "total": 0}

        results = {"fetched": 0, "failed": 0, "total": len(rows)}
        for row in rows:
            try:
                transcript = await self._fetch_youtube_transcript(row["url"])
                if transcript:
                    self._save_body(row["id"], transcript)
                    results["fetched"] += 1
                else:
                    self._increment_attempts(row["id"])
                    results["failed"] += 1
            except Exception as exc:
                logger.debug("yt_transcript: video %d failed: %s", row["id"], exc)
                self._increment_attempts(row["id"])
                results["failed"] += 1

        logger.info(
            "content_filler: yt_transcript %d/%d", results["fetched"], results["total"]
        )
        return results

    async def _fetch_youtube_transcript(self, url: str) -> str | None:
        """使用 yt-dlp 提取 YouTube 视频字幕。"""
        try:
            from yt_dlp import YoutubeDL

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["zh-Hans", "zh", "en", "-auto"],
                "skip_download": True,
                "subtitlesformat": "vtt",
            }
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                if not info:
                    return None

                # 优先取字幕
                subtitles = info.get("subtitles") or {}
                for lang in ("zh-Hans", "zh", "en"):
                    sub_data = subtitles.get(lang)
                    if sub_data:
                        for fmt in ("vtt", "json3", "srv1", "srv2", "srv3"):
                            entry = sub_data.get(fmt) or (
                                sub_data[0] if isinstance(sub_data, list) else None
                            )
                            if entry:
                                import requests
                                resp = requests.get(
                                    entry.get("url") or (entry if isinstance(entry, str) else ""),
                                    timeout=30,
                                )
                                if resp.status_code == 200:
                                    return resp.text[:50000]

                # 降级：自动生成字幕
                auto_subs = info.get("automatic_captions") or {}
                for lang in ("zh-Hans", "zh", "en"):
                    cap_data = auto_subs.get(lang)
                    if cap_data:
                        entry = cap_data[0] if isinstance(cap_data, list) else None
                        if entry:
                            url_entry = entry.get("url")
                            if url_entry:
                                import requests
                                resp = requests.get(url_entry, timeout=30)
                                if resp.status_code == 200:
                                    return resp.text[:50000]

                # 最后退路：description
                desc = info.get("description") or ""
                if len(desc) > 200:
                    return desc[:50000]
                return None
        except Exception:
            return None

    # ═══════════════════════════════════════════════════════════════
    # 管线 3: Bilibili 字幕提取
    # ═══════════════════════════════════════════════════════════════

    async def fetch_bilibili_subtitles(
        self, limit: int = _BILI_SUBTITLE_BATCH,
    ) -> dict[str, int]:
        """提取 Bilibili 视频字幕作为 content_text。"""
        conn = self._conn()
        rows = conn.execute(
            """SELECT id, url, body_fetch_attempts
               FROM articles
               WHERE source_type IN ('bilibili','bili')
                 AND (content_text IS NULL OR content_text = '')
                 AND body_fetch_attempts < ?
               ORDER BY body_fetch_attempts ASC, id ASC
               LIMIT ?""",
            (_MAX_BILI_RETRIES, limit),
        ).fetchall()
        conn.close()

        if not rows:
            return {"fetched": 0, "total": 0}

        results = {"fetched": 0, "failed": 0, "total": len(rows)}
        for row in rows:
            try:
                bvid = self._extract_bvid(row["url"])
                if not bvid:
                    self._increment_attempts(row["id"])
                    results["failed"] += 1
                    continue

                from openbiliclaw.bilibili.subtitle import BiliSubtitleFetcher

                fetcher = BiliSubtitleFetcher()
                text = await fetcher.fetch_subtitle_text(bvid)
                if text and len(text) > 50:
                    self._save_body(row["id"], text)
                    results["fetched"] += 1
                else:
                    # 无字幕的视频，保存 description 作为 content
                    conn2 = self._conn()
                    desc = conn2.execute(
                        "SELECT summary FROM articles WHERE id = ?", (row["id"],)
                    ).fetchone()
                    conn2.close()
                    if desc and desc["summary"] and len(desc["summary"]) > 100:
                        self._save_body(row["id"], desc["summary"])
                        results["fetched"] += 1
                    else:
                        self._increment_attempts(row["id"])
                        results["failed"] += 1
            except Exception as exc:
                logger.debug("bili_subtitle: video %d failed: %s", row["id"], exc)
                self._increment_attempts(row["id"])
                results["failed"] += 1

        logger.info(
            "content_filler: bili_subtitle %d/%d", results["fetched"], results["total"]
        )
        return results

    @staticmethod
    def _extract_bvid(url: str) -> str | None:
        import re
        m = re.search(r"BV[a-zA-Z0-9]{10,}", url)
        return m.group(0) if m else None

    # ═══════════════════════════════════════════════════════════════
    # 管线 4: AI 摘要生成
    # ═══════════════════════════════════════════════════════════════

    async def generate_ai_summaries(
        self, limit: int = _AI_SUMMARY_BATCH,
    ) -> dict[str, int]:
        """对有正文但无 AI 摘要的文章批量生成总结/讲解。

        使用 LLM 为每篇文章生成：
        - 一句话核心（一句话概括）
        - 要点清单（3-5 个关键点）
        - 讲解总结（100-200 字通俗讲解）
        """
        if not self._llm_service:
            logger.warning("ai_summary: no LLM service available, skipping")
            return {"summarized": 0, "total": 0}

        conn = self._conn()
        rows = conn.execute(
            """SELECT id, title, source_type, content_text
               FROM articles
               WHERE content_text IS NOT NULL AND content_text != ''
                 AND (ai_summary IS NULL OR ai_summary = '')
               ORDER BY id ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        conn.close()

        if not rows:
            return {"summarized": 0, "total": 0}

        sem = asyncio.Semaphore(_AI_SUMMARY_CONCURRENCY)

        async def _summarize_one(row: sqlite3.Row) -> bool:
            async with sem:
                try:
                    summary = await self._llm_generate_summary(
                        title=row["title"] or "",
                        content=row["content_text"][:8000],
                    )
                    if summary:
                        self._save_ai_summary(row["id"], summary)
                        return True
                    return False
                except Exception:
                    return False

        tasks = [_summarize_one(r) for r in rows]
        results_list = await asyncio.gather(*tasks)
        success = sum(1 for r in results_list if r)
        logger.info("content_filler: ai_summary %d/%d", success, len(rows))
        return {"summarized": success, "total": len(rows)}

    async def _llm_generate_summary(self, title: str, content: str) -> str | None:
        """调用 LLM 生成文章摘要。"""
        prompt = (
            "你是一位内容总结专家。请为以下文章生成结构化总结。\n\n"
            f"标题：{title}\n\n"
            f"正文：\n\"\"\"\n{content}\n\"\"\"\n\n"
            "请输出 JSON 格式：\n"
            "{\n"
            '  "core": "一句话核心（30字以内）",\n'
            '  "key_points": ["要点1", "要点2", "要点3", "要点4", "要点5"],\n'
            '  "explanation": "通俗讲解总结（100-200字）"\n'
            "}\n"
            "不要输出任何其他文字。"
        )
        resp = await self._llm_service.complete_structured_task(
            system_instruction="你是一位内容总结专家，输出结构化 JSON。",
            user_input=prompt,
            temperature=0.3,
            max_tokens=2048,
            caller="content_filler.ai_summary",
            reasoning_effort="none",
            inject_core_memory=False,
        )
        raw = getattr(resp, "content", "")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and "core" in data:
                return json.dumps(data, ensure_ascii=False)
            return raw[:2000]
        except json.JSONDecodeError:
            return raw[:2000]

    def _save_ai_summary(self, article_id: int, summary: str) -> None:
        conn = self._conn()
        conn.execute(
            "UPDATE articles SET ai_summary = ?, updated_at = ? WHERE id = ?",
            (summary, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), article_id),
        )
        conn.commit()
        conn.close()