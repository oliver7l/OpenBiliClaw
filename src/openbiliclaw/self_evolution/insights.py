"""Content insights module: knowledge gap analysis and cross-platform insights.

Identifies:
1. Knowledge gaps: topics with many collected articles but low reading completion
2. Cross-platform insights: topics covered across multiple platforms with complementary perspectives

Inspired by Agent-SaveMark's knowledge gap and cross-platform analysis features.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("self_evolution.insights")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeGap:
    """A topic where the user has collected content but hasn't deeply engaged."""

    topic: str
    article_count: int
    avg_reading_percent: float
    favorited_count: int
    high_quality_articles: list[dict[str, Any]] = field(default_factory=list)
    gap_score: float = 0.0  # Higher = bigger gap
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "article_count": self.article_count,
            "avg_reading_percent": round(self.avg_reading_percent, 1),
            "favorited_count": self.favorited_count,
            "gap_score": round(self.gap_score, 2),
            "suggestion": self.suggestion,
            "high_quality_articles": self.high_quality_articles[:5],
        }


@dataclass
class CrossPlatformInsight:
    """A topic covered across multiple platforms with complementary perspectives."""

    topic: str
    platform_counts: dict[str, int] = field(default_factory=dict)
    total_articles: int = 0
    platform_articles: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    insight: str = ""
    complementary_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "platform_counts": self.platform_counts,
            "total_articles": self.total_articles,
            "complementary_score": round(self.complementary_score, 2),
            "insight": self.insight,
            "platform_articles": {p: arts[:3] for p, arts in self.platform_articles.items()},
        }


@dataclass
class InsightsReport:
    """A combined report of knowledge gaps and cross-platform insights."""

    generated_at: str = ""
    knowledge_gaps: list[KnowledgeGap] = field(default_factory=list)
    cross_platform_insights: list[CrossPlatformInsight] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "summary": self.summary,
            "knowledge_gaps": [g.to_dict() for g in self.knowledge_gaps],
            "cross_platform_insights": [i.to_dict() for i in self.cross_platform_insights],
        }


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------


class ContentInsightsAnalyzer:
    """Analyze content library for knowledge gaps and cross-platform insights.

    Args:
        db_path: Path to the SQLite database.
        llm_service: Optional LLM service for generating natural-language insights.

    """

    def __init__(self, db_path: str, *, llm_service: Any | None = None) -> None:
        self.db_path = db_path
        self.llm_service = llm_service

    def _get_conn(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate_report(
        self,
        *,
        min_articles_per_topic: int = 3,
        max_gaps: int = 15,
        max_cross_platform: int = 10,
    ) -> InsightsReport:
        """Generate a full insights report.

        Args:
            min_articles_per_topic: Minimum articles for a topic to be considered.
            max_gaps: Maximum knowledge gaps to return.
            max_cross_platform: Maximum cross-platform insights to return.

        Returns:
            An InsightsReport with knowledge gaps and cross-platform insights.

        """
        report = InsightsReport(generated_at=datetime.now().isoformat())

        # Analyze knowledge gaps
        report.knowledge_gaps = self._analyze_knowledge_gaps(
            min_articles=min_articles_per_topic,
            max_results=max_gaps,
        )

        # Analyze cross-platform insights
        report.cross_platform_insights = self._analyze_cross_platform(
            min_articles=min_articles_per_topic,
            max_results=max_cross_platform,
        )

        # Generate summary
        report.summary = self._generate_summary(report)

        # Save report
        self._save_report(report)

        logger.info(
            "Generated insights report: %d gaps, %d cross-platform insights",
            len(report.knowledge_gaps),
            len(report.cross_platform_insights),
        )

        return report

    def get_latest_report(self) -> InsightsReport | None:
        """Get the most recent saved insights report."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            row = conn.execute(
                "SELECT report_json FROM content_insights_reports ORDER BY generated_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            data = json.loads(row["report_json"])
            return self._dict_to_report(data)
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Knowledge Gap Analysis
    # -----------------------------------------------------------------------

    def _analyze_knowledge_gaps(
        self, *, min_articles: int = 3, max_results: int = 15
    ) -> list[KnowledgeGap]:
        """Identify topics with many articles but low reading engagement.

        Gap score = article_count * (1 - avg_reading_percent/100) * (1 - favorited_ratio)
        Higher score = bigger gap (collected but not deeply engaged).
        """
        conn = self._get_conn()
        try:
            # Get all articles with tags and reading data
            rows = conn.execute(
                """
                SELECT id, title, url, source_type, tags, reading_percent,
                       favorited, ai_summary, length(content_text) as content_length
                FROM articles
                WHERE tags IS NOT NULL AND tags != ''
                  AND content_text IS NOT NULL AND length(content_text) > 100
                """
            ).fetchall()
        finally:
            conn.close()

        # Group by topic (parse tags)
        topic_articles: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            tags = self._parse_tags(row["tags"])
            for tag in tags:
                if len(tag) >= 2:  # Skip single-char tags
                    topic_articles[tag].append(dict(row))

        gaps: list[KnowledgeGap] = []
        for topic, articles in topic_articles.items():
            if len(articles) < min_articles:
                continue

            avg_reading = sum((a.get("reading_percent") or 0) for a in articles) / len(articles)
            favorited_count = sum(1 for a in articles if a.get("favorited", 0) > 0)
            favorited_ratio = favorited_count / len(articles) if articles else 0

            # Gap score: more articles + lower reading + fewer favorites = bigger gap
            gap_score = (
                len(articles) * 0.3 + (100 - avg_reading) * 0.4 + (1 - favorited_ratio) * 100 * 0.3
            )

            # Find high-quality articles to suggest for deep reading
            high_quality = sorted(
                articles,
                key=lambda a: (
                    (a.get("content_length") or 0),
                    (a.get("reading_percent") or 0),
                ),
                reverse=True,
            )[:5]

            # Generate suggestion
            if avg_reading < 30:
                suggestion = (
                    f"收集了{len(articles)}篇但平均只读了{avg_reading:.0f}%，建议深读高质量长文"
                )
            elif favorited_count == 0:
                suggestion = f"{len(articles)}篇内容无一收藏，建议筛选出最有价值的收藏"
            else:
                suggestion = f"有{len(articles)}篇内容，可系统整理成学习路径"

            gaps.append(
                KnowledgeGap(
                    topic=topic,
                    article_count=len(articles),
                    avg_reading_percent=avg_reading,
                    favorited_count=favorited_count,
                    high_quality_articles=[
                        {
                            "id": a["id"],
                            "title": a["title"][:80],
                            "url": a["url"],
                            "source_type": a["source_type"],
                            "reading_percent": a.get("reading_percent") or 0,
                            "content_length": a.get("content_length") or 0,
                        }
                        for a in high_quality
                    ],
                    gap_score=gap_score,
                    suggestion=suggestion,
                )
            )

        # Sort by gap score descending
        gaps.sort(key=lambda g: -g.gap_score)
        return gaps[:max_results]

    # -----------------------------------------------------------------------
    # Cross-Platform Insights
    # -----------------------------------------------------------------------

    def _analyze_cross_platform(
        self, *, min_articles: int = 3, max_results: int = 10
    ) -> list[CrossPlatformInsight]:
        """Identify topics covered across multiple platforms with complementary perspectives.

        Complementary score: number of distinct platforms * evenness of distribution.
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, title, url, source_type, tags, reading_percent,
                       ai_summary, created_at
                FROM articles
                WHERE tags IS NOT NULL AND tags != ''
                  AND content_text IS NOT NULL AND length(content_text) > 100
                """
            ).fetchall()
        finally:
            conn.close()

        # Group by topic, then by platform
        topic_platform_articles: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in rows:
            tags = self._parse_tags(row["tags"])
            for tag in tags:
                if len(tag) >= 2:
                    topic_platform_articles[tag][row["source_type"]].append(dict(row))

        insights: list[CrossPlatformInsight] = []
        for topic, platform_articles in topic_platform_articles.items():
            total = sum(len(arts) for arts in platform_articles.values())
            if total < min_articles:
                continue

            platform_counts = {p: len(arts) for p, arts in platform_articles.items()}
            num_platforms = len(platform_counts)

            if num_platforms < 2:
                continue  # Need at least 2 platforms for cross-platform insight

            # Complementary score: more platforms + more even distribution = higher
            max_count = max(platform_counts.values())
            min_count = min(platform_counts.values())
            evenness = min_count / max_count if max_count > 0 else 0
            complementary_score = num_platforms * 10 + evenness * 20

            # Generate insight text
            platform_list = list(platform_counts.keys())
            if num_platforms == 2:
                insight = (
                    f"「{topic}」在{platform_list[0]}({platform_counts[platform_list[0]]}篇)"
                    f"和{platform_list[1]}({platform_counts[platform_list[1]]}篇)"
                    f"都有内容，可对比不同平台视角"
                )
            else:
                top_platforms = sorted(platform_counts.items(), key=lambda x: -x[1])[:3]
                platform_str = "、".join(f"{p}({c}篇)" for p, c in top_platforms)
                insight = f"「{topic}」横跨{num_platforms}个平台（{platform_str}），多视角互补"

            # Get representative articles per platform
            platform_repr = {}
            for p, arts in platform_articles.items():
                sorted_arts = sorted(
                    arts,
                    key=lambda a: (a.get("reading_percent") or 0, a.get("created_at") or ""),
                    reverse=True,
                )
                platform_repr[p] = [
                    {
                        "id": a["id"],
                        "title": a["title"][:80],
                        "url": a["url"],
                        "reading_percent": a.get("reading_percent") or 0,
                    }
                    for a in sorted_arts[:3]
                ]

            insights.append(
                CrossPlatformInsight(
                    topic=topic,
                    platform_counts=platform_counts,
                    total_articles=total,
                    platform_articles=platform_repr,
                    insight=insight,
                    complementary_score=complementary_score,
                )
            )

        # Sort by complementary score descending
        insights.sort(key=lambda i: -i.complementary_score)
        return insights[:max_results]

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _parse_tags(self, tags_str: str | None) -> list[str]:
        """Parse tags from various formats (JSON array, comma-separated, etc.)."""
        if not tags_str:
            return []

        tags_str = tags_str.strip()
        if not tags_str:
            return []

        # Try JSON array first
        try:
            parsed = json.loads(tags_str)
            if isinstance(parsed, list):
                return [str(t).strip() for t in parsed if str(t).strip()]
        except (json.JSONDecodeError, TypeError):
            pass

        # Fallback: comma/semicolon/space separated
        import re

        tags = re.split(r"[,，;；\s]+", tags_str)
        return [t.strip() for t in tags if t.strip() and len(t.strip()) >= 2]

    def _generate_summary(self, report: InsightsReport) -> str:
        """Generate a natural-language summary of the report."""
        gaps = report.knowledge_gaps
        insights = report.cross_platform_insights

        parts = []
        if gaps:
            top_gap = gaps[0]
            parts.append(
                f"发现{len(gaps)}个知识缺口，最大的缺口是「{top_gap.topic}」"
                f"（{top_gap.article_count}篇，平均阅读{top_gap.avg_reading_percent:.0f}%）"
            )
        if insights:
            top_insight = insights[0]
            parts.append(
                f"发现{len(insights)}个跨平台主题，最值得关注的是「{top_insight.topic}」"
                f"（横跨{len(top_insight.platform_counts)}个平台）"
            )

        if not parts:
            return "内容库分析完成，未发现显著的知识缺口或跨平台主题"

        return "；".join(parts)

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS content_insights_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                generated_at TEXT,
                report_json TEXT
            )
            """
        )
        conn.commit()

    def _save_report(self, report: InsightsReport) -> None:
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            conn.execute(
                "INSERT INTO content_insights_reports (generated_at, report_json) VALUES (?, ?)",
                (report.generated_at, json.dumps(report.to_dict(), ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

    def _dict_to_report(self, data: dict[str, Any]) -> InsightsReport:
        report = InsightsReport(
            generated_at=data.get("generated_at", ""),
            summary=data.get("summary", ""),
        )
        for g in data.get("knowledge_gaps", []):
            report.knowledge_gaps.append(
                KnowledgeGap(
                    topic=g["topic"],
                    article_count=g["article_count"],
                    avg_reading_percent=g["avg_reading_percent"],
                    favorited_count=g["favorited_count"],
                    high_quality_articles=g.get("high_quality_articles", []),
                    gap_score=g["gap_score"],
                    suggestion=g.get("suggestion", ""),
                )
            )
        for i in data.get("cross_platform_insights", []):
            report.cross_platform_insights.append(
                CrossPlatformInsight(
                    topic=i["topic"],
                    platform_counts=i.get("platform_counts", {}),
                    total_articles=i.get("total_articles", 0),
                    platform_articles=i.get("platform_articles", {}),
                    insight=i.get("insight", ""),
                    complementary_score=i.get("complementary_score", 0),
                )
            )
        return report
