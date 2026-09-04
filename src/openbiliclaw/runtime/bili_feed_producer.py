"""Bilibili recommendation feed scheduler.

Calls the Bilibili recommend API (same as bilibili.com homepage) every 24 hours
using the SESSDATA from the bili CLI credential store, and inserts new videos
into ``content_cache`` so they become available in the recommendation pool.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24

# 直连 opener：urllib 默认会读 env 里的 http(s)_proxy（本机 Clash 类代理），
# 代理挂掉时请求失败、抓取静默返回空，很难排查。这里显式禁用代理，直连更稳。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CREDENTIAL_PATH = Path.home() / ".bilibili-cli" / "credential.json"
RECOMMEND_URL = "https://api.bilibili.com/x/web-interface/index/top/feed/rcmd?y_num=5&fresh_type=4&fresh_idx=1&fresh_idx_1h=1"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def _load_sessdata() -> str | None:
    """Read SESSDATA from bili CLI credential file."""
    try:
        cred = json.loads(CREDENTIAL_PATH.read_text())
        sessdata: str = str(cred.get("sessdata", "")).strip()
        if sessdata:
            return sessdata
        logger.warning("credential file has no sessdata field")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.error("failed to read credential: %s", exc)
    return None


def _fetch_feed() -> list[dict[str, Any]]:
    """Call the Bilibili recommend API and return parsed items."""
    sessdata = _load_sessdata()
    if not sessdata:
        logger.error("no SESSDATA available, cannot fetch bilibili recommend")
        return []

    headers = {**REQUEST_HEADERS, "Cookie": f"SESSDATA={sessdata}"}
    req = urllib.request.Request(RECOMMEND_URL, headers=headers)

    try:
        with _NO_PROXY_OPENER.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("bilibili recommend API request failed: %s", exc)
        return []

    if data.get("code") != 0:
        logger.warning("bilibili recommend API error: %s", data.get("message", "unknown"))
        return []

    items: list[dict[str, Any]] = cast("list[dict[str, Any]]", data.get("data", {}).get("item", []))
    if not items:
        logger.info("bilibili recommend returned 0 items")
        return []
    return items


def _format_duration(seconds: int) -> str:
    """Convert seconds to mm:ss or hh:mm:ss format."""
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields from recommend items into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        bvid = str(item.get("bvid", "")).strip()
        if not bvid or not bvid.startswith("BV"):
            continue
        if bvid in seen:
            continue
        seen.add(bvid)

        owner = item.get("owner", {}) or {}
        stat = item.get("stat", {}) or {}

        title = str(item.get("title", "") or "").strip()
        if not title:
            continue

        content_url = f"https://www.bilibili.com/video/{bvid}"
        pic = str(item.get("pic", "") or "").strip()

        rows.append(
            {
                "bvid": bvid,
                "title": title,
                "up_name": str(owner.get("name", "") or "").strip(),
                "author_name": str(owner.get("name", "") or "").strip(),
                "up_mid": int(owner.get("mid", 0) or 0),
                "content_url": content_url,
                "cover_url": pic,
                "source_platform": "bilibili",
                "source": "bili-feed",
                "content_type": "video",
                "pool_status": "fresh",
                "duration": int(item.get("duration", 0) or 0),
                "view_count": int(stat.get("view", 0) or 0),
                "like_count": int(stat.get("like", 0) or 0),
                "danmaku_count": int(stat.get("danmaku", 0) or 0),
                "description": str(item.get("desc", "") or "").strip(),
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
                    bvid, title, up_name, author_name, up_mid, content_url,
                    cover_url, source_platform, source, content_type, pool_status,
                    duration, view_count, like_count, danmaku_count, description,
                    discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["bvid"],
                    row["title"],
                    row["up_name"],
                    row["author_name"],
                    row["up_mid"],
                    row["content_url"],
                    row["cover_url"],
                    row["source_platform"],
                    row["source"],
                    row["content_type"],
                    row["pool_status"],
                    row["duration"],
                    row["view_count"],
                    row["like_count"],
                    row["danmaku_count"],
                    row["description"],
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
    logger.info("bili feed producer started (interval=%dh)", INTERVAL_HOURS)

    while True:
        logger.info("fetching bilibili recommend...")
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
