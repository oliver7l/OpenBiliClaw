"""Xiaoyuzhou (小宇宙) podcast RSS source adapter.

Fetches podcast RSS feeds via RSSHub, extracts episode metadata
(audio URL, duration, cover image), and normalises into
``DiscoveredContent`` for the recommendation pipeline.
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import TYPE_CHECKING

import feedparser

if TYPE_CHECKING:
    from openbiliclaw.discovery.engine import DiscoveredContent
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

# 同 rss_adapter：feedparser 同步网络抓取走独立线程池，避免占用
# FastAPI 默认 executor（HTTP DB 查询同池）导致轮询期页面请求排队。
_FEED_PARSE_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = (
    concurrent.futures.ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="feedparse",
    )
)


class XiaoyuzhouAdapter:
    """Adapter for Xiaoyuzhou podcast RSS feeds."""

    @property
    def source_type(self) -> str:
        return "xiaoyuzhou"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: object | None = None,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Fetch and parse podcast RSS feed defined by recipe config."""
        feed_url = (recipe.config or {}).get("url", "")
        feed_name = recipe.name or recipe.config.get("name", feed_url)
        if not feed_url:
            logger.warning("XiaoyuzhouAdapter: no URL in recipe %s", recipe.id)
            return []

        import asyncio

        feed = await asyncio.get_running_loop().run_in_executor(
            _FEED_PARSE_EXECUTOR, feedparser.parse, feed_url
        )

        if feed.bozo and not feed.entries:
            logger.warning(
                "XiaoyuzhouAdapter: failed to parse %s: %s",
                feed_url,
                feed.bozo_exception,
            )
            return []

        import re

        from openbiliclaw.discovery.engine import DiscoveredContent

        # Get podcast-level metadata from the feed
        podcast_cover = ""
        if feed.feed and hasattr(feed.feed, "image"):
            image = feed.feed.image
            podcast_cover = getattr(image, "href", "") or getattr(image, "url", "")

        items: list[DiscoveredContent] = []
        for entry in feed.entries[:limit]:
            title = (getattr(entry, "title", "") or "").strip()
            link = (getattr(entry, "link", "") or "").strip()
            if not title or not link:
                continue

            author = ""
            if hasattr(entry, "author") and entry.author:
                author = entry.author.strip()
            elif hasattr(entry, "authors") and entry.authors:
                author = entry.authors[0].get("name", "")

            summary = ""
            if hasattr(entry, "summary") and entry.summary:
                summary = entry.summary.strip()
            elif hasattr(entry, "description") and entry.description:
                summary = entry.description.strip()
            if summary:
                summary = re.sub(r"<[^>]+>", "", summary)[:500]

            published = ""
            if hasattr(entry, "published") and entry.published:
                published = entry.published
            elif hasattr(entry, "updated") and entry.updated:
                published = entry.updated

            # Podcast-specific metadata
            audio_url = ""
            duration_seconds = 0
            episode_cover = podcast_cover

            # Parse enclosure (audio URL)
            if hasattr(entry, "enclosures") and entry.enclosures:
                for enc in entry.enclosures:
                    href = getattr(enc, "href", "") or ""
                    if href:
                        audio_url = href
                        break
            elif hasattr(entry, "links"):
                for link_item in entry.links:
                    rel = getattr(link_item, "rel", "")
                    if rel == "enclosure" or rel == "audio":
                        href = getattr(link_item, "href", "") or ""
                        if href:
                            audio_url = href
                            break

            # Parse duration
            if hasattr(entry, "itunes_duration") and entry.itunes_duration:
                duration_seconds = _parse_duration(str(entry.itunes_duration))
            if not duration_seconds and hasattr(entry, "duration") and entry.duration:
                duration_seconds = _parse_duration(str(entry.duration))

            # Parse episode cover
            if hasattr(entry, "itunes_image") and entry.itunes_image:
                episode_cover = (
                    getattr(entry.itunes_image, "href", "") or entry.itunes_image or podcast_cover
                )

            content_id = f"xyz-{hash(link) & 0xFFFFFFFF:08x}"

            items.append(
                DiscoveredContent(
                    content_id=content_id,
                    content_url=link,
                    source_platform="xiaoyuzhou",
                    title=title,
                    description=summary[:300],
                    author_name=author or feed_name,
                    discovered_at=published,
                    up_name=feed_name,
                    cover_url=episode_cover,
                    duration=duration_seconds,
                    content_type="audio",
                    body_text=audio_url,
                )
            )

        logger.info("XiaoyuzhouAdapter: fetched %d episodes from %s", len(items), feed_name)
        return items


def _parse_duration(value: str) -> int:
    """Parse duration string (HH:MM:SS, MM:SS, or plain seconds) into seconds."""
    if not value:
        return 0
    value = value.strip()
    try:
        parts = value.split(":")
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        elif len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        else:
            return int(value)
    except (ValueError, TypeError):
        return 0
