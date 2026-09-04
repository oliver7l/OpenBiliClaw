"""Saved-sync API routes for cross-platform saved-item management.

Adds endpoints for the library (稍后再看 / 收藏 / 历史记录) frontend views.
Registered from ``app.py`` during ``create_app()``.
"""

import time
import unicodedata
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.saved_sync.models import (
    NATIVE_SAVE_STATUSES,
    SavedListKind,
    SavedSyncBatchResult,
)

_RECOMMENDATION_SNAPSHOT_TTL_SECONDS = 30.0
_saved_state_snapshot_cache: dict[tuple[str, str], tuple[float, Any]] = {}


def _saved_service(ctx: RuntimeContext) -> Any:
    service = getattr(ctx, "saved_sync_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="saved sync service unavailable")
    return service


def _safe_native_status(value: object) -> str:
    if isinstance(value, str):
        for status in NATIVE_SAVE_STATUSES:
            if value == status:
                return status
    return "failed"


def _safe_result_text(value: object, *, limit: int = 512) -> str:
    if not isinstance(value, str):
        return ""
    filtered = "".join(
        character for character in value if not unicodedata.category(character).startswith("C")
    )
    return filtered[:limit]


def _saved_state_response(
    ctx: RuntimeContext,
    list_kind: SavedListKind,
    item_key: str,
) -> dict[str, Any]:
    cache_key = (list_kind, item_key)
    cached = _saved_state_snapshot_cache.get(cache_key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _RECOMMENDATION_SNAPSHOT_TTL_SECONDS:
        return cast("dict[str, Any]", cached[1])

    row = ctx.database.get_saved_membership(list_kind, item_key)
    if row is None:
        response = {"saved": False, "item_key": item_key}
    else:
        response = {
            "saved": True,
            "item_key": item_key,
            "sync_status": _safe_native_status(row.get("sync_status")),
            "sync_task_id": str(row.get("sync_task_id", "")),
            "resolved_action": str(row.get("resolved_action", "")),
            "resolved_target": _safe_result_text(row.get("resolved_target", ""), limit=256),
            "error_code": _safe_result_text(row.get("last_error_code", ""), limit=128),
            "error_message": _safe_result_text(row.get("last_error_message", "")),
        }
    if len(_saved_state_snapshot_cache) >= 1000:
        _saved_state_snapshot_cache.clear()
    _saved_state_snapshot_cache[cache_key] = (now, response)
    return response


def _saved_list_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "item_key": str(row.get("item_key", "")),
        "source_platform": str(row.get("source_platform", "")),
        "content_id": str(row.get("content_id", "")),
        "content_url": str(row.get("content_url", "")),
        "content_type": str(row.get("content_type", "") or "video"),
        "title": str(row.get("title", "")),
        "author_name": str(row.get("author_name", "")),
        "cover_url": str(row.get("cover_url", "")),
        "note": str(row.get("note", "")),
        "added_at": str(row.get("added_at", "")),
        "sync_status": _safe_native_status(row.get("sync_status")),
        "sync_task_id": str(row.get("sync_task_id", "")),
        "requested_action": str(row.get("requested_action", "")),
        "resolved_action": str(row.get("resolved_action", "")),
        "resolved_target": _safe_result_text(row.get("resolved_target", ""), limit=256),
        "error_code": _safe_result_text(row.get("last_error_code", ""), limit=128),
        "error_message": _safe_result_text(row.get("last_error_message", "")),
    }


def _sync_item_response(result: Any) -> dict[str, Any]:
    return {
        "item_key": result.item_key,
        "status": result.status,
        "resolved_action": result.resolved_action,
        "resolved_target": _safe_result_text(result.resolved_target, limit=256),
        "error_code": _safe_result_text(result.error_code, limit=128),
        "error_message": _safe_result_text(result.error_message),
    }


def _sync_batch_response(result: SavedSyncBatchResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "items": [_sync_item_response(item) for item in result.items],
    }


class UpdateTagsBody(BaseModel):
    tags: list[str]


class UpdateStatusBody(BaseModel):
    status: str


def register_saved_sync_routes(app: Any, ctx: RuntimeContext) -> None:
    """Register all saved-sync API routes on the FastAPI app."""

    # ── Add item to saved list ──────────────────────────────────────────
    @app.post("/api/saved/{list_kind}")
    async def saved_add(list_kind: SavedListKind, payload: dict[str, Any]) -> JSONResponse:
        from openbiliclaw.saved_sync.models import SavedItemInput

        item = SavedItemInput(
            source_platform=str(payload.get("source_platform", "")),
            content_id=str(payload.get("content_id", "")),
            content_url=str(payload.get("content_url", "")),
            content_type=str(payload.get("content_type", "video")),
            title=str(payload.get("title", "")),
            author_name=str(payload.get("author_name", "")),
            cover_url=str(payload.get("cover_url", "")),
        )
        saved_sync_cfg = getattr(getattr(ctx, "config", None), "saved_sync", None)
        auto_sync = bool(getattr(saved_sync_cfg, "auto_sync_enabled", False))
        try:
            result = _saved_service(ctx).save_local(
                list_kind,
                item,
                note=str(payload.get("note", "")),
                auto_sync=auto_sync,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid saved item") from exc
        _saved_state_snapshot_cache.pop((list_kind, result.item_key), None)
        response = {
            "saved": True,
            "item_key": result.item_key,
            "sync_status": result.sync_status,
            "sync_task_id": str(result.sync_task_id) if result.sync_task_id else "",
        }
        return JSONResponse(response)

    # ── Remove item from saved list ─────────────────────────────────────
    @app.post("/api/saved/{list_kind}/remove")
    async def saved_remove(list_kind: SavedListKind, payload: dict[str, Any]) -> JSONResponse:
        item_key = str(payload.get("item_key", ""))
        if not item_key:
            raise HTTPException(status_code=422, detail="item_key is required")
        ctx.database.remove_saved_membership(list_kind, item_key)
        _saved_state_snapshot_cache.pop((list_kind, item_key), None)
        return JSONResponse({"saved": False, "item_key": item_key})

    # ── List saved items ────────────────────────────────────────────────
    @app.get("/api/saved/{list_kind}")
    async def saved_list(
        list_kind: SavedListKind,
        limit: int = 20,
        offset: int = 0,
    ) -> JSONResponse:
        rows = list(ctx.database.list_saved_memberships(list_kind, limit=limit, offset=offset))
        count = ctx.database.count_saved_memberships(list_kind)
        return JSONResponse(
            {
                "items": [_saved_list_item(row) for row in rows],
                "total": count,
            }
        )

    # ── Check item saved status ─────────────────────────────────────────
    @app.get("/api/saved/{list_kind}/status")
    async def saved_status(list_kind: SavedListKind, item_key: str) -> JSONResponse:
        response = _saved_state_response(ctx, list_kind, item_key)
        return JSONResponse(response)

    # ── Sync saved items to native platform ────────────────────────────
    @app.post("/api/saved/{list_kind}/sync")
    async def saved_sync(list_kind: SavedListKind, payload: dict[str, Any]) -> JSONResponse:
        item_keys = list(payload.get("item_keys", []))
        if not item_keys:
            raise HTTPException(status_code=422, detail="item_keys is required")
        trigger = "manual_single" if len(item_keys) == 1 else "manual_batch"
        try:
            created = _saved_service(ctx).create_sync_task(list_kind, item_keys, trigger)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid sync selection") from exc
        return JSONResponse(_sync_batch_response(created))

    # ── Get sync task status ───────────────────────────────────────────
    @app.get("/api/saved-sync/tasks/{task_id}")
    async def saved_sync_task(task_id: UUID) -> JSONResponse:
        service = _saved_service(ctx)
        task_id_str = str(task_id)
        exists = service.has_sync_task(task_id_str)
        if not exists:
            raise HTTPException(status_code=404, detail="sync task not found")
        result = service.get_sync_task(task_id_str)
        return JSONResponse(_sync_batch_response(result))

    # ── Reading library items ──────────────────────────────────────────
    @app.get("/api/reading/items")
    async def get_reading_items(
        request: Request,
        limit: int = 30,
        offset: int = 0,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> JSONResponse:
        db = ctx.database
        items = db.get_recent_articles(
            limit=limit,
            offset=offset,
            source_type=source_type,
            status=status,
            tag=tag,
        )
        return JSONResponse(items)

    # ── Reading library total (for paging) ─────────────────────────────
    @app.get("/api/reading/count")
    async def get_reading_count(
        request: Request,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> JSONResponse:
        db = ctx.database
        return JSONResponse(
            {"total": db.count_articles(source_type=source_type, status=status, tag=tag)}
        )

    # ── Reading library source-type distribution (for dynamic filters) ──
    @app.get("/api/reading/sources")
    async def get_reading_sources(request: Request) -> JSONResponse:
        db = ctx.database
        rows = db.conn.execute(
            "SELECT source_type, COUNT(*) c FROM articles GROUP BY source_type ORDER BY c DESC"
        ).fetchall()
        sources = [{"source_type": r[0], "count": r[1]} for r in rows]
        return JSONResponse({"sources": sources})

    @app.put("/api/reading/items/{item_id}/tags")
    async def update_reading_item_tags(item_id: int, body: UpdateTagsBody) -> JSONResponse:
        db = ctx.database
        ok = db.update_article_tags(item_id, body.tags)
        if not ok:
            raise HTTPException(404, "Article not found")
        return JSONResponse({"ok": True})

    @app.put("/api/reading/items/{item_id}/status")
    async def update_reading_item_status(item_id: int, body: UpdateStatusBody) -> JSONResponse:
        if body.status not in ("unread", "reading", "finished"):
            raise HTTPException(422, "Invalid status value")
        db = ctx.database
        ok = db.update_article_status(item_id, body.status)
        if not ok:
            raise HTTPException(404, "Article not found")
        return JSONResponse({"ok": True})

    # ── Reading library full-text search ────────────────────────────
    @app.get("/api/reading/search")
    async def search_reading_items(
        request: Request,
        q: str = "",
        limit: int = 30,
        offset: int = 0,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> JSONResponse:
        if limit <= 0 or limit > 100:
            limit = 30
        if offset < 0:
            offset = 0
        items = ctx.database.search_articles(
            q=q,
            limit=limit,
            offset=offset,
            source_type=source_type,
            status=status,
            tag=tag,
        )
        return JSONResponse(items)

    # ── Read archive (已读库) — standalone table ──────────────────────────
    @app.get("/api/read-archive/items")
    async def get_read_archive_items(
        request: Request,
        limit: int = 24,
        offset: int = 0,
        source_type: str | None = None,
        tag: str | None = None,
    ) -> JSONResponse:
        db = ctx.database
        items = db.get_recent_readarchive(
            limit=limit,
            offset=offset,
            source_type=source_type,
            tag=tag,
        )
        return JSONResponse(items)

    @app.get("/api/read-archive/count")
    async def get_read_archive_count(
        request: Request,
        source_type: str | None = None,
        tag: str | None = None,
    ) -> JSONResponse:
        db = ctx.database
        return JSONResponse(
            {
                "total": db.count_readarchive(
                    source_type=source_type,
                    tag=tag,
                )
            }
        )

    @app.get("/api/read-archive/search")
    async def search_read_archive_items(
        request: Request,
        q: str = "",
        limit: int = 24,
        offset: int = 0,
        source_type: str | None = None,
        tag: str | None = None,
    ) -> JSONResponse:
        if limit <= 0 or limit > 100:
            limit = 24
        if offset < 0:
            offset = 0
        items = ctx.database.search_readarchive(
            q=q,
            limit=limit,
            offset=offset,
            source_type=source_type,
            tag=tag,
        )
        return JSONResponse(items)
