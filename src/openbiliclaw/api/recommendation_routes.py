"""Recommendation feed API routes (M1 extraction from ``api/app.py``).

This module owns the recommendation-stream HTTP surface:

- ``GET /api/recommendations`` + ``POST /api/recommendations/{reshuffle,append,refresh}``
- ``GET /api/pool/all``
- ``POST|DELETE /api/user-feedback``, ``GET /api/user-feedback/batch``
- ``GET /api/interest-tags``
- ``GET /api/view-history``, ``POST /api/view-record``, ``POST /api/view-dwell``
- ``GET /api/agent-recommend``

M1 extraction rule (behavior-preserving): every endpoint and helper below is
moved verbatim from the ``create_app`` closure in ``api/app.py`` — only the
decorator target changes (``app`` → ``router``) and the previously-closed-over
values become explicit ``build_recommendation_router(...)`` parameters:

- ``ctx`` / ``config``: runtime context + config (unchanged semantics)
- ``fire_and_forget_tasks``: module-level task set from ``api/app.py``
- ``init_active_now``: ``_init_active_now`` (shared with other handlers)
- ``pick_best_xhs_url``: ``_pick_best_xhs_url`` (shared with other handlers)
- ``serialize_recommendation_items``: ``_serialize_recommendation_items``
  (shared with the chat/recommend handler)
- ``load_interest_keywords``: ``_load_interest_keywords`` (shared)
- ``request_runtime_replenishment``: ``_request_runtime_replenishment`` (shared
  with the init-completed / event-ingest handlers in ``api/app.py``)

Per-module state that used to live in ``create_app`` (auto-replenishment
debounce, agent session cache) now lives in this builder's closure.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os.path
import random
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel

from openbiliclaw.api.models import (
    PoolAllResponse,
    PoolItemOut,
    RecommendationAppendIn,
    RecommendationListResponse,
    RecommendationOut,
    RecommendationRefreshResponse,
    RecommendationReshuffleResponse,
    ViewDwellIn,
    ViewHistoryOut,
    ViewRecordIn,
)
from openbiliclaw.recommendation.agents import IntentAgent, InterestSyncer, RankAgent
from openbiliclaw.recommendation.quality_scorer import (
    QualityScorer,
    SupportsQualityCandidate,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# Debounce window between automatic pool replenishment requests (was a
# module-level constant in api/app.py, only consumed by the recommendation
# endpoints moved here).
_AUTO_REPLENISH_DEBOUNCE_SECONDS = 30.0


def _cap_by_franchise(
    rows: list[dict[str, Any]],
    *,
    max_per_franchise: int = 2,
) -> list[dict[str, Any]]:
    """Drop later duplicates of the same ``franchise_key`` from a list.

    ``franchise_key`` is the LLM-tagged IP / series column (set during
    content evaluation, see ``llm/prompts.py`` and
    ``discovery/engine.py``). Empty franchise = general-interest content
    (科普 / 美食 / 通用资讯…) and passes through with no constraint —
    only matched IPs are subject to the cap.

    Why not in SQL: the recommendation pipeline orders by
    ``created_at DESC`` and we want a stable preserve-newest-N filter
    that's clearly testable. SQL window functions could do it, but the
    in-Python pass is cheap (≤ 40 rows) and easy to audit.

    Moved verbatim from ``api/app.py::_cap_by_franchise`` (only the
    recommendation endpoints referenced it).
    """
    if max_per_franchise <= 0:
        return list(rows)
    seen: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        franchise = str(row.get("franchise_key", "") or "").strip()
        if not franchise:
            out.append(row)
            continue
        if seen.get(franchise, 0) >= max_per_franchise:
            continue
        seen[franchise] = seen.get(franchise, 0) + 1
        out.append(row)
    return out


def build_recommendation_router(
    *,
    ctx: Any,
    config: Any,
    fire_and_forget_tasks: set[asyncio.Task[Any]],
    init_active_now: Callable[[], bool],
    pick_best_xhs_url: Callable[..., str],
    serialize_recommendation_items: Callable[[list[Any]], list[RecommendationOut]],
    load_interest_keywords: Callable[[], list[tuple[str, float]]],
    request_runtime_replenishment: Callable[..., Awaitable[dict[str, object] | None]],
) -> APIRouter:
    """Build the recommendation-stream ``APIRouter``.

    All parameters are runtime dependencies owned by ``create_app``;
    passing them explicitly keeps this module free of imports from
    ``api/app.py`` (no circular import).
    """
    router = APIRouter()

    # ── Per-router state (was create_app closure state) ─────────────
    auto_replenishment_task: asyncio.Task[None] | None = None
    auto_replenishment_started_at = 0.0
    # Multi-turn session cache: {session_id: {where_clauses, params, keywords}}
    _agent_session_cache: dict[str, dict[str, Any]] = {}

    # ── Response models (was create_app closure models) ─────────────

    class InterestTagOut(BaseModel):
        tag: str
        weight: int
        source_platforms: list[str] = []
        count: int

    class InterestTagsResponse(BaseModel):
        tags: list[InterestTagOut]

    # ── Helpers (moved verbatim from api/app.py) ────────────────────

    @dataclass
    class _QualityScorerCandidate:
        """Minimal candidate shape for QualityScorer.score_batch."""

        bvid: str
        title: str
        description: str
        up_name: str
        source_platform: str
        topic_key: str
        relevance_score: float
        content_type: str
        body_text: str

    async def _bg_quality_score_recommendations(rows: list[dict[str, Any]]) -> None:
        """Score recommendation items that lack quality scores, in background."""
        logger.info(
            "_bg_quality_score_recommendations called with %d rows, llm=%s, soul=%s",
            len(rows),
            ctx.llm_service is not None,
            ctx.soul_engine is not None,
        )
        if not rows or ctx.llm_service is None or ctx.soul_engine is None:
            return
        to_score = [r for r in rows if not float(r.get("quality_score", 0.0) or 0.0)]
        if not to_score:
            return
        try:
            profile = await ctx.soul_engine.get_profile()
            scorer = QualityScorer(llm_service=ctx.llm_service, batch_size=10)
            candidates = [
                _QualityScorerCandidate(
                    bvid=str(r.get("bvid", "")),
                    title=str(r.get("title", "")),
                    description=str(r.get("body_text", r.get("expression", ""))),
                    up_name=str(r.get("up_name", "")),
                    source_platform=str(r.get("source_platform", "")),
                    topic_key=str(r.get("topic", "")),
                    relevance_score=float(r.get("confidence", 0.0) or 0.0),
                    content_type=str(r.get("content_type", "video")),
                    body_text=str(r.get("body_text", "")),
                )
                for r in to_score
            ]
            scored = await scorer.score_batch(
                cast("list[SupportsQualityCandidate]", candidates), profile
            )
            db_scores: list[tuple[str, float, str]] = []
            for bvid, result in scored.items():
                db_scores.append((bvid, result["quality_score"], result["reason"]))
            if db_scores:
                ctx.database.batch_update_content_quality_scores(db_scores)
                logger.info(
                    "Quality scored %d/%d recommendation items",
                    len(db_scores),
                    len(to_score),
                )
        except Exception:
            logger.exception("Background quality scoring failed")

    def _enrich_xhs_urls(
        serialized: list[RecommendationOut],
        database: Any,
    ) -> None:
        """Post-process serialized recommendations to fix Xiaohongshu URLs.

        Xiaohongshu explore-feed cards carry ``xsec_token`` in the URL but
        search-result pages don't. Items that entered the pool via the search
        path may have a bare URL that triggers a login wall on click. This
        helper looks up ``xhs_observed_urls`` for a tokenized variant and
        swaps it in — mutates ``serialized`` in place.
        """
        for rec in serialized:
            if rec.source_platform != "xiaohongshu":
                continue
            if "xsec_token=" in rec.content_url:
                continue
            note_id = rec.content_url.rstrip("/").rsplit("/", 1)[-1]
            if not note_id:
                continue
            try:
                better = pick_best_xhs_url(database, note_id, rec.content_url)
                if better != rec.content_url:
                    rec.content_url = better
            except Exception:
                continue

    def _pool_available_count() -> int | None:
        """Return the best available servable-pool count for hot-path guards."""
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            with suppress(Exception):
                status = get_runtime_status()
                if isinstance(status, dict) and "pool_available_count" in status:
                    return max(0, int(status.get("pool_available_count") or 0))

        readiness = getattr(ctx.database, "count_pool_readiness", None)
        if callable(readiness):
            with suppress(Exception):
                counts = readiness()
                if isinstance(counts, dict) and "available" in counts:
                    return max(0, int(counts.get("available") or 0))

        count_pool = getattr(ctx.database, "count_pool_candidates", None)
        if callable(count_pool):
            with suppress(Exception):
                return max(0, int(count_pool()))
        return None

    def _runtime_pool_status_payload() -> dict[str, object]:
        """Return frontend runtime fields needed to resync pool status."""
        status: dict[str, object] = {}
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if callable(get_runtime_status):
            with suppress(Exception):
                runtime_status = get_runtime_status()
                if isinstance(runtime_status, dict):
                    status.update(runtime_status)

        if "pool_available_count" not in status:
            readiness = getattr(ctx.database, "count_pool_readiness", None)
            if callable(readiness):
                with suppress(Exception):
                    counts = readiness()
                    if isinstance(counts, dict):
                        status.update(
                            {
                                "pool_available_count": counts.get("available", 0),
                                "pool_raw_count": counts.get("raw", counts.get("available", 0)),
                                "pool_pending_count": counts.get("pending", 0),
                                "pool_pending_eval_count": counts.get("pending_eval", 0),
                                "pool_evaluated_pending_count": counts.get("evaluated_pending", 0),
                            }
                        )
            else:
                count_pool = getattr(ctx.database, "count_pool_candidates", None)
                if callable(count_pool):
                    with suppress(Exception):
                        status["pool_available_count"] = int(count_pool())

        int_fields = (
            "pool_available_count",
            "pool_raw_count",
            "pool_pending_count",
            "pool_pending_eval_count",
            "pool_evaluated_pending_count",
            "pool_target_count",
            "last_replenished_count",
            "last_discovered_count",
        )
        payload: dict[str, object] = {}
        for field in int_fields:
            if field not in status:
                continue
            raw_value = status.get(field)
            if raw_value is None:
                raw_value = 0
            with suppress(TypeError, ValueError):
                payload[field] = max(0, int(cast("Any", raw_value)))
        recent_pool_topics = status.get("recent_pool_topics")
        if isinstance(recent_pool_topics, list):
            payload["recent_pool_topics"] = [
                str(item) for item in recent_pool_topics if str(item).strip()
            ]
        return payload

    async def _publish_pool_status_snapshot(message: str = "推荐池已同步") -> None:
        """Broadcast pool counts after recommendation endpoints consume inventory."""
        event_hub = getattr(ctx, "event_hub", None) or getattr(
            ctx.runtime_controller, "event_hub", None
        )
        publish = getattr(event_hub, "publish", None)
        if not callable(publish):
            return
        event = {
            "type": "refresh.pool_updated",
            "phase": "done",
            "message": message,
            # The payload builder runs several pool-count SQL queries; keep
            # them off the request event loop (they take 100ms-1s on a large
            # pool and otherwise stall every in-flight request).
            **await asyncio.get_running_loop().run_in_executor(None, _runtime_pool_status_payload),
        }
        with suppress(Exception):
            result = publish(event)
            if asyncio.iscoroutine(result):
                await result

    async def _run_auto_replenishment(trigger: Callable[[], Any]) -> None:
        try:
            await trigger()
        except Exception:
            logger.exception("Automatic pool replenishment failed")

    async def _trigger_replenishment_if_needed(*, force: bool = False) -> None:
        """Fire a background Discovery refresh when the pool runs low."""
        nonlocal auto_replenishment_started_at, auto_replenishment_task
        if not force:
            curator = getattr(ctx.recommendation_engine, "_curator", None)
            if curator is None or not hasattr(curator, "needs_replenishment"):
                return
            # needs_replenishment() runs pool-count SQL synchronously; keep it
            # off the event loop so it cannot stall in-flight requests.
            needs = await asyncio.get_running_loop().run_in_executor(
                None, curator.needs_replenishment
            )
            if not needs:
                return

        now = time.monotonic()
        if auto_replenishment_task is not None and not auto_replenishment_task.done():
            logger.debug("Pool low - automatic replenishment already running; skipping")
            return
        if now - auto_replenishment_started_at < _AUTO_REPLENISH_DEBOUNCE_SECONDS:
            logger.debug("Pool low - automatic replenishment recently requested; skipping")
            return

        auto_replenishment_started_at = now
        logger.info("Pool low - triggering automatic replenishment")
        reason = "pool_empty" if force else "pool_low_after_recommendation_refresh"
        task = asyncio.create_task(
            _run_auto_replenishment(
                lambda: request_runtime_replenishment(reason=reason, force=True)
            )
        )
        auto_replenishment_task = task
        fire_and_forget_tasks.add(task)

    # ── Endpoints (moved verbatim from api/app.py) ──────────────────

    @router.get("/api/recommendations", response_model=RecommendationListResponse)
    async def recommendations() -> RecommendationListResponse:
        def _admission_min_score() -> float:
            runtime_config = getattr(ctx, "config", None) or config
            discovery_config = getattr(runtime_config, "discovery", None)
            try:
                threshold = float(getattr(discovery_config, "admission_min_score", 0.60) or 0.60)
            except (TypeError, ValueError):
                return 0.60
            return threshold if 0.0 < threshold <= 1.0 else 0.60

        def _filter_low_confidence(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
            threshold = _admission_min_score()
            filtered: list[dict[str, Any]] = []
            for row in rows:
                if "confidence" not in row:
                    filtered.append(row)
                    continue
                try:
                    confidence = float(row.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    confidence = 0.0
                if confidence >= threshold:
                    filtered.append(row)
            return filtered

        # Pull a 2x window so the per-franchise cap below still has 20
        # survivors to return after dropping over-represented IPs.
        # Without the wider pool, capping 原神 at 2 in a 20-row request
        # would leave gaps that other items further back in time would
        # have filled.
        rows = _filter_low_confidence(
            ctx.database.get_recommendations(limit=40, exclude_processed=True)
        )

        # Fresh-install bootstrap: ``recommendations`` table is the
        # write-only history of items we've ever served. On first popup
        # load nobody has called ``reshuffle`` / ``append`` / CLI
        # ``recommend`` yet, so the table is empty even if the discovery
        # pool already has 100+ scored candidates. Surface those by
        # bootstrapping a single ``serve()`` call right here — it writes
        # 10 fresh entries to the history table that the next ``rows =
        # get_recommendations`` re-read will pick up. Failure is fully
        # silent: any error returns the original empty list, leaving
        # the popup's "正在补货" state intact and giving the regular
        # refresh tick another chance.
        # gui-init D1: this empty-history bootstrap calls serve(), which WRITES
        # (recommendation rows + pool "shown" markers). It's a side-effecting
        # GET, so the deny-by-default middleware (POST/PUT/PATCH/DELETE) doesn't
        # cover it — skip it during an active init so a read can't serve from /
        # mark a half-built pool. The post-init refresh tick serves normally.
        if (
            not rows
            and not init_active_now()
            and ctx.recommendation_engine is not None
            and ctx.soul_engine is not None
        ):
            with suppress(Exception):
                pool_count_fn = getattr(ctx.database, "count_pool_candidates", None)
                pool_count = int(pool_count_fn()) if callable(pool_count_fn) else 0
                if pool_count > 0:
                    profile = await ctx.soul_engine.get_profile()
                    await ctx.recommendation_engine.serve(profile, limit=10)
                    rows = _filter_low_confidence(
                        ctx.database.get_recommendations(limit=40, exclude_processed=True)
                    )
                    await _publish_pool_status_snapshot()
                    logger.info(
                        "GET /api/recommendations bootstrap: served from "
                        "empty history (pool_count=%d → wrote %d to history)",
                        pool_count,
                        len(rows),
                    )

        rows = _cap_by_franchise(rows, max_per_franchise=2)[:20]

        # M4: record exposure. Serving a recommendation to the UI is an
        # impression — persist presented=1 / presented_at so the offline
        # eval loop (and any future online CTR metric) has real
        # exposure→feedback data instead of a table stuck at presented=0.
        _mark_presented_ids = [int(r["id"]) for r in rows if r.get("id") is not None]
        if _mark_presented_ids:
            try:
                ctx.database.mark_recommendations_presented(_mark_presented_ids)
            except Exception:
                logger.exception("mark_recommendations_presented (recommendations) failed")

        # Fire background quality scoring for items that lack scores
        task = asyncio.create_task(_bg_quality_score_recommendations(rows))
        fire_and_forget_tasks.add(task)
        task.add_done_callback(fire_and_forget_tasks.discard)

        reshuffle_items = []
        for row in rows:
            item_url = str(row.get("content_url", "") or "")
            item_platform = str(row.get("source_platform", "") or "bilibili")
            # xiaohongshu: try to upgrade bare URL with xsec_token
            if item_platform == "xiaohongshu" and item_url and "xsec_token=" not in item_url:
                note_id = str(row.get("bvid", "") or row.get("content_id", "") or "")
                if note_id:
                    with suppress(Exception):
                        item_url = pick_best_xhs_url(ctx.database, note_id, item_url)
            reshuffle_items.append(
                RecommendationOut(
                    id=int(row["id"]),
                    bvid=str(row.get("bvid", "")),
                    title=str(row.get("title", "")),
                    up_name=str(row.get("up_name", "")),
                    cover_url=str(row.get("cover_url", "")),
                    expression=str(row.get("expression", "")),
                    topic_label=str(row.get("topic", "")),
                    presented=bool(row.get("presented", 0)),
                    feedback_type=str(row.get("feedback_type", "") or ""),
                    content_id=str(row.get("content_id", "") or row.get("bvid", "")),
                    content_url=item_url,
                    source_platform=item_platform,
                    content_type=str(row.get("content_type", "") or "video"),
                    body_text=str(row.get("body_text", "") or ""),
                    quality_score=float(row.get("quality_score", 0.0) or 0.0),
                    quality_reason=str(row.get("quality_reason", "") or ""),
                )
            )

        return RecommendationListResponse(items=reshuffle_items)

    @router.post("/api/recommendations/reshuffle", response_model=RecommendationReshuffleResponse)
    async def reshuffle_recommendations(
        platform: str | None = Query(
            default=None,
            description="Restrict the fresh batch to this source_platform (e.g. bilibili, xiaohongshu).",
        ),
        limit: int = Query(
            default=10, ge=1, le=5000, description="Number of recommendations to return per batch."
        ),
    ) -> RecommendationReshuffleResponse:
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        # 池为空（罕见，如首次部署/刚耗尽）时直接返回空并触发补货，跳过
        # profile/reshuffle 路径——与 append 端点保持一致，避免无谓的 soul
        # 调用和空转。_pool_available_count() 同步执行 count SQL，放到
        # executor 里避免阻塞事件循环。
        if await asyncio.get_running_loop().run_in_executor(None, _pool_available_count) == 0:
            await _trigger_replenishment_if_needed(force=True)
            return RecommendationReshuffleResponse(items=[])
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        items = await ctx.recommendation_engine.reshuffle_recommendations(
            profile=profile, limit=limit, platform=platform
        )
        _serialized = serialize_recommendation_items(items)
        _enrich_xhs_urls(_serialized, ctx.database)
        # M4: record exposure for freshly shown batch.
        _mark_presented_ids = [
            r.recommendation_id for r in items if getattr(r, "recommendation_id", None)
        ]
        if _mark_presented_ids:
            try:
                ctx.database.mark_recommendations_presented(_mark_presented_ids)
            except Exception:
                logger.exception("mark_recommendations_presented (reshuffle) failed")
        # Best-effort post-processing — explicitly kept OFF the user's
        # latency path. serve() already consumed pool inventory and the
        # batch buffer refills asynchronously, so we must not block the
        # HTTP response on snapshot publishing / replenishment checks.
        try:
            _loop = asyncio.get_running_loop()
            _loop.create_task(_publish_pool_status_snapshot())
            _loop.create_task(_trigger_replenishment_if_needed())
        except RuntimeError:
            pass
        return RecommendationReshuffleResponse(items=_serialized)

    @router.post("/api/recommendations/append", response_model=RecommendationReshuffleResponse)
    async def append_recommendations(
        payload: RecommendationAppendIn,
    ) -> RecommendationReshuffleResponse:
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return RecommendationReshuffleResponse(items=[])
        # _pool_available_count() runs count SQL synchronously; keep it off
        # the event loop.
        if await asyncio.get_running_loop().run_in_executor(None, _pool_available_count) == 0:
            await _trigger_replenishment_if_needed(force=True)
            return RecommendationReshuffleResponse(items=[])
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return RecommendationReshuffleResponse(items=[])
        items = await ctx.recommendation_engine.append_recommendations(
            profile=profile,
            excluded_bvids=payload.excluded_bvids,
            limit=10,
        )
        await _publish_pool_status_snapshot()
        await _trigger_replenishment_if_needed()
        _serialized = serialize_recommendation_items(items)
        _enrich_xhs_urls(_serialized, ctx.database)
        # M4: record exposure for freshly shown batch.
        _mark_presented_ids = [
            r.recommendation_id for r in items if getattr(r, "recommendation_id", None)
        ]
        if _mark_presented_ids:
            try:
                ctx.database.mark_recommendations_presented(_mark_presented_ids)
            except Exception:
                logger.exception("mark_recommendations_presented (append) failed")
        return RecommendationReshuffleResponse(items=_serialized)

    @router.post("/api/recommendations/refresh", response_model=RecommendationRefreshResponse)
    async def refresh_recommendations() -> RecommendationRefreshResponse:
        result = await request_runtime_replenishment(reason="manual", force=True)
        if not isinstance(result, dict):
            return RecommendationRefreshResponse(
                ok=True,
                accepted=False,
                state="idle",
                reason="runtime_unavailable",
            )
        return RecommendationRefreshResponse(
            ok=True,
            accepted=bool(result.get("accepted", False)),
            state=str(result.get("state", "idle")),
            reason=str(result.get("reason", "")),
        )

    @router.get("/api/pool/all", response_model=PoolAllResponse)
    async def pool_all(
        platform: str | None = Query(default=None, description="Filter by source_platform."),
        source: str | None = Query(default=None, description="Filter by source (e.g. xhs-feed)."),
        status: str | None = Query(
            default=None,
            description="Filter by pool_status (fresh, shown, stale, suppressed, pending).",
        ),
        shuffle: bool = Query(default=False, description="Randomize the result order."),
        limit: int = Query(default=10000, ge=1, le=10000, description="Max items to return."),
        min_score: float | None = Query(
            default=None, ge=0.0, le=1.0, description="Minimum quality_score filter."
        ),
        max_score: float | None = Query(
            default=None, ge=0.0, le=1.0, description="Maximum quality_score filter."
        ),
        scored_only: bool = Query(default=False, description="Only items with quality_score > 0."),
        unscored_only: bool = Query(
            default=False, description="Only items with quality_score = 0."
        ),
        topic_group: str | None = Query(default=None, description="Filter by topic_group."),
        has_url: bool | None = Query(
            default=None, description="Filter by content_url presence (true=has url, false=no url)."
        ),
        has_expression: bool | None = Query(
            default=None, description="Filter by pool_expression presence."
        ),
        date_from: str | None = Query(
            default=None, description="Filter by discovered_at >= YYYY-MM-DD."
        ),
        date_to: str | None = Query(
            default=None, description="Filter by discovered_at <= YYYY-MM-DD."
        ),
    ) -> PoolAllResponse:
        db = getattr(ctx, "database", None)
        if db is None:
            return PoolAllResponse(items=[], total=0, available=0, raw=0, pending=0)
        try:
            loop = asyncio.get_running_loop()
            pool_counts = await loop.run_in_executor(None, db.count_pool_readiness)
            available = int(pool_counts.get("available", 0))
            raw = int(pool_counts.get("raw", 0))
            pending = int(pool_counts.get("pending", 0))

            where_clauses = [
                "pool_status IS NOT NULL",
                "COALESCE(pool_status, '') != ''",
                "COALESCE(pool_status, '') != 'purged_by_dislike'",
            ]
            params: list[Any] = []
            if platform:
                where_clauses.append("source_platform = ?")
                params.append(platform)
            if source:
                where_clauses.append("source = ?")
                params.append(source)
            if status:
                where_clauses.append("pool_status = ?")
                params.append(status)
            if min_score is not None:
                where_clauses.append("quality_score >= ?")
                params.append(min_score)
            if max_score is not None:
                where_clauses.append("quality_score <= ?")
                params.append(max_score)
            if scored_only:
                where_clauses.append("quality_score > 0.0")
            if unscored_only:
                where_clauses.append("(quality_score IS NULL OR quality_score = 0.0)")
            if topic_group:
                where_clauses.append("topic_group = ?")
                params.append(topic_group)
            if has_url is True:
                where_clauses.append("COALESCE(content_url, '') != ''")
            elif has_url is False:
                where_clauses.append("(content_url IS NULL OR content_url = '')")
            if has_expression is True:
                where_clauses.append("COALESCE(pool_expression, '') != ''")
            elif has_expression is False:
                where_clauses.append("(pool_expression IS NULL OR pool_expression = '')")
            if date_from:
                where_clauses.append("discovered_at >= ?")
                params.append(date_from)
            if date_to:
                where_clauses.append("discovered_at <= ?")
                params.append(date_to)

            # 先查总数
            count_sql = (
                f"SELECT COUNT(*) AS cnt FROM content_cache WHERE {' AND '.join(where_clauses)}"
            )
            total_row = await loop.run_in_executor(
                None, lambda: db.conn.execute(count_sql, params).fetchall()
            )
            total = int(total_row[0]["cnt"]) if total_row else 0

            # 排序：随机或按状态+质量分
            if shuffle:
                # 优化：先查所有满足条件的 rowid（只查小字段，不排序），
                # 再在 Python 中随机采样，最后用 rowid IN (...) 查完整数据。
                # 避免对 75000 行的大字段进行 ORDER BY RANDOM() 排序。
                import random as _random

                id_sql = f"""
                    SELECT rowid FROM content_cache
                    WHERE {" AND ".join(where_clauses)}
                """
                id_rows = await loop.run_in_executor(
                    None, lambda: db.conn.execute(id_sql, params).fetchall()
                )
                all_rowids = [r["rowid"] for r in id_rows]
                sample_size = min(limit, len(all_rowids))
                selected_rowids = _random.sample(all_rowids, sample_size) if sample_size > 0 else []
                if selected_rowids:
                    placeholders = ",".join(["?"] * len(selected_rowids))
                    sql = f"""
                        SELECT bvid, title, up_name, source_platform, content_type,
                               cover_url, content_url, body_text, pool_status, quality_score, quality_reason,
                               topic_group, pool_expression
                        FROM content_cache
                        WHERE rowid IN ({placeholders})
                    """
                    rows = await loop.run_in_executor(
                        None, lambda: db.conn.execute(sql, selected_rowids).fetchall()
                    )
                else:
                    rows = []
            else:
                order_clause = """
                ORDER BY
                  CASE pool_status
                    WHEN 'fresh' THEN 1
                    WHEN 'feedbacked' THEN 2
                    WHEN 'shown' THEN 3
                    WHEN 'stale' THEN 4
                    WHEN 'suppressed' THEN 5
                    ELSE 6
                  END,
                  quality_score DESC,
                  bvid DESC
            """
                sql = f"""
                    SELECT bvid, title, up_name, source_platform, content_type,
                           cover_url, content_url, body_text, pool_status, quality_score, quality_reason,
                           topic_group, pool_expression
                    FROM content_cache
                    WHERE {" AND ".join(where_clauses)}
                    {order_clause}
                    LIMIT ?
                """
                params.append(limit)
                rows = await loop.run_in_executor(
                    None, lambda: db.conn.execute(sql, params).fetchall()
                )
            items = []
            for r in rows:
                item_url = str(r["content_url"] or "")
                item_platform = str(r["source_platform"] or "")
                # xiaohongshu: try to upgrade bare URL with xsec_token
                if item_platform == "xiaohongshu" and item_url and "xsec_token=" not in item_url:
                    note_id = str(r["bvid"] or "")
                    if note_id:
                        with suppress(Exception):
                            item_url = pick_best_xhs_url(db, note_id, item_url)
                items.append(
                    PoolItemOut(
                        bvid=str(r["bvid"]),
                        title=str(r["title"] or ""),
                        up_name=str(r["up_name"] or ""),
                        source_platform=item_platform,
                        content_type=str(r["content_type"] or "video"),
                        cover_url=str(r["cover_url"] or ""),
                        content_url=item_url,
                        body_text=str(r["body_text"] or ""),
                        pool_status=str(r["pool_status"] or ""),
                        quality_score=float(r["quality_score"] or 0.0),
                        quality_reason=str(r["quality_reason"] or ""),
                        topic_group=str(r["topic_group"] or ""),
                        pool_expression=str(r["pool_expression"] or ""),
                    )
                )
            return PoolAllResponse(
                items=items, total=total, available=available, raw=raw, pending=pending
            )
        except Exception:
            logger.exception("pool_all failed")
            return PoolAllResponse(items=[], total=0, available=0, raw=0, pending=0)

    @router.post("/api/user-feedback")
    async def post_user_feedback(
        bvid: str = Body(..., embed=True),
        action: str = Body(..., embed=True),
        source_platform: str = Body("", embed=True),
        title: str = Body("", embed=True),
        topic_group: str = Body("", embed=True),
        body_text: str = Body("", embed=True),
    ) -> dict[str, Any]:
        """Record a like or dislike for a content item."""
        db = getattr(ctx, "database", None)
        if db is None:
            return {"ok": False, "action": action, "bvid": bvid}
        try:
            ok = db.insert_user_feedback(
                bvid,
                action,
                source_platform=source_platform,
                title=title,
                topic_group=topic_group,
                body_text=body_text,
            )
            # After successful feedback insertion, sync interest to soul_profile
            if ok:
                try:
                    profile_path = os.path.join(ctx.config.data_dir, "memory", "soul_profile.json")
                    if os.path.exists(profile_path):
                        InterestSyncer.sync(db, profile_path)
                except Exception:
                    # Don't fail the request if sync fails
                    pass
            return {"ok": ok, "action": action, "bvid": bvid}
        except Exception:
            return {"ok": False, "action": action, "bvid": bvid}

    @router.delete("/api/user-feedback")
    async def delete_user_feedback(
        bvid: str = Query(min_length=1),
        action: str = Query(pattern=r"^(like|dislike)$"),
    ) -> dict[str, Any]:
        """Remove a feedback action for a content item."""
        db = getattr(ctx, "database", None)
        if db is None:
            return {"ok": False, "action": action, "bvid": bvid}
        try:
            ok = db.remove_user_feedback(bvid, action)
            return {"ok": ok, "action": action, "bvid": bvid}
        except Exception:
            return {"ok": False, "action": action, "bvid": bvid}

    @router.get("/api/user-feedback/batch")
    async def get_user_feedback_batch(
        bvids: str = Query(description="Comma-separated bvid list"),
    ) -> dict[str, Any]:
        """Get feedback status for a batch of bvids."""
        db = getattr(ctx, "database", None)
        if db is None:
            return {}
        bvid_list = [b.strip() for b in bvids.split(",") if b.strip()]
        if not bvid_list:
            return {}
        try:
            return cast("dict[str, Any]", db.get_user_feedback_batch(bvid_list))
        except Exception:
            return {}

    @router.get("/api/interest-tags", response_model=InterestTagsResponse)
    async def get_interest_tags(
        limit: int = Query(default=20, ge=1, le=50),
    ) -> InterestTagsResponse:
        """Get aggregated interest tags from liked content."""
        db = getattr(ctx, "database", None)
        if db is None:
            return InterestTagsResponse(tags=[])
        try:
            tags = db.get_interest_tags(limit=limit)
            return InterestTagsResponse(tags=[InterestTagOut(**t) for t in tags])
        except Exception:
            return InterestTagsResponse(tags=[])

    @router.get("/api/view-history")
    async def get_view_history(
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[ViewHistoryOut]:
        """Get recent view history."""
        db = getattr(ctx, "database", None)
        if db is None:
            return []
        try:
            rows = db.get_recent_views(limit=limit)
            return [ViewHistoryOut(**r) for r in rows]
        except Exception:
            logger.exception("view-history failed")
            return []

    @router.post("/api/view-record")
    async def record_view(payload: ViewRecordIn) -> dict[str, Any]:
        """Record a content view (implicit feedback)."""
        db = getattr(ctx, "database", None)
        if db is None:
            return {"ok": False}
        try:
            db.insert_view_history(payload.model_dump())
            return {"ok": True}
        except Exception:
            logger.exception("view-record failed")
            return {"ok": False}

    @router.post("/api/view-dwell")
    async def report_view_dwell(payload: ViewDwellIn) -> dict[str, Any]:
        """Attach dwell seconds to the latest view of a bvid (implicit feedback)."""
        db = getattr(ctx, "database", None)
        if db is None:
            return {"ok": False}
        try:
            updated = db.update_view_dwell(payload.bvid, payload.dwell_seconds)
            return {"ok": updated}
        except Exception:
            logger.exception("view-dwell failed")
            return {"ok": False}

    @router.get("/api/agent-recommend")
    async def agent_recommend(
        q: str = Query(default="", description="Natural language query, e.g. 'AI 创业 播客'."),
        limit: int = Query(default=20, ge=1, le=100, description="Max items to return."),
        shuffle: bool = Query(default=True, description="Randomize the result order."),
        session_id: str | None = Query(
            default=None, description="Session ID for multi-turn context continuation."
        ),
    ) -> PoolAllResponse:
        """对话式推荐入口。

        Accept a natural language query, extract keywords, and search across
        content_cache (title, body_text, topic_group, up_name, source_platform).
        """
        db = getattr(ctx, "database", None)
        if db is None:
            return PoolAllResponse(items=[], total=0, available=0, raw=0, pending=0)

        q = (q or "").strip()
        if not q:
            return PoolAllResponse(items=[], total=0, available=0, raw=0, pending=0)

        try:
            loop = asyncio.get_running_loop()

            # --- 1. keyword extraction (LLM-enhanced) ---
            import re

            keywords = []
            platform_filter = None
            content_type_filter = None

            # Try LLM-based intent extraction if available
            soul_engine = getattr(ctx, "soul_engine", None)
            llm_available = (
                soul_engine is not None
                and hasattr(soul_engine, "llm_ask")
                and callable(soul_engine.llm_ask)
            )
            llm_used = False

            if llm_available and len(q) >= 3:
                try:
                    sys_prompt = """You are a query intent parser. Given a user's natural language query, extract:
1. keywords: the search keywords (list of strings, split compound terms if useful)
2. platform: the target platform (null if unspecified). Valid values: bilibili, zhihu, xiaohongshu, youtube, v2ex, xiaoyuzhou
3. content_type: the content type (null if unspecified). Valid values: video, podcast, article

Respond ONLY with valid JSON: {"keywords": [...], "platform": null, "content_type": null}
Keep keywords focused and specific. Remove stop words."""
                    llm_result = await cast("Any", soul_engine).llm_ask(sys_prompt, q)
                    if llm_result:
                        parsed = json.loads(llm_result)
                        kw = parsed.get("keywords", [])
                        if kw:
                            keywords = [str(k).strip() for k in kw if str(k).strip()]
                            platform_name = parsed.get("platform")
                            if platform_name and platform_name.lower() in {
                                "bilibili",
                                "zhihu",
                                "xiaohongshu",
                                "youtube",
                                "v2ex",
                                "xiaoyuzhou",
                            }:
                                platform_filter = platform_name.lower()
                            ct_name = parsed.get("content_type")
                            if ct_name and ct_name.lower() in {"video", "podcast", "article"}:
                                content_type_filter = ct_name.lower()
                            llm_used = True
                except Exception:
                    pass

            if not llm_used:
                # Fallback to keyword-based extraction
                raw_tokens = re.split(r"[,，、\s;；]+", q)
                keywords = [t.strip() for t in raw_tokens if len(t.strip()) >= 1]

                platform_map = {
                    "b站": "bilibili",
                    "bilibili": "bilibili",
                    "哔哩哔哩": "bilibili",
                    "知乎": "zhihu",
                    "zhihu": "zhihu",
                    "小红书": "xiaohongshu",
                    "xhs": "xiaohongshu",
                    "xiaohongshu": "xiaohongshu",
                    "youtube": "youtube",
                    "油管": "youtube",
                    "v2ex": "v2ex",
                    "小宇宙": "xiaoyuzhou",
                    "播客": "xiaoyuzhou",
                    "podcast": "xiaoyuzhou",
                }
                content_type_map = {
                    "视频": "video",
                    "video": "video",
                    "播客": "podcast",
                    "podcast": "podcast",
                    "音频": "podcast",
                    "文章": "article",
                    "article": "article",
                }

                remaining_keywords = []
                for kw in keywords:
                    kw_lower = kw.lower().strip()
                    if kw_lower in platform_map:
                        platform_filter = platform_map[kw_lower]
                    elif kw_lower in content_type_map:
                        content_type_filter = content_type_map[kw_lower]
                    else:
                        remaining_keywords.append(kw)
                keywords = remaining_keywords or keywords

            if not keywords:
                return PoolAllResponse(items=[], total=0, available=0, raw=0, pending=0)

            # --- 2. IntentAgent: parse exclude filters from original query ---
            intent = IntentAgent.parse(q)
            if session_id and session_id in _agent_session_cache:
                intent = IntentAgent.merge_session_context(intent, _agent_session_cache[session_id])

            exclude_platforms = list(intent["exclude_platforms"])
            exclude_content_types = list(intent["exclude_content_types"])
            exclude_keywords = list(intent["exclude_keywords"])

            # --- 3. build WHERE clauses ---
            where_clauses = [
                "pool_status IS NOT NULL",
                "COALESCE(pool_status, '') != ''",
                "COALESCE(pool_status, '') != 'purged_by_dislike'",
            ]
            params: list[Any] = []

            if platform_filter:
                where_clauses.append("source_platform = ?")
                params.append(platform_filter)

            if content_type_filter:
                where_clauses.append("content_type = ?")
                params.append(content_type_filter)

            # Exclusion filters
            for ep in exclude_platforms:
                where_clauses.append("source_platform != ?")
                params.append(ep)
            for ect in exclude_content_types:
                where_clauses.append("content_type != ?")
                params.append(ect)

            # LIKE search across multiple fields
            like_parts: list[str] = []
            for kw in keywords:
                kw_escaped = kw.replace("!", "!!").replace("%", "!%").replace("_", "!_")
                like_pattern = f"%{kw_escaped}%"
                like_parts.append(
                    "(title LIKE ? ESCAPE '!' "
                    "OR body_text LIKE ? ESCAPE '!' "
                    "OR topic_group LIKE ? ESCAPE '!' "
                    "OR up_name LIKE ? ESCAPE '!' "
                    "OR source_platform LIKE ? ESCAPE '!')"
                )
                for _ in range(5):
                    params.append(like_pattern)

            if like_parts:
                where_clauses.append(f"({' OR '.join(like_parts)})")

            # --- 5. count & fetch ---
            count_sql = (
                f"SELECT COUNT(*) AS cnt FROM content_cache WHERE {' AND '.join(where_clauses)}"
            )
            total_row = await loop.run_in_executor(
                None, lambda: db.conn.execute(count_sql, params).fetchall()
            )
            total = int(total_row[0]["cnt"]) if total_row else 0

            fetch_limit = min(limit * 3, 200)  # fetch more for sorting + diversity
            sql = f"""
                SELECT bvid, title, up_name, source_platform, content_type,
                       cover_url, content_url, body_text, description, pool_status, quality_score, quality_reason,
                       topic_group, pool_expression
                FROM content_cache
                WHERE {" AND ".join(where_clauses)}
                ORDER BY quality_score DESC
                LIMIT ?
            """
            params.append(fetch_limit)
            rows = await loop.run_in_executor(None, lambda: db.conn.execute(sql, params).fetchall())

            if not rows:
                return PoolAllResponse(items=[], total=0, available=0, raw=0, pending=0)

            # Implicit feedback: exclude content viewed in the last 7 days,
            # but only when enough candidates remain (avoid empty results).
            try:
                viewed_bvids = await loop.run_in_executor(None, lambda: db.get_viewed_bvids(days=7))
            except Exception:
                viewed_bvids = set()
            if viewed_bvids and len(rows) > limit:
                unviewed = [r for r in rows if str(r["bvid"]) not in viewed_bvids]
                if len(unviewed) >= max(1, limit // 2):
                    rows = unviewed

            # --- 6. RankAgent: score, semantic re-rank, diversity mix ---
            profile_keywords = load_interest_keywords()

            # Semantic search: embed only the query (single, cache-backed
            # await). Content vectors are read from the MMR prewarm cache via
            # lookup_cached using the canonical key — never an API round-trip
            # on this hot path. All synchronous work (cache reads + RankAgent's
            # DB aggregation queries) runs in a worker thread so it does not
            # block the event loop.
            emb_service = getattr(soul_engine, "_embedding_service", None) if soul_engine else None
            q_embed = None
            if emb_service is not None:
                try:
                    q_embed = await emb_service.embed(q)
                except Exception:
                    q_embed = None

            def _rank_offloop() -> dict[str, Any]:
                content_embeds: dict[str, list[float]] = {}
                if emb_service is not None and q_embed and any(q_embed):
                    from openbiliclaw.llm.embedding import mmr_cache_text

                    lookup = getattr(emb_service, "lookup_cached", None)
                    if callable(lookup):
                        for r in rows:  # cache-only: no API, cheap even for full pool
                            ctext = mmr_cache_text(
                                str(r["title"] or ""), str(r["description"] or "")
                            )
                            if not ctext:
                                continue
                            vec = lookup(ctext)
                            if vec and any(vec):
                                content_embeds[str(r["bvid"] or "")] = vec
                try:
                    interest_centroids: dict[str, list[float]] = (
                        RankAgent.compute_interest_centroids(db, emb_service)
                        if emb_service is not None
                        else {}
                    )
                except Exception:
                    interest_centroids = {}
                return RankAgent.score_and_rank(
                    rows=rows,
                    intent=intent,
                    db=db,
                    profile_keywords=profile_keywords,
                    q_embed=q_embed,
                    content_embeds=content_embeds,
                    interest_centroids=interest_centroids,
                )

            rank_result = await loop.run_in_executor(None, _rank_offloop)
            alpha = rank_result.get("alpha", 0.0)
            high_fit = rank_result["high_fit"]
            low_fit = rank_result["low_fit"]

            # --- 7. diversity mix: 80% high-fit, 20% exploration ---
            final_items: list[PoolItemOut] = []
            high_count = min(len(high_fit), int(limit * 0.8))
            low_count = min(len(low_fit), limit - high_count)
            # Ensure at least 1 exploration item if available
            if low_count == 0 and low_fit and len(high_fit) >= limit:
                high_count = limit - 1
                low_count = 1

            selected = high_fit[:high_count] + low_fit[:low_count]
            random.shuffle(selected)  # final shuffle for presentation

            for s in selected:
                r = s["row"]
                item_url = str(r["content_url"] or "")
                item_platform = str(r["source_platform"] or "")
                is_xhs_url = "xiaohongshu.com/explore/" in item_url
                if (
                    item_url
                    and "xsec_token=" not in item_url
                    and (item_platform == "xiaohongshu" or is_xhs_url)
                ):
                    note_id = str(r["bvid"] or "")
                    if note_id:
                        with suppress(Exception):
                            item_url = pick_best_xhs_url(db, note_id, item_url)
                final_items.append(
                    PoolItemOut(
                        bvid=str(r["bvid"]),
                        title=str(r["title"] or ""),
                        up_name=str(r["up_name"] or ""),
                        source_platform=item_platform,
                        content_type=str(r["content_type"] or "video"),
                        cover_url=str(r["cover_url"] or ""),
                        content_url=item_url,
                        body_text=str(r["body_text"] or ""),
                        pool_status=str(r["pool_status"] or ""),
                        quality_score=float(r["quality_score"] or 0.0),
                        fit_score=float(s["fit_score"] or 0.0),
                        quality_reason=str(r["quality_reason"] or ""),
                        topic_group=str(r["topic_group"] or ""),
                        pool_expression=str(r["pool_expression"] or ""),
                    )
                )
            # Build session context with RankAgent
            intent["keywords"] = keywords
            session_context = RankAgent.build_context_text(
                intent, alpha, rank_result.get("beta", 0.0)
            )
            if session_id:
                _agent_session_cache[session_id] = {
                    "exclude_platforms": list(exclude_platforms),
                    "exclude_content_types": list(exclude_content_types),
                    "exclude_keywords": list(exclude_keywords),
                    "keywords": list(keywords),
                    "platform_filter": platform_filter,
                    "content_type_filter": content_type_filter,
                    "session_context": session_context,
                }
            return PoolAllResponse(
                items=final_items,
                total=total,
                available=total,
                raw=0,
                pending=0,
                session_context=session_context,
            )
        except Exception:
            logger.exception("agent-recommend failed (q=%r)", q)
            return PoolAllResponse(items=[], total=0, available=0, raw=0, pending=0)

    return router
