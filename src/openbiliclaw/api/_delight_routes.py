"""Delight (surprise recommendation) API routes."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from openbiliclaw.api.models import (
    DelightAckIn,
    DelightAckResponse,
    PendingDelightOut,
    PendingDelightResponse,
)
from openbiliclaw.api.runtime_context import RuntimeContext


def register_delight_routes(app: Any, ctx: RuntimeContext) -> None:
    """Register delight endpoints on the FastAPI app."""

    @app.get("/api/delight/pending", response_model=PendingDelightResponse)
    async def pending_delight() -> PendingDelightResponse:
        get_pending_delight = getattr(ctx.runtime_controller, "get_pending_delight", None)
        item = get_pending_delight() if callable(get_pending_delight) else None
        if item is None:
            return PendingDelightResponse(item=None)
        return PendingDelightResponse(item=PendingDelightOut(**item))

    @app.get("/api/delight/pending-batch")
    async def pending_delight_batch(limit: int | None = None) -> dict[str, Any]:
        """Return un-notified delight candidates."""
        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        configured_limit = getattr(
            getattr(getattr(ctx, "config", None), "scheduler", None),
            "delight_queue_limit",
            20,
        )
        requested_limit = configured_limit if limit is None else limit
        rows = ctx.database.get_delight_candidates(
            min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            limit=max(1, min(100, int(requested_limit))),
            include_liked=True,
        )
        controller = ctx.runtime_controller
        load_phrases = getattr(controller, "_load_disliked_topic_phrases", None)
        disliked_phrases = load_phrases() if callable(load_phrases) else []

        def passes_filter(row: dict[str, Any]) -> bool:
            haystack = f"{str(row.get('title', '')).lower()} {str(row.get('tags', '')).lower()}"
            return not any(p and p in haystack for p in disliked_phrases)

        items = [
            {
                "bvid": str(row.get("bvid", "")),
                "title": str(row.get("title", "")),
                "delight_reason": str(row.get("delight_reason", "")),
                "delight_score": float(row.get("delight_score", 0.0) or 0.0),
                "delight_hook": str(row.get("delight_hook", "")),
                "cover_url": str(row.get("cover_url", "")),
                "content_url": str(row.get("content_url", "")),
                "source_platform": str(row.get("source_platform", "bilibili")),
                "state": (
                    "liked" if str(row.get("feedback_type", "") or "") == "like" else "pending"
                ),
            }
            for row in rows
            if passes_filter(row)
        ]
        return {"items": items}

    @app.post("/api/delight/sent", response_model=DelightAckResponse)
    async def mark_delight_sent(payload: DelightAckIn) -> DelightAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Delight bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_delight_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_delight_notified(bvid)
        return DelightAckResponse(ok=True, bvid=bvid)
