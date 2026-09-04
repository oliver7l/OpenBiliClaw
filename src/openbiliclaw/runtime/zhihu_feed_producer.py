"""Zhihu recommendation feed scheduler.

Calls ``zhihu feed --json`` every 24 hours and inserts new answers / pins
into ``content_cache`` so they become available in the recommendation pool.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from typing import Any, cast

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliclaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
FEED_CMD = [
    "/Users/imac/.local/share/uv/tools/pyzhihu-cli/bin/zhihu",
    "feed",
    "--json",
    "-l",
    "10",
]
CLEAN_ENV = os.environ.copy()
CLEAN_ENV["PYTHONHOME"] = ""
CLEAN_ENV["PYTHONPATH"] = ""
# 清掉代理环境变量：本机代理（Clash 类）经常重启/挂掉，挂掉时子进程（zhihu CLI）走代理会失败。
# 与 cli.py 的 _strip_proxy_env 同一思路——本脚本不经 cli.py 启动，需自行清理。
for _proxy_key in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    CLEAN_ENV.pop(_proxy_key, None)

_ANSWER_ID_RE = re.compile(r"^\d+$")


def _fetch_feed() -> list[dict[str, Any]]:
    """Call ``zhihu feed --json`` and return the parsed items."""
    result = subprocess.run(
        FEED_CMD,
        capture_output=True,
        text=True,
        env=CLEAN_ENV,
        timeout=60,
    )
    if result.returncode != 0:
        logger.error("zhihu feed failed (rc=%d): %s", result.returncode, result.stderr[:500])
        return []
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        logger.error("zhihu feed JSON parse error: %s", exc)
        return []
    raw_items = data.get("data", [])
    items: list[dict[str, Any]] = cast("list[dict[str, Any]]", raw_items)
    if not items:
        logger.info("zhihu feed returned 0 items")
        return []
    return items


def _parse_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields from feed items into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        target = item.get("target", {}) or {}
        aid = str(target.get("id", "")).strip()
        if not aid or not _ANSWER_ID_RE.match(aid):
            continue
        if aid in seen:
            continue
        seen.add(aid)

        author = target.get("author", {}) or {}
        question = target.get("question", {}) or {}

        answer_title = str(target.get("title", "") or "").strip()
        question_title = str(question.get("title", "") or "").strip()
        title = answer_title or question_title

        author_name = str(author.get("name", "") or "").strip()
        author_url_token = str(author.get("url_token", "") or "").strip()
        qid = str(question.get("id", "") or "").strip()
        content_type = "pin" if not qid else "article"
        likes = int(target.get("voteup_count", 0) or 0)
        excerpt = str(target.get("excerpt", "") or "").strip()

        if not title:
            continue

        if qid:
            content_url = f"https://www.zhihu.com/question/{qid}/answer/{aid}"
        else:
            content_url = f"https://www.zhihu.com/pin/{aid}"

        rows.append(
            {
                "bvid": aid,
                "title": title,
                "up_name": author_name,
                "author_name": author_name,
                "author_url_token": author_url_token,
                "content_url": content_url,
                "source_platform": "zhihu",
                "source": "zhihu-feed",
                "content_type": content_type,
                "pool_status": "fresh",
                "like_count": likes,
                "body_text": excerpt,
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
                    like_count, body_text, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    logger.info("zhihu feed producer started (interval=%dh)", INTERVAL_HOURS)

    while True:
        logger.info("fetching zhihu feed...")
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
