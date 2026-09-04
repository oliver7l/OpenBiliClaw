"""V2EX feed producer using Playwright browser automation (with proxy).

Why this exists
---------------
The legacy ``v2ex_feed_producer.py`` (direct API) and ``v2ex_cli_producer.py``
(user's ``v2ex`` CLI) both fail on this machine: V2EX sits behind Cloudflare
and direct HTTP / API calls get a 403 "Just a moment..." challenge. The
``v2ex_rss_producer.py`` works via RSSHub but only gives titles (no body).

This producer drives a real Chromium through Playwright (with the local
Clash proxy at 127.0.0.1:7890) to load V2EX pages like a normal browser,
passing Cloudflare. It scrapes the latest + hot topic lists and optionally
enriches each topic with its full body by visiting the detail page.

Outputs
-------
  * ``content_cache`` (recommendation pool) — title + author + node + replies
  * ``articles`` (reading library) — full body text when enrichment is enabled

Usage
-----
  python3 -m openbiliclaw.runtime.v2ex_browser_producer            # loop forever (24h)
  python3 -m openbiliclaw.runtime.v2ex_browser_producer --once     # one cycle
  python3 -m openbiliclaw.runtime.v2ex_browser_producer --dry-run  # one cycle, no DB writes
  python3 -m openbiliclaw.runtime.v2ex_browser_producer --no-enrich  # skip detail-page body fetch
  python3 -m openbiliclaw.runtime.v2ex_browser_producer --proxy http://127.0.0.1:7890
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
USER_DATA_DIR = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/v2ex_browser_profile"
DEFAULT_PROXY = "http://127.0.0.1:7890"
PAGE_TIMEOUT_MS = 30000
LIST_WAIT_SECONDS = 5  # wait for Cloudflare + page render
DETAIL_WAIT_SECONDS = 3  # wait for detail page render
ENRICH_DELAY_SECONDS = 1.5  # delay between detail-page visits (anti-rate-limit)
MAX_BODY_LENGTH = 3000  # truncate body text

V2EX_LATEST_URL = "https://www.v2ex.com/?tab=latest"
V2EX_HOT_URL = "https://www.v2ex.com/?tab=hot"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Toggled by --dry-run
_DRY_RUN = False

# Regex to extract topic id from /t/<id> or /t/<id>#reply<N>
_TID_RE = re.compile(r"/t/(\d+)")


def _require_playwright() -> Any:
    """Lazy-import ``playwright.sync_api`` with a helpful error message."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required for v2ex_browser_producer. "
            "Install with: pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright


def _is_challenged(page: Any) -> bool:
    """Check if the page is showing a Cloudflare challenge."""
    try:
        title = page.title().lower()
        if "just a moment" in title or "checking your browser" in title:
            return True
        content = page.content()[:3000].lower()
        return "cf-browser-verification" in content or "challenge-platform" in content
    except Exception:
        return False


def _parse_topic_item(item: Any) -> dict[str, Any] | None:
    """Parse a single .cell.item element into a topic dict."""
    try:
        # Title + href
        title_el = item.query_selector(".item_title a")
        if not title_el:
            return None
        title = title_el.inner_text().strip()
        href = title_el.get_attribute("href") or ""
        if not title or not href:
            return None

        # Topic ID
        tid_m = _TID_RE.search(href)
        if not tid_m:
            return None
        tid = tid_m.group(1)

        # Replies count
        replies = 0
        count_el = item.query_selector(".count_livid, .count_orange")
        if count_el:
            count_text = count_el.inner_text().strip()
            try:
                replies = int(count_text)
            except (ValueError, TypeError):
                replies = 0

        # Author
        author = ""
        author_el = item.query_selector("strong a")
        if author_el:
            author = author_el.inner_text().strip()

        # Node
        node = ""
        node_el = item.query_selector('a[href^="/go/"]')
        if node_el:
            node = node_el.inner_text().strip()

        # Time (from .small.fade)
        post_time = ""
        small_el = item.query_selector(".small.fade")
        if small_el:
            post_time = small_el.inner_text().strip()

        return {
            "id": tid,
            "title": title,
            "author": author,
            "node": node,
            "replies": replies,
            "post_time": post_time,
            "url": f"https://www.v2ex.com/t/{tid}",
        }
    except Exception:
        return None


def _fetch_topic_list(page: Any, url: str, source_tag: str) -> list[dict[str, Any]]:
    """Navigate to a V2EX list page and parse all topic items."""
    logger.info("navigating to %s", url)
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:
        logger.error("navigation to %s failed: %s", url, exc)
        return []

    time.sleep(LIST_WAIT_SECONDS)

    if _is_challenged(page):
        logger.error("Cloudflare challenge detected on %s - proxy may be down", url)
        return []

    items = page.query_selector_all(".cell.item")
    logger.info("found %d topic items on %s", len(items), source_tag)

    topics: list[dict[str, Any]] = []
    for item in items:
        parsed = _parse_topic_item(item)
        if parsed:
            parsed["source"] = source_tag
            topics.append(parsed)
    return topics


def _enrich_topic_body(page: Any, topic: dict[str, Any]) -> None:
    """Visit a topic detail page and extract the full body text (in-place)."""
    tid = topic["id"]
    url = topic["url"]
    try:
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(DETAIL_WAIT_SECONDS)

        if _is_challenged(page):
            logger.warning("Cloudflare challenge on detail page t/%s - skipping body", tid)
            return

        # Body content
        body = ""
        body_el = page.query_selector(".topic_content")
        if body_el:
            body = body_el.inner_text().strip()

        # Also try to get published time + view count from header
        published_at = ""
        view_count = 0
        header_meta = page.query_selector(".header .small.fade")
        if header_meta:
            meta_text = header_meta.inner_text().strip()
            # Format: "author · 7h 28m ago · 6251 views"
            topic["post_time"] = meta_text
            view_m = re.search(r"([\d,]+)\s*views", meta_text)
            if view_m:
                with contextlib.suppress(ValueError, TypeError):
                    view_count = int(view_m.group(1).replace(",", ""))

        if body:
            if len(body) > MAX_BODY_LENGTH:
                body = body[:MAX_BODY_LENGTH] + "..."
            topic["body"] = body
        topic["view_count"] = view_count
        topic["published_at"] = published_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    except Exception as exc:
        logger.warning("enrichment failed for t/%s: %s", tid, exc)


def _fetch_all(limit: int, enrich: bool, proxy: str | None) -> list[dict[str, Any]]:
    """Fetch latest + hot topics, optionally enrich with body.

    Returns a de-duplicated list of topic dicts.
    """
    sync_playwright = _require_playwright()
    all_topics: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    try:
        os.makedirs(USER_DATA_DIR, exist_ok=True)
        with sync_playwright() as pw:
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": USER_DATA_DIR,
                "headless": True,
                "viewport": {"width": 1280, "height": 900},
                "user_agent": USER_AGENT,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            }
            if proxy:
                launch_kwargs["proxy"] = {"server": proxy}
                logger.info("using proxy: %s", proxy)

            context = pw.chromium.launch_persistent_context(**launch_kwargs)
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT_MS)

            try:
                # Fetch latest
                latest = _fetch_topic_list(page, V2EX_LATEST_URL, "v2ex-browser-latest")
                for t in latest:
                    if t["id"] not in seen_ids:
                        seen_ids.add(t["id"])
                        all_topics.append(t)

                # Fetch hot
                hot = _fetch_topic_list(page, V2EX_HOT_URL, "v2ex-browser-hot")
                for t in hot:
                    if t["id"] not in seen_ids:
                        seen_ids.add(t["id"])
                        all_topics.append(t)

                logger.info("discovered %d unique topics (latest+hot)", len(all_topics))

                # Apply limit before enrichment
                if limit and len(all_topics) > limit:
                    all_topics = all_topics[:limit]
                    logger.info("limited to %d topics for enrichment", limit)

                # Enrich with body
                if enrich and all_topics:
                    logger.info("enriching %d topics with body text...", len(all_topics))
                    for i, topic in enumerate(all_topics):
                        _enrich_topic_body(page, topic)
                        if (i + 1) % 10 == 0:
                            logger.info("  enriched %d/%d", i + 1, len(all_topics))
                        if i < len(all_topics) - 1:
                            time.sleep(ENRICH_DELAY_SECONDS)
                    with_body = sum(1 for t in all_topics if t.get("body"))
                    logger.info("enrichment complete: %d/%d have body", with_body, len(all_topics))

            finally:
                with contextlib.suppress(Exception):
                    page.close()
                with contextlib.suppress(Exception):
                    context.close()
    except Exception as exc:
        logger.error("v2ex browser fetch failed: %s", exc, exc_info=True)
        return []

    return all_topics


def _insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
    """Insert rows into content_cache (pool) + articles (library).

    Returns (cache_inserted, cache_skipped, articles_inserted).
    """
    cache_ins = 0
    cache_skip = 0
    art_ins = 0

    for row in rows:
        tid = row["id"]
        title = row["title"]
        author = row.get("author") or ""
        body = row.get("body") or ""
        node = row.get("node") or ""
        published_at = row.get("published_at") or ""
        replies = row.get("replies", 0)
        view_count = row.get("view_count", 0)
        url = row["url"]
        source = row.get("source", "v2ex-browser")

        # --- content_cache ---
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    discovered_at, body_text, like_count, view_count, topic_group
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tid,
                    title,
                    author,
                    author,
                    url,
                    "v2ex",
                    source,
                    "thread",
                    "fresh",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    body,
                    replies,
                    view_count,
                    node,
                ),
            )
            if cur.rowcount > 0:
                cache_ins += 1
            else:
                cache_skip += 1
        except sqlite3.IntegrityError:
            cache_skip += 1

        # --- articles (only when we have a body) ---
        if body:
            try:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO articles (
                        source_type, source_name, title, url, author,
                        content_text, published_at, tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        "v2ex",
                        "V2EX",
                        title,
                        url,
                        author,
                        body,
                        published_at,
                        json.dumps([node] if node else []),
                    ),
                )
                if cur.rowcount > 0:
                    art_ins += 1
            except sqlite3.IntegrityError:
                pass

    return cache_ins, cache_skip, art_ins


def _run_once(limit: int, enrich: bool, proxy: str | None) -> dict[str, Any]:
    """One full fetch cycle."""
    topics = _fetch_all(limit=limit, enrich=enrich, proxy=proxy)
    if not topics:
        return {"ok": False, "reason": "empty_feed", "discovered": 0, "inserted": 0, "articles": 0}

    if _DRY_RUN:
        with_body = sum(1 for t in topics if t.get("body"))
        return {
            "ok": True,
            "dry_run": True,
            "discovered": len(topics),
            "with_body": with_body,
        }

    conn = sqlite3.connect(DB_PATH)
    try:
        cache_ins, cache_skip, art_ins = _insert_rows(conn, topics)
        conn.commit()
        return {
            "ok": True,
            "discovered": len(topics),
            "with_body": sum(1 for t in topics if t.get("body")),
            "cache_inserted": cache_ins,
            "cache_skipped": cache_skip,
            "articles_inserted": art_ins,
        }
    finally:
        conn.close()


def run_forever(interval_hours: int, limit: int, enrich: bool, proxy: str | None) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logger.info(
        "v2ex browser producer started (proxy=%s, interval=%dh, limit=%d, enrich=%s, dry_run=%s)",
        proxy or "none",
        interval_hours,
        limit,
        enrich,
        _DRY_RUN,
    )
    while True:
        logger.info("fetching v2ex topics via browser...")
        result = _run_once(limit=limit, enrich=enrich, proxy=proxy)
        if result["ok"]:
            logger.info(
                "feed ok: %d discovered, %d with body, %d pool new, %d pool dup, %d articles new",
                result["discovered"],
                result.get("with_body", 0),
                result.get("cache_inserted", 0),
                result.get("cache_skipped", 0),
                result.get("articles_inserted", 0),
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))
        time.sleep(interval_hours * 3600)


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="V2EX browser-based feed producer (Playwright + proxy)"
    )
    parser.add_argument("--once", action="store_true", help="Run a single cycle and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes.")
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="Skip detail-page body enrichment (title-only rows).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max topics to process (0 = all discovered, typically ~60).",
    )
    parser.add_argument(
        "--interval", type=int, default=INTERVAL_HOURS, help="Hours between cycles when looping."
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=DEFAULT_PROXY,
        help=f"HTTP proxy server (default: {DEFAULT_PROXY}).",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Disable proxy (direct connection - likely blocked by Cloudflare).",
    )
    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    proxy = None if args.no_proxy else args.proxy

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.once or args.dry_run:
        result = _run_once(limit=args.limit, enrich=not args.no_enrich, proxy=proxy)
        if result["ok"]:
            logger.info(
                "v2ex feed ok: %d discovered, %d with body, %d pool new, "
                "%d pool dup, %d articles new",
                result["discovered"],
                result.get("with_body", 0),
                result.get("cache_inserted", 0),
                result.get("cache_skipped", 0),
                result.get("articles_inserted", 0),
            )
        else:
            logger.warning("v2ex feed skipped: %s", result.get("reason", "unknown"))
        return

    run_forever(
        interval_hours=args.interval,
        limit=args.limit,
        enrich=not args.no_enrich,
        proxy=proxy,
    )


if __name__ == "__main__":
    _main()
