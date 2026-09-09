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
import sqlite3
import time
from pathlib import Path

from openbiliclaw.runtime.inbox_db import get_inbox_path, list_inbox_platforms

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


def merge_inbox(platform: str, pool_db_path: str | Path, data_dir: str | Path = "data") -> dict:
    """合并单个 platform 的 inbox 子库到 pool.db。

    返回合并统计: {platform, pending, merged, skipped, cleared}
    """
    inbox_path = get_inbox_path(platform, data_dir)
    if not inbox_path.exists():
        return {"platform": platform, "pending": 0, "merged": 0, "skipped": 0, "cleared": 0}

    pool_conn = sqlite3.connect(str(pool_db_path), timeout=60.0)
    try:
        # ATTACH inbox 子库
        pool_conn.execute("ATTACH DATABASE ? AS inbox", (str(inbox_path),))

        # 统计待合并数量
        pending = pool_conn.execute("SELECT COUNT(*) FROM inbox.content_cache").fetchone()[0]
        if pending == 0:
            pool_conn.execute("DETACH DATABASE inbox")
            return {"platform": platform, "pending": 0, "merged": 0, "skipped": 0, "cleared": 0}

        # INSERT OR IGNORE 合并到总库
        cols = ", ".join(_MERGE_COLUMNS)
        cursor = pool_conn.execute(
            f"INSERT OR IGNORE INTO content_cache ({cols}) SELECT {cols} FROM inbox.content_cache"
        )
        merged = cursor.rowcount
        skipped = pending - merged

        # 清空 inbox 子库
        pool_conn.execute("DELETE FROM inbox.content_cache")
        pool_conn.commit()
        pool_conn.execute("DETACH DATABASE inbox")

        logger.info("[%s] merged: %d new, %d duplicates (pending=%d)", platform, merged, skipped, pending)
        return {
            "platform": platform,
            "pending": pending,
            "merged": merged,
            "skipped": skipped,
            "cleared": merged + skipped,
        }
    except Exception as e:
        logger.error("[%s] merge failed: %s", platform, e)
        pool_conn.rollback()
        try:
            pool_conn.execute("DETACH DATABASE inbox")
        except Exception:
            pass
        return {"platform": platform, "pending": -1, "merged": 0, "skipped": 0, "cleared": 0, "error": str(e)}
    finally:
        pool_conn.close()


def merge_all(pool_db_path: str | Path, data_dir: str | Path = "data") -> list[dict]:
    """合并所有 inbox 子库到 pool.db。"""
    platforms = list_inbox_platforms(data_dir)
    results = []
    for platform in sorted(platforms):
        results.append(merge_inbox(platform, pool_db_path, data_dir))
        time.sleep(0.5)  # 合并之间短暂间隔
    return results


def run_forever(pool_db_path: str | Path, interval_minutes: int = 5, data_dir: str | Path = "data") -> None:
    """定期合并所有 inbox 子库。"""
    logger.info("inbox merger started (interval=%dmin)", interval_minutes)
    while True:
        cycle_start = time.time()
        results = merge_all(pool_db_path, data_dir)
        total_merged = sum(r.get("merged", 0) for r in results)
        total_pending = sum(r.get("pending", 0) for r in results if r.get("pending", 0) > 0)
        elapsed = time.time() - cycle_start
        logger.info("cycle done: %d merged, %d pending in %.0fs", total_merged, total_pending, elapsed)
        time.sleep(interval_minutes * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inbox sub-database merger")
    parser.add_argument("--pool-db", default="data/pool.db", help="Path to pool.db (default: data/pool.db)")
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
        result = merge_inbox(args.platform, args.pool_db, args.data_dir)
        print(result)
    elif args.once:
        results = merge_all(args.pool_db, args.data_dir)
        for r in results:
            print(r)
    else:
        run_forever(args.pool_db, args.interval, args.data_dir)


if __name__ == "__main__":
    main()
