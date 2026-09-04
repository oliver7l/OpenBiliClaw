"""Hupu (虎扑) hot-posts feed producer, built on the ``hupu`` Go CLI.

Calls ``hupu hot --output json`` and inserts new trending BBS posts into
``content_cache`` (recommendation pool) so they become available to the
recommendation engine immediately.

Why a CLI producer
------------------
虎扑没有稳定的官方公开 API。``hupu`` is a single pure-Go binary
(github.com/tamnd/hupu-cli, Apache-2.0) that reads the public Hupu BBS
homepage and returns clean records without login or an API key. It
supports ``--output json`` (and defaults to JSONL when piped), so it is a
clean fit for a headless subprocess call. Keeping it as a separate
process also keeps the CLI's license out of the Python codebase (same
isolation rule the project already applies to GPL/other CLIs).

Outputs
-------
  * ``content_cache`` (recommendation pool) — one row per hot post,
    title + link only (the CLI does not return post bodies).

Usage
-----
  python3 -m openbiliclaw.runtime.hupu_feed_producer            # loop forever (24h)
  python3 -m openbiliclaw.runtime.hupu_feed_producer --once     # one cycle
  python3 -m openbiliclaw.runtime.hupu_feed_producer --dry-run  # one cycle, no DB writes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
CLI_TIMEOUT = 60  # seconds per ``hupu hot`` subprocess call

# Locate the hupu executable. Prefer PATH, fall back to the known install dir
# (same convention as the v2ex CLI producer).
_HUPU_BIN = shutil.which("hupu") or "/Users/imac/.local/bin/hupu"

# Strip proxy env vars: the local proxy (Clash-like) often dies/restarts and a
# dead proxy breaks the CLI child process. Same rationale as xhs_feed_producer /
# cli.py — these producer scripts don't go through cli.py so they clean up
# themselves.
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

# Toggled by --dry-run; when True we parse but skip all DB writes.
_DRY_RUN = False


def _fetch_hot(limit: int) -> list[dict[str, Any]]:
    """Call ``hupu hot --output json`` and return the parsed posts.

    Returns ``[]`` on any failure (logged loudly) so the cycle degrades
    gracefully and never dies mid-loop.
    """
    try:
        proc = subprocess.run(
            [_HUPU_BIN, "hot", "--output", "json", "--limit", str(limit), "--quiet"],
            capture_output=True,
            text=True,
            env=CLEAN_ENV,
            timeout=CLI_TIMEOUT,
        )
    except FileNotFoundError:
        logger.error("hupu CLI not found at %s", _HUPU_BIN)
        return []
    except subprocess.TimeoutExpired:
        logger.error("hupu hot timed out after %ds", CLI_TIMEOUT)
        return []

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-500:]
        logger.error("hupu hot failed (rc=%d): %s", proc.returncode, detail)
        return []

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        logger.error("hupu hot JSON parse error: %s", exc)
        return []
    if not isinstance(data, list):
        logger.warning("hupu hot returned non-list payload: %r", type(data).__name__)
        return []
    return data


def _to_rows(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize hupu posts into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
        if not isinstance(post, dict):
            continue
        pid = str(post.get("id", "") or "").strip()
        title = str(post.get("title", "") or "").strip()
        url = str(post.get("url", "") or "").strip()
        if not pid or not pid.isdigit() or not title or not url:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        rows.append(
            {
                "bvid": pid,
                "title": title,
                "up_name": "",
                "author_name": "",
                "content_url": url,
                "source_platform": "hupu",
                "source": "hupu-hot",
                "content_type": "thread",
                "pool_status": "fresh",
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def _insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Insert new rows into content_cache, skipping duplicates by bvid."""
    inserted = 0
    for row in rows:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            continue
    return inserted


def _run_once(limit: int) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    posts = _fetch_hot(limit)
    if not posts:
        return {"ok": False, "reason": "empty_feed", "fetched": 0, "inserted": 0}

    rows = _to_rows(posts)
    if not rows:
        return {"ok": False, "reason": "no_valid_posts", "fetched": len(posts), "inserted": 0}

    if _DRY_RUN:
        return {"ok": True, "dry_run": True, "fetched": len(posts), "valid": len(rows)}

    conn = sqlite3.connect(DB_PATH)
    try:
        inserted = _insert_rows(conn, rows)
        conn.commit()
        return {
            "ok": True,
            "fetched": len(posts),
            "valid": len(rows),
            "inserted": inserted,
            "skipped_duplicates": len(rows) - inserted,
        }
    finally:
        conn.close()


def run_forever(interval_hours: int, limit: int) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logger.info(
        "hupu feed producer started (cli=%s, interval=%dh, limit=%d, dry_run=%s)",
        _HUPU_BIN,
        interval_hours,
        limit,
        _DRY_RUN,
    )
    while True:
        logger.info("fetching hupu hot...")
        result = _run_once(limit)
        if result["ok"]:
            logger.info(
                "feed ok: %d fetched, %d new, %d duplicate",
                result["fetched"],
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))
        time.sleep(interval_hours * 3600)


def _main() -> None:
    parser = argparse.ArgumentParser(description="Hupu hot-posts feed producer")
    parser.add_argument(
        "--once", action="store_true", help="Run a single cycle and exit (no loop)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes.")
    parser.add_argument("--limit", type=int, default=20, help="Posts to fetch per cycle.")
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
                "hupu feed ok: %d fetched, %d new, %d duplicate",
                result["fetched"],
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
            )
        else:
            logger.warning("hupu feed skipped: %s", result.get("reason", "unknown"))
        return

    run_forever(args.interval, args.limit)


if __name__ == "__main__":
    _main()
