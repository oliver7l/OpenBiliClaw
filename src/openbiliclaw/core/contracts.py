"""Discovery 合同类型 — sources 与 discovery 的共享契约（K6b）。

``DiscoveredContent``（值对象）与 ``DiscoveryStrategy``（策略 ABC）原定义在
``obc_discovery.engine``，sources 侧 17 个 adapter 引用它们形成
sources→discovery 同层依赖（K6b）。下沉到中性 core：

- ``obc_discovery.engine`` 从这里 import 并 re-export（引擎与策略零改动）
- ``sources`` 侧直接依赖 core（领域→core 合法）
- ``SoulProfile`` 仅出现在注解中（``from __future__ import annotations``
  惰性求值 + TYPE_CHECKING），运行时零依赖
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from obc_soul.profile import SoulProfile

@dataclass
class DiscoveredContent:
    """A piece of content discovered by the engine."""

    bvid: str = ""  # Bilibili video ID (legacy; prefer content_id for new code)
    title: str = ""
    up_name: str = ""  # UP主 name (legacy; prefer author_name for new code)
    up_mid: int = 0  # UP主 ID
    cover_url: str = ""
    duration: int = 0  # seconds
    view_count: int = 0
    like_count: int = 0
    favorite_count: int = 0
    collect_count: int = 0
    comment_count: int = 0
    share_count: int = 0
    danmaku_count: int = 0
    reply_count: int = 0
    retweet_count: int = 0
    bookmark_count: int = 0
    tags: list[str] = field(default_factory=list)
    topic_key: str = ""
    topic_group: str = ""  # Coarse semantic category (e.g. "强化学习") for diversity
    style_key: str = ""
    # Franchise / IP / series key tagged by the LLM at evaluation time
    # (e.g. "原神", "崩坏:星穹铁道", "ChatGPT", "塞尔达传说"). Empty
    # for general-interest content. Lets the curator down-rank items
    # in the same IP after a single dislike, and lets the
    # ``/api/recommendations`` endpoint cap how many same-franchise
    # items appear in a single response window. Better than the
    # heuristic title-substring approach (which v0.3.17 briefly tried)
    # because the LLM already saw title + description + topic and can
    # infer the IP correctly even when the title is bilingual or coded
    # ("提瓦特摄影" → 原神, "宝可梦" → 精灵宝可梦, etc.).
    franchise_key: str = ""
    description: str = ""
    source_strategy: str = ""  # Which strategy found this
    relevance_score: float = 0.0  # 0.0 - 1.0 (based on user soul)
    relevance_reason: str = ""  # Why this is relevant to the user
    pool_expression: str = ""  # Precomputed recommendation copy for fast popup paths
    pool_topic_label: str = ""  # Precomputed personalized topic label for fast popup paths
    candidate_tier: str = "primary"  # Primary discovery vs backfill supply
    discovered_at: str = ""  # Cache timestamp for recency-aware ranking
    last_scored_at: str = ""  # Last relevance scoring timestamp

    # ── Multi-source fields (Phase 0) ───────────────────────────────
    content_id: str = ""  # Universal content ID; equals bvid for Bilibili content
    content_url: str = ""  # Direct clickable URL
    source_platform: str = ""  # "bilibili" | "xiaohongshu" | "web" | ...
    author_name: str = ""  # Universal author name; equals up_name for Bilibili
    score_threshold: float = 0.0  # Strategy-specific admission floor for raw candidates
    body_text: str = ""  # tweet/thread full text; empty for video sources
    content_type: str = "video"  # shape: "video" | "note" | "tweet" | "thread"
    # Full article body extracted from the feed (``content:encoded`` /
    # ``content``). Deliberately NOT part of ``to_cache_kwargs()`` — it feeds
    # the reading library (``articles.content_text``) only and must not bloat
    # the recommendation pool.
    content_text: str = ""
    # P1.8 yield provenance: the ``discovery_keywords.id`` of the search word
    # that produced this item (unified keyword planner). ``None`` for every
    # non-search / legacy / flag-off path — the admit-time yield backfill is a
    # no-op then, so attribution stays opt-in and byte-compatible.
    source_keyword_id: int | None = None

    def __post_init__(self) -> None:
        if not self.content_id and self.bvid:
            self.content_id = self.bvid
        if not self.source_platform and self.bvid:
            self.source_platform = "bilibili"
        if not self.author_name and self.up_name:
            self.author_name = self.up_name
        if not self.content_url and self.bvid:
            self.content_url = f"https://www.bilibili.com/video/{self.bvid}"
        if not self.content_url and self.source_platform == "xiaohongshu" and self.content_id:
            self.content_url = f"https://www.xiaohongshu.com/explore/{self.content_id}"

    def to_cache_kwargs(self) -> dict[str, object]:
        """Build the kwargs dict for ``Database.cache_content()``.

        Single source of truth for the DiscoveredContent → content_cache
        field mapping.  Used by discovery's ``_cache_results`` and the
        recommendation engine's ``classify_pool_backlog`` persist loop.
        """
        return {
            "title": self.title,
            "up_name": self.up_name,
            "up_mid": self.up_mid,
            "duration": self.duration,
            "tags": self.tags,
            "topic_key": self.topic_key,
            "topic_group": self.topic_group,
            "style_key": self.style_key,
            "franchise_key": self.franchise_key,
            "description": self.description,
            "cover_url": self.cover_url,
            "view_count": self.view_count,
            "like_count": self.like_count,
            "favorite_count": self.favorite_count,
            "collect_count": self.collect_count,
            "comment_count": self.comment_count,
            "share_count": self.share_count,
            "danmaku_count": self.danmaku_count,
            "reply_count": self.reply_count,
            "retweet_count": self.retweet_count,
            "bookmark_count": self.bookmark_count,
            "relevance_score": self.relevance_score,
            "relevance_reason": self.relevance_reason,
            "candidate_tier": self.candidate_tier,
            "source": self.source_strategy,
            "source_platform": self.source_platform or "bilibili",
            "content_id": self.content_id or self.bvid,
            "content_url": self.content_url,
            "author_name": self.author_name or self.up_name,
            "body_text": self.body_text,
            "content_type": self.content_type,
            "source_keyword_id": self.source_keyword_id,
        }


class DiscoveryStrategy(ABC):
    """Base class for content discovery strategies."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Strategy name."""
        ...

    @abstractmethod
    async def discover(self, profile: SoulProfile, limit: int = 20) -> list[DiscoveredContent]:
        """Execute the discovery strategy.

        Args:
            profile: Current user soul profile for relevance guidance.
            limit: Maximum number of items to return.

        Returns:
            List of discovered content items.
        """
        ...

    def create_backfill_strategy(self) -> DiscoveryStrategy | None:
        """Return an expanded/relaxed variant for supply backfill if supported."""
        return None
