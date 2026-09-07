"""自进化模块 API 路由。

将洞察报告、兴趣漂移、专题挖掘、知识卡片、知识图谱、主动推送等
自进化功能的 HTTP 端点独立为 APIRouter，由主应用注册。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from openbiliclaw.self_evolution import (
    InsightReportGenerator,
    InterestDriftDetector,
    KnowledgeCardGenerator,
    KnowledgeGraphBuilder,
    ProactivePushEngine,
    PushConfig,
    TopicMiner,
)

logger = logging.getLogger(__name__)


def create_self_evolution_router(db_path: str, llm_service: Any = None) -> APIRouter:
    """创建自进化模块的 API 路由。"""
    router = APIRouter(prefix="/api/self-evolution", tags=["self-evolution"])

    @router.get("/insight-reports")
    async def list_insight_reports(limit: int = 20):
        """List saved insight reports."""
        gen = InsightReportGenerator(db_path)
        return {"reports": gen.list_reports(limit=limit)}

    @router.post("/insight-reports/generate")
    async def generate_insight_report(window_days: int = 7, include_llm: bool = True):
        """Generate a new insight report."""
        llm_svc = llm_service if include_llm else None
        gen = InsightReportGenerator(db_path, llm_service=llm_svc)
        report = gen.generate_report(window_days=window_days, include_llm_summary=include_llm)
        gen.save_report(report)
        return report.to_dict()

    @router.get("/insight-reports/{report_id}")
    async def get_insight_report(report_id: str):
        """Get a specific insight report."""
        gen = InsightReportGenerator(db_path)
        report = gen.get_report(report_id)
        if not report:
            return {"error": "Report not found"}
        return report

    @router.get("/drift")
    async def get_interest_drift(current_window_days: int = 7, previous_window_days: int = 30):
        """Get interest drift analysis."""
        detector = InterestDriftDetector(db_path)
        report = detector.detect(
            current_window_days=current_window_days,
            previous_window_days=previous_window_days,
        )
        detector.save_report(report)
        return report.to_dict()

    @router.get("/topics")
    async def get_mined_topics(window_days: int = 14):
        """Get auto-mined topic candidates."""
        miner = TopicMiner(db_path)
        report = miner.mine(window_days=window_days)
        miner.save_report(report)
        return report.to_dict()

    @router.get("/knowledge-cards")
    async def list_knowledge_cards(limit: int = 50, card_type: str = None):
        """List knowledge cards."""
        gen = KnowledgeCardGenerator(db_path)
        cards = gen.list_cards(limit=limit, card_type=card_type)
        return {"cards": [c.to_dict() for c in cards], "total": len(cards)}

    @router.post("/knowledge-cards/generate")
    async def generate_knowledge_cards(article_id: int = None, limit: int = 20, max_per_article: int = 3):
        """Generate knowledge cards from articles."""
        gen = KnowledgeCardGenerator(db_path, llm_service=llm_service)
        if article_id:
            cards = gen.generate_cards_from_article(article_id, max_cards=max_per_article)
        else:
            cards = gen.generate_cards_batch(limit=limit, max_cards_per_article=max_per_article)
        return {"cards": [c.to_dict() for c in cards], "generated": len(cards)}

    @router.get("/knowledge-cards/due")
    async def get_due_cards(limit: int = 20):
        """Get knowledge cards due for review."""
        gen = KnowledgeCardGenerator(db_path)
        session = gen.create_review_session(limit=limit)
        return session.to_dict()

    @router.post("/knowledge-cards/{card_id}/review")
    async def review_card(card_id: str, quality: int = 4):
        """Record a card review and update scheduling."""
        gen = KnowledgeCardGenerator(db_path)
        card = gen.record_review(card_id, quality)
        if not card:
            return {"error": "Card not found"}
        return card.to_dict()

    @router.get("/knowledge-graph")
    async def get_knowledge_graph(limit: int = 500, min_mentions: int = 2):
        """Build and return the personal knowledge graph."""
        builder = KnowledgeGraphBuilder(db_path)
        graph = builder.build(limit=limit, min_mentions=min_mentions)
        builder.save_graph(graph)
        return graph.to_dict()

    @router.get("/knowledge-graph/entity/{entity_id}")
    async def get_entity_subgraph(entity_id: str, depth: int = 2, max_nodes: int = 50):
        """Get a subgraph around a specific entity."""
        builder = KnowledgeGraphBuilder(db_path)
        graph = builder.load_latest_graph()
        if not graph:
            graph = builder.build(limit=500, min_mentions=2)
        return graph.get_subgraph(entity_id, depth=depth, max_nodes=max_nodes)

    @router.get("/notifications")
    async def list_notifications(limit: int = 20, unread_only: bool = False):
        """List push notifications."""
        engine = ProactivePushEngine(db_path)
        notifs = engine.get_notifications(limit=limit, unread_only=unread_only)
        return {"notifications": [n.to_dict() for n in notifs], "total": len(notifs)}

    @router.post("/notifications/check")
    async def check_and_push(dry_run: bool = False):
        """Check for high-value content and push notifications."""
        config = PushConfig(enabled=True)
        engine = ProactivePushEngine(db_path, config=config)
        notifs = engine.check_and_push(dry_run=dry_run)
        return {"notifications": [n.to_dict() for n in notifs], "count": len(notifs)}

    @router.post("/notifications/{notification_id}/read")
    async def mark_notification_read(notification_id: str):
        """Mark a notification as read."""
        engine = ProactivePushEngine(db_path)
        engine.mark_as_read(notification_id)
        return {"status": "ok"}

    @router.post("/notifications/{notification_id}/dismiss")
    async def dismiss_notification(notification_id: str):
        """Dismiss a notification."""
        engine = ProactivePushEngine(db_path)
        engine.dismiss(notification_id)
        return {"status": "ok"}

    # ─── Learning Paths ───────────────────────────────────────────────

    @router.get("/learning-paths")
    async def list_learning_paths(status: str | None = None, limit: int = 20):
        """List all learning paths."""
        from openbiliclaw.self_evolution.learning_path import LearningPathGenerator
        llm_service = llm_service
        generator = LearningPathGenerator(db_path, llm_service=llm_service)
        paths = generator.list_paths(status=status, limit=limit)
        return {"paths": [p.to_dict() for p in paths]}

    @router.post("/learning-paths/generate")
    async def generate_learning_path(payload: dict[str, Any]):
        """Generate a learning path for a topic.

        Request body:
        - topic: 学习主题（必填）
        - description: 学习目标/描述（可选）
        - max_articles: 最多考虑多少篇文章（默认30）
        """
        from openbiliclaw.self_evolution.learning_path import LearningPathGenerator
        topic = (payload or {}).get("topic", "").strip()
        if not topic:
            return JSONResponse({"error": "topic is required"}, status_code=400)
        description = (payload or {}).get("description", "")
        max_articles = int((payload or {}).get("max_articles", 30))
        llm_service = llm_service
        generator = LearningPathGenerator(db_path, llm_service=llm_service)
        try:
            path = generator.generate_path(
                topic, description=description, max_articles=max_articles
            )
            return {"status": "ok", "path": path.to_dict()}
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            logger.exception("学习路径生成失败")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/learning-paths/{path_id}")
    async def get_learning_path(path_id: str):
        """Get a learning path by ID."""
        from openbiliclaw.self_evolution.learning_path import LearningPathGenerator
        llm_service = llm_service
        generator = LearningPathGenerator(db_path, llm_service=llm_service)
        path = generator.get_path(path_id)
        if path is None:
            return JSONResponse({"error": "path not found"}, status_code=404)
        return {"path": path.to_dict()}

    @router.patch("/learning-paths/{path_id}/steps/{step_index}")
    async def update_learning_step(
        path_id: str, step_index: int, payload: dict[str, Any]
    ):
        """Update progress on a learning path step.

        Request body:
        - completed: 是否完成（布尔）
        - notes: 学习笔记（可选）
        """
        from openbiliclaw.self_evolution.learning_path import LearningPathGenerator
        llm_service = llm_service
        generator = LearningPathGenerator(db_path, llm_service=llm_service)
        completed = (payload or {}).get("completed")
        notes = (payload or {}).get("notes")
        path = generator.update_step_progress(
            path_id, step_index, completed=completed, notes=notes
        )
        if path is None:
            return JSONResponse({"error": "path or step not found"}, status_code=404)
        return {"status": "ok", "path": path.to_dict()}

    @router.delete("/learning-paths/{path_id}")
    async def delete_learning_path(path_id: str):
        """Delete a learning path."""
        from openbiliclaw.self_evolution.learning_path import LearningPathGenerator
        llm_service = llm_service
        generator = LearningPathGenerator(db_path, llm_service=llm_service)
        ok = generator.delete_path(path_id)
        if not ok:
            return JSONResponse({"error": "path not found"}, status_code=404)
        return {"status": "ok"}

    # ─── TL;DR (Too Long; Didn't Read) ────────────────────────────────

    @router.get("/tldrs")
    async def list_tldrs(limit: int = 50, source_type: str | None = None):
        """List all cached TL;DR summaries."""
        from openbiliclaw.self_evolution.tldr import TLDRGenerator
        llm_service = llm_service
        generator = TLDRGenerator(db_path, llm_service=llm_service)
        tldrs = generator.list_tldrs(limit=limit, source_type=source_type)
        return {"tldrs": [t.to_dict() for t in tldrs], "count": len(tldrs)}

    @router.get("/tldrs/{article_id}")
    async def get_tldr(article_id: int):
        """Get a TL;DR for a specific article (generates if not cached)."""
        from openbiliclaw.self_evolution.tldr import TLDRGenerator
        llm_service = llm_service
        generator = TLDRGenerator(db_path, llm_service=llm_service)
        tldr = generator.generate_tldr(article_id)
        if tldr is None:
            return JSONResponse({"error": "article not found"}, status_code=404)
        return {"tldr": tldr.to_dict()}

    @router.post("/tldrs/{article_id}/regenerate")
    async def regenerate_tldr(article_id: int):
        """Force regenerate a TL;DR for an article."""
        from openbiliclaw.self_evolution.tldr import TLDRGenerator
        llm_service = llm_service
        generator = TLDRGenerator(db_path, llm_service=llm_service)
        tldr = generator.generate_tldr(article_id, force=True)
        if tldr is None:
            return JSONResponse({"error": "article not found"}, status_code=404)
        return {"status": "ok", "tldr": tldr.to_dict()}

    @router.post("/tldrs/batch-generate")
    async def batch_generate_tldrs(payload: dict[str, Any] | None = None):
        """Batch generate TL;DRs for favorited articles.

        Request body (optional):
        - only_favorited: only generate for favorited articles (default true)
        - limit: maximum number to generate (default 20)
        - force: regenerate even if exists (default false)
        """
        from openbiliclaw.self_evolution.tldr import TLDRGenerator
        payload = payload or {}
        llm_service = llm_service
        generator = TLDRGenerator(db_path, llm_service=llm_service)
        try:
            results = generator.batch_generate(
                only_favorited=payload.get("only_favorited", True),
                limit=int(payload.get("limit", 20)),
                force=payload.get("force", False),
            )
            return {"status": "ok", "generated": len(results), "tldrs": [t.to_dict() for t in results]}
        except Exception as e:
            logger.exception("批量生成 TL;DR 失败")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.delete("/tldrs/{article_id}")
    async def delete_tldr(article_id: int):
        """Delete a cached TL;DR."""
        from openbiliclaw.self_evolution.tldr import TLDRGenerator
        llm_service = llm_service
        generator = TLDRGenerator(db_path, llm_service=llm_service)
        ok = generator.delete_tldr(article_id)
        if not ok:
            return JSONResponse({"error": "tldr not found"}, status_code=404)
        return {"status": "ok"}

    # ─── Content Insights (Knowledge Gaps + Cross-Platform) ───────────

    @router.post("/insights/generate")
    async def generate_insights(payload: dict[str, Any] | None = None):
        """Generate content insights report (knowledge gaps + cross-platform insights).

        Request body (optional):
        - min_articles_per_topic: minimum articles per topic (default 3)
        - max_gaps: maximum knowledge gaps (default 15)
        - max_cross_platform: maximum cross-platform insights (default 10)
        """
        from openbiliclaw.self_evolution.insights import ContentInsightsAnalyzer
        payload = payload or {}
        llm_service = llm_service
        analyzer = ContentInsightsAnalyzer(db_path, llm_service=llm_service)
        try:
            report = analyzer.generate_report(
                min_articles_per_topic=int(payload.get("min_articles_per_topic", 3)),
                max_gaps=int(payload.get("max_gaps", 15)),
                max_cross_platform=int(payload.get("max_cross_platform", 10)),
            )
            return {"status": "ok", "report": report.to_dict()}
        except Exception as e:
            logger.exception("生成内容洞察报告失败")
            return JSONResponse({"error": str(e)}, status_code=500)

    @router.get("/insights/latest")
    async def get_latest_insights():
        """Get the latest content insights report."""
        from openbiliclaw.self_evolution.insights import ContentInsightsAnalyzer
        llm_service = llm_service
        analyzer = ContentInsightsAnalyzer(db_path, llm_service=llm_service)
        report = analyzer.get_latest_report()
        if report is None:
            return JSONResponse({"error": "no report found, generate one first"}, status_code=404)
        return {"report": report.to_dict()}

    # ─── Reading Schedule (智能阅读调度) ─────────────────────────────

    @router.get("/reading-schedule/stats")
    async def reading_schedule_stats():
        """Get reading schedule statistics."""
        from openbiliclaw.self_evolution.reading_schedule import ReadingScheduler
        scheduler = ReadingScheduler(db_path)
        return scheduler.get_stats()

    @router.get("/reading-schedule/daily")
    async def reading_schedule_daily(limit: int = 20, include_new: bool = True, new_count: int = 5):
        """Get today's reading queue.

        Args:
            limit: Maximum number of articles in queue.
            include_new: Whether to include new articles.
            new_count: Maximum number of new articles.
        """
        from openbiliclaw.self_evolution.reading_schedule import ReadingScheduler
        scheduler = ReadingScheduler(db_path)
        queue = scheduler.get_daily_queue(limit=limit, include_new=include_new, new_count=new_count)
        return {"queue": [item.to_dict() for item in queue], "count": len(queue)}

    @router.get("/reading-schedule/{article_id}")
    async def reading_schedule_article(article_id: int):
        """Get reading schedule for a specific article."""
        from openbiliclaw.self_evolution.reading_schedule import ReadingScheduler
        scheduler = ReadingScheduler(db_path)
        item = scheduler.get_article_schedule(article_id)
        if item is None:
            return JSONResponse({"error": "article not found in reading schedule"}, status_code=404)
        return item.to_dict()

    @router.post("/reading-schedule/{article_id}/review")
    async def reading_schedule_review(article_id: int, payload: dict[str, Any] | None = None):
        """Submit reading feedback for an article.

        Request body:
        - rating: Review rating (again/hard/good/easy)
        - reading_percent: Reading progress percentage (0-100), optional
        """
        from openbiliclaw.self_evolution.reading_schedule import ReadingScheduler, ReviewRating
        payload = payload or {}
        rating_str = payload.get("rating", "good")
        try:
            rating = ReviewRating(rating_str)
        except ValueError:
            return JSONResponse({"error": f"invalid rating: {rating_str}, must be one of: again/hard/good/easy"}, status_code=400)

        reading_percent = payload.get("reading_percent")
        if reading_percent is not None:
            reading_percent = float(reading_percent)

        scheduler = ReadingScheduler(db_path)
        item = scheduler.review_article(article_id, rating, reading_percent=reading_percent)
        if item is None:
            return JSONResponse({"error": "article not found in reading schedule"}, status_code=404)
        return item.to_dict()

    @router.post("/reading-schedule/batch-register")
    async def reading_schedule_batch_register(payload: dict[str, Any] | None = None):
        """Batch register articles into reading schedule.

        Request body (optional):
        - limit: Maximum number of articles to register (default 1000)
        """
        from openbiliclaw.self_evolution.reading_schedule import ReadingScheduler
        payload = payload or {}
        limit = int(payload.get("limit", 1000))
        scheduler = ReadingScheduler(db_path)
        count = scheduler.batch_register_from_articles(limit=limit)
        return {"status": "ok", "registered": count}

    @router.get("/status")
    async def self_evolution_status():
        """Get self-evolution module status and stats."""
        import sqlite3
        conn = sqlite3.connect(db_path)
        stats = {}
        for table in ["insight_reports", "drift_reports", "topic_mining_reports",
                       "knowledge_cards", "knowledge_graph", "push_notifications",
                       "learning_paths", "article_tldrs", "content_insights_reports",
                       "reading_schedule", "article_snapshots"]:
            try:
                count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                stats[table] = count
            except Exception:
                stats[table] = 0
        conn.close()
        return {"status": "running", "stats": stats}

    # ─── Auto Topic Generator (自动专题生成引擎) ─────────────────────

    @router.get("/auto-topic/candidates")
    async def auto_topic_candidates(min_mentions: int = 50, limit: int = 10):
        """Discover candidate topics from knowledge graph.

        Args:
            min_mentions: Minimum entity mention count.
            limit: Maximum number of candidates to return.
        """
        from openbiliclaw.self_evolution.auto_topic_generator import AutoTopicGenerator
        generator = AutoTopicGenerator(db_path)
        candidates = generator.discover_candidates(min_mentions=min_mentions, limit=limit)
        return {
            "candidates": [
                {
                    "name": c.name,
                    "slug": c.slug,
                    "description": c.description,
                    "keywords": c.keywords,
                    "entity_mention_count": c.entity_mention_count,
                    "article_count": c.article_count,
                    "platforms": c.platforms,
                    "quality_score": c.quality_score,
                    "related_entities": c.related_entities,
                }
                for c in candidates
            ],
            "count": len(candidates),
        }

    @router.post("/auto-topic/generate")
    async def auto_topic_generate(payload: dict[str, Any] | None = None):
        """Generate a topic from a candidate.

        Request body:
        - name: Topic name (required if no candidate)
        - slug: Topic slug (optional)
        - keywords: List of search keywords (required if no candidate)
        - description: Topic description (optional)
        - max_articles: Maximum articles to include (default 50)
        - use_llm: Whether to use LLM for summary generation (default true)
        """
        from openbiliclaw.self_evolution.auto_topic_generator import (
            AutoTopicGenerator,
            TopicCandidate,
        )
        payload = payload or {}

        # 从候选主题生成，或从请求参数创建
        if "name" in payload and "keywords" in payload:
            candidate = TopicCandidate(
                name=payload["name"],
                slug=payload.get("slug") or payload["name"].lower().replace(" ", "-"),
                description=payload.get("description", f"关于{payload['name']}的跨平台综合专题"),
                keywords=payload["keywords"],
                entity_mention_count=payload.get("entity_mention_count", 0),
            )
        else:
            return JSONResponse({"error": "name and keywords are required"}, status_code=400)

        max_articles = int(payload.get("max_articles", 50))
        use_llm = bool(payload.get("use_llm", True))
        llm_service = llm_service if use_llm else None

        generator = AutoTopicGenerator(db_path, llm_service=llm_service)
        topic = generator.generate_topic(candidate, max_articles=max_articles, use_llm=use_llm)

        if topic is None:
            return JSONResponse({"error": "Failed to generate topic (no articles found)"}, status_code=404)

        return topic.to_dict()

    @router.post("/auto-topic/auto-generate")
    async def auto_topic_auto_generate(payload: dict[str, Any] | None = None):
        """Automatically discover and generate topics.

        Request body (optional):
        - min_mentions: Minimum entity mention count (default 50)
        - max_topics: Maximum topics to generate (default 3)
        - max_articles_per_topic: Maximum articles per topic (default 50)
        - use_llm: Whether to use LLM for summary (default true)
        """
        from openbiliclaw.self_evolution.auto_topic_generator import AutoTopicGenerator
        payload = payload or {}

        min_mentions = int(payload.get("min_mentions", 50))
        max_topics = int(payload.get("max_topics", 3))
        max_articles_per_topic = int(payload.get("max_articles_per_topic", 50))
        use_llm = bool(payload.get("use_llm", True))
        llm_service = llm_service if use_llm else None

        generator = AutoTopicGenerator(db_path, llm_service=llm_service)
        topics = generator.auto_generate(
            min_mentions=min_mentions,
            max_topics=max_topics,
            max_articles_per_topic=max_articles_per_topic,
            use_llm=use_llm,
        )

        return {
            "generated": [t.to_dict() for t in topics],
            "count": len(topics),
        }


    return router
