"""Runtime status API routes."""

from __future__ import annotations

import asyncio
from typing import Any

from openbiliclaw.api.models import RuntimeStatusResponse
from openbiliclaw.api.runtime_context import RuntimeContext


def register_runtime_status_routes(app: Any, ctx: RuntimeContext) -> None:
    """Register runtime status endpoints on the FastAPI app."""

    @app.get("/api/runtime-status", response_model=RuntimeStatusResponse)
    async def runtime_status() -> RuntimeStatusResponse:
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if not callable(get_runtime_status):
            return RuntimeStatusResponse(
                initialized=False,
                recommendation_count=0,
                pending_signal_events=0,
                unread_count=0,
            )
        payload = dict(await asyncio.get_running_loop().run_in_executor(None, get_runtime_status))
        get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            payload.update(get_account_sync_status())
        get_update_status = getattr(ctx.auto_update_service, "get_runtime_status", None)
        if callable(get_update_status):
            payload.update(get_update_status())
        return RuntimeStatusResponse(**payload)
