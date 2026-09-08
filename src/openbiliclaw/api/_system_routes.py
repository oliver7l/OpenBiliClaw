"""System-level API routes: update status, notifications, cognition updates."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from openbiliclaw.api.models import (
    BackendUpdateStatusOut,
    CognitionUpdateSeenIn,
    CognitionUpdateSeenResponse,
    PendingCognitionUpdateOut,
    PendingCognitionUpdateResponse,
    PendingNotificationOut,
    PendingNotificationResponse,
    UpdateApplyIn,
    UpdateCheckIn,
    UpdateStatusResponse,
)
from openbiliclaw.api.runtime_context import RuntimeContext


def register_system_routes(app: Any, ctx: RuntimeContext) -> None:
    """Register system-level endpoints on the FastAPI app."""

    def _backend_update_status() -> BackendUpdateStatusOut:
        get_update_status = getattr(ctx.auto_update_service, "get_update_status", None)
        if callable(get_update_status):
            status = get_update_status()
            return BackendUpdateStatusOut.model_validate(
                dict(status) if isinstance(status, dict) else {}
            )
        get_runtime_update_status = getattr(ctx.auto_update_service, "get_runtime_status", None)
        if callable(get_runtime_update_status):
            runtime_status = dict(get_runtime_update_status())
            return BackendUpdateStatusOut(
                state=str(runtime_status.get("backend_update_state", "unknown")),
                auto_update_enabled=bool(runtime_status.get("auto_update_enabled", False)),
                install_mode=str(runtime_status.get("install_mode", "")),
                current_version=str(runtime_status.get("current_version", "")),
                latest_version=str(runtime_status.get("latest_remote_version", "")),
                latest_tag=str(runtime_status.get("latest_remote_version", "")),
                last_check_at=str(runtime_status.get("last_update_check_at", "")),
                last_error=str(runtime_status.get("last_update_error", "")),
                reason=str(runtime_status.get("backend_update_reason", "none")),
            )
        return BackendUpdateStatusOut(
            state="disabled",
            auto_update_enabled=False,
            current_version="",
            latest_version="",
            latest_tag="",
            last_check_at="",
            last_error="",
            reason="none",
        )

    @app.get("/api/update-status", response_model=UpdateStatusResponse)
    async def update_status() -> UpdateStatusResponse:
        return UpdateStatusResponse(backend=_backend_update_status())

    @app.post("/api/update/check", response_model=UpdateStatusResponse)
    async def update_check(_payload: UpdateCheckIn | None = None) -> UpdateStatusResponse:
        check_now = getattr(ctx.auto_update_service, "check_now", None)
        if callable(check_now):
            backend = await check_now()
        else:
            backend = _backend_update_status()
        return UpdateStatusResponse(backend=BackendUpdateStatusOut.model_validate(backend))

    @app.post("/api/update/apply")
    async def update_apply(payload: UpdateApplyIn) -> JSONResponse:
        request_apply = getattr(ctx.auto_update_service, "request_apply", None)
        if not callable(request_apply):
            return JSONResponse(
                status_code=409,
                content={
                    "target": "backend",
                    "state": "unsupported",
                    "reason": "unsupported_install_mode",
                    "accepted": False,
                    "observe_via": "runtime-stream",
                },
            )
        status_code, body = await request_apply(tag=payload.tag)
        return JSONResponse(status_code=int(status_code), content=body)

    @app.get("/api/notifications/pending", response_model=PendingNotificationResponse)
    async def pending_notification() -> PendingNotificationResponse:
        get_pending_notification = getattr(ctx.runtime_controller, "get_pending_notification", None)
        item = get_pending_notification() if callable(get_pending_notification) else None
        if item is None:
            get_notification_candidate = getattr(ctx.database, "get_notification_candidate", None)
            if callable(get_notification_candidate):
                candidate = get_notification_candidate(min_confidence=0.82)
                if candidate is not None:
                    item = {
                        "recommendation_id": int(candidate["id"]),
                        "bvid": str(candidate.get("bvid", "")),
                        "title": str(candidate.get("title", "")),
                        "reason": str(candidate.get("expression", "")),
                    }
        if item is None:
            return PendingNotificationResponse(item=None)
        return PendingNotificationResponse(item=PendingNotificationOut(**item))

    @app.get(
        "/api/cognition-updates/pending",
        response_model=PendingCognitionUpdateResponse,
    )
    async def pending_cognition_update() -> PendingCognitionUpdateResponse:
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if not callable(load_cognition_updates):
            return PendingCognitionUpdateResponse(item=None)
        updates = [
            item
            for item in load_cognition_updates()
            if isinstance(item, dict) and not bool(item.get("notified", False))
        ]
        if not updates:
            return PendingCognitionUpdateResponse(item=None)
        latest = updates[-1]
        return PendingCognitionUpdateResponse(
            item=PendingCognitionUpdateOut(
                id=str(latest.get("id", "")),
                kind=str(latest.get("kind", "")),
                summary=str(latest.get("summary", "")),
            )
        )

    @app.post(
        "/api/cognition-updates/seen",
        response_model=CognitionUpdateSeenResponse,
    )
    async def cognition_update_seen(
        payload: CognitionUpdateSeenIn,
    ) -> CognitionUpdateSeenResponse:
        update_id = payload.id.strip()
        if not update_id:
            raise HTTPException(status_code=422, detail="Cognition update id is required.")
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        save_cognition_updates = getattr(ctx.memory_manager, "save_cognition_updates", None)
        if not callable(load_cognition_updates) or not callable(save_cognition_updates):
            raise HTTPException(status_code=500, detail="Cognition update storage unavailable.")
        updates = load_cognition_updates()
        found = False
        for item in updates:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")).strip() != update_id:
                continue
            item["notified"] = True
            found = True
            break
        if not found:
            raise HTTPException(status_code=404, detail="Cognition update not found.")
        save_cognition_updates(updates)
        return CognitionUpdateSeenResponse(ok=True, id=update_id)

    @app.get("/api/ping")
    async def ping() -> JSONResponse:
        """Pure liveness probe: no DB, no provider round-trips."""
        return JSONResponse({"status": "ok", "service": "openbiliclaw-api"})
