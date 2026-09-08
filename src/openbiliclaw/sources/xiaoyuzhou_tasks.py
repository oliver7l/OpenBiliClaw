"""Xiaoyuzhou (小宇宙) podcast scheduled fetching tasks.

Periodically fetches configured podcast RSS feeds and injects episodes
into the recommendation pool.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.sources.registry import AdapterRegistry
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

_XYZ_FETCH_INTERVAL_SECONDS = 7200  # 2 hours

# 独立写库线程池：入库移出事件循环，避免轮询写库阻塞页面请求（见 rss_tasks）。
_DB_WRITE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="dbwrite"
)


def _persist_items(db: Database, items: list, feed_name: str) -> int:
    """Synchronously upsert episodes + inject into pool (runs on writer thread)."""
    n = 0
    for item in items:
        # Mirror into the reading library. Podcasts have no article body,
        # so the episode shownotes (``description``) are what we archive.
        db.upsert_article(
            source_type="xiaoyuzhou",
            source_name=feed_name,
            title=item.title,
            url=item.content_url,
            author=item.author_name or "",
            summary=item.description or "",
            # Podcasts have no article body — archive the episode
            # shownotes so the entry is still readable in the library.
            content_text=item.content_text or item.description or "",
            published_at=item.discovered_at or "",
        )
        bvid = "xyz-" + hashlib.md5(item.content_url.encode()).hexdigest()[:16]
        db.inject_article_to_pool(
            bvid=bvid,
            title=item.title,
            url=item.content_url,
            author=item.author_name or "",
            source_name=feed_name,
            description=item.description or "",
            published_at=item.discovered_at or "",
            source_platform="xiaoyuzhou",
        )
        n += 1
    return n


async def run_xiaoyuzhou_polling(
    adapter_registry: AdapterRegistry,
    db: Database,
    subscriptions: list[dict[str, str]],
) -> int:
    """Fetch all configured Xiaoyuzhou podcast feeds using the registered adapter.

    Resolves the "xiaoyuzhou" adapter from the registry, creates a SourceRecipe
    for each subscription, and injects episodes into the recommendation pool.

    Returns:
        Total number of episodes fetched.

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
            source_type="xiaoyuzhou",
            name=feed_name,
            strategy="feed",
            config={"url": feed_url, "name": feed_name},
        )
        adapter = adapter_registry.resolve(recipe)
        if adapter is None:
            logger.warning("Xiaoyuzhou adapter not registered, skipping %s", feed_url)
            continue

        try:
            items = await adapter.fetch(recipe, profile=None, limit=30)
        except Exception:
            logger.exception("Xiaoyuzhou fetch failed for %s", feed_url)
            continue

        loop = asyncio.get_running_loop()
        total += await loop.run_in_executor(
            _DB_WRITE_EXECUTOR, _persist_items, db, items, feed_name
        )

    logger.info(
        "Xiaoyuzhou polling: fetched %d episodes from %d feeds",
        total,
        len(subscriptions),
    )
    return total
