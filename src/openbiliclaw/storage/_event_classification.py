"""事件满意度分类 — storage 侧唯一事实。

原本在 ``sources.event_format``，但 ``storage._events_mixin`` 在事件入库时
调用它做分类，形成 storage→sources 反向依赖（K5）。整段（常量 + 3 个函数）
自包含、零外部耦合，移到 storage 后：
- ``sources.event_format`` re-export，生产者与 api 既有 import 路径不变
- storage 直接本地 import
"""

from __future__ import annotations

import logging
from typing import Any, Literal

logger = logging.getLogger(__name__)

SatisfactionCategory = Literal["positive", "neutral", "negative", "unknown"]

# Dwell thresholds for satisfaction inference on click events.
#
# - meaningful_dwell: at least 15s AND at least 30% of the video duration.
#   Below either bound the watch was probably exploratory, not engaged.
# - quick_exit: under 5s. Almost always a clickbait-baited tab close.
#
# Tuned conservatively: the goal is to feed the preference layer only
# the events we are highly confident reflect real interest, while still
# letting genuinely short clips count if the user watched the bulk of them.
_MEANINGFUL_DWELL_MIN_SECONDS = 15
_MEANINGFUL_DWELL_MIN_RATIO = 0.3
_QUICK_EXIT_MAX_SECONDS = 5

# Explicit engagement event types (no dwell needed to read intent).
_EXPLICIT_POSITIVE_EVENT_TYPES = frozenset(
    {"like", "coin", "favorite", "comment", "article_finished"}
)

# Explicit aversion event types — the user actively signalled "never again",
# so intent is unambiguous without any dwell heuristics. Mirrors
# ``article_finished``: the reading-library block button emits this.
_EXPLICIT_NEGATIVE_EVENT_TYPES = frozenset({"article_dismissed"})

# Feedback metadata vocabulary — set on `feedback` events emitted by the
# extension's "👍 / 👎" UI and the recommendation feedback endpoint.
_POSITIVE_FEEDBACK_TYPES = frozenset({"like"})
_NEUTRAL_FEEDBACK_TYPES = frozenset({"comment"})
_POSITIVE_REACTIONS = frozenset({"thumbs_up"})
_NEGATIVE_FEEDBACK_TYPES = frozenset({"dislike"})
_NEGATIVE_REACTIONS = frozenset({"thumbs_down"})

# Events that record passive browse — useful for context but never a
# direct signal of like / dislike.
_PASSIVE_BROWSE_EVENT_TYPES = frozenset({"snapshot", "scroll", "hover", "search"})


def classify_event_satisfaction(event: dict[str, Any]) -> tuple[SatisfactionCategory, str]:
    """Return ``(category, reason)`` describing whether the user enjoyed this event.

    Pure, deterministic, audit-friendly. Never raises — a malformed
    payload returns ``("unknown", "fallback")`` so the persistence path
    can always store *something* without a classification step crashing
    the request.

    The reason string is a short stable identifier (snake_case) suitable
    for storage and observability dashboards; see the design doc for the
    full list of values.
    """
    try:
        event_type = str(event.get("event_type") or event.get("type") or "").strip()
        metadata_raw = event.get("metadata")
    except (TypeError, AttributeError):
        logger.debug("classify_event_satisfaction: malformed event payload", exc_info=True)
        return ("unknown", "fallback")

    # A non-None, non-dict metadata is a contract violation (the rest of
    # the pipeline assumes dict-shaped metadata). Treat it as unreadable
    # rather than silently coercing to {} and emitting `missing_dwell`,
    # which would suggest the payload was well-formed but lacked dwell.
    if metadata_raw is None:
        metadata: dict[str, Any] = {}
    elif isinstance(metadata_raw, dict):
        metadata = metadata_raw
    else:
        logger.debug(
            "classify_event_satisfaction: metadata is %s (not dict); returning fallback",
            type(metadata_raw).__name__,
        )
        return ("unknown", "fallback")

    if event_type in _EXPLICIT_POSITIVE_EVENT_TYPES:
        return ("positive", "explicit_engagement")

    if event_type in _EXPLICIT_NEGATIVE_EVENT_TYPES:
        return ("negative", "explicit_aversion")

    if event_type == "feedback":
        feedback_type = str(metadata.get("feedback_type") or "").strip().lower()
        reaction = str(metadata.get("reaction") or "").strip().lower()
        if feedback_type in _NEGATIVE_FEEDBACK_TYPES or reaction in _NEGATIVE_REACTIONS:
            return ("negative", "explicit_negative")
        if feedback_type in _POSITIVE_FEEDBACK_TYPES or reaction in _POSITIVE_REACTIONS:
            return ("positive", "explicit_engagement")
        if feedback_type in _NEUTRAL_FEEDBACK_TYPES:
            return ("neutral", "direct_feedback")
        return ("unknown", "fallback")

    if event_type == "click":
        return _classify_click_dwell(event, metadata)

    if event_type in _PASSIVE_BROWSE_EVENT_TYPES:
        return ("neutral", "passive_browse")

    return ("unknown", "fallback")


def _classify_click_dwell(
    event: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[SatisfactionCategory, str]:
    """Inner helper for click events — split out so the main rule table reads cleanly."""
    watch_seconds = _read_dwell_field(event, metadata, "watch_seconds")
    if watch_seconds is None:
        return ("unknown", "missing_dwell")

    if watch_seconds < _QUICK_EXIT_MAX_SECONDS:
        return ("negative", "quick_exit")

    duration = _read_dwell_field(event, metadata, "video_duration_seconds")
    if duration is None:
        # Legacy extension events use the `duration` key instead.
        duration = _read_dwell_field(event, metadata, "duration")

    meets_seconds = watch_seconds >= _MEANINGFUL_DWELL_MIN_SECONDS
    meets_ratio = (
        duration is not None
        and duration > 0
        and (watch_seconds / duration >= _MEANINGFUL_DWELL_MIN_RATIO)
    )

    if meets_seconds and (duration is None or meets_ratio):
        return ("positive", "meaningful_dwell")

    return ("neutral", "shallow_view")


def _read_dwell_field(
    event: dict[str, Any],
    metadata: dict[str, Any],
    key: str,
) -> float | None:
    """Read a numeric field from either the top-level event or its metadata.

    Returns ``None`` if the field is absent or the stored value cannot
    be coerced to a float (e.g. ``"unknown"`` strings from older payloads).
    """
    raw = event.get(key)
    if raw is None:
        raw = metadata.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
