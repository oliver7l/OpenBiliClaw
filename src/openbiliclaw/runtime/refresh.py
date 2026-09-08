"""Continuous refresh controller for the local API runtime."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol, cast

from openbiliclaw.config import SchedulerConfig
from openbiliclaw.discovery.pool_snapshot import (
    build_cold_start_pool_snapshot,
    build_pool_distribution_snapshot,
)
from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD
from openbiliclaw.runtime._refresh_loop_supervision_mixin import LoopSupervisionMixin
from openbiliclaw.runtime._refresh_notify_delight_mixin import NotifyDelightMixin
from openbiliclaw.runtime._refresh_plan_drain_mixin import PlanDrainMixin
from openbiliclaw.runtime._refresh_platform_loops_mixin import PlatformLoopsMixin
from openbiliclaw.runtime._refresh_probe_publish_mixin import ProbePublishMixin
from openbiliclaw.runtime._refresh_shared import (
    _DEFAULT_PLATFORM_SOURCE_SHARES,
    _MAX_DISCOVERY_BACKFILL_PER_REFRESH,
)
from openbiliclaw.runtime._refresh_source_budget_mixin import SourceBudgetMixin
from openbiliclaw.runtime.presence import PresenceTracker, background_llm_work_allowed

if TYPE_CHECKING:
    from collections.abc import Callable

    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

logger = logging.getLogger(__name__)

class SupportsRuntimeState(Protocol):
    def load_discovery_runtime_state(self) -> dict[str, object]: ...
    def save_discovery_runtime_state(self, state: dict[str, object]) -> None: ...
    def update_discovery_runtime_state(
        self,
        mutator: Callable[[dict[str, object]], dict[str, object] | None],
    ) -> dict[str, object]: ...
    def get_layer(self, name: str) -> Any: ...

class SupportsEventDatabase(Protocol):
    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]: ...
    def get_latest_event_id(self) -> int: ...
    def count_recommendations(self) -> int: ...
    def count_unread_recommendations(self) -> int: ...
    def count_pool_candidates(self, *, xhs_self_nickname: str = "") -> int: ...
    def count_pool_readiness(self, *, xhs_self_nickname: str = "") -> dict[str, int]: ...
    def count_pool_candidates_by_source(self) -> dict[str, int]: ...
    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 0, xhs_self_nickname: str = ""
    ) -> dict[str, int]: ...
    def count_pool_raw_material_candidates(self) -> int: ...
    def count_pool_raw_material_by_source(self) -> dict[str, int]: ...
    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]: ...
    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int: ...
    def trim_topic_group_overflow(self, *, max_per_group: int) -> int: ...
    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int: ...
    def trim_pool_source_overflow(self, *, source_share_quotas: dict[str, int]) -> int: ...
    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
    ) -> int: ...
    def evict_stale_pool_items(self, *, max_age_days: int = 14) -> int: ...
    def iter_cover_lifecycle(self) -> list[tuple[str, str, bool]]: ...
    def iter_servable_cover_urls(
        self, *, recent_hours: int = 12, limit: int = 300
    ) -> list[str]: ...
    def get_notification_candidate(
        self,
        *,
        min_confidence: float = 0.82,
    ) -> dict[str, Any] | None: ...
    def mark_notification_sent(self, bvid: str) -> None: ...
    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = DEFAULT_DELIGHT_THRESHOLD,
    ) -> dict[str, Any] | None: ...
    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = DEFAULT_DELIGHT_THRESHOLD,
        limit: int = 20,
    ) -> list[dict[str, Any]]: ...
    def mark_delight_notified(self, bvid: str) -> None: ...
    def count_delight_candidates(
        self,
        *,
        min_delight_score: float = DEFAULT_DELIGHT_THRESHOLD,
    ) -> int: ...

class SupportsProfileEngine(Protocol):
    async def get_profile(self) -> Any: ...

    # Effective disliked topics (AI dislikes + flat preference dislikes with
    # user overrides applied). Used by the proactive-delight hard filter so a
    # manually added dislike filters and a manually removed one does not.
    def get_effective_disliked_topics(self) -> list[str]: ...

    # Optional: the soul engine exposes a ProfileUpdatePipeline that the
    # refresh loop ticks periodically. The attribute may be missing on
    # older test doubles, so callers should `getattr(..., "pipeline", None)`.
    @property
    def pipeline(self) -> Any: ...

class SupportsDiscoveryEngine(Protocol):
    async def discover(
        self,
        profile: Any,
        strategies: list[str] | None = None,
        limit: int = 30,
        *,
        strategy_limits: dict[str, int] | None = None,
        pool_snapshot: Any | None = None,
        fully_parallel: bool = False,
    ) -> list[Any]: ...

class SupportsRecommendationEngine(Protocol):
    async def generate_recommendations(
        self,
        discovered: list[Any] | None,
        profile: Any,
        limit: int = 10,
    ) -> list[Any]: ...

    async def precompute_pool_copy(
        self,
        *,
        profile: Any,
        limit: int,
    ) -> int: ...

    async def prewarm_supergroup_embeddings(self) -> int: ...

    async def prewarm_pool_mmr_embeddings(self, *, limit: int = 200) -> int: ...

# Staged strategy plan for guided-init pool backfill (gui-init spec §5d).
# Mirrors cli._INIT_DISCOVERY_PLAN; B2 consolidates the CLI to reuse this.
_INIT_DISCOVERY_PLAN: list[list[str]] = [
    ["search", "trending", "related_chain", "explore"],
]

@dataclass
class ContinuousRefreshController(PlanDrainMixin, SourceBudgetMixin, ProbePublishMixin, NotifyDelightMixin, LoopSupervisionMixin, PlatformLoopsMixin):
    """Keep discovery cache and recommendations fresh during API runtime."""

    memory_manager: SupportsRuntimeState
    database: SupportsEventDatabase
    soul_engine: SupportsProfileEngine
    discovery_engine: SupportsDiscoveryEngine
    recommendation_engine: SupportsRecommendationEngine
    event_hub: Any | None = None
    discovery_candidate_pipeline: Any | None = None
    bilibili_producer: Any | None = None
    xhs_producer: Any | None = None
    douyin_producer: Any | None = None
    youtube_producer: Any | None = None
    x_producer: Any | None = None
    zhihu_producer: Any | None = None
    rss_adapter_registry: Any | None = None
    scheduler_config: Any = field(default_factory=SchedulerConfig)
    presence: PresenceTracker = field(default_factory=PresenceTracker)
    # gui-init D1: optional init-aware gate. When it returns True (a guided init
    # is active) ALL background loops pause so they don't race init's explicit
    # analyze/build. ``run_init_backfill`` bypasses this (it never calls
    # ``_llm_work_allowed``), so init's own discovery is not self-blocked.
    init_active_check: Callable[[], bool] | None = None
    signal_event_threshold: int = 6
    event_refresh_minutes: int = 0
    trending_refresh_hours: int = 3
    explore_refresh_hours: int = 12
    notification_cooldown_hours: int = 2
    delight_cooldown_hours: int = 4
    check_interval_seconds: int = 60
    # Proactive probe-push loop runs much less frequently than the main
    # refresh loop.  Probes aren't streaming content — once the active
    # set has been delivered, the only reason to push again is when a
    # slot rotates (user feedback / TTL).  10 min is enough to surface
    # newly generated probes without hammering the user.
    # Pre-2026-05-04 default was 600s (10 min). At that cadence new
    # delights took up to 10 minutes to surface in the popup, plus the
    # proactive_push only emits ONE candidate per tick. 120s is a much
    # tighter fallback while keeping chrome-notification cooldowns
    # intact (those have their own dedup window). The primary push path
    # is still the immediate ``delight.refreshed`` event emitted at the
    # end of ``_run_refresh_plan`` once new candidates are scored — this
    # interval is a safety net for the case where a refresh-less window
    # produces delights via some other path (manual rescore, init).
    proactive_push_interval_seconds: int = 120
    # Soul pipeline tick runs every minute to drain buffers, but the
    # speculator inside the pipeline doesn't need that cadence — its
    # gating happens upstream now in pipeline.tick().  Kept explicit so
    # we can tune in tests.
    discovery_limit: int = 30
    pool_target_count: int = 300
    pool_source_shares: dict[str, int] = field(
        default_factory=lambda: dict(_DEFAULT_PLATFORM_SOURCE_SHARES)
    )
    # v0.3.63+: optional registry so detached tasks (manual-refresh
    # background work, per-strategy precompute fire-and-forget) can be
    # cancelled by ``RuntimeContext.rebuild_from_config`` before the
    # next runtime starts. ``_track_task`` uses bare ``create_task``
    # when this is ``None`` so existing tests that build the controller
    # directly without injecting a registry keep working.
    task_registry: BackgroundTaskRegistry | None = None
    # P1.6: unified keyword planner (deficit-pulled merged keyword generation).
    # Constructed as its own object in ``api/runtime_context.py`` because the
    # controller holds no ``llm_service``. Its loop is launched by
    # ``run_forever``; with the feature flag off (default) the loop is a pure
    # no-op, so wiring it in is zero behavior change. ``None`` (the default,
    # used by tests that build the controller directly) means the planner loop
    # returns immediately.
    keyword_planner: Any | None = None
    # P1.7: unified keyword planner FETCH coordinator. Drives the B站 search
    # inline-admit lifecycle (claim → inject as ``queries`` → used / failed) when
    # the flag is on. Constructed in ``api/runtime_context.py``; ``None`` (tests
    # / flag off) → the B站 search keeps its legacy self-generating path.
    keyword_fetch: Any | None = None
    _manual_refresh_task: asyncio.Task[None] | None = None
    _discovery_drain_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock,
        init=False,
        repr=False,
    )
    # v0.3.62+ global "skip-if-busy" gate. Direct refresh execution is
    # intentionally centralized: periodic ticks call ``refresh_if_needed``;
    # user/manual replenishment calls ``force_refresh``. Event/feedback/init
    # paths only queue a reason and wait for the unified scheduler.
    # Without this lock, a slow periodic tick (10+ minutes when WBI
    # rate-limits) can run concurrently with manual refresh + per-event
    # opportunistic refresh, amplifying load on Bilibili and causing
    # SQLite write contention. Acquired with ``async with`` inside
    # ``refresh_if_needed``; if already held, the new caller exits
    # immediately with ``{"skipped": True, ...}`` rather than queueing.
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _manual_refresh_state: str = "idle"
    _manual_refresh_message: str = ""
    _manual_refresh_started_at: str = ""
    _manual_refresh_finished_at: str = ""
    _pending_replenishment_reasons: set[str] = field(default_factory=set, init=False)
    # Last-tick fingerprint of pool maintenance state, used to demote
    # the per-minute "reactivated=N" / "trim dropped=N top=X" log lines
    # to DEBUG when nothing actually changed since the previous tick.
    # INFO fires only when the count or top-group rotates.
    _last_pool_maintenance_fingerprint: tuple[int, int, str] = (-1, -1, "")
    _warned_pool_count_fallbacks: set[str] = field(default_factory=set, init=False)
    # Last pool_available count emitted via the runtime event stream so
    # popup-side ``mergeRuntimeStatusEvent`` only re-renders when the
    # number actually changes — see ``_publish_pool_status_if_changed``.
    _last_published_pool_count: int = -1
    # Flips false→true when soul profile is first detected. Used by
    # ``_loop_refresh`` to fire a one-shot ``classify_pool_backlog``
    # the moment init's analyze_events finishes — otherwise items
    # ingested during the ~7-minute init window sit un-classified
    # until the next natural refresh tick (and recommendation summary
    # would print fallback ``topic_group="title[:N]"`` until then).
    _profile_ready_observed: bool = False
    # v0.3.61+: skip the first ``refresh_if_needed`` invocation after
    # daemon start to give Bilibili a 30s cool-down window. Init's
    # synchronous chunk (history fetch + favorites + following) hits
    # the WBI search backend hard in the first ~10s; firing discovery
    # search queries immediately afterwards routinely triggers
    # v_voucher storm. One refresh tick of grace = much fewer
    # exhausted retries on the first half-hour.
    _init_grace_consumed: bool = False
    _last_llm_gate_allowed: bool = field(default=True, init=False)
    # Observability: per-loop registry filled by ``_supervise_loop`` and read
    # by ``get_loop_health`` (the /api/observability 运行时健康 tab). Keys are
    # stable loop names; values carry the task handle, display label, expected
    # interval and the last "still alive" probe timestamp.
    _loop_meta: dict[str, dict[str, object]] = field(default_factory=dict, init=False, repr=False)

    _signal_event_types = [
        "view",
        "search",
        "favorite",
        "like",
        "coin",
        "comment",
        "feedback",
    ]

    def _llm_work_allowed(self) -> bool:
        """Return whether daemon-owned background LLM / embedding work can run."""
        # Pause every background loop while a guided init is active (gui-init
        # D1) — the continuous refresh / soul-pipeline / producer ticks all gate
        # on this, so init's explicit analyze/build/backfill runs uncontended.
        if self.init_active_check is not None:
            try:
                if self.init_active_check():
                    return False
            except Exception:
                pass
        allowed = background_llm_work_allowed(self.scheduler_config, self.presence)
        if allowed != self._last_llm_gate_allowed:
            logger.info(
                "Background LLM work gate %s",
                "allowed" if allowed else "blocked",
            )
            self._last_llm_gate_allowed = allowed
        return allowed

    def _xhs_self_nickname(self) -> str:
        """Return the persisted XHS self nickname for pool guards."""
        try:
            state = self.memory_manager.load_discovery_runtime_state()
        except Exception:
            return ""
        info = state.get("xhs_self_info")
        if not isinstance(info, dict):
            return ""
        return str(info.get("nickname", "") or "").strip()

    def _pool_readiness_counts(self) -> dict[str, int]:
        """Return normalized pool readiness counts for status payloads."""
        nickname = self._xhs_self_nickname()
        try:
            # allow_stale：缓存过期先返回旧值、后台重算，避免后台刷新循环
            # 触发同步冷算（0.8~3.8s）阻塞 api 事件循环。
            readiness = self.database.count_pool_readiness(
                xhs_self_nickname=nickname, allow_stale=True
            )
            available = int(readiness.get("available", 0))
            return {
                "available": max(0, available),
                "raw": max(0, int(readiness.get("raw", available))),
                "pending": max(0, int(readiness.get("pending", 0))),
                "pending_eval": max(0, int(readiness.get("pending_eval", 0))),
                "evaluated_pending": max(0, int(readiness.get("evaluated_pending", 0))),
            }
        except Exception:
            available = int(self.database.count_pool_candidates(xhs_self_nickname=nickname))
            return {
                "available": max(0, available),
                "raw": max(0, available),
                "pending": 0,
                "pending_eval": 0,
                "evaluated_pending": 0,
            }

    @staticmethod
    def _pool_count_payload(counts: dict[str, int]) -> dict[str, int]:
        return {
            "pool_available_count": int(counts.get("available", 0)),
            "pool_raw_count": int(counts.get("raw", counts.get("available", 0))),
            "pool_pending_count": int(counts.get("pending", 0)),
            "pool_pending_eval_count": int(counts.get("pending_eval", 0)),
            "pool_evaluated_pending_count": int(counts.get("evaluated_pending", 0)),
        }

    def get_runtime_status(self) -> dict[str, object]:
        """Build a lightweight runtime summary for popup or diagnostics."""
        state = self.memory_manager.load_discovery_runtime_state()
        refresh_values = [
            str(state.get("last_event_refresh_at", "")),
            str(state.get("last_trending_refresh_at", "")),
            str(state.get("last_explore_refresh_at", "")),
        ]
        parsed_refresh_values: list[datetime] = []
        for value in refresh_values:
            parsed = self._parse_iso_datetime(value)
            if parsed is not None:
                parsed_refresh_values.append(parsed)
        last_refresh_at = max(parsed_refresh_values).isoformat() if parsed_refresh_values else ""
        pending_delight_count = 0
        with suppress(Exception):
            pending_delight_count = self.database.count_delight_candidates(
                min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            )
        pool_counts = self._pool_readiness_counts()
        return {
            "initialized": self._is_initialized(),
            "recommendation_count": self.database.count_recommendations(),
            "pending_signal_events": self._pending_signal_events_count(state),
            "last_refresh_at": last_refresh_at,
            "last_notification_at": str(state.get("last_notification_at", "")),
            "unread_count": self.database.count_unread_recommendations(),
            **self._pool_count_payload(pool_counts),
            "pool_target_count": self.pool_target_count,
            "last_discovered_count": self._int_state_value(state, "last_discovered_count"),
            "last_replenished_count": self._int_state_value(state, "last_replenished_count"),
            "recent_pool_topics": self._list_state_value(state, "recent_pool_topics"),
            "manual_refresh_state": self._manual_refresh_state,
            "manual_refresh_message": self._manual_refresh_message,
            "pending_delight_count": pending_delight_count,
            "last_delight_notification_at": str(state.get("last_delight_notification_at", "")),
        }

    async def refresh_if_needed(self) -> dict[str, object]:
        """Refresh discovery candidates when thresholds are met.

        Runtime replenishment now has one deciding path: the periodic scheduler
        calls this method, while event / feedback / init hooks only queue a
        reason through ``request_replenishment``. A module-level
        ``_refresh_lock`` (an ``asyncio.Lock``) is checked at the very top: if
        another refresh is already in progress, this call returns
        ``{"skipped": True, "reason": "another refresh holds lock"}``
        immediately rather than queueing. The remaining body runs inside
        ``async with self._refresh_lock:``, so the lock is released even on
        exception paths.

        Internal helpers (``_run_refresh_plan``, ``force_refresh``)
        intentionally do NOT acquire this lock — only the public
        ``refresh_if_needed`` entry does, so callers reaching it from
        different paths can't double-acquire.
        """
        if not self._llm_work_allowed():
            return {"refreshed": False, "strategies": [], "reason": "llm_paused"}

        if self._refresh_lock.locked():
            logger.debug("refresh_if_needed skipped: another refresh in flight")
            return {"skipped": True, "reason": "another refresh holds lock"}

        async with self._refresh_lock:
            state = self.memory_manager.load_discovery_runtime_state()
            queued_reasons = self._consume_replenishment_reasons()

            def _result(payload: dict[str, object]) -> dict[str, object]:
                if queued_reasons:
                    payload["queued_reasons"] = queued_reasons
                return payload

            if not self._is_initialized():
                return _result({"refreshed": False, "strategies": [], "reason": "not_initialized"})

            pool_at_cap = self._enforce_pool_cap()
            await self._publish_pool_status_if_changed()
            if pool_at_cap:
                return _result({"refreshed": False, "strategies": [], "reason": "pool_at_cap"})

            profile = await self.soul_engine.get_profile()
            plan = self._build_refresh_plan(state)
            if not plan:
                return _result({"refreshed": False, "strategies": [], "reason": "below_threshold"})

            return await self._run_refresh_plan(
                state=state,
                profile=profile,
                plan=plan,
                reason="triggered",
            )

    async def run_init_backfill(
        self,
        profile: Any,
        target_pool_count: int,
        *,
        fully_parallel: bool = True,
    ) -> int:
        """Backfill the initial discovery pool for guided init.

        Holds ``_refresh_lock`` so it serializes with continuous refresh and
        never races it on ``content_cache`` (gui-init spec §5d). Mirrors the
        CLI's staged ``_INIT_DISCOVERY_PLAN`` backfill, but against this
        controller's live ``discovery_engine``/``database``. Cooperative
        cancel: ``async with`` releases the lock on ``CancelledError``.
        Returns the total number of items discovered.
        """
        discovered_count = 0
        async with self._refresh_lock:
            for strategies in _INIT_DISCOVERY_PLAN:
                current = self.database.count_pool_candidates()
                if current >= target_pool_count:
                    break
                request_limit = max(20, target_pool_count - current)
                pool_snapshot = self._build_init_pool_snapshot(
                    profile,
                    current_pool_count=current,
                    target_pool_count=target_pool_count,
                )
                discovered = await self.discovery_engine.discover(
                    profile,
                    strategies=strategies,
                    limit=request_limit,
                    fully_parallel=fully_parallel,
                    pool_snapshot=pool_snapshot,
                )
                discovered_count += len(discovered)
        return discovered_count

    def _build_init_pool_snapshot(
        self,
        profile: Any,
        *,
        current_pool_count: int,
        target_pool_count: int,
    ) -> Any | None:
        if current_pool_count <= 0:
            return build_cold_start_pool_snapshot(
                profile,
                pool_target_count=target_pool_count,
                source_targets=self._source_target_counts(total=target_pool_count),
            )
        try:
            return build_pool_distribution_snapshot(
                self.database,
                pool_target_count=target_pool_count,
                source_targets=self._source_target_counts(total=target_pool_count),
            )
        except Exception:
            logger.debug("init backfill pool snapshot unavailable", exc_info=True)
            return None

    async def force_refresh(self) -> dict[str, object]:
        """Run a full refresh immediately, bypassing runtime thresholds.

        Runs all 4 Bilibili strategies in a single discover() call so they
        execute concurrently via asyncio.gather, maximizing pool diversity. The pool
        target still applies as a hard cap — if the pool is already full, no
        discovery runs and overflow is trimmed.

        v0.3.62+: also acquires ``_refresh_lock`` so manual refresh
        (which calls ``force_refresh`` rather than ``refresh_if_needed``)
        respects the global skip-if-busy gate. Without this, periodic
        + manual / pool-low refresh used to run through different code paths,
        amplifying Bilibili API load and SQLite write contention.
        Skip semantics match ``refresh_if_needed``: return immediately
        with ``{"refreshed": False, "reason": "another refresh holds lock"}``
        instead of queueing.
        """
        if self._refresh_lock.locked():
            logger.debug("force_refresh skipped: another refresh in flight")
            return {
                "refreshed": False,
                "strategies": [],
                "reason": "another refresh holds lock",
            }
        async with self._refresh_lock:
            return await self._force_refresh_locked()

    async def _force_refresh_locked(self) -> dict[str, object]:
        state = self.memory_manager.load_discovery_runtime_state()
        queued_reasons = self._consume_replenishment_reasons()

        def _result(payload: dict[str, object]) -> dict[str, object]:
            if queued_reasons:
                payload["queued_reasons"] = queued_reasons
            return payload

        if not self._is_initialized():
            return _result({"refreshed": False, "strategies": [], "reason": "not_initialized"})

        pool_at_cap = self._enforce_pool_cap()
        await self._publish_pool_status_if_changed()
        if pool_at_cap:
            return _result({"refreshed": False, "strategies": [], "reason": "pool_at_cap"})

        profile = await self.soul_engine.get_profile()
        plan = self._build_source_replenishment_plan()
        if not plan:
            return _result({"refreshed": False, "strategies": [], "reason": "below_threshold"})
        refresh_result = await self._run_refresh_plan(
            state=state,
            profile=profile,
            plan=plan,
            reason="manual",
        )
        return _result(refresh_result)

    def _enforce_pool_cap(self) -> bool:
        """Run pool maintenance and report whether frontend availability is at target.

        ``pool_target_count`` is a frontend-visible availability floor, not the
        raw material cap. Raw rows may exceed it until ``_raw_material_ceiling``.
        """
        source_targets = self._source_target_counts()
        raw_source_targets = self._raw_source_target_counts()

        # Cross-source topic_group quota runs every tick, not just inside
        # _run_refresh_plan: when pool sits at cap, refresh exits before
        # discover, so the in-plan trim would never fire and pre-existing
        # topic concentration would persist indefinitely. This call is a
        # cheap SQL group-by + UPDATE, safe to run unconditionally.
        try:
            self.database.trim_topic_group_overflow(
                max_per_group=max(3, self.pool_target_count // 10),
            )
        except Exception:
            logger.exception("trim_topic_group_overflow failed")

        reactivate_fn = getattr(self.database, "reactivate_under_quota_pool_sources", None)
        if callable(reactivate_fn):
            try:
                reactivated = reactivate_fn(
                    target=self.pool_target_count,
                    source_share_quotas=source_targets,
                    raw_source_share_quotas=raw_source_targets,
                )
                if reactivated > 0:
                    # Demote to DEBUG when the count is identical to the
                    # previous tick — pool sitting in steady-state with
                    # the same N items reactivating each minute is noise,
                    # not signal. INFO fires only when N changes (real
                    # state transition: pool drained to refill, or new
                    # source surge).
                    last_reactivated = self._last_pool_maintenance_fingerprint[1]
                    log_fn = logger.info if reactivated != last_reactivated else logger.debug
                    log_fn(
                        "enforce_pool_cap: reactivated=%s under-quota source items",
                        reactivated,
                    )
                    self._last_pool_maintenance_fingerprint = (
                        self._last_pool_maintenance_fingerprint[0],
                        reactivated,
                        self._last_pool_maintenance_fingerprint[2],
                    )
                    self.database.trim_topic_group_overflow(
                        max_per_group=max(3, self.pool_target_count // 10),
                    )
            except Exception:
                logger.exception("reactivate_under_quota_pool_sources failed")

        pool_available = self.database.count_pool_candidates(
            xhs_self_nickname=self._xhs_self_nickname()
        )

        # Source-overflow suppress pass disabled by request: with the
        # per-topic-group window removed, availability sits above target and
        # this pass would immediately re-suppress ~1.3k qualified items
        # (youtube/zhihu over family quota), fighting the "let everything
        # into the rotation" policy. Family quotas remain advisory for
        # discovery planning only.
        raw_ceiling = self._raw_material_ceiling()
        trimmed = 0
        try:
            trimmed = self.database.trim_pool_to_target_count(
                target=raw_ceiling,
                source_share_quotas=raw_source_targets,
            )
        except Exception:
            logger.exception("trim_pool_to_target_count failed")
        if trimmed > 0:
            pool_available = self.database.count_pool_candidates(
                xhs_self_nickname=self._xhs_self_nickname()
            )
            logger.info(
                "enforce_pool_cap: raw_trimmed=%s, pool_available=%s, target=%s, raw_ceiling=%s",
                trimmed,
                pool_available,
                self.pool_target_count,
                raw_ceiling,
            )
        else:
            logger.debug(
                "enforce_pool_cap: no raw trim needed, "
                "pool_available=%s, target=%s, raw_ceiling=%s",
                pool_available,
                self.pool_target_count,
                raw_ceiling,
            )
        return pool_available >= self.pool_target_count

    async def trigger_manual_refresh(self, *, reason: str = "manual") -> dict[str, object]:
        """Schedule one background manual refresh without blocking the caller."""
        normalized_reason = self._normalize_replenishment_reason(reason)
        if not self._is_initialized():
            return {"accepted": False, "state": "idle", "reason": "not_initialized"}
        if self._manual_refresh_task is not None and not self._manual_refresh_task.done():
            return {"accepted": True, "state": "running", "reason": "already_running"}

        self._manual_refresh_state = "running"
        self._manual_refresh_message = "正在补货…"
        self._manual_refresh_started_at = self._now().isoformat()
        self._manual_refresh_finished_at = ""
        logger.info("Manual replenishment requested: reason=%s", normalized_reason)
        self._manual_refresh_task = self._track_task(
            "manual_refresh",
            self._complete_manual_refresh(),
        )
        return {"accepted": True, "state": "running", "reason": "started"}

    def _track_task(
        self,
        name: str,
        coro: Any,
    ) -> asyncio.Task[Any]:
        """Spawn a detached task, routing through the registry when available.

        v0.3.63+: when ``self.task_registry`` is wired (by
        ``RuntimeContext`` at startup), the task is registered so that
        ``rebuild_from_config``'s ``cancel_all`` can cancel it before
        the new runtime starts. Tests that construct the controller
        directly (no registry) fall back to bare
        ``asyncio.create_task`` for backward compat.
        """
        registry = self.task_registry
        if registry is not None:
            return registry.track(name, coro)
        return asyncio.create_task(coro, name=name)

    def _update_discovery_runtime_state(
        self,
        mutator: Callable[[dict[str, object]], dict[str, object] | None],
    ) -> dict[str, object]:
        update_state = getattr(self.memory_manager, "update_discovery_runtime_state", None)
        if callable(update_state):
            return cast("dict[str, object]", update_state(mutator))
        state = self.memory_manager.load_discovery_runtime_state()
        result = mutator(state)
        next_state = state if result is None else result
        self.memory_manager.save_discovery_runtime_state(next_state)
        return next_state

    @staticmethod
    def _normalize_replenishment_reason(reason: str) -> str:
        normalized = str(reason or "").strip().lower().replace("-", "_").replace(" ", "_")
        return normalized or "unknown"

    def _queue_replenishment_reason(self, reason: str) -> dict[str, object]:
        normalized = self._normalize_replenishment_reason(reason)
        self._pending_replenishment_reasons.add(normalized)
        return {
            "refreshed": False,
            "strategies": [],
            "reason": "queued",
            "queued_reason": normalized,
        }

    def _consume_replenishment_reasons(self) -> list[str]:
        reasons = sorted(self._pending_replenishment_reasons)
        self._pending_replenishment_reasons.clear()
        return reasons

    async def request_replenishment(
        self,
        *,
        reason: str,
        force: bool = False,
    ) -> dict[str, object]:
        """Single public ingress for replenishment requests.

        Non-force requests only record why the next scheduler pass should
        re-check the pool. Force requests are reserved for explicit user actions
        or UI paths that just consumed the visible pool.
        """
        normalized = self._normalize_replenishment_reason(reason)
        if force:
            return await self.trigger_manual_refresh(reason=normalized)
        queued = self._queue_replenishment_reason(normalized)
        return {
            "accepted": True,
            "state": "queued",
            "reason": normalized,
            "refresh": queued,
        }

    async def _safe_precompute_pool_copy(self, *, profile: Any) -> int:
        """Run ``precompute_pool_copy`` swallowing any exception.

        v0.3.47+ uses this from per-strategy fire-and-forget tasks in
        ``_run_refresh_plan``. The lock inside the engine queues
        concurrent calls so two strategies don't double-spend LLM
        tokens; this wrapper exists so a single failed expression
        batch doesn't take down the whole refresh round (caller does
        ``return_exceptions=True`` on the gather, but a logged warning
        from one place is cleaner than scattering try/except).
        """
        try:
            return await self.recommendation_engine.precompute_pool_copy(
                profile=profile,
                limit=_MAX_DISCOVERY_BACKFILL_PER_REFRESH,
            )
        except Exception:
            logger.exception("precompute_pool_copy task failed")
            return 0

    async def _safe_prewarm_pool_mmr_embeddings(self) -> int:
        """Warm MMR embeddings without blocking refresh completion."""
        try:
            return int(await self.recommendation_engine.prewarm_pool_mmr_embeddings())
        except Exception:
            logger.exception("prewarm_pool_mmr_embeddings failed")
            return 0

    async def _safe_prewarm_supergroup_embeddings(self) -> int:
        """Warm topic-supergroup embeddings without blocking refresh completion."""
        try:
            return int(await self.recommendation_engine.prewarm_supergroup_embeddings())
        except Exception:
            logger.exception("prewarm_supergroup_embeddings failed")
            return 0

    def _is_initialized(self) -> bool:
        try:
            soul_layer = self.memory_manager.get_layer("soul")
        except Exception:
            return False
        data = getattr(soul_layer, "data", {})
        return isinstance(data, dict) and bool(data)

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime | None:
        if not value:
            return None
        with suppress(ValueError):
            return datetime.fromisoformat(value)
        return None

    @staticmethod
    def _int_state_value(state: dict[str, object], key: str) -> int:
        value = state.get(key, 0)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            with suppress(ValueError):
                return int(value)
        return 0

    def _is_due(self, value: str, *, hours: int) -> bool:
        if hours <= 0:
            return True
        last_run = self._parse_iso_datetime(value)
        if last_run is None:
            return True
        return self._now() - last_run >= timedelta(hours=hours)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _list_state_value(state: dict[str, object], key: str) -> list[str]:
        raw_value = state.get(key, [])
        if not isinstance(raw_value, list):
            return []
        return [str(item).strip() for item in raw_value if str(item).strip()]

    @staticmethod
    def _extract_topics(discovered: list[Any]) -> list[str]:
        topics: list[str] = []
        strategy_map = {
            "search": "相近兴趣",
            "related_chain": "相关推荐",
            "trending": "站内热榜",
            "explore": "跨圈探索",
        }
        for item in discovered:
            tags: Any = (
                item.get("tags", []) if isinstance(item, dict) else getattr(item, "tags", [])
            )
            if isinstance(tags, list):
                for tag in tags:
                    text = str(tag).strip()
                    if text:
                        topics.append(text)
            if isinstance(item, dict):
                source_strategy = str(item.get("source_strategy", "")).strip()
            else:
                source_strategy = str(getattr(item, "source_strategy", "")).strip()
            if source_strategy:
                topics.append(strategy_map.get(source_strategy, source_strategy))
        return topics

    @staticmethod
    def _dedupe_topics(topics: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for topic in topics:
            text = topic.strip()
            if not text or text in seen:
                continue
            seen.add(text)
            ordered.append(text)
        return ordered
