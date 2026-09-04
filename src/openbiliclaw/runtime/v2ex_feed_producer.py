"""V2EX recommendation feed scheduler.

Calls the V2EX public API (no authentication required) to fetch latest and
hot topics every 24 hours, and inserts them into ``content_cache`` so they
become available in the recommendation pool.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import urllib.request
from datetime import datetime
from typing import Any, cast

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24

# V2EX API 走代理：实测直连（urllib 默认 + Chrome UA）会 25s 超时
# HTTP 000，必须走本机 Clash 类代理（HTTP_PROXY）才能在 0.3s 内拿到 200。
# 与 youtube 不同：v2ex 失败时会抛 urlopen error 而不是静默返回 0，
# 已经写进 error log，不会像 youtube 那样伪装成功。
_DEFAULT_OPENER = urllib.request.build_opener()
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

API_LATEST = "https://www.v2ex.com/api/topics/latest.json"
API_HOT = "https://www.v2ex.com/api/topics/hot.json"


def _fetch_json(url: str) -> list[dict[str, Any]]:
    """Fetch a V2EX API endpoint and return the parsed JSON array."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _DEFAULT_OPENER.open(req, timeout=30) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            return cast("list[dict[str, Any]]", parsed)
    except Exception as exc:
        logger.error("V2EX API request failed for %s: %s", url, exc)
        return []


def _parse_topics(topics: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    """Extract fields from V2EX topic list into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for topic in topics:
        tid = str(topic.get("id", "")).strip()
        if not tid:
            continue
        if tid in seen:
            continue
        seen.add(tid)

        title = str(topic.get("title", "") or "").strip()
        if not title:
            continue

        member = topic.get("member", {}) or {}
        node = topic.get("node", {}) or {}
        node_name = str(node.get("name", "") or "").strip()

        content_url = f"https://www.v2ex.com/t/{tid}"
        replies = int(topic.get("replies", 0) or 0)
        body_plain = str(topic.get("content_rendered", "") or "").strip()
        # Remove HTML tags for plain text
        body_text = ""
        if body_plain:
            import re

            body_text = re.sub(r"<[^>]+>", "", body_plain)[:500]

        rows.append(
            {
                "bvid": tid,
                "title": title,
                "up_name": str(member.get("username", "") or "").strip(),
                "author_name": str(member.get("username", "") or "").strip(),
                "content_url": content_url,
                "source_platform": "v2ex",
                "source": source,
                "content_type": "article",
                "pool_status": "fresh",
                "like_count": replies,
                "topic_group": node_name,
                "body_text": body_text,
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
                    like_count, topic_group, body_text, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    row["like_count"],
                    row["topic_group"],
                    row["body_text"],
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
    # Fetch latest topics
    latest_topics = _fetch_json(API_LATEST)
    latest_rows = _parse_topics(latest_topics, "v2ex-feed") if latest_topics else []

    # Fetch hot topics
    hot_topics = _fetch_json(API_HOT)
    hot_rows = _parse_topics(hot_topics, "v2ex-feed") if hot_topics else []

    all_rows = latest_rows + hot_rows
    if not all_rows:
        return {"ok": False, "reason": "no_data", "items_fetched": 0, "inserted": 0}

    # Deduplicate by bvid
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in all_rows:
        if row["bvid"] not in seen:
            seen.add(row["bvid"])
            deduped.append(row)

    conn = sqlite3.connect(DB_PATH)
    try:
        inserted = _insert_rows(conn, deduped)
        conn.commit()
        return {
            "ok": True,
            "items_fetched": len(latest_topics) + len(hot_topics),
            "valid_items": len(deduped),
            "inserted": inserted,
            "skipped_duplicates": len(deduped) - inserted,
        }
    finally:
        conn.close()


def run_forever() -> None:
    """Main loop: fetch every 24 hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("v2ex feed producer started (interval=%dh)", INTERVAL_HOURS)

    while True:
        logger.info("fetching v2ex topics...")
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
