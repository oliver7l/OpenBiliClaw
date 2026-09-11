"""Hupu (虎扑) hot-posts, 步行街 (Buxingjie), and search feed producer.

Three modes:
  * Default (CLI): calls ``hupu hot --output json`` (Go binary, tamnd/hupu-cli)
    and inserts trending BBS homepage posts into ``content_cache``.
  * ``--bxj``: direct HTTP scrape of the 步行街 forum (bbs.hupu.com/bxj),
    parsing post title / reply count / view count / author / time from HTML.
    No login or API key required; supports pagination (50 posts per page).
  * ``--search``: direct HTTP search (bbs.hupu.com/search?q=...), supports
    keyword(s), sort order (general/createtime/light/reply), and returns
    post title / forum / date / reply / recommend / light counts.

Why a CLI producer
------------------
虎扑没有稳定的官方公开 API。``hupu`` is a single pure-Go binary
(github.com/tamnd/hupu-cli, Apache-2.0) that reads the public Hupu BBS
homepage and returns clean records without login or an API key. It
supports ``--output json`` (and defaults to JSONL when piped), so it is a
clean fit for a headless subprocess call. Keeping it as a separate
process also keeps the CLI's license out of the Python codebase (same
isolation rule the project already applies to GPL/other CLIs).

The ``--bxj`` mode complements the CLI: the CLI only covers the homepage
hot list, while 步行街 is the highest-traffic general discussion forum
and has a stable HTML structure suitable for direct scraping.

The ``--search`` mode complements both: it discovers content by interest
keywords (e.g. python, AI, 理财, 职场) rather than browsing hot lists,
filling gaps in the recommendation pool.

Outputs
-------
  * ``content_cache`` (recommendation pool) — one row per post,
    title + link (+ author / reply / view counts in bxj mode,
    + forum / reply / recommend / light in search mode).

Usage
-----
  python3 -m openbiliclaw.runtime.hupu_feed_producer            # loop forever (24h, hot)
  python3 -m openbiliclaw.runtime.hupu_feed_producer --once     # one cycle (hot)
  python3 -m openbiliclaw.runtime.hupu_feed_producer --bxj --once   # one cycle (步行街)
  python3 -m openbiliclaw.runtime.hupu_feed_producer --search --keyword python --once  # search
  python3 -m openbiliclaw.runtime.hupu_feed_producer --search --keywords python,AI  # multi-kw
  python3 -m openbiliclaw.runtime.hupu_feed_producer --dry-run  # one cycle, no DB writes
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from datetime import datetime
from typing import Any

from openbiliclaw.runtime._db import connect_inbox

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
CLI_TIMEOUT = 60  # seconds per ``hupu hot`` subprocess call

# Locate the hupu executable. Prefer PATH, fall back to the known install dir
# (same convention as the v2ex CLI producer).
_HUPU_BIN = shutil.which("hupu") or "/Users/imac/.local/bin/hupu"

# 步行街 (Buxingjie) direct HTTP scrape constants
BXJ_BASE_URL = "https://bbs.hupu.com/bxj"
BXJ_POSTS_PER_PAGE = 50
BXJ_HTTP_TIMEOUT = 15  # seconds per page fetch
BXJ_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Regex patterns for bxj HTML parsing (compiled once at import)
_BXJ_POST_RE = re.compile(r'<li class="bbs-sl-web-post-body">(.*?)</li>', re.DOTALL)
_BXJ_TITLE_RE = re.compile(r'class="p-title"[^>]*>(.*?)</a>', re.DOTALL)
_BXJ_PID_RE = re.compile(r'href="/(\d+)\.html"')
_BXJ_DATUM_RE = re.compile(r'class="post-datum"[^>]*>(.*?)</div>', re.DOTALL)
_BXJ_AUTHOR_RE = re.compile(r'href="https://my\.hupu\.com/(\d+)"[^>]*>(.*?)</a>', re.DOTALL)
_BXJ_TIME_RE = re.compile(r'class="post-time"[^>]*>(.*?)</div>', re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# Search (虎扑搜索) direct HTTP scrape constants
SEARCH_BASE_URL = "https://bbs.hupu.com/search"
SEARCH_HTTP_TIMEOUT = 15
SEARCH_SORT_OPTIONS = ("general", "createtime", "light", "reply")
SEARCH_DEFAULT_KEYWORDS = ("python", "AI", "理财", "职场")

# Regex patterns for search HTML parsing
_SEARCH_WRAP_RE = re.compile(r'<div class="content-wrap">(.*?)</div>', re.DOTALL)
_SEARCH_LINK_RE = re.compile(
    r'<a class="content-wrap-span" href="(https://bbs\.hupu\.com/(\d+)\.html)"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_SEARCH_FORUM_RE = re.compile(
    r'<a class="content-wrap-span" href="https://bbs\.hupu\.com/\d+"[^>]*>(.*?)</a>', re.DOTALL
)
_SEARCH_DATE_RE = re.compile(r"<span>(\d{4}-\d{2}-\d{2})</span>")
_SEARCH_STAT_RE = re.compile(r'<span class="content-wrap-span1">(\d+)</span>')
_FONT_TAG_RE = re.compile(r"<font[^>]*>|</font>", re.IGNORECASE)

# Strip proxy env vars: the local proxy (Clash-like) often dies/restarts and a
# dead proxy breaks the CLI child process. Same rationale as xhs_producer /
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


# ---------------------------------------------------------------------------
# 步行街 (Buxingjie) direct HTTP scrape
# ---------------------------------------------------------------------------


def _fetch_bxj_page(page_num: int) -> str:
    """Fetch a single 步行街 page and return its HTML.

    Args:
        page_num: 1-based page number (page 1 = /bxj, page 2 = /bxj-2).

    Returns the HTML string, or ``""`` on failure.

    """
    url = BXJ_BASE_URL if page_num <= 1 else f"{BXJ_BASE_URL}-{page_num}"
    req = urllib.request.Request(url, headers={"User-Agent": BXJ_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=BXJ_HTTP_TIMEOUT) as resp:
            raw: bytes = resp.read()
        # Hupu pages are UTF-8; detect encoding from Content-Type if available
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.error("hupu bxj page %d fetch failed: %s", page_num, exc)
        return ""


def _parse_bxj_html(html: str) -> list[dict[str, Any]]:
    """Parse 步行街 list HTML into normalized post dicts.

    Each post contains: id, title, reply_count, view_count, author,
    author_uid, post_time, url.
    """
    if not html:
        return []

    posts: list[dict[str, Any]] = []
    for match in _BXJ_POST_RE.finditer(html):
        block = match.group(1)

        # Post ID + URL
        pid_m = _BXJ_PID_RE.search(block)
        if not pid_m:
            continue
        pid = pid_m.group(1)

        # Title
        title_m = _BXJ_TITLE_RE.search(block)
        if not title_m:
            continue
        title = _TAG_RE.sub("", title_m.group(1)).strip()
        if not title:
            continue

        # Reply / View counts: "102 / 32408"
        reply_count = 0
        view_count = 0
        datum_m = _BXJ_DATUM_RE.search(block)
        if datum_m:
            datum_text = _TAG_RE.sub("", datum_m.group(1)).strip()
            parts = [p.strip() for p in datum_text.split("/")]
            if len(parts) >= 1:
                reply_count = _parse_int(parts[0])
            if len(parts) >= 2:
                view_count = _parse_int(parts[1])

        # Author
        author = ""
        author_uid = ""
        author_m = _BXJ_AUTHOR_RE.search(block)
        if author_m:
            author_uid = author_m.group(1)
            author = _TAG_RE.sub("", author_m.group(2)).strip()

        # Post time
        post_time = ""
        time_m = _BXJ_TIME_RE.search(block)
        if time_m:
            post_time = _TAG_RE.sub("", time_m.group(1)).strip()

        posts.append(
            {
                "id": pid,
                "title": title,
                "reply_count": reply_count,
                "view_count": view_count,
                "author": author,
                "author_uid": author_uid,
                "post_time": post_time,
                "url": f"https://bbs.hupu.com/{pid}.html",
            }
        )
    return posts


def _parse_int(text: str) -> int:
    """Parse an integer from text that may contain commas or 万 units."""
    text = text.strip().replace(",", "").replace("，", "")
    if not text:
        return 0
    try:
        if text.endswith("万"):
            return int(float(text[:-1]) * 10000)
        return int(float(text))
    except (ValueError, TypeError):
        return 0


def _fetch_bxj(limit: int) -> list[dict[str, Any]]:
    """Scrape 步行街 posts across multiple pages up to ``limit``.

    Returns a list of normalized post dicts, or ``[]`` on failure.
    """
    all_posts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    pages_needed = max(1, (limit + BXJ_POSTS_PER_PAGE - 1) // BXJ_POSTS_PER_PAGE)

    for page_num in range(1, pages_needed + 1):
        html = _fetch_bxj_page(page_num)
        if not html:
            break
        posts = _parse_bxj_html(html)
        logger.info("hupu bxj page %d: parsed %d posts", page_num, len(posts))
        for post in posts:
            pid = post["id"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            all_posts.append(post)
            if len(all_posts) >= limit:
                break
        if len(all_posts) >= limit:
            break
        # Polite delay between pages
        time.sleep(0.5)

    return all_posts


def _to_bxj_rows(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize 步行街 posts into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
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
                "up_name": post.get("author", ""),
                "author_name": post.get("author", ""),
                "content_url": url,
                "source_platform": "hupu",
                "source": "hupu-bxj",
                "content_type": "thread",
                "pool_status": "fresh",
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "reply_count": post.get("reply_count", 0),
                "view_count": post.get("view_count", 0),
                "post_time": post.get("post_time", ""),
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Search (虎扑搜索) direct HTTP scrape
# ---------------------------------------------------------------------------


def _fetch_search_page(keyword: str, sortby: str = "general", page: int = 1) -> str:
    """Fetch a single search results page and return its HTML.

    Args:
        keyword: Search query string.
        sortby: One of 'general' (综合), 'createtime' (最新), 'light' (亮回复), 'reply' (回复数).
        page: 1-based page number.

    Returns the HTML string, or ``""`` on failure.

    """
    from urllib.parse import quote

    params = f"q={quote(keyword)}&sortby={sortby}"
    if page > 1:
        params += f"&page={page}"
    url = f"{SEARCH_BASE_URL}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": BXJ_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=SEARCH_HTTP_TIMEOUT) as resp:
            raw: bytes = resp.read()
        return raw.decode("utf-8", errors="replace")
    except Exception as exc:
        logger.error("hupu search page %d fetch failed (q=%s): %s", page, keyword, exc)
        return ""


def _parse_search_html(html: str) -> list[dict[str, Any]]:
    """Parse search results HTML into normalized post dicts.

    Each result block (div.content-wrap) contains:
      - First link (content-wrap-span): post title + full URL (may have <font> highlights)
      - Second link (content-wrap-span): forum name
      - First <span>: post date (YYYY-MM-DD)
      - Three <span class="content-wrap-span1">: reply, recommend, light counts

    Returns list of dicts with: id, title, forum, post_time, reply_count,
    recommend_count, light_count, url.
    """
    if not html:
        return []

    posts: list[dict[str, Any]] = []
    for match in _SEARCH_WRAP_RE.finditer(html):
        block = match.group(1)

        # Post ID + URL + title (first content-wrap-span link)
        link_m = _SEARCH_LINK_RE.search(block)
        if not link_m:
            continue
        url = link_m.group(1)
        pid = link_m.group(2)
        # Title may contain <font color='...'>keyword</font> highlights
        title_raw = link_m.group(3)
        title = _FONT_TAG_RE.sub("", title_raw)
        title = _TAG_RE.sub("", title).strip()
        if not title:
            continue

        # Forum name (second content-wrap-span link)
        forum = ""
        forum_m = _SEARCH_FORUM_RE.search(block)
        if forum_m:
            forum = _TAG_RE.sub("", forum_m.group(1)).strip()

        # Post date
        post_time = ""
        date_m = _SEARCH_DATE_RE.search(block)
        if date_m:
            post_time = date_m.group(1)

        # Stats: reply_count, recommend_count, light_count
        stats = _SEARCH_STAT_RE.findall(block)
        reply_count = int(stats[0]) if len(stats) >= 1 else 0
        recommend_count = int(stats[1]) if len(stats) >= 2 else 0
        light_count = int(stats[2]) if len(stats) >= 3 else 0

        posts.append(
            {
                "id": pid,
                "title": title,
                "forum": forum,
                "post_time": post_time,
                "reply_count": reply_count,
                "recommend_count": recommend_count,
                "light_count": light_count,
                "url": url,
            }
        )
    return posts


def _fetch_search(
    keywords: list[str], sortby: str = "general", limit: int = 50
) -> list[dict[str, Any]]:
    """Search Hupu for one or more keywords and return deduplicated posts.

    Args:
        keywords: List of search queries. Each keyword fetches one page.
        sortby: Sort order, one of SEARCH_SORT_OPTIONS.
        limit: Maximum total posts to return across all keywords.

    Returns a list of normalized post dicts, deduplicated by post id.

    """
    all_posts: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for keyword in keywords:
        html = _fetch_search_page(keyword, sortby=sortby, page=1)
        if not html:
            continue
        posts = _parse_search_html(html)
        logger.info("hupu search q='%s': parsed %d posts", keyword, len(posts))
        for post in posts:
            pid = post["id"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            post["keyword"] = keyword
            post["sortby"] = sortby
            all_posts.append(post)
            if len(all_posts) >= limit:
                break
        if len(all_posts) >= limit:
            break
        # Polite delay between keyword searches
        time.sleep(0.5)

    return all_posts


def _to_search_rows(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize search results into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for post in posts:
        pid = str(post.get("id", "") or "").strip()
        title = str(post.get("title", "") or "").strip()
        url = str(post.get("url", "") or "").strip()
        if not pid or not pid.isdigit() or not title or not url:
            continue
        if pid in seen:
            continue
        seen.add(pid)
        forum = post.get("forum", "")
        keyword = post.get("keyword", "")
        # source includes keyword for traceability, e.g. "hupu-search-python"
        source = f"hupu-search-{keyword}" if keyword else "hupu-search"
        rows.append(
            {
                "bvid": pid,
                "title": title,
                "up_name": "",
                "author_name": "",
                "content_url": url,
                "source_platform": "hupu",
                "source": source,
                "content_type": "thread",
                "pool_status": "fresh",
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
                "reply_count": post.get("reply_count", 0),
                "view_count": 0,
                "post_time": post.get("post_time", ""),
                "forum": forum,
                "recommend_count": post.get("recommend_count", 0),
                "light_count": post.get("light_count", 0),
            }
        )
    return rows


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
    """Insert new rows into content_cache, skipping duplicates by bvid.

    Supports optional extra fields (reply_count → comment_count, view_count)
    when present in the row (e.g. 步行街 posts).
    """
    inserted = 0
    for row in rows:
        # Build column list dynamically based on available fields
        columns = [
            "bvid",
            "title",
            "up_name",
            "author_name",
            "content_url",
            "source_platform",
            "source",
            "content_type",
            "pool_status",
            "discovered_at",
        ]
        values: list[Any] = [
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
        ]
        # Optional: map reply_count → comment_count, view_count → view_count
        if row.get("reply_count") is not None:
            columns.append("comment_count")
            values.append(row["reply_count"])
        if row.get("view_count") is not None:
            columns.append("view_count")
            values.append(row["view_count"])

        placeholders = ", ".join("?" for _ in columns)
        col_list = ", ".join(columns)
        try:
            cursor = conn.execute(
                f"INSERT OR IGNORE INTO content_cache ({col_list}) VALUES ({placeholders})",
                values,
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.OperationalError:
            # Fallback: if optional columns don't exist in this DB version,
            # insert with the base columns only
            base_cols = [
                "bvid",
                "title",
                "up_name",
                "author_name",
                "content_url",
                "source_platform",
                "source",
                "content_type",
                "pool_status",
                "discovered_at",
            ]
            base_vals = [
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
            ]
            ph = ", ".join("?" for _ in base_cols)
            cl = ", ".join(base_cols)
            try:
                cursor = conn.execute(
                    f"INSERT OR IGNORE INTO content_cache ({cl}) VALUES ({ph})",
                    base_vals,
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except sqlite3.IntegrityError:
                continue
        except sqlite3.IntegrityError:
            continue
    return inserted


def _run_once(
    limit: int,
    bxj_mode: bool = False,
    search_mode: bool = False,
    keywords: list[str] | None = None,
    sortby: str = "general",
) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict.

    Args:
        bxj_mode: If True, scrape 步行街 instead of the CLI hot list.
        search_mode: If True, search Hupu by keyword(s) instead of hot list.
        keywords: Search keywords (only used when search_mode=True).
        sortby: Search sort order (only used when search_mode=True).

    """
    if search_mode:
        kw_list = keywords or list(SEARCH_DEFAULT_KEYWORDS)
        posts = _fetch_search(kw_list, sortby=sortby, limit=limit)
        rows = _to_search_rows(posts)
        feed_label = "search"
    elif bxj_mode:
        posts = _fetch_bxj(limit)
        rows = _to_bxj_rows(posts)
        feed_label = "bxj"
    else:
        posts = _fetch_hot(limit)
        rows = _to_rows(posts)
        feed_label = "hot"

    if not posts:
        return {"ok": False, "reason": "empty_feed", "fetched": 0, "inserted": 0}

    if not rows:
        return {"ok": False, "reason": "no_valid_posts", "fetched": len(posts), "inserted": 0}

    if _DRY_RUN:
        return {"ok": True, "dry_run": True, "fetched": len(posts), "valid": len(rows)}

    conn = connect_inbox("hupu")
    try:
        inserted = _insert_rows(conn, rows)
        conn.commit()
        return {
            "ok": True,
            "feed": feed_label,
            "fetched": len(posts),
            "valid": len(rows),
            "inserted": inserted,
            "skipped_duplicates": len(rows) - inserted,
        }
    finally:
        conn.close()


def run_forever(
    interval_hours: int,
    limit: int,
    bxj_mode: bool = False,
    search_mode: bool = False,
    keywords: list[str] | None = None,
    sortby: str = "general",
) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    if search_mode:
        feed_label = "search"
    elif bxj_mode:
        feed_label = "bxj"
    else:
        feed_label = "hot"
    logger.info(
        "hupu %s feed producer started (cli=%s, interval=%dh, limit=%d, dry_run=%s)",
        feed_label,
        _HUPU_BIN,
        interval_hours,
        limit,
        _DRY_RUN,
    )
    while True:
        logger.info("fetching hupu %s...", feed_label)
        result = _run_once(
            limit,
            bxj_mode=bxj_mode,
            search_mode=search_mode,
            keywords=keywords,
            sortby=sortby,
        )
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
    parser = argparse.ArgumentParser(description="Hupu hot-posts and 步行街 feed producer")
    parser.add_argument(
        "--once", action="store_true", help="Run a single cycle and exit (no loop)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes.")
    parser.add_argument("--limit", type=int, default=20, help="Posts to fetch per cycle.")
    parser.add_argument(
        "--interval", type=int, default=INTERVAL_HOURS, help="Hours between cycles when looping."
    )
    parser.add_argument(
        "--bxj",
        action="store_true",
        help="Scrape 步行街 (Buxingjie) forum via direct HTTP instead of CLI hot list.",
    )
    parser.add_argument(
        "--search",
        action="store_true",
        help="Search Hupu by keyword(s) via direct HTTP instead of hot list.",
    )
    parser.add_argument(
        "--keyword",
        type=str,
        default="",
        help="Single search keyword (use with --search). Default: built-in keyword list.",
    )
    parser.add_argument(
        "--keywords",
        type=str,
        default="",
        help="Comma-separated search keywords (use with --search). Overrides --keyword.",
    )
    parser.add_argument(
        "--sortby",
        type=str,
        default="general",
        choices=SEARCH_SORT_OPTIONS,
        help="Search sort order: general(综合)/createtime(最新)/light(亮回复)/reply(回复数).",
    )
    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Resolve search keywords
    search_keywords: list[str] | None = None
    if args.search:
        if args.keywords:
            search_keywords = [k.strip() for k in args.keywords.split(",") if k.strip()]
        elif args.keyword:
            search_keywords = [args.keyword.strip()]
        else:
            search_keywords = list(SEARCH_DEFAULT_KEYWORDS)

    if args.once or args.dry_run:
        result = _run_once(
            args.limit,
            bxj_mode=args.bxj,
            search_mode=args.search,
            keywords=search_keywords,
            sortby=args.sortby,
        )
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

    run_forever(
        args.interval,
        args.limit,
        bxj_mode=args.bxj,
        search_mode=args.search,
        keywords=search_keywords,
        sortby=args.sortby,
    )


if __name__ == "__main__":
    _main()
