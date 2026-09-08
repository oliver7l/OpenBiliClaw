"""路由注册集中区。

所有 API 路由的注册都集中在这里，避免 app.py 中散落大量注册调用。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def register_all_routes(
    app: Any,
    ctx: Any,
    config: Any,
    *,
    fire_and_forget_tasks: Any,
    serialize_recommendation_items: Any,
    config_save_lock: Any,
    init_active_now: Any,
    schedule_post_feedback_tasks: Any,
    record_exploration_buffer_event: Any,
    recommendation_buffer_domain: Any,
    get_auth_gate: Any,
    ingest_profile_update_events: Any,
    snapshot_config_file: Any,
    restore_config_snapshot: Any,
    pick_best_xhs_url: Any,
    load_interest_keywords: Any,
    request_runtime_replenishment: Any,
    build_recommendation_router: Any,
) -> None:
    """Register all API routes on the FastAPI app."""

    # ── Notes CRUD routes ──────────────────────────────────────
    from openbiliclaw.api.notes_routes import register_notes_routes

    register_notes_routes(app, ctx)

    # ── Saved-sync (reading library) routes ─────────────────────
    from openbiliclaw.api.saved_sync_routes import register_saved_sync_routes

    register_saved_sync_routes(app, ctx)

    # ── System-level routes (update status, notifications, cognition) ──
    from openbiliclaw.api._system_routes import register_system_routes

    register_system_routes(app, ctx)

    # ── Image proxy routes ───────────────────────────────────────
    from openbiliclaw.api._image_proxy_routes import register_image_proxy_routes

    register_image_proxy_routes(app, ctx)

    # ── Cookie management routes (douyin/x) ─────────────────────
    from openbiliclaw.api._cookie_routes import register_cookie_routes

    register_cookie_routes(app, ctx, config=config)

    # ── LLM quota monitoring routes ─────────────────────────────
    from openbiliclaw.api._llm_routes import register_llm_routes

    register_llm_routes(app, ctx)

    # ── Clone system routes ─────────────────────────────────────
    from openbiliclaw.api._clone_routes import register_clone_routes

    register_clone_routes(app, ctx)

    # ── Activity feed routes ────────────────────────────────────
    from openbiliclaw.api._activity_feed_routes import register_activity_feed_routes

    register_activity_feed_routes(app, ctx)

    # ── Runtime status routes ───────────────────────────────────
    from openbiliclaw.api._runtime_status_routes import register_runtime_status_routes

    register_runtime_status_routes(app, ctx)

    # ── 拆分后未接线的路由注册（K3 孤儿路由修复）──────────────────
    for _mod_name, _fn_name in [
        ("article_routes", "register_article_routes"),
        ("diary_routes", "register_diary_routes"),
        ("health_routes", "register_health_routes"),
        ("knowledge_routes", "register_knowledge_routes"),
        ("library_routes", "register_library_routes"),
        ("reading_routes", "register_reading_routes"),
    ]:
        try:
            _mod = __import__(f"openbiliclaw.api.{_mod_name}", fromlist=[_fn_name])
            getattr(_mod, _fn_name)(app, ctx)
        except Exception:  # noqa: BLE001
            logger.exception("%s registration failed", _fn_name)

    # ── 有额外依赖参数的路由注册 ────────────────────────────────
    try:
        from openbiliclaw.api.chat_probe_routes import register_chat_probe_routes

        register_chat_probe_routes(
            app,
            ctx,
            fire_and_forget_tasks=fire_and_forget_tasks,
            serialize_recommendation_items=serialize_recommendation_items,
        )
    except Exception:  # noqa: BLE001
        logger.exception("chat_probe_routes registration failed")

    try:
        from openbiliclaw.api.chat_recommend_routes import register_chat_recommend_routes

        register_chat_recommend_routes(
            app,
            ctx,
            serialize_recommendation_items=serialize_recommendation_items,
        )
    except Exception:  # noqa: BLE001
        logger.exception("chat_recommend_routes registration failed")

    try:
        from openbiliclaw.api.config_routes import register_config_routes

        register_config_routes(
            app,
            ctx,
            config_save_lock=config_save_lock,
            init_active_now=init_active_now,
        )
    except Exception:  # noqa: BLE001
        logger.exception("config_routes registration failed")

    try:
        from openbiliclaw.api.feedback_topics_routes import (
            register_feedback_topics_routes,
        )

        register_feedback_topics_routes(
            app,
            ctx,
            schedule_post_feedback_tasks=schedule_post_feedback_tasks,
            record_exploration_buffer_event=record_exploration_buffer_event,
            recommendation_buffer_domain=recommendation_buffer_domain,
        )
    except Exception:  # noqa: BLE001
        logger.exception("feedback_topics_routes registration failed")

    try:
        from openbiliclaw.api.source_routes import register_source_routes

        register_source_routes(
            app,
            ctx,
            config_save_lock,
            get_auth_gate=get_auth_gate,
            init_active_now=init_active_now,
            ingest_profile_update_events=ingest_profile_update_events,
            snapshot_config_file=snapshot_config_file,
            restore_config_snapshot=restore_config_snapshot,
        )
    except Exception:  # noqa: BLE001
        logger.exception("source_routes registration failed")

    try:
        from openbiliclaw.api.subscription_routes import register_subscription_routes

        register_subscription_routes(
            app,
            ctx,
            config_save_lock=config_save_lock,
        )
    except Exception:  # noqa: BLE001
        logger.exception("subscription_routes registration failed")

    # ── Knowledge Forge routes ─────────────────────────────────
    try:
        from openbiliclaw.api.knowledge_forge_routes import (
            register_knowledge_forge_routes,
        )

        register_knowledge_forge_routes(app, ctx)
    except Exception:  # noqa: BLE001
        logger.exception("Knowledge Forge routes registration failed")

    # ── Recommendation feed routes (M1 extraction from this file) ──
    app.include_router(
        build_recommendation_router(
            ctx=ctx,
            config=config,
            fire_and_forget_tasks=fire_and_forget_tasks,
            init_active_now=init_active_now,
            pick_best_xhs_url=pick_best_xhs_url,
            serialize_recommendation_items=serialize_recommendation_items,
            load_interest_keywords=load_interest_keywords,
            request_runtime_replenishment=request_runtime_replenishment,
        )
    )

    # ── 旅行预算 API ─────────────────────────────────────────────
    try:
        from openbiliclaw.travel.routes import build_travel_router

        _travel_cfg = getattr(config, "travel", None)
        app.include_router(
            build_travel_router(
                data_path=getattr(_travel_cfg, "data_path", "") or "",
                budget_doc=getattr(_travel_cfg, "budget_doc", "新疆旅行预算.md"),
                flights_json=getattr(
                    _travel_cfg, "flights_json", "ctrip-ticket-crawler/our_routes_results.json"
                ),
            )
        )
    except Exception:  # noqa: BLE001
        logger.exception("Travel routes registration failed")

    # ── 求职面试备战 API ─────────────────────────────────────────
    try:
        from openbiliclaw.interview.routes import build_interview_router

        _interview_cfg = getattr(config, "interview", None)
        app.include_router(
            build_interview_router(
                root=str(getattr(_interview_cfg, "root", "") or "") or None,
            )
        )
    except Exception:  # noqa: BLE001 — 可选模块导入失败不阻塞主 API
        logger.exception("Interview routes registration failed")

    # ── 面试复盘记录 API ─────────────────────────────────────────
    try:
        from openbiliclaw.interview.review_routes import build_review_router

        app.include_router(build_review_router())
    except Exception:  # noqa: BLE001
        logger.exception("Interview review routes registration failed")
