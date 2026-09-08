"""Activity feed API routes."""

from __future__ import annotations

import asyncio
from typing import Any

from openbiliclaw.api.models import ActivityFeedItemOut, ActivityFeedResponse
from openbiliclaw.api.runtime_context import RuntimeContext


def register_activity_feed_routes(app: Any, ctx: RuntimeContext) -> None:
    """Register activity feed endpoints on the FastAPI app."""

    @app.get("/api/activity-feed", response_model=ActivityFeedResponse)
    async def activity_feed(
        limit: int = 10,
        before: str = "",
    ) -> ActivityFeedResponse:
        from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder

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
        return ActivityFeedResponse(
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
