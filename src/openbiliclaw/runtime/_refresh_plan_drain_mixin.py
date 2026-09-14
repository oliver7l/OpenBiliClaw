"""ContinuousRefreshController mixin: 刷新计划构建与候选排水。

从 ``runtime/refresh.py`` 拆出的方法组；``ContinuousRefreshController``
继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, cast

from obc_discovery.pool_snapshot import (
    build_pool_distribution_snapshot,
)

from openbiliclaw.runtime._refresh_shared import (
    _PLATFORM_SOURCE_ORDER,
    RefreshControllerAttrs,
    _call_accepts_keyword_ids,
    _call_accepts_keywords,
    _call_accepts_pool_snapshot,
    _call_accepts_strategy_limits,
)
from openbiliclaw.runtime.keyword_fetch import PLATFORM_BILIBILI as _KW_PLATFORM_BILIBILI

logger = logging.getLogger("openbiliclaw.runtime.refresh")


class PlanDrainMixin(RefreshControllerAttrs):
    """刷新计划构建与候选排水。"""

    def _build_refresh_plan(
        self,
        state: dict[str, object],
    ) -> list[tuple[list[str], int]]:
        pending_events = self._pending_signal_events_count(state)
        pool_available = self.database.count_pool_candidates(
            xhs_self_nickname=self._xhs_self_nickname()
        )
        pool_below_target = pool_available < self.pool_target_count

        if pool_below_target:
            source_plan = self._build_source_replenishment_plan()
            if source_plan:
                return source_plan
            # When Bilibili is already at its platform quota, the missing
            # capacity belongs to enabled non-Bilibili platform producers.
            # Running the Bilibili fallback here would immediately violate
            # the configured pool-source ratio.
            self._log_empty_refresh_plan_diagnostics(pool_available=pool_available)
            return []

        if "bilibili" not in self._normalized_pool_source_shares():
            return []

        plan: list[tuple[list[str], int]] = []
        if pending_events >= self.signal_event_threshold:
            plan.append((["search", "related_chain"], self.discovery_limit))
        if self._is_due(
            str(state.get("last_trending_refresh_at", "")),
            hours=self.trending_refresh_hours,
        ):
            plan.append((["trending"], self.discovery_limit))
        if self._is_due(
            str(state.get("last_explore_refresh_at", "")),
            hours=self.explore_refresh_hours,
        ):
            plan.append((["explore"], self.discovery_limit))
        return plan

    def _log_empty_refresh_plan_diagnostics(self, *, pool_available: int) -> None:
        try:
            readiness = self._pool_readiness_counts()
        except Exception:
            logger.debug("refresh plan empty readiness diagnostics failed", exc_info=True)
            readiness = {}
        try:
            source_available = self._count_pool_available_candidates_by_source()
        except Exception:
            logger.debug("refresh plan empty source available diagnostics failed", exc_info=True)
            source_available = {}
        try:
            source_raw = self._count_pool_raw_material_by_source()
        except Exception:
            logger.debug("refresh plan empty source raw diagnostics failed", exc_info=True)
            source_raw = {}
        source_targets = self._source_target_counts()
        raw_targets = self._raw_source_target_counts()
        requested_by_source: dict[str, int] = {}
        sources = sorted(
            set(source_targets)
            | set(raw_targets)
            | set(source_available)
            | set(source_raw)
            | set(_PLATFORM_SOURCE_ORDER)
        )
        for source in sources:
            try:
                requested_by_source[source] = self._source_requested_count(
                    source,
                    source_available_counts=source_available,
                    source_raw_counts=source_raw,
                    target_counts=source_targets,
                    raw_target_counts=raw_targets,
                )
            except Exception:
                logger.debug(
                    "refresh plan empty requested_by_source diagnostics failed for %s",
                    source,
                    exc_info=True,
                )
                requested_by_source[source] = -1

        logger.info(
            "refresh plan empty: pool_available=%s raw=%s pending=%s "
            "source_available=%s source_raw=%s source_targets=%s raw_targets=%s "
            "requested_by_source=%s",
            pool_available,
            readiness.get("raw", "?"),
            readiness.get("pending", "?"),
            source_available,
            source_raw,
            source_targets,
            raw_targets,
            requested_by_source,
        )

    async def refresh_after_event_ingest(self) -> dict[str, object]:
        """Compatibility shim: event ingest marks demand, scheduler refreshes later."""
        return self._queue_replenishment_reason("event_ingest")

    async def refresh_after_feedback(self) -> dict[str, object]:
        """Compatibility shim: feedback marks demand, scheduler refreshes later."""
        return self._queue_replenishment_reason("feedback")

    async def refresh_after_init(self) -> dict[str, object]:
        """Compatibility shim: init completion should kick replenishment now."""
        return await self.request_replenishment(reason="init_completed", force=True)

    async def drain_discovery_candidates_once(
        self,
        *,
        batch_size: int | None = None,
        reason: str = "manual",
    ) -> dict[str, int]:
        """Drain one pending discovery-candidate batch through the shared evaluator."""
        return await self._drain_discovery_candidates_and_precompute(
            reason=reason,
            batch_size=batch_size,
            precompute=False,
        )

    async def _drain_discovery_candidates_and_precompute(
        self,
        *,
        reason: str,
        batch_size: int | None = None,
        profile: Any | None = None,
        precompute: bool = True,
    ) -> dict[str, int]:
        """Drain one pending raw-candidate batch and optionally precompute it."""
        pipeline = self.discovery_candidate_pipeline
        if pipeline is None:
            logger.debug("candidate eval drain skipped: reason=no_pipeline caller=%s", reason)
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        if self._discovery_drain_lock.locked():
            logger.debug("candidate eval drain skipped: reason=locked caller=%s", reason)
            return {"evaluated": 0, "cached": 0, "rejected": 0}
        async with self._discovery_drain_lock:
            try:
                pool_available = self.database.count_pool_candidates(
                    xhs_self_nickname=self._xhs_self_nickname()
                )
            except TypeError:
                pool_available = self.database.count_pool_candidates()
            before_pool_count = int(pool_available)
            if int(pool_available) >= self.pool_target_count:
                logger.debug(
                    "candidate eval drain skipped: reason=pool_at_cap "
                    "pool_available=%s target=%s caller=%s",
                    pool_available,
                    self.pool_target_count,
                    reason,
                )
                return {"evaluated": 0, "cached": 0, "rejected": 0}
            if profile is None:
                try:
                    profile = await self.soul_engine.get_profile()
                except Exception as exc:
                    logger.info(
                        "candidate eval drain skipped: reason=no_profile caller=%s error=%s",
                        reason,
                        exc,
                    )
                    return {"evaluated": 0, "cached": 0, "rejected": 0}
            if profile is None:
                logger.info("candidate eval drain skipped: reason=no_profile caller=%s", reason)
                return {"evaluated": 0, "cached": 0, "rejected": 0}
            result = await pipeline.drain_pending(
                profile=profile,
                batch_size=self._candidate_eval_drain_batch_size(batch_size),
            )
            drain_result = cast("dict[str, int]", result)
            evaluated = int(drain_result.get("evaluated", 0) or 0)
            cached = int(drain_result.get("cached", 0) or 0)
            rejected = int(drain_result.get("rejected", 0) or 0)
            failed = int(drain_result.get("failed", 0) or 0)
            waiting = int(drain_result.get("waiting", 0) or 0)
        if cached > 0 and precompute:
            await self._safe_precompute_pool_copy(profile=profile)
            await self._publish_precompute_replenishment_if_needed(
                before_pool_count=before_pool_count,
            )
        if evaluated or cached or rejected or failed:
            logger.info(
                "candidate eval drain done: caller=%s evaluated=%s cached=%s rejected=%s failed=%s",
                reason,
                evaluated,
                cached,
                rejected,
                failed,
            )
        elif waiting:
            logger.info(
                "candidate eval drain skipped: reason=batch_waiting pending=%s caller=%s",
                waiting,
                reason,
            )
        else:
            logger.debug("candidate eval drain skipped: reason=no_pending caller=%s", reason)
        return drain_result

    async def _complete_manual_refresh(self) -> None:
        try:
            refresh_result = await self.force_refresh()
        except Exception as exc:
            self._manual_refresh_state = "failed"
            self._manual_refresh_message = f"这次补货没跑通：{exc}"
            self._manual_refresh_finished_at = self._now().isoformat()
            await self._publish_event(
                {
                    "type": "refresh.failed",
                    "phase": "failed",
                    "message": self._manual_refresh_message,
                    **self._pool_count_payload(self._pool_readiness_counts()),
                }
            )
            return
        self._manual_refresh_state = "success"
        if bool(refresh_result.get("refreshed")):
            runtime_state = self.memory_manager.load_discovery_runtime_state()
            last_discovered = self._int_state_value(runtime_state, "last_discovered_count")
            last_replenished = self._int_state_value(runtime_state, "last_replenished_count")
        else:
            last_discovered = 0
            last_replenished = 0
        self._manual_refresh_message = (
            "刚给你补了一批新的。"
            if last_replenished > 0
            else (
                "这轮找到了内容，但可立即换的库存没变。"
                if last_discovered > 0
                else "这轮没补进新的候选。"
            )
        )
        self._manual_refresh_finished_at = self._now().isoformat()
        await self._publish_event(
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "message": self._manual_refresh_message,
                **self._pool_count_payload(self._pool_readiness_counts()),
            }
        )

    async def _run_refresh_plan(
        self,
        *,
        state: dict[str, object],
        profile: Any,
        plan: list[tuple[list[str], int]],
        reason: str,
    ) -> dict[str, object]:
        before_pool_counts = self._pool_readiness_counts()
        before_pool_count = before_pool_counts["available"]
        initial_pool_below_target = before_pool_count < self.pool_target_count
        all_discovered: list[Any] = []
        pipeline_discovered_count = 0
        flattened_strategies: list[str] = []
        replenished_topics: list[str] = []
        # v0.3.47+: per-strategy expression precompute tasks. Each strategy's
        # `discover()` blocks on a slow LLM eval batch (8-16 minutes
        # observed in production). Without this, popup copy precompute was
        # gated until ALL strategies finished — i.e. ~30 min of latency
        # for fresh items. Now: as soon as a strategy yields content we
        # kick a precompute task; ``self._precompute_lock`` inside
        # ``RecommendationEngine`` serialises them so two tasks don't
        # double-spend LLM tokens on the same un-precomputed candidates.
        precompute_tasks: list[asyncio.Task[Any]] = []

        await self._publish_event(
            {
                "type": "refresh.started",
                "phase": "running",
                "message": "开始给你补候选了",
                **self._pool_count_payload(before_pool_counts),
            }
        )

        for strategies, requested_limit in plan:
            current_pool_counts = self._pool_readiness_counts()
            current_pool_count = current_pool_counts["available"]
            if current_pool_count >= self.pool_target_count:
                break

            await self._publish_event(
                {
                    "type": "refresh.strategy",
                    "phase": "running",
                    "strategy": "+".join(strategies),
                    "message": self._strategy_message(strategies),
                    **self._pool_count_payload(current_pool_counts),
                }
            )

            effective_limit = self._requested_refresh_limit(
                requested_limit=requested_limit,
                current_pool_count=current_pool_count,
                pool_below_target=initial_pool_below_target,
            )
            strategy_limits = self._requested_strategy_limits(
                strategies=strategies,
                requested_limit=requested_limit,
                effective_limit=effective_limit,
                current_pool_count=current_pool_count,
                pool_below_target=initial_pool_below_target,
            )
            try:
                pool_snapshot = build_pool_distribution_snapshot(
                    self.database,
                    pool_target_count=self.pool_target_count,
                    source_targets=self._source_target_counts(),
                )
            except Exception:
                logger.exception("Failed to build pool distribution snapshot")
                pool_snapshot = None
            # Unified keyword planner fetch path (P1.7, flag-gated). B站 search is
            # inline-admit: this plan iteration fetches + drains (admits) in the
            # same call. When the flag is on and this entry includes ``search``,
            # claim words from the store and inject them as ``keywords`` (the
            # engine maps them onto the search strategy's ``queries`` param); on
            # a successful admit mark them ``used``, on an empty/failed iteration
            # mark them ``failed``. Non-search sub-strategies in the same entry
            # are unaffected (they never receive the injected words).
            claimed_search: list[Any] = []
            coordinator = self.keyword_fetch
            if (
                "search" in strategies
                and coordinator is not None
                and bool(getattr(coordinator, "should_claim", lambda: False)())
                and int(current_pool_counts.get("pending_eval", 0) or 0) < effective_limit
            ):
                claimed_search = coordinator.claim(_KW_PLATFORM_BILIBILI)
            injected_keywords = (
                [item.keyword for item in claimed_search] if claimed_search else None
            )
            # P1.8 yield provenance: ``query → keyword id`` for the claimed words
            # so each produced candidate carries ``source_keyword_id`` for
            # admit-time yield backfill. Empty / None on the flag-off path.
            injected_keyword_ids = (
                {item.keyword: int(item.id) for item in claimed_search} if claimed_search else None
            )

            pipeline = self.discovery_candidate_pipeline
            discovered: list[Any] = []
            topic_items: list[Any] = []
            discovered_count = 0
            admitted_count = 0
            iteration_failed = False
            try:
                if pipeline is not None:
                    produce_kwargs: dict[str, Any] = {
                        "profile": profile,
                        "strategies": strategies,
                        "limit": effective_limit,
                        "strategy_limits": strategy_limits,
                        "pool_snapshot": pool_snapshot,
                    }
                    if injected_keywords is not None:
                        produce_kwargs["keywords"] = injected_keywords
                    if injected_keyword_ids:
                        produce_kwargs["keyword_ids"] = injected_keyword_ids
                    ensure_supply = getattr(pipeline, "ensure_pending_supply", None)
                    if callable(ensure_supply):
                        supply_result = await ensure_supply(
                            **produce_kwargs,
                            target_pending=effective_limit,
                        )
                        produced_count = int(
                            dict(supply_result).get("inserted", 0)
                            if isinstance(supply_result, dict)
                            else 0
                        )
                    else:
                        produced_count = await pipeline.produce_and_enqueue(**produce_kwargs)
                    drain_result = await self._drain_discovery_candidates_and_precompute(
                        reason="refresh",
                        profile=profile,
                        batch_size=effective_limit,
                        precompute=False,
                    )
                    discovered_count = int(produced_count or 0)
                    admitted_count = int(drain_result.get("cached", 0) or 0)
                    if admitted_count > 0:
                        topic_items = list(getattr(pipeline, "last_admitted_items", []) or [])
                    pipeline_discovered_count += discovered_count
                else:
                    discover_fn = self.discovery_engine.discover
                    discover_kwargs: dict[str, Any] = {
                        "strategies": strategies,
                        "limit": effective_limit,
                    }
                    if strategy_limits and _call_accepts_strategy_limits(discover_fn):
                        discover_kwargs["strategy_limits"] = strategy_limits
                    if _call_accepts_pool_snapshot(discover_fn):
                        discover_kwargs["pool_snapshot"] = pool_snapshot
                    if injected_keywords is not None and _call_accepts_keywords(discover_fn):
                        discover_kwargs["keywords"] = injected_keywords
                    if injected_keyword_ids and _call_accepts_keyword_ids(discover_fn):
                        discover_kwargs["keyword_ids"] = injected_keyword_ids
                    discovered = await discover_fn(profile, **discover_kwargs)
                    topic_items = discovered
                    discovered_count = len(discovered)
                    admitted_count = discovered_count
            except Exception:
                iteration_failed = True
                if claimed_search and coordinator is not None:
                    coordinator.mark_failed(claimed_search)
                raise
            finally:
                if claimed_search and coordinator is not None and not iteration_failed:
                    # Inline-admit terminal: words that drove a fetch producing
                    # candidates are ``used``; an empty fetch marks them ``failed``
                    # (retry). yield backfill is P1.8, decoupled from ``used``.
                    if discovered_count > 0:
                        coordinator.mark_used(claimed_search)
                    else:
                        coordinator.mark_failed(claimed_search)
            all_discovered.extend(discovered)
            flattened_strategies.extend(strategies)

            if admitted_count > 0:
                replenished_topics.extend(self._extract_topics(topic_items))
                # Fire expression precompute now (in parallel with the next
                # strategy's discovery LLM call). The lock inside the engine
                # queues this if a previous task is still running.
                precompute_tasks.append(
                    self._track_task(
                        "precompute_pool_copy",
                        self._safe_precompute_pool_copy(profile=profile),
                    )
                )

        if flattened_strategies:
            self.database.trim_explore_cluster_overflow(max_per_cluster=3)
            # Cap each topic_group at ~10% of pool target so a single hot
            # topic (e.g. 人工智能 from related_chain) can't accumulate
            # hundreds of fresh candidates across rounds and starve other
            # sources/topics. Floor at 3 to keep small pools usable.
            self.database.trim_topic_group_overflow(
                max_per_group=max(3, self.pool_target_count // 10),
            )
            self.database.evict_stale_pool_items(max_age_days=365)
            # Bound growth of the high-volume ``events`` table: low-value
            # behavior events (views/scrolls/hovers/snapshots) are folded into
            # the persistent soul/preference layers by the cognition watermark,
            # so pruning them by age loses no profiling signal. Runs on this
            # background loop, never the request loop.
            _events_retention = int(getattr(self.scheduler_config, "events_retention_days", 0) or 0)
            if _events_retention > 0:
                try:
                    cast("Any", self.database).prune_events_by_retention(
                        retention_days=_events_retention
                    )
                except Exception:
                    logger.debug("events retention prune failed", exc_info=True)
            # Same maintenance pass: bound the terminal crawl-task rows
            # (zhihu_tasks/dy_tasks) and rejected discovery_candidates, which
            # previously grew without bound (~10KB/row payloads).
            _task_retention = int(
                getattr(self.scheduler_config, "task_history_retention_days", 0) or 0
            )
            if _task_retention > 0:
                try:
                    cast("Any", self.database).prune_task_history(retention_days=_task_retention)
                except Exception:
                    logger.debug("task history prune failed", exc_info=True)
            # Same maintenance pass: bound ``recommendations`` (24h de-dup
            # ledger, no readers past that window) and ``llm_usage`` (write-
            # only cost ledger). Both grew without bound before.
            _rec_retention = int(
                getattr(self.scheduler_config, "recommendations_retention_days", 0) or 0
            )
            if _rec_retention > 0:
                try:
                    cast("Any", self.database).prune_recommendations(retention_days=_rec_retention)
                except Exception:
                    logger.debug("recommendations prune failed", exc_info=True)
            _usage_retention = int(
                getattr(self.scheduler_config, "llm_usage_retention_days", 0) or 0
            )
            if _usage_retention > 0:
                try:
                    cast("Any", self.database).prune_llm_usage(retention_days=_usage_retention)
                except Exception:
                    logger.debug("llm_usage prune failed", exc_info=True)
            # Snapshot delight count BEFORE precompute so we can detect
            # net new above-threshold delights and push a refresh event
            # to the popup (no per-item chrome notification — popup
            # re-fetches /api/delight/pending-batch when this fires).
            delight_count_before = self._safe_count_delight_candidates()
            # v0.3.47+: drain the per-strategy precompute tasks fired
            # eagerly above. They have already been running in parallel
            # with discovery's later strategies, so this awaits whatever
            # is still pending instead of starting from scratch. If the
            # discovery loop produced nothing precompute-eligible (e.g.
            # all rejected at eval), fall back to one synchronous call so
            # any earlier-cycle backlog still gets cleared.
            if precompute_tasks:
                await asyncio.gather(*precompute_tasks, return_exceptions=True)
            else:
                await self._safe_precompute_pool_copy(profile=profile)
            # Pre-warm supergroup-merge embeddings so the popup's "换一批"
            # hot path always hits the L1/L2 cache. New labels added by
            # this refresh round get warmed before the user clicks.
            # Warm embedding-derived caches in the background. They are
            # latency optimizations for later serve() calls, not
            # requirements for this refresh result to become visible.
            # Keeping them off the refresh lock prevents slow local
            # embedding backends from leaving the popup stuck at "正在补货".
            self._track_task(
                "prewarm_supergroup_embeddings",
                self._safe_prewarm_supergroup_embeddings(),
            )
            self._track_task(
                "prewarm_pool_mmr_embeddings",
                self._safe_prewarm_pool_mmr_embeddings(),
            )
            delight_count_after = self._safe_count_delight_candidates()
            net_new_delights = max(0, delight_count_after - delight_count_before)
            if net_new_delights > 0:
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
            await self._publish_delight_if_available()
            await self._publish_probe_if_available()

            # v0.3.66+: enforce the absolute pool cap at the end of every
            # refresh plan. The earlier trim_topic_group_overflow /
            # trim_explore_cluster_overflow / evict_stale calls only bound
            # per-axis concentration (topic, cluster, age) — none of them
            # cap the total count. Long-running discovery cycles (10-30
            # min for the LLM eval batch) also block the periodic
            # _enforce_pool_cap tick in run_forever, so the popup
            # routinely saw pool_available_count drift well past
            # pool_target_count (e.g. 668 with target=600 in production).
            # _enforce_pool_cap also runs reactivate_under_quota and
            # source-share-aware trim, so this is the right place to land
            # the freshly-discovered items into their final shape before
            # the popup re-fetches.
            try:
                self._enforce_pool_cap()
            except Exception:
                logger.exception("post-refresh enforce_pool_cap failed")

        now = self._now().isoformat()
        latest_event_id = self.database.get_latest_event_id()
        runtime_updates: dict[str, object] = {}
        if "search" in flattened_strategies or "related_chain" in flattened_strategies:
            runtime_updates["last_event_refresh_at"] = now
            runtime_updates["last_processed_event_id"] = latest_event_id
        if "trending" in flattened_strategies:
            runtime_updates["last_trending_refresh_at"] = now
        if "explore" in flattened_strategies:
            runtime_updates["last_explore_refresh_at"] = now
        after_pool_counts = self._pool_readiness_counts()
        after_pool_count = after_pool_counts["available"]
        runtime_updates["last_discovered_count"] = len(all_discovered) + pipeline_discovered_count
        runtime_updates["last_replenished_count"] = max(0, after_pool_count - before_pool_count)
        if replenished_topics:
            runtime_updates["recent_pool_topics"] = self._dedupe_topics(replenished_topics)[:3]
        state = self._update_discovery_runtime_state(
            lambda runtime_state: runtime_state.update(runtime_updates)
        )
        discovered_count = self._int_state_value(state, "last_discovered_count")
        replenished_count = self._int_state_value(state, "last_replenished_count")
        await self._publish_event(
            {
                "type": "refresh.pool_updated",
                "phase": "done",
                "message": (
                    f"刚补进 {replenished_count} 条新的"
                    if replenished_count > 0
                    else (
                        "这轮找到了内容，但可立即换的库存没变"
                        if discovered_count > 0
                        else "这轮没补进新的候选"
                    )
                ),
                **self._pool_count_payload(after_pool_counts),
                "last_discovered_count": discovered_count,
                "last_replenished_count": replenished_count,
                "recent_pool_topics": self._list_state_value(state, "recent_pool_topics"),
            }
        )
        return {
            "refreshed": bool(flattened_strategies),
            "strategies": flattened_strategies,
            "reason": reason,
            "recommendation_count": 0,
        }
