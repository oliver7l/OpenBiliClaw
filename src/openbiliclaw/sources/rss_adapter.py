"""RSS/Atom feed source adapter.

Fetches and parses RSS/Atom feeds, normalises items into DiscoveredContent
for the recommendation pipeline.
"""

from __future__ import annotations

import concurrent.futures
import logging
from typing import TYPE_CHECKING

import feedparser

if TYPE_CHECKING:
    from openbiliclaw.core.contracts import DiscoveredContent
    from openbiliclaw.sources.protocol import SourceRecipe

logger = logging.getLogger(__name__)

# feedparser.parse 是同步网络抓取（每源 1~10s）。用独立线程池执行，
# 避免占用 FastAPI 默认 ThreadPoolExecutor（HTTP 端点的 DB 查询同池）——
# 否则一次轮询串行抓几十个源时，HTTP 请求会在线程池排队 30~50s
# （页面打开 / 换一批被饿死）。独立池固定 4 线程，与请求路径物理隔离。
_FEED_PARSE_EXECUTOR: concurrent.futures.ThreadPoolExecutor | None = (
    concurrent.futures.ThreadPoolExecutor(
        max_workers=4,
        thread_name_prefix="feedparse",
    )
)


class RssAdapter:
    """Adapter for RSS/Atom feed sources."""

    @property
    def source_type(self) -> str:
        return "rss"

    async def fetch(
        self,
        recipe: SourceRecipe,
        profile: object | None = None,
        limit: int = 20,
    ) -> list[DiscoveredContent]:
        """Fetch and parse RSS feed defined by recipe config."""
        feed_url = (recipe.config or {}).get("url", "")
        feed_name = recipe.name or recipe.config.get("name", feed_url)
        if not feed_url:
            logger.warning("RssAdapter: no URL in recipe %s", recipe.id)
            return []

        import asyncio

        feed = await asyncio.get_running_loop().run_in_executor(
            _FEED_PARSE_EXECUTOR, feedparser.parse, feed_url
        )

        if feed.bozo and not feed.entries:
            logger.warning("RssAdapter: failed to parse %s: %s", feed_url, feed.bozo_exception)
            return []

        import re

        from openbiliclaw.core.contracts import DiscoveredContent

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

            raw_summary = ""
            if hasattr(entry, "summary") and entry.summary:
                raw_summary = entry.summary.strip()
            elif hasattr(entry, "description") and entry.description:
                raw_summary = entry.description.strip()
            summary = ""
            if raw_summary:
                summary = re.sub(r"<[^>]+>", "", raw_summary)[:500]

            published = ""
            if hasattr(entry, "published") and entry.published:
                published = entry.published
            elif hasattr(entry, "updated") and entry.updated:
                published = entry.updated

            # Full article body — feeds the reading library. Most full-text
            # feeds (wechat2rss etc.) expose it as ``content:encoded``, which
            # feedparser surfaces as ``entry.content[0].value``.
            content_text = ""
            raw_content = getattr(entry, "content", None)
            if raw_content:
                try:
                    content_text = (raw_content[0].get("value", "") or "").strip()
                except (AttributeError, IndexError, TypeError):
                    content_text = ""
            if not content_text:
                content_text = (getattr(entry, "content_encoded", "") or "").strip()
            # Most feeds omit content:encoded but ship the full article in
            # summary (e.g. 虎嗅 ~5k chars). Archive that as the body instead
            # of throwing it away — the 300-char ``description`` above is only
            # for pool display.
            if not content_text and raw_summary:
                content_text = raw_summary
            if content_text:
                content_text = re.sub(r"<[^>]+>", " ", content_text)
                content_text = re.sub(r"\s+", " ", content_text).strip()
                content_text = content_text[:20000]

            content_id = f"rss-{hash(link) & 0xFFFFFFFF:08x}"

            items.append(
                DiscoveredContent(
                    content_id=content_id,
                    content_url=link,
                    source_platform="rss",
                    title=title,
                    description=summary[:300],
                    author_name=author,
                    discovered_at=published,
                    up_name=feed_name,
                    content_text=content_text,
                )
            )

        logger.info("RssAdapter: fetched %d items from %s", len(items), feed_url)
        return items
