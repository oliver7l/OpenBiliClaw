"""V2EX RSSHub feed scheduler.

Fetches V2EX latest/hot topics via RSSHub (https://rsshub.bestblogs.dev),
parses them with feedparser, and inserts new topics into ``content_cache``
so they appear in the recommendation pool.

Background:
  - The legacy ``v2ex_feed_producer.py`` hits V2EX's own API directly
    (https://www.v2ex.com/api/...). As of 2026-09-04, that path is broken:
    direct access from this network times out (HTTP 000), and the local
    Clash proxy returns 403 Forbidden. So that producer now silently
    reports ``feed skipped: no_data`` on every run.
  - RSSHub mirrors V2EX via ``/v2ex/topics/latest`` and
    ``/v2ex/topics/hot`` and remains reachable. This script uses that
    public mirror instead, so the recommendation pool still gets fresh
    V2EX topics even when the official site is unreachable.
  - RSSHub responses are plain XML (not a CLI JSON payload), so we parse
    with feedparser in-process instead of shelling out.

Usage:
  python3 -m openbiliclaw.runtime.v2ex_rss_producer

The script runs forever, sleeping ``INTERVAL_HOURS`` between fetches.
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import time
import urllib.request
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 6

# Runtime flag toggled by --dry-run from main(). When True, _run_once
# fetches and parses the feeds but skips the INSERT step.
_DRY_RUN = False

# RSSHub mirror endpoints (V2EX public routes, no auth).
RSS_URL_LATEST = "https://rsshub.bestblogs.dev/v2ex/topics/latest"
RSS_URL_HOT = "https://rsshub.bestblogs.dev/v2ex/topics/hot"

# V2EX topic id is a positive integer.
_TID_RE = re.compile(r"^/t/(\d+)$")
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_feed(url: str, timeout: int = 30) -> list[dict[str, Any]]:
    """Fetch an RSSHub endpoint and return list of feed entry dicts.

    Returns an empty list on any failure (HTTP error, parse error,
    empty feed). Errors are logged so the failure is visible — not silent
    like the legacy v2ex producer was on 0-item responses.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
    except Exception as exc:
        logger.error("V2EX RSSHub fetch failed for %s: %s", url, exc)
        return []

    feed = feedparser.parse(data)
    if feed.bozo and not feed.entries:
        logger.warning(
            "V2EX RSSHub parse failed for %s: %s",
            url,
            feed.bozo_exception,
        )
        return []
    return list(feed.entries)


def _parse_published(entry: dict[str, Any]) -> str:
    """Best-effort parse of an entry's published time to 'YYYY-MM-DD HH:MM:SS'.

    Falls back to the current time if the field is missing or unparseable.
    The recommendation pool relies on ``discovered_at`` for ordering,
    not the entry date — so an approximate date is fine for display.
    """
    raw = getattr(entry, "published", "") or getattr(entry, "updated", "") or ""
    if not raw:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        dt = parsedate_to_datetime(raw)
        # RSS gives GMT; convert to local naive for SQLite.
        return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _parse_entries(entries: list[dict[str, Any]], source_tag: str) -> list[dict[str, Any]]:
    """Convert feedparser entries into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        link = (getattr(entry, "link", "") or "").strip()
        if not link:
            continue
        # Extract topic id from canonical URL pattern: https://www.v2ex.com/t/<id>
        m = _TID_RE.search(link.replace("https://www.v2ex.com", ""))
        if not m:
            # Some mirrors use absolute URLs without /t/ prefix; try to
            # extract any trailing numeric segment as a fallback.
            tail = link.rstrip("/").split("/")[-1]
            if not tail.isdigit():
                continue
            tid = tail
        else:
            tid = m.group(1)
        if tid in seen:
            continue
        seen.add(tid)

        title = (getattr(entry, "title", "") or "").strip()
        author = (getattr(entry, "author", "") or "").strip()
        # strip email-like "user (https://...)" forms that some mirrors emit
        author = author.split("(")[0].strip() if "(" in author else author

        rows.append(
            {
                "bvid": tid,
                "title": title,
                "up_name": author,
                "author_name": author,
                "content_url": f"https://www.v2ex.com/t/{tid}",
                "source_platform": "v2ex",
                "source": source_tag,
                "content_type": "thread",
                "pool_status": "fresh",
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def _insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> tuple[int, int]:
    """Insert new rows; skip duplicates by bvid.

    Returns (inserted, skipped_duplicates).
    """
    inserted = 0
    skipped = 0
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
            else:
                skipped += 1
        except sqlite3.IntegrityError:
            skipped += 1
    return inserted, skipped


def _run_once() -> dict[str, Any]:
    """One full fetch cycle across both RSSHub endpoints."""
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    ok_any = False
    for url, tag in ((RSS_URL_LATEST, "v2ex-rss-latest"), (RSS_URL_HOT, "v2ex-rss-hot")):
        entries = _fetch_feed(url)
        if not entries:
            continue
        ok_any = True
        total_fetched += len(entries)
        rows = _parse_entries(entries, tag)
        if not rows:
            continue
        conn = sqlite3.connect(DB_PATH)
        try:
            inserted, skipped = _insert_rows(conn, rows)
            conn.commit()
            total_inserted += inserted
            total_skipped += skipped
        finally:
            conn.close()

    if _DRY_RUN:
        # Only the first endpoint's count is meaningful in dry-run, since
        # we don't actually persist anything; just report what we'd insert.
        return {
            "ok": ok_any,
            "fetched": total_fetched,
            "would_insert": total_inserted,
            "skipped_duplicates": total_skipped,
            "dry_run": True,
        }
    if not ok_any:
        return {"ok": False, "reason": "all_endpoints_failed", "fetched": 0, "inserted": 0}
    return {
        "ok": True,
        "fetched": total_fetched,
        "inserted": total_inserted,
        "skipped_duplicates": total_skipped,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="V2EX RSSHub feed scheduler")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse feeds but skip the database INSERT step.",
    )
    args = parser.parse_args()

    if args.dry_run:
        global _DRY_RUN
        _DRY_RUN = True

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("v2ex rss producer started (interval=%dh, dry_run=%s)", INTERVAL_HOURS, _DRY_RUN)

    if _DRY_RUN:
        # Single-shot, don't loop forever.
        logger.info("fetching v2ex RSSHub feeds...")
        result = _run_once()
        if result["ok"]:
            logger.info(
                "dry-run ok: %d fetched, %d would_insert, %d duplicate",
                result["fetched"],
                result["would_insert"],
                result["skipped_duplicates"],
            )
        else:
            logger.warning("dry-run skipped: %s", result.get("reason", "unknown"))
        return

    while True:
        logger.info("fetching v2ex RSSHub feeds...")
        result = _run_once()
        if result["ok"]:
            logger.info(
                "feed ok: %d fetched, %d new, %d duplicate",
                result["fetched"],
                result["inserted"],
                result["skipped_duplicates"],
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))
        time.sleep(INTERVAL_HOURS * 3600)


if __name__ == "__main__":
    _main()
