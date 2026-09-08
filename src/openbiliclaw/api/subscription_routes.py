"""Subscription management routes for OpenBiliClaw API."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from openbiliclaw.api.models import (
    SubscriptionAddIn,
    SubscriptionDeleteIn,
    SubscriptionItemOut,
    SubscriptionListOut,
    SubscriptionStatsOut,
)

logger = logging.getLogger(__name__)


def register_subscription_routes(
    app: FastAPI,
    ctx: Any,
    *,
    config_save_lock: Any,
) -> None:
    """Register subscription management endpoints on the FastAPI app."""

    @app.get("/api/subscriptions", response_model=SubscriptionListOut)
    def list_subscriptions() -> SubscriptionListOut:
        """List all subscription sources grouped by type."""
        from openbiliclaw.config import load_config as _load_cfg

        _cfg = _load_cfg()
        return SubscriptionListOut(
            rss=list(_cfg.scheduler.rss_subscriptions),
            xiaoyuzhou=list(_cfg.scheduler.xiaoyuzhou_subscriptions),
            wechat=list(_cfg.scheduler.wechat_subscriptions),
        )

    @app.get("/api/subscriptions/stats", response_model=SubscriptionStatsOut)
    def list_subscriptions_with_stats() -> SubscriptionStatsOut:
        """List all subscriptions with item count and last fetch statistics."""
        from openbiliclaw.config import load_config as _load_cfg

        _cfg = _load_cfg()
        stats_map: dict[str, tuple[int, str]] = {}  # url -> (count, last_fetched)

        # Query from content_cache
        database = getattr(ctx, "database", None)
        conn = getattr(database, "conn", None) if database else None

        if conn is not None:
            for platform, config_list in [
                ("rss", _cfg.scheduler.rss_subscriptions),
                ("xiaoyuzhou", _cfg.scheduler.xiaoyuzhou_subscriptions),
                ("wechat", _cfg.scheduler.wechat_subscriptions),
            ]:
                for item in config_list:
                    name = item.get("name", "")
                    url = item.get("url", "")
                    if not url:
                        continue
                    cursor = conn.execute(
                        """
                        SELECT COUNT(*) AS item_count, MAX(discovered_at) AS last_fetched
                        FROM content_cache
                        WHERE source_platform = ? AND up_name = ?
                        """,
                        (platform, name),
                    )
                    row = cursor.fetchone()
                    if row:
                        stats_map[f"{platform}:{url}"] = (int(row[0] or 0), str(row[1] or ""))

        # Build response
        result = SubscriptionStatsOut()

        def map_subs(subs: list[dict[str, str]], platform: str) -> list[SubscriptionItemOut]:
            out = []
            for sub in subs:
                key = f"{platform}:{sub['url']}"
                count, last = stats_map.get(key, (0, ""))
                out.append(
                    SubscriptionItemOut(
                        name=sub["name"],
                        url=sub["url"],
                        item_count=int(count),
                        last_fetched_at=last or "",
                    )
                )
            return out

        result.rss = map_subs(list(_cfg.scheduler.rss_subscriptions), "rss")
        result.xiaoyuzhou = map_subs(list(_cfg.scheduler.xiaoyuzhou_subscriptions), "xiaoyuzhou")
        result.wechat = map_subs(list(_cfg.scheduler.wechat_subscriptions), "wechat")
        return result

    @app.post("/api/subscriptions")
    async def add_subscription(payload: SubscriptionAddIn) -> JSONResponse:
        """Add a new subscription source."""
        from openbiliclaw.config import load_config as _load_cfg
        from openbiliclaw.config import save_config as _save_cfg

        async with config_save_lock:
            _cfg = _load_cfg()
            source_type = payload.source_type
            source_map = {
                "rss": "rss_subscriptions",
                "xiaoyuzhou": "xiaoyuzhou_subscriptions",
                "wechat": "wechat_subscriptions",
            }
            field_name = source_map.get(source_type)
            if field_name is None:
                return JSONResponse(
                    {"ok": False, "error": f"unknown source_type: {source_type}"},
                    status_code=400,
                )
            subscriptions: list[dict[str, str]] = getattr(_cfg.scheduler, field_name)
            # Check if already exists
            for sub in subscriptions:
                if sub.get("url") == payload.url:
                    return JSONResponse(
                        {"ok": False, "error": "subscription already exists"},
                        status_code=409,
                    )
            subscriptions.append({"name": payload.name, "url": payload.url})
            setattr(_cfg.scheduler, field_name, subscriptions)
            _save_cfg(_cfg)
        return JSONResponse({"ok": True})

    @app.delete("/api/subscriptions")
    async def delete_subscription(payload: SubscriptionDeleteIn) -> JSONResponse:
        """Delete a subscription source by type and URL."""
        from openbiliclaw.config import load_config as _load_cfg
        from openbiliclaw.config import save_config as _save_cfg

        async with config_save_lock:
            _cfg = _load_cfg()
            source_type = payload.source_type
            source_map = {
                "rss": "rss_subscriptions",
                "xiaoyuzhou": "xiaoyuzhou_subscriptions",
                "wechat": "wechat_subscriptions",
            }
            field_name = source_map.get(source_type)
            if field_name is None:
                return JSONResponse(
                    {"ok": False, "error": f"unknown source_type: {source_type}"},
                    status_code=400,
                )
            subscriptions: list[dict[str, str]] = getattr(_cfg.scheduler, field_name)
            new_list = [s for s in subscriptions if s.get("url") != payload.url]
            if len(new_list) == len(subscriptions):
                return JSONResponse(
                    {"ok": False, "error": "subscription not found"},
                    status_code=404,
                )
            setattr(_cfg.scheduler, field_name, new_list)
            _save_cfg(_cfg)
        return JSONResponse({"ok": True})

