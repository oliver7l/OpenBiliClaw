"""Self-evolution loop engine.

Provides persistent state tracking, multi-level content filtering,
quality-based prioritization, and batch accumulation so the
``_loop_self_evolution`` background task in ``refresh.py`` can
process new content incrementally without re-scanning the entire
database on every tick.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.diary.models import DiaryAnalysis

import asyncio
import json
import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import open_db_conn

# 商汤日日新配额限制（每 5 小时 60,000 点 ≈ 60M tokens）
_SENSENOVA_QUOTA_LIMIT = 60000
_SENSENOVA_QUOTA_WINDOW_HOURS = 5
# 留 20% 余量，超过 80% 时暂停 LLM 密集型任务
_SENSENOVA_QUOTA_SOFT_LIMIT = 0.8

logger = logging.getLogger("self_evolution.loop_engine")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Batch processing: wait until this many NEW articles accumulate before
# running LLM-heavy tasks (TL;DR, knowledge cards, etc.)
_BATCH_THRESHOLD = 20
# Maximum time (hours) before forcing a batch run even if under threshold
_BATCH_MAX_HOURS = 24
# Minimum content length (chars) for an article to be worth processing
_MIN_CONTENT_CHARS = 200
# How many articles to process in one batch for LLM-heavy tasks
_BATCH_PROCESS_LIMIT = 15

# ---------------------------------------------------------------------------
# Persistent state
# ---------------------------------------------------------------------------


class SelfEvolutionState:
    """Persistent state tracking for the self-evolution loop.

    Stores keys in a ``self_evolution_state`` table so state survives
    API restarts.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._ensure_table()

    def _get_conn(self) -> sqlite3.Connection:
        conn = open_db_conn(self._db_path)
        conn.row_factory = sqlite3.Row

        # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
        from contextlib import suppress as _suppress
        from pathlib import Path as _Path
        _content_path = _Path(str(self._db_path)).with_name('content.db')
        if _content_path.exists():
            with _suppress(Exception):
                conn.execute('ATTACH DATABASE ? AS content', (str(_content_path),))
        return conn

    def _ensure_table(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS self_evolution_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def get(self, key: str, default: str | None = None) -> str | None:
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM self_evolution_state WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        with self._get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO self_evolution_state (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    # -- Convenience accessors ------------------------------------------------

    @property
    def last_processed_article_id(self) -> int:
        raw = self.get("last_processed_article_id", "0")
        if raw is None:
            return 0
        try:
            return int(raw)
        except (ValueError, TypeError):
            return 0

    @last_processed_article_id.setter
    def last_processed_article_id(self, value: int) -> None:
        self.set("last_processed_article_id", str(value))

    @property
    def last_batch_run_at(self) -> datetime | None:
        raw = self.get("last_batch_run_at")
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            return None

    @last_batch_run_at.setter
    def last_batch_run_at(self, value: datetime) -> None:
        self.set("last_batch_run_at", value.isoformat())

    def get_timestamp(self, key: str) -> datetime | None:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            return None

    def set_timestamp(self, key: str, value: datetime) -> None:
        self.set(key, value.isoformat())


# ---------------------------------------------------------------------------
# Content filtering & prioritisation
# ---------------------------------------------------------------------------


class ContentFilter:
    """Multi-level content filtering pipeline.

    Levels
    ------
    **Rule** (zero-cost)
        Skip articles that are too short, have no content text, or
        already have a TL;DR / knowledge cards.
    **Quality** (lightweight SQL)
        Prioritise articles that have user engagement signals (favorite,
        like, view events) and prefer newer content.
    **LLM** (only runs on the final shortlist)
        The caller handles LLM calls — this filter just produces the
        candidate list.
    """

    @staticmethod
    def count_new_articles(conn: sqlite3.Connection, since_id: int) -> int:
        """How many articles have been added since ``since_id``."""
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM content.articles WHERE id > ?", (since_id,)
        ).fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def top_candidates(
        conn: sqlite3.Connection,
        *,
        limit: int = 15,
        since_id: int = 0,
        min_chars: int = _MIN_CONTENT_CHARS,
        exclude_with_tldr: bool = True,
        exclude_with_cards: bool = True,
    ) -> list[int]:
        """Return article IDs prioritised by quality, newest first.

        Applies rule filters first, then sorts by a simple quality score
        (favorited → liked → viewed → others) so the most engaging
        content gets processed first.
        """
        conditions = [
            "a.content_text IS NOT NULL",
            f"length(a.content_text) >= {min_chars}",
            f"a.id > {since_id}",
        ]
        if exclude_with_tldr:
            conditions.append("a.id NOT IN (SELECT article_id FROM content.article_tldrs)")
        if exclude_with_cards:
            conditions.append("a.id NOT IN (SELECT source_article_id FROM knowledge.knowledge_cards)")

        where_clause = " AND ".join(conditions)

        query = f"""
            SELECT a.id,
                   CASE WHEN a.favorited = 1 THEN 3
                        WHEN EXISTS (SELECT 1 FROM events.events e
                                     WHERE e.article_id = a.id
                                       AND e.event_type IN ('favorite','like'))
                        THEN 2
                        WHEN EXISTS (SELECT 1 FROM events.events e
                                     WHERE e.article_id = a.id
                                       AND e.event_type = 'view')
                        THEN 1
                        ELSE 0 END AS quality_score
            FROM content.articles a
            WHERE {where_clause}
            ORDER BY quality_score DESC, a.id DESC
            LIMIT ?
        """
        rows = conn.execute(query, (limit,)).fetchall()
        return [r["id"] for r in rows]

    @staticmethod
    def count_unprocessed_articles(
        conn: sqlite3.Connection, since_id: int = 0, min_chars: int = _MIN_CONTENT_CHARS
    ) -> int:
        """Count articles that are new and long enough to process."""
        row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM content.articles a
            WHERE a.id > ?
              AND a.content_text IS NOT NULL
              AND length(a.content_text) >= ?
              AND a.id NOT IN (SELECT article_id FROM content.article_tldrs)
              AND a.id NOT IN (SELECT source_article_id FROM knowledge.knowledge_cards)
            """,
            (since_id, min_chars),
        ).fetchone()
        return row["cnt"] if row else 0


# ---------------------------------------------------------------------------
# Sliding window statistics
# ---------------------------------------------------------------------------


class SlidingWindowStats:
    """Rolling window statistics for topic / platform / interest tracking.

    Maintains pre-computed counts for three window sizes so the
    interest-drift detector can compare short vs long windows without
    re-scanning the entire database.
    """

    _WINDOW_DAYS = [7, 30, 90]

    @staticmethod
    def refresh(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
        """Compute and store sliding-window stats in a summary table.

        Returns a dict with the latest snapshot for the 7d window.
        """
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS self_evolution_window_stats (
                window_days INTEGER,
                computed_at TEXT,
                topic_json TEXT,
                platform_json TEXT,
                total_articles INTEGER,
                PRIMARY KEY (window_days)
            )
            """
        )

        results = {}
        for days in SlidingWindowStats._WINDOW_DAYS:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()

            # Topic distribution
            topic_rows = conn.execute(
                """
                SELECT topic_group, COUNT(*) AS cnt
                FROM content.articles
                WHERE created_at >= ? AND topic_group IS NOT NULL
                GROUP BY topic_group
                ORDER BY cnt DESC LIMIT 30
                """,
                (cutoff,),
            ).fetchall()
            topics = {r["topic_group"]: r["cnt"] for r in topic_rows}

            # Platform distribution
            plat_rows = conn.execute(
                """
                SELECT source_type, COUNT(*) AS cnt
                FROM content.articles
                WHERE created_at >= ?
                GROUP BY source_type
                ORDER BY cnt DESC
                """,
                (cutoff,),
            ).fetchall()
            platforms = {r["source_type"]: r["cnt"] for r in plat_rows}

            total = conn.execute(
                "SELECT COUNT(*) AS cnt FROM content.articles WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()["cnt"]

            payload = json.dumps(
                {
                    "topics": topics,
                    "platforms": platforms,
                    "total_articles": total,
                }
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO self_evolution_window_stats
                (window_days, computed_at, topic_json, platform_json, total_articles)
                VALUES (?, ?, ?, ?, ?)
                """,
                (days, datetime.now().isoformat(), payload, payload, total),
            )
            results[days] = {"topics": topics, "platforms": platforms, "total": total}

        conn.commit()
        return results

    @staticmethod
    def get_latest(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
        """Get the most recent snapshot for each window."""
        results: dict[int, dict[str, Any]] = {}
        for days in SlidingWindowStats._WINDOW_DAYS:
            row = conn.execute(
                """
                SELECT topic_json, platform_json, total_articles
                FROM self_evolution_window_stats
                WHERE window_days = ?
                ORDER BY computed_at DESC LIMIT 1
                """,
                (days,),
            ).fetchone()
            if row:
                try:
                    data = json.loads(row["topic_json"])
                    results[days] = {
                        "topics": data.get("topics", {}),
                        "platforms": data.get("platforms", {}),
                        "total": row["total_articles"],
                    }
                except (json.JSONDecodeError, TypeError):
                    pass
        return results


# ---------------------------------------------------------------------------
# Loop engine — orchestrator
# ---------------------------------------------------------------------------


class SelfEvolutionLoopEngine:
    """Orchestrates self-evolution tasks with incremental state, filtering,
    batch accumulation, and quality prioritisation.

    Designed to be called from ``ContinuousRefreshController._loop_self_evolution()``
    on a 1-hour tick.
    """

    def __init__(
        self,
        db_path: str,
        llm_service: Any = None,
        *,
        batch_threshold: int = _BATCH_THRESHOLD,
        batch_max_hours: int = _BATCH_MAX_HOURS,
    ) -> None:
        self._db_path = db_path
        self._llm_service = llm_service
        self._batch_threshold = batch_threshold
        self._batch_max_hours = batch_max_hours
        self._state = SelfEvolutionState(db_path)

    # -- Quota awareness -------------------------------------------------------

    def _quota_ok(self) -> bool:
        """检查商汤日日新配额是否充足。

        查询最近 5 小时的 token 使用量，如果超过软限制（80%）则暂停
        LLM 密集型任务，避免触发上游限流。
        """
        try:
            conn = open_db_conn(self._db_path)
            cutoff = (datetime.now() - timedelta(hours=_SENSENOVA_QUOTA_WINDOW_HOURS)).isoformat()
            total_tokens = conn.execute(
                "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) "
                "FROM llm_usage WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()[0]
            conn.close()
            # 假设 1 point ≈ 1000 tokens
            estimated_points = max(1, total_tokens // 1000)
            usage_pct = estimated_points / _SENSENOVA_QUOTA_LIMIT
            if usage_pct >= _SENSENOVA_QUOTA_SOFT_LIMIT:
                logger.info(
                    "self_evolution: quota usage %.1f%% >= soft limit %.0f%%, "
                    "skipping LLM-heavy tasks",
                    usage_pct * 100,
                    _SENSENOVA_QUOTA_SOFT_LIMIT * 100,
                )
                return False
            return True
        except Exception:
            logger.debug("self_evolution: quota check failed", exc_info=True)
            return True  # 配额检查失败时允许执行，避免误拦

    # -- Tick entry point -----------------------------------------------------

    async def run_tick(self) -> dict[str, Any]:
        """Run one tick of the self-evolution loop.

        Returns a dict with the results of each module that ran.
        """
        now = datetime.now()
        results: dict[str, Any] = {}

        with open_db_conn(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            # v0.4.0+: articles/article_tldrs 迁到 content.db，主库连接需 ATTACH
            # content 别名，SQL 以 content.articles / content.article_tldrs 前缀访问
            # （与 SelfEvolutionState._get_conn 的 ATTACH 保持一致）。
            from contextlib import suppress as _suppress

            _content_path = Path(str(self._db_path)).with_name("content.db")
            if _content_path.exists():
                with _suppress(Exception):
                    conn.execute("ATTACH DATABASE ? AS content", (str(_content_path),))

            # ── Check batch accumulation ────────────────────────────────────
            since_id = self._state.last_processed_article_id
            new_count = ContentFilter.count_new_articles(conn, since_id)
            last_batch = self._state.last_batch_run_at
            hours_since_batch = (now - last_batch).total_seconds() / 3600 if last_batch else 999

            enough_new = new_count >= self._batch_threshold
            force_timeout = hours_since_batch >= self._batch_max_hours

            if not enough_new and not force_timeout:
                logger.debug(
                    "self_evolution: batch threshold not met "
                    "(%d new, need %d; %.1fh since last batch, max %.1fh)",
                    new_count,
                    self._batch_threshold,
                    hours_since_batch,
                    self._batch_max_hours,
                )
                results["batched"] = False
                results["new_count"] = new_count
                return results

            results["batched"] = True
            results["new_count"] = new_count
            logger.info(
                "self_evolution: batch triggered (%d new articles, %.1fh since last)",
                new_count,
                hours_since_batch,
            )

            # ── Step 1: refresh sliding window stats ────────────────────────
            try:
                window_stats = SlidingWindowStats.refresh(conn)
                results["window_stats"] = {
                    d: {"total": s["total"]} for d, s in window_stats.items()
                }
            except Exception:
                logger.debug("self_evolution: window_stats failed", exc_info=True)

            # ── Step 2: pick top-quality candidates ──────────────────────────
            candidate_ids = ContentFilter.top_candidates(
                conn,
                limit=_BATCH_PROCESS_LIMIT,
                since_id=since_id,
            )
            results["candidates"] = len(candidate_ids)

            if not candidate_ids:
                logger.info(
                    "self_evolution: no new candidates to process (last_id=%d, new_count=%d)",
                    since_id,
                    new_count,
                )
                # Still update last_processed_article_id so we don't re-scan
                # processed articles
                self._update_last_id(conn)

        # ── Step 3: Content filler (body, subtitle, ai_summary, getnote) ──
        if self._quota_ok():
            # Body fetch: every tick, up to 30 articles
            await self._run_if_due(
                "content_filler_body",
                1,
                results,
                lambda: self._do_content_filler_body(),
            )
            # YouTube subtitle: every tick, up to 10 videos
            if self._quota_ok():
                await self._run_if_due(
                    "content_filler_yt",
                    1,
                    results,
                    lambda: self._do_content_filler_yt(),
                )
            # Bilibili subtitle: every tick, up to 10 videos
            if self._quota_ok():
                await self._run_if_due(
                    "content_filler_bili",
                    1,
                    results,
                    lambda: self._do_content_filler_bili(),
                )
            # AI summary: every tick, up to 20 articles (LLM heavy)
            if self._quota_ok():
                await self._run_if_due(
                    "content_filler_ai",
                    1,
                    results,
                    lambda: self._do_content_filler_ai(),
                )

        # ── Step 4: TL;DR (batch) ───────────────────────────────────────────
        if candidate_ids and self._quota_ok():
            try:
                from openbiliclaw.self_evolution.tldr import TLDRGenerator

                tldr = TLDRGenerator(self._db_path, llm_service=self._llm_service)
                tldr_results = await asyncio_to_thread(
                    tldr.batch_generate,
                    only_favorited=False,
                    limit=len(candidate_ids),
                    force=False,
                )
                results["tldr"] = len(tldr_results) if tldr_results else 0
            except Exception:
                logger.debug("self_evolution: tldr failed", exc_info=True)

            # ── Step 4: Knowledge cards (batch) ─────────────────────────────
            if self._quota_ok():
                try:
                    from openbiliclaw.self_evolution.knowledge_card import (
                        KnowledgeCardGenerator,
                    )

                    kc = KnowledgeCardGenerator(self._db_path, llm_service=self._llm_service)
                    cards = await asyncio_to_thread(
                        kc.generate_cards_batch,
                        article_ids=candidate_ids,
                        max_cards_per_article=3,
                    )
                    results["knowledge_cards"] = len(cards) if cards else 0
                except Exception:
                    logger.debug("self_evolution: knowledge_card failed", exc_info=True)

            # Update last processed ID to the highest in this batch
            self._state.last_processed_article_id = max(candidate_ids)

        # ── Step 5: Diary analysis (every tick, incremental) ────────────────
        if self._quota_ok():
            await self._run_if_due(
                "diary_analysis",
                1,
                results,
                lambda: self._do_diary_analysis(),
            )

        # ── Step 6: Chat analysis (every 6 hours, incremental) ──────────────
        if self._quota_ok():
            await self._run_if_due(
                "chat_analysis",
                6,
                results,
                lambda: self._do_chat_analysis(),
            )

        # ── Step 7: Interest drift (daily) ──────────────────────────────────
        await self._run_if_due(
            "drift",
            24,
            results,
            lambda: self._do_drift_detect(),
        )

        # ── Step 8: Insight report (daily) ──────────────────────────────────
        if self._quota_ok():
            await self._run_if_due(
                "insight_report",
                24,
                results,
                lambda: self._do_insight_report(),
            )

        # ── Step 9: Topic mining (every 3 days) ─────────────────────────────
        await self._run_if_due(
            "topic_miner",
            72,
            results,
            lambda: self._do_topic_mining(),
        )

        # ── Step 10: Knowledge graph (every 3 days, incremental) ─────────────
        await self._run_if_due(
            "knowledge_graph",
            72,
            results,
            lambda: self._do_knowledge_graph(),
        )

        # ── Step 11: Auto topic (weekly) ────────────────────────────────────
        if self._quota_ok():
            await self._run_if_due(
                "auto_topic",
                168,
                results,
                lambda: self._do_auto_topic(),
            )

        # ── Step 12: Content insights (weekly) ──────────────────────────────
        if self._quota_ok():
            await self._run_if_due(
                "content_insights",
                168,
                results,
                lambda: self._do_content_insights(),
            )

        # ── Step 13: Cross-module synthesis (every 6 hours) ─────────────────
        if self._quota_ok():
            await self._run_if_due(
                "synthesis",
                6,
                results,
                lambda: self._do_synthesis(),
            )

        # Save batch timestamp
        self._state.last_batch_run_at = datetime.now()

        return results

    # -- Helpers --------------------------------------------------------------

    def _update_last_id(self, conn: sqlite3.Connection) -> None:
        try:
            row = conn.execute("SELECT MAX(id) AS max_id FROM content.articles").fetchone()
            if row and row["max_id"]:
                self._state.last_processed_article_id = int(row["max_id"])
        except Exception:
            pass

    async def _run_if_due(
        self,
        key: str,
        hours: int,
        results: dict[str, Any],
        coro_fn: Any,
    ) -> None:
        last = self._state.get_timestamp(f"last_{key}")
        if last is not None and (datetime.now() - last).total_seconds() < hours * 3600:
            return
        try:
            result = await coro_fn()
            if result is not None:
                results[key] = result
            self._state.set_timestamp(f"last_{key}", datetime.now())
        except Exception:
            logger.debug("self_evolution: %s failed", key, exc_info=True)

    # -- Module dispatchers ---------------------------------------------------

    async def _do_drift_detect(self) -> dict[str, Any] | None:
        from openbiliclaw.self_evolution.interest_drift import (
            InterestDriftDetector,
        )

        detector = InterestDriftDetector(self._db_path)
        report = await asyncio_to_thread(
            detector.detect,
            current_window_days=7,
            previous_window_days=30,
        )
        await asyncio_to_thread(detector.save_report, report)
        if report.alerts:
            logger.info("self_evolution: drift detected %d alerts", len(report.alerts))
            return {"alerts": len(report.alerts)}
        return None

    async def _do_insight_report(self) -> dict[str, Any] | None:
        from openbiliclaw.self_evolution.insight_report import (
            InsightReportGenerator,
        )

        gen = InsightReportGenerator(self._db_path, llm_service=self._llm_service)
        report = await asyncio_to_thread(
            gen.generate_report, window_days=7, include_llm_summary=True
        )
        await asyncio_to_thread(gen.save_report, report)
        logger.info("self_evolution: insight report generated (window=7d)")
        return {"report_id": report.report_id}

    async def _do_topic_mining(self) -> dict[str, Any] | None:
        from openbiliclaw.self_evolution.topic_miner import TopicMiner

        miner = TopicMiner(self._db_path)
        report = await asyncio_to_thread(miner.mine, window_days=14, auto_create=True)
        await asyncio_to_thread(miner.save_report, report)
        if report.candidates:
            logger.info(
                "self_evolution: mined %d topic candidates",
                len(report.candidates),
            )
            return {"candidates": len(report.candidates)}
        return None

    async def _do_knowledge_graph(self) -> dict[str, Any] | None:
        from openbiliclaw.self_evolution.knowledge_graph import (
            KnowledgeGraphBuilder,
        )

        builder = KnowledgeGraphBuilder(self._db_path)
        graph = await asyncio_to_thread(builder.build, limit=1000, min_mentions=2)
        await asyncio_to_thread(builder.save_graph, graph)
        logger.info(
            "self_evolution: knowledge graph built (%d entities, %d rels)",
            len(graph.entities),
            len(graph.relationships),
        )
        return {
            "entities": len(graph.entities),
            "relationships": len(graph.relationships),
        }

    async def _do_auto_topic(self) -> dict[str, Any] | None:
        from openbiliclaw.self_evolution.auto_topic_generator import (
            AutoTopicGenerator,
        )

        atg = AutoTopicGenerator(self._db_path, llm_service=self._llm_service)
        topics = await asyncio_to_thread(
            atg.auto_generate, min_mentions=30, max_topics=2, use_llm=True
        )
        if topics:
            logger.info("self_evolution: auto-generated %d topics", len(topics))
            return {"topics": len(topics)}
        return None

    async def _do_content_insights(self) -> dict[str, Any] | None:
        from openbiliclaw.self_evolution.insights import (
            ContentInsightsAnalyzer,
        )

        analyzer = ContentInsightsAnalyzer(self._db_path, llm_service=self._llm_service)
        report = await asyncio_to_thread(analyzer.generate_report)
        logger.info(
            "self_evolution: content insights generated (%d gaps, %d cross-plat)",
            len(report.knowledge_gaps),
            len(report.cross_platform_insights),
        )
        return {
            "gaps": len(report.knowledge_gaps),
            "cross_platform": len(report.cross_platform_insights),
        }

    async def _do_content_filler_body(self) -> dict[str, int] | None:
        """增量拉取 RSS/Web 文章正文。"""
        try:
            from openbiliclaw.self_evolution.content_filler import ContentFiller

            filler = ContentFiller(self._db_path)
            result = await filler.fetch_bodies()
            if result and result.get("fetched"):
                logger.info("content_filler: body_fetch done: %s", result)
            return result
        except Exception:
            logger.debug("content_filler: body_fetch failed", exc_info=True)
            return None

    async def _do_content_filler_yt(self) -> dict[str, int] | None:
        """增量提取 YouTube 视频字幕。"""
        try:
            from openbiliclaw.self_evolution.content_filler import ContentFiller

            filler = ContentFiller(self._db_path)
            result = await filler.fetch_youtube_transcripts()
            if result and result.get("fetched"):
                logger.info("content_filler: yt_transcript done: %s", result)
            return result
        except Exception:
            logger.debug("content_filler: yt_transcript failed", exc_info=True)
            return None

    async def _do_content_filler_bili(self) -> dict[str, int] | None:
        """增量提取 Bilibili 视频字幕。"""
        try:
            from openbiliclaw.self_evolution.content_filler import ContentFiller

            filler = ContentFiller(self._db_path)
            result = await filler.fetch_bilibili_subtitles()
            if result and result.get("fetched"):
                logger.info("content_filler: bili_subtitle done: %s", result)
            return result
        except Exception:
            logger.debug("content_filler: bili_subtitle failed", exc_info=True)
            return None

    async def _do_content_filler_ai(self) -> dict[str, int] | None:
        """批量生成 AI 摘要（LLM 密集型）。"""
        try:
            from openbiliclaw.self_evolution.content_filler import ContentFiller

            filler = ContentFiller(self._db_path, llm_service=self._llm_service)
            result = await filler.generate_ai_summaries()
            if result and result.get("summarized"):
                logger.info("content_filler: ai_summary done: %s", result)
            return result
        except Exception:
            logger.debug("content_filler: ai_summary failed", exc_info=True)
            return None

    async def _do_diary_analysis(self) -> dict[int, DiaryAnalysis | None] | None:
        """增量分析未分析的日记，每 tick 最多处理 20 篇。"""
        try:
            from openbiliclaw.diary.service import DiaryService
            from openbiliclaw.diary.store import DiaryStore

            store = DiaryStore(db_path=self._db_path)
            store.initialize()
            svc = DiaryService(
                database=store._database,
                db_path=self._db_path,
                llm_service=self._llm_service,
            )
            result = await svc.analyze_unanalyzed(limit=20, concurrency=3)
            if result:
                logger.info("self_evolution: diary analysis done: %s", result)
            return result
        except Exception:
            logger.debug("self_evolution: diary analysis failed", exc_info=True)
            return None

    async def _do_chat_analysis(self) -> dict[str, Any] | None:
        """增量分析未分析的聊天会话，每 6 小时最多处理 10 个会话。"""
        try:
            from openbiliclaw.chat_analysis.service import ChatAnalysisService

            svc = ChatAnalysisService(db_path=Path("data/chat_analysis.db"))
            result = await svc.analyze_unanalyzed(limit=10, concurrency=2)
            if result:
                logger.info("self_evolution: chat analysis done: %s", result)
            return result
        except Exception:
            logger.debug("self_evolution: chat analysis failed", exc_info=True)
            return None

    async def _do_synthesis(self) -> dict[str, Any] | None:
        """执行跨模块迭代合成（日记+聊天+文章洞察）。"""
        try:
            from openbiliclaw.synthesis.engine import SynthesisEngine
            from openbiliclaw.synthesis.models import SynthesisConfig

            config = SynthesisConfig(main_db_path=self._db_path)
            engine = SynthesisEngine(
                config=config,
                llm_service=self._llm_service,
            )
            result = await engine.run()
            if result is None:
                logger.info("self_evolution: synthesis skipped (no new data)")
                return {"skipped": True}
            logger.info(
                "self_evolution: synthesis v%d saved (%d diary, %d insights, %d topics)",
                result.version,
                result.new_diary_count,
                result.new_chat_insight_count,
                result.new_chat_topic_count,
            )
            return {
                "version": result.version,
                "diary": result.new_diary_count,
                "insights": result.new_chat_insight_count,
                "topics": result.new_chat_topic_count,
            }
        except Exception:
            logger.debug("self_evolution: synthesis failed", exc_info=True)
            return None


# ---------------------------------------------------------------------------
# Thread bridge
# ---------------------------------------------------------------------------


async def asyncio_to_thread(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Run a sync function in a thread so the event loop is not blocked."""

    return await asyncio.to_thread(fn, *args, **kwargs)
