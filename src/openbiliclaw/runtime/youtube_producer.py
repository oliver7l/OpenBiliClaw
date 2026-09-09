"""Runtime YouTube producer: discovery + recommendation feed.

YouTube steady-state discovery is backend-direct: the runtime can call
scrapetube / yt-dlp backed strategies itself and does not need the
browser-extension task queue used by bootstrap imports.

The recommendation-feed half calls ``yt-dlp --cookies-from-browser chrome``
to fetch the YouTube recommended feed (same as youtube.com homepage) on a
schedule and inserts new videos into ``content_cache`` so they become
available in the recommendation pool.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
import subprocess
import time
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from openbiliclaw.runtime._db import connect_inbox as _obc_connect
from openbiliclaw.runtime.keyword_fetch import PLATFORM_YOUTUBE as _PLATFORM_YOUTUBE

logger = logging.getLogger(__name__)

# ── Discovery constants ────────────────────────────────────────────────────

YOUTUBE_DISCOVERY_STRATEGIES = ("yt_search", "yt_trending", "yt_channel")
_YT_SEARCH = "yt_search"
_YOUTUBE_SCORE_THRESHOLDS = {
    "yt_search": 0.60,
    "yt_trending": 0.60,
    "yt_channel": 0.60,
}


@dataclass(frozen=True)
class YoutubeStrategyRunResult:
    """Result summary for one YouTube strategy execution."""

    items: list[Any]
    units_used: int
    source_counts: dict[str, int]


YoutubeDiscoverCallable = Callable[..., Awaitable[YoutubeStrategyRunResult]]


@dataclass
class YoutubeDiscoveryProducer:
    """Throttle and invoke YouTube discovery from the runtime loop."""

    database: Any
    soul_engine: Any
    discover: YoutubeDiscoverCallable
    enabled: bool = True
    min_interval_minutes: int = 60
    daily_search_budget: int = 0
    daily_trending_budget: int = 0
    daily_channel_budget: int = 0
    strategies: tuple[str, ...] = YOUTUBE_DISCOVERY_STRATEGIES
    candidate_pipeline: Any | None = None
    # Unified keyword planner fetch coordinator (P1.7). When wired AND the flag
    # is on, the ``yt_search`` strategy claims words from the keyword store and
    # injects them as ``queries``; the words are marked ``used`` once the raw
    # candidates are handed off to the candidate pipeline (fetch-only: admission
    # is downstream). ``None`` (default / flag off) → legacy self-gen path.
    keyword_fetch: Any | None = None
    _last_run_at: datetime | None = field(default=None, init=False)
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(self, *, limit: int | None = None) -> dict[str, object]:
        """Run one YouTube discovery cycle if enabled, due, and under budget."""
        if not self.enabled:
            return self._skip("disabled")
        if not self._is_due():
            return self._skip("throttled")
        if self._candidate_pool_full():
            return self._skip("pool_full")

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.debug("youtube producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        requested_limit = max(1, int(limit or 10))
        remaining = self.remaining_budgets(per_run_budget=requested_limit)
        runnable = [strategy for strategy in self.strategies if int(remaining.get(strategy, 0)) > 0]
        if not runnable:
            return self._skip("budget_exhausted")

        discovered_total = 0
        enqueued_total = 0
        source_counts: Counter[str] = Counter()
        error_count = 0

        # Unified keyword planner fetch path (P1.7, flag-gated): claim words once
        # for ``yt_search`` and inject them as ``queries``. The deficit gate is
        # upstream; the distinct floor is ``min_interval`` / ``_is_due`` above;
        # the per-strategy daily budget still gates the run.
        claimed_search: list[Any] = []
        coordinator = self.keyword_fetch
        flag_on = coordinator is not None and bool(
            getattr(coordinator, "should_claim", lambda: False)()
        )
        if flag_on and coordinator is not None and _YT_SEARCH in runnable:
            claimed_search = coordinator.claim(_PLATFORM_YOUTUBE)
            if not claimed_search:
                # Flag on but the store has no claimable pending words → drop
                # yt_search this cycle (the planner refills); other strategies
                # (trending / channel) still run on their own budgets.
                runnable = [s for s in runnable if s != _YT_SEARCH]

        search_handed_off = False
        for strategy in runnable:
            unit_budget = max(0, int(remaining.get(strategy, 0)))
            if unit_budget <= 0:
                continue
            extra: dict[str, Any] = {}
            if strategy == _YT_SEARCH and claimed_search:
                extra["queries"] = [item.keyword for item in claimed_search]
                # P1.8: thread the producing word's id onto each candidate for
                # admit-time yield backfill.
                extra["keyword_ids"] = {item.keyword: int(item.id) for item in claimed_search}
            try:
                result = await self.discover(
                    profile,
                    strategy=strategy,
                    unit_budget=unit_budget,
                    result_limit=requested_limit,
                    **extra,
                )
            except Exception as exc:
                error_count += 1
                logger.warning(
                    "youtube producer strategy failed: strategy=%s error=%s",
                    strategy,
                    exc,
                )
                self.record_strategy_run(
                    strategy,
                    units_used=0,
                    discovered=0,
                    reason="error",
                )
                continue

            units_used = max(0, min(unit_budget, int(result.units_used)))
            discovered = len(result.items)
            self.record_strategy_run(
                strategy,
                units_used=units_used,
                discovered=discovered,
                reason="ok",
            )
            discovered_total += discovered
            source_counts.update(result.source_counts)
            if self.candidate_pipeline is not None and result.items:
                self._stamp_candidate_score_thresholds(result.items, strategy=strategy)
                enqueued_total += int(
                    self.candidate_pipeline.enqueue_candidates(
                        list(result.items),
                        source_context=strategy,
                    )
                )
            if strategy == _YT_SEARCH:
                # Fetch-only: the claimed words are consumed on the handoff of
                # raw candidates to the candidate pipeline — mark them ``used``.
                # When no pipeline is wired (cache mode), reaching this point
                # after a successful strategy return still counts as consumed.
                search_handed_off = True

        # Fetch-only lifecycle: yt_search words → ``used`` once handed off (yield
        # backfill is P1.8). If the strategy errored (never handed off), leave
        # them claimed — the lease reclaim returns them to pending.
        if claimed_search and self.keyword_fetch is not None and search_handed_off:
            self.keyword_fetch.mark_used(claimed_search)
        elif claimed_search and self.keyword_fetch is not None:
            self.keyword_fetch.mark_failed(claimed_search)

        self._last_run_at = datetime.now(UTC)
        if discovered_total <= 0 and error_count >= len(runnable):
            return {"discovered": 0, "reason": "error"}
        payload: dict[str, object] = {
            "discovered": discovered_total,
            "source_counts": dict(source_counts),
            "reason": "ok",
        }
        if self.candidate_pipeline is not None:
            payload["enqueued"] = enqueued_total
            if enqueued_total > 0:
                drain_result = await self.candidate_pipeline.drain_pending(
                    profile=profile,
                    batch_size=requested_limit,
                )
                payload.update(drain_result)
        return payload

    def remaining_budgets(self, *, per_run_budget: int | None = None) -> dict[str, int]:
        """Return runnable execution units by YouTube strategy.

        ``daily_*_budget == 0`` means no per-day cap, matching the Bilibili
        producer style: every due run is bounded by the runtime deficit /
        ``discovery_limit`` passed in as ``per_run_budget``.
        """
        run_budget = max(1, int(per_run_budget or 10))
        configured = {
            "yt_search": int(self.daily_search_budget),
            "yt_trending": int(self.daily_trending_budget),
            "yt_channel": int(self.daily_channel_budget),
        }
        remaining: dict[str, int] = {}
        for strategy, budget in configured.items():
            if budget == 0:
                remaining[strategy] = run_budget
            elif budget < 0:
                remaining[strategy] = 0
            else:
                remaining[strategy] = max(0, budget - self.consumed_today(strategy))
        return remaining

    @property
    def _discovery_conn(self):
        """获取 discovery.db 连接，回退到主库连接。"""
        conn = getattr(self.database, '_discovery_conn', None)
        return conn if conn is not None else self.database.conn

    def consumed_today(self, strategy: str) -> int:
        """Return today's successful execution units for one strategy."""
        self._ensure_ledger_table()
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        row = self._discovery_conn.execute(
            """
            SELECT COALESCE(SUM(units), 0)
            FROM youtube_discovery_runs
            WHERE strategy = ? AND created_at >= ? AND reason = 'ok'
            """,
            (strategy, today),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def record_strategy_run(
        self,
        strategy: str,
        *,
        units_used: int,
        discovered: int,
        reason: str,
    ) -> None:
        """Record one strategy execution in the daily budget ledger."""
        self._ensure_ledger_table()
        self._discovery_conn.execute(
            """
            INSERT INTO youtube_discovery_runs(strategy, units, discovered, reason)
            VALUES (?, ?, ?, ?)
            """,
            (
                strategy,
                max(0, int(units_used)),
                max(0, int(discovered)),
                reason,
            ),
        )
        self._discovery_conn.commit()

    def _ensure_ledger_table(self) -> None:
        self._discovery_conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS youtube_discovery_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                units INTEGER NOT NULL DEFAULT 0,
                discovered INTEGER NOT NULL DEFAULT 0,
                reason TEXT NOT NULL DEFAULT 'ok',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_youtube_discovery_runs_strategy_created
                ON youtube_discovery_runs(strategy, created_at);
            """
        )
        self._discovery_conn.commit()

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        if self._last_run_at is None:
            return True
        return datetime.now(UTC) - self._last_run_at >= timedelta(minutes=self.min_interval_minutes)

    def _candidate_pool_full(self) -> bool:
        if self.candidate_pipeline is None:
            return False
        pool_full = getattr(self.candidate_pipeline, "pool_full", None)
        if not callable(pool_full):
            return False
        try:
            return bool(pool_full())
        except Exception:
            logger.debug("youtube producer: candidate pool fullness unavailable", exc_info=True)
            return False

    def _stamp_candidate_score_thresholds(self, items: list[Any], *, strategy: str) -> None:
        threshold = _YOUTUBE_SCORE_THRESHOLDS.get(strategy, 0.60)
        for item in items:
            try:
                if float(getattr(item, "score_threshold", 0.0) or 0.0) > 0:
                    continue
                item.score_threshold = threshold
            except Exception:
                logger.debug("youtube producer: failed to stamp score threshold", exc_info=True)

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("youtube producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"discovered": 0, "reason": reason}


# ── Recommendation feed constants ──────────────────────────────────────────

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24
CLEAN_ENV = os.environ.copy()
CLEAN_ENV.pop("PYTHONHOME", None)
CLEAN_ENV.pop("PYTHONPATH", None)
CLEAN_ENV.pop("__PYVENV_LAUNCHER__", None)
# 清掉代理环境变量：本机代理（Clash 类）经常重启/挂掉，挂掉时 yt-dlp 走代理会拿到
# "Unable to connect to proxy ... 502 Bad Gateway"，表现为 "yt-dlp returned 0 items"
# （退出码仍是 0，所以不会报错、只会被当成空 feed 跳过，很难发现）。
# 实测清掉代理后直连可正常抓取推荐页（与 cli.py 的 _strip_proxy_env 同一思路）。
for _proxy_key in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    CLEAN_ENV.pop(_proxy_key, None)

YT_COOKIES_FILE = os.path.join(os.path.dirname(DB_PATH), "youtube_cookies.txt")
YT_DLP_CMD = [
    "yt-dlp",
    "--cookies",
    YT_COOKIES_FILE,
    "--flat-playlist",
    "--print",
    "%(title)s|%(uploader)s|%(view_count)s|%(webpage_url)s",
    "https://www.youtube.com/feed/recommended",
]

# YouTube video ID: 11 characters, [a-zA-Z0-9_-]
_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def _fetch_feed() -> list[dict[str, Any]]:
    """Call yt-dlp and return parsed items."""
    try:
        result = subprocess.run(
            YT_DLP_CMD,
            capture_output=True,
            text=True,
            env=CLEAN_ENV,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        logger.warning("yt-dlp timed out after 120s (cookies or network issue)")
        return []
    except FileNotFoundError:
        logger.error("yt-dlp not found on PATH")
        return []
    except OSError as exc:
        logger.error("yt-dlp subprocess error: %s", exc)
        return []
    if result.returncode != 0:
        logger.error("yt-dlp failed (rc=%d): %s", result.returncode, result.stderr[:500])
        return []

    lines = [ln.strip() for ln in result.stdout.split("\n") if ln.strip()]
    if not lines:
        logger.info("yt-dlp returned 0 items")
        return []

    items: list[dict[str, Any]] = []
    for line in lines:
        parts = line.split("|", 3)
        if len(parts) < 4:
            continue
        title, uploader, view_count_str, url = parts
        if not title.strip():
            continue

        url = url.strip()
        vid = _extract_video_id(url)
        if not vid:
            continue

        view_count = 0
        try:
            view_count = int(view_count_str) if view_count_str != "NA" else 0
        except ValueError:
            view_count = 0

        items.append(
            {
                "title": title.strip(),
                "uploader": uploader.strip() if uploader != "NA" else "",
                "view_count": view_count,
                "url": url,
                "video_id": vid,
            }
        )
    return items


def _extract_video_id(url: str) -> str | None:
    """Extract YouTube video ID from URL."""
    m = re.search(r"(?:v=|youtu\.be/|/v/|embed/|shorts/)([a-zA-Z0-9_-]{11})", url)
    if m:
        return m.group(1)
    return None


def _parse_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        vid = item["video_id"]
        if vid in seen:
            continue
        seen.add(vid)

        title = item["title"]
        if not title:
            continue

        rows.append(
            {
                "bvid": vid,
                "title": title,
                "up_name": item["uploader"],
                "author_name": item["uploader"],
                "content_url": item["url"],
                "source_platform": "youtube",
                "source": "youtube-feed",
                "content_type": "video",
                "pool_status": "fresh",
                "view_count": item["view_count"],
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
                    view_count, discovered_at
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
                    row["view_count"],
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

    conn = _obc_connect("youtube")
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
    logger.info("youtube feed producer started (interval=%dh)", INTERVAL_HOURS)

    while True:
        logger.info("fetching youtube recommend...")
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
