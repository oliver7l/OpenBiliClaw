"""RSS feed scheduled fetching tasks.

Periodically fetches configured RSS feeds and stores articles into
the database, then injects them into the recommendation pool.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.sources.registry import AdapterRegistry
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

_RSS_FETCH_INTERVAL_SECONDS = 3600

# 独立写库线程池：轮询入库（每源几十条同步 sqlite 写）从事件循环移出，
# 避免一次轮询占住事件循环数秒、把页面 HTTP 请求全部排队（与推荐流
# feedparse 独立线程池同一思路）。串行写（1 线程）规避并发写锁竞争。
_DB_WRITE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="dbwrite"
)


def _persist_items(db: Database, items: list, feed_name: str) -> int:
    """Synchronously upsert articles + inject into pool (runs on writer thread)."""
    import hashlib

    n = 0
    for item in items:
        db.upsert_article(
            source_type="rss",
            source_name=feed_name,
            title=item.title,
            url=item.content_url,
            author=item.author_name or "",
            summary=item.description or "",
            content_text=item.content_text or "",
            published_at=item.discovered_at or "",
        )
        # Also inject into the recommendation pool
        bvid = "rss-" + hashlib.md5(item.content_url.encode()).hexdigest()[:16]
        db.inject_article_to_pool(
            bvid=bvid,
            title=item.title,
            url=item.content_url,
            author=item.author_name or "",
            source_name=feed_name,
            description=item.description or "",
            published_at=item.discovered_at or "",
        )
        n += 1
    return n


async def run_rss_polling(
    adapter_registry: AdapterRegistry,
    db: Database,
    subscriptions: list[dict[str, str]],
) -> int:
    """Fetch all configured RSS feeds using the registered adapter.

    Resolves the "rss" adapter from the registry, creates a SourceRecipe
    for each subscription, and stores fetched articles.

    Returns:
        Total number of articles fetched.
    """
    import uuid

    from openbiliclaw.sources.protocol import SourceRecipe

    total = 0
    for sub in subscriptions:
        feed_url = sub.get("url", "")
        feed_name = sub.get("name", feed_url)
        if not feed_url:
            continue

        recipe = SourceRecipe(
            id=str(uuid.uuid4()),
            source_type="rss",
            name=feed_name,
            strategy="feed",
            config={"url": feed_url, "name": feed_name},
        )
        adapter = adapter_registry.resolve(recipe)
        if adapter is None:
            logger.warning("RSS adapter not registered, skipping %s", feed_url)
            continue

        try:
            items = await adapter.fetch(recipe, profile=None, limit=30)
        except Exception:
            logger.exception("RSS fetch failed for %s", feed_url)
            continue

        loop = asyncio.get_running_loop()
        total += await loop.run_in_executor(
            _DB_WRITE_EXECUTOR, _persist_items, db, items, feed_name
        )

    logger.info(
        "RSS polling complete: %d articles from %d feeds",
        total,
        len(subscriptions),
    )
    return total
