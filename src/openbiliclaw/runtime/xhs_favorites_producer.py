"""Xiaohongshu favorites (bookmarked notes) producer.

Fetches the logged-in user's bookmarked notes via ``xhs favorites --json``
and inserts new notes into ``content_cache`` (pool.db). Runs once by
default; use ``--loop`` for continuous 24h-interval background mode.

Usage:
    python3 -m openbiliclaw.runtime.xhs_favorites_producer --once
    python3 -m openbiliclaw.runtime.xhs_favorites_producer --once --pages 5
    python3 -m openbiliclaw.runtime.xhs_favorites_producer --loop
    python3 -m openbiliclaw.runtime.xhs_favorites_producer --dry-run
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
FAVORITES_CMD = ["xhs", "favorites", "--json"]

# xhs favorites returns note_id like "6a2e6a78000000002101b784"
_NOTE_ID_RE = re.compile(r"^[0-9a-f]{24}$")

# Clean environment so xhs CLI finds its config regardless of cwd
CLEAN_ENV = {
    **os.environ,
    "PYTHONIOENCODING": "utf-8",
    "LANG": os.environ.get("LANG", "en_US.UTF-8"),
}


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _obc_connect(db_path: Path) -> sqlite3.Connection:
    """Connect to pool.db directly (favorites live in the recommendation pool)."""
    conn = sqlite3.connect(str(db_path))
    return conn


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def _fetch_favorites_page(cursor: str | None = None) -> dict[str, Any]:
    """Call ``xhs favorites --json [--cursor X]`` and return parsed JSON."""
    cmd = list(FAVORITES_CMD)
    if cursor:
        cmd.extend(["--cursor", cursor])
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
        timeout=60,
    )
    if result.returncode != 0:
        logger.error("xhs favorites failed (rc=%d): %s", result.returncode, result.stderr[:500])
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("xhs favorites JSON parse error: %s", exc)
        return {}
    if not data.get("ok"):
        logger.warning("xhs favorites returned ok=false: %s", str(data)[:300])
        return {}
    return data


def _fetch_all_favorites(max_pages: int = 3) -> list[dict[str, Any]]:
    """Fetch multiple pages of favorites using cursor pagination."""
    all_notes: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_ids: set[str] = set()

    for page in range(max_pages):
        logger.info(
            "fetching xhs favorites page %d/%d (cursor=%s)",
            page + 1,
            max_pages,
            cursor or "initial",
        )
        data = _fetch_favorites_page(cursor)
        if not data:
            break

        notes = data.get("data", {}).get("notes", [])
        if not notes:
            logger.info("page %d returned 0 notes, stopping", page + 1)
            break

        new_count = 0
        for note in notes:
            note_id = str(note.get("note_id", "") or "").strip()
            if note_id and note_id not in seen_ids:
                seen_ids.add(note_id)
                all_notes.append(note)
                new_count += 1

        logger.info("page %d: %d notes (%d new unique)", page + 1, len(notes), new_count)

        has_more = data.get("data", {}).get("has_more", False)
        next_cursor = data.get("data", {}).get("cursor", "")
        if not has_more or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(2)  # be gentle with rate limits

    return all_notes


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------


def _parse_likes(raw: str) -> int:
    """Parse Xiaohongshu like count format (e.g. '7.2万' → 72000)."""
    raw = str(raw or "").strip().lower()
    if not raw:
        return 0
    try:
        return int(raw)
    except ValueError:
        pass
    if "万" in raw:
        num = raw.replace("万", "").strip()
        try:
            return int(float(num) * 10000)
        except ValueError:
            return 0
    if "w" in raw:
        num = raw.replace("w", "").strip()
        try:
            return int(float(num) * 10000)
        except ValueError:
            return 0
    return 0


def _parse_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields from favorites items into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items:
        note_id = str(item.get("note_id", "") or "").strip()
        if not note_id or not _NOTE_ID_RE.match(note_id):
            continue
        if note_id in seen:
            continue
        seen.add(note_id)

        xsec_token = str(item.get("xsec_token", "") or "").strip()
        title = str(item.get("display_title", "") or "").strip()
        user = item.get("user", {}) or {}
        author = str(user.get("nickname", "") or "").strip()
        interact = item.get("interact_info", {}) or {}
        likes_raw = str(interact.get("liked_count", "0") or "0").strip()
        likes = _parse_likes(likes_raw)

        if not xsec_token:
            # favorites without xsec_token still have note_id; build URL without token
            content_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        else:
            content_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={xsec_token}&xsec_source=pc_favorites"

        note_type = str(item.get("type", "normal") or "").strip()
        content_type = "image-note" if note_type == "normal" else "video"

        if not title:
            title = f"小红书笔记 {note_id}"

        rows.append(
            {
                "bvid": note_id,
                "title": title[:500],
                "up_name": author,
                "author_name": author,
                "content_url": content_url,
                "source_platform": "xiaohongshu",
                "source": "xhs-favorites",
                "content_type": content_type,
                "pool_status": "fresh",
                "body_text": title[:2000],
                "like_count": likes,
                "comment_count": 0,
                "favorite_count": 0,
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


def _run_once(pages: int = 3, dry_run: bool = False) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    items = _fetch_all_favorites(max_pages=pages)
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


def run_forever(pages: int = 3, interval_hours: int = 24) -> None:
    """Main loop: fetch every ``interval_hours`` hours with rate-limit guard."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    guard = RateLimitGuard("xhs-favorites", state_dir=PROJECT_ROOT / "data" / "rate_limit")
    logger.info(
        "xhs favorites producer started (interval=%dh, pages=%d, rate-limit guard enabled)",
        interval_hours,
        pages,
    )
    while True:
        # Rate-limit guard check
        if guard.should_skip():
            logger.info("sleeping %d hours (cooldown active)", interval_hours)
            time.sleep(interval_hours * 3600)
            continue

        try:
            result = _run_once(pages=pages)
            if result.get("ok"):
                inserted = result.get("inserted", 0)
                fetched = result.get("items_fetched", 0)
                # Success only if we actually got data back (0 fetched may indicate auth failure)
                if fetched > 0:
                    guard.record_success()
                else:
                    guard.record_failure(
                        reason="empty_result", detail="xhs favorites returned 0 items"
                    )
                logger.info(
                    "xhs favorites ok: %d fetched, %d new, %d duplicate",
                    fetched,
                    inserted,
                    result.get("skipped_duplicates", 0),
                )
            else:
                reason = result.get("reason", "unknown")
                detail = result.get("detail", "")
                guard.record_failure(reason=reason, detail=detail)
                logger.warning("xhs favorites skipped: %s", reason)
        except Exception as exc:
            guard.record_failure(reason="exception", detail=str(exc))
            logger.exception("xhs favorites cycle failed: %s", exc)
        logger.info("sleeping %d hours until next cycle", interval_hours)
        time.sleep(interval_hours * 3600)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(description="Xiaohongshu favorites producer")
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit")
    parser.add_argument("--loop", action="store_true", help="Run forever (24h interval)")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes")
    parser.add_argument(
        "--pages", type=int, default=3, help="Max pages to fetch per cycle (default: 3)"
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
        result = _run_once(pages=args.pages, dry_run=args.dry_run)
        if result.get("ok"):
            logger.info(
                "xhs favorites ok: %d fetched, %d new, %d duplicate",
                result.get("items_fetched", 0),
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
            )
        else:
            logger.warning("xhs favorites skipped: %s", result.get("reason", "unknown"))
        return

    if args.loop:
        run_forever(pages=args.pages, interval_hours=args.interval)
        return

    parser.print_help()


if __name__ == "__main__":
    _main()
