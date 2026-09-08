"""Cookie management API routes: Douyin and X (Twitter) cookie sync."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any

from openbiliclaw.api.models import (
    DouyinCookieIn,
    DouyinCookieResponse,
    XCookieIn,
    XCookieResponse,
)
from openbiliclaw.sources.x_auth import X_REQUIRED_COOKIE_NAMES, XCookieManager

if TYPE_CHECKING:
    from openbiliclaw.api.runtime_context import RuntimeContext


def register_cookie_routes(app: Any, ctx: RuntimeContext, *, config: Any) -> None:
    """Register cookie management endpoints on the FastAPI app."""

    @app.post("/api/sources/dy/cookie", response_model=DouyinCookieResponse)
    async def sync_douyin_cookie(payload: DouyinCookieIn) -> DouyinCookieResponse:
        """Receive a Douyin cookie from the browser extension."""
        from openbiliclaw.sources.douyin_auth import DouyinCookieManager
        from openbiliclaw.sources.douyin_direct import parse_cookie_header

        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return DouyinCookieResponse(
                ok=False,
                has_cookie=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        runtime_config = getattr(ctx, "config", None) or config
        manager = DouyinCookieManager(runtime_config.data_path)
        manager.set_cookie(cookie_value, source=payload.source)
        cookie_names = sorted(parse_cookie_header(cookie_value).keys())

        with suppress(Exception):
            await ctx.event_hub.publish(
                {
                    "type": "douyin_cookie_synced",
                    "source": payload.source,
                    "cookie_names": cookie_names,
                }
            )

        return DouyinCookieResponse(
            ok=True,
            has_cookie=True,
            cookie_names=cookie_names,
            message="Douyin Cookie synced.",
        )

    @app.post("/api/sources/x/cookie", response_model=XCookieResponse)
    async def sync_x_cookie(payload: XCookieIn) -> XCookieResponse:
        """Receive an X (Twitter) cookie from the browser extension."""
        from openbiliclaw.sources.douyin_direct import parse_cookie_header

        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return XCookieResponse(
                ok=False,
                has_cookie=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        runtime_config = getattr(ctx, "config", None) or config
        XCookieManager(runtime_config.data_path).set_cookie(cookie_value, source=payload.source)
        cookie_pairs = parse_cookie_header(cookie_value)
        cookie_names = sorted(cookie_pairs.keys())
        has_cookie = all(name in cookie_pairs for name in X_REQUIRED_COOKIE_NAMES)

        if has_cookie:
            with suppress(Exception):
                from openbiliclaw.storage.x_health import XSourceHealthStore

                XSourceHealthStore(ctx.database).clear_relogin_block()

        with suppress(Exception):
            await ctx.event_hub.publish(
                {
                    "type": "x_cookie_synced",
                    "source": payload.source,
                    "has_cookie": has_cookie,
                    "cookie_names": cookie_names,
                }
            )

        return XCookieResponse(
            ok=True,
            has_cookie=has_cookie,
            cookie_names=cookie_names,
            message=(
                "X Cookie synced."
                if has_cookie
                else "X Cookie stored but missing auth_token / ct0."
            ),
        )
