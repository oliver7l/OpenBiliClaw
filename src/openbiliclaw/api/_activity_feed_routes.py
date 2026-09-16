"""Activity feed API routes."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from openbiliclaw.api.models import ActivityFeedItemOut, ActivityFeedResponse

if TYPE_CHECKING:
    from openbiliclaw.api.runtime_context import RuntimeContext


def register_activity_feed_routes(app: Any, ctx: RuntimeContext) -> None:
    """Register activity feed endpoints on the FastAPI app."""

    #: mobile 客户端每 ~8s 轮询本端点；按 (limit, before) 键控的 2s TTL 缓存。
    feed_cache_ttl_seconds = 2.0
    feed_cache: dict[tuple[int, str], tuple[float, ActivityFeedResponse]] = {}

    @app.get("/api/activity-feed", response_model=ActivityFeedResponse)
    async def activity_feed(
        limit: int = 10,
        before: str = "",
        refresh: bool = False,
    ) -> ActivityFeedResponse:
        from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder

        cache_key = (limit, before)
        if not refresh:
            cached = feed_cache.get(cache_key)
            if (
                cached is not None
                and time.monotonic() - cached[0] < feed_cache_ttl_seconds
            ):
                return cached[1]

        def _collect_feed_inputs() -> dict[str, object]:
            runtime_status: dict[str, object] = {}
            get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
            if callable(get_runtime_status):
                runtime_status = dict(get_runtime_status())
            get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
            if callable(get_account_sync_status):
                runtime_status.update(get_account_sync_status())

            cognition_updates: list[dict[str, object]] = []
            load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
            if callable(load_cognition_updates):
                cognition_updates = [
                    item for item in load_cognition_updates() if isinstance(item, dict)
                ]

            builder = ActivityFeedBuilder(database=ctx.database)
            return builder.build(
                runtime_status=runtime_status,
                cognition_updates=cognition_updates,
                limit=limit,
                before=before,
            )

        payload = await asyncio.get_running_loop().run_in_executor(None, _collect_feed_inputs)
        payload_items = payload.get("items", [])
        item_dicts = payload_items if isinstance(payload_items, list) else []
        response = ActivityFeedResponse(
            live_summary=str(payload.get("live_summary", "")),
            headline=str(payload.get("headline", "")),
            items=[
                ActivityFeedItemOut(
                    id=str(item.get("id", "")),
                    kind=str(item.get("kind", "")),
                    summary=str(item.get("summary", "")),
                    detail=str(item.get("detail", "")),
                    created_at=str(item.get("created_at", "")),
                    tone=str(item.get("tone", "info")),
                )
                for item in item_dicts
                if isinstance(item, dict)
            ],
            has_more=bool(payload.get("has_more", False)),
            next_cursor=str(payload.get("next_cursor", "")),
        )
        feed_cache[cache_key] = (time.monotonic(), response)
        return response
