"""Toutiao (今日头条) hot-news feed producer, built on the ``toutiao`` Go CLI.

Calls ``toutiao hot --output json`` and inserts new trending news into
``content_cache`` (recommendation pool) and ``articles`` (reading
library), so headlines land in the pool and their abstracts become
searchable/readable.

Why a CLI producer
------------------
头条没有面向第三方的稳定公开 API。``toutiao`` is a single pure-Go binary
(github.com/tamnd/toutiao-cli, Apache-2.0) that reads the public Toutiao
feed and returns clean records (title / source / abstract / url) without
login or an API key, with ``--output json``. It is kept as a separate
subprocess for the same license-isolation reason as the other CLI
producers.

Outputs
-------
  * ``content_cache`` (recommendation pool) — with ``body_text`` = the
    news abstract so the pool carries a snippet, not just a title.
  * ``articles`` (reading library) — ``content_text`` = abstract, so
    Toutiao news is searchable/readable.

Usage
-----
  python3 -m openbiliclaw.runtime.toutiao_feed_producer            # loop forever (24h)
  python3 -m openbiliclaw.runtime.toutiao_feed_producer --once     # one cycle
  python3 -m openbiliclaw.runtime.toutiao_feed_producer --dry-run  # one cycle, no DB writes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from typing import Any

from openbiliclaw.runtime._db import connect_inbox as _obc_connect

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
CLI_TIMEOUT = 60  # seconds per ``toutiao hot`` subprocess call

# Locate the toutiao executable. Prefer PATH, fall back to the known install
# dir (same convention as the v2ex / hupu CLI producers).
_TOUTIAO_BIN = shutil.which("toutiao") or "/Users/imac/.local/bin/toutiao"

# Strip proxy env vars (same rationale as hupu_feed_producer / xhs_producer).
CLEAN_ENV = os.environ.copy()
CLEAN_ENV["PYTHONHOME"] = ""
CLEAN_ENV["PYTHONPATH"] = ""
for _proxy_key in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    CLEAN_ENV.pop(_proxy_key, None)

# Extracts the numeric article id from https://www.toutiao.com/group/<id>/
# (group/article use a trailing slash, ``/a<id>`` does not — allow an optional ``/``).
_GROUP_ID_RE = re.compile(r"/(?:group|article|a)/?(\d+)")

# Toggled by --dry-run; when True we parse but skip all DB writes.
_DRY_RUN = False


def _bvid_for(url: str) -> str:
    """Return a stable unique id for a toutiao url."""
    m = _GROUP_ID_RE.search(url)
    if m:
        return m.group(1)
    return hashlib.md5(url.encode("utf-8")).hexdigest()


def _fetch_hot(limit: int) -> list[dict[str, Any]]:
    """Call ``toutiao hot --output json`` and return the parsed news.

    Returns ``[]`` on any failure (logged loudly) so the cycle degrades
    gracefully and never dies mid-loop.
    """
    try:
        proc = subprocess.run(
            [_TOUTIAO_BIN, "hot", "--output", "json", "--limit", str(limit), "--quiet"],
            capture_output=True,
            text=True,
            env=CLEAN_ENV,
            timeout=CLI_TIMEOUT,
        )
    except FileNotFoundError:
        logger.error("toutiao CLI not found at %s", _TOUTIAO_BIN)
        return []
    except subprocess.TimeoutExpired:
        logger.error("toutiao hot timed out after %ds", CLI_TIMEOUT)
        return []

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        logger.error("toutiao hot failed (rc=%d): %s", proc.returncode, detail)
        return []

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        logger.error("toutiao hot JSON parse error: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("toutiao hot returned non-list payload: %r", type(data).__name__)
        return []
    return data


def _to_rows(news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize toutiao news into content_cache/articles-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in news:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        if not title or not url:
            continue
        bvid = _bvid_for(url)
        if bvid in seen:
            continue
        seen.add(bvid)
        abstract = str(item.get("abstract", "") or "").strip()
        rows.append(
            {
                "bvid": bvid,
                "title": title,
                "up_name": str(item.get("source", "") or "").strip(),
                "author_name": str(item.get("source", "") or "").strip(),
                "content_url": url,
                "source_platform": "toutiao",
                "source": "toutiao-hot",
                "content_type": "article",
                "pool_status": "fresh",
                "body_text": abstract,
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def _insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Insert new rows into content_cache (+articles when a body exists).

    Returns (cache_inserted, articles_inserted).
    """
    cache_ins = 0
    art_ins = 0
    for row in rows:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    body_text, discovered_at
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
                    row["body_text"],
                    row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                cache_ins += 1
        except sqlite3.IntegrityError:
            pass

        # Reading library: only when we have an abstract to store.
        if row["body_text"]:
            try:
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO articles (
                        source_type, source_name, title, url, author,
                        content_text, published_at, tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "toutiao",
                        "今日头条",
                        row["title"],
                        row["content_url"],
                        row["up_name"],
                        row["body_text"],
                        row["discovered_at"],
                        json.dumps(["头条热闻"]),
                    ),
                )
                if cursor.rowcount > 0:
                    art_ins += 1
            except sqlite3.IntegrityError:
                pass
    return cache_ins, art_ins


def _run_once(limit: int) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    news = _fetch_hot(limit)
    if not news:
        return {"ok": False, "reason": "empty_feed", "fetched": 0, "inserted": 0}

    rows = _to_rows(news)
    if not rows:
        return {"ok": False, "reason": "no_valid_items", "fetched": len(news), "inserted": 0}

    if _DRY_RUN:
        return {"ok": True, "dry_run": True, "fetched": len(news), "valid": len(rows)}

    conn = _obc_connect("toutiao")
    try:
        cache_ins, art_ins = _insert_rows(conn, rows)
        conn.commit()
        return {
            "ok": True,
            "fetched": len(news),
            "valid": len(rows),
            "inserted": cache_ins,
            "articles_inserted": art_ins,
            "skipped_duplicates": len(rows) - cache_ins,
        }
    finally:
        conn.close()


def run_forever(interval_hours: int, limit: int) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logger.info(
        "toutiao feed producer started (cli=%s, interval=%dh, limit=%d, dry_run=%s)",
        _TOUTIAO_BIN,
        interval_hours,
        limit,
        _DRY_RUN,
    )
    while True:
        logger.info("fetching toutiao hot...")
        result = _run_once(limit)
        if result["ok"]:
            logger.info(
                "feed ok: %d fetched, %d new, %d duplicate, %d articles",
                result["fetched"],
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
                result.get("articles_inserted", 0),
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))
        time.sleep(interval_hours * 3600)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Toutiao hot-news feed producer")
    parser.add_argument(
        "--once", action="store_true", help="Run a single cycle and exit (no loop)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes.")
    parser.add_argument("--limit", type=int, default=20, help="Items to fetch per cycle.")
    parser.add_argument(
        "--interval", type=int, default=INTERVAL_HOURS, help="Hours between cycles when looping."
    )
    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.once or args.dry_run:
        result = _run_once(args.limit)
        if result["ok"]:
            logger.info(
                "toutiao feed ok: %d fetched, %d new, %d duplicate, %d articles",
                result["fetched"],
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
                result.get("articles_inserted", 0),
            )
        else:
            logger.warning("toutiao feed skipped: %s", result.get("reason", "unknown"))
        return

    run_forever(args.interval, args.limit)


if __name__ == "__main__":
    _main()
