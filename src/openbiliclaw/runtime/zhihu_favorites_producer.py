"""Zhihu favorites (collections) producer.

Fetches the logged-in user's Zhihu collections via ``zhihu interact collect
list/view --json`` and inserts new items into ``content_cache`` (pool.db).
Runs once by default; use ``--loop`` for continuous 24h-interval mode.

Usage:
    python3 -m openbiliclaw.runtime.zhihu_favorites_producer --once
    python3 -m openbiliclaw.runtime.zhihu_favorites_producer --once --max 100
    python3 -m openbiliclaw.runtime.zhihu_favorites_producer --loop
    python3 -m openbiliclaw.runtime.zhihu_favorites_producer --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openbiliclaw.runtime.rate_limit_guard import RateLimitGuard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pool.db"

# Clean environment so zhihu CLI finds its config regardless of cwd
CLEAN_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "LANG": os.environ.get("LANG", "en_US.UTF-8"),
}

# Zhihu answer/article ID patterns
_ANSWER_ID_RE = re.compile(r"^\d{15,25}$")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _obc_connect(db_path: Path) -> sqlite3.Connection:
    """Connect to pool.db directly."""
    return sqlite3.connect(str(db_path))


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _fetch_collections() -> list[dict[str, Any]]:
    """Call ``zhihu interact collect list --json`` and return collection list."""
    result = subprocess.run(
        ["zhihu", "interact", "collect", "list", "--json"],
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
        timeout=60,
    )
    if result.returncode != 0:
        logger.error(
            "zhihu collect list failed (rc=%d): %s", result.returncode, result.stderr[:500]
        )
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("zhihu collect list JSON parse error: %s", exc)
        return []
    if isinstance(data, list):
        return data
    return []


def _fetch_collection_items(collection_id: str, max_items: int = 50) -> list[dict[str, Any]]:
    """Call ``zhihu interact collect view <id> --json --max N`` and return items."""
    result = subprocess.run(
        ["zhihu", "interact", "collect", "view", collection_id, "--json", "--max", str(max_items)],
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
        timeout=120,
    )
    if result.returncode != 0:
        logger.error(
            "zhihu collect view %s failed (rc=%d): %s",
            collection_id,
            result.returncode,
            result.stderr[:500],
        )
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("zhihu collect view JSON parse error: %s", exc)
        return []
    items = data.get("items", [])
    if not isinstance(items, list):
        return []
    return items


def _fetch_all_favorites(max_per_collection: int = 100) -> list[dict[str, Any]]:
    """Fetch all collections and their items.

    Skips collections with 0 items. The main "我的收藏" collection usually
    holds the bulk of user favorites.
    """
    collections = _fetch_collections()
    if not collections:
        logger.warning("no zhihu collections found")
        return []

    logger.info("found %d zhihu collections", len(collections))
    all_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for coll in collections:
        coll_id = str(coll.get("id", "") or "").strip()
        coll_title = str(coll.get("title", "") or "").strip()
        answer_count = int(coll.get("answer_count", 0) or 0)

        if not coll_id or answer_count == 0:
            logger.info(
                "skipping collection '%s' (id=%s, count=%d)", coll_title, coll_id, answer_count
            )
            continue

        logger.info(
            "fetching collection '%s' (id=%s, ~%d items)", coll_title, coll_id, answer_count
        )
        items = _fetch_collection_items(coll_id, max_items=max_per_collection)

        new_count = 0
        for item in items:
            item_id = str(item.get("id", "") or "").strip()
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                # Attach collection metadata for later use
                item["_collection_id"] = coll_id
                item["_collection_title"] = coll_title
                all_items.append(item)
                new_count += 1

        logger.info(
            "collection '%s': %d items fetched (%d new unique)", coll_title, len(items), new_count
        )
        time.sleep(2)  # be gentle with rate limits

    return all_items


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _parse_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields from zhihu collection items into content_cache rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items:
        item_id = str(item.get("id", "") or "").strip()
        if not item_id or not _ANSWER_ID_RE.match(item_id):
            continue
        if item_id in seen:
            continue
        seen.add(item_id)

        item_type = str(item.get("type", "answer") or "").strip()
        title = str(item.get("title", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        author = str(item.get("author_name", "") or "").strip()
        excerpt = str(item.get("excerpt", "") or "").strip()
        voteup_count = int(item.get("voteup_count", 0) or 0)
        comment_count = int(item.get("comment_count", 0) or 0)
        thanks_count = int(item.get("thanks_count", 0) or 0)
        collection_count = int(item.get("collection_count", 0) or 0)
        collect_time = str(item.get("collect_time", "") or "").strip()
        coll_title = str(item.get("_collection_title", "") or "").strip()

        if not url:
            if item_type == "answer":
                # Try to reconstruct URL from question/answer IDs
                url = f"https://www.zhihu.com/answer/{item_id}"
            elif item_type == "article":
                url = f"https://zhuanlan.zhihu.com/p/{item_id}"
            else:
                url = f"https://www.zhihu.com/answer/{item_id}"

        if not title:
            title = f"知乎{item_type} {item_id}"

        # Build body text from excerpt + collection context
        body_parts = []
        if excerpt:
            body_parts.append(excerpt)
        if coll_title and coll_title != "我的收藏":
            body_parts.append(f"[收藏夹: {coll_title}]")
        if collect_time:
            body_parts.append(f"[收藏时间: {collect_time}]")
        body_text = "\n\n".join(body_parts)[:5000] if body_parts else title[:2000]

        content_type = "zhihu-answer" if item_type == "answer" else "zhihu-article"

        rows.append(
            {
                "bvid": item_id,
                "title": title[:500],
                "up_name": author,
                "author_name": author,
                "content_url": url,
                "source_platform": "zhihu",
                "source": "zhihu-favorites",
                "content_type": content_type,
                "pool_status": "fresh",
                "body_text": body_text,
                "like_count": voteup_count,
                "comment_count": comment_count,
                "favorite_count": collection_count,
                "share_count": thanks_count,
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


def _insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Insert new rows into content_cache, skipping duplicates by bvid."""
    inserted = 0
    for row in rows:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    body_text, like_count, comment_count, favorite_count,
                    share_count, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["bvid"],
                    row["title"],
                    row["up_name"],
                    row["author_name"],
                    row["content_url"],
                    row["source_platform"],
                    row["source"],
                    row["content_type"],
                    row["pool_status"],
                    row["body_text"],
                    row["like_count"],
                    row["comment_count"],
                    row["favorite_count"],
                    row["share_count"],
                    row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            continue
    return inserted


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------


def _run_once(max_per_collection: int = 100, dry_run: bool = False) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    items = _fetch_all_favorites(max_per_collection=max_per_collection)
    if not items:
        return {"ok": False, "reason": "empty_favorites", "items_fetched": 0, "inserted": 0}

    rows = _parse_items(items)
    if not rows:
        return {"ok": False, "reason": "no_valid_items", "items_fetched": len(items), "inserted": 0}

    if dry_run:
        logger.info(
            "dry-run: %d items fetched, %d valid rows (not inserted)", len(items), len(rows)
        )
        return {
            "ok": True,
            "items_fetched": len(items),
            "valid_items": len(rows),
            "inserted": 0,
            "skipped_duplicates": 0,
            "dry_run": True,
        }

    conn = _obc_connect(DB_PATH)
    try:
        inserted = _insert_rows(conn, rows)
        conn.commit()
        return {
            "ok": True,
            "items_fetched": len(items),
            "valid_items": len(rows),
            "inserted": inserted,
            "skipped_duplicates": len(rows) - inserted,
        }
    finally:
        conn.close()


def run_forever(max_per_collection: int = 100, interval_hours: int = 24) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    guard = RateLimitGuard("zhihu-favorites", state_dir=PROJECT_ROOT / "data" / "rate_limit")
    logger.info(
        "zhihu favorites producer started (interval=%dh, max_per_collection=%d, rate-limit guard enabled)",
        interval_hours,
        max_per_collection,
    )
    while True:
        if guard.should_skip():
            logger.info("sleeping %d hours (cooldown active)", interval_hours)
            time.sleep(interval_hours * 3600)
            continue
        try:
            result = _run_once(max_per_collection=max_per_collection)
            if result.get("ok"):
                fetched = result.get("items_fetched", 0)
                if fetched > 0:
                    guard.record_success()
                else:
                    guard.record_failure(
                        reason="empty_result", detail="zhihu favorites returned 0 items"
                    )
                logger.info(
                    "zhihu favorites ok: %d fetched, %d new, %d duplicate",
                    fetched,
                    result.get("inserted", 0),
                    result.get("skipped_duplicates", 0),
                )
            else:
                reason = result.get("reason", "unknown")
                detail = result.get("detail", "")
                guard.record_failure(reason=reason, detail=detail)
                logger.warning("zhihu favorites skipped: %s", reason)
        except Exception as exc:
            guard.record_failure(reason="exception", detail=str(exc))
            logger.exception("zhihu favorites cycle failed: %s", exc)
        logger.info("sleeping %d hours until next cycle", interval_hours)
        time.sleep(interval_hours * 3600)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(description="Zhihu favorites producer")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--loop", action="store_true", help="Run forever (24h interval)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes")
    parser.add_argument(
        "--max", type=int, default=100, help="Max items per collection (default: 100)"
    )
    parser.add_argument(
        "--interval", type=int, default=24, help="Loop interval in hours (default: 24)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.once or args.dry_run:
        result = _run_once(max_per_collection=args.max, dry_run=args.dry_run)
        if result.get("ok"):
            logger.info(
                "zhihu favorites ok: %d fetched, %d new, %d duplicate",
                result.get("items_fetched", 0),
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
            )
        else:
            logger.warning("zhihu favorites skipped: %s", result.get("reason", "unknown"))
        return

    if args.loop:
        run_forever(max_per_collection=args.max, interval_hours=args.interval)
        return

    parser.print_help()


if __name__ == "__main__":
    _main()
