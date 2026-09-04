"""YouTube recommendation feed scheduler.

Calls ``yt-dlp --cookies-from-browser chrome`` to fetch the YouTube
recommended feed (same as youtube.com homepage) every 24 hours, and inserts
new videos into ``content_cache`` so they become available in the
recommendation pool.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
CLEAN_ENV = os.environ.copy()
CLEAN_ENV["PYTHONHOME"] = ""
CLEAN_ENV["PYTHONPATH"] = ""
# 清掉代理环境变量：本机代理（Clash 类）经常重启/挂掉，挂掉时 yt-dlp 走代理会拿到
# "Unable to connect to proxy ... 502 Bad Gateway"，表现为 "yt-dlp returned 0 items"
# （退出码仍是 0，所以不会报错、只会被当成空 feed 跳过，很难发现）。
# 实测清掉代理后直连可正常抓取推荐页（与 cli.py 的 _strip_proxy_env 同一思路）。
for _proxy_key in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    CLEAN_ENV.pop(_proxy_key, None)

YT_DLP_CMD = [
    "yt-dlp",
    "--cookies-from-browser",
    "chrome",
    "--flat-playlist",
    "--print",
    "%(title)s|%(uploader)s|%(view_count)s|%(webpage_url)s",
    "https://www.youtube.com/feed/recommended",
]

# YouTube video ID: 11 characters, [a-zA-Z0-9_-]
_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def _fetch_feed() -> list[dict[str, Any]]:
    """Call yt-dlp and return parsed items."""
    result = subprocess.run(
        YT_DLP_CMD,
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
        timeout=120,
    )
    if result.returncode != 0:
        logger.error("yt-dlp failed (rc=%d): %s", result.returncode, result.stderr[:500])
        return []

    lines = [ln.strip() for ln in result.stdout.split("\n") if ln.strip()]
    if not lines:
        logger.info("yt-dlp returned 0 items")
        return []

    items: list[dict[str, Any]] = []
    for line in lines:
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        title, uploader, view_count_str, url = parts
        if not title.strip():
            continue

        url = url.strip()
        vid = _extract_video_id(url)
        if not vid:
            continue

        view_count = 0
        try:
            view_count = int(view_count_str) if view_count_str != "NA" else 0
        except ValueError:
            view_count = 0

        items.append(
            {
                "title": title.strip(),
                "uploader": uploader.strip() if uploader != "NA" else "",
                "view_count": view_count,
                "url": url,
                "video_id": vid,
            }
        )
    return items


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    m = re.search(r"(?:v=|youtu\.be/|/v/|embed/|shorts/)([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    return None


def _parse_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        vid = item["video_id"]
        if vid in seen:
            continue
        seen.add(vid)

        title = item["title"]
        if not title:
            continue

        rows.append(
            {
                "bvid": vid,
                "title": title,
                "up_name": item["uploader"],
                "author_name": item["uploader"],
                "content_url": item["url"],
                "source_platform": "youtube",
                "source": "youtube-feed",
                "content_type": "video",
                "pool_status": "fresh",
                "view_count": item["view_count"],
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def _insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Insert new rows, skip duplicates by bvid."""
    inserted = 0
    for row in rows:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    view_count, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    row["view_count"],
                    row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            continue
    return inserted


def _run_once() -> dict[str, Any]:
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
    """Main loop: fetch every 24 hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("youtube feed producer started (interval=%dh)", INTERVAL_HOURS)

    while True:
        logger.info("fetching youtube recommend...")
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
