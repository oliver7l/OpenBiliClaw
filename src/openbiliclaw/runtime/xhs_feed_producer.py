"""Xiaohongshu recommendation feed scheduler.

Calls ``xhs feed --json`` every 3 hours and inserts new notes into
``content_cache`` with fresh ``xsec_token``, so they become available
in the recommendation pool immediately.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliclaw/data/openbiliclaw.db"
INTERVAL_HOURS = 3
FEED_CMD = [
    "uvx",
    "--from",
    "xiaohongshu-cli",
    "xhs",
    "feed",
    "--json",
]
CLEAN_ENV = os.environ.copy()
CLEAN_ENV["PYTHONHOME"] = ""
CLEAN_ENV["PYTHONPATH"] = ""

# Used to extract note_id from the note_card's potential id field
_NOTE_ID_RE = re.compile(r"^[0-9a-f]{24}$")


def _last_fetch_time(conn: sqlite3.Connection) -> datetime | None:
    row = conn.execute(
        "SELECT MAX(discovered_at) FROM content_cache WHERE source = 'xhs-feed'"
    ).fetchone()
    if row and row[0]:
        try:
            return datetime.strptime(str(row[0]), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _fetch_feed() -> list[dict]:
    """Call ``xhs feed --json`` and return the parsed items."""
    result = subprocess.run(
        FEED_CMD,
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
        timeout=60,
    )
    if result.returncode != 0:
        logger.error("xhs feed failed (rc=%d): %s", result.returncode, result.stderr[:500])
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("xhs feed JSON parse error: %s", exc)
        return []
    items = data.get("data", {}).get("items", [])
    if not items:
        logger.info("xhs feed returned 0 items")
        return []
    return items


def _parse_likes(raw: str) -> int:
    """Parse Xiaohongshu like count format (e.g. '7.2万' → 72000)."""
    raw = raw.strip().lower()
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


def _parse_items(items: list[dict]) -> list[dict]:
    """Extract fields from feed items into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict] = []
    seen: set[str] = set()
    for item in items:
        note_id = str(item.get("id", "")).strip()
        if not note_id or not _NOTE_ID_RE.match(note_id):
            continue
        if note_id in seen:
            continue
        seen.add(note_id)

        xsec_token = str(item.get("xsec_token", "")).strip()
        nc = item.get("note_card", {}) or {}
        title = str(nc.get("display_title", "") or "").strip()
        user = nc.get("user", {}) or {}
        author = str(user.get("nickname", "") or "").strip()
        interact = nc.get("interact_info", {}) or {}
        likes_raw = str(interact.get("liked_count", "0") or "0").strip()
        likes = _parse_likes(likes_raw)

        if not xsec_token:
            continue

        content_url = (
            f"https://www.xiaohongshu.com/explore/{note_id}"
            f"?xsec_token={xsec_token}&xsec_source=pc_feed"
        )

        rows.append({
            "bvid": note_id,
            "title": title,
            "up_name": author,
            "author_name": author,
            "content_url": content_url,
            "source_platform": "xiaohongshu",
            "source": "xhs-feed",
            "content_type": "note",
            "pool_status": "fresh",
            "like_count": likes,
            "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return rows


def _insert_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert new rows, skip duplicates by bvid."""
    inserted = 0
    for row in rows:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    like_count, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["bvid"], row["title"], row["up_name"], row["author_name"],
                    row["content_url"], row["source_platform"], row["source"],
                    row["content_type"], row["pool_status"], row["like_count"],
                    row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            continue
    return inserted


def _run_once() -> dict:
    """One full fetch cycle. Returns a summary dict."""
    items = _fetch_feed()
    if not items:
        return {"ok": False, "reason": "empty_feed", "items_fetched": 0, "inserted": 0}

    rows = _parse_items(items)
    if not rows:
        return {"ok": False, "reason": "no_valid_items", "items_fetched": len(items), "inserted": 0}

    conn = sqlite3.connect(DB_PATH)
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


def run_forever() -> None:
    """Main loop: fetch every 3 hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("xhs feed producer started (interval=%dh)", INTERVAL_HOURS)

    while True:
        logger.info("fetching xhs feed...")
        result = _run_once()
        if result["ok"]:
            logger.info(
                "feed ok: %d fetched, %d new, %d duplicate",
                result["items_fetched"],
                result["inserted"],
                result["skipped_duplicates"],
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))

        time.sleep(INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    run_forever()