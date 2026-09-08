"""Clone (网站克隆) API routes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.responses import JSONResponse


def register_clone_routes(app: Any, ctx: Any) -> None:
    """Register clone system endpoints on the FastAPI app."""

    _clone_service: Any | None = None

    def _get_clone_service() -> Any | None:
        """获取或创建克隆服务实例（懒加载）。"""
        nonlocal _clone_service
        if _clone_service is not None:
            return _clone_service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        from openbiliclaw.clone import CloneService as _CloneService
        from openbiliclaw.clone.store import CloneStore as _CloneStore

        _web_dir = Path(__file__).resolve().parent.parent / "web"
        _sites_dir = _web_dir / "clone" / "sites"
        store = _CloneStore(database=database)
        _clone_service = _CloneService(store=store, sites_dir=_sites_dir)
        return _clone_service

    @app.get("/api/clone/sites")
    def clone_list(
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> JSONResponse:
        """列出克隆站点，支持筛选和搜索。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        sites, total = svc.list_sites(
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            category=category,
            status=status,
            tag=tag,
            search=search,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [s.model_dump(mode="json") for s in sites],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/clone/stats")
    def clone_stats() -> JSONResponse:
        """获取克隆系统统计信息。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = svc.get_stats()
        return JSONResponse({"ok": True, "stats": stats.model_dump(mode="json")})

    @app.get("/api/clone/sites/{site_id:int}")
    def clone_get(site_id: int) -> JSONResponse:
        """获取单个克隆站点详情。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        site = svc.get_site(site_id)
        if site is None:
            return JSONResponse({"ok": False, "error": "site not found"}, status_code=404)
        return JSONResponse({"ok": True, "site": site.model_dump(mode="json")})

    @app.post("/api/clone/sites")
    def clone_create(payload: dict[str, Any]) -> JSONResponse:
        """创建新克隆站点记录。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            from openbiliclaw.clone import CloneSiteCreate, CloneStatus

            status = (
                CloneStatus(payload.get("status", "cloned"))
                if payload.get("status")
                else CloneStatus.CLONED
            )
            data = CloneSiteCreate(
                name=payload["name"],
                slug=payload.get("slug", ""),
                source_url=payload.get("source_url", ""),
                local_path=payload.get("local_path", ""),
                description=payload.get("description", ""),
                category=payload.get("category", "other"),
                status=status,
                tags=payload.get("tags", []),
            )
            site = svc.create_site(data)
            return JSONResponse({"ok": True, "site": site.model_dump(mode="json")}, status_code=201)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.put("/api/clone/sites/{site_id:int}")
    def clone_update(site_id: int, payload: dict[str, Any]) -> JSONResponse:
        """更新克隆站点信息。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            from openbiliclaw.clone import CloneSiteUpdate

            data = CloneSiteUpdate(
                name=payload.get("name"),
                description=payload.get("description"),
                category=payload.get("category"),
                status=payload.get("status"),
                tags=payload.get("tags"),
            )
            site = svc.update_site(site_id, data)
            if site is None:
                return JSONResponse({"ok": False, "error": "site not found"}, status_code=404)
            return JSONResponse({"ok": True, "site": site.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/clone/sites/{site_id:int}")
    def clone_delete(site_id: int) -> JSONResponse:
        """删除克隆站点记录。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = svc.delete_site(site_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "site not found"}, status_code=404)
        return JSONResponse({"ok": True})

    @app.post("/api/clone/import")
    def clone_import() -> JSONResponse:
        """扫描 clone/sites/ 目录，批量导入已有站点。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            _web_dir = Path(__file__).resolve().parent.parent / "web"
            sites_dir = _web_dir / "clone" / "sites"
            imported = svc.import_existing_sites(sites_dir)
            return JSONResponse(
                {
                    "ok": True,
                    "imported": [s.model_dump(mode="json") for s in imported],
                    "count": len(imported),
                }
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/clone/clone")
    def clone_new(payload: dict[str, Any]) -> JSONResponse:
        """克隆新网站。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            from openbiliclaw.clone import CloneRequest

            request = CloneRequest(
                url=payload["url"],
                name=payload["name"],
                category=payload.get("category", "website"),
                tags=payload.get("tags", []),
                depth=min(int(payload.get("depth", 1)), 3),
            )
            site = svc.clone_new_site(request)
            return JSONResponse({"ok": True, "site": site.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/clone/tags")
    def clone_tags() -> JSONResponse:
        """列出所有克隆站点标签。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        tags = svc.list_tags()
        return JSONResponse({"ok": True, "tags": tags})
