"""Manual douban feed fetch script.

Run this to pull douban feeds (comments / groups / diary) into the reading library:
    python3 scripts/fetch_douban_feed.py

It reads douban_feed_subscriptions from config, fetches each feed, and stores
articles in the database. Cookie is read from OPENBILICLAW_DOUBAN_COOKIE (or
the [sources.douban] cookie_env value) so private feeds work.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openbiliclaw.config import load_config
from openbiliclaw.sources.douban_feed_adapter import DoubanFeedAdapter
from openbiliclaw.sources.registry import AdapterRegistry
from openbiliclaw.storage.database import Database

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


def _resolve_cookie(config) -> str:
    """从 [sources.douban].cookie_env 环境变量读取 cookie；未配置返回空。"""
    cookie_env = "OPENBILICLAW_DOUBAN_COOKIE"
    try:
        sources = getattr(config, "sources", None)
        if sources is not None:
            db_src = getattr(sources, "douban", None)
            env = (getattr(db_src, "cookie_env", "") or "").strip()
            if env:
                cookie_env = env
    except Exception:  # noqa: BLE001
        pass
    return os.environ.get(cookie_env, "")


async def main() -> None:
    config = load_config()
    db_path = config.storage.db_path
    db = Database(db_path)
    db.initialize()

    registry = AdapterRegistry()
    registry.register(DoubanFeedAdapter(cookie=_resolve_cookie(config)))

    subscriptions = config.scheduler.douban_feed_subscriptions
    if not subscriptions:
        logger.warning("No douban_feed_subscriptions configured (see [scheduler] in config.toml)")
        return

    from openbiliclaw.sources.douban_feed_tasks import run_douban_feed_polling

    total = await run_douban_feed_polling(registry, db, subscriptions)
    logger.info("Done: %d douban articles fetched", total)

    # Show what we got (recent douban_feed articles)
    articles = db.get_recent_articles(limit=20)
    shown = 0
    for a in articles:
        if str(a.get("source_type", "")).startswith("douban"):
            print(f"  [{a['source_name']}] {a['title']}")
            shown += 1
    if not shown:
        print("  （暂无 douban_feed 入库文章，可能是订阅为空或抓取返回空）")
    db.close()


if __name__ == "__main__":
    asyncio.run(main())
