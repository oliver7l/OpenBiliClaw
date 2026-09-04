"""Douyin (抖音) personalized recommendation feed producer.

Uses Playwright to drive a logged-in Chrome session and scrape the
personalized recommendation feed (https://www.douyin.com/?recommend=1).

Why a browser producer
----------------------
抖音推荐流是登录态个性化内容，没有公开 API。唯一稳定的获取方式是
复用已登录的浏览器会话，通过 DOM 提取视频信息。本 producer 支持
三种运行模式：

  * ``--login`` — headed 模式，等待用户扫码登录，保存 user_data_dir
  * ``--headless`` (默认) — 无头模式，复用已保存的 user_data_dir
  * ``--cdp-port`` — 连接到已运行的 Chrome（真实浏览器，无无头检测风险）

Outputs
-------
  * ``content_cache`` (recommendation pool) — one row per video, with
    author / title / interaction counts / video URL.

Usage
-----
  python3 -m openbiliclaw.runtime.douyin_feed_producer --login     # first-time login
  python3 -m openbiliclaw.runtime.douyin_feed_producer --once      # one cycle
  python3 -m openbiliclaw.runtime.douyin_feed_producer --dry-run   # no DB writes
  python3 -m openbiliclaw.runtime.douyin_feed_producer             # loop forever (6h)
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import logging
import os
import re
import sqlite3
import time
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 6
USER_DATA_DIR = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/douyin_profile"
RECOMMEND_URL = "https://www.douyin.com/?recommend=1"
PAGE_TIMEOUT_MS = 30000
SCROLL_PAUSE = 2.0  # seconds between ArrowDown presses
LOGIN_WAIT_TIMEOUT = 300  # seconds to wait for user to scan QR in --login mode

# Strip proxy env vars so the launched browser does not inherit a dead
# local proxy (same rationale as the other CLI producers).
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

_DRY_RUN = False

# ---------------------------------------------------------------------------
# Text parsing helpers
# ---------------------------------------------------------------------------

_COUNT_WAN_RE = re.compile(r"^([\d.]+)\s*万$")
_COUNT_W_RE = re.compile(r"^([\d.]+)\s*[wW]$")
_PURE_NUM_RE = re.compile(r"^\d+$")
_AUTHOR_RE = re.compile(r"^@(\S+)")
_TIME_RE = re.compile(r"·\s*(\d+\s*(?:分钟前|小时前|天前|周前|月前|年前|刚刚))")
_HASHTAG_RE = re.compile(r"#(\S+)")


def _parse_count(raw: str) -> int | None:
    """Parse a Douyin interaction count (``1.4万`` → 14000, ``527`` → 527)."""
    raw = raw.strip()
    if not raw:
        return None
    m = _COUNT_WAN_RE.match(raw)
    if m:
        return int(float(m.group(1)) * 10000)
    m = _COUNT_W_RE.match(raw)
    if m:
        return int(float(m.group(1)) * 10000)
    if _PURE_NUM_RE.match(raw):
        return int(raw)
    return None


def _extract_aweme_id(page: Any) -> str:
    """Extract the current video's aweme_id from the DOM via JS.

    Tries (in order): the active-video container's child links, any
    ``/video/<id>`` link on the page. Returns ``""`` when nothing is
    found — callers should fall back to a hash-based bvid.
    """
    script = """\
() => {
  let id = '';
  const active = document.querySelector('[data-e2e="feed-active-video"]');
  const root = active || document;
  const links = root.querySelectorAll('a[href*="/video/"]');
  for (const a of links) {
    const m = (a.href || '').match(/video\\/(\\d+)/);
    if (m) { id = m[1]; break; }
  }
  return id;
}
"""
    try:
        result = page.evaluate(script)
    except Exception:
        return ""
    if isinstance(result, str) and result:
        return result
    return ""


def _parse_video_text(text: str) -> dict[str, Any] | None:
    """Parse a single video block from Douyin recommend-feed page text.

    Returns a dict with ``author``, ``title``, ``published_at``,
    ``hashtags``, ``likes``, ``comments``, ``favorites``, ``shares``,
    ``duration``, or ``None`` when the text does not contain a valid
    video block.
    """
    lines = [line.strip() for line in text.split("\n")]
    # Find the author line (@nickname)
    author_idx = -1
    author = ""
    for i, line in enumerate(lines):
        m = _AUTHOR_RE.match(line)
        if m:
            author = m.group(1)
            author_idx = i
            break
    if author_idx < 0:
        return None

    # Published time: line right after author often looks like "· 5小时前"
    published_at = ""
    if author_idx + 1 < len(lines):
        m = _TIME_RE.search(lines[author_idx + 1])
        if m:
            published_at = m.group(1)

    # Title / description: lines between author and the next hashtag block
    # or interaction-number block.
    title_parts: list[str] = []
    hashtags: list[str] = []
    for j in range(author_idx + 1, min(author_idx + 8, len(lines))):
        line = lines[j]
        if not line:
            continue
        if _TIME_RE.search(line):
            continue
        # Stop at interaction-number-only lines or next author
        if _parse_count(line) is not None and len(line) <= 10:
            break
        if line.startswith("@"):
            break
        # Collect hashtags
        tags = _HASHTAG_RE.findall(line)
        if tags:
            hashtags.extend(tags)
        title_parts.append(line)
    title = " ".join(title_parts).strip()
    # Truncate overly long titles
    if len(title) > 300:
        title = title[:300]

    # Interaction counts: 4 consecutive numeric lines *above* the author
    # (right-side action bar in the Douyin web feed).
    counts: list[int] = []
    for k in range(author_idx - 1, max(author_idx - 12, -1), -1):
        line = lines[k]
        if not line:
            continue
        num = _parse_count(line)
        if num is not None and len(line) <= 10:
            counts.append(num)
            if len(counts) == 4:
                break
        elif counts:
            break
    counts.reverse()
    while len(counts) < 4:
        counts.append(0)
    likes, comments, favorites, shares = counts[0], counts[1], counts[2], counts[3]

    # Duration: look for "mm:ss / mm:ss" pattern
    duration = ""
    for line in lines:
        m = re.search(r"(\d{2}:\d{2})\s*/\s*(\d{2}:\d{2})", line)
        if m:
            duration = m.group(2)
            break

    return {
        "author": author,
        "title": title,
        "published_at": published_at,
        "hashtags": hashtags,
        "likes": likes,
        "comments": comments,
        "favorites": favorites,
        "shares": shares,
        "duration": duration,
    }


def _is_logged_in(page: Any) -> bool:
    """Detect whether the Douyin page is in a logged-in state.

    Heuristic: the page shows a ``登录`` button or ``扫码登录`` text when
    anonymous; when logged in those are replaced by the user avatar /
    ``我的`` entry. We check for the absence of login-prompt text.
    """
    try:
        text = page.evaluate("() => document.body.innerText")
    except Exception:
        return False
    if not isinstance(text, str):
        return False
    # Anonymous state shows these exact prompts
    if "扫码登录" in text or "验证码登录" in text:
        return False
    # The top nav when logged in still contains "我的" but not "登录" as
    # a standalone action. A loose check: if "登录" appears as its own
    # line, treat as anonymous.
    return all(line.strip() != "登录" for line in text.split("\n"))


# ---------------------------------------------------------------------------
# Browser orchestration
# ---------------------------------------------------------------------------


def _click_recommend_tab(page: Any) -> None:
    """Click the 推荐 nav link to switch from 精选 grid to single-column feed.

    Direct navigation to ``?recommend=1`` lands on the multi-column 精选
    (jingxuan) grid page. The personalized single-column recommendation feed
    is only reachable by clicking the 推荐 tab in the top nav.
    """
    script = """\
() => {
  // Try href-based selector first (most reliable)
  const link = document.querySelector('a[href*="recommend"]');
  if (link) { link.click(); return 'href'; }
  // Fallback: find visible nav element with exact text "推荐"
  const candidates = document.querySelectorAll('a, div, span, li');
  for (const el of candidates) {
    if (el.textContent.trim() === '推荐' && el.offsetParent !== null) {
      el.click();
      return 'text';
    }
  }
  return 'not_found';
}
"""
    try:
        result = page.evaluate(script)
        logger.debug("recommend tab click result: %s", result)
    except Exception as exc:
        logger.debug("recommend tab click failed: %s", exc)


def _require_playwright() -> Any:
    """Lazy-import ``playwright.sync_api`` with a helpful error message."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright not installed. Install with: "
            "pip install 'openbiliclaw[browser]' "
            "and then: playwright install chromium"
        ) from exc
    return sync_playwright


def _fetch_feed(
    limit: int,
    headless: bool,
    cdp_port: int | None,
    login_mode: bool,
) -> list[dict[str, Any]]:
    """Scrape the Douyin recommendation feed and return normalized video dicts.

    Returns ``[]`` on any failure (logged loudly) so the cycle degrades
    gracefully and never dies mid-loop.
    """
    sync_playwright = _require_playwright()
    videos: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    try:
        with sync_playwright() as pw:
            # --- Launch / connect ---
            if cdp_port:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                owns_browser = False
            else:
                os.makedirs(USER_DATA_DIR, exist_ok=True)
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=USER_DATA_DIR,
                    headless=headless and not login_mode,
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/131.0.0.0 Safari/537.36"
                    ),
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-gpu",
                    ],
                    env=CLEAN_ENV,
                )
                # Override navigator.webdriver to reduce headless detection
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page = context.pages[0] if context.pages else context.new_page()
                owns_browser = True

            page.set_default_timeout(PAGE_TIMEOUT_MS)

            try:
                # --- Navigate ---
                logger.info("navigating to %s", RECOMMEND_URL)
                page.goto(RECOMMEND_URL, wait_until="domcontentloaded")
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    logger.debug("networkidle timeout; proceeding with domcontentloaded")
                time.sleep(2)

                # --- Click "推荐" tab to enter single-column feed ---
                # Direct navigation to ?recommend=1 lands on the multi-column
                # 精选 (jingxuan) grid. We must click the 推荐 nav link via JS
                # to switch to the personalized single-column feed.
                _click_recommend_tab(page)
                time.sleep(3)

                # --- Login mode: wait for user to scan QR ---
                if login_mode:
                    logger.info(
                        "LOGIN MODE: please scan the QR code in the browser window. "
                        "Waiting up to %ds...",
                        LOGIN_WAIT_TIMEOUT,
                    )
                    waited = 0
                    while waited < LOGIN_WAIT_TIMEOUT:
                        if _is_logged_in(page):
                            logger.info("login detected! saving profile and exiting.")
                            break
                        time.sleep(5)
                        waited += 5
                    if not _is_logged_in(page):
                        logger.warning(
                            "login timeout — no login detected after %ds",
                            LOGIN_WAIT_TIMEOUT,
                        )
                    # In login mode we don't scrape; just save the profile.
                    return []

                # --- Check login state ---
                if not _is_logged_in(page):
                    logger.error(
                        "not logged in — run with --login first to save the session, "
                        "or use --cdp-port to connect to a logged-in Chrome."
                    )
                    return []

                # --- Scrape: press ArrowDown and extract each video ---
                for i in range(limit):
                    try:
                        text = page.evaluate("() => document.body.innerText")
                        if not isinstance(text, str):
                            text = ""
                    except Exception:
                        text = ""

                    aweme_id = _extract_aweme_id(page)
                    parsed = _parse_video_text(text)

                    if parsed and parsed.get("author"):
                        vid = aweme_id or hashlib.md5(
                            (parsed["author"] + "|" + parsed["title"]).encode("utf-8")
                        ).hexdigest()[:16]
                        if vid not in seen_ids:
                            seen_ids.add(vid)
                            parsed["aweme_id"] = aweme_id
                            parsed["bvid"] = vid
                            parsed["content_url"] = (
                                f"https://www.douyin.com/video/{aweme_id}"
                                if aweme_id
                                else RECOMMEND_URL
                            )
                            videos.append(parsed)
                            logger.info(
                                "  [%d/%d] @%s: %s (likes=%s)",
                                i + 1,
                                limit,
                                parsed["author"],
                                parsed["title"][:40],
                                parsed["likes"],
                            )

                    # Press ArrowDown to next video
                    with contextlib.suppress(Exception):
                        page.keyboard.press("ArrowDown")
                    time.sleep(SCROLL_PAUSE)

            finally:
                with contextlib.suppress(Exception):
                    page.close()
                if owns_browser:
                    with contextlib.suppress(Exception):
                        context.close()
                else:
                    with contextlib.suppress(Exception):
                        browser.close()  # only detaches from CDP
    except Exception as exc:
        logger.error("douyin feed scrape failed: %s", exc, exc_info=True)
        return []

    return videos


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _to_rows(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize scraped videos into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for v in videos:
        if not isinstance(v, dict):
            continue
        bvid = str(v.get("bvid", "") or "").strip()
        title = str(v.get("title", "") or "").strip()
        author = str(v.get("author", "") or "").strip()
        if not bvid or not title or not author:
            continue
        if bvid in seen:
            continue
        seen.add(bvid)
        hashtags = v.get("hashtags", []) or []
        body = title
        if hashtags:
            body += " " + " ".join(f"#{t}" for t in hashtags)
        rows.append(
            {
                "bvid": bvid,
                "title": title,
                "up_name": author,
                "author_name": author,
                "content_url": str(v.get("content_url", RECOMMEND_URL)),
                "source_platform": "douyin",
                "source": "douyin-recommend",
                "content_type": "video",
                "pool_status": "fresh",
                "body_text": body,
                "like_count": int(v.get("likes", 0) or 0),
                "comment_count": int(v.get("comments", 0) or 0),
                "favorite_count": int(v.get("favorites", 0) or 0),
                "share_count": int(v.get("shares", 0) or 0),
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
                    body_text, like_count, comment_count, favorite_count,
                    share_count, discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    row["like_count"],
                    row["comment_count"],
                    row["favorite_count"],
                    row["share_count"],
                    row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            continue
    return inserted


def _run_once(
    limit: int,
    headless: bool,
    cdp_port: int | None,
    login_mode: bool,
) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    if login_mode:
        _fetch_feed(limit=1, headless=False, cdp_port=cdp_port, login_mode=True)
        return {"ok": True, "login_mode": True}

    videos = _fetch_feed(limit=limit, headless=headless, cdp_port=cdp_port, login_mode=False)
    if not videos:
        return {"ok": False, "reason": "empty_feed", "fetched": 0, "inserted": 0}

    rows = _to_rows(videos)
    if not rows:
        return {"ok": False, "reason": "no_valid_videos", "fetched": len(videos), "inserted": 0}

    if _DRY_RUN:
        return {"ok": True, "dry_run": True, "fetched": len(videos), "valid": len(rows)}

    conn = sqlite3.connect(DB_PATH)
    try:
        inserted = _insert_rows(conn, rows)
        conn.commit()
        return {
            "ok": True,
            "fetched": len(videos),
            "valid": len(rows),
            "inserted": inserted,
            "skipped_duplicates": len(rows) - inserted,
        }
    finally:
        conn.close()


def run_forever(
    interval_hours: int,
    limit: int,
    headless: bool,
    cdp_port: int | None,
) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    logger.info(
        "douyin recommend feed producer started "
        "(headless=%s, cdp_port=%s, interval=%dh, limit=%d, dry_run=%s)",
        headless,
        cdp_port,
        interval_hours,
        limit,
        _DRY_RUN,
    )
    while True:
        logger.info("fetching douyin recommend feed...")
        result = _run_once(limit=limit, headless=headless, cdp_port=cdp_port, login_mode=False)
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
    parser = argparse.ArgumentParser(
        description="Douyin personalized recommendation feed producer"
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single cycle and exit (no loop)."
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Fetch + parse but skip DB writes."
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="Headed mode: wait for QR scan and save the session.",
    )
    parser.add_argument("--limit", type=int, default=20, help="Videos to scrape per cycle.")
    parser.add_argument(
        "--interval",
        type=int,
        default=INTERVAL_HOURS,
        help="Hours between cycles when looping.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run headless (default). Use --no-headless for headed.",
    )
    parser.add_argument(
        "--no-headless",
        dest="headless",
        action="store_false",
        help="Run headed (visible browser window).",
    )
    parser.add_argument(
        "--cdp-port",
        type=int,
        default=None,
        help="Connect to a running Chrome via CDP on this port (e.g. 9222).",
    )
    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    if args.login:
        result = _run_once(limit=1, headless=False, cdp_port=args.cdp_port, login_mode=True)
        if result.get("login_mode"):
            logger.info("login session saved to %s", USER_DATA_DIR)
        return

    if args.once or args.dry_run:
        result = _run_once(
            limit=args.limit,
            headless=args.headless,
            cdp_port=args.cdp_port,
            login_mode=False,
        )
        if result["ok"]:
            logger.info(
                "douyin feed ok: %d fetched, %d new, %d duplicate",
                result["fetched"],
                result.get("inserted", 0),
                result.get("skipped_duplicates", 0),
            )
        else:
            logger.warning("douyin feed skipped: %s", result.get("reason", "unknown"))
        return

    run_forever(
        interval_hours=args.interval,
        limit=args.limit,
        headless=args.headless,
        cdp_port=args.cdp_port,
    )


if __name__ == "__main__":
    _main()
