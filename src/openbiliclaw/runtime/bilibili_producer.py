"""Runtime Bilibili producers: extension-search fallback and recommendation feed.

The extension-search producer enqueues browser search tasks when the Bilibili
API search is degraded. The recommendation-feed scheduler calls the Bilibili
recommend API (same as bilibili.com homepage) every 24 hours using the SESSDATA
from the bili CLI credential store, and inserts new videos into
``content_cache`` so they become available in the recommendation pool.
"""

from __future__ import annotations

import inspect
import json
import logging
import sqlite3
import time
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from openbiliclaw.discovery.strategies._utils import (
    build_profile_summary,
    search_cooldown_remaining,
)
from openbiliclaw.llm.json_utils import parse_llm_json_tolerant
from openbiliclaw.llm.prompts import build_search_queries_prompt
from openbiliclaw.runtime.keyword_fetch import PLATFORM_BILIBILI as _PLATFORM_BILIBILI

if TYPE_CHECKING:
    from openbiliclaw.llm.service import LLMService
    from openbiliclaw.soul.profile import SoulProfile
    from openbiliclaw.sources.bili_tasks import BiliTaskQueue

logger = logging.getLogger(__name__)

BiliKickCallable = Callable[[], Awaitable[None] | None]

# ---------------------------------------------------------------------------
# Recommendation feed constants
# ---------------------------------------------------------------------------

DB_PATH = "/Volumes/固态硬盘1T/002-探索项目/040-OpenBiliClaw/data/openbiliclaw.db"
INTERVAL_HOURS = 24

# 直连 opener：urllib 默认会读 env 里的 http(s)_proxy（本机 Clash 类代理），
# 代理挂掉时请求失败、抓取静默返回空，很难排查。这里显式禁用代理，直连更稳。
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
CREDENTIAL_PATH = Path.home() / ".bilibili-cli" / "credential.json"
RECOMMEND_URL = (
    "https://api.bilibili.com/x/web-interface/index/top/feed/rcmd"
    "?y_num=5&fresh_type=4&fresh_idx=1&fresh_idx_1h=1"
)

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


# ---------------------------------------------------------------------------
# Recommendation feed functions
# ---------------------------------------------------------------------------


def _load_sessdata() -> str | None:
    """Read SESSDATA from bili CLI credential file."""
    try:
        cred = json.loads(CREDENTIAL_PATH.read_text())
        sessdata: str = str(cred.get("sessdata", "")).strip()
        if sessdata:
            return sessdata
        logger.warning("credential file has no sessdata field")
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        logger.error("failed to read credential: %s", exc)
    return None


def _fetch_feed() -> list[dict[str, Any]]:
    """Call the Bilibili recommend API and return parsed items."""
    sessdata = _load_sessdata()
    if not sessdata:
        logger.error("no SESSDATA available, cannot fetch bilibili recommend")
        return []

    headers = {**REQUEST_HEADERS, "Cookie": f"SESSDATA={sessdata}"}
    req = urllib.request.Request(RECOMMEND_URL, headers=headers)

    try:
        with _NO_PROXY_OPENER.open(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.error("bilibili recommend API request failed: %s", exc)
        return []

    if data.get("code") != 0:
        logger.warning("bilibili recommend API error: %s", data.get("message", "unknown"))
        return []

    items: list[dict[str, Any]] = cast("list[dict[str, Any]]", data.get("data", {}).get("item", []))
    if not items:
        logger.info("bilibili recommend returned 0 items")
        return []
    return items


def _format_duration(seconds: int) -> str:
    """Convert seconds to mm:ss or hh:mm:ss format."""
    if not seconds:
        return ""
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _parse_feed_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract fields from recommend items into content_cache-compatible rows."""
    now = datetime.now()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        bvid = str(item.get("bvid", "")).strip()
        if not bvid or not bvid.startswith("BV"):
            continue
        if bvid in seen:
            continue
        seen.add(bvid)

        owner = item.get("owner", {}) or {}
        stat = item.get("stat", {}) or {}

        title = str(item.get("title", "") or "").strip()
        if not title:
            continue

        content_url = f"https://www.bilibili.com/video/{bvid}"
        pic = str(item.get("pic", "") or "").strip()

        rows.append(
            {
                "bvid": bvid,
                "title": title,
                "up_name": str(owner.get("name", "") or "").strip(),
                "author_name": str(owner.get("name", "") or "").strip(),
                "up_mid": int(owner.get("mid", 0) or 0),
                "content_url": content_url,
                "cover_url": pic,
                "source_platform": "bilibili",
                "source": "bili-feed",
                "content_type": "video",
                "pool_status": "fresh",
                "duration": int(item.get("duration", 0) or 0),
                "view_count": int(stat.get("view", 0) or 0),
                "like_count": int(stat.get("like", 0) or 0),
                "danmaku_count": int(stat.get("danmaku", 0) or 0),
                "description": str(item.get("desc", "") or "").strip(),
                "discovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )
    return rows


def _insert_feed_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    """Insert new rows, skip duplicates by bvid."""
    inserted = 0
    for row in rows:
        try:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, author_name, up_mid, content_url,
                    cover_url, source_platform, source, content_type, pool_status,
                    duration, view_count, like_count, danmaku_count, description,
                    discovered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row["bvid"],
                    row["title"],
                    row["up_name"],
                    row["author_name"],
                    row["up_mid"],
                    row["content_url"],
                    row["cover_url"],
                    row["source_platform"],
                    row["source"],
                    row["content_type"],
                    row["pool_status"],
                    row["duration"],
                    row["view_count"],
                    row["like_count"],
                    row["danmaku_count"],
                    row["description"],
                    row["discovered_at"],
                ),
            )
            if cursor.rowcount > 0:
                inserted += 1
        except sqlite3.IntegrityError:
            continue
    return inserted


def _run_feed_once() -> dict[str, Any]:
    """One full fetch cycle. Returns a summary dict."""
    items = _fetch_feed()
    if not items:
        return {"ok": False, "reason": "empty_feed", "items_fetched": 0, "inserted": 0}

    rows = _parse_feed_items(items)
    if not rows:
        return {"ok": False, "reason": "no_valid_items", "items_fetched": len(items), "inserted": 0}

    conn = sqlite3.connect(DB_PATH)
    try:
        inserted = _insert_feed_rows(conn, rows)
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


def run_feed_forever() -> None:
    """Main loop: fetch every 24 hours."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    logger.info("bili feed producer started (interval=%dh)", INTERVAL_HOURS)

    while True:
        logger.info("fetching bilibili recommend...")
        result = _run_feed_once()
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


# ---------------------------------------------------------------------------
# Extension-search keyword generation
# ---------------------------------------------------------------------------


async def generate_bili_search_keywords(
    llm_service: LLMService,
    profile: SoulProfile,
    *,
    count: int = 5,
) -> list[str]:
    """Generate Bilibili search queries for extension fallback tasks."""

    try:
        messages = build_search_queries_prompt(profile_summary=build_profile_summary(profile))
        response = await llm_service.complete_structured_task(
            system_instruction=messages[0]["content"],
            user_input=messages[1]["content"],
            caller="runtime.bilibili_extension_search.queries",
            max_tokens=512,
        )
        queries = _parse_queries(str(getattr(response, "content", "")), limit=count)
        if queries:
            return queries
    except Exception:
        logger.exception("bili extension search keyword generation failed")
    return _fallback_queries(profile, count)


# ---------------------------------------------------------------------------
# Extension-search producer
# ---------------------------------------------------------------------------


@dataclass
class BilibiliExtensionSearchProducer:
    """Enqueue Bilibili search tasks when API search is degraded."""

    task_queue: BiliTaskQueue
    soul_engine: Any
    llm_service: LLMService
    bilibili_client: Any
    presence: Any
    enabled: bool = True
    daily_budget: int = 0
    min_interval_minutes: int = 30
    keywords_per_cycle: int = 3
    page_size: int = 20
    presence_grace_seconds: int = 90
    candidate_pipeline: Any | None = None
    keyword_fetch: Any | None = None
    kick: BiliKickCallable | None = None
    _last_skip_reason: str = field(default="", init=False)

    async def produce_if_due(
        self,
        *,
        limit: int | None = None,
        keywords: list[str] | None = None,
    ) -> dict[str, object]:
        """Run one fallback cycle if Bilibili API search needs DOM help."""

        if not self.enabled:
            return self._skip("disabled")
        if not self._api_search_fallback_needed():
            return self._skip("search_not_cooling")
        if not self._extension_present():
            return self._skip("extension_absent")
        if self._candidate_pool_full():
            return self._skip("pool_full")
        if not self._is_due():
            return self._skip("throttled")

        keyword_count = min(
            self.keywords_per_cycle,
            max(1, int(limit or self.keywords_per_cycle)),
        )

        coordinator = self.keyword_fetch
        if (
            keywords is None
            and coordinator is not None
            and bool(getattr(coordinator, "should_claim", lambda: False)())
        ):
            claimed = coordinator.claim(_PLATFORM_BILIBILI, keyword_count)
            if not claimed:
                return self._skip("no_keywords")
            result = self._enqueue_claimed_keywords(claimed)
            await self._kick_if_needed(result)
            return result

        if keywords is not None:
            resolved_keywords = _dedupe_keywords(keywords)[:keyword_count]
            if not resolved_keywords:
                return self._skip("no_keywords")
            result = self._enqueue_keywords(resolved_keywords)
            await self._kick_if_needed(result)
            return result

        try:
            profile = await self.soul_engine.get_profile()
        except Exception as exc:
            logger.warning("bili extension producer: soul profile unavailable: %s", exc)
            return self._skip("no_profile")
        if profile is None:
            return self._skip("no_profile")

        resolved_keywords = await generate_bili_search_keywords(
            self.llm_service,
            profile,
            count=keyword_count,
        )
        if not resolved_keywords:
            return self._skip("no_keywords")
        result = self._enqueue_keywords(resolved_keywords[:keyword_count])
        await self._kick_if_needed(result)
        return result

    def _enqueue_keywords(self, keywords: list[str]) -> dict[str, object]:
        enqueued = 0
        for keyword in keywords:
            task_id = self.task_queue.enqueue_with_id(
                "search",
                self._task_payload(keyword),
                daily_budget=self.daily_budget,
            )
            if task_id is None:
                break
            enqueued += 1
        logger.info("bili extension producer enqueued %d/%d search tasks", enqueued, len(keywords))
        return {"enqueued": enqueued, "attempted": len(keywords), "reason": "ok"}

    def _enqueue_claimed_keywords(self, claimed: list[Any]) -> dict[str, object]:
        coordinator = self.keyword_fetch
        enqueued = 0
        for item in claimed:
            task_id = self.task_queue.enqueue_with_id(
                "search",
                self._task_payload(item.keyword, source_keyword_id=int(item.id)),
                daily_budget=self.daily_budget,
            )
            if task_id is not None:
                enqueued += 1
                if coordinator is not None:
                    coordinator.mark_executing(item)
                continue
            if coordinator is not None:
                coordinator.rollback(item)
            break
        if coordinator is not None and enqueued < len(claimed):
            for item in claimed[enqueued + 1 :]:
                coordinator.rollback(item)
        logger.info(
            "bili extension producer enqueued %d/%d claimed search tasks",
            enqueued,
            len(claimed),
        )
        return {"enqueued": enqueued, "attempted": len(claimed), "reason": "ok"}

    def _task_payload(self, query: str, *, source_keyword_id: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": query,
            "limit": self.page_size,
            "page": 1,
            "page_size": self.page_size,
            "source": "bili-extension-search",
        }
        if source_keyword_id is not None:
            payload["source_keyword_id"] = int(source_keyword_id)
        return payload

    async def _kick_if_needed(self, result: dict[str, object]) -> None:
        enqueued_raw = result.get("enqueued", 0)
        try:
            enqueued = int(enqueued_raw) if isinstance(enqueued_raw, int | float | str) else 0
        except (TypeError, ValueError):
            enqueued = 0
        if enqueued <= 0 or self.kick is None:
            return
        try:
            maybe_awaitable = self.kick()
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            logger.debug("bili extension producer kick failed", exc_info=True)

    def _extension_present(self) -> bool:
        is_present = getattr(self.presence, "is_present", None)
        if not callable(is_present):
            return False
        try:
            return bool(is_present(max(1, int(self.presence_grace_seconds))))
        except Exception:
            logger.debug("bili extension producer: presence unavailable", exc_info=True)
            return False

    def _api_search_fallback_needed(self) -> bool:
        if search_cooldown_remaining(self.bilibili_client) > 0:
            return True
        remaining = getattr(self.bilibili_client, "search_dom_fallback_remaining", None)
        if not callable(remaining):
            return False
        try:
            return float(remaining()) > 0
        except Exception:
            logger.debug("bili extension producer: DOM fallback state unavailable", exc_info=True)
            return False

    def _candidate_pool_full(self) -> bool:
        if self.candidate_pipeline is None:
            return False
        pool_full = getattr(self.candidate_pipeline, "pool_full", None)
        if not callable(pool_full):
            return False
        try:
            return bool(pool_full())
        except Exception:
            logger.debug(
                "bili extension producer: candidate pool fullness unavailable",
                exc_info=True,
            )
            return False

    def _is_due(self) -> bool:
        if self.min_interval_minutes <= 0:
            return True
        recent = self.task_queue.find_recent_task(
            "search",
            recent_hours=max(0.0, float(self.min_interval_minutes) / 60.0),
            statuses=("pending", "in_progress", "completed"),
        )
        return recent is None

    def _skip(self, reason: str) -> dict[str, object]:
        if reason != self._last_skip_reason:
            logger.info("bili extension producer skip: reason=%s", reason)
        self._last_skip_reason = reason
        return {"enqueued": 0, "attempted": 0, "reason": reason}


# ---------------------------------------------------------------------------
# Extension-search helpers
# ---------------------------------------------------------------------------


def _parse_queries(content: str, *, limit: int) -> list[str]:
    text = content.strip()
    if not text:
        return []
    payload = parse_llm_json_tolerant(text)
    if payload is None:
        try:
            payload = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return []
    if not isinstance(payload, dict):
        return []
    raw = payload.get("queries")
    if not isinstance(raw, list):
        return []
    return _dedupe_keywords([str(item) for item in raw])[: max(1, int(limit))]


def _fallback_queries(profile: Any, count: int) -> list[str]:
    preferences = getattr(profile, "preferences", None)
    interests = getattr(preferences, "interests", []) if preferences is not None else []
    out: list[str] = []
    for item in interests:
        name = str(getattr(item, "name", "") or "").strip()
        if name:
            out.append(name)
    return _dedupe_keywords(out)[: max(1, int(count))]


def _dedupe_keywords(keywords: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in keywords:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


if __name__ == "__main__":
    run_feed_forever()
