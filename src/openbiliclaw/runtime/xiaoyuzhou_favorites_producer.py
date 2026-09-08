"""Xiaoyuzhou (小宇宙) personal content producer — subscriptions and play history.

Fetches the logged-in user's Xiaoyuzhou podcast subscriptions and play
history via the ``xyz`` CLI and inserts new items into ``content_cache``
(pool.db).

Usage:
    python3 -m openbiliclaw.runtime.xiaoyuzhou_favorites_producer --once --subs
    python3 -m openbiliclaw.runtime.xiaoyuzhou_favorites_producer --once --all
    python3 -m openbiliclaw.runtime.xiaoyuzhou_favorites_producer --loop --all
    python3 -m openbiliclaw.runtime.xiaoyuzhou_favorites_producer --dry-run --history
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

from openbiliclaw.runtime._db import connect_pool as _obc_connect
from openbiliclaw.runtime.rate_limit_guard import RateLimitGuard

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "pool.db"

# Clean environment so xyz CLI finds its config regardless of cwd
CLEAN_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "LANG": os.environ.get("LANG", "en_US.UTF-8"),
}

# Xiaoyuzhou ID patterns (hex strings)
_PID_RE = re.compile(r"^[0-9a-f]{20,30}$")
_EID_RE = re.compile(r"^[0-9a-f]{20,30}$")


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fetch helpers
# ---------------------------------------------------------------------------


def _run_xyz_cmd(args: list[str], timeout: int = 60) -> list[dict[str, Any]]:
    """Run an xyz CLI command with --jsonl and return parsed list."""
    result = subprocess.run(
        ["xyz", *args],
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.error(
            "xyz %s failed (rc=%d): %s", " ".join(args[:3]), result.returncode, result.stderr[:500]
        )
        return []
    items: list[dict[str, Any]] = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return items


def _fetch_subscriptions() -> list[dict[str, Any]]:
    """Fetch subscribed podcasts via ``xyz subs --jsonl``."""
    items = _run_xyz_cmd(["subs", "--jsonl"])
    logger.info("xiaoyuzhou subscriptions: %d podcasts", len(items))
    return items


def _fetch_history(limit: int = 100) -> list[dict[str, Any]]:
    """Fetch play history via ``xyz history --limit N --jsonl``."""
    items = _run_xyz_cmd(["history", "--limit", str(limit), "--jsonl"])
    logger.info("xiaoyuzhou history: %d episodes", len(items))
    return items


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _parse_subscriptions(podcasts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields from subscribed podcasts into content_cache rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for pod in podcasts:
        pid = str(pod.get("pid", "") or "").strip()
        if not pid or not _PID_RE.match(pid):
            continue
        if pid in seen:
            continue
        seen.add(pid)

        title = str(pod.get("title", "") or "").strip()
        author = str(pod.get("author", "") or "").strip()
        brief = str(pod.get("brief", "") or "").strip()
        subscription_count = int(pod.get("subscription_count", 0) or 0)
        episode_count = int(pod.get("episode_count", 0) or 0)
        latest_pub = str(pod.get("latest_episode_pub_date", "") or "").strip()
        has_unread = bool(pod.get("has_unread", False))
        str(pod.get("cover_url", "") or "").strip()

        if not title:
            title = f"小宇宙播客 {pid}"

        # Build body text
        body_parts = []
        if brief:
            body_parts.append(brief)
        body_parts.append(f"订阅数: {subscription_count:,}")
        body_parts.append(f"单集数: {episode_count}")
        if latest_pub:
            body_parts.append(f"最新更新: {latest_pub}")
        if has_unread:
            body_parts.append("有未读单集")
        body_text = "\n".join(body_parts)[:3000]

        content_url = f"https://www.xiaoyuzhoufm.com/podcast/{pid}"

        rows.append(
            {
                "bvid": pid,
                "title": title[:500],
                "up_name": author,
                "author_name": author,
                "content_url": content_url,
                "source_platform": "xiaoyuzhou",
                "source": "xyz-subs",
                "content_type": "podcast",
                "pool_status": "fresh",
                "body_text": body_text,
                "like_count": subscription_count,
                "comment_count": 0,
                "favorite_count": episode_count,
                "share_count": 0,
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def _parse_history(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields from play history episodes into content_cache rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for ep in episodes:
        eid = str(ep.get("eid", "") or "").strip()
        if not eid or not _EID_RE.match(eid):
            continue
        if eid in seen:
            continue
        seen.add(eid)

        str(ep.get("pid", "") or "").strip()
        podcast_title = str(ep.get("podcast_title", "") or "").strip()
        title = str(ep.get("title", "") or "").strip()
        duration_seconds = int(ep.get("duration_seconds", 0) or 0)
        is_finished = bool(ep.get("is_finished", False))
        is_played = bool(ep.get("is_played", False))
        pub_date = str(ep.get("pub_date", "") or "").strip()

        if not title:
            title = f"小宇宙单集 {eid}"

        # Format duration
        duration_min = duration_seconds // 60
        duration_str = f"{duration_min}分钟" if duration_min > 0 else ""

        # Build body text
        body_parts = []
        if podcast_title:
            body_parts.append(f"播客: {podcast_title}")
        if duration_str:
            body_parts.append(f"时长: {duration_str}")
        if is_finished:
            body_parts.append("已听完")
        elif is_played:
            body_parts.append("播放过")
        if pub_date:
            body_parts.append(f"发布时间: {pub_date}")
        body_text = "\n".join(body_parts)[:3000]

        content_url = f"https://www.xiaoyuzhoufm.com/episode/{eid}"

        rows.append(
            {
                "bvid": eid,
                "title": title[:500],
                "up_name": podcast_title,
                "author_name": podcast_title,
                "content_url": content_url,
                "source_platform": "xiaoyuzhou",
                "source": "xyz-history",
                "content_type": "podcast-episode",
                "pool_status": "fresh",
                "body_text": body_text,
                "like_count": 0,
                "comment_count": 0,
                "favorite_count": 1 if is_finished else 0,
                "share_count": 0,
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
    subs: bool = False,
    history: bool = False,
    history_limit: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    all_rows: list[dict[str, Any]] = []
    stats: dict[str, int] = {}

    if subs:
        logger.info("=== fetching xiaoyuzhou subscriptions ===")
        podcasts = _fetch_subscriptions()
        rows = _parse_subscriptions(podcasts)
        stats["subs_fetched"] = len(podcasts)
        stats["subs_valid"] = len(rows)
        all_rows.extend(rows)
        time.sleep(2)

    if history:
        logger.info("=== fetching xiaoyuzhou play history ===")
        episodes = _fetch_history(limit=history_limit)
        rows = _parse_history(episodes)
        stats["history_fetched"] = len(episodes)
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
    subs: bool = False,
    history: bool = False,
    history_limit: int = 100,
    interval_hours: int = 24,
) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    modes = []
    if subs:
        modes.append("subs")
    if history:
        modes.append("history")
    guard = RateLimitGuard("xiaoyuzhou-favorites", state_dir=PROJECT_ROOT / "data" / "rate_limit")
    logger.info(
        "xiaoyuzhou personal content producer started (modes=%s, interval=%dh, rate-limit guard enabled)",  # noqa: E501
        ",".join(modes),
        interval_hours,
    )
    while True:
        if guard.should_skip():
            logger.info("sleeping %d hours (cooldown active)", interval_hours)
            time.sleep(interval_hours * 3600)
            continue
        try:
            result = _run_once(subs=subs, history=history, history_limit=history_limit)
            if result.get("ok"):
                unique = result.get("unique", 0)
                if unique > 0:
                    guard.record_success()
                else:
                    guard.record_failure(
                        reason="empty_result", detail="xiaoyuzhou returned 0 unique items"
                    )
                logger.info(
                    "xiaoyuzhou ok: %d unique, %d new, %d duplicate",
                    unique,
                    result.get("inserted", 0),
                    result.get("skipped_duplicates", 0),
                )
            else:
                reason = result.get("reason", "unknown")
                detail = result.get("detail", "")
                guard.record_failure(reason=reason, detail=detail)
                logger.warning("xiaoyuzhou skipped: %s", reason)
        except Exception as exc:
            guard.record_failure(reason="exception", detail=str(exc))
            logger.exception("xiaoyuzhou cycle failed: %s", exc)
        logger.info("sleeping %d hours until next cycle", interval_hours)
        time.sleep(interval_hours * 3600)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Xiaoyuzhou personal content producer (subscriptions/history)"
    )
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--loop", action="store_true", help="Run forever (24h interval)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes")
    parser.add_argument("--subs", action="store_true", help="Fetch subscribed podcasts")
    parser.add_argument("--history", action="store_true", help="Fetch play history")
    parser.add_argument("--all", action="store_true", help="Fetch all modes (subs + history)")
    parser.add_argument(
        "--history-limit", type=int, default=100, help="Max history items (default: 100)"
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
    subs = args.subs or args.all
    history = args.history or args.all

    # Default to all if no mode specified
    if not subs and not history:
        subs = True
        history = True
        logger.info("no mode specified, defaulting to --all")

    if args.once or args.dry_run:
        result = _run_once(
            subs=subs, history=history, history_limit=args.history_limit, dry_run=args.dry_run
        )
        if result.get("ok"):
            logger.info(
                "xiaoyuzhou ok: %d unique, %d new, %d duplicate",
                result.get("unique", 0),
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
            )
        else:
            logger.warning("xiaoyuzhou skipped: %s", result.get("reason", "unknown"))
        return

    if args.loop:
        run_forever(
            subs=subs,
            history=history,
            history_limit=args.history_limit,
            interval_hours=args.interval,
        )
        return

    parser.print_help()


if __name__ == "__main__":
    _main()
