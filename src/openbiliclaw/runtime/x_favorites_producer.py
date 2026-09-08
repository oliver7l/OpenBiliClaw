"""X/Twitter personal content producer — likes and bookmarks.

Fetches the logged-in user's X/Twitter liked tweets and bookmarked tweets
via ``XClient`` (server-side cookie replay) and inserts new items into
``content_cache`` (pool.db).

Usage:
    python3 -m openbiliclaw.runtime.x_favorites_producer --once --likes
    python3 -m openbiliclaw.runtime.x_favorites_producer --once --all
    python3 -m openbiliclaw.runtime.x_favorites_producer --loop --all
    python3 -m openbiliclaw.runtime.x_favorites_producer --dry-run --likes
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from openbiliclaw.runtime._db import connect_pool as _obc_connect
from openbiliclaw.runtime.rate_limit_guard import RateLimitGuard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pool.db"
DATA_DIR = PROJECT_ROOT / "data"

# X tweet ID pattern (numeric, up to ~20 digits)
_TWEET_ID_RE = __import__("re").compile(r"^\d{10,25}$")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fetch (async)
# ---------------------------------------------------------------------------


async def _fetch_likes(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch liked tweets via XClient.likes()."""
    from openbiliclaw.sources.x_auth import resolve_x_cookie
    from openbiliclaw.sources.x_client import XClient

    cookie = resolve_x_cookie(data_dir=DATA_DIR, cookie_env="OPENBILICLAW_X_COOKIE")
    if not cookie:
        logger.warning("no X cookie available")
        return []
    client = XClient(cookie=cookie)
    try:
        tweets = await client.likes(limit=limit)
        logger.info("x likes: %d tweets", len(tweets))
        return tweets
    except Exception as exc:
        logger.error("x likes fetch failed: %s", exc)
        return []


async def _fetch_bookmarks(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch bookmarked tweets via XClient.bookmarks()."""
    from openbiliclaw.sources.x_auth import resolve_x_cookie
    from openbiliclaw.sources.x_client import XClient

    cookie = resolve_x_cookie(data_dir=DATA_DIR, cookie_env="OPENBILICLAW_X_COOKIE")
    if not cookie:
        logger.warning("no X cookie available")
        return []
    client = XClient(cookie=cookie)
    try:
        tweets = await client.bookmarks(limit=limit)
        logger.info("x bookmarks: %d tweets", len(tweets))
        return tweets
    except Exception as exc:
        logger.error("x bookmarks fetch failed: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _parse_tweets(tweets: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Extract fields from X tweets into content_cache rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for tweet in tweets:
        tweet_id = str(tweet.get("id", "") or "").strip()
        if not tweet_id or not _TWEET_ID_RE.match(tweet_id):
            continue
        if tweet_id in seen:
            continue
        seen.add(tweet_id)

        text = str(tweet.get("text", "") or "").strip()
        author = tweet.get("author", {}) or {}
        author_name = str(author.get("name", "") or "").strip()
        author_handle = str(author.get("screenName", "") or "").strip()
        author_display = f"{author_name} (@{author_handle})" if author_handle else author_name

        metrics = tweet.get("metrics", {}) or {}
        like_count = int(metrics.get("likes", 0) or 0)
        retweet_count = int(metrics.get("retweets", 0) or 0)
        reply_count = int(metrics.get("replies", 0) or 0)
        quote_count = int(metrics.get("quotes", 0) or 0)
        bookmark_count = int(metrics.get("bookmarks", 0) or 0)
        int(metrics.get("views", 0) or 0)

        created_at = str(
            tweet.get("createdAtLocal", "") or tweet.get("createdAt", "") or ""
        ).strip()
        lang = str(tweet.get("lang", "") or "").strip()
        is_retweet = bool(tweet.get("isRetweet", False))
        retweeted_by = str(tweet.get("retweetedBy", "") or "").strip()

        # Build title (first 100 chars of text)
        title = text[:100].replace("\n", " ").strip()
        if not title:
            title = f"X推文 {tweet_id}"

        # Build body text
        body_parts = []
        if text:
            body_parts.append(text)
        if created_at:
            body_parts.append(f"发布时间: {created_at}")
        if lang:
            body_parts.append(f"语言: {lang}")
        if is_retweet and retweeted_by:
            body_parts.append(f"转发自: {retweeted_by}")
        body_text = "\n\n".join(body_parts)[:5000]

        content_url = f"https://x.com/i/web/status/{tweet_id}"
        if author_handle:
            content_url = f"https://x.com/{author_handle}/status/{tweet_id}"

        rows.append(
            {
                "bvid": tweet_id,
                "title": title[:500],
                "up_name": author_display,
                "author_name": author_display,
                "content_url": content_url,
                "source_platform": "x",
                "source": source,
                "content_type": "tweet",
                "pool_status": "fresh",
                "body_text": body_text,
                "like_count": like_count,
                "comment_count": reply_count,
                "favorite_count": bookmark_count,
                "share_count": retweet_count + quote_count,
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
    likes: bool = False,
    bookmarks: bool = False,
    limit: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    all_rows: list[dict[str, Any]] = []
    stats: dict[str, int] = {}

    if likes:
        logger.info("=== fetching x likes ===")
        tweets = asyncio.run(_fetch_likes(limit=limit))
        rows = _parse_tweets(tweets, "x-likes")
        stats["likes_fetched"] = len(tweets)
        stats["likes_valid"] = len(rows)
        all_rows.extend(rows)
        time.sleep(2)

    if bookmarks:
        logger.info("=== fetching x bookmarks ===")
        tweets = asyncio.run(_fetch_bookmarks(limit=limit))
        rows = _parse_tweets(tweets, "x-bookmarks")
        stats["bookmarks_fetched"] = len(tweets)
        stats["bookmarks_valid"] = len(rows)
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
    likes: bool = False,
    bookmarks: bool = False,
    limit: int = 100,
    interval_hours: int = 24,
) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    modes = []
    if likes:
        modes.append("likes")
    if bookmarks:
        modes.append("bookmarks")
    guard = RateLimitGuard("x-favorites", state_dir=PROJECT_ROOT / "data" / "rate_limit")
    logger.info(
        "x personal content producer started (modes=%s, interval=%dh, rate-limit guard enabled)",
        ",".join(modes),
        interval_hours,
    )
    while True:
        if guard.should_skip():
            logger.info("sleeping %d hours (cooldown active)", interval_hours)
            time.sleep(interval_hours * 3600)
            continue
        try:
            result = _run_once(likes=likes, bookmarks=bookmarks, limit=limit)
            if result.get("ok"):
                unique = result.get("unique", 0)
                if unique > 0:
                    guard.record_success()
                else:
                    guard.record_failure(reason="empty_result", detail="x returned 0 unique items")
                logger.info(
                    "x ok: %d unique, %d new, %d duplicate",
                    unique,
                    result.get("inserted", 0),
                    result.get("skipped_duplicates", 0),
                )
            else:
                reason = result.get("reason", "unknown")
                detail = result.get("detail", "")
                guard.record_failure(reason=reason, detail=detail)
                logger.warning("x skipped: %s", reason)
        except Exception as exc:
            guard.record_failure(reason="exception", detail=str(exc))
            logger.exception("x cycle failed: %s", exc)
        logger.info("sleeping %d hours until next cycle", interval_hours)
        time.sleep(interval_hours * 3600)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="X/Twitter personal content producer (likes/bookmarks)"
    )
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--loop", action="store_true", help="Run forever (24h interval)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes")
    parser.add_argument("--likes", action="store_true", help="Fetch liked tweets")
    parser.add_argument("--bookmarks", action="store_true", help="Fetch bookmarked tweets")
    parser.add_argument("--all", action="store_true", help="Fetch all modes (likes + bookmarks)")
    parser.add_argument("--limit", type=int, default=100, help="Max items per mode (default: 100)")
    parser.add_argument(
        "--interval", type=int, default=24, help="Loop interval in hours (default: 24)"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Determine modes
    likes = args.likes or args.all
    bookmarks = args.bookmarks or args.all

    # Default to likes if no mode specified
    if not likes and not bookmarks:
        likes = True
        logger.info("no mode specified, defaulting to --likes")

    if args.once or args.dry_run:
        result = _run_once(likes=likes, bookmarks=bookmarks, limit=args.limit, dry_run=args.dry_run)
        if result.get("ok"):
            logger.info(
                "x ok: %d unique, %d new, %d duplicate",
                result.get("unique", 0),
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
            )
        else:
            logger.warning("x skipped: %s", result.get("reason", "unknown"))
        return

    if args.loop:
        run_forever(
            likes=likes, bookmarks=bookmarks, limit=args.limit, interval_hours=args.interval
        )
        return

    parser.print_help()


if __name__ == "__main__":
    _main()
