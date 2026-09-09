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
from datetime import datetime, timedelta

logger = logging.getLogger("self_evolution.content_filler")

# 每次 tick 最多处理数
_BODY_FETCH_BATCH = 30
_YT_TRANSCRIPT_BATCH = 10
_BILI_SUBTITLE_BATCH = 10
_AI_SUMMARY_BATCH = 20
_AI_SUMMARY_CONCURRENCY = 3
_GETNOTE_BATCH = 40
# 得到大脑异步生成：save 不只是提交，CLI 会阻塞等待平台建好笔记，
# 实测单篇最长约 60s（之前 30s/60s 超时正是全失败根因）。
# 因此拆两阶段「播种(save) → 稍后收割(note 读取)」，且单轮批量要小防阻塞：
_GETNOTE_POLL_WAIT_SECONDS = 180  # save 返回后 web_page.content 仍异步生成，实测约3-4分钟才就绪
_GETNOTE_MAX_POLLS = 6  # 单篇最多收割轮数，超出视为无法获取并释放
_GETNOTE_MAX_PENDING = 200  # 在途待收割队列上限（避免积压/触发平台风控）
_GETNOTE_SAVE_TIMEOUT = 110  # save 提交阻塞等待，实测约 58s
_GETNOTE_NOTE_TIMEOUT = 60  # note 读取超时
_GETNOTE_SEED_PER_CALL = 3  # 单轮最多播种（save 慢，防止阻塞 tick 太久）
_GETNOTE_HARVEST_PER_CALL = 10  # 单轮最多收割

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
        self._ensure_getnote_pending_table()

    # ── 得到大脑异步收割队列表结构 ─────────────────────────────────
    def _ensure_getnote_pending_table(self) -> None:
        """确保 getnote_pending 队列表存在（持久化「播种后待收割」项）。

        字段说明：
        - article_id 关联 articles.id（主键）
        - note_id    得到大脑笔记 ID（poll 时读取该笔记）
        - saved_at   save 提交时间，用于判断是否已过异步生成缓冲期
        - poll_attempts  已收割轮数，超 GETNOTE_MAX_POLLS 则放弃
        """
        conn = self._conn()
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS getnote_pending (
                    article_id INTEGER PRIMARY KEY,
                    note_id TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    poll_attempts INTEGER DEFAULT 0,
                    updated_at TEXT DEFAULT ''
                )"""
            )
            conn.commit()
        finally:
            conn.close()

    # ── 辅助 ───────────────────────────────────────────────────────

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    # ═══════════════════════════════════════════════════════════════
    # 管线 1: 正文拉取
    # ═══════════════════════════════════════════════════════════════

    async def fetch_bodies(self, limit: int = _BODY_FETCH_BATCH) -> dict[str, int]:
        """增量拉取 RSS/Web 文章正文。

        选择条件：
        - content_text 为空
        - body_fetch_attempts < 最大重试次数
        - 非视频/短内容来源（youtube/bilibili/douyin/xiaohongshu 走字幕或特殊处理）
        """
        conn = self._conn()
        rows = conn.execute(
            """SELECT id, url, source_type, body_fetch_attempts
               FROM articles
               WHERE (content_text IS NULL OR content_text = '')
                 AND body_fetch_attempts < ?
                 AND source_type NOT IN ('youtube','bilibili','bili','yt','douyin','xiaohongshu')
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
               SET content_text = ?,
                   content_hash = ?,
                   body_fetch_attempts = body_fetch_attempts + 1,
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
        self,
        limit: int = _YT_TRANSCRIPT_BATCH,
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

        logger.info("content_filler: yt_transcript %d/%d", results["fetched"], results["total"])
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
        self,
        limit: int = _BILI_SUBTITLE_BATCH,
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

        logger.info("content_filler: bili_subtitle %d/%d", results["fetched"], results["total"])
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
        self,
        limit: int = _AI_SUMMARY_BATCH,
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
            f'正文：\n"""\n{content}\n"""\n\n'
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

    # ═══════════════════════════════════════════════════════════════
    # 管线 5: 通过得到大脑填充正文+AI摘要
    # ═══════════════════════════════════════════════════════════════

    async def fetch_via_getnote(
        self,
        limit: int = _GETNOTE_BATCH,
    ) -> dict[str, int]:
        """通过 getnote 异步补正文+AI摘要（两阶段：播种 → 收割）。

        得到大脑 ``save`` 只是**提交**抓取任务，正文/AI 摘要由平台后台异步
        生成（视频类通常需数十秒到数分钟）。旧实现 save 后立即读、读不到就
        累加失败次数，导致大量文章被误判为永久失败而跳过，且丢掉的 note_id
        无法复用。这里改为：

        - **阶段A · 收割**: 处理 ``getnote_pending`` 队列中已过异步缓冲期的
          条目，``getnote note`` 读取内容；成功写回正文+摘要并出队；未就绪
          则保留等下轮；超轮数上限才放弃释放。
        - **阶段B · 播种**: 队列有容量时，为缺正文的新文章 ``save`` 并写入
          ``getnote_pending``，**立即返回不等待**，异步内容下一轮再收割。

        读取失败不再累加 ``body_fetch_attempts``（那是给“正文抓不到”的永久
        跳过机制），异步等待本身是正常现象。
        """
        return {
            **await self._harvest_getnote_pending(min(limit, _GETNOTE_HARVEST_PER_CALL)),
            **await self._seed_getnote_pending(min(limit, _GETNOTE_SEED_PER_CALL)),
        }

    # ── 阶段A：收割已就绪的笔记 ────────────────────────────────────
    async def _harvest_getnote_pending(
        self,
        limit: int,
    ) -> dict[str, int]:
        """收割队列表中已过异步缓冲期的条目，写回本地数据库。"""
        now = datetime.now()
        cutoff = (now - timedelta(seconds=_GETNOTE_POLL_WAIT_SECONDS)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        conn = self._conn()
        rows = conn.execute(
            """SELECT article_id, note_id, poll_attempts
               FROM getnote_pending
               WHERE saved_at <= ?
               ORDER BY saved_at ASC, article_id ASC
               LIMIT ?""",
            (cutoff, limit),
        ).fetchall()
        conn.close()

        results = {
            "harvested": 0,
            "harvest_total": len(rows),
            "harvest_pending": 0,
            "harvest_dropped": 0,
            "summarized": 0,
        }
        if not rows:
            return results

        import json
        import subprocess

        for row in rows:
            article_id = row["article_id"]
            note_id = row["note_id"]
            polls = row["poll_attempts"]

            try:
                web_content, ai_content = self._poll_note(note_id, subprocess)
                if (web_content or ai_content):
                    self._save_getnote_harvest(article_id, web_content, ai_content)
                    self._delete_pending(article_id)
                    results["harvested"] += 1
                    if ai_content:
                        results["summarized"] += 1
                    continue
                # 内容未就绪（平台仍在生成）
                if polls + 1 >= _GETNOTE_MAX_POLLS:
                    self._delete_pending(article_id)
                    self._increment_attempts(article_id)  # 释放占坑
                    results["harvest_dropped"] += 1
                    logger.debug(
                        "getnote: article %d 收割 %d 轮仍无内容，放弃",
                        article_id, polls + 1,
                    )
                else:
                    self._bump_pending(article_id, polls + 1)
                    results["harvest_pending"] += 1
                    logger.debug(
                        "getnote: article %d 内容未就绪，等待下轮(poll %d)",
                        article_id, polls + 1,
                    )
            except Exception as exc:
                logger.debug("getnote: harvest article %d failed: %s", article_id, exc)
                if polls + 1 >= _GETNOTE_MAX_POLLS:
                    self._delete_pending(article_id)
                    self._increment_attempts(article_id)
                    results["harvest_dropped"] += 1
                else:
                    self._bump_pending(article_id, polls + 1)
                    results["harvest_pending"] += 1

        logger.info(
            "content_filler: getnote harvest %d/%d (summarized:%d pending:%d dropped:%d)",
            results["harvested"], results["harvest_total"],
            results["summarized"], results["harvest_pending"], results["harvest_dropped"],
        )
        return results

    def _poll_note(
        self,
        note_id: str,
        subprocess: object,
    ) -> tuple[str, str]:
        """调用 getnote note <id> 读取，返回 (web正文, AI智能总结)。"""
        import json as _json

        cmd = ["getnote", "note", note_id, "-o", "json"]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_GETNOTE_NOTE_TIMEOUT
        )
        if proc.returncode != 0:
            return "", ""
        try:
            payload = _json.loads(proc.stdout or "{}")
        except _json.JSONDecodeError:
            return "", ""
        data = payload.get("data") if isinstance(payload, dict) else None
        note = (data or payload or {}).get("note") or None
        if not note and isinstance(data, dict):
            notes = data.get("notes") or []
            note = notes[0] if notes else None
        note = note or {}
        web_content = (note.get("web_page") or {}).get("content") or ""
        ai_content = note.get("content") or ""
        return web_content, ai_content

    def _save_getnote_harvest(self, article_id: int, web: str, ai: str) -> None:
        """写回正文+AI摘要（正文优先原始网页正文，否则用平台智能总结兜底）。"""
        import json as _json

        body = (web or "").strip()
        if len(body) < 200:
            body = (ai or "").strip()  # 视频类原始正文常为空，用智能总结当正文
        if body:
            self._save_body(article_id, body)
        if ai:
            summary = _json.dumps(
                {
                    "core": "",
                    "key_points": [],
                    "explanation": ai[:5000],
                    "source": "getnote",
                },
                ensure_ascii=False,
            )
            self._save_ai_summary(article_id, summary)

    # ── 阶段B：为新文章播种（save 并登记待收割） ───────────────────
    async def _seed_getnote_pending(
        self,
        limit: int,
    ) -> dict[str, int]:
        """为缺正文的新文章 save，登记进待收割队列；队列满则跳过。"""
        conn = self._conn()
        pending_count = conn.execute(
            "SELECT COUNT(*) FROM getnote_pending"
        ).fetchone()[0]
        if pending_count >= _GETNOTE_MAX_PENDING:
            conn.close()
            return {"seeded": 0, "seed_total": 0, "pending": pending_count}

        capacity = min(limit, _GETNOTE_MAX_PENDING - pending_count)
        rows = conn.execute(
            """SELECT id, url, title FROM articles
               WHERE (content_text IS NULL OR content_text = '')
                 AND url IS NOT NULL AND url != ''
                 AND body_fetch_attempts < ?
                 AND id NOT IN (SELECT article_id FROM getnote_pending)
               ORDER BY
                 CASE
                   WHEN source_type IN ('youtube','yt') THEN 0
                   WHEN source_type IN ('bilibili','bili') THEN 1
                   WHEN source_type IN ('douyin','kuaishou') THEN 2
                   WHEN source_type = 'xiaohongshu' THEN 3
                   ELSE 4
                 END,
                 body_fetch_attempts ASC,
                 id ASC
               LIMIT ?""",
            (_MAX_BODY_RETRIES, capacity),
        ).fetchall()
        conn.close()

        results = {"seeded": 0, "seed_total": len(rows), "pending": pending_count}
        if not rows:
            return results

        import subprocess

        for row in rows:
            article_id = row["id"]
            url = row["url"]
            title = row["title"] or ""
            try:
                note_id = self._run_getnote_save(url, title, subprocess)
                if not note_id:
                    logger.debug("getnote: save 无 note_id article %d", article_id)
                    self._mark_save_failed(article_id)
                    continue
                self._enqueue_pending(article_id, note_id)
                results["seeded"] += 1
                results["pending"] += 1
            except Exception as exc:
                logger.debug("getnote: seed article %d failed: %s", article_id, exc)
                self._mark_save_failed(article_id)

        logger.info(
            "content_filler: getnote seed %d/%d (pending now %d)",
            results["seeded"], results["seed_total"], results["pending"],
        )
        return results

    def _run_getnote_save(self, url: str, title: str, subprocess: object) -> str | None:
        """getnote save 提交抓取任务，返回 note_id；容错解析 table/json 输出。"""
        import json as _json

        cmd = [
            "getnote", "save", url,
            "--title", title[:200],
            "--tag", "AI摘要", "--tag", "自动填充",
            "-o", "json",
        ]
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_GETNOTE_SAVE_TIMEOUT
        )
        if proc.returncode != 0:
            # table 输出可能没有 "-o json"; 兼容
            combined = (proc.stdout or "") + (proc.stderr or "")
            if "ID" in combined and "|" in combined:
                for line in combined.splitlines():
                    if "ID" in line and "|" in line:
                        parts = line.split("|")
                        for p in reversed(parts):
                            if p.strip().isdigit():
                                return p.strip()
            return None
        try:
            payload = _json.loads(proc.stdout or "{}")
        except _json.JSONDecodeError:
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        data = data or {}
        return (
            data.get("note_id")
            or data.get("id")
            or data.get("note", {}).get("id")
            or (data.get("notes") or [{}])[0].get("id")
        )

    def _mark_save_failed(self, article_id: int) -> None:
        """save 阶段失败：仅视网络/超时，下一轮本方法会重新尝试播种。"""
        # 不累加 body_fetch_attempts，避免误杀；仅记录日志由上层配额控制频率
        logger.debug("getnote: save failed for article %d（下轮重试）", article_id)

    # ── getnote_pending 队列 CRUD ─────────────────────────────────
    def _enqueue_pending(self, article_id: int, note_id: str) -> None:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        conn = self._conn()
        try:
            conn.execute(
                """INSERT OR REPLACE INTO getnote_pending
                   (article_id, note_id, saved_at, poll_attempts, updated_at)
                   VALUES (?, ?, ?, 0, ?)""",
                (article_id, note_id, now, now),
            )
            conn.commit()
        finally:
            conn.close()

    def _bump_pending(self, article_id: int, polls: int) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE getnote_pending SET poll_attempts = ?, updated_at = ? WHERE article_id = ?",
                (polls, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), article_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _delete_pending(self, article_id: int) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM getnote_pending WHERE article_id = ?", (article_id,))
            conn.commit()
        finally:
            conn.close()
