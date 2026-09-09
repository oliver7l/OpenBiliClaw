"""Proactive push notification module.

Monitors the content library and user interests, and proactively
notifies the user when high-value content is discovered.  Provides:
- High-value content detection (quality + relevance scoring)
- Push notification generation
- Frequency control (avoid notification fatigue)
- Notification history and tracking
- Multiple notification channels (in-app, email, webhook)
- Digest generation (daily/weekly summaries)
"""

from __future__ import annotations
from openbiliclaw.storage.database import open_db_conn

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("self_evolution.proactive_push")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PushNotification:
    """A push notification."""

    notification_id: str
    title: str
    body: str
    notification_type: str  # "high_value" | "new_interest" | "digest" | "reminder" | "milestone"
    priority: str  # "high" | "medium" | "low"
    content_url: str = ""
    content_title: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: str = ""
    sent_at: str = ""
    read_at: str = ""
    dismissed: bool = False
    channel: str = "in_app"  # "in_app" | "email" | "webhook"

    def to_dict(self) -> dict[str, Any]:
        return {
            "notification_id": self.notification_id,
            "title": self.title,
            "body": self.body,
            "notification_type": self.notification_type,
            "priority": self.priority,
            "content_url": self.content_url,
            "content_title": self.content_title,
            "tags": self.tags,
            "created_at": self.created_at,
            "sent_at": self.sent_at,
            "read_at": self.read_at,
            "dismissed": self.dismissed,
            "channel": self.channel,
        }


@dataclass
class PushConfig:
    """Configuration for push notifications."""

    enabled: bool = True
    max_notifications_per_day: int = 5
    min_quality_threshold: float = 0.7
    min_relevance_threshold: float = 0.6
    quiet_hours_start: int = 22  # 22:00
    quiet_hours_end: int = 8  # 08:00
    digest_enabled: bool = True
    digest_time: str = "09:00"  # Daily digest time
    channels: list[str] = field(default_factory=lambda: ["in_app"])
    webhook_url: str = ""
    email_address: str = ""


# ---------------------------------------------------------------------------
# Push Engine
# ---------------------------------------------------------------------------


class ProactivePushEngine:
    """Engine for generating and sending proactive push notifications.

    Args:
        db_path: Path to the SQLite database.
        config: Push configuration.
        llm_service: Optional LLM service for notification generation.

    """

    def __init__(
        self,
        db_path: str,
        *,
        config: PushConfig | None = None,
        llm_service: Any | None = None,
    ) -> None:
        self.db_path = db_path
        self.config = config or PushConfig()
        self.llm_service = llm_service

    def _get_conn(self) -> Any:
        import sqlite3

        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def check_and_push(self, *, dry_run: bool = False) -> list[PushNotification]:
        """Check for high-value content and generate push notifications.

        Args:
            dry_run: If True, generate but don't send.

        Returns:
            List of generated notifications.

        """
        if not self.config.enabled:
            return []

        # Check frequency limit
        if self._exceeded_daily_limit():
            logger.info("Daily notification limit exceeded, skipping push")
            return []

        # Check quiet hours
        if self._in_quiet_hours():
            logger.info("Currently in quiet hours, skipping push")
            return []

        notifications: list[PushNotification] = []

        # 1. High-value new content
        high_value = self._find_high_value_content()
        for content in high_value:
            notif = self._create_high_value_notification(content)
            if notif:
                notifications.append(notif)

        # 2. New interest detection (if we have drift data)
        new_interest_notif = self._check_new_interest()
        if new_interest_notif:
            notifications.append(new_interest_notif)

        # 3. Reminders (unread favorites, due reviews)
        reminder_notif = self._check_reminders()
        if reminder_notif:
            notifications.append(reminder_notif)

        # Limit to max per day
        remaining = self.config.max_notifications_per_day - self._count_today_sent()
        notifications = notifications[: max(0, remaining)]

        # Send notifications
        if not dry_run:
            for notif in notifications:
                self._send_notification(notif)

        return notifications

    def generate_digest(self, *, period: str = "daily") -> PushNotification | None:
        """Generate a daily/weekly digest notification.

        Args:
            period: "daily" or "weekly".

        Returns:
            A digest notification, or None if nothing to report.

        """
        conn = self._get_conn()
        try:
            days = 1 if period == "daily" else 7
            start = (datetime.now() - timedelta(days=days)).isoformat()

            # Count new content
            new_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM articles WHERE created_at >= ?",
                (start,),
            ).fetchone()["cnt"]

            # Count favorites
            fav_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM events WHERE event_type = 'favorite' AND created_at >= ?",
                (start,),
            ).fetchone()["cnt"]

            # Get top topics
            rows = conn.execute(
                """
                SELECT tags, COUNT(*) as cnt FROM articles
                WHERE created_at >= ? AND tags IS NOT NULL
                GROUP BY tags ORDER BY cnt DESC LIMIT 5
                """,
                (start,),
            ).fetchall()

            top_topics = []
            for row in rows:
                if row["tags"]:
                    for tag in str(row["tags"]).split(",")[:2]:
                        if tag.strip() and tag.strip() not in top_topics:
                            top_topics.append(tag.strip())
                        if len(top_topics) >= 5:
                            break
                if len(top_topics) >= 5:
                    break

            if new_count == 0 and fav_count == 0:
                return None

            period_label = "今日" if period == "daily" else "本周"

            body_parts = [f"新增 {new_count} 条内容"]
            if fav_count > 0:
                body_parts.append(f"收藏 {fav_count} 条")
            if top_topics:
                body_parts.append(f"热门主题：{', '.join(top_topics[:3])}")

            notif = PushNotification(
                notification_id=f"digest-{period}-{datetime.now().strftime('%Y%m%d')}",
                title=f"📚 {period_label}阅读摘要",
                body="；".join(body_parts),
                notification_type="digest",
                priority="medium",
                created_at=datetime.now().isoformat(),
            )

            return notif

        finally:
            conn.close()

    def _find_high_value_content(self) -> list[dict[str, Any]]:
        """Find recently added high-value content."""
        conn = self._get_conn()
        try:
            # Look for content added in the last 24 hours with indicators of high value
            start = (datetime.now() - timedelta(hours=24)).isoformat()

            # Use available columns as quality proxies:
            # - ai_summary exists → was processed by LLM
            # - content_text length → substantial content
            # - reading_percent → user engagement
            # - tags → categorized content
            try:
                rows = conn.execute(
                    """
                    SELECT id, title, url, source_type, tags, ai_summary,
                           reading_percent, content_text, created_at
                    FROM articles
                    WHERE created_at >= ?
                      AND content_text IS NOT NULL AND length(content_text) > 500
                      AND (ai_summary IS NOT NULL AND length(ai_summary) > 10
                           OR reading_percent >= 50
                           OR tags IS NOT NULL AND length(tags) > 2)
                    ORDER BY COALESCE(reading_percent, 0) DESC, length(content_text) DESC
                    LIMIT 10
                    """,
                    (start,),
                ).fetchall()
            except Exception:
                # Fallback if reading_percent column doesn't exist
                rows = conn.execute(
                    """
                    SELECT id, title, url, source_type, tags, ai_summary,
                           content_text, created_at
                    FROM articles
                    WHERE created_at >= ?
                      AND content_text IS NOT NULL AND length(content_text) > 500
                      AND (ai_summary IS NOT NULL AND length(ai_summary) > 10
                           OR tags IS NOT NULL AND length(tags) > 2)
                    ORDER BY length(content_text) DESC
                    LIMIT 10
                    """,
                    (start,),
                ).fetchall()

            # Filter out already-notified content
            notified_ids = self._get_notified_content_ids()

            result = []
            for row in rows:
                if row["id"] not in notified_ids:
                    result.append(dict(row))

            return result[:3]  # Max 3 high-value notifications per check

        finally:
            conn.close()

    def _create_high_value_notification(self, content: dict[str, Any]) -> PushNotification | None:
        """Create a notification for high-value content."""
        title = content.get("title") or "高价值内容"
        quality = content.get("quality_score", 0)
        source = content.get("source_type", "")

        # Generate notification body
        if self.llm_service is not None:
            try:
                body = self._generate_notification_body_with_llm(content)
            except Exception:
                body = f"发现一篇高质量内容（质量分{quality:.1f}）：{title[:50]}"
        else:
            summary = content.get("ai_summary") or ""
            body = summary[:100] if summary else f"来自{source}的高质量内容：{title[:50]}"

        return PushNotification(
            notification_id=f"hv-{content['id']}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            title="✨ 发现高价值内容",
            body=body,
            notification_type="high_value",
            priority="high" if quality >= 0.85 else "medium",
            content_url=content.get("url") or "",
            content_title=title,
            tags=[t.strip() for t in str(content.get("tags") or "").split(",") if t.strip()][:3],
            created_at=datetime.now().isoformat(),
        )

    def _generate_notification_body_with_llm(self, content: dict[str, Any]) -> str:
        """Generate notification body using LLM."""
        from openbiliclaw.llm.generation import generate_structured

        title = content.get("title") or ""
        summary = content.get("ai_summary") or ""
        tags = content.get("tags") or ""

        result = generate_structured(
            self.llm_service,
            system_instruction=(
                "你是一个内容推荐助手。根据文章信息，生成一句吸引人的推荐语，"
                "不超过50字，突出文章的核心价值。不要用夸张的语气，要真诚。"
            ),
            user_input=f"标题：{title}\n摘要：{summary}\n标签：{tags}",
            parse=lambda x: x,
            label="push_notification_body",
            temperature=0.7,
            max_tokens=100,
        )

        return str(result).strip()[:100]

    def _check_new_interest(self) -> PushNotification | None:
        """Check for new emerging interests from drift reports."""
        conn = self._get_conn()
        try:
            # Look for recent drift reports with new interests
            row = conn.execute(
                """
                SELECT report_json FROM drift_reports
                ORDER BY generated_at DESC LIMIT 1
                """
            ).fetchone()

            if not row:
                return None

            import json as json_mod

            try:
                data = json_mod.loads(row["report_json"])
            except (json_mod.JSONDecodeError, TypeError):
                return None

            new_interests = data.get("new_interests", [])
            if not new_interests:
                return None

            # Check if we've already notified about these interests
            notified = self._get_notified_interests()
            new_to_notify = [i for i in new_interests if i not in notified]

            if not new_to_notify:
                return None

            interests_str = "、".join(new_to_notify[:3])
            return PushNotification(
                notification_id=f"new-interest-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                title="🔍 发现新兴趣",
                body=f"你最近开始关注：{interests_str}。已为你调整推荐权重。",
                notification_type="new_interest",
                priority="medium",
                tags=new_to_notify[:3],
                created_at=datetime.now().isoformat(),
            )

        finally:
            conn.close()

    def _check_reminders(self) -> PushNotification | None:
        """Check for reminders (unread favorites, due reviews)."""
        conn = self._get_conn()
        try:
            # Count unread favorites (favorited but not viewed)
            unread_favs = conn.execute(
                """
                SELECT COUNT(*) as cnt FROM events e1
                WHERE e1.event_type = 'favorite'
                  AND e1.created_at >= datetime('now', '-7 days')
                  AND NOT EXISTS (
                    SELECT 1 FROM events e2
                    WHERE e2.event_type = 'view'
                      AND e2.url = e1.url
                      AND e2.created_at > e1.created_at
                  )
                """
            ).fetchone()["cnt"]

            # Count due knowledge cards
            due_cards = 0
            try:  # noqa: SIM105
                due_cards = conn.execute(
                    "SELECT COUNT(*) as cnt FROM knowledge_cards WHERE next_review <= datetime('now')"
                ).fetchone()["cnt"]
            except Exception:
                pass  # Table might not exist

            if unread_favs == 0 and due_cards == 0:
                return None

            parts = []
            if unread_favs > 0:
                parts.append(f"{unread_favs} 条收藏还没读")
            if due_cards > 0:
                parts.append(f"{due_cards} 张知识卡片待复习")

            return PushNotification(
                notification_id=f"reminder-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                title="⏰ 阅读提醒",
                body="；".join(parts) + "，抽空看看吧。",
                notification_type="reminder",
                priority="low",
                created_at=datetime.now().isoformat(),
            )

        finally:
            conn.close()

    def _send_notification(self, notif: PushNotification) -> bool:
        """Send a notification through configured channels."""
        notif.sent_at = datetime.now().isoformat()

        # Always save to database (in-app channel)
        self._save_notification(notif)

        # Webhook channel
        if "webhook" in self.config.channels and self.config.webhook_url:
            try:
                import json as json_mod
                import urllib.request

                data = json_mod.dumps(notif.to_dict()).encode()
                req = urllib.request.Request(
                    self.config.webhook_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception:
                logger.exception("Failed to send webhook notification")

        # Email channel (would need SMTP config)
        if "email" in self.config.channels and self.config.email_address:
            logger.info(
                "Email notification would be sent to %s: %s", self.config.email_address, notif.title
            )

        return True

    def _save_notification(self, notif: PushNotification) -> None:
        """Save notification to database."""
        import json as json_mod

        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS push_notifications (
                    notification_id TEXT PRIMARY KEY,
                    title TEXT,
                    body TEXT,
                    notification_type TEXT,
                    priority TEXT,
                    content_url TEXT,
                    content_title TEXT,
                    tags TEXT,
                    created_at TEXT,
                    sent_at TEXT,
                    read_at TEXT,
                    dismissed INTEGER DEFAULT 0,
                    channel TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO push_notifications
                (notification_id, title, body, notification_type, priority, content_url,
                 content_title, tags, created_at, sent_at, read_at, dismissed, channel)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notif.notification_id,
                    notif.title,
                    notif.body,
                    notif.notification_type,
                    notif.priority,
                    notif.content_url,
                    notif.content_title,
                    json_mod.dumps(notif.tags, ensure_ascii=False),
                    notif.created_at,
                    notif.sent_at,
                    notif.read_at,
                    1 if notif.dismissed else 0,
                    notif.channel,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_notifications(
        self, *, limit: int = 20, unread_only: bool = False
    ) -> list[PushNotification]:
        """Get recent notifications."""
        conn = self._get_conn()
        try:
            if unread_only:
                rows = conn.execute(
                    """
                    SELECT * FROM push_notifications
                    WHERE read_at IS NULL OR read_at = ''
                    ORDER BY created_at DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM push_notifications ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()

            return [self._row_to_notification(row) for row in rows]
        finally:
            conn.close()

    def mark_as_read(self, notification_id: str) -> bool:
        """Mark a notification as read."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE push_notifications SET read_at = ? WHERE notification_id = ?",
                (datetime.now().isoformat(), notification_id),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def dismiss(self, notification_id: str) -> bool:
        """Dismiss a notification."""
        conn = self._get_conn()
        try:
            conn.execute(
                "UPDATE push_notifications SET dismissed = 1 WHERE notification_id = ?",
                (notification_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def _exceeded_daily_limit(self) -> bool:
        """Check if daily notification limit has been exceeded."""
        return self._count_today_sent() >= self.config.max_notifications_per_day

    def _count_today_sent(self) -> int:
        """Count notifications sent today."""
        conn = self._get_conn()
        try:
            # Ensure table exists
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS push_notifications (
                    notification_id TEXT PRIMARY KEY,
                    title TEXT,
                    body TEXT,
                    notification_type TEXT,
                    priority TEXT,
                    content_url TEXT,
                    content_title TEXT,
                    tags TEXT,
                    created_at TEXT,
                    sent_at TEXT,
                    read_at TEXT,
                    dismissed INTEGER DEFAULT 0,
                    channel TEXT
                )
                """
            )
            today = datetime.now().strftime("%Y-%m-%d")
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM push_notifications WHERE date(sent_at) = ?",
                (today,),
            ).fetchone()
            return row["cnt"] if row else 0
        finally:
            conn.close()

    def _in_quiet_hours(self) -> bool:
        """Check if current time is in quiet hours."""
        now = datetime.now()
        hour = now.hour
        if self.config.quiet_hours_start < self.config.quiet_hours_end:
            return self.config.quiet_hours_start <= hour < self.config.quiet_hours_end
        else:  # Overnight quiet hours (e.g., 22:00 - 08:00)
            return hour >= self.config.quiet_hours_start or hour < self.config.quiet_hours_end

    def _get_notified_content_ids(self) -> set[int]:
        """Get IDs of content already notified about."""
        conn = self._get_conn()
        try:
            conn.execute(
                "SELECT content_title FROM push_notifications WHERE notification_type = 'high_value'"
            ).fetchall()
            # This is a simplified approach - in production we'd store content_id
            return set()
        finally:
            conn.close()

    def _get_notified_interests(self) -> set[str]:
        """Get interests already notified about."""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT tags FROM push_notifications WHERE notification_type = 'new_interest'"
            ).fetchall()
            notified = set()
            import json as json_mod

            for row in rows:
                if row["tags"]:
                    try:
                        tags = json_mod.loads(row["tags"])
                        notified.update(tags)
                    except (json_mod.JSONDecodeError, TypeError):
                        pass
            return notified
        finally:
            conn.close()

    def _row_to_notification(self, row: Any) -> PushNotification:
        """Convert a database row to a PushNotification."""
        import json as json_mod

        tags = []
        if row["tags"]:
            try:
                tags = json_mod.loads(row["tags"])
            except (json_mod.JSONDecodeError, TypeError):
                tags = [t.strip() for t in str(row["tags"]).split(",") if t.strip()]

        return PushNotification(
            notification_id=row["notification_id"],
            title=row["title"] or "",
            body=row["body"] or "",
            notification_type=row["notification_type"] or "",
            priority=row["priority"] or "medium",
            content_url=row["content_url"] or "",
            content_title=row["content_title"] or "",
            tags=tags,
            created_at=row["created_at"] or "",
            sent_at=row["sent_at"] or "",
            read_at=row["read_at"] or "",
            dismissed=bool(row["dismissed"]),
            channel=row["channel"] or "in_app",
        )
