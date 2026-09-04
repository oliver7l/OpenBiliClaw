"""WeChat (微信公众号) RSS scheduled fetching tasks.

Fetches WeChat public account articles via wechat2rss RSS feeds and
injects them into the recommendation pool with ``source_platform="wechat"``.
"""

from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.sources.registry import AdapterRegistry
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

_WECHAT_FETCH_INTERVAL_SECONDS = 7200  # 2 hours


async def run_wechat_polling(
    adapter_registry: AdapterRegistry,
    db: Database,
    subscriptions: list[dict[str, str]],
) -> int:
    """Fetch all configured WeChat RSS feeds.

    Uses the registered "rss" adapter for parsing, then injects articles
    with ``source_platform="wechat"`` for proper categorisation.

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
            items = await adapter.fetch(recipe, profile=None, limit=20)
        except Exception:
            logger.exception("WeChat fetch failed for %s", feed_url)
            continue

        for item in items:
            # Also mirror into the reading library so subscribed accounts
            # accumulate a readable archive (not just pool candidates).
            db.upsert_article(
                source_type="wechat",
                source_name=feed_name,
                title=item.title,
                url=item.content_url,
                author=item.author_name or "",
                summary=item.description or "",
                content_text=item.content_text or "",
                published_at=item.discovered_at or "",
            )
            bvid = "wx-" + hashlib.md5(item.content_url.encode()).hexdigest()[:16]
            db.inject_article_to_pool(
                bvid=bvid,
                title=item.title,
                url=item.content_url,
                author=item.author_name or "",
                source_name=feed_name,
                description=item.description or "",
                published_at=item.discovered_at or "",
                source_platform="wechat",
            )
            total += 1

    logger.info(
        "WeChat polling: fetched %d articles from %d feeds",
        total,
        len(subscriptions),
    )
    return total
