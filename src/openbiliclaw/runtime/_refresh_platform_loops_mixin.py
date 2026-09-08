"""ContinuousRefreshController mixin: 平台生产者循环 / 轮询 / 封面缓存。

从 ``runtime/refresh.py`` 拆出的方法组；``ContinuousRefreshController``
继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any, cast

from openbiliclaw.runtime._refresh_shared import (
    _COVER_PREFETCH_INTERVAL_SECONDS,
    _COVER_PREFETCH_MAX_FETCH,
    _COVER_PREFETCH_RECENT_HOURS,
    _COVER_PREFETCH_SCAN,
    _IMAGE_CACHE_CLEANUP_INTERVAL_SECONDS,
    _call_accepts_limit,
)
from openbiliclaw.runtime.image_cache import (
    cleanup_image_cache,
    prefetch_cover,
    select_prefetch_targets,
)

logger = logging.getLogger("openbiliclaw.runtime.refresh")


class PlatformLoopsMixin:
    """平台生产者循环 / 轮询 / 封面缓存。"""

    _is_initialized: Any  # 由 ContinuousRefreshController 提供
    _llm_work_allowed: Any  # 由 ContinuousRefreshController 提供
    _source_deficit: Any  # 由 ContinuousRefreshController 提供
    bilibili_producer: Any  # 由 ContinuousRefreshController 提供
    check_interval_seconds: Any  # 由 ContinuousRefreshController 提供
    database: Any  # 由 ContinuousRefreshController 提供
    discovery_limit: Any  # 由 ContinuousRefreshController 提供
    douyin_producer: Any  # 由 ContinuousRefreshController 提供
    rss_adapter_registry: Any  # 由 ContinuousRefreshController 提供
    scheduler_config: Any  # 由 ContinuousRefreshController 提供
    x_producer: Any  # 由 ContinuousRefreshController 提供
    xhs_producer: Any  # 由 ContinuousRefreshController 提供
    youtube_producer: Any  # 由 ContinuousRefreshController 提供
    zhihu_producer: Any  # 由 ContinuousRefreshController 提供

    async def _loop_xhs_producer(self) -> None:
        """XHS keyword production — Soul-driven search task generation."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_xhs_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_bilibili_producer(self) -> None:
        """Bilibili extension fallback — only enqueues while API search cools down."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_bilibili_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_douyin_producer(self) -> None:
        """Douyin production — plugin/direct discovery when Douyin is below quota."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_douyin_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_youtube_producer(self) -> None:
        """YouTube production — backend-direct discovery when YouTube is below quota."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_youtube_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_x_producer(self) -> None:
        """X (Twitter) production — server-side cookie-replay discovery when under quota."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_x_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_zhihu_producer(self) -> None:
        """Zhihu production — plugin-backed discovery when under quota."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_zhihu_producer()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_rss_polling(self) -> None:
        """RSS feed polling — fetch articles from configured RSS/Atom feeds.

        Unlike other producers, RSS polling doesn't need LLM or quota
        tracking. It runs on a fixed 1-hour interval.
        """
        while True:
            subscriptions = getattr(self.scheduler_config, "rss_subscriptions", [])
            if subscriptions and self.rss_adapter_registry is not None:
                from openbiliclaw.sources.rss_tasks import run_rss_polling

                with suppress(Exception):
                    await run_rss_polling(
                        self.rss_adapter_registry,
                        cast("Any", self.database),
                        subscriptions,
                    )
            await asyncio.sleep(3600)

    async def _loop_xiaoyuzhou_polling(self) -> None:
        """Xiaoyuzhou (小宇宙) podcast polling — fetch episodes from
        configured podcast RSS feeds and inject into recommendation pool.

        Runs on a fixed 2-hour interval.
        """
        while True:
            subscriptions = getattr(self.scheduler_config, "xiaoyuzhou_subscriptions", [])
            if subscriptions and self.rss_adapter_registry is not None:
                from openbiliclaw.sources.xiaoyuzhou_tasks import (
                    run_xiaoyuzhou_polling,
                )

                with suppress(Exception):
                    await run_xiaoyuzhou_polling(
                        self.rss_adapter_registry,
                        cast("Any", self.database),
                        subscriptions,
                    )
            await asyncio.sleep(7200)

    async def _loop_wechat_polling(self) -> None:
        """WeChat (微信公众号) RSS polling — fetch articles from configured
        wechat2rss feeds and inject into recommendation pool.

        Runs on a fixed 2-hour interval.
        """
        while True:
            subscriptions = getattr(self.scheduler_config, "wechat_subscriptions", [])
            if subscriptions and self.rss_adapter_registry is not None:
                from openbiliclaw.sources.wechat_tasks import (
                    run_wechat_polling,
                )

                with suppress(Exception):
                    await run_wechat_polling(
                        self.rss_adapter_registry,
                        cast("Any", self.database),
                        subscriptions,
                    )
            await asyncio.sleep(7200)

    async def _loop_image_cache_cleanup(self) -> None:
        """Periodically prune the cover-image disk cache.

        Evicts cached covers of consumed, unsaved content (the user has seen and
        passed on them, and they are not in favorites / watch-later). Covers of
        saved or still-pending content are kept, and un-refetchable covers (XHS
        rotating-token URLs) are protected — the cached copy is their only durable
        source once the upstream token expires. The bulk first pass runs at API
        startup; this is the steady-state sweep.
        """
        while True:
            await asyncio.sleep(_IMAGE_CACHE_CLEANUP_INTERVAL_SECONDS)
            try:
                result = cleanup_image_cache(database=self.database)
            except Exception:
                logger.debug("image cache cleanup tick failed", exc_info=True)
                continue
            if result.removed:
                logger.info(
                    "image cache cleanup: removed %d cover files (%.1f MB freed; "
                    "%d consumed, %d aged orphans, %d unrefetchable protected)",
                    result.removed,
                    result.freed_bytes / (1024 * 1024),
                    result.removed_consumed,
                    result.removed_aged_orphans,
                    result.protected_unrefetchable,
                )

    async def _prefetch_uncached_covers(
        self,
        *,
        scan: int = _COVER_PREFETCH_SCAN,
        max_fetch: int = _COVER_PREFETCH_MAX_FETCH,
    ) -> int:
        """Cache covers for recently discovered, still-servable content.

        Fixes the «封面 502» failure mode: cover images were previously fetched only
        when a card was displayed, by which point a short-lived XHS signed token had
        often expired. Prefetching right after discovery saves the image while the
        token is fresh. Un-refetchable (XHS rotating-token) covers are tried first
        since re-fetchable ones (Bilibili etc.) never expire. Best-effort and bounded.
        """
        candidates = self.database.iter_servable_cover_urls(
            recent_hours=_COVER_PREFETCH_RECENT_HOURS,
            limit=scan,
        )
        targets = select_prefetch_targets(candidates, max_fetch=max_fetch)
        fetched = 0
        for url in targets:
            if await prefetch_cover(url):
                fetched += 1
        return fetched

    async def _loop_cover_prefetch(self) -> None:
        """Periodically cache discovered covers while their CDN token is fresh."""
        while True:
            try:
                cached = await self._prefetch_uncached_covers()
            except Exception:
                logger.debug("cover prefetch tick failed", exc_info=True)
                cached = 0
            if cached:
                logger.info("cover prefetch: cached %d new covers", cached)
            await asyncio.sleep(_COVER_PREFETCH_INTERVAL_SECONDS)

    async def _tick_xhs_producer(self) -> None:
        """Invoke the xhs search task producer if one is configured."""
        producer = self.xhs_producer
        if producer is None:
            return
        deficit = self._source_deficit("xiaohongshu")
        if deficit <= 0:
            return
        limit = max(1, min(deficit, self.discovery_limit))
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()

    async def _tick_bilibili_producer(self) -> None:
        """Invoke the Bili extension fallback producer if Bilibili is under quota."""
        producer = self.bilibili_producer
        if producer is None:
            return
        if not self._is_initialized():
            return
        deficit = self._source_deficit("bilibili")
        if deficit <= 0:
            return
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        limit = max(1, min(deficit, self.discovery_limit))
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()

    async def _tick_douyin_producer(self) -> None:
        """Invoke the Douyin discovery producer if Douyin is under quota."""
        producer = self.douyin_producer
        if producer is None:
            return
        if not self._is_initialized():
            return
        deficit = self._source_deficit("douyin")
        if deficit <= 0:
            return
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        limit = max(1, min(deficit, self.discovery_limit))
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()

    async def _tick_youtube_producer(self) -> None:
        """Invoke the YouTube discovery producer if YouTube is under quota."""
        producer = self.youtube_producer
        if producer is None:
            return
        if not self._is_initialized():
            return
        deficit = self._source_deficit("youtube")
        if deficit <= 0:
            return
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        limit = max(1, min(deficit, self.discovery_limit))
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()

    async def _tick_x_producer(self) -> None:
        """Invoke the X (Twitter) discovery producer if X is under quota."""
        producer = self.x_producer
        if producer is None:
            return
        if not self._is_initialized():
            return
        deficit = self._source_deficit("twitter")
        if deficit <= 0:
            return
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        limit = max(1, min(deficit, self.discovery_limit))
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()

    async def _tick_zhihu_producer(self) -> None:
        """Invoke the Zhihu discovery producer if Zhihu is under quota."""
        producer = self.zhihu_producer
        if producer is None:
            return
        if not self._is_initialized():
            return
        deficit = self._source_deficit("zhihu")
        if deficit <= 0:
            return
        produce_fn = getattr(producer, "produce_if_due", None)
        if not callable(produce_fn):
            return
        limit = max(1, min(deficit, self.discovery_limit))
        if _call_accepts_limit(produce_fn):
            await produce_fn(limit=limit)
        else:
            await produce_fn()
