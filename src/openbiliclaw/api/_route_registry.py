"""路由注册集中区。

所有 API 路由的注册都集中在这里，避免 app.py 中散落大量注册调用。

设计约定：单个路由模块注册失败**不阻塞主 API 启动**（可选模块/插件式路由很常见），
但**绝不静默**——失败会被收集，并在注册末尾以一条聚合 ERROR 日志列出。
"静默吞错"曾导致 health_routes 的 55 条路由长期未注册却无人察觉，故此处强制可见。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class _RouteRegistrationFailures:
    """收集路由模块注册失败项，供末尾聚合告警。"""

    def __init__(self) -> None:
        self._items: list[tuple[str, BaseException]] = []

    def record(self, module_name: str, exc: BaseException) -> None:
        self._items.append((module_name, exc))

    def report(self) -> None:
        if not self._items:
            return
        summary = ", ".join(f"{name}({type(exc).__name__}: {exc})" for name, exc in self._items)
        logger.error(
            "%d route module(s) FAILED to register — their endpoints are NOT available: %s",
            len(self._items),
            summary,
        )
        for name, exc in self._items:
            logger.error("route module %r registration traceback:", name, exc_info=exc)

    @property
    def failures(self) -> list[tuple[str, BaseException]]:
        return list(self._items)


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
    """Register all API routes on the FastAPI app.

    注册失败的模块会被收集，在末尾以聚合 ERROR 日志告警（不再静默吞错）。
    """
    _failures = _RouteRegistrationFailures()

    # ── Notes CRUD routes ──────────────────────────────────────
    from openbiliclaw.api.notes_routes import register_notes_routes

    register_notes_routes(app, ctx)

    # ── Conversation archive routes (用户与 AI 对话内容归档) ──
    from openbiliclaw.api.conversation_archive_routes import (
        register_conversation_archive_routes,
    )

    register_conversation_archive_routes(app, ctx)

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
        except Exception as _exc:  # noqa: BLE001
            _failures.record(_mod_name, _exc)

    # ── 有额外依赖参数的路由注册 ────────────────────────────────
    try:
        from openbiliclaw.api.chat_probe_routes import register_chat_probe_routes

        register_chat_probe_routes(
            app,
            ctx,
            fire_and_forget_tasks=fire_and_forget_tasks,
            serialize_recommendation_items=serialize_recommendation_items,
        )
    except Exception as _exc:  # noqa: BLE001
        _failures.record("chat_probe_routes", _exc)

    try:
        from openbiliclaw.api.chat_recommend_routes import register_chat_recommend_routes

        register_chat_recommend_routes(
            app,
            ctx,
            serialize_recommendation_items=serialize_recommendation_items,
        )
    except Exception as _exc:  # noqa: BLE001
        _failures.record("chat_recommend_routes", _exc)

    try:
        from openbiliclaw.api.config_routes import register_config_routes

        register_config_routes(
            app,
            ctx,
            config_save_lock=config_save_lock,
            init_active_now=init_active_now,
        )
    except Exception as _exc:  # noqa: BLE001
        _failures.record("config_routes", _exc)

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
    except Exception as _exc:  # noqa: BLE001
        _failures.record("feedback_topics_routes", _exc)

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
    except Exception as _exc:  # noqa: BLE001
        _failures.record("source_routes", _exc)

    try:
        from openbiliclaw.api.subscription_routes import register_subscription_routes

        register_subscription_routes(
            app,
            ctx,
            config_save_lock=config_save_lock,
        )
    except Exception as _exc:  # noqa: BLE001
        _failures.record("subscription_routes", _exc)

    # ── Knowledge Forge routes ─────────────────────────────────
    try:
        from openbiliclaw.api.knowledge_forge_routes import (
            register_knowledge_forge_routes,
        )

        register_knowledge_forge_routes(app, ctx)
    except Exception as _exc:  # noqa: BLE001
        _failures.record("Knowledge Forge routes", _exc)

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
    except Exception as _exc:  # noqa: BLE001
        _failures.record("Travel routes", _exc)

    # ── 求职面试备战 API ─────────────────────────────────────────
    try:
        from openbiliclaw.interview.routes import build_interview_router

        _interview_cfg = getattr(config, "interview", None)
        app.include_router(
            build_interview_router(
                root=str(getattr(_interview_cfg, "root", "") or "") or None,
            )
        )
    except Exception as _exc:  # noqa: BLE001
        _failures.record("Interview routes", _exc)

    # ── 面试复盘记录 API ─────────────────────────────────────────
    try:
        from openbiliclaw.interview.review_routes import build_review_router

        app.include_router(build_review_router())
    except Exception as _exc:  # noqa: BLE001
        _failures.record("Interview review routes", _exc)

    # ── 面试题阅读追踪 API ───────────────────────────────────────
    try:
        from openbiliclaw.api._interview_routes import register_interview_routes

        register_interview_routes(app, ctx)
    except Exception as _exc:  # noqa: BLE001
        _failures.record("Interview question tracker routes", _exc)

    # ── 本地媒体浏览 API ──────────────────────────────────────────
    try:
        from openbiliclaw.media.routes import build_media_router

        app.include_router(
            build_media_router(
                config=config,
                config_save_lock=config_save_lock,
            )
        )
    except Exception as _exc:  # noqa: BLE001
        _failures.record("Media routes", _exc)

    # ── ed2k / Kad 下载管理 API ───────────────────────────────────
    try:
        from openbiliclaw.ed2k.routes import build_ed2k_router

        app.include_router(build_ed2k_router(config=config))
    except Exception as _exc:  # noqa: BLE001
        _failures.record("ed2k routes", _exc)

    # ── 豆瓣书影音 API ───────────────────────────────────────────
    try:
        from openbiliclaw.douban.routes import build_douban_router

        app.include_router(
            build_douban_router(
                config=config,
                llm_service=getattr(ctx, "llm_service", None),
            )
        )
    except Exception as _exc:  # noqa: BLE001
        _failures.record("douban routes", _exc)

    # ── 周末怎么玩 API ───────────────────────────────────────────
    try:
        from openbiliclaw.weekend.routes import build_weekend_router
        from openbiliclaw.weekend.store import WeekendStore

        _wk_cfg = getattr(config, "weekend", None)
        _wk_db = str(getattr(_wk_cfg, "db_path", "") or "") or None
        _wk_use_llm = bool(getattr(_wk_cfg, "use_llm", False))
        app.include_router(
            build_weekend_router(
                store=WeekendStore(_wk_db) if _wk_db else None,
                use_llm=_wk_use_llm,
            )
        )
    except Exception as _exc:  # noqa: BLE001
        _failures.record("Weekend routes", _exc)

    # ── 聚合告警：注册失败的路由模块必须可见（不再静默吞错）──────────
    _failures.report()
