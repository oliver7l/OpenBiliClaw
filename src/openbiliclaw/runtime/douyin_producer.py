"""Runtime Douyin producers.

This module contains two Douyin producers:

1. ``DouyinDiscoveryProducer`` — throttle and invoke Douyin discovery from
   the runtime loop. The continuous refresh controller owns pool quotas;
   this producer owns the throttled call into the reusable Douyin discovery
   service when the Douyin platform family is under quota.

2. Douyin personalized recommendation feed producer — uses Playwright to
   drive a logged-in Chrome session and scrape the personalized
   recommendation feed, 精选 (jingxuan) grid, liked videos, and favorited
   videos.

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
     python3 -m openbiliclaw.runtime.douyin_producer --login     # first-time login
     python3 -m openbiliclaw.runtime.douyin_producer --once      # one cycle
     python3 -m openbiliclaw.runtime.douyin_producer --dry-run   # no DB writes
     python3 -m openbiliclaw.runtime.douyin_producer             # loop forever (6h)
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import logging
import math
import os
import re
import sqlite3
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from openbiliclaw.discovery.douyin import DouyinDiscoveryOptions, DouyinDiscoveryResult
from openbiliclaw.runtime._db import connect_inbox as _obc_connect
from openbiliclaw.runtime.keyword_fetch import PLATFORM_DOUYIN as _PLATFORM_DOUYIN
from openbiliclaw.sources.douyin_plugin_search import (
    DouyinBudgetExhausted as _DouyinBudgetExhausted,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Discovery producer constants
# ---------------------------------------------------------------------------

DouyinDiscoverCallable = Callable[[Any, DouyinDiscoveryOptions], Awaitable[DouyinDiscoveryResult]]
_DOUYIN_SCORE_THRESHOLDS = {
    "search": 0.60,
    "hot": 0.60,
    "feed": 0.60,
}
_DOUYIN_DEFAULT_SCORE_THRESHOLD = _DOUYIN_SCORE_THRESHOLDS["search"]

# ---------------------------------------------------------------------------
# Feed producer constants
# ---------------------------------------------------------------------------

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
USER_DATA_DIR = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/douyin_profile"
RECOMMEND_URL = "https://www.douyin.com/?recommend=1"
JINGXUAN_URL = "https://www.douyin.com/jingxuan"
USER_SELF_URL = "https://www.douyin.com/user/self"
LIKES_URL = "https://www.douyin.com/user/self?showTab=like"
PAGE_TIMEOUT_MS = 30000
SCROLL_PAUSE = 2.0  # seconds between ArrowDown presses
JINGXUAN_SCROLL_PAUSE = 2.5  # seconds between page scrolls
USER_LIST_SCROLL_PAUSE = 2.5  # seconds between page scrolls for likes/favorites
DEFAULT_AUTHOR = "抖音用户"  # fallback when list pages don't show author
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
# Discovery producer
# ---------------------------------------------------------------------------


def douyin_runtime_hot_budget(*, base_budget: int, requested_limit: int) -> int:
    """Return the effective hot-task budget for one runtime replenishment run."""
    configured = int(base_budget)
    if configured <= 0:
        return 0
    requested = max(1, int(requested_limit))
    if requested < 10:
        return configured
    return max(configured, min(60, requested))


@dataclass
class DouyinDiscoveryProducer:
    """Throttle and invoke Douyin discovery from the runtime loop."""

    soul_engine: Any
    discover: DouyinDiscoverCallable
    enabled: bool = True
    min_interval_minutes: int = 30
    sources: tuple[str, ...] = ("search", "hot", "feed")
    evaluate: bool = True
    candidate_pipeline: Any | None = None
    per_source_limit: int = 20
    # Unified keyword planner fetch coordinator (P1.7). When wired AND the flag
    # is on, the producer's search source claims words from the keyword store
    # and walks them through the inline-admit lifecycle (used / failed /
    # budget-rollback). ``None`` (default / tests / flag off) → legacy path.
    keyword_fetch: Any | None = None
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        """Run one Douyin discovery cycle if enabled and due."""
        if not self.enabled:
            return self._skip("disabled")
        if not self._is_due():
            return self._skip("throttled")
        if self._candidate_pool_full():
            return self._skip("pool_full")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("douyin producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or self.per_source_limit))
        selected_sources = self._sources_for_limit(requested_limit)
        per_source_limit = max(
            1,
            min(
                self.per_source_limit,
                math.ceil(requested_limit / max(1, len(selected_sources))),
            ),
        )
        use_candidate_pipeline = self.candidate_pipeline is not None

        # Unified keyword planner fetch path (P1.7, flag-gated). Only when this
        # run actually includes the ``search`` source — hot/feed-only runs never
        # touch the keyword store. The deficit gate is enforced upstream (the
        # controller only invokes the producer when douyin is under quota); the
        # distinct floor is ``min_interval`` via ``_is_due`` above.
        claimed: list[Any] = []
        coordinator = self.keyword_fetch
        flag_on_search = (
            coordinator is not None
            and bool(getattr(coordinator, "should_claim", lambda: False)())
            and "search" in selected_sources
        )
        if flag_on_search and coordinator is not None:
            claimed = coordinator.claim(_PLATFORM_DOUYIN)
            if not claimed:
                # Flag on but the store has no claimable pending words → skip the
                # search fetch this cycle (the planner will refill); don't run a
                # legacy self-generated search behind the planner's back.
                return self._skip("no_keywords")

        options = DouyinDiscoveryOptions(
            limit=requested_limit,
            sources=selected_sources,
            cache=not use_candidate_pipeline,
            evaluate=False if use_candidate_pipeline else self.evaluate,
            per_source_limit=per_source_limit,
            keywords_per_run=1,
            keywords=tuple(item.keyword for item in claimed) if claimed else (),
            # P1.8: thread the producing word's id onto each search candidate for
            # admit-time yield backfill.
            keyword_ids={item.keyword: int(item.id) for item in claimed} if claimed else {},
            raise_on_budget=bool(claimed),
        )
        try:
            result = await self.discover(profile, options)
        except _DouyinBudgetExhausted:
            # Claimed but the plugin search budget was spent → no search ran →
            # roll every claimed word back to pending (do NOT burn as used).
            if coordinator is not None:
                for item in claimed:
                    coordinator.rollback(item)
            return self._skip("budget_exhausted")
        except Exception as exc:
            logger.warning("douyin producer failed: %s", exc)
            if claimed and coordinator is not None:
                coordinator.mark_failed(claimed)
            return self._skip("error")

        # Inline-admit lifecycle: a successful return that produced candidates
        # marks every claimed word ``used``; an empty fetch marks them ``failed``
        # (retry). yield backfill is P1.8, decoupled from ``used``.
        if claimed and coordinator is not None:
            if result.items:
                coordinator.mark_used(claimed)
            else:
                coordinator.mark_failed(claimed)

        self._last_run_at = datetime.now(UTC)
        payload: dict[str, object] = {
            "discovered": len(result.items),
            "source_counts": dict(result.source_counts),
            "reason": "ok",
        }
        if self.candidate_pipeline is None:
            payload["cached"] = result.cached
            return payload

        self._stamp_candidate_score_thresholds(result.items)
        enqueued = int(
            self.candidate_pipeline.enqueue_candidates(
                list(result.items),
                source_context="douyin",
            )
        )
        payload["enqueued"] = enqueued
        if enqueued > 0:
            drain_result = await self.candidate_pipeline.drain_pending(
                profile=profile,
                batch_size=requested_limit,
            )
            payload.update(drain_result)
        return payload

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _sources_for_limit(self, requested_limit: int) -> tuple[str, ...]:
        configured = tuple(source for source in self.sources if str(source).strip())
        if requested_limit >= 10:
            selected = tuple(source for source in ("search", "hot") if source in configured)
            if selected:
                return selected
            return configured[:1] or ("search",)

        preferred = ("feed",) if requested_limit <= 3 else ("hot", "feed")
        selected = tuple(source for source in preferred if source in configured)
        if selected:
            return selected

        non_search = tuple(source for source in configured if source != "search")
        if non_search:
            return non_search[:1]
        return configured[:1] or ("search",)

    def _candidate_pool_full(self) -> bool:
        if self.candidate_pipeline is None:
            return False
        pool_full = getattr(self.candidate_pipeline, "pool_full", None)
        if not callable(pool_full):
            return False
        try:
            return bool(pool_full())
        except Exception:
            logger.debug("douyin producer: candidate pool fullness unavailable", exc_info=True)
            return False

    def _stamp_candidate_score_thresholds(self, items: list[Any]) -> None:
        for item in items:
            try:
                if float(getattr(item, "score_threshold", 0.0) or 0.0) > 0:
                    continue
                item.score_threshold = self._score_threshold_for_item(item)
            except Exception:
                logger.debug("douyin producer: failed to stamp score threshold", exc_info=True)

    @staticmethod
    def _score_threshold_for_item(item: Any) -> float:
        strategy = str(getattr(item, "source_strategy", "") or "").strip().lower()
        for key, threshold in _DOUYIN_SCORE_THRESHOLDS.items():
            if key in strategy:
                return threshold
        return _DOUYIN_DEFAULT_SCORE_THRESHOLD

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("douyin producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


def build_douyin_discovery_producer(
    *,
    config: Any,
    database: Any,
    soul_engine: Any,
    discovery_engine: Any,
    candidate_pipeline: Any | None = None,
    keyword_fetch: Any | None = None,
) -> DouyinDiscoveryProducer | None:
    """Build the runtime Douyin producer if Douyin discovery is enabled."""
    dy_cfg = getattr(getattr(config, "sources", None), "douyin", None)
    if dy_cfg is None or not bool(getattr(dy_cfg, "enabled", False)):
        return None
    if str(getattr(dy_cfg, "mode", "direct")).strip().lower() != "direct":
        logger.info("douyin producer disabled: unsupported mode=%r", getattr(dy_cfg, "mode", ""))
        return None
    if not hasattr(database, "conn"):
        logger.info("douyin producer disabled: database does not expose task tables")
        return None

    async def _discover(profile: Any, options: DouyinDiscoveryOptions) -> DouyinDiscoveryResult:
        from openbiliclaw.discovery.douyin import DouyinDiscoveryService
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie
        from openbiliclaw.sources.douyin_direct import DouyinDirectClient
        from openbiliclaw.sources.douyin_plugin_search import DouyinPluginSearchClient

        cookie_env = str(getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"))
        cookie = resolve_douyin_cookie(
            data_dir=config.data_path,
            cookie_env=cookie_env,
        )
        if not cookie:
            raise RuntimeError(
                f"missing Douyin cookie; set {cookie_env} or keep the browser extension online"
            )

        async with DouyinDirectClient(cookie=cookie) as direct_client:
            client: Any = direct_client
            if any(source in options.sources for source in ("search", "hot", "feed")):
                wait_seconds = float(
                    os.environ.get("OPENBILICLAW_DY_DISCOVERY_SEARCH_WAIT_SECONDS", "180")
                )
                client = DouyinPluginSearchClient(
                    database=database,
                    direct_client=direct_client,
                    wait_seconds=wait_seconds,
                    daily_search_budget=int(getattr(dy_cfg, "daily_search_budget", 0)),
                    daily_hot_budget=douyin_runtime_hot_budget(
                        base_budget=int(getattr(dy_cfg, "daily_hot_budget", 0)),
                        requested_limit=options.limit,
                    ),
                    daily_feed_budget=int(getattr(dy_cfg, "daily_feed_budget", 0)),
                    # Unified keyword planner fetch path: surface budget
                    # exhaustion as a distinguishable signal so the claimed
                    # keyword rolls back instead of being burned (P1.7).
                    raise_on_budget=bool(getattr(options, "raise_on_budget", False)),
                )
            service = DouyinDiscoveryService(
                client=client,
                discovery_engine=discovery_engine,
            )
            return await service.discover(profile, options)

    scheduler = getattr(config, "scheduler", None)
    return DouyinDiscoveryProducer(
        soul_engine=soul_engine,
        discover=_discover,
        enabled=bool(getattr(scheduler, "enabled", True)),
        min_interval_minutes=30,
        sources=("search", "hot", "feed"),
        candidate_pipeline=candidate_pipeline,
        per_source_limit=20,
        keyword_fetch=keyword_fetch,
    )


# ---------------------------------------------------------------------------
# Feed producer — text parsing helpers
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
# Feed producer — browser orchestration
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


_DURATION_RE = re.compile(r"^\d{1,2}:\d{2}$")
_DATE_RE = re.compile(r"·\s*(\d+\s*月\s*\d+\s*日|\d+\s*天前|\d+\s*小时前|\d+\s*分钟前|刚刚)")


def _parse_jingxuan_cards(text: str) -> list[dict[str, Any]]:
    """Parse video cards from the Douyin 精选 (jingxuan) multi-column grid.

    Each card in the page innerText follows the pattern::

        mm:ss            ← duration
        1234 / 1.2万     ← like count
        标题文字 #话题1 #话题2
        @作者名
         · 8月14日        ← publish date

    Ads (``广告``) and live streams (``直播中``) are skipped. Returns a
    list of dicts with ``author``, ``title``, ``hashtags``, ``likes``,
    ``duration``, ``published_at``.
    """
    lines = [line.strip() for line in text.split("\n")]
    cards: list[dict[str, Any]] = []

    i = 0
    while i < len(lines):
        # Card starts with a duration line (mm:ss)
        if not _DURATION_RE.match(lines[i]):
            i += 1
            continue

        duration = lines[i]
        i += 1

        # Next line: like count (may be absent for ads/live)
        likes = 0
        if i < len(lines):
            num = _parse_count(lines[i])
            if num is not None:
                likes = num
                i += 1

        # Title lines: collect until we hit @author or another duration
        title_parts: list[str] = []
        hashtags: list[str] = []
        author = ""
        published_at = ""
        is_ad_or_live = False

        while i < len(lines):
            line = lines[i]
            if not line:
                i += 1
                continue
            # Stop at next card's duration
            if _DURATION_RE.match(line) and title_parts:
                break
            # Stop at @author
            if line.startswith("@") and len(line) > 1:
                author = line[1:]
                i += 1
                # Next line is publish date
                if i < len(lines):
                    m = _DATE_RE.search(lines[i])
                    if m:
                        published_at = m.group(1)
                        i += 1
                break
            # Skip ad / live markers
            if line in ("广告", "直播中") or line.endswith("正在直播"):
                is_ad_or_live = True
                i += 1
                continue
            # Collect hashtags
            tags = _HASHTAG_RE.findall(line)
            if tags:
                hashtags.extend(tags)
            title_parts.append(line)
            i += 1

        if is_ad_or_live or not author or not title_parts:
            continue

        title = " ".join(title_parts).strip()
        if len(title) > 300:
            title = title[:300]

        cards.append(
            {
                "author": author,
                "title": title,
                "hashtags": hashtags,
                "likes": likes,
                "duration": duration,
                "published_at": published_at,
                "comments": 0,
                "favorites": 0,
                "shares": 0,
            }
        )

    return cards


def _extract_all_aweme_ids(page: Any) -> list[str]:
    """Extract all aweme_ids from /video/ links on the page (for jingxuan grid)."""
    script = """\
() => {
  const ids = [];
  const seen = new Set();
  const links = document.querySelectorAll('a[href*="/video/"]');
  for (const a of links) {
    const m = (a.href || '').match(/video\\/(\\d+)/);
    if (m && !seen.has(m[1])) {
      seen.add(m[1]);
      ids.push(m[1]);
    }
  }
  return ids;
}
"""
    try:
        result = page.evaluate(script)
    except Exception:
        return []
    if isinstance(result, list):
        return [str(x) for x in result if x]
    return []


def _fetch_jingxuan(
    limit: int,
    headless: bool,
    cdp_port: int | None,
) -> list[dict[str, Any]]:
    """Scrape the Douyin 精选 (jingxuan) multi-column grid feed.

    The jingxuan page is a public multi-column grid (no login required,
    though a logged-in profile may show personalized results). Cards are
    loaded lazily as the page scrolls. Returns ``[]`` on failure.
    """
    sync_playwright = _require_playwright()
    videos: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    max_scrolls = max(3, (limit // 12) + 2)  # ~12 cards per scroll

    try:
        with sync_playwright() as pw:
            if cdp_port:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                owns_browser = False
            else:
                os.makedirs(USER_DATA_DIR, exist_ok=True)
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=USER_DATA_DIR,
                    headless=headless,
                    viewport={"width": 1280, "height": 900},
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
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page = context.pages[0] if context.pages else context.new_page()
                owns_browser = True

            page.set_default_timeout(PAGE_TIMEOUT_MS)

            try:
                logger.info("navigating to %s", JINGXUAN_URL)
                page.goto(JINGXUAN_URL, wait_until="domcontentloaded")
                with contextlib.suppress(Exception):
                    page.wait_for_load_state("networkidle", timeout=8000)
                time.sleep(3)

                for scroll_idx in range(max_scrolls):
                    # Parse current page text
                    try:
                        text = page.evaluate("() => document.body.innerText")
                        if not isinstance(text, str):
                            text = ""
                    except Exception:
                        text = ""

                    aweme_ids = _extract_all_aweme_ids(page)
                    cards = _parse_jingxuan_cards(text)

                    for idx, card in enumerate(cards):
                        if not card.get("author"):
                            continue
                        aweme_id = aweme_ids[idx] if idx < len(aweme_ids) else ""
                        vid = (
                            aweme_id
                            or hashlib.md5(
                                (card["author"] + "|" + card["title"]).encode("utf-8")
                            ).hexdigest()[:16]
                        )
                        if vid in seen_ids:
                            continue
                        seen_ids.add(vid)
                        card["aweme_id"] = aweme_id
                        card["bvid"] = vid
                        card["content_url"] = (
                            f"https://www.douyin.com/video/{aweme_id}" if aweme_id else JINGXUAN_URL
                        )
                        videos.append(card)
                        if len(videos) >= limit:
                            break

                    logger.info(
                        "  jingxuan scroll %d/%d: %d cards parsed, %d unique total",
                        scroll_idx + 1,
                        max_scrolls,
                        len(cards),
                        len(videos),
                    )

                    if len(videos) >= limit:
                        break

                    # Scroll down to load more cards
                    with contextlib.suppress(Exception):
                        page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
                    time.sleep(JINGXUAN_SCROLL_PAUSE)

            finally:
                with contextlib.suppress(Exception):
                    page.close()
                if owns_browser:
                    with contextlib.suppress(Exception):
                        context.close()
                else:
                    with contextlib.suppress(Exception):
                        browser.close()
    except Exception as exc:
        logger.error("douyin jingxuan scrape failed: %s", exc, exc_info=True)
        return []

    return videos


# ---------------------------------------------------------------------------
# Feed producer — user list (likes / favorites) scraping
# ---------------------------------------------------------------------------


def _parse_user_list_card(card_text: str) -> dict[str, Any] | None:
    """Parse a single card from the likes/favorites grid.

    Card text format (order may vary between likes and favorites)::

        1.4万              ← like count
        标题文字 #话题标签

    Returns a dict with ``title``, ``hashtags``, ``likes``, or ``None``
    when the text does not contain a valid card.
    """
    lines = [line.strip() for line in card_text.split("\n") if line.strip()]
    if not lines:
        return None

    likes = 0
    title_parts: list[str] = []
    for line in lines:
        num = _parse_count(line)
        if num is not None and len(line) <= 10:
            likes = num
        else:
            title_parts.append(line)

    title = " ".join(title_parts).strip()
    if not title:
        return None
    if len(title) > 300:
        title = title[:300]

    hashtags = _HASHTAG_RE.findall(title)
    return {
        "title": title,
        "hashtags": hashtags,
        "likes": likes,
        "comments": 0,
        "favorites": 0,
        "shares": 0,
        "duration": "",
        "published_at": "",
    }


def _extract_user_list_cards(page: Any) -> list[dict[str, Any]]:
    """Extract all video cards from the likes/favorites page via JS.

    Returns a list of dicts with ``aweme_id`` and ``text`` (card innerText).
    """
    script = """\
() => {
  const cards = [];
  const seen = new Set();
  const links = document.querySelectorAll('a[href*="/video/"]');
  for (const a of links) {
    const m = (a.href || '').match(/video\\/(\\d+)/);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    // Walk up to find the card container
    let card = a.closest(
      'div[class*="card"], div[class*="item"], li, ' +
      'div[class*="video"], div[class*="work"]'
    );
    if (!card) {
      let el = a.parentElement;
      for (let i = 0; i < 5 && el; i++) {
        if (el.innerText && el.innerText.length > 5) { card = el; break; }
        el = el.parentElement;
      }
    }
    const text = card ? card.innerText : a.textContent;
    cards.push({aweme_id: m[1], text: text || ''});
  }
  return cards;
}
"""
    try:
        result = page.evaluate(script)
    except Exception:
        return []
    if not isinstance(result, list):
        return []
    cards: list[dict[str, Any]] = []
    for c in result:
        if not isinstance(c, dict):
            continue
        cards.append(
            {
                "aweme_id": str(c.get("aweme_id", "")),
                "text": str(c.get("text", "")),
            }
        )
    return cards


def _click_favorites_tab(page: Any) -> None:
    """Click the 收藏 tab on the user profile page."""
    script = """\
() => {
  const candidates = document.querySelectorAll('div, span, a, li');
  for (const el of candidates) {
    if (el.textContent.trim() === '收藏' && el.offsetParent !== null) {
      el.click();
      return true;
    }
  }
  return false;
}
"""
    with contextlib.suppress(Exception):
        page.evaluate(script)


def _fetch_user_list(
    list_type: str,
    limit: int,
    headless: bool,
    cdp_port: int | None,
) -> list[dict[str, Any]]:
    """Scrape the user's liked or favorited videos.

    Args:
        list_type: ``"like"`` or ``"favorite"``.
        limit: Maximum number of videos to fetch.

    Returns a list of normalized video dicts, or ``[]`` on failure.

    """
    sync_playwright = _require_playwright()
    videos: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    max_scrolls = max(3, (limit // 15) + 2)

    try:
        with sync_playwright() as pw:
            if cdp_port:
                browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                owns_browser = False
            else:
                os.makedirs(USER_DATA_DIR, exist_ok=True)
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=USER_DATA_DIR,
                    headless=headless,
                    viewport={"width": 1280, "height": 900},
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
                context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page = context.pages[0] if context.pages else context.new_page()
                owns_browser = True

            page.set_default_timeout(PAGE_TIMEOUT_MS)

            try:
                # Navigate and switch to the right tab
                if list_type == "like":
                    logger.info("navigating to likes page: %s", LIKES_URL)
                    page.goto(LIKES_URL, wait_until="domcontentloaded")
                else:
                    logger.info("navigating to user profile for favorites")
                    page.goto(USER_SELF_URL, wait_until="domcontentloaded")
                    time.sleep(3)
                    _click_favorites_tab(page)
                time.sleep(4)

                # Check login state
                if not _is_logged_in(page):
                    logger.error(
                        "not logged in — run with --login first, "
                        "or use --cdp-port to connect to a logged-in Chrome."
                    )
                    return []

                for scroll_idx in range(max_scrolls):
                    raw_cards = _extract_user_list_cards(page)
                    for raw in raw_cards:
                        aweme_id = raw.get("aweme_id", "")
                        if not aweme_id or aweme_id in seen_ids:
                            continue
                        parsed = _parse_user_list_card(raw.get("text", ""))
                        if not parsed or not parsed.get("title"):
                            continue
                        seen_ids.add(aweme_id)
                        parsed["aweme_id"] = aweme_id
                        parsed["bvid"] = aweme_id
                        parsed["author"] = DEFAULT_AUTHOR
                        parsed["content_url"] = f"https://www.douyin.com/video/{aweme_id}"
                        videos.append(parsed)
                        if len(videos) >= limit:
                            break

                    logger.info(
                        "  %s scroll %d/%d: %d cards extracted, %d unique total",
                        list_type,
                        scroll_idx + 1,
                        max_scrolls,
                        len(raw_cards),
                        len(videos),
                    )

                    if len(videos) >= limit:
                        break

                    # Scroll down to load more
                    with contextlib.suppress(Exception):
                        page.evaluate("window.scrollBy(0, window.innerHeight * 1.5)")
                    time.sleep(USER_LIST_SCROLL_PAUSE)

            finally:
                with contextlib.suppress(Exception):
                    page.close()
                if owns_browser:
                    with contextlib.suppress(Exception):
                        context.close()
                else:
                    with contextlib.suppress(Exception):
                        browser.close()
    except Exception as exc:
        logger.error("douyin %s scrape failed: %s", list_type, exc, exc_info=True)
        return []

    return videos


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
                        vid = (
                            aweme_id
                            or hashlib.md5(
                                (parsed["author"] + "|" + parsed["title"]).encode("utf-8")
                            ).hexdigest()[:16]
                        )
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
# Feed producer — DB helpers
# ---------------------------------------------------------------------------


def _to_rows(
    videos: list[dict[str, Any]],
    source: str = "douyin-recommend",
) -> list[dict[str, Any]]:
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
                "content_url": str(v.get("content_url", JINGXUAN_URL)),
                "source_platform": "douyin",
                "source": source,
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
    jingxuan_mode: bool = False,
    user_list_mode: str | None = None,
) -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict.

    Args:
        user_list_mode: ``"like"``, ``"favorite"``, or ``None`` (default
            recommend feed).

    """
    if login_mode:
        _fetch_feed(limit=1, headless=False, cdp_port=cdp_port, login_mode=True)
        return {"ok": True, "login_mode": True}

    if user_list_mode == "like":
        source = "douyin-likes"
        videos = _fetch_user_list("like", limit=limit, headless=headless, cdp_port=cdp_port)
    elif user_list_mode == "favorite":
        source = "douyin-favorites"
        videos = _fetch_user_list("favorite", limit=limit, headless=headless, cdp_port=cdp_port)
    elif jingxuan_mode:
        source = "douyin-jingxuan"
        videos = _fetch_jingxuan(limit=limit, headless=headless, cdp_port=cdp_port)
    else:
        source = "douyin-recommend"
        videos = _fetch_feed(limit=limit, headless=headless, cdp_port=cdp_port, login_mode=False)

    if not videos:
        return {"ok": False, "reason": "empty_feed", "fetched": 0, "inserted": 0}

    rows = _to_rows(videos, source=source)
    if not rows:
        return {"ok": False, "reason": "no_valid_videos", "fetched": len(videos), "inserted": 0}

    if _DRY_RUN:
        return {"ok": True, "dry_run": True, "fetched": len(videos), "valid": len(rows)}

    conn = _obc_connect("douyin")
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
    jingxuan_mode: bool = False,
    user_list_mode: str | None = None,
) -> None:
    """Main loop: fetch every ``interval_hours`` hours."""
    if user_list_mode:
        feed_label = user_list_mode
    elif jingxuan_mode:
        feed_label = "jingxuan"
    else:
        feed_label = "recommend"
    logger.info(
        "douyin %s feed producer started "
        "(headless=%s, cdp_port=%s, interval=%dh, limit=%d, dry_run=%s)",
        feed_label,
        headless,
        cdp_port,
        interval_hours,
        limit,
        _DRY_RUN,
    )
    while True:
        logger.info("fetching douyin %s feed...", feed_label)
        result = _run_once(
            limit=limit,
            headless=headless,
            cdp_port=cdp_port,
            login_mode=False,
            jingxuan_mode=jingxuan_mode,
            user_list_mode=user_list_mode,
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


# ---------------------------------------------------------------------------
# Feed producer — CLI entry
# ---------------------------------------------------------------------------


def _main() -> None:
    parser = argparse.ArgumentParser(description="Douyin personalized recommendation feed producer")
    parser.add_argument(
        "--once", action="store_true", help="Run a single cycle and exit (no loop)."
    )
    parser.add_argument("--dry-run", action="store_true", help="Fetch + parse but skip DB writes.")
    parser.add_argument(
        "--login",
        action="store_true",
        help="Headed mode: wait for QR scan and save the session.",
    )
    parser.add_argument("--limit", type=int, default=50, help="Videos to scrape per cycle.")
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
    parser.add_argument(
        "--jingxuan",
        action="store_true",
        help="Scrape the 精选 (jingxuan) public grid feed instead of personalized recommend.",
    )
    parser.add_argument(
        "--likes",
        action="store_true",
        help="Scrape the user's liked videos (requires login).",
    )
    parser.add_argument(
        "--favorites",
        action="store_true",
        help="Scrape the user's favorited/collected videos (requires login).",
    )
    args = parser.parse_args()

    global _DRY_RUN
    _DRY_RUN = args.dry_run

    # Determine user list mode
    user_list_mode: str | None = None
    if args.likes:
        user_list_mode = "like"
    elif args.favorites:
        user_list_mode = "favorite"

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
            jingxuan_mode=args.jingxuan,
            user_list_mode=user_list_mode,
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
        jingxuan_mode=args.jingxuan,
        user_list_mode=user_list_mode,
    )


if __name__ == "__main__":
    _main()
