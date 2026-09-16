"""Runtime status API routes."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from fastapi import Query

from openbiliclaw.api.models import RuntimeStatusResponse

if TYPE_CHECKING:
    from openbiliclaw.api.runtime_context import RuntimeContext

#: mobile 客户端每 ~8s 轮询本端点；2s TTL 足以合并轮询突发，又不影响实时感。
_STATUS_CACHE_TTL_SECONDS = 2.0
_status_cache: dict[str, tuple[float, RuntimeStatusResponse]] = {}


def register_runtime_status_routes(app: Any, ctx: RuntimeContext) -> None:
    """Register runtime status endpoints on the FastAPI app."""

    @app.get("/api/runtime-status", response_model=RuntimeStatusResponse)
    async def runtime_status(
        refresh: bool = Query(default=False, description="跳过缓存强制刷新"),
    ) -> RuntimeStatusResponse:
        if not refresh:
            cached = _status_cache.get("status")
            if cached is not None and time.monotonic() - cached[0] < _STATUS_CACHE_TTL_SECONDS:
                return cached[1]
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
        response = RuntimeStatusResponse(**payload)
        _status_cache["status"] = (time.monotonic(), response)
        return response
