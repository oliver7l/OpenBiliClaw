"""ContinuousRefreshController mixin: 补货来源配额与预算规划。

从 ``runtime/refresh.py`` 拆出的方法组；``ContinuousRefreshController``
继承本 mixin，调用方代码无需修改。
"""

from __future__ import annotations

import logging
from typing import Any

from openbiliclaw.runtime._refresh_shared import (
    _BILIBILI_DISCOVERY_SOURCES,
    _DEFAULT_CANDIDATE_EVAL_BATCH_SIZE,
    _DEFAULT_PLATFORM_SOURCE_SHARES,
    _MAX_DISCOVERY_BACKFILL_PER_REFRESH,
    _PLATFORM_SOURCE_ORDER,
)

logger = logging.getLogger(__name__)


class SourceBudgetMixin:
    """补货来源配额与预算规划。"""

    _pending_signal_events_count: Any  # 由 ContinuousRefreshController 提供
    _warned_pool_count_fallbacks: Any  # 由 ContinuousRefreshController 提供
    _xhs_self_nickname: Any  # 由 ContinuousRefreshController 提供
    database: Any  # 由 ContinuousRefreshController 提供
    discovery_candidate_pipeline: Any  # 由 ContinuousRefreshController 提供
    discovery_limit: Any  # 由 ContinuousRefreshController 提供
    douyin_producer: Any  # 由 ContinuousRefreshController 提供
    memory_manager: Any  # 由 ContinuousRefreshController 提供
    pool_source_shares: Any  # 由 ContinuousRefreshController 提供
    pool_target_count: Any  # 由 ContinuousRefreshController 提供
    signal_event_threshold: Any  # 由 ContinuousRefreshController 提供
    x_producer: Any  # 由 ContinuousRefreshController 提供
    xhs_producer: Any  # 由 ContinuousRefreshController 提供
    youtube_producer: Any  # 由 ContinuousRefreshController 提供
    zhihu_producer: Any  # 由 ContinuousRefreshController 提供

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
