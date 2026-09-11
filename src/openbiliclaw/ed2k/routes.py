"""ed2k / Kad 下载管理 API 路由。

Endpoints（prefix ``/api/ed2k``）：
- GET  /api/ed2k/net      — 网络状态（Kad / ed2k 服务器连接数）
- GET  /api/ed2k/search   — 按关键词搜索 ed2k 文件（?q=&wait=35）
- POST /api/ed2k/download — 按最近一次搜索的 id 启动下载（body {ids:[int]}）
- GET  /api/ed2k/downloads— 进行中（含已完成）下载列表
- POST /api/ed2k/cancel   — 取消下载（body {ids:[int]}）
- POST /api/ed2k/commit   — 把完成的下载移入落地目录
- GET  /api/ed2k/path     — 完成文件落地目录（宿主路径）

后端通过 ``mule`` CLI 驱动 MLDonkey。搜索是慢操作（Kad 30-60s），
路由声明为 ``async def`` 并用 ``asyncio.to_thread`` 跑，避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from openbiliclaw.ed2k.service import MuleService


def _base_service(config: Any) -> MuleService:
    cfg: Any = getattr(config, "ed2k", None)
    mule_path = getattr(cfg, "mule_path", "") or ""
    download_dir = getattr(cfg, "download_dir", "") or ""
    return MuleService(mule_path=mule_path, download_dir=download_dir)


class IdsIn(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=50)


class LinkIn(BaseModel):
    link: str = Field(..., min_length=1, max_length=2048)


def build_ed2k_router(*, config: Any) -> APIRouter:
    """创建 ed2k 下载管理路由。config 提供 [ed2k] mule_path / download_dir。

    mule 未安装或容器不在时会返回友好错误，不阻塞主 API（注册处已 try/except）。
    """
    router = APIRouter(prefix="/api/ed2k", tags=["ed2k"])

    def _service() -> MuleService:
        return _base_service(config)

    async def _thread(fn: Any, *args: Any) -> Any:
        return await asyncio.to_thread(fn, *args)

    def _fail(e: Exception) -> HTTPException:
        return HTTPException(status_code=500, detail=f"ed2k 操作失败：{e}")

    @router.get("/net")
    async def net() -> dict[str, Any]:
        try:
            return await _thread(_service().net)
        except Exception as e:  # noqa: BLE001
            raise _fail(e) from e

    @router.get("/search")
    async def search(
        q: str = Query("", max_length=200),
        wait: int = Query(35, ge=5, le=90),
        limit: int = Query(25, ge=1, le=50),
    ) -> dict[str, Any]:
        if not q.strip():
            raise HTTPException(status_code=422, detail="缺少搜索关键词 q")
        try:
            return await _thread(_service().search, q.strip(), wait, limit)
        except Exception as e:  # noqa: BLE001
            raise _fail(e) from e

    @router.post("/download")
    async def download(body: IdsIn) -> dict[str, Any]:
        try:
            return await _thread(_service().download, body.ids)
        except Exception as e:  # noqa: BLE001
            raise _fail(e) from e

    @router.post("/download-link")
    async def download_link(body: LinkIn) -> dict[str, Any]:
        try:
            return await _thread(_service().download_link, body.link)
        except Exception as e:  # noqa: BLE001
            raise _fail(e) from e

    @router.get("/downloads")
    async def downloads() -> dict[str, Any]:
        try:
            return await _thread(_service().downloads)
        except Exception as e:  # noqa: BLE001
            raise _fail(e) from e

    @router.post("/cancel")
    async def cancel(body: IdsIn) -> dict[str, Any]:
        try:
            return await _thread(_service().cancel, body.ids)
        except Exception as e:  # noqa: BLE001
            raise _fail(e) from e

    @router.post("/commit")
    async def commit() -> dict[str, Any]:
        try:
            return await _thread(_service().commit)
        except Exception as e:  # noqa: BLE001
            raise _fail(e) from e

    @router.get("/path")
    async def path() -> dict[str, Any]:
        try:
            return {"path": await _thread(_service().path)}
        except Exception as e:  # noqa: BLE001
            raise _fail(e) from e

    return router