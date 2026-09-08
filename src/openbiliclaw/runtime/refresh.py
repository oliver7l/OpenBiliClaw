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
from openbiliclaw.runtime._refresh_platform_loops_mixin import PlatformLoopsMixin
from openbiliclaw.runtime._refresh_probe_publish_mixin import ProbePublishMixin
from openbiliclaw.runtime._refresh_shared import (
    _BILIBILI_DISCOVERY_SOURCES,
    _DEFAULT_CANDIDATE_EVAL_BATCH_SIZE,
    _DEFAULT_PLATFORM_SOURCE_SHARES,
    _MAX_DISCOVERY_BACKFILL_PER_REFRESH,
    _PLATFORM_SOURCE_ORDER,
    _call_accepts_keyword_ids,
    _call_accepts_keywords,
    _call_accepts_pool_snapshot,
    _call_accepts_strategy_limits,
)
from openbiliclaw.runtime.keyword_fetch import PLATFORM_BILIBILI as _KW_PLATFORM_BILIBILI
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
class ContinuousRefreshController(ProbePublishMixin, NotifyDelightMixin, LoopSupervisionMixin, PlatformLoopsMixin):
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

    def _build_source_replenishment_plan(self) -> list[tuple[list[str], int]]:
        source_available_counts = self._count_pool_available_candidates_by_source()
        source_raw_counts = self._count_pool_raw_material_by_source()
        target_counts = self._source_target_counts()
        raw_target_counts = self._raw_source_target_counts()
        plan: list[tuple[list[str], int]] = []
        for source in _PLATFORM_SOURCE_ORDER:
            requested = self._source_requested_count(
                source,
                source_available_counts=source_available_counts,
                source_raw_counts=source_raw_counts,
                target_counts=target_counts,
                raw_target_counts=raw_target_counts,
            )
            if requested <= 0:
                continue
            if source == "bilibili":
                # Bilibili is a platform quota now, but its implementation
                # still fans out through four established strategy names.
                plan.append((list(_BILIBILI_DISCOVERY_SOURCES), requested))
        return plan

    def _raw_material_ceiling(self) -> int:
        return max(self.pool_target_count * 2, self.pool_target_count + 120)

    def _source_target_counts(self, *, total: int | None = None) -> dict[str, int]:
        target_total = self.pool_target_count if total is None else max(0, int(total))
        shares = self._normalized_pool_source_shares()
        total_share = sum(shares.values())
        remaining = target_total
        targets: dict[str, int] = {}
        items = list(shares.items())
        for index, (source, share) in enumerate(items):
            if index == len(items) - 1:
                targets[source] = remaining
                break
            count = round(target_total * share / total_share)
            count = min(remaining, count)
            targets[source] = count
            remaining -= count
        return targets

    def _raw_source_target_counts(self) -> dict[str, int]:
        return self._source_target_counts(total=self._raw_material_ceiling())

    def _source_deficit(self, source_family: str) -> int:
        return self._source_requested_count(source_family)

    # ── keyword planner deficit / catalyst口径 (P1.6) ─────────────────────
    # The unified keyword planner reuses these so its "real deficit" shares the
    # exact available-pool deficit口径 that drives pool replenishment, instead of
    # naively counting visible pool rows. Raw headroom still caps normal request
    # size, but cannot turn an under-target available pool into "no deficit".

    def keyword_planner_real_deficit(self, platform: str) -> int:
        """Real search deficit for one platform.

        Wraps ``_source_requested_count`` — the same口径 used by
        ``_build_source_replenishment_plan``. ``> 0`` means the platform
        genuinely needs more search supply.
        """
        try:
            return int(self._source_requested_count(str(platform).strip()))
        except Exception:
            logger.exception("keyword_planner_real_deficit failed for %s", platform)
            return 0

    def keyword_planner_bilibili_catalyst(self) -> bool:
        """B站's extra catalyst: pool-below-target OR ≥ signal-event threshold.

        Mirrors ``_build_refresh_plan`` — B站 search regenerates keywords when
        the pool is below target (its four strategies fire together) or when
        ≥ ``signal_event_threshold`` signal events have queued (profile may have
        just drifted), even if its keyword cache is not below the low watermark.
        """
        try:
            pool_available = self.database.count_pool_candidates(
                xhs_self_nickname=self._xhs_self_nickname()
            )
        except TypeError:
            pool_available = self.database.count_pool_candidates()
        except Exception:
            logger.exception("keyword_planner_bilibili_catalyst pool count failed")
            return False
        if int(pool_available) < self.pool_target_count:
            return True
        try:
            state = self.memory_manager.load_discovery_runtime_state()
            pending_events = self._pending_signal_events_count(state)
        except Exception:
            logger.exception("keyword_planner_bilibili_catalyst signal count failed")
            return False
        return pending_events >= self.signal_event_threshold

    def _source_requested_count(
        self,
        source_family: str,
        *,
        source_available_counts: dict[str, int] | None = None,
        source_raw_counts: dict[str, int] | None = None,
        target_counts: dict[str, int] | None = None,
        raw_target_counts: dict[str, int] | None = None,
    ) -> int:
        if source_available_counts is None:
            source_available_counts = self._count_pool_available_candidates_by_source()
        if source_raw_counts is None:
            source_raw_counts = self._count_pool_raw_material_by_source()
        if target_counts is None:
            target_counts = self._source_target_counts()
        if raw_target_counts is None:
            raw_target_counts = self._raw_source_target_counts()

        available_target = int(target_counts.get(source_family, 0))
        current_available = self._platform_source_count(source_available_counts, source_family)
        available_deficit = max(0, available_target - current_available)
        try:
            current_global_available = self.database.count_pool_candidates(
                xhs_self_nickname=self._xhs_self_nickname()
            )
        except TypeError:
            current_global_available = self.database.count_pool_candidates()
        global_available_deficit = max(0, self.pool_target_count - int(current_global_available))
        raw_target = int(raw_target_counts.get(source_family, 0))
        current_raw = self._platform_source_count(source_raw_counts, source_family)
        raw_headroom = max(0, raw_target - current_raw)
        requested_by_available = max(0, min(available_deficit, global_available_deficit))
        if requested_by_available <= 0:
            return 0
        if raw_headroom > 0:
            return min(requested_by_available, raw_headroom)
        # Raw ceiling is a trimming guard, not a hard stop for replenishment.
        # A pool can have enough raw material but still be far below the
        # frontend-servable target because existing rows are blocked by topic
        # windows, linkability, copied text/category readiness, or recommendation
        # history. In that state, returning 0 strands pending keywords and leaves
        # the scheduler alive but unable to search.
        return requested_by_available

    def _count_pool_available_candidates_by_source(self) -> dict[str, int]:
        count_fn = getattr(self.database, "count_pool_available_candidates_by_source", None)
        if callable(count_fn):
            try:
                counts = count_fn(xhs_self_nickname=self._xhs_self_nickname())
            except TypeError:
                counts = count_fn()
            return {str(source): int(count) for source, count in dict(counts).items()}
        self._warn_pool_count_fallback_once("available_by_source")
        return self.database.count_pool_candidates_by_source()

    def _count_pool_raw_material_by_source(self) -> dict[str, int]:
        count_fn = getattr(self.database, "count_pool_raw_material_by_source", None)
        if callable(count_fn):
            counts = count_fn()
            return {str(source): int(count) for source, count in dict(counts).items()}
        self._warn_pool_count_fallback_once("raw_material_by_source")
        return self.database.count_pool_candidates_by_source()

    def _warn_pool_count_fallback_once(self, key: str) -> None:
        if key in self._warned_pool_count_fallbacks:
            return
        self._warned_pool_count_fallbacks.add(key)
        logger.warning(
            "pool source count fallback used for %s; production should expose available/raw "
            "source counters to avoid raw-count deadlocks",
            key,
        )

    def _platform_source_count(self, source_counts: dict[str, int], source_family: str) -> int:
        if source_family == "bilibili":
            if "bilibili" in source_counts:
                return int(source_counts.get("bilibili", 0))
            return sum(int(source_counts.get(source, 0)) for source in _BILIBILI_DISCOVERY_SOURCES)
        return int(source_counts.get(source_family, 0))

    def _warn_on_stranded_source_shares(self) -> None:
        """Warn once at startup if any configured share has no producer.

        ``runtime.source_policy.effective_pool_source_shares`` already strips
        sources whose ``enabled`` flag is False, so a stranded share here
        means the user kept the source on but the matching producer is
        not wired (missing build_*_producer, scheduler.enabled=False, …).
        Without this warning the pool sits below ``pool_target_count``
        forever and the missing slack is invisible.
        """
        shares = self._normalized_pool_source_shares()
        targets = self._source_target_counts()
        stranded: list[str] = []
        for source, target in targets.items():
            if target <= 0:
                continue
            if source == "bilibili":
                continue  # always served by the four discovery strategies
            if source == "xiaohongshu" and self.xhs_producer is None:
                stranded.append("xiaohongshu")
            elif source == "douyin" and self.douyin_producer is None:
                stranded.append("douyin")
            elif source == "youtube" and self.youtube_producer is None:
                stranded.append("youtube")
            elif source == "twitter" and self.x_producer is None:
                stranded.append("twitter")
            elif source == "zhihu" and self.zhihu_producer is None:
                stranded.append("zhihu")
            elif source not in {
                "bilibili",
                "xiaohongshu",
                "douyin",
                "youtube",
                "twitter",
                "zhihu",
            }:
                # Unknown source family with an explicit share.
                stranded.append(source)
        if stranded:
            logger.warning(
                "pool_source_shares allocate quota to sources without an "
                "active producer (will leave pool under target): sources=%s "
                "shares=%s",
                stranded,
                {s: shares.get(s) for s in stranded},
            )

    def _normalized_pool_source_shares(self) -> dict[str, int]:
        raw = self.pool_source_shares or _DEFAULT_PLATFORM_SOURCE_SHARES
        normalized: dict[str, int] = {}
        for source in _PLATFORM_SOURCE_ORDER:
            try:
                share = int(raw.get(source, 0))
            except (TypeError, ValueError):
                share = 0
            if share > 0:
                normalized[source] = share
        for source, raw_share in raw.items():
            source_key = str(source).strip().lower()
            if not source_key or source_key in normalized:
                continue
            try:
                share = int(raw_share)
            except (TypeError, ValueError):
                continue
            if share > 0:
                normalized[source_key] = share
        return normalized or dict(_DEFAULT_PLATFORM_SOURCE_SHARES)

    def _requested_refresh_limit(
        self,
        *,
        requested_limit: int,
        current_pool_count: int,
        pool_below_target: bool,
    ) -> int:
        """Decide how many candidates a grouped discovery call should target.

        v0.3.24+ pool-aware sizing. Pre-fix this enforced an absolute
        floor of ``discovery_limit`` (30) per grouped call, even when the
        pool was 595/600 and only needed 5 more items. With 4 strategies
        × 30 = 120 candidates LLM-evaluated per refresh — and the
        suppress-pass keeping only ~20 — that meant ~80% of LLM
        evaluation cost went to candidates that were immediately
        suppressed. The fix sizes each strategy's limit to the smaller
        of total pool gap and requested source gap (with 1.5x oversample
        for items below score threshold and a floor of 5 to keep
        grouped call productive on tiny gaps), capped by ``discovery_limit``
        so a sudden post-init replenish doesn't turn into a single huge
        wave.
        """
        if pool_below_target:
            total_gap = max(0, self.pool_target_count - current_pool_count)
            requested_gap = max(1, int(requested_limit))
            gap = min(total_gap, requested_gap)
            # The 2-phase plan dispatches strategies in groups; per-
            # strategy target is roughly gap // (typical strategy count
            # per phase = 2), with a 1.5x oversample for threshold
            # filtering. Floor at 5 so a strategy that only finds 2
            # interesting items doesn't starve the pool entirely.
            per_strategy_target = max(5, gap * 3 // 4)
            # Cap at discovery_limit to preserve original behaviour
            # when the gap is huge (e.g. fresh init, just-trimmed pool).
            effective_limit = min(self.discovery_limit, per_strategy_target)
            min_eval_batch = self._candidate_eval_batch_floor()
            if min_eval_batch > 1:
                effective_limit = max(effective_limit, min_eval_batch)
        else:
            effective_limit = max(self.discovery_limit, requested_limit)
        return min(_MAX_DISCOVERY_BACKFILL_PER_REFRESH, max(1, effective_limit))

    def _candidate_eval_batch_floor(self) -> int:
        pipeline = self.discovery_candidate_pipeline
        if pipeline is None:
            return 1
        try:
            configured = int(getattr(pipeline, "min_eval_batch_size", 1) or 1)
        except (TypeError, ValueError):
            configured = 1
        return min(_MAX_DISCOVERY_BACKFILL_PER_REFRESH, max(1, configured))

    def _candidate_eval_drain_batch_size(self, batch_size: int | None) -> int:
        default = min(
            _MAX_DISCOVERY_BACKFILL_PER_REFRESH,
            max(self.discovery_limit, _DEFAULT_CANDIDATE_EVAL_BATCH_SIZE),
        )
        if batch_size is None:
            return default
        try:
            requested = int(batch_size)
        except (TypeError, ValueError):
            return default
        if requested <= 0:
            return default
        return requested

    def _requested_strategy_limits(
        self,
        *,
        strategies: list[str],
        requested_limit: int,
        effective_limit: int,
        current_pool_count: int,
        pool_below_target: bool,
    ) -> dict[str, int] | None:
        """Split a grouped Bilibili refresh budget across its strategies."""
        if not pool_below_target or len(strategies) <= 1:
            return None
        if not all(strategy in _BILIBILI_DISCOVERY_SOURCES for strategy in strategies):
            return None
        total_gap = max(1, self.pool_target_count - current_pool_count)
        requested_budget = max(1, int(requested_limit))
        if pool_below_target:
            min_eval_batch = self._candidate_eval_batch_floor()
            total_gap = max(total_gap, min_eval_batch)
            requested_budget = max(requested_budget, min_eval_batch)
        shared_budget = min(
            requested_budget,
            max(1, int(effective_limit)),
            total_gap,
        )
        return self._split_budget_across_strategies(strategies, shared_budget)

    @staticmethod
    def _split_budget_across_strategies(
        strategies: list[str],
        budget: int,
    ) -> dict[str, int]:
        if not strategies:
            return {}
        safe_budget = max(0, int(budget))
        base, extra = divmod(safe_budget, len(strategies))
        return {
            strategy: base + (1 if index < extra else 0)
            for index, strategy in enumerate(strategies)
        }

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
