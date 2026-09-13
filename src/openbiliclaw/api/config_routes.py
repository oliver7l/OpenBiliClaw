"""Configuration management routes for OpenBiliClaw API."""

from __future__ import annotations

import logging
import shutil
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from openbiliclaw.api.models import (
    AutostartConfigOut,
    BilibiliConfigOut,
    BilibiliSourceConfigOut,
    ConfigIssueOut,
    ConfigModelDiscoveryIn,
    ConfigModelDiscoveryResponse,
    ConfigResponse,
    ConfigUpdateIn,
    ConfigUpdateResponse,
    DiscoveryConfigOut,
    DouyinSourceConfigOut,
    EmbeddingConfigOut,
    LLMConfigOut,
    LLMProviderConfigOut,
    LoggingConfigOut,
    ModuleLLMConfigOut,
    SchedulerConfigOut,
    SourcesBrowserConfigOut,
    SourcesConfigOut,
    SourceShareSuggestionIn,
    SourceShareSuggestionResponse,
    StorageConfigOut,
    TwitterSourceConfigOut,
    XiaohongshuSourceConfigOut,
    YoutubeSourceConfigOut,
    ZhihuSourceConfigOut,
)
from openbiliclaw.sources.x_auth import XCookieManager, resolve_x_cookie

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ── Resettable config fields ─────────────────────────────────────

_RESETTABLE_CONFIG_FIELDS = {
    "llm.openai.api_key": ("llm", "openai", "api_key"),
    "llm.claude.api_key": ("llm", "claude", "api_key"),
    "llm.gemini.api_key": ("llm", "gemini", "api_key"),
    "llm.deepseek.api_key": ("llm", "deepseek", "api_key"),
    "llm.openrouter.api_key": ("llm", "openrouter", "api_key"),
    "llm.openai_compatible.api_key": ("llm", "openai_compatible", "api_key"),
    "llm.embedding.api_key": ("llm", "embedding", "api_key"),
}


# ── Config file snapshot helpers ─────────────────────────────────


def _config_backup_path(config_path: Path) -> Path:
    return config_path.with_name(f"{config_path.name}.bak")


def _snapshot_config_file(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    backup_path = _config_backup_path(config_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, backup_path)
    return backup_path


def _restore_config_snapshot(backup_path: Path, config_path: Path) -> None:
    shutil.copy2(backup_path, config_path)


def _validate_llm_buildable(cfg: Any, base_issues: list[Any]) -> list[Any]:
    from openbiliclaw.config import ConfigIssue
    from openbiliclaw.llm.registry import RegistryBuildError, build_llm_registry

    issues = list(base_issues)
    try:
        build_llm_registry(cfg.llm)
    except RegistryBuildError as exc:
        issues.append(
            ConfigIssue(
                field="llm",
                message=f"LLM registry would fail to build: {exc}",
                severity="blocking",
            )
        )
    return issues


# ── Lazy imports for shared helpers (avoid circular imports) ─────


def _get_source_share_order() -> tuple[str, ...]:
    from openbiliclaw.api.app import _SOURCE_SHARE_ORDER

    return _SOURCE_SHARE_ORDER


def _get_count_events_by_source_platform(database: Any) -> dict[str, int]:
    from openbiliclaw.api.app import _count_events_by_source_platform

    return _count_events_by_source_platform(database)


def _get_x_required_cookie_names() -> tuple[str, ...]:
    # 常量真身在 sources/x_auth.py（api/_cookie_routes.py 也用它）。
    # 此前从 api.app 导入一个从未存在过的 `_X_REQUIRED_COOKIE_NAMES`，
    # 导致 PUT /api/config 更新 X cookie 时抛 ImportError。
    from openbiliclaw.sources.x_auth import X_REQUIRED_COOKIE_NAMES

    return X_REQUIRED_COOKIE_NAMES


# ── Route registration ───────────────────────────────────────────


def register_config_routes(
    app: FastAPI,
    ctx: Any,
    *,
    config_save_lock: Any = None,
    init_active_now: Any = None,
) -> None:
    """Register configuration management endpoints on the FastAPI app."""

    def _config_to_response(
        cfg: Any,
        issues: list[Any] | None = None,
        *,
        mask_keys: bool = True,
        degraded: bool = False,
        degraded_reason: str = "",
    ) -> ConfigResponse:
        """Convert a Config dataclass to a ConfigResponse, optionally masking API keys."""

        def _mask(key: str) -> str:
            if not mask_keys or not key:
                return key
            if len(key) <= 8:
                return "*" * len(key)
            return key[:4] + "*" * (len(key) - 8) + key[-4:]

        # Douyin / X store their cookie in data/*.json (env override wins),
        # not in config.toml — resolve here so the settings pages can show
        # the live credential exactly like the Bilibili card does.
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

        dy_cookie = ""
        with suppress(Exception):
            dy_cookie = resolve_douyin_cookie(
                data_dir=cfg.data_path,
                cookie_env=cfg.sources.douyin.cookie_env,
            )
        tw_cookie = ""
        with suppress(Exception):
            tw_cookie = resolve_x_cookie(
                data_dir=cfg.data_path,
                cookie_env=cfg.sources.twitter.cookie_env,
            )

        from openbiliclaw.config import LLM_PROVIDER_NAMES

        def _provider_out(p: Any) -> LLMProviderConfigOut:
            return LLMProviderConfigOut(
                api_key=_mask(p.api_key),
                model=p.model,
                base_url=p.base_url,
                auth_mode=getattr(p, "auth_mode", ""),
                http_referer=getattr(p, "http_referer", ""),
                x_title=getattr(p, "x_title", ""),
                reasoning_effort=getattr(p, "reasoning_effort", ""),
            )

        issue_list = [
            ConfigIssueOut(
                field=i.field,
                message=i.message,
                severity=getattr(i, "severity", "warning"),
            )
            for i in (issues or [])
        ]

        return ConfigResponse(
            language=cfg.language,
            data_dir=cfg.data_dir,
            degraded=degraded,
            degraded_reason=degraded_reason,
            llm=LLMConfigOut(
                default_provider=cfg.llm.default_provider,
                concurrency=int(getattr(cfg.llm, "concurrency", 3)),
                timeout=int(getattr(cfg.llm, "timeout", 300)),
                fallback_enabled=cfg.llm.fallback_enabled,
                fallback_provider=cfg.llm.fallback_provider,
                **{
                    provider_name: _provider_out(getattr(cfg.llm, provider_name))
                    for provider_name in LLM_PROVIDER_NAMES
                },
                embedding=EmbeddingConfigOut(
                    provider=cfg.llm.embedding.provider,
                    model=cfg.llm.embedding.model,
                    api_key=_mask(cfg.llm.embedding.api_key),
                    base_url=cfg.llm.embedding.base_url,
                    output_dimensionality=cfg.llm.embedding.output_dimensionality,
                    similarity_threshold=cfg.llm.embedding.similarity_threshold,
                    fallback_enabled=cfg.llm.embedding.fallback_enabled,
                    fallback_provider=cfg.llm.embedding.fallback_provider,
                ),
                soul=ModuleLLMConfigOut(
                    provider=cfg.llm.soul.provider,
                    model=cfg.llm.soul.model,
                ),
                discovery=ModuleLLMConfigOut(
                    provider=cfg.llm.discovery.provider,
                    model=cfg.llm.discovery.model,
                ),
                recommendation=ModuleLLMConfigOut(
                    provider=cfg.llm.recommendation.provider,
                    model=cfg.llm.recommendation.model,
                ),
                evaluation=ModuleLLMConfigOut(
                    provider=cfg.llm.evaluation.provider,
                    model=cfg.llm.evaluation.model,
                ),
            ),
            bilibili=BilibiliConfigOut(
                auth_method=cfg.bilibili.auth_method,
                cookie=_mask(cfg.bilibili.cookie),
                browser_executable=cfg.bilibili.browser_executable,
                browser_headed=cfg.bilibili.browser_headed,
            ),
            sources=SourcesConfigOut(
                browser=SourcesBrowserConfigOut(
                    cdp_url=cfg.sources.browser_cdp_url,
                    headed=cfg.sources.browser_headed,
                ),
                bilibili=BilibiliSourceConfigOut(
                    enabled=cfg.sources.bilibili.enabled,
                ),
                xiaohongshu=XiaohongshuSourceConfigOut(
                    enabled=cfg.sources.xiaohongshu.enabled,
                    daily_search_budget=cfg.sources.xiaohongshu.daily_search_budget,
                    daily_creator_budget=cfg.sources.xiaohongshu.daily_creator_budget,
                    task_interval_seconds=cfg.sources.xiaohongshu.task_interval_seconds,
                ),
                douyin=DouyinSourceConfigOut(
                    enabled=cfg.sources.douyin.enabled,
                    mode=cfg.sources.douyin.mode,
                    cookie=_mask(dy_cookie),
                    cookie_env=cfg.sources.douyin.cookie_env,
                    daily_search_budget=cfg.sources.douyin.daily_search_budget,
                    daily_hot_budget=cfg.sources.douyin.daily_hot_budget,
                    daily_feed_budget=cfg.sources.douyin.daily_feed_budget,
                    request_interval_seconds=cfg.sources.douyin.request_interval_seconds,
                ),
                youtube=YoutubeSourceConfigOut(
                    enabled=cfg.sources.youtube.enabled,
                    daily_search_budget=cfg.sources.youtube.daily_search_budget,
                    daily_trending_budget=cfg.sources.youtube.daily_trending_budget,
                    daily_channel_budget=cfg.sources.youtube.daily_channel_budget,
                    request_interval_seconds=cfg.sources.youtube.request_interval_seconds,
                    min_interval_minutes=cfg.sources.youtube.min_interval_minutes,
                ),
                twitter=TwitterSourceConfigOut(
                    enabled=cfg.sources.twitter.enabled,
                    mode=cfg.sources.twitter.mode,
                    cookie=_mask(tw_cookie),
                    cookie_env=cfg.sources.twitter.cookie_env,
                    daily_search_budget=cfg.sources.twitter.daily_search_budget,
                    daily_feed_budget=cfg.sources.twitter.daily_feed_budget,
                    daily_creator_budget=cfg.sources.twitter.daily_creator_budget,
                    request_interval_seconds=cfg.sources.twitter.request_interval_seconds,
                    min_interval_minutes=cfg.sources.twitter.min_interval_minutes,
                ),
                zhihu=ZhihuSourceConfigOut(
                    enabled=cfg.sources.zhihu.enabled,
                    source_modes=list(cfg.sources.zhihu.source_modes),
                    daily_search_budget=cfg.sources.zhihu.daily_search_budget,
                    daily_hot_budget=cfg.sources.zhihu.daily_hot_budget,
                    daily_feed_budget=cfg.sources.zhihu.daily_feed_budget,
                    daily_creator_budget=cfg.sources.zhihu.daily_creator_budget,
                    daily_related_budget=cfg.sources.zhihu.daily_related_budget,
                    request_interval_seconds=cfg.sources.zhihu.request_interval_seconds,
                    min_interval_minutes=cfg.sources.zhihu.min_interval_minutes,
                ),
            ),
            scheduler=SchedulerConfigOut(
                enabled=cfg.scheduler.enabled,
                pause_on_extension_disconnect=cfg.scheduler.pause_on_extension_disconnect,
                extension_disconnect_grace_seconds=cfg.scheduler.extension_disconnect_grace_seconds,
                discovery_cron=cfg.scheduler.discovery_cron,
                pool_target_count=cfg.scheduler.pool_target_count,
                pool_source_shares=dict(cfg.scheduler.pool_source_shares),
                account_sync_interval_hours=cfg.scheduler.account_sync_interval_hours,
                refresh_check_interval_seconds=cfg.scheduler.refresh_check_interval_seconds,
                signal_event_threshold=cfg.scheduler.signal_event_threshold,
                feedback_batch_threshold=cfg.scheduler.feedback_batch_threshold,
                trending_refresh_hours=cfg.scheduler.trending_refresh_hours,
                explore_refresh_hours=cfg.scheduler.explore_refresh_hours,
                discovery_limit=cfg.scheduler.discovery_limit,
                delight_queue_limit=cfg.scheduler.delight_queue_limit,
                proactive_push_interval_seconds=cfg.scheduler.proactive_push_interval_seconds,
                speculator_idle_interval_minutes=cfg.scheduler.speculator_idle_interval_minutes,
                speculation_interval_minutes=cfg.scheduler.speculation_interval_minutes,
                speculation_ttl_days=cfg.scheduler.speculation_ttl_days,
                speculation_cooldown_days=cfg.scheduler.speculation_cooldown_days,
                speculation_confirmation_threshold=(
                    cfg.scheduler.speculation_confirmation_threshold
                ),
                speculation_max_active=cfg.scheduler.speculation_max_active,
                speculation_max_primary_interests=(cfg.scheduler.speculation_max_primary_interests),
                speculation_max_secondary_interests=(
                    cfg.scheduler.speculation_max_secondary_interests
                ),
                avoidance_speculation_interval_minutes=(
                    cfg.scheduler.avoidance_speculation_interval_minutes
                ),
                avoidance_speculation_ttl_days=cfg.scheduler.avoidance_speculation_ttl_days,
                avoidance_speculation_cooldown_days=(
                    cfg.scheduler.avoidance_speculation_cooldown_days
                ),
                avoidance_speculation_confirmation_threshold=(
                    cfg.scheduler.avoidance_speculation_confirmation_threshold
                ),
                avoidance_speculation_max_active=cfg.scheduler.avoidance_speculation_max_active,
                auto_update_enabled=cfg.scheduler.auto_update_enabled,
                auto_update_check_interval_hours=cfg.scheduler.auto_update_check_interval_hours,
                auto_update_allow_prerelease=cfg.scheduler.auto_update_allow_prerelease,
                auto_update_allowed_remotes=list(cfg.scheduler.auto_update_allowed_remotes),
                rss_subscriptions=list(cfg.scheduler.rss_subscriptions),
                xiaoyuzhou_subscriptions=list(cfg.scheduler.xiaoyuzhou_subscriptions),
                wechat_subscriptions=list(cfg.scheduler.wechat_subscriptions),
            ),
            discovery=DiscoveryConfigOut(
                unified_keyword_planner_enabled=cfg.discovery.unified_keyword_planner_enabled,
                kw_cache_high=cfg.discovery.kw_cache_high,
                kw_cache_low=cfg.discovery.kw_cache_low,
                gen_batch=cfg.discovery.gen_batch,
                fetch_batch=cfg.discovery.fetch_batch,
                history_window_size=cfg.discovery.history_window_size,
                history_window_hours=cfg.discovery.history_window_hours,
                claim_lease_minutes=cfg.discovery.claim_lease_minutes,
                planner_poll_seconds=cfg.discovery.planner_poll_seconds,
                plan_ttl_hours=cfg.discovery.plan_ttl_hours,
                admission_min_score=cfg.discovery.admission_min_score,
                multimodal_evaluation_enabled=cfg.discovery.multimodal_evaluation_enabled,
                multimodal_batch_size=cfg.discovery.multimodal_batch_size,
                multimodal_image_max_px=cfg.discovery.multimodal_image_max_px,
                multimodal_image_quality=cfg.discovery.multimodal_image_quality,
                multimodal_image_timeout_seconds=(cfg.discovery.multimodal_image_timeout_seconds),
            ),
            autostart=AutostartConfigOut(
                enabled=cfg.autostart.enabled,
                manage_ollama=cfg.autostart.manage_ollama,
            ),
            storage=StorageConfigOut(
                db_path=cfg.storage.db_path,
                interview_db_path=cfg.storage.interview_db_path,
            ),
            logging=LoggingConfigOut(
                level=cfg.logging.level,
                file_level=cfg.logging.file_level,
                directory=cfg.logging.directory,
                filename=cfg.logging.filename,
                file_path=str(cfg.logging.file_path),
                max_file_size_mb=cfg.logging.max_file_size_mb,
                backup_count=cfg.logging.backup_count,
                aggregate_budget_mb=cfg.logging.aggregate_budget_mb,
                unmanaged_truncate_mb=cfg.logging.unmanaged_truncate_mb,
                unmanaged_max_age_days=cfg.logging.unmanaged_max_age_days,
            ),
            issues=issue_list,
        )

    @app.get("/api/config", response_model=ConfigResponse)
    def get_config(reveal_keys: bool = False) -> ConfigResponse:
        """Return the current configuration (API keys masked by default)."""
        from openbiliclaw.config import (
            _collect_config_issues,
            load_config,
        )

        cfg = load_config()
        issues = list(_collect_config_issues(cfg))
        if bool(getattr(ctx, "degraded", False)):
            issues.extend(getattr(ctx, "degraded_issues", []))
        return _config_to_response(
            cfg,
            issues,
            mask_keys=not reveal_keys,
            degraded=bool(getattr(ctx, "degraded", False)),
            degraded_reason=str(getattr(ctx, "degraded_reason", "")),
        )

    def _as_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _apply_llm_update(cfg: Any, llm_data: object) -> None:
        """Apply the LLM subset of a config update to an in-memory config."""
        if not isinstance(llm_data, dict):
            return
        from openbiliclaw.config import (
            LLM_PROVIDER_NAMES,
            _normalize_llm_concurrency,
            _normalize_llm_timeout,
        )

        if "default_provider" in llm_data:
            cfg.llm.default_provider = str(llm_data["default_provider"])
        if "concurrency" in llm_data:
            cfg.llm.concurrency = _normalize_llm_concurrency(llm_data["concurrency"])
        if "timeout" in llm_data:
            cfg.llm.timeout = _normalize_llm_timeout(llm_data["timeout"])
        if "fallback_enabled" in llm_data:
            cfg.llm.fallback_enabled = _as_bool(llm_data["fallback_enabled"])
        if "fallback_provider" in llm_data:
            cfg.llm.fallback_provider = str(llm_data["fallback_provider"]).strip()
        for provider_name in LLM_PROVIDER_NAMES:
            if provider_name in llm_data and isinstance(llm_data[provider_name], dict):
                provider_cfg = getattr(cfg.llm, provider_name)
                pdata = llm_data[provider_name]
                skipped_fields: list[str] = []
                for field_name in (
                    "api_key",
                    "model",
                    "base_url",
                    "auth_mode",
                    "http_referer",
                    "x_title",
                    "reasoning_effort",
                ):
                    if field_name in pdata:
                        new_value = str(pdata[field_name])
                        if field_name == "api_key" and "*" in new_value:
                            skipped_fields.append(f"{field_name}=masked")
                            continue
                        existing = getattr(provider_cfg, field_name, "")
                        if (
                            field_name not in {"auth_mode", "reasoning_effort"}
                            and not new_value.strip()
                            and isinstance(existing, str)
                            and existing.strip()
                        ):
                            skipped_fields.append(f"{field_name}=empty_skip")
                            continue
                        setattr(provider_cfg, field_name, new_value)
                if skipped_fields:
                    logger.debug(
                        "Config LLM update: provider %s skipped fields: %s",
                        provider_name,
                        ", ".join(skipped_fields),
                    )
        if "embedding" in llm_data and isinstance(llm_data["embedding"], dict):
            emb = llm_data["embedding"]
            if "provider" in emb:
                cfg.llm.embedding.provider = str(emb["provider"])
            if "model" in emb:
                new_model = str(emb["model"])
                if new_model.strip() or not cfg.llm.embedding.model.strip():
                    cfg.llm.embedding.model = new_model
            if "api_key" in emb:
                new_key = str(emb["api_key"])
                if "*" not in new_key and (
                    new_key.strip() or not cfg.llm.embedding.api_key.strip()
                ):
                    cfg.llm.embedding.api_key = new_key
            if "base_url" in emb:
                new_base_url = str(emb["base_url"])
                if new_base_url.strip() or not cfg.llm.embedding.base_url.strip():
                    cfg.llm.embedding.base_url = new_base_url
            if "output_dimensionality" in emb:
                try:
                    cfg.llm.embedding.output_dimensionality = max(
                        0,
                        int(emb["output_dimensionality"] or 0),
                    )
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="llm.embedding.output_dimensionality must be an integer",
                    ) from exc
            if "similarity_threshold" in emb:
                cfg.llm.embedding.similarity_threshold = float(emb["similarity_threshold"])
            if "fallback_enabled" in emb:
                cfg.llm.embedding.fallback_enabled = _as_bool(emb["fallback_enabled"])
            if "fallback_provider" in emb:
                cfg.llm.embedding.fallback_provider = str(emb["fallback_provider"]).strip()
        for module_name in ("soul", "discovery", "recommendation", "evaluation"):
            if module_name in llm_data and isinstance(llm_data[module_name], dict):
                mod_cfg = getattr(cfg.llm, module_name)
                mdata = llm_data[module_name]
                if "provider" in mdata:
                    mod_cfg.provider = str(mdata["provider"])
                if "model" in mdata:
                    mod_cfg.model = str(mdata["model"])

    # ── Service probe（已提取到 probe_routes.py）──
    from openbiliclaw.api.probe_routes import register_probe_routes

    register_probe_routes(app, apply_llm_update=_apply_llm_update)

    @app.put("/api/config", response_model=ConfigUpdateResponse)
    async def update_config(payload: ConfigUpdateIn) -> ConfigUpdateResponse | JSONResponse:
        """Update configuration, persist to config.toml, and hot-reload runtime.

        Only the fields included in the request body are modified.
        After persisting, the backend attempts to rebuild all swappable
        runtime components so the new settings take effect immediately.
        """
        from openbiliclaw.config import (
            _DEFAULT_ADMISSION_MIN_SCORE,
            _DEFAULT_DELIGHT_QUEUE_LIMIT,
            _DEFAULT_DISCOVERY_LIMIT,
            _DEFAULT_EXPLORE_REFRESH_HOURS,
            _DEFAULT_FEEDBACK_BATCH_THRESHOLD,
            _DEFAULT_MULTIMODAL_BATCH_SIZE,
            _DEFAULT_MULTIMODAL_IMAGE_MAX_PX,
            _DEFAULT_MULTIMODAL_IMAGE_QUALITY,
            _DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS,
            _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
            _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
            _DEFAULT_SIGNAL_EVENT_THRESHOLD,
            _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
            _DEFAULT_TRENDING_REFRESH_HOURS,
            _collect_config_issues,
            _default_config_path,
            _normalize_extension_disconnect_grace,
            _normalize_pool_source_shares,
            _normalize_probability,
            _normalize_scheduler_int,
            load_config,
            save_config,
        )

        cfg = load_config()
        update = payload.model_dump(exclude_none=True)
        reset_fields = [str(field) for field in update.pop("reset_fields", [])]
        suppress_background_llm_work = bool(update.pop("suppress_background_llm_work", False))
        unknown_reset_fields = [
            field for field in reset_fields if field not in _RESETTABLE_CONFIG_FIELDS
        ]
        if unknown_reset_fields:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_reset_fields",
                    "fields": unknown_reset_fields,
                },
            )

        # Apply top-level scalars
        if "language" in update:
            cfg.language = str(update["language"])
        if "data_dir" in update:
            cfg.data_dir = str(update["data_dir"])

        # Apply LLM updates
        if "llm" in update:
            _apply_llm_update(cfg, update["llm"])

        # A masked GET echo looks like ``SESS****abcd`` — a long asterisk run
        # never appears in a genuine Cookie header, so use it (not a single
        # ``*``, which cookie values may legally contain) to detect echoes.
        def _is_masked_echo(value: str) -> bool:
            return "****" in value

        # Apply bilibili updates
        if "bilibili" in update:
            bdata = update["bilibili"]
            if "auth_method" in bdata:
                cfg.bilibili.auth_method = str(bdata["auth_method"])
            if "cookie" in bdata:
                # Mirror the api_key guards: never persist a masked echo, and
                # an empty field never wipes an existing cookie (the browser
                # extension's auto-sync owns refresh; a blank textarea on save
                # must not log the backend out).
                new_cookie = str(bdata["cookie"])
                if not _is_masked_echo(new_cookie) and (
                    new_cookie.strip() or not cfg.bilibili.cookie.strip()
                ):
                    cfg.bilibili.cookie = new_cookie
            if "browser_executable" in bdata:
                cfg.bilibili.browser_executable = str(bdata["browser_executable"])
            if "browser_headed" in bdata:
                cfg.bilibili.browser_headed = _as_bool(bdata["browser_headed"])

        # Apply source updates
        if "sources" in update:
            sources_data = update["sources"]
            if isinstance(sources_data, dict):
                browser_data = sources_data.get("browser")
                if isinstance(browser_data, dict):
                    if "cdp_url" in browser_data:
                        cfg.sources.browser_cdp_url = str(browser_data["cdp_url"])
                    if "headed" in browser_data:
                        cfg.sources.browser_headed = _as_bool(browser_data["headed"])

                bilibili_data = sources_data.get("bilibili")
                if isinstance(bilibili_data, dict) and "enabled" in bilibili_data:
                    cfg.sources.bilibili.enabled = _as_bool(bilibili_data["enabled"])

                xhs_data = sources_data.get("xiaohongshu")
                if isinstance(xhs_data, dict):
                    if "enabled" in xhs_data:
                        cfg.sources.xiaohongshu.enabled = _as_bool(xhs_data["enabled"])
                    for key in (
                        "daily_search_budget",
                        "daily_creator_budget",
                        "task_interval_seconds",
                    ):
                        if key in xhs_data:
                            setattr(cfg.sources.xiaohongshu, key, int(xhs_data[key]))

                dy_data = sources_data.get("douyin")
                if isinstance(dy_data, dict):
                    if "enabled" in dy_data:
                        cfg.sources.douyin.enabled = _as_bool(dy_data["enabled"])
                    if "mode" in dy_data:
                        cfg.sources.douyin.mode = str(dy_data["mode"])
                    if "cookie_env" in dy_data:
                        # The env var name has a sensible default; an emptied
                        # field keeps the current name rather than wiping it.
                        new_env = str(dy_data["cookie_env"]).strip()
                        if new_env:
                            cfg.sources.douyin.cookie_env = new_env
                    if "cookie" in dy_data:
                        # Manual paste from the settings pages. Routed to
                        # data/douyin_cookie.json (same store the extension
                        # auto-sync writes) — secrets never land in config.toml.
                        from openbiliclaw.sources.douyin_auth import (
                            DouyinCookieManager,
                            resolve_douyin_cookie,
                        )

                        new_cookie = str(dy_data["cookie"]).strip()
                        if new_cookie and not _is_masked_echo(new_cookie):
                            current = ""
                            with suppress(Exception):
                                current = resolve_douyin_cookie(
                                    data_dir=cfg.data_path,
                                    cookie_env=cfg.sources.douyin.cookie_env,
                                )
                            # Unchanged form echo → no write (env override
                            # must not be copied into the file needlessly).
                            if new_cookie != current:
                                DouyinCookieManager(cfg.data_path).set_cookie(
                                    new_cookie, source="config-update"
                                )
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_feed_budget",
                        "request_interval_seconds",
                    ):
                        if key in dy_data:
                            setattr(cfg.sources.douyin, key, int(dy_data[key]))

                yt_data = sources_data.get("youtube")
                if isinstance(yt_data, dict):
                    if "enabled" in yt_data:
                        cfg.sources.youtube.enabled = _as_bool(yt_data["enabled"])
                    for key in (
                        "daily_search_budget",
                        "daily_trending_budget",
                        "daily_channel_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in yt_data:
                            setattr(cfg.sources.youtube, key, int(yt_data[key]))

                tw_data = sources_data.get("twitter")
                if isinstance(tw_data, dict):
                    if "enabled" in tw_data:
                        cfg.sources.twitter.enabled = _as_bool(tw_data["enabled"])
                    if "mode" in tw_data:
                        cfg.sources.twitter.mode = str(tw_data["mode"])
                    if "cookie_env" in tw_data:
                        new_env = str(tw_data["cookie_env"]).strip()
                        if new_env:
                            cfg.sources.twitter.cookie_env = new_env
                    if "cookie" in tw_data:
                        # Manual paste — routed to data/x_cookie.json like the
                        # extension auto-sync; never lands in config.toml.
                        new_cookie = str(tw_data["cookie"]).strip()
                        if new_cookie and not _is_masked_echo(new_cookie):
                            current = ""
                            with suppress(Exception):
                                current = resolve_x_cookie(
                                    data_dir=cfg.data_path,
                                    cookie_env=cfg.sources.twitter.cookie_env,
                                )
                            if new_cookie != current:
                                XCookieManager(cfg.data_path).set_cookie(
                                    new_cookie, source="config-update"
                                )
                                # A pasted valid cookie is a re-login signal,
                                # same as the extension sync endpoint: lift any
                                # missing/expired/blocked health block so
                                # discovery retries instead of staying parked.
                                from openbiliclaw.sources.douyin_direct import (
                                    parse_cookie_header,
                                )

                                pairs = parse_cookie_header(new_cookie)
                                if all(
                                    name in pairs for name in _get_x_required_cookie_names()
                                ) and hasattr(ctx.database, "conn"):
                                    with suppress(Exception):
                                        from openbiliclaw.storage.x_health import (
                                            XSourceHealthStore,
                                        )

                                        XSourceHealthStore(ctx.database).clear_relogin_block()
                    for key in (
                        "daily_search_budget",
                        "daily_feed_budget",
                        "daily_creator_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in tw_data:
                            setattr(cfg.sources.twitter, key, int(tw_data[key]))

                zh_data = sources_data.get("zhihu")
                if isinstance(zh_data, dict):
                    if "enabled" in zh_data:
                        cfg.sources.zhihu.enabled = _as_bool(zh_data["enabled"])
                    if "source_modes" in zh_data:
                        raw_modes = zh_data["source_modes"]
                        if isinstance(raw_modes, str):
                            modes = [part.strip() for part in raw_modes.split(",")]
                        elif isinstance(raw_modes, list):
                            modes = [str(part).strip() for part in raw_modes]
                        else:
                            modes = []
                        selected = [
                            mode
                            for mode in modes
                            if mode in {"search", "hot", "feed", "creator", "related"}
                        ]
                        if selected:
                            cfg.sources.zhihu.source_modes = tuple(dict.fromkeys(selected))
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_feed_budget",
                        "daily_creator_budget",
                        "daily_related_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in zh_data:
                            setattr(cfg.sources.zhihu, key, int(zh_data[key]))

        # Apply scheduler updates
        if "scheduler" in update:
            sdata = update["scheduler"]
            scheduler_int_limits = {
                "refresh_check_interval_seconds": (
                    _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
                    15,
                    None,
                ),
                "signal_event_threshold": (_DEFAULT_SIGNAL_EVENT_THRESHOLD, 1, None),
                "trending_refresh_hours": (_DEFAULT_TRENDING_REFRESH_HOURS, 1, None),
                "explore_refresh_hours": (_DEFAULT_EXPLORE_REFRESH_HOURS, 1, None),
                "discovery_limit": (_DEFAULT_DISCOVERY_LIMIT, 1, 60),
                "delight_queue_limit": (_DEFAULT_DELIGHT_QUEUE_LIMIT, 1, 100),
                "proactive_push_interval_seconds": (
                    _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
                    30,
                    None,
                ),
                "speculator_idle_interval_minutes": (
                    _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
                    5,
                    None,
                ),
                "feedback_batch_threshold": (
                    _DEFAULT_FEEDBACK_BATCH_THRESHOLD,
                    1,
                    None,
                ),
                "avoidance_speculation_interval_minutes": (10, 1, None),
                "avoidance_speculation_ttl_days": (3, 1, None),
                "avoidance_speculation_cooldown_days": (7, 1, None),
                "avoidance_speculation_confirmation_threshold": (3, 1, None),
                "avoidance_speculation_max_active": (5, 1, None),
            }
            for key in (
                "enabled",
                "pause_on_extension_disconnect",
                "extension_disconnect_grace_seconds",
                "discovery_cron",
                "pool_target_count",
                "account_sync_interval_hours",
                "refresh_check_interval_seconds",
                "signal_event_threshold",
                "trending_refresh_hours",
                "explore_refresh_hours",
                "discovery_limit",
                "delight_queue_limit",
                "proactive_push_interval_seconds",
                "speculator_idle_interval_minutes",
                "speculation_interval_minutes",
                "speculation_ttl_days",
                "speculation_cooldown_days",
                "speculation_confirmation_threshold",
                "speculation_max_active",
                "speculation_max_primary_interests",
                "speculation_max_secondary_interests",
                "avoidance_speculation_interval_minutes",
                "avoidance_speculation_ttl_days",
                "avoidance_speculation_cooldown_days",
                "avoidance_speculation_confirmation_threshold",
                "avoidance_speculation_max_active",
                "auto_update_enabled",
                "auto_update_check_interval_hours",
                "auto_update_allow_prerelease",
                "auto_update_allowed_remotes",
                "feedback_batch_threshold",
            ):
                if key in sdata:
                    current_val = getattr(cfg.scheduler, key)
                    if key == "auto_update_allowed_remotes":
                        next_remotes = _string_list(sdata[key])
                        if next_remotes:
                            setattr(cfg.scheduler, key, next_remotes)
                    elif key == "extension_disconnect_grace_seconds":
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_extension_disconnect_grace(sdata[key]),
                        )
                    elif key in scheduler_int_limits:
                        default, min_value, max_value = scheduler_int_limits[key]
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_scheduler_int(
                                sdata[key],
                                default=default,
                                min_value=min_value,
                                max_value=max_value,
                            ),
                        )
                    elif isinstance(current_val, bool):
                        setattr(cfg.scheduler, key, _as_bool(sdata[key]))
                    elif isinstance(current_val, int):
                        setattr(cfg.scheduler, key, int(sdata[key]))
                    else:
                        setattr(cfg.scheduler, key, str(sdata[key]))
            if "pool_source_shares" in sdata:
                cfg.scheduler.pool_source_shares = _normalize_pool_source_shares(
                    sdata["pool_source_shares"]
                )
            # Update subscription lists (rss, xiaoyuzhou, wechat)
            if "rss_subscriptions" in sdata:
                cfg.scheduler.rss_subscriptions = list(sdata["rss_subscriptions"])
            if "xiaoyuzhou_subscriptions" in sdata:
                cfg.scheduler.xiaoyuzhou_subscriptions = list(sdata["xiaoyuzhou_subscriptions"])
            if "wechat_subscriptions" in sdata:
                cfg.scheduler.wechat_subscriptions = list(sdata["wechat_subscriptions"])

        # Apply discovery planner / evaluator updates
        if "discovery" in update:
            ddata = update["discovery"]
            if isinstance(ddata, dict):
                discovery_int_limits = {
                    "multimodal_batch_size": (
                        _DEFAULT_MULTIMODAL_BATCH_SIZE,
                        1,
                        12,
                    ),
                    "multimodal_image_max_px": (
                        _DEFAULT_MULTIMODAL_IMAGE_MAX_PX,
                        128,
                        768,
                    ),
                    "multimodal_image_quality": (
                        _DEFAULT_MULTIMODAL_IMAGE_QUALITY,
                        40,
                        90,
                    ),
                    "multimodal_image_timeout_seconds": (
                        _DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS,
                        1,
                        20,
                    ),
                }
                if "multimodal_evaluation_enabled" in ddata:
                    cfg.discovery.multimodal_evaluation_enabled = _as_bool(
                        ddata["multimodal_evaluation_enabled"]
                    )
                if "admission_min_score" in ddata:
                    cfg.discovery.admission_min_score = _normalize_probability(
                        ddata["admission_min_score"],
                        default=_DEFAULT_ADMISSION_MIN_SCORE,
                    )
                for key, (default, min_value, max_value) in discovery_int_limits.items():
                    if key in ddata:
                        setattr(
                            cfg.discovery,
                            key,
                            _normalize_scheduler_int(
                                ddata[key],
                                default=default,
                                min_value=min_value,
                                max_value=max_value,
                            ),
                        )

        # Apply storage updates
        if "storage" in update:
            stdata = update["storage"]
            if "db_path" in stdata:
                cfg.storage.db_path = str(stdata["db_path"])

        # Apply logging updates
        if "logging" in update:
            ldata = update["logging"]
            for key in ("level", "file_level", "directory", "filename"):
                if key in ldata:
                    setattr(cfg.logging, key, str(ldata[key]))
            for key in (
                "max_file_size_mb",
                "backup_count",
                "aggregate_budget_mb",
                "unmanaged_truncate_mb",
                "unmanaged_max_age_days",
            ):
                if key in ldata:
                    setattr(cfg.logging, key, int(ldata[key]))

        for field in reset_fields:
            target = _RESETTABLE_CONFIG_FIELDS[field]
            section = getattr(cfg, target[0])
            subsection = getattr(section, target[1])
            setattr(subsection, target[2], "")

        issues = _validate_llm_buildable(cfg, _collect_config_issues(cfg))
        if any(getattr(issue, "severity", "warning") == "blocking" for issue in issues):
            response = ConfigUpdateResponse(
                ok=False,
                config=_config_to_response(
                    cfg,
                    issues,
                    mask_keys=True,
                    degraded=bool(getattr(ctx, "degraded", False)),
                    degraded_reason=str(getattr(ctx, "degraded_reason", "")),
                ),
                message="配置校验失败，未写入 config.toml。",
                reloaded=False,
                rollback_applied=False,
                restart_required=False,
            )
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json"),
            )

        async with config_save_lock:
            # gui-init D1 / spec §5b: re-check inside the lock. The middleware
            # gated this path on init_active before the handler ran, but a run
            # could have been reserved in between; saving + rebuilding config
            # mid-init would swap components the run is using.
            if init_active_now():
                return JSONResponse(
                    {"error": "init_running", "detail": "初始化进行中，请稍后再保存配置"},
                    status_code=409,
                )
            config_path = _default_config_path()
            try:
                backup_path = _snapshot_config_file(config_path)
            except Exception as exc:
                logger.exception("Config snapshot failed — refusing to overwrite config.toml")
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "config_snapshot_failed",
                        "message": f"couldn't snapshot config, refusing to risk overwrite: {exc}",
                    },
                )

            saved_path = save_config(cfg)
            logger.info("Configuration saved to %s", saved_path)

            if bool(getattr(ctx, "degraded", False)):
                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(
                        cfg,
                        issues,
                        mask_keys=True,
                        degraded=True,
                        degraded_reason=str(getattr(ctx, "degraded_reason", "")),
                    ),
                    message=(
                        f"配置已保存到 {saved_path}。当前后端处于降级模式，"
                        "请 restart daemon 后让新配置生效。"
                    ),
                    reloaded=False,
                    rollback_applied=False,
                    restart_required=True,
                )

            # ── Hot-reload: rebuild runtime components ──────────────
            reload_message = f"配置已保存到 {saved_path}。"
            try:
                await ctx.rebuild_from_config(cfg)
                await ctx.restart_background_tasks(
                    app,
                    run_post_reload_llm_work=not suppress_background_llm_work,
                )
                reload_message += " 运行时组件已热重载，新配置立即生效。"
                logger.info("Config hot-reload succeeded")
                # Notify WebSocket subscribers so the extension re-fetches data
                with suppress(Exception):
                    await ctx.event_hub.publish(
                        {
                            "type": "config_reloaded",
                            "message": "配置已热重载，运行时组件已重建。",
                        }
                    )
                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(cfg, issues, mask_keys=True),
                    message=reload_message,
                    reloaded=True,
                    rollback_applied=False,
                    restart_required=False,
                )
            except Exception as exc:
                logger.exception("Config hot-reload failed — attempting config rollback")
                if backup_path is None:
                    rollback_message = (
                        f" 热重载失败（{str(exc)[:200]}），未找到可回滚的 config.toml.bak。"
                    )
                    rollback_cfg = cfg
                    rollback_applied = False
                else:
                    try:
                        _restore_config_snapshot(backup_path, saved_path)
                    except Exception as restore_exc:
                        logger.critical(
                            "Config rollback failed after hot-reload exception",
                            exc_info=True,
                        )
                        return JSONResponse(
                            status_code=500,
                            content={
                                "error": "config_persistence_corrupted",
                                "message": (
                                    "config.toml may be in inconsistent state after hot-reload "
                                    f"failure and rollback failure: {restore_exc}"
                                ),
                                "manual_recovery": (
                                    "config.toml may be in inconsistent state; if "
                                    "config.toml.bak exists, manually copy it back."
                                ),
                            },
                        )
                    rollback_cfg = load_config(saved_path)
                    rollback_message = (
                        f" 热重载失败（{str(exc)[:200]}），已从 config.toml.bak 回滚。"
                    )
                    rollback_applied = True

                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(rollback_cfg, _collect_config_issues(rollback_cfg)),
                    message=reload_message + rollback_message,
                    reloaded=False,
                    rollback_applied=rollback_applied,
                    restart_required=False,
                )

    def _normalize_enabled_sources_override(
        raw_enabled: dict[str, bool] | None,
        fallback: dict[str, bool],
    ) -> dict[str, bool]:
        if raw_enabled is None:
            return fallback
        enabled: dict[str, bool] = {}
        for source in _get_source_share_order():
            enabled[source] = bool(raw_enabled.get(source, fallback.get(source, False)))
        return {source: enabled.get(source, False) for source in _get_source_share_order()}

    def _build_source_share_suggestion_response(
        payload: SourceShareSuggestionIn | None = None,
    ) -> SourceShareSuggestionResponse:
        """Suggest pool source shares from observed platform event counts."""
        from openbiliclaw.config import load_config
        from openbiliclaw.runtime.source_policy import (
            source_enabled_map,
            suggest_pool_source_shares,
        )

        cfg = load_config()
        event_counts = _get_count_events_by_source_platform(ctx.database)
        enabled_sources = _normalize_enabled_sources_override(
            payload.enabled_sources if payload else None,
            source_enabled_map(cfg),
        )
        suggested_shares = suggest_pool_source_shares(
            event_counts,
            enabled_sources=enabled_sources,
            configured_shares=(
                payload.configured_shares
                if payload and payload.configured_shares is not None
                else cfg.scheduler.pool_source_shares
            ),
        )
        return SourceShareSuggestionResponse(
            event_counts=event_counts,
            enabled_sources=enabled_sources,
            suggested_shares=suggested_shares,
        )

    @app.get(
        "/api/config/source-share-suggestion",
        response_model=SourceShareSuggestionResponse,
    )
    def source_share_suggestion() -> SourceShareSuggestionResponse:
        """Suggest pool source shares from saved config switches."""
        return _build_source_share_suggestion_response()

    @app.post(
        "/api/config/source-share-suggestion",
        response_model=SourceShareSuggestionResponse,
    )
    def source_share_suggestion_for_form(
        payload: SourceShareSuggestionIn,
    ) -> SourceShareSuggestionResponse:
        """Suggest pool source shares from unsaved settings form state."""
        return _build_source_share_suggestion_response(payload)

    @app.post(
        "/api/config/discover-models",
        response_model=ConfigModelDiscoveryResponse,
    )
    async def discover_config_models(
        payload: ConfigModelDiscoveryIn,
    ) -> ConfigModelDiscoveryResponse:
        """List the model ids the submitted endpoint advertises.

        Backs the setup wizard's 「获取模型」 button. Reads nothing from and
        writes nothing to ``config.toml`` — the wizard asks this *before* its
        first successful save, so the credentials only exist in the form.

        Blank ``api_key`` / ``base_url`` fall back to the persisted
        ``[llm.<provider>]`` block: on a relaunch the wizard shows a masked key
        and promises "留空则沿用当前 Key", so re-pasting it just to enumerate
        models would be a needless step.

        Failures come back as ``ok=False`` with a rendered-inline message
        rather than a 4xx, because the model field stays hand-editable and an
        unreachable endpoint must not look like a broken wizard.
        """
        from openbiliclaw.config import load_config
        from openbiliclaw.llm.model_discovery import (
            REASONING_EFFORT_SUGGESTIONS,
            discover_models,
        )

        provider = str(payload.provider_type or "").strip().lower()
        api_key = str(payload.api_key or "").strip()
        base_url = str(payload.base_url or "").strip()
        if not api_key or not base_url:
            saved = getattr(ctx.config or load_config(), "llm", None)
            provider_cfg = getattr(saved, provider, None) if saved is not None else None
            if provider_cfg is not None:
                if not api_key:
                    api_key = str(getattr(provider_cfg, "api_key", "") or "").strip()
                if not base_url:
                    base_url = str(getattr(provider_cfg, "base_url", "") or "").strip()

        result = await discover_models(
            provider_type=provider,
            api_key=api_key,
            base_url=base_url,
            auth_mode=payload.auth_mode,
        )
        return ConfigModelDiscoveryResponse(
            ok=result.ok,
            models=list(result.models),
            reasoning_efforts=list(REASONING_EFFORT_SUGGESTIONS),
            error=result.error,
        )
