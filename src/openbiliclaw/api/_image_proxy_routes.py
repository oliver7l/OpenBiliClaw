"""Image proxy API routes: cache-first cover image proxying."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Query
from fastapi.responses import FileResponse, Response

from openbiliclaw.api.runtime_context import RuntimeContext
from openbiliclaw.runtime.image_cache import (
    CoverFetchError,
    fetch_cover_bytes,
    image_cache_dir,
    image_cache_key,
    save_image_bytes,
)


def _image_cache_lookup(url: str) -> tuple[Path, str] | None:
    """Return (path, content_type) if a cached copy exists."""
    key = image_cache_key(url)
    cache_dir = image_cache_dir()
    for candidate in cache_dir.glob(f"{key}.*"):
        ext = candidate.suffix.lstrip(".")
        content_type = f"image/{ext}" if ext else "image/jpeg"
        if candidate.stat().st_size > 0:
            return candidate, content_type
    return None


def _image_cache_response(url: str) -> FileResponse | None:
    cached = _image_cache_lookup(url)
    if not cached:
        return None
    cache_path, cache_ct = cached
    return FileResponse(
        cache_path,
        media_type=cache_ct,
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "X-Image-Cache": "hit",
        },
    )


def register_image_proxy_routes(app: Any, ctx: RuntimeContext) -> None:
    """Register image proxy endpoints on the FastAPI app."""

    @app.get("/api/image-proxy", response_model=None)
    async def image_proxy(
        url: str = Query(..., description="URL-encoded image URL to proxy"),
    ) -> Response | FileResponse:
        """Proxy whitelisted remote cover images through the local backend.

        Cache-first: a cached copy IS the image for that URL.
        """
        if cached := _image_cache_response(url):
            return cached

        started = time.monotonic()
        try:
            data, content_type = await fetch_cover_bytes(url)
        except CoverFetchError as exc:
            if exc.status_code >= 500 and (cached := _image_cache_response(url)):
                return cached
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

        save_image_bytes(url, data, content_type)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if elapsed_ms > 800:
            ctx.logger.debug("image-proxy MISS %dms %s", elapsed_ms, url[:100])
        return Response(
            content=data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Content-Type-Options": "nosniff",
                "X-Image-Cache": "miss",
            },
        )
