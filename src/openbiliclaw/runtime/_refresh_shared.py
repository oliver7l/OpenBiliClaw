"""ContinuousRefreshController 共享常量与签名探测工具。

从 ``runtime/refresh.py`` 拆出的模块级常量与函数；refresh 核心、各 mixin
统一从本模块导入，避免 mixin → refresh 反向依赖。
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, ClassVar, Protocol

from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable, Coroutine

    from openbiliclaw.config import SchedulerConfig
    from openbiliclaw.runtime.presence import PresenceTracker
    from openbiliclaw.runtime.task_registry import BackgroundTaskRegistry

_MAX_DISCOVERY_BACKFILL_PER_REFRESH = 60
_DEFAULT_CANDIDATE_EVAL_BATCH_SIZE = 45
# How often the cover-image disk cache is pruned of consumed + unsaved covers.
# The bulk one-shot prune runs at API startup; this is the steady-state sweep.
_IMAGE_CACHE_CLEANUP_INTERVAL_SECONDS = 6 * 60 * 60
# Discovery-time cover prefetch: cache covers while their CDN token is still fresh
# (XHS signed URLs expire fast). Runs often, scans recent discoveries newest-first,
# and is bounded per tick so it never floods a CDN.
_COVER_PREFETCH_INTERVAL_SECONDS = 60
_COVER_PREFETCH_RECENT_HOURS = 12
_COVER_PREFETCH_SCAN = 300
_COVER_PREFETCH_MAX_FETCH = 40
_DEFAULT_PLATFORM_SOURCE_SHARES: dict[str, int] = {
    "bilibili": 5,
}
_PLATFORM_SOURCE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu")
_BILIBILI_DISCOVERY_SOURCES = ("search", "related_chain", "trending", "explore")
_PROBE_CHALLENGE_MODES = {"lateral", "bridge", "wildcard"}


def _call_accepts_limit(fn: Any) -> bool:
    """Return whether a producer callable accepts a ``limit=`` keyword."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "limit" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_strategy_limits(fn: Any) -> bool:
    """Return whether a discovery callable accepts ``strategy_limits=``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "strategy_limits" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_pool_snapshot(fn: Any) -> bool:
    """Return whether a discovery callable accepts ``pool_snapshot=``."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "pool_snapshot" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_keywords(fn: Any) -> bool:
    """Return whether a discovery callable accepts a ``keywords=`` keyword.

    Used for the direct-engine B站 search fallback path so the unified keyword
    planner's injected words are only forwarded to engines/stubs that declare
    the kwarg — stubs without it stay byte-compatible (flag-off / tests).
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "keywords" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _call_accepts_keyword_ids(fn: Any) -> bool:
    """Return whether a discovery callable accepts a ``keyword_ids=`` keyword.

    P1.8 parallel of :func:`_call_accepts_keywords` for the direct-engine B站
    search fallback so the keyword→id provenance map is only forwarded to
    engines that declare it; stubs without it stay byte-compatible.
    """
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return True
    return "keyword_ids" in signature.parameters or any(
        param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()
    )


def _string_state_map(value: object) -> dict[str, str]:
    """Normalize a JSON object field into a string-to-string map."""
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


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
    def count_pool_readiness(
        self, *, xhs_self_nickname: str = "", allow_stale: bool = False
    ) -> dict[str, int]: ...
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


class RefreshControllerAttrs:
    """ContinuousRefreshController 的实例属性类型基座（非 dataclass）。

    各 mixin 继承本类以获得与拆分前一致的 mypy 视图；字段实体与
    ``field(...)`` 默认值仍全部定义在核心 dataclass 中。
    """

    memory_manager: SupportsRuntimeState
    database: SupportsEventDatabase
    soul_engine: SupportsProfileEngine
    discovery_engine: SupportsDiscoveryEngine
    recommendation_engine: SupportsRecommendationEngine
    event_hub: Any | None
    discovery_candidate_pipeline: Any | None
    bilibili_producer: Any | None
    xhs_producer: Any | None
    douyin_producer: Any | None
    youtube_producer: Any | None
    x_producer: Any | None
    zhihu_producer: Any | None
    rss_adapter_registry: Any | None
    scheduler_config: SchedulerConfig
    presence: PresenceTracker
    init_active_check: Callable[[], bool] | None
    signal_event_threshold: int
    event_refresh_minutes: int
    trending_refresh_hours: int
    explore_refresh_hours: int
    notification_cooldown_hours: int
    delight_cooldown_hours: int
    check_interval_seconds: int
    proactive_push_interval_seconds: int
    discovery_limit: int
    pool_target_count: int
    pool_source_shares: dict[str, int]
    task_registry: BackgroundTaskRegistry | None
    keyword_planner: Any | None
    keyword_fetch: Any | None
    _manual_refresh_task: asyncio.Task[None] | None
    _discovery_drain_lock: asyncio.Lock
    _refresh_lock: asyncio.Lock
    _manual_refresh_state: str
    _manual_refresh_message: str
    _manual_refresh_started_at: str
    _manual_refresh_finished_at: str
    _pending_replenishment_reasons: set[str]
    _last_pool_maintenance_fingerprint: tuple[int, int, str]
    _warned_pool_count_fallbacks: set[str]
    _last_published_pool_count: int
    _profile_ready_observed: bool
    _init_grace_consumed: bool
    _last_llm_gate_allowed: bool
    _loop_meta: dict[str, dict[str, object]]
    _signal_event_types: ClassVar[list[str]]

    _PROBE_COOLDOWN_HOURS: Any
    _build_source_replenishment_plan: Callable[..., list[tuple[list[str], int]]]
    _candidate_eval_drain_batch_size: Any
    _count_pool_available_candidates_by_source: Any
    _count_pool_raw_material_by_source: Any
    _dedupe_topics: Any
    _drain_discovery_candidates_and_precompute: Any
    _enforce_pool_cap: Any
    _extract_topics: Any
    _int_state_value: Any
    _is_due: Any
    _is_initialized: Any
    _list_state_value: Any
    _llm_work_allowed: Any
    _loop_bilibili_producer: Any
    _loop_cover_prefetch: Any
    _loop_douyin_producer: Any
    _loop_image_cache_cleanup: Any
    _loop_rss_polling: Any
    _loop_wechat_polling: Any
    _loop_x_producer: Any
    _loop_xhs_producer: Any
    _loop_xiaoyuzhou_polling: Any
    _loop_youtube_producer: Any
    _loop_zhihu_producer: Any
    _normalized_pool_source_shares: Any
    _now: Any
    _parse_iso_datetime: Any
    _pending_signal_events_count: Callable[..., int]
    _pool_count_payload: Any
    _pool_readiness_counts: Any
    _precompute_lock: Any
    _publish_delight_if_available: Any
    _publish_event: Any
    _publish_precompute_replenishment_if_needed: Any
    _publish_probe_if_available: Any
    _queue_replenishment_reason: Callable[..., dict[str, object]]
    _raw_source_target_counts: Any
    _requested_refresh_limit: Any
    _requested_strategy_limits: Any
    _safe_count_delight_candidates: Any
    _safe_precompute_pool_copy: Any
    _safe_prewarm_pool_mmr_embeddings: Any
    _safe_prewarm_supergroup_embeddings: Any
    _source_deficit: Any
    _source_requested_count: Any
    _source_target_counts: Any
    _strategy_message: Any
    _track_task: Any
    _update_discovery_runtime_state: Any
    _warn_on_stranded_source_shares: Any
    _xhs_self_nickname: Any
    force_refresh: Callable[..., Coroutine[Any, Any, dict[str, object]]]
    prepare_delight_candidates: Any
    refresh_if_needed: Any
    request_replenishment: Callable[..., Coroutine[Any, Any, dict[str, object]]]
    trigger_manual_refresh: Callable[..., Coroutine[Any, Any, dict[str, object]]]
