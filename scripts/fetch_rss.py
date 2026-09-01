"""Manual RSS feed fetch script.

Run this to test RSS feeds manually:
    python3 scripts/fetch_rss.py

It reads rss_subscriptions from config, fetches all feeds,
and stores articles in the database.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openbiliclaw.config import load_config
from openbiliclaw.sources.registry import AdapterRegistry
from openbiliclaw.sources.rss_adapter import RssAdapter
from openbiliclaw.storage.database import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def main():
    config = load_config()
    db_path = config.storage.db_path
    db = Database(db_path)
    db.initialize()

    # Register RSS adapter
    registry = AdapterRegistry()
    registry.register(RssAdapter())

    subscriptions = config.scheduler.rss_subscriptions
    if not subscriptions:
        logger.warning("No RSS subscriptions configured")
        return

    from openbiliclaw.sources.rss_tasks import run_rss_polling

    total = await run_rss_polling(registry, db, subscriptions)
    logger.info("Done: %d articles fetched", total)

    # Show what we got
    articles = db.get_recent_articles(limit=5)
    for a in articles:
        print(f"  [{a['source_name']}] {a['title']}")

    db.close()


if __name__ == "__main__":
    asyncio.run(main())