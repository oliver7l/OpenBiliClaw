"""Saved-sync API routes for cross-platform saved-item management.

Adds endpoints for the library (稍后再看 / 收藏 / 历史记录) frontend views.
Registered from ``app.py`` during ``create_app()``.
"""

import time
import unicodedata
from contextlib import suppress
from typing import Any, cast
from uuid import UUID

from fastapi import HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from openbiliclaw.api.models import (
    FavoriteAddIn,
    FavoriteItem,
    FavoriteListResponse,
    FavoriteStateResponse,
    WatchLaterAddIn,
    WatchLaterItem,
    WatchLaterListResponse,
    WatchLaterStateResponse,
)
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
        # v0.4.0+: articles 表迁移到 content.db
        content_conn = getattr(db, "_content_conn", None) or db.conn
        rows = content_conn.execute(
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

    # ── Watch-later (稍后再看) / Favorites (收藏夹) ────────────────
    # 2026-09-15 起**正本 = saved_memberships**（主库），content.db 的
    # legacy favorites/watch_later 表已冻结（历史数据经
    # scripts/migrate_legacy_lists_to_saved.py 迁入）。端点 URL 与响应
    # 形状保持不变（浏览器扩展 popup 的契约）。

    def _bvid_item_key(bvid: str) -> str:
        from openbiliclaw.saved_sync.identity import make_item_key

        return make_item_key("bilibili", bvid)

    def _saved_list_kind(bvid_kind: str) -> SavedListKind:
        return "watch_later" if bvid_kind == "watch_later" else "favorite"

    def _metadata_from_cache(bvid: str) -> dict[str, str]:
        """bvid → 内容元数据（pool.content_cache；取不到给空默认值）。"""
        row = ctx.database.conn.execute(
            """
            SELECT COALESCE(source_platform, '') AS source_platform,
                   COALESCE(content_url, '')     AS content_url,
                   COALESCE(title, '')           AS title,
                   COALESCE(up_name, '')         AS author_name,
                   COALESCE(cover_url, '')       AS cover_url
              FROM pool.content_cache WHERE bvid = ? LIMIT 1
            """,
            (bvid,),
        ).fetchone()
        if row is None:
            return {
                "source_platform": "bilibili",
                "content_url": "",
                "title": "",
                "author_name": "",
                "cover_url": "",
            }
        return {
            "source_platform": str(row["source_platform"]) or "bilibili",
            "content_url": str(row["content_url"]),
            "title": str(row["title"]),
            "author_name": str(row["author_name"]),
            "cover_url": str(row["cover_url"]),
        }

    def _bvid_list_state(list_kind: SavedListKind, bvid: str) -> WatchLaterStateResponse:
        saved = (
            ctx.database.get_saved_membership(list_kind, _bvid_item_key(bvid)) is not None
        )
        return WatchLaterStateResponse(
            saved=saved,
            total=ctx.database.count_saved_memberships(list_kind),
        )

    def _bvid_list_rows(
        list_kind: SavedListKind, limit: int, offset: int
    ) -> tuple[list[WatchLaterItem], int]:
        rows = list(
            ctx.database.list_saved_memberships(list_kind, limit=limit, offset=offset)
        )
        items = [
            WatchLaterItem(
                bvid=str(row.get("content_id", "")),
                title=str(row.get("title", "")),
                up_name=str(row.get("author_name", "")),
                cover_url=str(row.get("cover_url", "")),
                content_url=str(row.get("content_url", "")),
                source_platform=str(row.get("source_platform", "") or "bilibili"),
                added_at=str(row.get("added_at", "")),
            )
            for row in rows
        ]
        return items, ctx.database.count_saved_memberships(list_kind)

    def _bvid_list_add(
        list_kind: SavedListKind, bvid: str, note: str
    ) -> WatchLaterStateResponse:
        from openbiliclaw.saved_sync.models import SavedItemInput

        meta = _metadata_from_cache(bvid)
        item = SavedItemInput(
            source_platform=meta["source_platform"],
            content_id=bvid,
            content_url=meta["content_url"],
            title=meta["title"],
            author_name=meta["author_name"],
            cover_url=meta["cover_url"],
        )
        saved_sync_cfg = getattr(getattr(ctx, "config", None), "saved_sync", None)
        auto_sync = bool(getattr(saved_sync_cfg, "auto_sync_enabled", False))
        result = _saved_service(ctx).save_local(
            list_kind, item, note=note, auto_sync=auto_sync
        )
        _saved_state_snapshot_cache.pop((list_kind, result.item_key), None)
        return _bvid_list_state(list_kind, bvid)

    def _bvid_list_remove(list_kind: SavedListKind, bvid: str) -> WatchLaterStateResponse:
        item_key = _bvid_item_key(bvid)
        ctx.database.remove_saved_membership(list_kind, item_key)
        _saved_state_snapshot_cache.pop((list_kind, item_key), None)
        return _bvid_list_state(list_kind, bvid)

    @app.post("/api/watch-later", response_model=WatchLaterStateResponse)
    async def watch_later_add(payload: WatchLaterAddIn) -> WatchLaterStateResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required")
        return _bvid_list_add("watch_later", bvid, payload.note.strip())

    @app.delete("/api/watch-later/{bvid}", response_model=WatchLaterStateResponse)
    async def watch_later_remove(bvid: str) -> WatchLaterStateResponse:
        return _bvid_list_remove("watch_later", bvid.strip())

    @app.get("/api/watch-later/{bvid}", response_model=WatchLaterStateResponse)
    async def watch_later_status(bvid: str) -> WatchLaterStateResponse:
        return _bvid_list_state("watch_later", bvid.strip())

    @app.get("/api/watch-later", response_model=WatchLaterListResponse)
    async def watch_later_list(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> WatchLaterListResponse:
        items, total = _bvid_list_rows("watch_later", limit, offset)
        return WatchLaterListResponse(items=items, total=total)

    # ── Favorites (收藏夹) ────────────────────────────────────────

    @app.post("/api/favorites", response_model=FavoriteStateResponse)
    async def favorite_add(payload: FavoriteAddIn) -> FavoriteStateResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required")
        state = _bvid_list_add("favorite", bvid, payload.note.strip())
        return FavoriteStateResponse(saved=state.saved, total=state.total)

    @app.delete("/api/favorites/{bvid}", response_model=FavoriteStateResponse)
    async def favorite_remove(bvid: str) -> FavoriteStateResponse:
        state = _bvid_list_remove("favorite", bvid.strip())
        return FavoriteStateResponse(saved=state.saved, total=state.total)

    @app.get("/api/favorites/{bvid}", response_model=FavoriteStateResponse)
    async def favorite_status(bvid: str) -> FavoriteStateResponse:
        state = _bvid_list_state("favorite", bvid.strip())
        return FavoriteStateResponse(saved=state.saved, total=state.total)

    @app.get("/api/saved-status")
    async def saved_status_bulk(bvids: str = Query(...)) -> dict[str, dict[str, bool]]:
        """批量查询收藏 / 稍后看状态（v0.3.193）。正本 = saved_memberships。"""
        raw = [b.strip() for b in bvids.split(",") if b.strip()]
        raw = list(dict.fromkeys(raw))[:500]
        if not raw:
            return {}
        fav_set: set[str] = set()
        wl_set: set[str] = set()
        placeholders = ",".join("?" * len(raw))
        with suppress(Exception):
            for r in ctx.database.conn.execute(
                f"""
                SELECT si.content_id AS bvid, m.list_kind
                  FROM saved_memberships m
                  JOIN saved_items si ON si.item_key = m.item_key
                 WHERE m.list_kind IN ('favorite', 'watch_later')
                   AND si.content_id IN ({placeholders})
                """,
                raw,
            ).fetchall():
                if str(r["list_kind"]) == "favorite":
                    fav_set.add(str(r["bvid"]))
                else:
                    wl_set.add(str(r["bvid"]))
        return {b: {"saved": b in fav_set, "watch_later": b in wl_set} for b in raw}

    @app.get("/api/favorites", response_model=FavoriteListResponse)
    async def favorite_list(
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> FavoriteListResponse:
        items, total = _bvid_list_rows("favorite", limit, offset)
        return FavoriteListResponse(
            items=[
                FavoriteItem(
                    bvid=i.bvid,
                    title=i.title,
                    up_name=i.up_name,
                    cover_url=i.cover_url,
                    content_url=i.content_url,
                    source_platform=i.source_platform,
                    added_at=i.added_at,
                )
                for i in items
            ],
            total=total,
        )
