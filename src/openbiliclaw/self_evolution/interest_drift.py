"""Interest drift detection module.

Detects changes in user interests over time by comparing content
consumption patterns across different time windows.  Provides:
- Topic-level drift detection (rising / declining / stable)
- Platform-level drift detection
- Content-type drift detection
- New interest discovery (topics that appeared recently)
- Interest decay detection (topics that faded away)
- Drift alerts (significant changes worth notifying the user)
"""

from __future__ import annotations
from openbiliclaw.storage.database import open_db_conn

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from openbiliclaw.self_evolution.insight_report import (
    extract_topics,
    infer_platform_from_url,
)

logger = logging.getLogger("self_evolution.drift")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TopicDrift:
    """Drift measurement for a single topic."""

    topic: str
    current_count: int
    previous_count: int
    current_ratio: float  # current / total_current
    previous_ratio: float  # previous / total_previous
    change_ratio: float  # current_ratio / previous_ratio
    direction: str  # "rising" | "declining" | "stable" | "new" | "faded"
    significance: str  # "high" | "medium" | "low"
    z_score: float = 0.0  # statistical significance


@dataclass
class PlatformDrift:
    """Drift measurement for a single platform."""

    platform: str
    current_count: int
    previous_count: int
    change_pct: float
    direction: str


@dataclass
class DriftReport:
    """A complete interest drift analysis."""

    report_id: str
    current_start: str
    current_end: str
    previous_start: str
    previous_end: str
    generated_at: str
    topic_drifts: list[TopicDrift] = field(default_factory=list)
    platform_drifts: list[PlatformDrift] = field(default_factory=list)
    new_interests: list[str] = field(default_factory=list)
    fading_interests: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "current_start": self.current_start,
            "current_end": self.current_end,
            "previous_start": self.previous_start,
            "previous_end": self.previous_end,
            "generated_at": self.generated_at,
            "topic_drifts": [vars(d) for d in self.topic_drifts],
            "platform_drifts": [vars(d) for d in self.platform_drifts],
            "new_interests": self.new_interests,
            "fading_interests": self.fading_interests,
            "alerts": self.alerts,
            "summary": self.summary,
        }


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------


class InterestDriftDetector:
    """Detect interest drift by comparing two time windows.

    Args:
        db_path: Path to the SQLite database.

    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    def _get_conn(self) -> Any:
        import sqlite3

        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def detect(
        self,
        *,
        current_window_days: int = 7,
        previous_window_days: int = 30,
        end_date: datetime | None = None,
        min_count: int = 3,
    ) -> DriftReport:
        """Detect interest drift between current and previous windows.

        Args:
            current_window_days: Size of the current (recent) window in days.
            previous_window_days: Size of the previous (baseline) window in days.
            end_date: End of the current window (default: now).
            min_count: Minimum total count for a topic to be considered.

        Returns:
            A DriftReport with all drift measurements and alerts.

        """
        if end_date is None:
            end_date = datetime.now()

        current_start = end_date - timedelta(days=current_window_days)
        previous_end = current_start
        previous_start = previous_end - timedelta(days=previous_window_days)

        report_id = f"drift-{current_start.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"

        report = DriftReport(
            report_id=report_id,
            current_start=current_start.isoformat(),
            current_end=end_date.isoformat(),
            previous_start=previous_start.isoformat(),
            previous_end=previous_end.isoformat(),
            generated_at=datetime.now().isoformat(),
        )

        conn = self._get_conn()
        try:
            # Collect topic distributions for both windows
            current_topics = self._collect_topics(conn, current_start, end_date)
            previous_topics = self._collect_topics(conn, previous_start, previous_end)

            total_current = sum(current_topics.values())
            total_previous = sum(previous_topics.values())

            # Compute drift for each topic
            all_topics = set(current_topics.keys()) | set(previous_topics.keys())
            drifts: list[TopicDrift] = []

            for topic in all_topics:
                curr = current_topics.get(topic, 0)
                prev = previous_topics.get(topic, 0)

                if curr + prev < min_count:
                    continue

                curr_ratio = curr / max(total_current, 1)
                prev_ratio = prev / max(total_previous, 1)

                if prev == 0 and curr > 0:
                    direction = "new"
                    significance = "high" if curr >= 5 else "medium"
                    change_ratio = float("inf")
                elif curr == 0 and prev > 0:
                    direction = "faded"
                    significance = "high" if prev >= 5 else "medium"
                    change_ratio = 0.0
                else:
                    change_ratio = curr_ratio / max(prev_ratio, 0.001)
                    if change_ratio >= 1.5:
                        direction = "rising"
                        significance = "high" if change_ratio >= 2.0 else "medium"
                    elif change_ratio <= 0.5:
                        direction = "declining"
                        significance = "high" if change_ratio <= 0.3 else "medium"
                    else:
                        direction = "stable"
                        significance = "low"

                # Simple z-score approximation
                expected = total_current * (prev / max(total_previous, 1))
                variance = max(expected, 1.0)
                z_score = (curr - expected) / (variance**0.5) if variance > 0 else 0.0

                drifts.append(
                    TopicDrift(
                        topic=topic,
                        current_count=curr,
                        previous_count=prev,
                        current_ratio=curr_ratio,
                        previous_ratio=prev_ratio,
                        change_ratio=min(change_ratio, 10.0)
                        if change_ratio != float("inf")
                        else 10.0,
                        direction=direction,
                        significance=significance,
                        z_score=z_score,
                    )
                )

            report.topic_drifts = sorted(
                drifts,
                key=lambda d: (
                    0 if d.direction in ("new", "rising") else 1,
                    -abs(d.z_score),
                ),
            )

            # New and fading interests
            report.new_interests = [
                d.topic for d in drifts if d.direction == "new" and d.significance == "high"
            ][:10]
            report.fading_interests = [
                d.topic for d in drifts if d.direction == "faded" and d.significance == "high"
            ][:10]

            # Platform drift
            report.platform_drifts = self._detect_platform_drift(
                conn, current_start, end_date, previous_start, previous_end
            )

            # Generate alerts
            report.alerts = self._generate_alerts(report)

            # Generate summary
            report.summary = self._generate_summary(report)

        finally:
            conn.close()

        return report

    def _collect_topics(self, conn: Any, start: datetime, end: datetime) -> dict[str, int]:
        """Collect topic distribution for a time window."""
        rows = conn.execute(
            """
            SELECT e.title, a.tags, a.content_text
            FROM events.events e
            LEFT JOIN articles a ON a.url = e.url
            WHERE e.created_at >= ? AND e.created_at <= ?
              AND e.event_type IN ('view', 'favorite', 'like', 'click')
              AND e.title IS NOT NULL
            LIMIT 1000
            """,
            (start.isoformat(), end.isoformat()),
        ).fetchall()

        topics: dict[str, int] = {}
        for row in rows:
            text = " ".join(
                filter(
                    None, [row["title"] or "", row["tags"] or "", (row["content_text"] or "")[:300]]
                )
            )
            for topic in extract_topics(text, top_k=3):
                topics[topic] = topics.get(topic, 0) + 1

        return topics

    def _detect_platform_drift(
        self,
        conn: Any,
        curr_start: datetime,
        curr_end: datetime,
        prev_start: datetime,
        prev_end: datetime,
    ) -> list[PlatformDrift]:
        """Detect platform-level drift."""

        def get_platform_counts(start: datetime, end: datetime) -> dict[str, int]:
            rows = conn.execute(
                """
                SELECT url FROM events.events
                WHERE created_at >= ? AND created_at <= ?
                  AND event_type IN ('view', 'favorite', 'like')
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            counts: dict[str, int] = {}
            for row in rows:
                platform = infer_platform_from_url(row["url"] or "")
                counts[platform] = counts.get(platform, 0) + 1
            return counts

        curr = get_platform_counts(curr_start, curr_end)
        prev = get_platform_counts(prev_start, prev_end)

        all_platforms = set(curr.keys()) | set(prev.keys())
        drifts: list[PlatformDrift] = []

        for platform in all_platforms:
            c = curr.get(platform, 0)
            p = prev.get(platform, 0)
            if c + p < 5:
                continue
            change_pct = ((c - p) / max(p, 1)) * 100 if p > 0 else 100.0
            direction = (
                "rising" if change_pct > 20 else ("declining" if change_pct < -20 else "stable")
            )
            drifts.append(
                PlatformDrift(
                    platform=platform,
                    current_count=c,
                    previous_count=p,
                    change_pct=change_pct,
                    direction=direction,
                )
            )

        return sorted(drifts, key=lambda d: -abs(d.change_pct))

    def _generate_alerts(self, report: DriftReport) -> list[str]:
        """Generate human-readable alerts for significant drifts."""
        alerts: list[str] = []

        high_rising = [
            d
            for d in report.topic_drifts
            if d.direction in ("rising", "new") and d.significance == "high"
        ]
        if high_rising:
            topics = ", ".join([d.topic for d in high_rising[:5]])
            alerts.append(f"🔥 兴趣上升：{topics}")

        high_declining = [
            d
            for d in report.topic_drifts
            if d.direction in ("declining", "faded") and d.significance == "high"
        ]
        if high_declining:
            topics = ", ".join([d.topic for d in high_declining[:5]])
            alerts.append(f"📉 兴趣下降：{topics}")

        platform_shifts = [d for d in report.platform_drifts if d.direction != "stable"]
        if platform_shifts:
            shifts = ", ".join([f"{d.platform}({d.change_pct:+.0f}%)" for d in platform_shifts[:3]])
            alerts.append(f"📱 平台变化：{shifts}")

        return alerts

    def _generate_summary(self, report: DriftReport) -> str:
        """Generate a human-readable summary."""
        parts = []

        if report.new_interests:
            parts.append(f"发现新兴趣：{', '.join(report.new_interests[:3])}")

        rising = [
            d.topic
            for d in report.topic_drifts
            if d.direction == "rising" and d.significance == "high"
        ]
        if rising:
            parts.append(f"兴趣上升：{', '.join(rising[:3])}")

        if report.fading_interests:
            parts.append(f"兴趣消退：{', '.join(report.fading_interests[:3])}")

        if not parts:
            parts.append("近期兴趣相对稳定，没有显著变化")

        return "；".join(parts)

    def save_report(self, report: DriftReport) -> None:
        """Save drift report to database."""
        import json

        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS drift_reports (
                    report_id TEXT PRIMARY KEY,
                    current_start TEXT,
                    current_end TEXT,
                    previous_start TEXT,
                    previous_end TEXT,
                    generated_at TEXT,
                    report_json TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO drift_reports
                (report_id, current_start, current_end, previous_start, previous_end, generated_at, report_json)  # noqa: E501
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.report_id,
                    report.current_start,
                    report.current_end,
                    report.previous_start,
                    report.previous_end,
                    report.generated_at,
                    json.dumps(report.to_dict(), ensure_ascii=False),
                ),
            )
            conn.commit()
        finally:
            conn.close()
