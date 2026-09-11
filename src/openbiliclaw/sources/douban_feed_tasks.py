"""豆瓣 feed 文章抓取任务。

Periodically fetches configured douban feeds (comments / groups / diary) and
stores articles into the reading library ``articles`` table, then injects
them into the recommendation pool.

写库逻辑与 ``rss_tasks`` 一致（``upsert_article`` + ``inject_article_to_pool``），
但 ``source_type="douban_feed"``。独立文件避免改动现有 RSS 链路。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openbiliclaw.sources.registry import AdapterRegistry
    from openbiliclaw.storage.database import Database

logger = logging.getLogger(__name__)

_DB_WRITE_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="dbwrite-douban"
)


def _state_path(db: Database) -> Path | None:
    """Derive watermark state file path from the database location."""
    base = getattr(db, "_db_path", None)
    if base is None:
        return None
    return Path(base).with_name("douban_feed_state.json")


def _load_watermarks(db: Database) -> dict[str, str]:
    """Load {feed_key: latest create_time} persisted incremental watermark."""
    path = _state_path(db)
    if not path or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {k: str(v) for k, v in dict(data).items() if v}
    except Exception:  # noqa: BLE001
        logger.warning("豆瓣 feed 增量水印读取失败，重置: %s", path)
        return {}


def _save_watermarks(db: Database, watermarks: dict[str, str]) -> None:
    path = _state_path(db)
    if not path:
        return
    try:
        path.write_text(
            json.dumps(watermarks, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:  # noqa: BLE001
        logger.warning("豆瓣 feed 增量水印写入失败: %s", path)


def _persist_items(
    db: Database,
    items: list,
    feed_name: str,
    *,
    source_type: str = "douban_feed",
) -> int:
    """Synchronously upsert douban articles + inject into pool (writer thread)."""
    n = 0
    for item in items:
        db.upsert_article(
            source_type=source_type,
            source_name=feed_name,
            title=item.title,
            url=item.content_url,
            author=item.author_name or "",
            summary=item.description or "",
            content_text=item.content_text or "",
            published_at=item.discovered_at or "",
        )
        bvid = "douban_feed-" + hashlib.md5(item.content_url.encode()).hexdigest()[:16]
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


async def run_douban_feed_polling(
    adapter_registry: AdapterRegistry,
    db: Database,
    subscriptions: list[dict[str, str]],
) -> int:
    """抓取所有配置的豆瓣 feed 并写入阅读库。

    Resolves the "douban_feed" adapter from the registry, creates a
    ``SourceRecipe`` for each subscription (config carries feed_kind/uid/group_id),
    fetches and stores articles.

    Returns:
        Total number of articles fetched.
    """
    from openbiliclaw.sources.protocol import SourceRecipe

    watermarks = _load_watermarks(db)
    loop = asyncio.get_running_loop()
    total = 0

    for sub in subscriptions:
        feed_kind = sub.get("feed_kind", "review")
        feed_name = sub.get("name", "豆瓣feed")
        uid = sub.get("uid", "")
        group_id = sub.get("group_id", "")

        recipe = SourceRecipe(
            id=str(uuid.uuid4()),
            source_type="douban_feed",
            name=feed_name,
            strategy="feed",
            config={"feed_kind": feed_kind, "uid": uid, "group_id": group_id, "name": feed_name},
        )
        adapter = adapter_registry.resolve(recipe)
        if adapter is None:
            logger.warning("Douban feed adapter not registered, skipping %s", feed_name)
            continue

        try:
            # diary（动态）走增量：只拉自上次水印后的新动态。
            if feed_kind == "diary" and uid and hasattr(adapter, "fetch_diary_since"):
                key = f"diary:{uid}"
                since = watermarks.get(key, "")
                items, newest = await adapter.fetch_diary_since(uid, feed_name, since=since, limit=30)
                if newest and newest > since:
                    watermarks[key] = newest
            else:
                items = await adapter.fetch(recipe, profile=None, limit=30)
        except Exception:  # noqa: BLE001
            logger.exception("豆瓣 feed 抓取失败 %s", feed_name)
            continue

        total += await loop.run_in_executor(
            _DB_WRITE_EXECUTOR, _persist_items, db, items, feed_name
        )

    _save_watermarks(db, watermarks)
    logger.info(
        "豆瓣 feed 轮询完成: %d 篇文章来自 %d 个源", total, len(subscriptions)
    )
    return total
