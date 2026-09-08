"""Bilibili personal content producer — favorites, watch-later, history.

Fetches the logged-in user's Bilibili personal content via ``bili`` CLI
and inserts new items into ``content_cache`` (pool.db).

Supports three modes:
  * ``--favorites``  — all favorite folders (default)
  * ``--watch-later`` — watch-later list
  * ``--history``     — watch history (recent 100)
  * ``--all``         — all three modes

Usage:
    python3 -m openbiliclaw.runtime.bilibili_favorites_producer --once --favorites
    python3 -m openbiliclaw.runtime.bilibili_favorites_producer --once --all
    python3 -m openbiliclaw.runtime.bilibili_favorites_producer --loop --all
    python3 -m openbiliclaw.runtime.bilibili_favorites_producer --dry-run --favorites
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
from openbiliclaw.runtime._db import connect_pool as _obc_connect

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pool.db"

# Clean environment so bili CLI finds its config regardless of cwd
CLEAN_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "LANG": os.environ.get("LANG", "en_US.UTF-8"),
}

# B站BV号格式
_BVID_RE = re.compile(r"^BV[0-9A-Za-z]{10}$")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


def _run_bili_cmd(args: list[str], timeout: int = 60) -> dict[str, Any]:
    """Run a bili CLI command and return parsed JSON."""
    result = subprocess.run(
        ["bili", *args],
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.error(
            "bili %s failed (rc=%d): %s", " ".join(args[:3]), result.returncode, result.stderr[:500]
        )
        return {}
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("bili %s JSON parse error: %s", " ".join(args[:3]), exc)
        return {}


def _fetch_favorite_folders() -> list[dict[str, Any]]:
    """Fetch all favorite folders via ``bili favorites --json``."""
    data = _run_bili_cmd(["favorites", "--json"])
    folders = data.get("data", [])
    if not isinstance(folders, list):
        return []
    return folders


def _fetch_favorite_folder(folder_id: int, page: int = 1) -> dict[str, Any]:
    """Fetch one page of a favorite folder via ``bili favorites <id> --json --page N``."""
    return _run_bili_cmd(["favorites", str(folder_id), "--json", "--page", str(page)])


def _fetch_all_favorites(max_pages_per_folder: int = 25) -> list[dict[str, Any]]:
    """Fetch all favorite folders and their videos.

    Each page returns 20 items. Default max_pages=25 → up to 500 per folder.
    """
    folders = _fetch_favorite_folders()
    if not folders:
        logger.warning("no bilibili favorite folders found")
        return []

    logger.info("found %d favorite folders", len(folders))
    all_items: list[dict[str, Any]] = []
    seen_bvids: set[str] = set()

    for folder in folders:
        folder_id = int(folder.get("id", 0) or 0)
        folder_title = str(folder.get("title", "") or "").strip()
        media_count = int(folder.get("media_count", 0) or 0)

        if folder_id == 0 or media_count == 0:
            logger.info(
                "skipping folder '%s' (id=%d, count=%d)", folder_title, folder_id, media_count
            )
            continue

        logger.info(
            "fetching folder '%s' (id=%d, ~%d videos)", folder_title, folder_id, media_count
        )
        folder_new = 0

        for page in range(1, max_pages_per_folder + 1):
            data = _fetch_favorite_folder(folder_id, page)
            page_data = data.get("data", {})
            items = page_data.get("items", [])
            has_more = page_data.get("has_more", False)

            if not items:
                break

            for item in items:
                bvid = str(item.get("bvid", "") or item.get("id", "") or "").strip()
                if bvid and bvid not in seen_bvids:
                    seen_bvids.add(bvid)
                    item["_folder_id"] = folder_id
                    item["_folder_title"] = folder_title
                    all_items.append(item)
                    folder_new += 1

            logger.info("  page %d: %d items (folder total new: %d)", page, len(items), folder_new)

            if not has_more:
                break
            time.sleep(1)  # rate limit

        logger.info("folder '%s' done: %d new unique videos", folder_title, folder_new)
        time.sleep(2)

    return all_items


def _fetch_watch_later() -> list[dict[str, Any]]:
    """Fetch watch-later list via ``bili watch-later --json``."""
    data = _run_bili_cmd(["watch-later", "--json"])
    items = data.get("data", [])
    if isinstance(items, dict):
        items = items.get("list", items.get("items", []))
    if not isinstance(items, list):
        return []
    logger.info("watch-later: %d items", len(items))
    return items


def _fetch_history(max_items: int = 100) -> list[dict[str, Any]]:
    """Fetch watch history via ``bili history --json --max N``."""
    data = _run_bili_cmd(["history", "--json", "--max", str(max_items)])
    items = data.get("data", [])
    if isinstance(items, dict):
        items = items.get("list", items.get("items", []))
    if not isinstance(items, list):
        return []
    logger.info("history: %d items", len(items))
    return items


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _parse_items(items: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Extract fields from bili items into content_cache rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items:
        bvid = str(item.get("bvid", "") or item.get("id", "") or "").strip()
        if not bvid or not _BVID_RE.match(bvid):
            continue
        if bvid in seen:
            continue
        seen.add(bvid)

        title = str(item.get("title", "") or "").strip()
        upper = item.get("upper", {}) or {}
        author = str(upper.get("name", "") or item.get("owner", {}).get("name", "") or "").strip()
        duration = str(item.get("duration", "") or item.get("length", "") or "").strip()
        int(item.get("duration_seconds", 0) or 0)

        # Stats (may not be present in favorites list)
        int(item.get("view", 0) or item.get("play", 0) or 0)
        like_count = int(item.get("like", 0) or item.get("stat", {}).get("like", 0) or 0)
        comment_count = int(
            item.get("reply", 0)
            or item.get("comment", 0)
            or item.get("stat", {}).get("reply", 0)
            or 0
        )
        favorite_count = int(
            item.get("favorite", 0) or item.get("stat", {}).get("favorite", 0) or 0
        )
        share_count = int(item.get("share", 0) or item.get("stat", {}).get("share", 0) or 0)

        # Folder context
        folder_title = str(item.get("_folder_title", "") or "").strip()

        if not title:
            title = f"B站视频 {bvid}"

        # Build body text
        body_parts = [title]
        if duration:
            body_parts.append(f"时长: {duration}")
        if folder_title and source == "bilibili-favorites":
            body_parts.append(f"[收藏夹: {folder_title}]")
        body_text = "\n".join(body_parts)[:2000]

        content_url = f"https://www.bilibili.com/video/{bvid}"

        rows.append(
            {
                "bvid": bvid,
                "title": title[:500],
                "up_name": author,
                "author_name": author,
                "content_url": content_url,
                "source_platform": "bilibili",
                "source": source,
                "content_type": "video",
                "pool_status": "fresh",
                "body_text": body_text,
                "like_count": like_count,
                "comment_count": comment_count,
                "favorite_count": favorite_count,
                "share_count": share_count,
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


def _run_once(
    *,
    favorites: bool = False,
    watch_later: bool = False,
    history: bool = False,
    max_history: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    all_rows: list[dict[str, Any]] = []
    stats: dict[str, int] = {}

    if favorites:
        logger.info("=== fetching bilibili favorites ===")
        items = _fetch_all_favorites()
        rows = _parse_items(items, "bilibili-favorites")
        stats["favorites_fetched"] = len(items)
        stats["favorites_valid"] = len(rows)
        all_rows.extend(rows)
        time.sleep(2)

    if watch_later:
        logger.info("=== fetching bilibili watch-later ===")
        items = _fetch_watch_later()
        rows = _parse_items(items, "bilibili-watch-later")
        stats["watch_later_fetched"] = len(items)
        stats["watch_later_valid"] = len(rows)
        all_rows.extend(rows)
        time.sleep(2)

    if history:
        logger.info("=== fetching bilibili history ===")
        items = _fetch_history(max_items=max_history)
        rows = _parse_items(items, "bilibili-history")
        stats["history_fetched"] = len(items)
        stats["history_valid"] = len(rows)
        all_rows.extend(rows)

    if not all_rows:
        return {"ok": False, "reason": "no_items", **stats, "inserted": 0}

    # Deduplicate across modes by bvid
    seen: set[str] = set()
    unique_rows: list[dict[str, Any]] = []
    for row in all_rows:
        if row["bvid"] not in seen:
            seen.add(row["bvid"])
            unique_rows.append(row)

    if dry_run:
        logger.info(
            "dry-run: %d total items, %d unique rows (not inserted)",
            len(all_rows),
            len(unique_rows),
        )
        return {
            "ok": True,
            **stats,
            "total_fetched": len(all_rows),
            "unique": len(unique_rows),
            "inserted": 0,
            "dry_run": True,
        }

    conn = _obc_connect(DB_PATH)
    try:
        inserted = _insert_rows(conn, unique_rows)
        conn.commit()
        return {
            "ok": True,
            **stats,
            "total_fetched": len(all_rows),
            "unique": len(unique_rows),
            "inserted": inserted,
            "skipped_duplicates": len(unique_rows) - inserted,
        }
    finally:
        conn.close()


def run_forever(
    *,
    favorites: bool = False,
    watch_later: bool = False,
    history: bool = False,
    max_history: int = 100,
    interval_hours: int = 24,
) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    modes = []
    if favorites:
        modes.append("favorites")
    if watch_later:
        modes.append("watch-later")
    if history:
        modes.append("history")
    guard = RateLimitGuard("bilibili-favorites", state_dir=PROJECT_ROOT / "data" / "rate_limit")
    logger.info(
        "bilibili personal content producer started (modes=%s, interval=%dh, rate-limit guard enabled)",
        ",".join(modes),
        interval_hours,
    )
    while True:
        if guard.should_skip():
            logger.info("sleeping %d hours (cooldown active)", interval_hours)
            time.sleep(interval_hours * 3600)
            continue
        try:
            result = _run_once(
                favorites=favorites,
                watch_later=watch_later,
                history=history,
                max_history=max_history,
            )
            if result.get("ok"):
                unique = result.get("unique", 0)
                if unique > 0:
                    guard.record_success()
                else:
                    guard.record_failure(
                        reason="empty_result", detail="bilibili returned 0 unique items"
                    )
                logger.info(
                    "bilibili ok: %d unique, %d new, %d duplicate",
                    unique,
                    result.get("inserted", 0),
                    result.get("skipped_duplicates", 0),
                )
            else:
                reason = result.get("reason", "unknown")
                detail = result.get("detail", "")
                guard.record_failure(reason=reason, detail=detail)
                logger.warning("bilibili skipped: %s", reason)
        except Exception as exc:
            guard.record_failure(reason="exception", detail=str(exc))
            logger.exception("bilibili cycle failed: %s", exc)
        logger.info("sleeping %d hours until next cycle", interval_hours)
        time.sleep(interval_hours * 3600)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Bilibili personal content producer (favorites/watch-later/history)"
    )
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--loop", action="store_true", help="Run forever (24h interval)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes")
    parser.add_argument("--favorites", action="store_true", help="Fetch favorite folders")
    parser.add_argument("--watch-later", action="store_true", help="Fetch watch-later list")
    parser.add_argument("--history", action="store_true", help="Fetch watch history")
    parser.add_argument(
        "--all", action="store_true", help="Fetch all modes (favorites + watch-later + history)"
    )
    parser.add_argument(
        "--max-history", type=int, default=100, help="Max history items (default: 100)"
    )
    parser.add_argument(
        "--interval", type=int, default=24, help="Loop interval in hours (default: 24)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Determine modes
    favorites = args.favorites or args.all
    watch_later = args.watch_later or args.all
    history = args.history or args.all

    # Default to favorites if no mode specified
    if not favorites and not watch_later and not history:
        favorites = True
        logger.info("no mode specified, defaulting to --favorites")

    if args.once or args.dry_run:
        result = _run_once(
            favorites=favorites,
            watch_later=watch_later,
            history=history,
            max_history=args.max_history,
            dry_run=args.dry_run,
        )
        if result.get("ok"):
            logger.info(
                "bilibili ok: %d unique, %d new, %d duplicate",
                result.get("unique", 0),
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
            )
        else:
            logger.warning("bilibili skipped: %s", result.get("reason", "unknown"))
        return

    if args.loop:
        run_forever(
            favorites=favorites,
            watch_later=watch_later,
            history=history,
            max_history=args.max_history,
            interval_hours=args.interval,
        )
        return

    parser.print_help()


if __name__ == "__main__":
    _main()
