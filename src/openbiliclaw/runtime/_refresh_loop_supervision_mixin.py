"""ContinuousRefreshController mixin: 后台循环监督与生命周期。

从 ``runtime/refresh.py`` 拆出的方法组；``ContinuousRefreshController``
继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import datetime, timedelta
from typing import Any, cast

from openbiliclaw.runtime._refresh_shared import (
    _COVER_PREFETCH_INTERVAL_SECONDS,
    _GETNOTE_ABSORB_INTERVAL,
    _GETNOTE_DAILY_TARGET,
    _GETNOTE_DISPATCH_INTERVAL,
    _GETNOTE_SEED_PER_BATCH,
    _IMAGE_CACHE_CLEANUP_INTERVAL_SECONDS,
    _MAX_DISCOVERY_BACKFILL_PER_REFRESH,
    RefreshControllerAttrs,
)

logger = logging.getLogger("openbiliclaw.runtime.refresh")


class LoopSupervisionMixin(RefreshControllerAttrs):
    """后台循环监督与生命周期。"""

    async def run_forever(self) -> None:
        """Launch all background tasks as independent concurrent loops.

        Each task runs on its own timer so a slow discovery refresh
        (10+ minutes when B站 API challenges every request) never
        blocks proactive notifications, soul pipeline ticks, or XHS
        keyword production.

        Architecture::

            ┌─ _loop_refresh()           60s   LLM-heavy, may take minutes
            ├─ _loop_pool_precompute()   60s   v0.3.60+ — drain pool_expression
            ├─ _loop_candidate_eval()    60s   drain pending raw candidates
            ├─ _loop_soul_pipeline()     60s   profile updates, speculator
            ├─ _loop_bilibili_producer() 60s   Bili extension search fallback under cooldown
            ├─ _loop_xhs_producer()      60s   xhs keyword generation
            ├─ _loop_douyin_producer()   60s   Douyin discovery when under quota
            ├─ _loop_youtube_producer()  60s   YouTube discovery when under quota
            ├─ _loop_x_producer()        60s   X (Twitter) discovery when under quota
            ├─ _loop_zhihu_producer()    60s   Zhihu discovery when under quota
            ├─ _loop_proactive_push()    60s   delight + interest probe
            ├─ _loop_keyword_planner()  120s   P1.6 — merged keyword generation (flag-gated)
            ├─ _loop_image_cache_cleanup() 6h  prune consumed+unsaved covers
            └─ _loop_cover_prefetch()    60s   cache fresh-token covers (XHS)
        """
        if self._llm_work_allowed():
            with suppress(Exception):
                await self.prepare_delight_candidates()
        self._warn_on_stranded_source_shares()
        # P1.6: give the keyword planner the controller's deficit / catalyst
        # 口径 so it shares the exact in-flight + raw-headroom accounting that
        # drives pool replenishment (it never recounts visible pool rows).
        if self.keyword_planner is not None:
            with suppress(Exception):
                self.keyword_planner.bind_deficit_source(self)
            bind_soul = getattr(self.keyword_planner, "bind_soul_engine", None)
            if callable(bind_soul):
                with suppress(Exception):
                    bind_soul(self.soul_engine)
        tasks = [
            self._spawn_loop(
                "refresh", "发现刷新", self.check_interval_seconds, self._loop_refresh()
            ),
            self._spawn_loop(
                "pool_precompute",
                "池子预计算",
                self.check_interval_seconds,
                self._loop_pool_precompute(),
            ),
            self._spawn_loop(
                "candidate_eval",
                "候选评估",
                self.check_interval_seconds,
                self._loop_candidate_eval(),
            ),
            self._spawn_loop(
                "soul_pipeline", "灵魂管道", self.check_interval_seconds, self._loop_soul_pipeline()
            ),
            self._spawn_loop(
                "bilibili_producer",
                "B站内容生产",
                self.check_interval_seconds,
                self._loop_bilibili_producer(),
            ),
            self._spawn_loop(
                "xhs_producer",
                "小红书内容生产",
                self.check_interval_seconds,
                self._loop_xhs_producer(),
            ),
            self._spawn_loop(
                "douyin_producer",
                "抖音内容生产",
                self.check_interval_seconds,
                self._loop_douyin_producer(),
            ),
            self._spawn_loop(
                "youtube_producer",
                "YouTube内容生产",
                self.check_interval_seconds,
                self._loop_youtube_producer(),
            ),
            self._spawn_loop(
                "x_producer", "X内容生产", self.check_interval_seconds, self._loop_x_producer()
            ),
            self._spawn_loop(
                "zhihu_producer",
                "知乎内容生产",
                self.check_interval_seconds,
                self._loop_zhihu_producer(),
            ),
            self._spawn_loop("rss_polling", "RSS轮询", 3600, self._loop_rss_polling()),
            self._spawn_loop(
                "xiaoyuzhou_polling", "小宇宙轮询", 7200, self._loop_xiaoyuzhou_polling()
            ),
            self._spawn_loop("wechat_polling", "公众号轮询", 7200, self._loop_wechat_polling()),
            self._spawn_loop(
                "proactive_push",
                "主动推送",
                self.proactive_push_interval_seconds,
                self._loop_proactive_push(),
            ),
            self._spawn_loop(
                "keyword_planner",
                "关键词规划",
                int(getattr(self.keyword_planner, "poll_seconds", 120) or 120),
                self._loop_keyword_planner(),
            ),
            self._spawn_loop(
                "image_cache_cleanup",
                "图片缓存清理",
                _IMAGE_CACHE_CLEANUP_INTERVAL_SECONDS,
                self._loop_image_cache_cleanup(),
            ),
            self._spawn_loop(
                "cover_prefetch",
                "封面预取",
                _COVER_PREFETCH_INTERVAL_SECONDS,
                self._loop_cover_prefetch(),
            ),
            self._spawn_loop(
                "getnote_fill",
                "getnote补正文播种",
                _GETNOTE_DISPATCH_INTERVAL,
                self._loop_content_filler_getnote(),
            ),
            self._spawn_loop(
                "getnote_absorb",
                "getnote平台内容吸收",
                _GETNOTE_ABSORB_INTERVAL,
                self._loop_content_filler_absorb(),
            ),
            self._spawn_loop(
                "self_evolution",
                "自进化",
                3600,
                self._loop_self_evolution(),
            ),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _spawn_loop(
        self,
        name: str,
        label: str,
        interval_seconds: int,
        loop_coro: Any,
    ) -> asyncio.Task[None]:
        return asyncio.ensure_future(self._supervise_loop(name, label, interval_seconds, loop_coro))

    async def _supervise_loop(
        self,
        name: str,
        label: str,
        interval_seconds: int,
        loop_coro: Any,
    ) -> None:
        """Wrap a ``while True`` scheduler loop with liveness tracking.

        The wrapped loop runs untouched; the supervisor merely stamps a
        "still alive" timestamp every probe window so ``get_loop_health``
        can tell a running loop from a hung one (e.g. blocked for many
        minutes on a rate-limited upstream). Probe cadence is capped at
        300s so even 2-6h loops report recent liveness.
        """
        task = asyncio.ensure_future(loop_coro)
        with suppress(AttributeError, RuntimeError):
            task.set_name(f"loop:{name}")
        interval = max(1, int(interval_seconds))
        self._loop_meta[name] = {
            "label": label,
            "interval": interval,
            "task": task,
            "last_tick_at": datetime.now().isoformat(timespec="seconds"),
        }
        probe = min(interval, 300)
        try:
            while not task.done():
                self._loop_meta[name]["last_tick_at"] = datetime.now().isoformat(timespec="seconds")
                await asyncio.wait({task}, timeout=probe)
        except asyncio.CancelledError:
            task.cancel()
            with suppress(BaseException):
                await task
            raise

    async def _loop_content_filler_getnote(self) -> None:
        """getnote 补正文播种通道（按频受控）。

        恢复低频播种：每批 ``_GETNOTE_SEED_PER_BATCH`` 条缺正文文章的 URL 提交
        平台抓取，稍后收割回补正文+摘要。节奏目标每日 800 条，配合当日配额
        熔断——今日 write_note 剩余额度不足，或累计已达当日目标，即睡到次日。
        """
        raw = getattr(getattr(self, "database", None), "_db_path", None)
        db_path: str = str(raw) if raw else "data/openbiliclaw.db"

        while True:
            try:
                if await self._getnote_check_quota():
                    continue  # 熔断：已睡到次日
                from openbiliclaw.self_evolution.content_filler import ContentFiller

                filler = ContentFiller(db_path)
                result = await filler.fetch_via_getnote(limit=_GETNOTE_SEED_PER_BATCH)
                if result:
                    seeded = int(result.get("seeded") or 0)
                    if seeded:
                        await asyncio.to_thread(self._bump_getnote_seeded_today, seeded)
                    if seeded or result.get("harvested"):
                        logger.info("content_filler: getnote done: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug("content_filler: getnote seed tick failed", exc_info=True)
            await asyncio.sleep(_GETNOTE_DISPATCH_INTERVAL)

    async def _loop_content_filler_absorb(self) -> None:
        """getnote 平台内容吸收（每日一次：全量拉取平台已就绪笔记补录入库）。

        低频执行：将平台上本地没有的笔记新建入库、本地缺正文/摘要的用平台
        总结补全，避免频次过高在平台留下过多访问痕迹（风控）及占用过重。
        """
        raw = getattr(getattr(self, "database", None), "_db_path", None)
        db_path: str = str(raw) if raw else "data/openbiliclaw.db"

        while True:
            try:
                from openbiliclaw.self_evolution.content_filler import ContentFiller

                filler = ContentFiller(db_path)
                result = await filler.absorb_from_getnote()
                if result and (result.get("imported") or result.get("harvested")):
                    logger.info(
                        "content_filler: getnote absorb done: %s", result
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.debug(
                    "content_filler: getnote absorb tick failed", exc_info=True
                )
            await asyncio.sleep(_GETNOTE_ABSORB_INTERVAL)  # 每日一次

    async def _getnote_check_quota(self) -> bool:
        """按当日配额决定是否熔断播种，返回 True 表示已睡到次日。

        双重判断：
        1. 平台 write_note 今日剩余额度（remaining）够一整个批次才放行，
           否则直接睡到次日；
        2. 本服务当日累计播种量（persist 在 self_evolution_state）达
           ``_GETNOTE_DAILY_TARGET`` 即熔断至次日。
        """
        remaining = await asyncio.to_thread(self._getnote_daily_write_note_remaining)
        if remaining is None:  # 解析失败，保守放行（每批仅 3 条，风险低）
            return False
        if remaining < _GETNOTE_SEED_PER_BATCH:
            logger.info("getnote: 今日 write_note 仅剩 %d，暂停播种至次日", remaining)
            await self._sleep_until_next_day()
            return True

        seeded_today = await asyncio.to_thread(self._getnote_seeded_today)
        if seeded_today >= _GETNOTE_DAILY_TARGET:
            logger.info(
                "getnote: 今日已播种 %d ≥ 目标 %d，熔断至次日",
                seeded_today, _GETNOTE_DAILY_TARGET,
            )
            await self._sleep_until_next_day()
            return True
        return False

    def _getnote_daily_write_note_remaining(self) -> int | None:
        """解析 ``getnote quota -o json`` 的 write_note 今日剩余额度。"""
        import json
        import subprocess

        try:
            proc = subprocess.run(
                ["getnote", "quota", "-o", "json"],
                capture_output=True, text=True, timeout=30,
            )
            if proc.returncode != 0:
                return None
            payload = json.loads(proc.stdout or "{}")
            data = payload.get("data") if isinstance(payload, dict) else None
            daily = (data or {}).get("write_note", {}).get("daily") or {}
            remaining = daily.get("remaining")
            return int(remaining) if remaining is not None else None
        except Exception:
            logger.debug("content_filler: getnote quota parse failed", exc_info=True)
            return None

    def _getnote_seeded_today(self) -> int:
        """返回当日已播种条数（按本地日期分片持久化）。"""
        day_key = datetime.now().strftime("%Y-%m-%d")
        try:
            from openbiliclaw.self_evolution.loop_engine import SelfEvolutionState

            state = SelfEvolutionState(
                getattr(getattr(self, "database", None), "_db_path", None) or "data/openbiliclaw.db"
            )
            val = state.get(f"getnote_seeded_{day_key}")
            return int(val) if val else 0
        except Exception:
            return 0

    def _bump_getnote_seeded_today(self, delta: int) -> None:
        """累加当日已播种条数，供配额熔断计数。"""
        if delta <= 0:
            return
        now = datetime.now()
        day_key = now.strftime("%Y-%m-%d")
        try:
            from openbiliclaw.self_evolution.loop_engine import SelfEvolutionState

            state = SelfEvolutionState(
                getattr(getattr(self, "database", None), "_db_path", None) or "data/openbiliclaw.db"
            )
            current = int(state.get(f"getnote_seeded_{day_key}") or 0)
            state.set(f"getnote_seeded_{day_key}", str(current + delta))
        except Exception:
            logger.debug("content_filler: bump getnote seeded failed", exc_info=True)

    async def _sleep_until_next_day(self) -> None:
        """睡到次日 00:05，等平台配额重置。"""
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        await asyncio.sleep(max(0, (tomorrow - now).total_seconds()))

    def get_loop_health(self) -> list[dict[str, object]]:
        """Per-loop liveness snapshot for the observability dashboard."""
        loops: list[dict[str, object]] = []
        now = datetime.now()
        for name, meta in self._loop_meta.items():
            task = meta.get("task")
            task_ref = cast("asyncio.Task[None] | None", task)
            interval = int(str(meta.get("interval", 0) or 0))
            last_tick = str(meta.get("last_tick_at", ""))
            if task_ref is None:
                status = "not_started"
            elif task_ref.cancelled():
                status = "stopped"
            elif task_ref.done():
                status = "crashed" if task_ref.exception() is not None else "idle"
            else:
                status = "running"
                if last_tick:
                    with suppress(ValueError):
                        age = (now - datetime.fromisoformat(last_tick)).total_seconds()
                        if age > max(interval * 3, 600):
                            status = "lagging"
            loops.append(
                {
                    "name": name,
                    "label": str(meta.get("label", name)),
                    "interval_seconds": interval,
                    "last_tick_at": last_tick,
                    "status": status,
                }
            )
        return loops

    async def _loop_refresh(self) -> None:
        """Discovery refresh — fills the candidate pool."""
        while True:
            # v0.3.61+: 30s init grace period. The very first refresh
            # tick after daemon start lands while Bilibili's WBI
            # rate-limit bucket is still saturated from init's history
            # / favorites / following burst — firing discovery search
            # immediately produces ~50% v_voucher exhaustion. Skipping
            # the first refresh_if_needed gives the IP a single tick
            # to cool down before discovery starts hammering it.
            if not self._init_grace_consumed:
                self._init_grace_consumed = True
                logger.info(
                    "Init grace period — skipping first refresh tick to let "
                    "Bilibili WBI bucket cool down (next tick will run normally)"
                )
            elif not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            else:
                with suppress(Exception):
                    await self._on_profile_ready_if_first_time()
                with suppress(Exception):
                    await self.refresh_if_needed()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_pool_precompute(self) -> None:
        """v0.3.60+: drain pool_expression / pool_topic_label independently.

        v0.3.59 added ``_drain_pool_precompute_backlog`` to ``_loop_refresh``
        but placed it AFTER ``await self.refresh_if_needed()``. Production
        debugging on 2026-05-05 (PID 32644 daemon, started 22:35:12) found
        runtime stuck at ``manual_refresh_state="running"`` because B 站
        v_voucher rate limit kept refresh_if_needed pending for many
        minutes — the drain queued behind it never executed, even with
        184 fresh items in pool waiting for expression copy.

        Splitting the drain into its own loop matches the ``run_forever``
        contract every other ticker honours: a slow refresh must NEVER
        block independent maintenance work. Engine's ``_precompute_lock``
        still dedupes against per-strategy fire-and-forget tasks queued
        by ``_run_refresh_plan`` so no LLM token double-spend.
        """
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._drain_pool_precompute_backlog()
            await asyncio.sleep(self.check_interval_seconds)

    async def _loop_candidate_eval(self) -> None:
        """Drain pending discovery-candidate raw rows independently of refresh plans."""
        while True:
            if not self._llm_work_allowed():
                logger.debug("candidate eval drain skipped: reason=llm_paused")
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._drain_discovery_candidates_and_precompute(
                    reason="periodic",
                )
            await asyncio.sleep(self.check_interval_seconds)

    async def _drain_pool_precompute_backlog(self) -> None:
        """v0.3.59+: independent precompute drain.

        Fires ``precompute_pool_copy`` once per refresh-loop tick (60s)
        if the soul profile is ready. The engine's ``_precompute_lock``
        de-dupes against per-strategy fire-and-forget tasks queued by
        ``_run_refresh_plan`` so back-to-back triggers don't double-spend
        LLM tokens.
        """
        engine = self.recommendation_engine
        if engine is None:
            return
        if not self._is_initialized():
            return
        try:
            profile = await self.soul_engine.get_profile()
        except Exception:
            return
        if profile is None:
            return
        try:
            before_pool_count = int(
                self.database.count_pool_candidates(xhs_self_nickname=self._xhs_self_nickname())
            )
        except Exception:
            before_pool_count = -1
        try:
            await engine.precompute_pool_copy(
                profile=profile,
                limit=_MAX_DISCOVERY_BACKFILL_PER_REFRESH,
            )
        except Exception:
            logger.exception("Periodic precompute drain failed")
            return
        if before_pool_count >= 0:
            await self._publish_precompute_replenishment_if_needed(
                before_pool_count=before_pool_count,
            )

    async def _publish_precompute_replenishment_if_needed(
        self,
        *,
        before_pool_count: int,
    ) -> None:
        """Report candidates that became usable during the standalone drain."""
        try:
            after_pool_counts = self._pool_readiness_counts()
            after_pool_count = int(after_pool_counts["available"])
        except Exception:
            return
        replenished_count = max(0, after_pool_count - int(before_pool_count))
        if replenished_count <= 0:
            return

        state = self._update_discovery_runtime_state(
            lambda runtime_state: runtime_state.update(
                {"last_replenished_count": replenished_count}
            )
        )
        discovered_count = self._int_state_value(state, "last_discovered_count")
        recent_pool_topics = self._list_state_value(state, "recent_pool_topics")
        self._last_published_pool_count = after_pool_count
        logger.info(
            "Periodic precompute made %s pool candidates available (pool_available %s -> %s)",
            replenished_count,
            before_pool_count,
            after_pool_count,
        )
        await self._publish_event(
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "message": f"刚补进 {replenished_count} 条新的",
                **self._pool_count_payload(after_pool_counts),
                "last_discovered_count": discovered_count,
                "last_replenished_count": replenished_count,
                "recent_pool_topics": recent_pool_topics,
            }
        )

    async def _on_profile_ready_if_first_time(self) -> None:
        """One-shot hook fired the tick after soul profile first appears.

        Drains the un-classified pool backlog that piled up during init's
        analyze_events window. Without this, items entering the pool
        before profile-ready (XHS bootstrap notes, B站 history fetches)
        sit with empty ``topic_group`` / ``style_key`` until the next
        natural refresh tick — and the recommendation summary log shows
        fallback ``topic_group=title[:N]`` (the ugly "屎屎/165/三花"
        debug we saw on 2026-05-05).
        """
        if not self._llm_work_allowed():
            return
        if self._profile_ready_observed:
            return
        if not self._is_initialized():
            return
        self._profile_ready_observed = True
        engine = self.recommendation_engine
        classify_fn = getattr(engine, "classify_pool_backlog", None) if engine else None
        if not callable(classify_fn):
            return
        try:
            profile = await self.soul_engine.get_profile()
        except Exception:
            # Race: _is_initialized was true but get_profile raised.
            # Reset the flag so the next tick retries cleanly.
            self._profile_ready_observed = False
            return
        logger.info(
            "Soul profile became ready — kicking classify_pool_backlog to drain init-window backlog"
        )
        try:
            await classify_fn(profile=profile, limit=100)
        except Exception:
            logger.exception("profile-ready classify_pool_backlog failed")

    async def _loop_soul_pipeline(self) -> None:
        """Soul profile pipeline — buffer flushes, speculator, cognition."""
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.check_interval_seconds)
                continue
            with suppress(Exception):
                await self._tick_soul_pipeline()
            await asyncio.sleep(self.check_interval_seconds)

    async def _tick_soul_pipeline(self) -> None:
        """Invoke ProfileUpdatePipeline.tick() if the soul engine exposes one.

        Splitting this into a helper makes it cheap to call from tests
        and from a manual single-iteration loop runner.
        """
        pipeline = getattr(self.soul_engine, "pipeline", None)
        if pipeline is None:
            return
        tick_fn = getattr(pipeline, "tick", None)
        if not callable(tick_fn):
            return
        await tick_fn()

    async def _loop_keyword_planner(self) -> None:
        """P1.6: deficit-pulled merged keyword generation (flag-gated).

        Owns its own poll cadence (``planner_poll_seconds``) so a slow merged
        LLM call never blocks the 60s producer / refresh loops. The controller
        drives the planner per tick (rather than awaiting ``planner.run()``) so
        it can apply the same ``_llm_work_allowed`` gate every other LLM loop
        honours — pausing planning while a guided init runs or the extension is
        away. When ``keyword_planner`` is ``None`` (tests building the
        controller directly) or the feature flag is off, this is a no-op.
        """
        planner = self.keyword_planner
        if planner is None:
            return
        poll_seconds = max(1, int(getattr(planner, "poll_seconds", 120)))
        while True:
            if not bool(getattr(planner, "enabled", False)):
                await asyncio.sleep(poll_seconds)
                continue
            if not self._llm_work_allowed():
                await asyncio.sleep(poll_seconds)
                continue
            with suppress(Exception):
                planner.reclaim_leases()
            with suppress(Exception):
                await planner.run_once()
            await asyncio.sleep(poll_seconds)

    async def _loop_proactive_push(self) -> None:
        """Delight + interest probe push — lightweight, never blocks.

        Runs on a longer cadence than the main refresh loop because
        probes/delight are not streaming content — once the active set
        has been delivered, additional pushes within minutes only
        contribute notification fatigue.
        """
        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(self.proactive_push_interval_seconds)
                continue
            # Score un-scored pool items even when the discovery refresh
            # tick early-exits (pool_at_cap or below_threshold). Without
            # this, a steady-state pool that sits at cap silently starves
            # delight scoring — observed 2026-05-04: scoring last ran on
            # daemon startup at 03:15 and stopped for 9.5 hours because
            # _run_refresh_plan never reached the precompute_pool_copy
            # branch. ``prepare_delight_candidates`` calls precompute_pool_copy
            # with limit=0, which still runs precompute_delight_scores on
            # the up-to-50 un-scored items (relevance >= 0.55).
            with suppress(Exception):
                await self.prepare_delight_candidates()
            # Snapshot delight count BEFORE prepare so we can detect a
            # net new above-threshold delight (popup re-fetch trigger).
            delight_count_before = self._safe_count_delight_candidates()
            with suppress(Exception):
                await self._publish_delight_if_available()
            with suppress(Exception):
                await self._publish_probe_if_available()
            delight_count_after = self._safe_count_delight_candidates()
            net_new_delights = max(0, delight_count_after - delight_count_before)
            if net_new_delights > 0:
                with suppress(Exception):
                    await self._publish_event(
                        {
                            "type": "delight.refreshed",
                            "phase": "ready",
                            "count": net_new_delights,
                            "total_pending": delight_count_after,
                            "message": (
                                f"刚发现 {net_new_delights} 条新的惊喜推荐"
                                if net_new_delights > 1
                                else "刚发现一条新的惊喜推荐"
                            ),
                        }
                    )
            await asyncio.sleep(self.proactive_push_interval_seconds)

    async def _loop_self_evolution(self) -> None:
        """Self-evolution — incremental, filtered automatic data exploration.

        **Design based on multi-feed continuous data collection:**

        1. **Incremental processing**: Only processes *new* articles added since
           the last run, never re-scans the entire database.

        2. **Multi-level filtering**: Rules (length, already processed) first,
           then quality prioritization (favorited > liked > viewed > unviewed),
           so only high-signal content reaches LLM processing.

        3. **Batch accumulation**: Accumulates new articles until there are
           enough (default 20) or 24h have passed, whichever comes first.
           This reduces LLM cold starts and groups work efficiently.

        4. **Sliding window statistics**: Maintains pre-computed 7d/30d/90d
           topic/platform distributions so drift detection is efficient.

        5. **Persistent state**: All control state is stored in the database
           so it survives API restarts.

        Uses ``asyncio.to_thread`` for sync module calls so the async
        loop is never blocked by long LLM calls.
        """
        _base = 3600  # 1-hour base tick

        # Resolve db_path from the database object
        _db_path: str | None = None
        raw = getattr(self.database, "_db_path", None)
        if raw is not None:
            _db_path = str(raw)
        if not _db_path:
            _db_path = "data/openbiliclaw.db"

        # Get llm_service from soul_engine (stored as _llm_service)
        llm = getattr(self.soul_engine, "_llm_service", None)

        # Import the loop engine (lazy to avoid circular import)
        from openbiliclaw.self_evolution.loop_engine import SelfEvolutionLoopEngine

        engine = SelfEvolutionLoopEngine(
            _db_path,
            llm_service=llm,
            batch_threshold=20,
            batch_max_hours=24,
        )

        while True:
            if not self._llm_work_allowed():
                await asyncio.sleep(_base)
                continue

            try:
                results = await engine.run_tick()
                # Log summary for observability
                batched = results.get("batched", False)
                if batched:
                    stats = [
                        f"{k}={v}"
                        for k, v in results.items()
                        if isinstance(v, (int, float)) and v > 0
                    ]
                    if stats:
                        logger.info(
                            "self_evolution: completed tick: %s",
                            ", ".join(stats),
                        )

            except Exception:
                logger.debug("self_evolution: loop tick failed", exc_info=True)

            await asyncio.sleep(_base)

    def _pending_signal_events_count(self, state: dict[str, object]) -> int:
        return len(
            self.database.query_events_since(
                after_event_id=self._int_state_value(state, "last_processed_event_id"),
                event_types=self._signal_event_types,
            )
        )
