"""Unified cross-source event format for soul-pipeline consumption.

Every source adapter — Bilibili, Xiaohongshu, generic Web, future
platforms — emits events through ``build_event()``. The resulting
dict has a stable shape so downstream consumers (preference analyzer,
awareness analyzer, profile builder, memory layer) see one unified
contract regardless of where the signal came from.

Why this exists
---------------

Pre-v0.3.22 each producer hand-built its own event dict inline:
- B站 history → ``{event_type, title, url, metadata: {bvid, author}}``
- B站 收藏    → ``{event_type, title, metadata: {folder, upper}}``
- B站 关注    → ``{event_type, title, metadata: {up_name, sign}}``
- 小红书      → ``{event_type, title, url, context, metadata: {source_platform, ...}}``

Three problems:

1. Only Xiaohongshu populated the natural-language ``context`` field.
   Everything else dropped into the LLM prompt as a raw JSON blob, so
   the analyzer couldn't form a single readable description without
   schema-aware logic.
2. ``source_platform`` was only present on Xiaohongshu events;
   ``compute_source_platform_mix`` had to assume "missing = bilibili"
   which won't generalize to future sources.
3. Author / creator naming was scattered: ``author`` / ``up_name`` /
   ``upper`` / ``author_name`` — every consumer had to fall through a
   list.

The unified contract
--------------------

```python
{
    "event_type": str,         # "view" | "favorite" | "like" | "follow" | "dislike" | ...
    "title": str,
    "url": str,                 # optional, may be empty
    "context": str,             # natural-language sentence; primary input for LLM
    "metadata": {
        "source_platform": str,  # "bilibili" | "xiaohongshu" | "web" | ...
        "author": str,           # canonical creator/author name; empty when not applicable
        ...                      # source-specific extras (bvid / note_id / folder / ...)
    },
}
```

The ``context`` string is what matters for LLM prompts. It reads like
a Chinese sentence: who did what, on which platform, with which content,
optionally noting the author. Code that filters / weights events should
look at structured fields (``event_type`` / ``metadata.source_platform``);
the LLM consumes ``context``.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# K5：满意度分类唯一事实已迁至 storage._event_classification（events 入库时调用），
# 此处 re-export 保持生产者/api 既有 import 路径不变。
from openbiliclaw.storage._event_classification import (  # noqa: E402,F401
    SatisfactionCategory,
    classify_event_satisfaction,
)

# Source platform constants — kept stable for analyzer mix calculations.
SOURCE_BILIBILI = "bilibili"
SOURCE_XIAOHONGSHU = "xiaohongshu"
SOURCE_DOUYIN = "douyin"
SOURCE_WEB = "web"
SOURCE_YOUTUBE = "youtube"
SOURCE_TWITTER = "twitter"
SOURCE_ZHIHU = "zhihu"
SOURCE_LINUXDO = "linuxdo"

# Human-readable platform labels used to render the context string.
# Keys must match the source_platform values stored in event metadata.
_PLATFORM_LABELS: dict[str, str] = {
    SOURCE_BILIBILI: "B 站",
    SOURCE_XIAOHONGSHU: "小红书",
    SOURCE_DOUYIN: "抖音",
    SOURCE_WEB: "网页",
    SOURCE_YOUTUBE: "YouTube",
    SOURCE_TWITTER: "X",
    SOURCE_ZHIHU: "知乎",
    SOURCE_LINUXDO: "Linux.do",
}

# Action verbs per event_type. Designed so the rendered sentence reads
# naturally as "在<platform>上<verb>了《<title>》" — Chinese doesn't need
# articles, so this stays compact.
_EVENT_TYPE_LABELS: dict[str, str] = {
    "view": "看了",
    "favorite": "收藏了",
    "like": "点赞了",
    "follow": "关注了",
    "dislike": "标记不喜欢",
    "click": "点开了",
    "dialogue": "聊到",
    "feedback": "反馈过",
    "comment": "评论过",
    "share": "分享了",
    "article_finished": "读完了",
    "article_dismissed": "屏蔽了",
}

_DEFAULT_SIGNAL_STRENGTH_BY_EVENT_TYPE: dict[str, float] = {
    "favorite": 1.0,
    "coin": 0.95,
    "share": 0.85,
    "like": 0.85,
    "comment": 0.75,
    "dialogue": 0.65,
    "follow": 0.6,
    "view": 0.35,
    "click": 0.3,
    "search": 0.25,
    "hover": 0.1,
    "scroll": 0.1,
    "snapshot": 0.1,
    "dislike": 1.0,
    "article_finished": 0.8,
    "article_dismissed": 0.8,
}


def default_signal_strength_for_event(
    event_type: str,
    metadata: dict[str, Any] | None = None,
) -> float | None:
    """Return a cross-source fallback evidence strength for an event.

    Platform adapters may pass a more precise ``metadata.signal_strength``.
    This fallback only fills missing values; it describes evidence strength,
    not sentiment polarity or the final interest weight.
    """
    normalized_event_type = event_type.strip().lower()
    metadata = metadata or {}

    if normalized_event_type == "feedback":
        feedback_type = str(metadata.get("feedback_type") or "").strip().lower()
        reaction = str(metadata.get("reaction") or "").strip().lower()
        if feedback_type == "dislike" or reaction == "thumbs_down":
            return 1.0
        if feedback_type == "like" or reaction == "thumbs_up":
            return 1.0
        if feedback_type == "comment":
            return 0.8
        if feedback_type == "dismiss":
            return 0.5
        return 0.5

    return _DEFAULT_SIGNAL_STRENGTH_BY_EVENT_TYPE.get(normalized_event_type)


def format_event_context(
    *,
    event_type: str,
    source_platform: str,
    title: str,
    author: str = "",
    extra: str = "",
) -> str:
    """Render a single-sentence Chinese description of an event.

    Examples
    --------
    >>> format_event_context(
    ...     event_type="favorite",
    ...     source_platform="bilibili",
    ...     title="讲透历史叙事",
    ...     author="历史实验室",
    ... )
    '在 B 站收藏了《讲透历史叙事》,作者:历史实验室'

    >>> format_event_context(
    ...     event_type="like",
    ...     source_platform="xiaohongshu",
    ...     title="手冲咖啡入门",
    ...     author="豆子老师",
    ... )
    '在小红书点赞了《手冲咖啡入门》,作者:豆子老师'

    >>> format_event_context(
    ...     event_type="follow",
    ...     source_platform="bilibili",
    ...     title="历史实验室",
    ...     extra="签名:专注于讲透中国近代史",
    ... )
    '在 B 站关注了《历史实验室》(签名:专注于讲透中国近代史)'

    The output is intentionally terse — LLM prompts pack many of these
    end-to-end, so verbose phrasing wastes context window.

    """
    platform_label = _PLATFORM_LABELS.get(source_platform, source_platform or "")
    action_label = _EVENT_TYPE_LABELS.get(event_type, "记录了")

    title = (title or "").strip()
    author = (author or "").strip()
    extra = (extra or "").strip()

    parts: list[str] = []
    if platform_label:
        parts.append(f"在{platform_label}")
    parts.append(action_label)
    parts.append(f"《{title}》" if title else "一条内容")
    if author:
        parts.append(f",作者:{author}")
    if extra:
        parts.append(f"({extra})")
    return "".join(parts).strip()


def build_event(
    *,
    event_type: str,
    source_platform: str,
    title: str = "",
    url: str = "",
    author: str = "",
    context: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a unified event dict.

    Parameters
    ----------
    event_type
        Canonical action type. See ``_EVENT_TYPE_LABELS`` for the
        recognised set; unknown values fall through to the literal
        string in the rendered context.
    source_platform
        One of the ``SOURCE_*`` constants. Tagged into ``metadata``
        so analyzers' source-mix code can find it.
    title
        Content title (video / note / page name). Used in both the
        structured field and the natural-language context.
    url
        Optional canonical URL. Stored at top level so memory-layer
        dedup logic can match across events without having to look
        into metadata.
    author
        Canonical creator name. Stored in ``metadata.author``;
        producers should pass it here regardless of platform-native
        naming (``up_name`` / ``upper`` / ``nickname``) to keep the
        consumer side schema-free.
    context
        Pre-formatted natural-language sentence. If empty,
        ``format_event_context`` builds one from the structured fields.
        Producers that have richer context (e.g. xhs scope, B站 fold
        membership) can override.
    metadata
        Source-specific extras. ``source_platform`` is auto-populated
        from the parameter; explicit ``metadata.source_platform`` wins.
        ``author`` is also synced when not already present.

    Returns
    -------
    dict
        The unified event ready for ``MemoryManager.propagate_event``,
        ``SoulEngine.analyze_events``, etc.

    """
    final_metadata: dict[str, Any] = dict(metadata) if metadata else {}
    final_metadata.setdefault("source_platform", source_platform)
    if author and "author" not in final_metadata:
        final_metadata["author"] = author
    if "signal_strength" not in final_metadata:
        signal_strength = default_signal_strength_for_event(event_type, final_metadata)
        if signal_strength is not None:
            final_metadata["signal_strength"] = signal_strength

    # Reuse the author from metadata if the caller didn't pass one
    # explicitly — handles producers that set author only inside metadata.
    effective_author = author or str(final_metadata.get("author", "") or "")

    if not context:
        context = format_event_context(
            event_type=event_type,
            source_platform=source_platform,
            title=title,
            author=effective_author,
        )

    event: dict[str, Any] = {
        "event_type": event_type,
        "title": title,
        "context": context,
        "metadata": final_metadata,
    }
    if url:
        event["url"] = url
    return event
