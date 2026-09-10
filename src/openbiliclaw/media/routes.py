"""媒体浏览 API 路由。

Endpoints（prefix ``/api/media``）：
- GET  /api/media/roots   — 已配置根目录元信息（存在性 + 媒体数量）
- POST /api/media/roots   — 追加一个根目录并持久化到 ``[media] roots``
- GET  /api/media/list    — 分页列出媒体（?root=&kind=&q=&sub=&offset=&limit=）
- GET  /api/media/file    — 流式返回单文件（?root=&path=，支持 HTTP Range 拖动播放）

根目录来自 ``config.media.roots``；``/api/media/file`` 仅允许访问已配置
根目录内的文件，杜绝目录穿越。
"""

from __future__ import annotations

import hashlib
import logging
import mimetypes
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from openbiliclaw.media.service import MediaNotFoundError, MediaService

if TYPE_CHECKING:
    from openbiliclaw.config import Config

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 60


class AddRootIn(BaseModel):
    path: str = Field(..., min_length=1, max_length=1024)


class ItemStateIn(BaseModel):
    root: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1, max_length=1024)
    favorite: int | None = Field(None, ge=0, le=1)
    rating: int | None = Field(None, ge=0, le=5)


def _favorite_file_exists(item: dict[str, object]) -> bool:
    """校验收藏条目对应的文件仍存在（根目录/相对路径拼接）。"""
    root = str(item.get("root") or "")
    rel = str(item.get("rel") or "")
    if not root or not rel:
        return False
    return Path(root, rel).is_file()


# 视频封面（抽帧缩略图）相关：用 ffmpeg 抽第一帧并缓存，避免大文件反复解码
_FFMPEG_CANDIDATES = (
    "/opt/homebrew/bin/ffmpeg",
    "/usr/local/bin/ffmpeg",
    "/usr/bin/ffmpeg",
)


def _ffmpeg_exe() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    for candidate in _FFMPEG_CANDIDATES:
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def _poster_cache_dir(config: Any) -> Path:
    base = getattr(config, "data_path", None)
    if not base:
        base = Path("data")
    return Path(base) / "media_thumbs"


def _poster_key_for(file_path: Path) -> str | None:
    """封面缓存的哈希 key（随路径/mtime/size 变化）。"""
    try:
        st = file_path.stat()
    except OSError:
        return None
    return hashlib.md5(
        f"{file_path}|{st.st_mtime_ns}|{st.st_size}".encode("utf-8", "ignore")
    ).hexdigest()


def _ensure_poster(config: Any, file_path: Path) -> Path | None:
    """为视频生成/返回缓存封面（首页帧 JPEG）。失败或缺少 ffmpeg 返回 None。"""
    exe = _ffmpeg_exe()
    if exe is None:
        return None
    key = _poster_key_for(file_path)
    if key is None:
        return None
    cache_dir = _poster_cache_dir(config)
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    target = cache_dir / f"{key}.jpg"
    if target.is_file() and target.stat().st_size > 0:
        return target
    fd, tmp_path = tempfile.mkstemp(suffix=".jpg", prefix=".poster-", dir=cache_dir)
    os.close(fd)
    tmp = Path(tmp_path)
    try:
        proc = subprocess.run(
            [
                exe,
                "-y",
                "-loglevel", "error",
                "-ss", "00:00:01",
                "-i", str(file_path),
                "-frames:v", "1",
                "-vf", "scale=640:-1",
                str(tmp),
            ],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode != 0 or not (tmp.is_file() and tmp.stat().st_size > 0):
            tmp.unlink(missing_ok=True)
            return None
        os.replace(tmp, target)
        return target
    except Exception:  # noqa: BLE001
        tmp.unlink(missing_ok=True)
        logger.exception("生成视频封面失败: %s", file_path)
        return None


def build_media_router(
    *,
    config: Any,
    config_save_lock: Any = None,
    config_path: str | Path | None = None,
) -> APIRouter:
    """创建媒体浏览路由。config 提供根目录；config_save_lock 用于持久化。

    根目录通过闭包传给 MediaService；每次调用前用 ``config.media.roots``
    刷新，页面内「添加目录」写入后即可立刻在 /api/media/roots 看到。
    ``config_path`` 覆盖持久化目标文件（默认项目 config.toml），便于测试。
    """
    from openbiliclaw.media.store import MediaStateStore

    router = APIRouter(prefix="/api/media", tags=["media"])

    data_dir = getattr(config, "data_path", None)
    data_dir = data_dir if data_dir else Path("data")
    store = MediaStateStore(Path(data_dir) / "media_state.db")

    def _service() -> MediaService:
        cfg: Any = config
        roots = list(getattr(getattr(cfg, "media", None), "roots", None) or [])
        return MediaService(roots)

    def _not_found(detail: str) -> HTTPException:
        return HTTPException(status_code=404, detail=detail)

    @router.get("/roots")
    def roots_meta() -> dict[str, Any]:
        return {"roots": _service().roots_meta()}

    @router.post("/roots")
    def add_root(payload: AddRootIn) -> dict[str, Any]:
        cfg: Any = config
        path = payload.path.strip()
        if not path:
            raise HTTPException(status_code=400, detail="路径不能为空")
        media_cfg = getattr(cfg, "media", None)
        existing = [r.strip().rstrip("/\\") for r in (list(getattr(media_cfg, "roots", []) or []))]
        candidate = Path(path).resolve()
        target = str(candidate).rstrip("/\\")
        if target in existing:
            return {"roots": _service().roots_meta(), "added": False}
        existing.append(target)
        media_cfg.roots = existing
        if config_save_lock is not None:
            with config_save_lock:
                _persist(cfg, config_path=config_path)
        else:
            _persist(cfg, config_path=config_path)
        return {"roots": _service().roots_meta(), "added": True}

    @router.get("/list")
    def list_items(
        root: str = Query(..., description="配置根目录绝对路径"),
        kind: str = Query("all", pattern="^(all|video|image)$"),
        q: str = Query("", description="文件名包含匹配，忽略大小写"),
        sub: str = Query("", description="根目录下的相对子目录（前端逐层进入）"),
        offset: int = Query(0, ge=0),
        limit: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=200),
    ) -> dict[str, Any]:
        try:
            root_path = _service().resolve_root(root)
            result = _service().list_items(
                root, kind=kind, q=q, sub=sub, offset=offset, limit=limit
            )
        except MediaNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        # 富化：为每个条目附带收藏/评级状态
        items = cast("list[dict[str, object]]", result.get("items") or [])
        if items:
            abs_paths = {str(item.get("rel")): str(root_path / str(item.get("rel"))) for item in items if item.get("rel")}
            states = store.get_states(abs_paths.values())
            for item in items:
                rel = str(item.get("rel") or "")
                if rel and rel in abs_paths:
                    s = states.get(abs_paths[rel], {"favorite": 0, "rating": 0})
                    item["favorite"] = s.get("favorite", 0)
                    item["rating"] = s.get("rating", 0)
        result["items"] = items
        return result

    @router.get("/item")
    def get_item_state(
        root: str = Query(..., description="配置根目录绝对路径"),
        path: str = Query(..., description="根目录内相对文件路径"),
    ) -> dict[str, int]:
        try:
            file_path = _service().resolve_file(root, path)
        except MediaNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        return store.get_state(str(file_path))

    @router.post("/item")
    def set_item_state(payload: ItemStateIn) -> dict[str, int]:
        """写入收藏 / 评级。两个字段都可选；两者皆空等效于清除该条状态。"""
        if payload.favorite is None and payload.rating is None:
            raise HTTPException(status_code=400, detail="需要 favorite 或 rating 至少一个")
        try:
            file_path = _service().resolve_file(payload.root, payload.path)
        except MediaNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        return store.set_state(
            str(file_path),
            str(_service().resolve_root(payload.root)),
            payload.path.strip().strip("/\\"),
            favorite=payload.favorite,
            rating=payload.rating,
        )

    @router.delete("/item")
    def delete_item(
        root: str = Query(..., description="配置根目录绝对路径"),
        path: str = Query(..., description="根目录内相对文件路径"),
    ) -> dict[str, str]:
        """删除一个媒体文件。

        为了可恢复，文件被移动到回收站 ``<data_dir>/media_trash/<根目录名>/<相对路径>``
        （不污染媒体根目录列表），同时清除其收藏/评级记录与封面缓存。
        """
        try:
            file_path = _service().resolve_file(root, path)
        except MediaNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        # 记录收藏/评级与封面缓存 key（移动前）
        deleted_state = store.get_state(str(file_path))
        poster_key = _poster_key_for(file_path)
        # 目标回收站路径（保留相对结构）
        root_path = _service().resolve_root(root)
        trash_root = Path(_poster_cache_dir(config).parent) / "media_trash" / (root_path.name or "root")
        rel_cleaned = (path or "").strip().strip("/\\")
        target = trash_root / rel_cleaned
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(file_path), str(target))
        except OSError as exc:
            logger.exception("删除文件失败: %s", file_path)
            raise HTTPException(status_code=500, detail=f"删除失败: {exc}") from exc
        # 清理状态与封面缓存
        store.delete_state(str(file_path))
        if poster_key is not None:
            try:
                (_poster_cache_dir(config) / f"{poster_key}.jpg").unlink(missing_ok=True)
            except OSError:
                pass
        return {"deleted": rel_cleaned, "trashed": str(target), "had_favorite": "1" if deleted_state.get("favorite") else "0"}

    @router.get("/favorites")
    def list_favorites(
        root: str = Query("", description="可选：仅返回该根目录下的收藏"),
        kind: str = Query("all", pattern="^(all|video|image)$"),
        q: str = Query("", description="文件名包含匹配，忽略大小写"),
    ) -> dict[str, Any]:
        try:
            fav_root = _service().resolve_root(root) if root else None
        except MediaNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        items = store.favorites(root=str(fav_root) if fav_root else None)
        query = (q or "").strip().lower()
        filtered = [
            it
            for it in items
            if (kind == "all" or it.get("kind") == kind)
            and (not query or query in str(it.get("name", "")).lower())
            and _favorite_file_exists(it)
        ]
        return {"items": filtered, "total": len(filtered)}

    @router.get("/file")
    def stream_file(
        root: str = Query(..., description="配置根目录绝对路径"),
        path: str = Query(..., description="根目录内相对文件路径"),
    ) -> Any:
        try:
            file_path = _service().resolve_file(root, path)
        except MediaNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        media_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        from fastapi.responses import FileResponse

        return FileResponse(str(file_path), media_type=media_type)

    @router.get("/poster")
    def video_poster(
        root: str = Query(..., description="配置根目录绝对路径"),
        path: str = Query(..., description="根目录内相对视频文件路径"),
    ) -> Any:
        """返回视频首页帧缩略图（ffmpeg 抽帧 + 缓存）。无法生成时 404。"""
        try:
            file_path = _service().resolve_file(root, path)
        except MediaNotFoundError as exc:
            raise _not_found(str(exc)) from exc
        poster = _ensure_poster(config, file_path)
        if poster is None:
            raise HTTPException(status_code=404, detail="无法生成视频封面")
        from fastapi.responses import FileResponse

        return FileResponse(str(poster), media_type="image/jpeg")

    return router


def _persist(config: Any, *, config_path: str | Path | None = None) -> None:
    from openbiliclaw.config import save_config

    save_config(config, config_path=config_path)