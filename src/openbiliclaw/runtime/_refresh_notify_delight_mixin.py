"""ContinuousRefreshController mixin: 通知与惊喜（delight）投递。

从 ``runtime/refresh.py`` 拆出的方法组；``ContinuousRefreshController``
继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
from openbiliclaw.runtime._refresh_shared import RefreshControllerAttrs

logger = logging.getLogger("openbiliclaw.runtime.refresh")


class NotifyDelightMixin(RefreshControllerAttrs):
    """通知与惊喜（delight）投递。"""

    def get_pending_notification(self) -> dict[str, object] | None:
        """Return one recommendation candidate for browser notification."""
        state = self.memory_manager.load_discovery_runtime_state()
        last_notification_at = self._parse_iso_datetime(str(state.get("last_notification_at", "")))
        if last_notification_at is not None and self._now() - last_notification_at < timedelta(
            hours=self.notification_cooldown_hours
        ):
            return None
        candidate = self.database.get_notification_candidate(min_confidence=0.82)
        if candidate is None:
            return None
        return {
            "recommendation_id": int(candidate["id"]),
            "bvid": str(candidate.get("bvid", "")),
            "title": str(candidate.get("title", "")),
            "reason": str(candidate.get("expression", "")),
        }

    def mark_notification_sent(self, bvid: str) -> None:
        """Persist notification delivery markers."""
        self.database.mark_notification_sent(bvid)
        now = self._now().isoformat()
        self._update_discovery_runtime_state(
            lambda state: state.update({"last_notification_at": now})
        )

    def get_pending_delight(self) -> dict[str, object] | None:
        """Return one proactive delight candidate for browser notification.

        Honors the user's ``disliked_topics`` (from the preference layer)
        as a hard filter — a video whose title contains a disliked topic
        phrase is skipped even if its delight_score otherwise qualifies.
        """
        state = self.memory_manager.load_discovery_runtime_state()
        last_delight_at = self._parse_iso_datetime(
            str(state.get("last_delight_notification_at", ""))
        )
        if last_delight_at is not None and self._now() - last_delight_at < timedelta(
            hours=self.delight_cooldown_hours
        ):
            return None

        # Pull a small batch and filter disliked topics in Python — there
        # are typically only a handful of high-score candidates and a
        # very short disliked list, so the overhead is negligible.
        candidates = self.database.get_delight_candidates(
            min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            limit=20,
        )
        if not candidates:
            return None

        disliked_phrases = self._load_disliked_topic_phrases()
        candidate: dict[str, Any] | None = None
        for row in candidates:
            title = str(row.get("title", "")).lower()
            tags_raw = str(row.get("tags", "")).lower()
            haystack = f"{title} {tags_raw}"
            if any(phrase in haystack for phrase in disliked_phrases if phrase):
                continue
            candidate = row
            break
        if candidate is None:
            return None
        return {
            "bvid": str(candidate.get("bvid", "")),
            "title": str(candidate.get("title", "")),
            "delight_reason": str(candidate.get("delight_reason", "")),
            "delight_score": float(candidate.get("delight_score", 0.0) or 0.0),
            "delight_hook": str(candidate.get("delight_hook", "")),
            "cover_url": str(candidate.get("cover_url", "")),
            "content_url": str(candidate.get("content_url", "")),
            "source_platform": str(candidate.get("source_platform", "") or "bilibili"),
        }

    def _load_disliked_topic_phrases(self) -> list[str]:
        """Return lowercased *effective* disliked-topic substrings.

        Sourced from the soul engine's ``get_effective_disliked_topics`` —
        AI dislikes ∪ flat preference dislikes, with user overrides applied
        (base-then-overlay), so a manually added dislike filters here and a
        manually removed one does not. Phrases are case-insensitive substring
        matches against title + tags. Falls back to the raw preference layer
        for older soul-engine doubles lacking the method.
        """
        getter = getattr(self.soul_engine, "get_effective_disliked_topics", None)
        if callable(getter):
            try:
                return [str(item).strip().lower() for item in getter() if str(item).strip()]
            except Exception:
                return []
        try:
            layer = self.memory_manager.get_layer("preference")
        except Exception:
            return []
        data = getattr(layer, "data", None)
        if not isinstance(data, dict):
            return []
        raw = data.get("disliked_topics")
        if not isinstance(raw, list):
            return []
        return [str(item).strip().lower() for item in raw if str(item).strip()]

    def mark_delight_sent(self, bvid: str) -> None:
        """Persist delight notification delivery markers."""
        self.database.mark_delight_notified(bvid)
        now = self._now().isoformat()
        self._update_discovery_runtime_state(
            lambda state: state.update({"last_delight_notification_at": now})
        )

    async def prepare_delight_candidates(self) -> int:
        """Warm ready-to-push delight candidates even when no refresh runs."""
        if not self._is_initialized():
            return 0
        profile = await self.soul_engine.get_profile()
        return await self.recommendation_engine.precompute_pool_copy(
            profile=profile,
            limit=0,
        )

    def _safe_count_delight_candidates(self) -> int:
        """Best-effort count of pending delight candidates (returns 0 on any
        error so the caller can do delta-based comparison without crashing
        the refresh tick).
        """
        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        try:
            return int(
                self.database.count_delight_candidates(min_delight_score=DEFAULT_DELIGHT_THRESHOLD)
            )
        except Exception:
            return 0

    async def _publish_event(self, event: dict[str, object]) -> bool:
        publish = getattr(self.event_hub, "publish", None)
        if callable(publish):
            result = await publish(event)
            return True if result is None else bool(result)
        return False

    async def _publish_delight_if_available(self) -> None:
        """Check for a pending delight candidate and push it via WebSocket."""
        candidate = self.get_pending_delight()
        if candidate is None:
            return
        await self._publish_event(
            {
                "type": "delight.candidate",
                "phase": "ready",
                "message": "发现了一条你可能会意外喜欢的内容",
                "bvid": candidate.get("bvid", ""),
                "title": candidate.get("title", ""),
                "delight_reason": candidate.get("delight_reason", ""),
                "delight_score": candidate.get("delight_score", 0.0),
                "delight_hook": candidate.get("delight_hook", ""),
                "cover_url": candidate.get("cover_url", ""),
                "content_url": candidate.get("content_url", ""),
                "source_platform": candidate.get("source_platform", "bilibili"),
            }
        )

    _PROBE_COOLDOWN_HOURS = 4  # Don't re-push the same domain within this window

    async def _publish_pool_status_if_changed(self) -> None:
        """Emit a ``pool_status`` runtime event when the pool count rotates.

        Pool count changes most often via ``enforce_pool_cap`` reactivating
        suppressed items or trimming overflow — a path that doesn't go
        through the end-of-refresh ``refresh.pool_updated`` event. Without
        this hook, the popup's pool-count UI only refreshes when a full
        refresh wave completes; now it stays in sync within seconds of any
        pool-state change.

        Only emits when the count is different from the last emit, so
        steady-state ticks don't spam the WebSocket stream.
        """
        try:
            pool_counts = self._pool_readiness_counts()
            current = int(pool_counts["available"])
        except Exception:
            return
        if current == self._last_published_pool_count:
            return
        self._last_published_pool_count = current
        await self._publish_event(
            {
                "type": "pool_status",
                **self._pool_count_payload(pool_counts),
                "pool_target_count": int(self.pool_target_count),
            }
        )
