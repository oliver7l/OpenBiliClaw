"""Automatic topic mining module.

Discovers emerging topics from the content library and suggests
new topic collections.  Provides:
- Emerging topic detection (sudden increase in content volume)
- Topic clustering (group related content into candidate topics)
- Topic quality scoring (is this topic worth a dedicated collection?)
- Automatic topic creation (create topic collections for high-signal topics)
- Topic growth tracking (monitor how topics evolve over time)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from openbiliclaw.self_evolution.insight_report import extract_topics

logger = logging.getLogger("self_evolution.topic_miner")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TopicCandidate:
    """A candidate topic discovered by the miner."""

    topic_name: str
    topic_slug: str
    content_count: int
    recent_count: int  # content in the last N days
    growth_ratio: float  # recent / previous
    avg_quality: float
    sample_titles: list[str] = field(default_factory=list)
    related_topics: list[str] = field(default_factory=list)
    score: float = 0.0
    recommendation: str = ""  # "create" | "watch" | "skip"


@dataclass
class MiningReport:
    """A complete topic mining report."""

    report_id: str
    generated_at: str
    window_days: int
    candidates: list[TopicCandidate] = field(default_factory=list)
    created_topics: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "generated_at": self.generated_at,
            "window_days": self.window_days,
            "candidates": [vars(c) for c in self.candidates],
            "created_topics": self.created_topics,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Miner
# ---------------------------------------------------------------------------


class TopicMiner:
    """Mine emerging topics from the content library.

    Args:
        db_path: Path to the SQLite database.

    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _get_conn(self) -> Any:
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def mine(
        self,
        *,
        window_days: int = 14,
        min_content_count: int = 5,
        min_growth_ratio: float = 1.5,
        auto_create: bool = False,
        auto_create_threshold: float = 0.7,
    ) -> MiningReport:
        """Mine for emerging topics.

        Args:
            window_days: Number of days to look back for recent content.
            min_content_count: Minimum total content for a topic to be considered.
            min_growth_ratio: Minimum growth ratio to flag as emerging.
            auto_create: Whether to automatically create topics for high-score candidates.
            auto_create_threshold: Minimum score for auto-creation.

        Returns:
            A MiningReport with all candidates and actions.

        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=window_days)
        mid_date = end_date - timedelta(days=window_days // 2)

        report_id = f"topic-mining-{end_date.strftime('%Y%m%d')}"

        report = MiningReport(
            report_id=report_id,
            generated_at=datetime.now().isoformat(),
            window_days=window_days,
        )

        conn = self._get_conn()
        try:
            # Collect topic distributions for recent and previous halves
            recent_topics = self._collect_topics_with_content(conn, mid_date, end_date)
            previous_topics = self._collect_topics_with_content(conn, start_date, mid_date)

            all_topics = set(recent_topics.keys()) | set(previous_topics.keys())

            candidates: list[TopicCandidate] = []

            for topic in all_topics:
                recent = recent_topics.get(topic, [])
                previous = previous_topics.get(topic, [])
                total = len(recent) + len(previous)

                if total < min_content_count:
                    continue

                growth_ratio = len(recent) / max(len(previous), 1)

                # Calculate average quality
                all_content = recent + previous
                qualities = [
                    c.get("quality_score", 0) or 0 for c in all_content if c.get("quality_score")
                ]
                avg_quality = sum(qualities) / len(qualities) if qualities else 0.5

                # Sample titles
                sample_titles = [(c.get("title") or "")[:80] for c in recent[:5] if c.get("title")]

                # Score the candidate
                score = self._score_candidate(
                    topic=topic,
                    total_count=total,
                    recent_count=len(recent),
                    growth_ratio=growth_ratio,
                    avg_quality=avg_quality,
                )

                # Recommendation
                if score >= auto_create_threshold and growth_ratio >= min_growth_ratio:
                    recommendation = "create"
                elif score >= 0.4 or growth_ratio >= min_growth_ratio:
                    recommendation = "watch"
                else:
                    recommendation = "skip"

                # Related topics (from co-occurrence)
                related = self._find_related_topics(topic, all_content)

                candidates.append(
                    TopicCandidate(
                        topic_name=topic,
                        topic_slug=self._slugify(topic),
                        content_count=total,
                        recent_count=len(recent),
                        growth_ratio=growth_ratio,
                        avg_quality=avg_quality,
                        sample_titles=sample_titles,
                        related_topics=related,
                        score=score,
                        recommendation=recommendation,
                    )
                )

            report.candidates = sorted(candidates, key=lambda c: -c.score)

            # Auto-create topics if enabled
            if auto_create:
                for candidate in report.candidates:
                    if (
                        candidate.recommendation == "create"
                        and candidate.score >= auto_create_threshold
                    ):
                        created = self._create_topic(conn, candidate)
                        if created:
                            report.created_topics.append(candidate.topic_name)

            # Generate summary
            report.summary = self._generate_summary(report)

        finally:
            conn.close()

        return report

    def _collect_topics_with_content(
        self, conn: Any, start: datetime, end: datetime
    ) -> dict[str, list[dict[str, Any]]]:
        """Collect topics with their associated content for a time window."""
        rows = conn.execute(
            """
            SELECT id, title, url, source_type, tags, ai_summary, created_at
            FROM articles
            WHERE created_at >= ? AND created_at <= ?
              AND title IS NOT NULL AND length(title) > 0
            ORDER BY created_at DESC
            LIMIT 2000
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        topics: dict[str, list[dict[str, Any]]] = {}

        for row in rows:
            text = " ".join(
                filter(None, [row["title"] or "", row["tags"] or "", row["ai_summary"] or ""])
            )
            extracted = extract_topics(text, top_k=3)

            content = {
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "source_type": row["source_type"],
                "tags": row["tags"],
                "quality_score": 0.5,  # Default, no quality_score column
            }

            for topic in extracted:
                if topic not in topics:
                    topics[topic] = []
                topics[topic].append(content)

        return topics

    def _score_candidate(
        self,
        *,
        topic: str,
        total_count: int,
        recent_count: int,
        growth_ratio: float,
        avg_quality: float,
    ) -> float:
        """Score a topic candidate from 0 to 1."""
        # Volume score (more content = higher, with diminishing returns)
        volume_score = min(total_count / 20.0, 1.0) * 0.3

        # Growth score (faster growth = higher)
        growth_score = min(growth_ratio / 3.0, 1.0) * 0.3

        # Quality score
        quality_score = min(avg_quality, 1.0) * 0.2

        # Recency score (more recent content = higher)
        recency_score = min(recent_count / max(total_count, 1), 1.0) * 0.2

        return volume_score + growth_score + quality_score + recency_score

    def _find_related_topics(self, topic: str, content_list: list[dict[str, Any]]) -> list[str]:
        """Find topics that frequently co-occur with the given topic."""
        co_occurrence: dict[str, int] = {}

        for content in content_list:
            text = " ".join(filter(None, [content.get("title") or "", content.get("tags") or ""]))
            related = extract_topics(text, top_k=5)
            for r in related:
                if r != topic:
                    co_occurrence[r] = co_occurrence.get(r, 0) + 1

        return [t for t, _ in sorted(co_occurrence.items(), key=lambda x: -x[1])[:5]]

    def _slugify(self, text: str) -> str:
        """Convert topic name to a URL-safe slug."""
        # For Chinese, just use the text directly (URL-encoded by the framework)
        slug = re.sub(r"[^\w\u4e00-\u9fff-]", "-", text.lower())
        slug = re.sub(r"-+", "-", slug).strip("-")
        return slug or "topic"

    def _create_topic(self, conn: Any, candidate: TopicCandidate) -> bool:
        """Create a new topic collection in the database."""
        try:
            # Check if topic already exists
            existing = conn.execute(
                "SELECT id FROM topics WHERE slug = ? OR name = ?",
                (candidate.topic_slug, candidate.topic_name),
            ).fetchone()

            if existing:
                logger.info("Topic already exists: %s", candidate.topic_name)
                return False

            # Create topic
            conn.execute(
                """
                INSERT INTO topics (name, slug, description, created_at, auto_generated)
                VALUES (?, ?, ?, ?, 1)
                """,
                (
                    candidate.topic_name,
                    candidate.topic_slug,
                    f"自动发现的专题：{candidate.topic_name}（{candidate.content_count}条相关内容，增长率{candidate.growth_ratio:.1f}x）",
                    datetime.now().isoformat(),
                ),
            )
            topic_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

            # Add related content to topic
            # (This would need a topic_articles junction table)
            logger.info(
                "Created topic %s (id=%d) with %d related content",
                candidate.topic_name,
                topic_id,
                candidate.content_count,
            )

            conn.commit()
            return True

        except Exception:
            logger.exception("Failed to create topic: %s", candidate.topic_name)
            conn.rollback()
            return False

    def _generate_summary(self, report: MiningReport) -> str:
        """Generate a human-readable summary."""
        parts = []

        create_candidates = [c for c in report.candidates if c.recommendation == "create"]
        watch_candidates = [c for c in report.candidates if c.recommendation == "watch"]

        if create_candidates:
            topics = ", ".join([c.topic_name for c in create_candidates[:5]])
            parts.append(f"建议创建专题：{topics}")

        if watch_candidates:
            topics = ", ".join([c.topic_name for c in watch_candidates[:5]])
            parts.append(f"值得关注：{topics}")

        if report.created_topics:
            parts.append(f"已自动创建：{', '.join(report.created_topics)}")

        if not parts:
            parts.append("没有发现显著的新兴主题")

        return "；".join(parts)

    def save_report(self, report: MiningReport) -> None:
        """Save mining report to database."""
        import json

        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS topic_mining_reports (
                    report_id TEXT PRIMARY KEY,
                    generated_at TEXT,
                    window_days INTEGER,
                    report_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO topic_mining_reports
                (report_id, generated_at, window_days, report_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.generated_at,
                    report.window_days,
                    json.dumps(report.to_dict(), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
