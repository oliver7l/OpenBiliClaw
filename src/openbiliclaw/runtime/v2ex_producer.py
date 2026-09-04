"""Unified V2EX feed producer with four selectable fetch modes.

Modes
-----
``browser``
    Playwright + proxy browser automation. Drives a real Chromium through the
    local Clash proxy to load V2EX pages like a normal browser, passing
    Cloudflare. Scrapes latest + hot topic lists and optionally enriches each
    topic with its full body.

``cli``
    User's authenticated ``v2ex`` CLI for discovery (``topics latest/hot``),
    then enriches each topic with its full body via V2EX's legacy public API
    (``/api/topics/show.json?id=<id>``). Includes Cloudflare risk-control
    detection with hard backoff.

``rss``
    RSSHub mirror (``rsshub.bestblogs.dev``) fetch. Title-only (no body), but
    reliably reachable when V2EX's own API is blocked.

``api``
    Direct V2EX public API (``/api/topics/latest.json`` + ``/api/topics/hot.json``).
    Legacy path; may be blocked by Cloudflare on some networks.

Outputs
-------
  * ``content_cache`` (recommendation pool)
  * ``articles`` (reading library) — only browser/cli modes insert here, and
    only when a body is available.

Usage
-----
  python3 -m openbiliclaw.runtime.v2ex_producer --mode rss                  # loop forever
  python3 -m openbiliclaw.runtime.v2ex_producer --mode cli --once           # one cycle
  python3 -m openbiliclaw.runtime.v2ex_producer --mode browser --dry-run
      # one cycle, no DB writes
  python3 -m openbiliclaw.runtime.v2ex_producer --mode browser --no-enrich  # skip body enrichment
  python3 -m openbiliclaw.runtime.v2ex_producer --mode cli --discover-only  # skip body enrichment
  python3 -m openbiliclaw.runtime.v2ex_producer --mode browser --proxy http://127.0.0.1:7890
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import time
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast

import feedparser

logger = logging.getLogger(__name__)

# --- Shared -----------------------------------------------------------------
DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"

# Toggled by --dry-run; when True all modes fetch/parse but skip DB writes.
_DRY_RUN = False

# --- Browser mode constants -------------------------------------------------
BROWSER_INTERVAL_HOURS = 24
BROWSER_USER_DATA_DIR = (
    "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/v2ex_browser_profile"
)
BROWSER_DEFAULT_PROXY = "http://127.0.0.1:7890"
BROWSER_PAGE_TIMEOUT_MS = 30000
BROWSER_LIST_WAIT_SECONDS = 5
BROWSER_DETAIL_WAIT_SECONDS = 3
BROWSER_ENRICH_DELAY_SECONDS = 1.5
BROWSER_MAX_BODY_LENGTH = 3000
BROWSER_V2EX_LATEST_URL = "https://www.v2ex.com/?tab=latest"
BROWSER_V2EX_HOT_URL = "https://www.v2ex.com/?tab=hot"
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_BROWSER_TID_RE = re.compile(r"/t/(\d+)")

# --- CLI mode constants -----------------------------------------------------
CLI_INTERVAL_HOURS = 24
CLI_RISK_BACKOFF_MULTIPLIER = 3
CLI_DISCOVER_LIMIT = 20
CLI_TIMEOUT = 40
_CLI_V2EX_BIN = shutil.which("v2ex") or "/Users/imac/.local/bin/v2ex"
_CLI_LEGACY_SHOW_URL = "https://www.v2ex.com/api/topics/show.json?id={tid}"
_CLI_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_CLI_CF_MARKERS = (
    "just a moment",
    "cf-browser-verification",
    "cf_chl_opt",
    "challenge-platform",
    "enable javascript and cookies to continue",
)
_CLI_FORBIDDEN_MARKERS = ("access forbidden", "insufficient permissions")

# --- API mode constants -----------------------------------------------------
API_INTERVAL_HOURS = 24
_API_DEFAULT_OPENER = urllib.request.build_opener()
API_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
API_LATEST = "https://www.v2ex.com/api/topics/latest.json"
API_HOT = "https://www.v2ex.com/api/topics/hot.json"

# --- RSS mode constants -----------------------------------------------------
RSS_INTERVAL_HOURS = 6
RSS_URL_LATEST = "https://rsshub.bestblogs.dev/v2ex/topics/latest"
RSS_URL_HOT = "https://rsshub.bestblogs.dev/v2ex/topics/hot"
_RSS_TID_RE = re.compile(r"^/t/(\d+)$")
_RSS_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


# ============================================================================
# Browser mode (Playwright + proxy)
# ============================================================================


def _browser_require_playwright() -> Any:
    """Lazy-import ``playwright.sync_api`` with a helpful error message."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is required for v2ex_producer --mode browser. "
            "Install with: pip install playwright && playwright install chromium"
        ) from exc
    return sync_playwright


def _browser_is_challenged(page: Any) -> bool:
    """Check if the page is showing a Cloudflare challenge."""
    try:
        title = page.title().lower()
        if "just a moment" in title or "checking your browser" in title:
            return True
        content = page.content()[:3000].lower()
        return "cf-browser-verification" in content or "challenge-platform" in content
    except Exception:
        return False


def _browser_parse_topic_item(item: Any) -> dict[str, Any] | None:
    """Parse a single .cell.item element into a topic dict."""
    try:
        title_el = item.query_selector(".item_title a")
        if not title_el:
            return None
        title = title_el.inner_text().strip()
        href = title_el.get_attribute("href") or ""
        if not title or not href:
            return None

        tid_m = _BROWSER_TID_RE.search(href)
        if not tid_m:
            return None
        tid = tid_m.group(1)

        replies = 0
        count_el = item.query_selector(".count_livid, .count_orange")
        if count_el:
            count_text = count_el.inner_text().strip()
            try:
                replies = int(count_text)
            except (ValueError, TypeError):
                replies = 0

        author = ""
        author_el = item.query_selector("strong a")
        if author_el:
            author = author_el.inner_text().strip()

        node = ""
        node_el = item.query_selector('a[href^="/go/"]')
        if node_el:
            node = node_el.inner_text().strip()

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


def _browser_fetch_topic_list(page: Any, url: str, source_tag: str) -> list[dict[str, Any]]:
    """Navigate to a V2EX list page and parse all topic items."""
    logger.info("navigating to %s", url)
    try:
        page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:
        logger.error("navigation to %s failed: %s", url, exc)
        return []

    time.sleep(BROWSER_LIST_WAIT_SECONDS)

    if _browser_is_challenged(page):
        logger.error("Cloudflare challenge detected on %s - proxy may be down", url)
        return []

    items = page.query_selector_all(".cell.item")
    logger.info("found %d topic items on %s", len(items), source_tag)

    topics: list[dict[str, Any]] = []
    for item in items:
        parsed = _browser_parse_topic_item(item)
        if parsed:
            parsed["source"] = source_tag
            topics.append(parsed)
    return topics


def _browser_enrich_topic_body(page: Any, topic: dict[str, Any]) -> None:
    """Visit a topic detail page and extract the full body text (in-place)."""
    tid = topic["id"]
    url = topic["url"]
    try:
        page.goto(url, wait_until="domcontentloaded")
        time.sleep(BROWSER_DETAIL_WAIT_SECONDS)

        if _browser_is_challenged(page):
            logger.warning("Cloudflare challenge on detail page t/%s - skipping body", tid)
            return

        body = ""
        body_el = page.query_selector(".topic_content")
        if body_el:
            body = body_el.inner_text().strip()

        published_at = ""
        view_count = 0
        header_meta = page.query_selector(".header .small.fade")
        if header_meta:
            meta_text = header_meta.inner_text().strip()
            topic["post_time"] = meta_text
            view_m = re.search(r"([\d,]+)\s*views", meta_text)
            if view_m:
                with contextlib.suppress(ValueError, TypeError):
                    view_count = int(view_m.group(1).replace(",", ""))

        if body:
            if len(body) > BROWSER_MAX_BODY_LENGTH:
                body = body[:BROWSER_MAX_BODY_LENGTH] + "..."
            topic["body"] = body
        topic["view_count"] = view_count
        topic["published_at"] = published_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    except Exception as exc:
        logger.warning("enrichment failed for t/%s: %s", tid, exc)


def _browser_fetch_all(limit: int, enrich: bool, proxy: str | None) -> list[dict[str, Any]]:
    """Fetch latest + hot topics, optionally enrich with body.

    Returns a de-duplicated list of topic dicts.
    """
    sync_playwright = _browser_require_playwright()
    all_topics: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    try:
        os.makedirs(BROWSER_USER_DATA_DIR, exist_ok=True)
        with sync_playwright() as pw:
            launch_kwargs: dict[str, Any] = {
                "user_data_dir": BROWSER_USER_DATA_DIR,
                "headless": True,
                "viewport": {"width": 1280, "height": 900},
                "user_agent": BROWSER_USER_AGENT,
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
            page.set_default_timeout(BROWSER_PAGE_TIMEOUT_MS)

            try:
                latest = _browser_fetch_topic_list(
                    page, BROWSER_V2EX_LATEST_URL, "v2ex-browser-latest"
                )
                for t in latest:
                    if t["id"] not in seen_ids:
                        seen_ids.add(t["id"])
                        all_topics.append(t)

                hot = _browser_fetch_topic_list(page, BROWSER_V2EX_HOT_URL, "v2ex-browser-hot")
                for t in hot:
                    if t["id"] not in seen_ids:
                        seen_ids.add(t["id"])
                        all_topics.append(t)

                logger.info("discovered %d unique topics (latest+hot)", len(all_topics))

                if limit and len(all_topics) > limit:
                    all_topics = all_topics[:limit]
                    logger.info("limited to %d topics for enrichment", limit)

                if enrich and all_topics:
                    logger.info("enriching %d topics with body text...", len(all_topics))
                    for i, topic in enumerate(all_topics):
                        _browser_enrich_topic_body(page, topic)
                        if (i + 1) % 10 == 0:
                            logger.info("  enriched %d/%d", i + 1, len(all_topics))
                        if i < len(all_topics) - 1:
                            time.sleep(BROWSER_ENRICH_DELAY_SECONDS)
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


def _browser_insert_rows(
    conn: sqlite3.Connection, rows: list[dict[str, Any]]
) -> tuple[int, int, int]:
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


def _browser_run_once(limit: int, enrich: bool, proxy: str | None) -> dict[str, Any]:
    """One full fetch cycle."""
    topics = _browser_fetch_all(limit=limit, enrich=enrich, proxy=proxy)
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
        cache_ins, cache_skip, art_ins = _browser_insert_rows(conn, topics)
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


def _browser_run_forever(interval_hours: int, limit: int, enrich: bool, proxy: str | None) -> None:
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
        result = _browser_run_once(limit=limit, enrich=enrich, proxy=proxy)
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


# ============================================================================
# CLI mode (v2ex CLI discovery + legacy API enrichment)
# ============================================================================


def _cli_looks_like_challenge(payload: bytes | str) -> bool:
    """True if the payload is a Cloudflare interstitial rather than JSON."""
    if isinstance(payload, bytes):
        try:
            payload = payload.decode("utf-8", "replace")
        except Exception:
            return False
    head = payload[:2000].lower()
    return any(m in head for m in _CLI_CF_MARKERS)


def _cli_run_cli_topics(command: str, limit: int) -> tuple[str | None, bool]:
    """Run ``v2ex topics <command> --limit <limit>``.

    Returns ``(stdout_or_None, risk_control)``. ``risk_control`` is True when
    the failure looks like a Cloudflare challenge (403) rather than a plain
    outage — callers must back off instead of retrying.
    """
    try:
        proc = subprocess.run(
            [_CLI_V2EX_BIN, "topics", command, "--limit", str(limit)],
            capture_output=True,
            text=True,
            timeout=CLI_TIMEOUT,
        )
    except FileNotFoundError:
        logger.error("v2ex CLI not found at %s", _CLI_V2EX_BIN)
        return None, False
    except subprocess.TimeoutExpired:
        logger.error("v2ex topics %s timed out after %ds", command, CLI_TIMEOUT)
        return None, False

    if proc.returncode != 0:
        blob = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        lines = [
            ln
            for ln in blob.strip().splitlines()
            if "UserWarning" not in ln and "parser =" not in ln and "self.parse_args" not in ln
        ]
        detail = " | ".join(lines[-3:]) or "no output"
        lowered = blob.lower()
        if any(m in lowered for m in _CLI_FORBIDDEN_MARKERS):
            logger.warning(
                "v2ex topics %s: platform risk-control detected "
                "(Cloudflare challenge) - backing off, NOT retrying: %s",
                command,
                detail,
            )
            return None, True
        logger.error("v2ex topics %s failed (rc=%d): %s", command, proc.returncode, detail)
        return None, False
    return proc.stdout or "", False


def _cli_parse_topics_table(text: str) -> list[dict[str, Any]]:
    """Parse a rich table from ``v2ex topics latest/hot`` into row dicts.

    Robust to the real-world output, which differs from the source's
    assumptions:
      * Titles WRAP across multiple physical lines (rich wraps long cells),
        so a logical row may span several ``│...│`` lines — only the first
        carries the ID; continuation lines have an empty ID column and the
        title fragment in the Title column.
      * The Author column is EMPTY for node-topic listings (V2EX API 2.0
        ``/nodes/{node}/topics`` omits the member field), so we must accept
        a blank author and rely on legacy-API enrichment later.
    """
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line.lstrip().startswith("│"):
            continue
        cells = [c.strip() for c in line.split("│")]
        if len(cells) < 6:
            continue
        cid, title, author, replies, ttime = (cells[1], cells[2], cells[3], cells[4], cells[5])
        if cid and cid.isdigit():
            if current:
                rows.append(current)
            current = {
                "id": cid,
                "title": title,
                "author": author,
                "replies": replies,
                "time": ttime,
            }
        elif current is not None and title:
            current["title"] = (current["title"] + " " + title).strip()

    if current:
        rows.append(current)

    out: list[dict[str, Any]] = []
    for r in rows:
        if not r["id"].isdigit():
            continue
        try:
            reply_count = int(r["replies"]) if str(r["replies"]).isdigit() else 0
        except (ValueError, TypeError):
            reply_count = 0
        out.append(
            {
                "id": r["id"],
                "title": r["title"].replace("\n", " ").strip(),
                "author": (r["author"] or "").strip(),
                "replies": reply_count,
            }
        )
    return out


def _cli_discover(limit: int) -> tuple[list[dict[str, Any]], str, bool]:
    """Discover new V2EX topics via the CLI.

    Returns ``(rows, status, risk_control)``.
    """
    all_rows: list[dict[str, Any]] = []
    ok_any = False
    risk_control = False
    for command, source in (("latest", "v2ex-cli-latest"), ("hot", "v2ex-cli-hot")):
        text, risky = _cli_run_cli_topics(command, limit)
        if risky:
            risk_control = True
            break
        if text is None:
            continue
        parsed = _cli_parse_topics_table(text)
        if parsed:
            ok_any = True
            for r in parsed:
                r["source"] = source
            all_rows.extend(parsed)

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in all_rows:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        deduped.append(r)

    status = "risk_control" if risk_control else "ok" if ok_any else "cli_failed"
    return deduped, status, risk_control


def _cli_fetch_topic_body(topic_id: str) -> tuple[dict[str, Any] | None, bool]:
    """Fetch full topic via legacy API.

    Returns ``(normalized_dict_or_None, risk_control)``. A Cloudflare
    challenge (403 / interstitial HTML) sets ``risk_control`` so callers can
    abort the remaining per-topic calls instead of hammering the platform.
    """
    url = _CLI_LEGACY_SHOW_URL.format(tid=topic_id)
    req = urllib.request.Request(url, headers={"User-Agent": _CLI_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            logger.warning(
                "v2ex legacy t/%s: 403 (Cloudflare challenge) - risk control, aborting enrichment",
                topic_id,
            )
            return None, True
        try:
            if _cli_looks_like_challenge(exc.read(2000)):
                logger.warning(
                    "v2ex legacy t/%s: challenge page on HTTP %s - "
                    "risk control, aborting enrichment",
                    topic_id,
                    exc.code,
                )
                return None, True
        except Exception:
            pass
        logger.warning("v2ex legacy fetch failed for t/%s: HTTP %s", topic_id, exc.code)
        return None, False
    except Exception as exc:
        logger.warning("v2ex legacy fetch failed for t/%s: %s", topic_id, exc)
        return None, False

    if _cli_looks_like_challenge(raw):
        logger.warning(
            "v2ex legacy t/%s: challenge page instead of JSON - risk control, aborting enrichment",
            topic_id,
        )
        return None, True

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("v2ex legacy JSON decode failed for t/%s: %s", topic_id, exc)
        return None, False

    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None, False

    member = data.get("member") or {}
    node = data.get("node") or {}
    created = data.get("created") or data.get("last_modified") or 0
    try:
        published_at = datetime.fromtimestamp(int(created), tz=UTC).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        published_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "title": data.get("title", ""),
        "content": data.get("content") or "",
        "author": (member.get("username") or "").strip(),
        "node": node.get("title") or node.get("name") or "",
        "published_at": published_at,
        "replies": data.get("replies") or 0,
    }, False


def _cli_insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> tuple[int, int, int]:
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
        url = f"https://www.v2ex.com/t/{tid}"

        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, content_url,
                    source_platform, source, content_type, pool_status,
                    discovered_at, body_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tid,
                    title,
                    author,
                    author,
                    url,
                    "v2ex",
                    row.get("source", "v2ex-cli"),
                    "thread",
                    "fresh",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    body,
                ),
            )
            if cur.rowcount > 0:
                cache_ins += 1
            else:
                cache_skip += 1
        except sqlite3.IntegrityError:
            cache_skip += 1

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


def _cli_run_once(limit: int, discover_only: bool) -> dict[str, Any]:
    """One full fetch cycle: discovery via CLI + best-effort body enrichment."""
    rows, status, risk_control = _cli_discover(limit)
    if not rows:
        return {
            "ok": False,
            "reason": status,
            "risk_control": risk_control,
            "discovered": 0,
            "inserted": 0,
            "articles": 0,
        }

    enriched = 0
    for r in rows:
        if discover_only:
            continue
        body_data, risky = _cli_fetch_topic_body(r["id"])
        if risky:
            risk_control = True
            logger.warning(
                "risk-control hit on t/%s; aborting enrichment "
                "for the remaining %d topics this cycle",
                r["id"],
                len(rows) - enriched - 1,
            )
            break
        if body_data:
            r["body"] = body_data["content"]
            r["author"] = body_data["author"] or r["author"]
            r["node"] = body_data["node"]
            r["published_at"] = body_data["published_at"]
            enriched += 1

    if _DRY_RUN:
        return {
            "ok": True,
            "dry_run": True,
            "risk_control": risk_control,
            "discovered": len(rows),
            "would_have_body": enriched,
        }

    conn = sqlite3.connect(DB_PATH)
    try:
        cache_ins, cache_skip, art_ins = _cli_insert_rows(conn, rows)
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "risk_control": risk_control,
        "discovered": len(rows),
        "with_body": enriched,
        "cache_inserted": cache_ins,
        "cache_skipped": cache_skip,
        "articles_inserted": art_ins,
    }


def _cli_run_forever(interval_hours: int, limit: int, discover_only: bool) -> None:
    """Main loop with Cloudflare risk-control backoff."""
    logger.info(
        "v2ex cli producer started (cli=%s, interval=%dh, limit=%d, dry_run=%s, discover_only=%s)",
        _CLI_V2EX_BIN,
        interval_hours,
        limit,
        _DRY_RUN,
        discover_only,
    )
    while True:
        result = _cli_run_once(limit, discover_only)
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

        if result.get("risk_control"):
            sleep_hours = interval_hours * CLI_RISK_BACKOFF_MULTIPLIER
            logger.warning(
                "platform risk-control detected - backing off %.0fh "
                "(%dh x%d). Do NOT tighten this without checking the "
                "platform rate-limit policy.",
                sleep_hours,
                interval_hours,
                CLI_RISK_BACKOFF_MULTIPLIER,
            )
        else:
            sleep_hours = interval_hours
        time.sleep(sleep_hours * 3600)


# ============================================================================
# API mode (direct V2EX public API, legacy)
# ============================================================================


def _api_fetch_json(url: str) -> list[dict[str, Any]]:
    """Fetch a V2EX API endpoint and return the parsed JSON array."""
    req = urllib.request.Request(url, headers={"User-Agent": API_USER_AGENT})
    try:
        with _API_DEFAULT_OPENER.open(req, timeout=30) as resp:
            parsed = json.loads(resp.read().decode("utf-8"))
            return cast("list[dict[str, Any]]", parsed)
    except Exception as exc:
        logger.error("V2EX API request failed for %s: %s", url, exc)
        return []


def _api_parse_topics(topics: list[dict[str, Any]], source: str) -> list[dict[str, Any]]:
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
        body_text = ""
        if body_plain:
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


def _api_insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
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


def _api_run_once() -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    latest_topics = _api_fetch_json(API_LATEST)
    latest_rows = _api_parse_topics(latest_topics, "v2ex-feed") if latest_topics else []

    hot_topics = _api_fetch_json(API_HOT)
    hot_rows = _api_parse_topics(hot_topics, "v2ex-feed") if hot_topics else []

    all_rows = latest_rows + hot_rows
    if not all_rows:
        return {"ok": False, "reason": "no_data", "items_fetched": 0, "inserted": 0}

    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for row in all_rows:
        if row["bvid"] not in seen:
            seen.add(row["bvid"])
            deduped.append(row)

    if _DRY_RUN:
        return {
            "ok": True,
            "dry_run": True,
            "items_fetched": len(latest_topics) + len(hot_topics),
            "valid_items": len(deduped),
            "would_insert": len(deduped),
        }

    conn = sqlite3.connect(DB_PATH)
    try:
        inserted = _api_insert_rows(conn, deduped)
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


def _api_run_forever(interval_hours: int) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logger.info("v2ex api producer started (interval=%dh)", interval_hours)
    while True:
        logger.info("fetching v2ex topics...")
        result = _api_run_once()
        if result["ok"]:
            logger.info(
                "feed ok: %d fetched, %d new, %d duplicate",
                result["items_fetched"],
                result["inserted"],
                result["skipped_duplicates"],
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))
        time.sleep(interval_hours * 3600)


# ============================================================================
# RSS mode (RSSHub mirror)
# ============================================================================


def _rss_fetch_feed(url: str, timeout: int = 30) -> list[dict[str, Any]]:
    """Fetch an RSSHub endpoint and return list of feed entry dicts.

    Returns an empty list on any failure (HTTP error, parse error,
    empty feed). Errors are logged so the failure is visible.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _RSS_USER_AGENT})
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


def _rss_parse_published(entry: dict[str, Any]) -> str:
    """Best-effort parse of an entry's published time to 'YYYY-MM-DD HH:MM:SS'.

    Falls back to the current time if the field is missing or unparseable.
    """
    raw = getattr(entry, "published", "") or getattr(entry, "updated", "") or ""
    if not raw:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        dt = parsedate_to_datetime(raw)
        return dt.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _rss_parse_entries(entries: list[dict[str, Any]], source_tag: str) -> list[dict[str, Any]]:
    """Convert feedparser entries into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in entries:
        link = (getattr(entry, "link", "") or "").strip()
        if not link:
            continue
        m = _RSS_TID_RE.search(link.replace("https://www.v2ex.com", ""))
        if not m:
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


def _rss_insert_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> tuple[int, int]:
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


def _rss_run_once() -> dict[str, Any]:
    """One full fetch cycle across both RSSHub endpoints."""
    total_fetched = 0
    total_inserted = 0
    total_skipped = 0
    ok_any = False
    for url, tag in ((RSS_URL_LATEST, "v2ex-rss-latest"), (RSS_URL_HOT, "v2ex-rss-hot")):
        entries = _rss_fetch_feed(url)
        if not entries:
            continue
        ok_any = True
        total_fetched += len(entries)
        rows = _rss_parse_entries(entries, tag)
        if not rows:
            continue
        if _DRY_RUN:
            total_inserted += len(rows)
            continue
        conn = sqlite3.connect(DB_PATH)
        try:
            inserted, skipped = _rss_insert_rows(conn, rows)
            conn.commit()
            total_inserted += inserted
            total_skipped += skipped
        finally:
            conn.close()

    if _DRY_RUN:
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


def _rss_run_forever(interval_hours: int) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logger.info("v2ex rss producer started (interval=%dh, dry_run=%s)", interval_hours, _DRY_RUN)
    while True:
        logger.info("fetching v2ex RSSHub feeds...")
        result = _rss_run_once()
        if result["ok"]:
            logger.info(
                "feed ok: %d fetched, %d new, %d duplicate",
                result["fetched"],
                result["inserted"],
                result["skipped_duplicates"],
            )
        else:
            logger.warning("feed skipped: %s", result.get("reason", "unknown"))
        time.sleep(interval_hours * 3600)


# ============================================================================
# Unified main
# ============================================================================


def _default_interval(mode: str) -> int:
    """Return the mode-specific default interval in hours."""
    if mode == "rss":
        return RSS_INTERVAL_HOURS
    return 24


def _default_limit(mode: str) -> int:
    """Return the mode-specific default limit (0 = all)."""
    if mode == "cli":
        return CLI_DISCOVER_LIMIT
    return 0


def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified V2EX feed producer (browser/cli/rss/api modes)"
    )
    parser.add_argument(
        "--mode",
        choices=["browser", "cli", "rss", "api"],
        default="rss",
        help="Fetch mode: browser (Playwright+proxy), cli (v2ex CLI+legacy API), "
        "rss (RSSHub mirror), api (direct V2EX API). Default: rss.",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single cycle and exit (implied by --dry-run)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch + parse but skip DB writes (implies --once)."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max topics to process (0 = all). Default: 20 for cli, 0 for others.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Hours between cycles when looping. Default: 6 for rss, 24 for others.",
    )

    # Browser-specific
    parser.add_argument(
        "--no-enrich",
        action="store_true",
        help="[browser] Skip detail-page body enrichment (title-only rows).",
    )
    parser.add_argument(
        "--proxy",
        type=str,
        default=BROWSER_DEFAULT_PROXY,
        help=f"[browser] HTTP proxy server (default: {BROWSER_DEFAULT_PROXY}).",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="[browser] Disable proxy (direct connection - likely blocked by Cloudflare).",
    )

    # CLI-specific
    parser.add_argument(
        "--discover-only",
        action="store_true",
        help="[cli] Skip legacy body enrichment (title-only rows).",
    )

    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    interval = args.interval if args.interval is not None else _default_interval(args.mode)
    limit = args.limit if args.limit is not None else _default_limit(args.mode)
    once = args.once or args.dry_run

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # --- Single-cycle modes ---
    if once:
        if args.mode == "browser":
            proxy = None if args.no_proxy else args.proxy
            result = _browser_run_once(limit=limit, enrich=not args.no_enrich, proxy=proxy)
        elif args.mode == "cli":
            result = _cli_run_once(limit=limit, discover_only=args.discover_only)
        elif args.mode == "rss":
            result = _rss_run_once()
        else:  # api
            result = _api_run_once()

        if result["ok"]:
            if args.mode == "browser":
                logger.info(
                    "v2ex feed ok: %d discovered, %d with body, %d pool new, "
                    "%d pool dup, %d articles new",
                    result["discovered"],
                    result.get("with_body", 0),
                    result.get("cache_inserted", 0),
                    result.get("cache_skipped", 0),
                    result.get("articles_inserted", 0),
                )
            elif args.mode == "cli":
                if _DRY_RUN:
                    logger.info(
                        "dry-run ok: %d discovered, %d would have body",
                        result["discovered"],
                        result.get("would_have_body", 0),
                    )
                else:
                    logger.info(
                        "feed ok: %d discovered, %d with body, %d pool new, "
                        "%d pool dup, %d articles new",
                        result["discovered"],
                        result.get("with_body", 0),
                        result.get("cache_inserted", 0),
                        result.get("cache_skipped", 0),
                        result.get("articles_inserted", 0),
                    )
            elif args.mode == "rss":
                if _DRY_RUN:
                    logger.info(
                        "dry-run ok: %d fetched, %d would_insert, %d duplicate",
                        result["fetched"],
                        result["would_insert"],
                        result["skipped_duplicates"],
                    )
                else:
                    logger.info(
                        "feed ok: %d fetched, %d new, %d duplicate",
                        result["fetched"],
                        result["inserted"],
                        result["skipped_duplicates"],
                    )
            else:  # api
                if _DRY_RUN:
                    logger.info(
                        "dry-run ok: %d fetched, %d valid, %d would_insert",
                        result["items_fetched"],
                        result["valid_items"],
                        result["would_insert"],
                    )
                else:
                    logger.info(
                        "feed ok: %d fetched, %d new, %d duplicate",
                        result["items_fetched"],
                        result["inserted"],
                        result["skipped_duplicates"],
                    )
        else:
            logger.warning("v2ex feed skipped: %s", result.get("reason", "unknown"))
        return

    # --- Forever-loop modes ---
    if args.mode == "browser":
        proxy = None if args.no_proxy else args.proxy
        _browser_run_forever(
            interval_hours=interval, limit=limit, enrich=not args.no_enrich, proxy=proxy
        )
    elif args.mode == "cli":
        _cli_run_forever(interval_hours=interval, limit=limit, discover_only=args.discover_only)
    elif args.mode == "rss":
        _rss_run_forever(interval_hours=interval)
    else:  # api
        _api_run_forever(interval_hours=interval)


if __name__ == "__main__":
    _main()
