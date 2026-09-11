"""Inbox 子库合并器

将各 platform 的 inbox 子库数据合并到 pool.db（总库）。
合并逻辑：ATTACH 子库 → INSERT OR IGNORE → 清空子库。

用法:
    python -m openbiliclaw.runtime.inbox_merger --once
    python -m openbiliclaw.runtime.inbox_merger --interval 5
"""

from __future__ import annotations

import argparse
import logging
import time
from contextlib import suppress
from pathlib import Path

from openbiliclaw.runtime.inbox_db import get_inbox_path, list_inbox_platforms
from openbiliclaw.storage.database import open_db_conn

logger = logging.getLogger(__name__)

# 合并时需要的字段（与 content_cache 表一致，50 个字段）
_MERGE_COLUMNS = [
    "bvid", "title", "up_name", "up_mid", "duration", "tags", "topic_key",
    "style_key", "franchise_key", "description", "cover_url", "view_count",
    "like_count", "favorite_count", "collect_count", "comment_count",
    "share_count", "danmaku_count", "reply_count", "retweet_count",
    "bookmark_count", "relevance_score", "relevance_reason", "pool_expression",
    "pool_topic_label", "candidate_tier", "discovered_at", "last_scored_at",
    "notification_sent", "notified_at", "pool_status", "recommended_at",
    "feedback_type", "feedback_at", "source", "body_text", "content_type",
    "source_keyword_id", "topic_group", "delight_score", "delight_reason",
    "delight_hook", "delight_notified", "delight_notified_at", "content_id",
    "content_url", "source_platform", "author_name", "quality_score",
    "quality_reason",
]


def merge_inbox(
    platform: str,
    pool_db_path: str | Path,
    main_db_path: str | Path = "data/openbiliclaw.db",
    data_dir: str | Path = "data",
) -> dict:
    """合并单个 platform 的 inbox 子库到 pool.db（content_cache）和主库（articles）。

    返回合并统计: {platform, cache_pending, cache_merged, articles_merged, cleared}
    """
    inbox_path = get_inbox_path(platform, data_dir)
    if not inbox_path.exists():
        return {"platform": platform, "cache_pending": 0, "cache_merged": 0, "articles_merged": 0, "cleared": 0}

    # ── 1. 合并 content_cache 到 pool.db ──
    pool_conn = open_db_conn(str(pool_db_path))
    cache_merged = 0
    cache_pending = 0
    try:
        pool_conn.execute("ATTACH DATABASE ? AS inbox", (str(inbox_path),))
        cache_pending = pool_conn.execute("SELECT COUNT(*) FROM inbox.content_cache").fetchone()[0]
        if cache_pending > 0:
            cols = ", ".join(_MERGE_COLUMNS)
            cursor = pool_conn.execute(
                f"INSERT OR IGNORE INTO content_cache ({cols}) SELECT {cols} FROM inbox.content_cache"
            )
            cache_merged = cursor.rowcount
            pool_conn.execute("DELETE FROM inbox.content_cache")
            pool_conn.commit()
        pool_conn.execute("DETACH DATABASE inbox")
    except Exception as e:
        logger.error("[%s] content_cache merge failed: %s", platform, e)
        pool_conn.rollback()
        with suppress(Exception):
            pool_conn.execute("DETACH DATABASE inbox")
    finally:
        pool_conn.close()

    # ── 2. 合并 articles 到 content.db（带重试）──
    # v0.4.0+: articles 表迁移到 content.db，与主库锁域隔离
    content_db_path = Path(main_db_path).with_name("content.db")
    articles_merged = 0
    articles_pending = 0
    for attempt in range(3):
        content_conn = open_db_conn(str(content_db_path))
        try:
            content_conn.execute("ATTACH DATABASE ? AS inbox", (str(inbox_path),))
            articles_pending = content_conn.execute("SELECT COUNT(*) FROM inbox.articles").fetchone()[0]
            if articles_pending > 0:
                art_cols = "source_type, source_name, title, url, author, content_text, published_at, tags"
                cursor = content_conn.execute(
                    f"INSERT OR IGNORE INTO articles ({art_cols}) SELECT {art_cols} FROM inbox.articles"
                )
                articles_merged = cursor.rowcount
                content_conn.execute("DELETE FROM inbox.articles")
                content_conn.commit()
            content_conn.execute("DETACH DATABASE inbox")
            break  # 成功，跳出重试循环
        except Exception as e:
            if attempt < 2:
                logger.warning("[%s] articles merge attempt %d failed, retrying: %s", platform, attempt + 1, e)
                time.sleep(2)
            else:
                logger.error("[%s] articles merge failed after 3 attempts: %s", platform, e)
            content_conn.rollback()
            with suppress(Exception):
                content_conn.execute("DETACH DATABASE inbox")
        finally:
            content_conn.close()

    total = cache_pending + articles_pending
    logger.info(
        "[%s] merged: cache %d new (%d pending), articles %d new (%d pending)",
        platform, cache_merged, cache_pending, articles_merged, articles_pending,
    )
    return {
        "platform": platform,
        "cache_pending": cache_pending,
        "cache_merged": cache_merged,
        "articles_merged": articles_merged,
        "cleared": total,
    }


def merge_all(
    pool_db_path: str | Path,
    main_db_path: str | Path = "data/openbiliclaw.db",
    data_dir: str | Path = "data",
) -> list[dict]:
    """合并所有 inbox 子库到 pool.db（content_cache）和主库（articles）。"""
    platforms = list_inbox_platforms(data_dir)
    results = []
    for platform in sorted(platforms):
        results.append(merge_inbox(platform, pool_db_path, main_db_path, data_dir))
        time.sleep(0.5)  # 合并之间短暂间隔
    return results


def run_forever(
    pool_db_path: str | Path,
    interval_minutes: int = 5,
    main_db_path: str | Path = "data/openbiliclaw.db",
    data_dir: str | Path = "data",
) -> None:
    """定期合并所有 inbox 子库。"""
    logger.info("inbox merger started (interval=%dmin)", interval_minutes)
    while True:
        cycle_start = time.time()
        results = merge_all(pool_db_path, main_db_path, data_dir)
        total_cache = sum(r.get("cache_merged", 0) for r in results)
        total_articles = sum(r.get("articles_merged", 0) for r in results)
        elapsed = time.time() - cycle_start
        logger.info("cycle done: %d cache + %d articles merged in %.0fs", total_cache, total_articles, elapsed)
        time.sleep(interval_minutes * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inbox sub-database merger")
    parser.add_argument(
        "--pool-db", default="data/pool.db", help="Path to pool.db (default: data/pool.db)"
    )
    parser.add_argument(
        "--main-db",
        default="data/openbiliclaw.db",
        help="Path to main db (default: data/openbiliclaw.db)",
    )
    parser.add_argument("--data-dir", default="data", help="Data directory (default: data)")
    parser.add_argument("--interval", type=int, default=5, help="Merge interval in minutes (default: 5)")
    parser.add_argument("--once", action="store_true", help="Run only one merge cycle then exit")
    parser.add_argument("--platform", help="Merge only one platform's inbox")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.platform:
        result = merge_inbox(args.platform, args.pool_db, args.main_db, args.data_dir)
        print(result)
    elif args.once:
        results = merge_all(args.pool_db, args.main_db, args.data_dir)
        for r in results:
            print(r)
    else:
        run_forever(args.pool_db, args.interval, args.main_db, args.data_dir)


if __name__ == "__main__":
    main()
