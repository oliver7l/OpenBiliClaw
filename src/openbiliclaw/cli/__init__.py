"""CLI interface for OpenBiliClaw.

Provides the command-line entry point using Typer.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import typer
from rich.panel import Panel
from rich.table import Table

# K6：guided-init 管线已迁至 runtime/init_flow.py，此处 re-import 保持
# 既有调用方（cli 命令实现、外部 from openbiliclaw.cli import ...）不变。
from openbiliclaw.runtime.init_flow import (  # noqa: F401
    _DEFAULT_DY_BOOTSTRAP_DEDUPE_HOURS,
    _DEFAULT_DY_BOOTSTRAP_WAIT_SECONDS,
    _DEFAULT_XHS_BOOTSTRAP_DEDUPE_HOURS,
    _DEFAULT_XHS_BOOTSTRAP_WAIT_SECONDS,
    _DEFAULT_YT_BOOTSTRAP_DEDUPE_HOURS,
    _DEFAULT_YT_BOOTSTRAP_WAIT_SECONDS,
    _DEFAULT_ZHIHU_BOOTSTRAP_DEDUPE_HOURS,
    _DEFAULT_ZHIHU_BOOTSTRAP_WAIT_SECONDS,
    _INIT_BILIBILI_FAVORITE_LIMIT,
    _INIT_BILIBILI_FOLLOW_LIMIT,
    _INIT_BILIBILI_HISTORY_LIMIT,
    _INIT_BOOTSTRAP_MAX_ITEMS_PER_SCOPE,
    _INIT_POOL_TARGET_COUNT,
    _INIT_X_BOOKMARKS_LIMIT,
    _INIT_X_LIKES_LIMIT,
    _RUNTIME_COMPONENTS,
    GuidedInitError,
    InitResult,
    _build_draft_profile_for_discover,
    _collect_dy_bootstrap_events,
    _collect_xhs_bootstrap_events,
    _collect_yt_bootstrap_events,
    _collect_zhihu_bootstrap_events,
    _dy_bootstrap_dedupe_hours,
    _dy_events_to_history_items,
    _enqueue_dy_bootstrap_task,
    _enqueue_xhs_bootstrap_task,
    _enqueue_yt_bootstrap_task,
    _enqueue_zhihu_bootstrap_task,
    _fetch_bilibili_init_data,
    _fetch_x_init_data,
    _format_source_shares,
    _get_runtime_database,
    _history_item_to_event,
    _is_interactive_terminal,
    _kick_task_dispatcher,
    _maybe_update_init_source_shares,
    _merge_source_shares,
    _parse_source_share_input,
    _print_section_title,
    _run_with_progress,
    _select_init_source_shares,
    _x_events_to_history_items,
    _x_tweet_to_event,
    _xhs_bootstrap_dedupe_hours,
    _xhs_events_to_history_items,
    _yt_bootstrap_dedupe_hours,
    _yt_events_to_history_items,
    _zhihu_bootstrap_dedupe_hours,
    _zhihu_events_to_history_items,
    console,
    run_guided_init,
)
from openbiliclaw.runtime.ollama_supervisor import (
    _ollama_is_running,
    _ollama_start_serve_background,
    effective_ollama_endpoint,
    is_loopback,
    ollama_required,
)


def _strip_proxy_env() -> None:
    """Drop any inherited HTTP(S) proxy env vars from the running process.

    All configured outbound endpoints (Bilibili, sensenova, deepseek,
    Douyin, image CDNs) are reachable directly from this host. The macOS
    system proxy (127.0.0.1:7890, e.g. Clash) is restarted often and its
    downtime takes down every outbound request. Clearing the vars here —
    inside the Python process, before any httpx client is constructed —
    guarantees direct connectivity regardless of how the process was
    launched (PM2 re-injects env that a wrapper ``unset`` cannot always
    reach). httpx clients that already pass ``trust_env=False`` are doubly
    safe; this also covers SDKs (e.g. google-genai) that only honour env.
    """
    for _var in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "FTP_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "ftp_proxy",
        "no_proxy",
    ):
        os.environ.pop(_var, None)


_strip_proxy_env()


def _force_utf8_stdout_on_windows() -> None:
    """Reconfigure stdout/stderr to UTF-8 on Windows.

    Why: simplified-Chinese Windows defaults the console to GBK (cp936).
    Any emoji in our CLI output (e.g. ``⏱`` in the init banner, ``🦀``
    in the typer help text) raises UnicodeEncodeError as soon as the
    output stream tries to encode it. Users see the program crash with
    no useful message.

    Fix: force sys.stdout / sys.stderr into UTF-8 mode at import time,
    with ``errors='replace'`` as a final safety net so a stray
    untranslatable byte degrades to '?' instead of crashing the run.
    Idempotent + a no-op on POSIX (``reconfigure`` is a Python 3.7+
    method on TextIOWrapper that just rewires the codec).
    """
    if os.name != "nt":
        return
    # PYTHONUTF8=1 is the cleanest fix but only takes effect at process
    # start, not at module import — set it for any child processes we
    # spawn (subprocess calls inside the CLI inherit this).
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdout_on_windows()


app = typer.Typer(
    name="openbiliclaw",
    help="🦀 OpenBiliClaw — 你的 B 站专属 AI 朋友",
    add_completion=False,
)
auth_app = typer.Typer(help="B 站认证命令")
login_app = typer.Typer(help="账号登录命令")
browser_app = typer.Typer(help="agent-browser 浏览器命令")
app.add_typer(auth_app, name="auth")
app.add_typer(login_app, name="login")
app.add_typer(browser_app, name="browser")
# 命令组已抽至子模块（P4 第二/四刀）；这些模块顶层不 import 本包，
# 此处顶层导入无循环依赖。
from openbiliclaw.cli._cmd_autostart import (  # noqa: E402
    _format_autostart_config_status,
    autostart_app,
)
from openbiliclaw.cli._cmd_notes import note_app  # noqa: E402,F401

app.add_typer(autostart_app, name="autostart")
app.add_typer(note_app, name="note")
# fetch-* / discover-* 平铺命令组已抽至 cli/_cmd_fetch.py（P4 第五刀）；
# 该模块顶层不 import 本包，此处顶层注册无循环依赖。
from openbiliclaw.cli._cmd_fetch import (  # noqa: E402,F401
    _normalize_douyin_discovery_sources,  # re-export：tests/cli 直引
    _run_douyin_discovery,  # re-export：测试 patch 补丁点
    _run_single_source_bootstrap,  # re-export：本模块内 fetch-douyin/xhs 调用点
    _run_xhs_discovery,  # re-export：测试 patch 补丁点
    _run_zhihu_discovery,  # re-export：测试 patch cli_module._run_zhihu_discovery 的补丁点
)
from openbiliclaw.cli._cmd_fetch import register as _register_fetch_commands  # noqa: E402

_register_fetch_commands(app)

# cost / logs-prune 命令已抽至 cli/_cmd_usage.py（P4 第三刀）；register()
# 模式与 knowledge_forge 等外部命令组一致。
from openbiliclaw.cli._cmd_usage import register as _register_usage  # noqa: E402,F401

_register_usage(app)

# init 引导命令组已抽至 cli/_cmd_init.py（P4 第六刀）；该模块顶层不 import
# 本包，此处顶层注册无循环依赖。re-export 的 9 个符号：4 个是测试
# patch 到 cli 命名空间的补丁点，5 个是 tests/cli 直引 / 本文件其它命令
# （rebuild-profile）仍在调用。
from openbiliclaw.cli._cmd_init import (  # noqa: E402,F401
    _ask_dy_inclusion,  # re-export：tests/cli 直引
    _ask_network_binding,  # re-export：测试 patch 补丁点
    _ask_xhs_inclusion,  # re-export：tests/cli 直引
    _ask_yt_inclusion,  # re-export：tests/cli 直引
    _maybe_setup_password_in_init,  # re-export：测试 patch 补丁点
    _notify_running_server_init_completed,  # re-export：测试 patch 补丁点
    _persist_api_host_choice,  # re-export：测试 patch 补丁点
    _persist_init_source_enabled_flags,  # re-export：tests/cli 直引
    _print_init_cost_summary,  # rebuild-profile 仍在本文件调用
    init,  # re-export：tests/cli 经 cli_module.init 检查签名
)
from openbiliclaw.cli._cmd_init import register as _register_init_commands  # noqa: E402

_register_init_commands(app)

# soul 画像 / 推荐 / 对话命令组已抽至 cli/_cmd_soul.py（P4 第七刀）；该模块
# 顶层不 import 本包，此处顶层注册无循环依赖。re-export 的 8 个命令名：
# tests/cli 直引 / `openbiliclaw.cli.<cmd>` 外部调用点保持不变。
# 注：`profile` 命令**不在此 re-export** —— 它与本模块既有形参
# `_run_init_discovery_backfill_async(profile=...)` 同名，re-export 会触发
# ruff F811；命令本身已由 register() 挂到 app，`openbiliclaw profile` 与
# `runner.invoke(app, ["profile"])` 均不受影响。需要 Python 层引用时从
# `openbiliclaw.cli._cmd_soul` 直接导入。
from openbiliclaw.cli._cmd_soul import (  # noqa: E402,F401
    chat,
    delight,
    feedback,
    import_youtube,
    probe,
    profile_consolidate,
    rebuild_profile,
    recommend,
)
from openbiliclaw.cli._cmd_soul import register as _register_soul_commands  # noqa: E402

_register_soul_commands(app)

# Knowledge Forge（知识锻造炉）命令组
_APP_CONTEXT: dict[str, Any] = {}
try:
    from openbiliclaw.knowledge_forge.cli import register as _register_kf

    _register_kf(app)
except Exception as _kf_import_exc:  # noqa: BLE001 — 可选模块导入失败不阻塞主 CLI
    _APP_CONTEXT["kf_import_error"] = str(_kf_import_exc)


# 求职面试备战（interview）命令组
try:
    from openbiliclaw.interview.cli import register as _register_interview

    _register_interview(app)
except Exception as _interview_import_exc:  # noqa: BLE001 — 可选模块导入失败不阻塞主 CLI
    _APP_CONTEXT["interview_import_error"] = str(_interview_import_exc)

# 周末怎么玩（weekend）命令组
try:
    from openbiliclaw.weekend.cli import register as _register_weekend

    _register_weekend(app)
except Exception as _weekend_import_exc:  # noqa: BLE001 — 可选模块导入失败不阻塞主 CLI
    _APP_CONTEXT["weekend_import_error"] = str(_weekend_import_exc)
_CODEX_LOGIN_IMPORT_OPTION = typer.Option(
    False,
    "--import",
    help="只导入已有 Codex CLI 凭据，不调用 `codex login`。",
)
_CODEX_LOGIN_SOURCE_OPTION = typer.Option(
    None,
    "--source",
    help="Codex CLI auth.json 路径；默认读取 ~/.codex/auth.json。",
)
_CODEX_LOGIN_STATUS_OPTION = typer.Option(
    False,
    "--status",
    help="查看 Codex OAuth 登录状态。",
)
_CODEX_LOGIN_LOGOUT_OPTION = typer.Option(
    False,
    "--logout",
    help="删除 OpenBiliClaw 本地 Codex 凭据。",
)


def _bootstrap_container_runtime() -> None:
    """Bootstrap runtime root and optional proxy env inside Docker-like runtimes."""
    if not (os.environ.get("OPENBILICLAW_PROJECT_ROOT") or os.environ.get("OPENBILICLAW_CONFIG_TEMPLATE")):
        return

    from openbiliclaw.docker_runtime import bootstrap_runtime_environment

    bootstrap_runtime_environment(os.environ)


# Initial discover runs all four strategies in a single stage so the
# discovery engine's built-in concurrency kicks in: phase 1 runs
# ``search`` alone against a cookie-free client to avoid the IP-level
# search throttle, then phase 2 fans out ``trending``, ``related_chain``
# and ``explore`` concurrently via asyncio.gather. Wall time compresses
# from ``∑strategy`` to roughly ``search + max(trending, related, explore)``.
#
# Rate-limiting is already bounded by ``DiscoveryConcurrencyController``:
# ``search_budget_total=30`` splits across the three search-using
# strategies, and ``bilibili_request_concurrency=2`` caps simultaneous
# HTTP requests regardless of how many strategies run in parallel.
_INIT_DISCOVERY_PLAN = [
    ["search", "trending", "related_chain", "explore"],
]
# Initial pool target. Kept small so the discover phase finishes in
# one or two LLM-eval waves and ``_run_backfill`` doesn't trigger. The
# background refresh loop tops the pool up to
# ``scheduler.pool_target_count`` (300 by default) over the following hour, so a
# tiny init pool only delays diversity, never reduces it.
# X (Twitter): the user's own Likes + Bookmarks, fetched server-side via
# twitter-cli (no extension task). Both are strong explicit-preference signals.
_EXTENSION_PRESENCE_REQUIRED_WARNING = (
    "WARN extension presence required; backend will pause background LLM work "
    "after grace period if no extension client connects"
)

# 渲染 helper 已抽至 cli/_render.py（P4 第四/五刀），主文件与其余命令组共用。
from openbiliclaw.cli._render import (  # noqa: E402,F401
    _print_discovered_content_preview,
    _print_key_value_table,
    _print_page_title,
    _print_recommendation_card,
    _print_status_panel,
)


def _format_pause_on_disconnect_status(*, enabled: bool, grace_seconds: int) -> str:
    if not enabled:
        return "关闭"
    return f"开启（宽限 {grace_seconds}s）"


def _warn_if_pause_on_disconnect_requires_presence() -> None:
    """Print a startup warning when background work depends on extension presence."""
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
    except Exception:
        return

    if cfg.scheduler.pause_on_extension_disconnect:
        console.print(
            f"[yellow]{_EXTENSION_PRESENCE_REQUIRED_WARNING}[/yellow]",
            soft_wrap=True,
        )


def _is_default_ollama_endpoint(endpoint: str) -> bool:
    from urllib.parse import urlparse

    parsed = urlparse(endpoint)
    host = (parsed.hostname or "").strip().lower()
    return host in {"localhost", "127.0.0.1", "::1"} and parsed.port == 11434


def _preflight_loopback_ollama(cfg: Any) -> None:
    if not ollama_required(cfg) or not cfg.autostart.manage_ollama:
        return
    endpoint = effective_ollama_endpoint(cfg)
    if not is_loopback(endpoint):
        return
    if _ollama_is_running(host=endpoint):
        return
    if not _is_default_ollama_endpoint(endpoint):
        console.print(
            f"[yellow]本机 Ollama 端点 {endpoint} 未响应；自定义端口不会自动执行 "
            "`ollama serve`，请自行管理该服务。[/yellow]"
        )
        return
    if not _ollama_start_serve_background():
        console.print(
            "[yellow]Ollama preflight 未能拉起本机服务；后端继续启动，后续 LLM/embedding 请求可能降级或失败。[/yellow]"
        )


def _self_heal_autostart_registration(cfg: Any) -> None:
    from openbiliclaw.runtime import autostart

    state = autostart.status()
    if not state.supported:
        return

    if not cfg.autostart.enabled:
        if state.registered:
            try:
                autostart.unregister()
            except Exception as exc:
                console.print(f"[yellow]开机自启动残留项移除失败：{exc}[/yellow]")
        return

    if state.registered:
        return

    from openbiliclaw.runtime.autostart.guards import active_env_managed_inputs

    managed = active_env_managed_inputs(cfg)
    if managed:
        console.print(
            "[yellow]已开启开机自启动，但检测到环境变量配置，跳过自动补注册："
            f"{', '.join(managed)}。请先写入 config.toml。[/yellow]"
        )
        return
    try:
        autostart.register(cfg)
    except Exception as exc:
        console.print(f"[yellow]开机自启动补注册失败：{exc}[/yellow]")


def _print_placeholder(feature: str, next_step: str = "") -> None:
    """Render a consistent placeholder panel for unfinished commands."""
    body = "功能开发中"
    if next_step:
        body = f"{body}\n[dim]下一步：{next_step}[/dim]"
    _print_page_title(feature)
    _print_status_panel("stub", "开发中", body)


def _initialize_logging(log_level_override: str | None = None) -> None:
    """Load config and initialize the logging system.

    Skips the on-startup unmanaged-logs sweep when invoked via the
    ``logs-prune`` command — that command's whole purpose is letting
    the user inspect / control cleanup, so triggering automatic sweep
    inside the callback would defeat the dry-run contract.
    """
    import sys

    from openbiliclaw.config import load_config
    from openbiliclaw.logging_setup import configure_logging

    config = load_config()
    skip_sweep = "logs-prune" in sys.argv
    configure_logging(
        config,
        console_level_override=log_level_override,
        sweep_unmanaged=not skip_sweep,
    )


def _build_registry() -> Any:
    """Build the configured LLM registry."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm._compat_registry import build_llm_registry

    return build_llm_registry(load_config())


def _build_auth_manager() -> Any:
    """Build the configured Bilibili auth manager."""
    from openbiliclaw.bilibili.auth import AuthManager
    from openbiliclaw.config import load_config

    return AuthManager(load_config().data_path)


def _build_browser() -> Any:
    """Build the configured Bilibili browser integration."""
    from openbiliclaw.bilibili.auth import resolve_runtime_cookie
    from openbiliclaw.bilibili.browser import BilibiliBrowser
    from openbiliclaw.config import load_config

    config = load_config()
    return BilibiliBrowser(
        executable=config.bilibili.browser_executable,
        headed=config.bilibili.browser_headed,
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        ),
    )


def _build_bilibili_client() -> Any:
    """Build the configured Bilibili API client."""
    from openbiliclaw.bilibili.api import BilibiliAPIClient
    from openbiliclaw.bilibili.auth import resolve_runtime_cookie
    from openbiliclaw.config import load_config

    config = load_config()
    return BilibiliAPIClient(
        cookie=resolve_runtime_cookie(
            data_dir=config.data_path,
            configured_cookie=config.bilibili.cookie,
        )
    )


def _build_soul_engine() -> Any:
    """Build the configured soul engine with initialized memory storage."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.service import module_overrides_from_config
    from openbiliclaw.soul.engine import SoulEngine

    class _UnavailableLLM:
        default_provider = ""

        def is_chat_capable(self, _name: str) -> bool:
            return False

        async def complete(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM registry is unavailable for this command.")

        async def complete_provider(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("LLM registry is unavailable for this command.")

    cfg = load_config()
    memory = _build_memory_manager()
    try:
        llm = _build_registry()
    except Exception:
        llm = _UnavailableLLM()
    return SoulEngine(
        llm=llm,
        memory=memory,
        usage_recorder=_build_usage_recorder(),
        satisfaction_filter_enabled=cfg.soul.preference.satisfaction_filter_enabled,
        module_overrides=module_overrides_from_config(cfg),
        llm_concurrency=cfg.llm.concurrency,
        speculation_interval_minutes=cfg.scheduler.speculation_interval_minutes,
        speculation_ttl_days=cfg.scheduler.speculation_ttl_days,
        speculation_cooldown_days=cfg.scheduler.speculation_cooldown_days,
        speculation_confirmation_threshold=cfg.scheduler.speculation_confirmation_threshold,
        speculation_max_active=cfg.scheduler.speculation_max_active,
        speculation_max_primary_interests=cfg.scheduler.speculation_max_primary_interests,
        speculation_max_secondary_interests=cfg.scheduler.speculation_max_secondary_interests,
        avoidance_speculation_interval_minutes=(cfg.scheduler.avoidance_speculation_interval_minutes),
        avoidance_speculation_ttl_days=cfg.scheduler.avoidance_speculation_ttl_days,
        avoidance_speculation_cooldown_days=cfg.scheduler.avoidance_speculation_cooldown_days,
        avoidance_speculation_confirmation_threshold=(cfg.scheduler.avoidance_speculation_confirmation_threshold),
        avoidance_speculation_max_active=cfg.scheduler.avoidance_speculation_max_active,
        speculator_idle_interval_minutes=cfg.scheduler.speculator_idle_interval_minutes,
        profile_consolidation_enabled=cfg.scheduler.profile_consolidation_enabled,
        profile_consolidation_interval_hours=(cfg.scheduler.profile_consolidation_interval_hours),
        profile_consolidation_like_target_upper=(cfg.scheduler.profile_consolidation_like_target_upper),
        profile_consolidation_like_target_soft=cfg.scheduler.profile_consolidation_like_target_soft,
        profile_consolidation_archive_enabled=(cfg.scheduler.profile_consolidation_archive_enabled),
    )


def _build_recommendation_engine() -> Any:
    """Build the recommendation engine with core-memory-aware LLM access."""
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config
    from openbiliclaw.recommendation.engine import (
        RecommendationEngine,
        SupportsEmbeddingService,
    )

    memory = _build_memory_manager()
    database = _get_runtime_database()
    cfg = load_config()
    registry = _build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_build_usage_recorder(),
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
    )
    from openbiliclaw.llm._compat_registry import build_embedding_service

    _emb = build_embedding_service(cfg, registry)
    embedding_service = cast("SupportsEmbeddingService | None", _emb)

    def _xhs_self_info_provider() -> dict[str, object] | None:
        state = memory.load_discovery_runtime_state()
        info = state.get("xhs_self_info")
        return info if isinstance(info, dict) else None

    return RecommendationEngine(
        llm=llm_service,
        database=database,
        embedding_service=embedding_service,
        xhs_self_info_provider=_xhs_self_info_provider,
        # v0.4.0+: LLM semantic reranker (generative recommendation, step 1)
        # 防御性：测试 fake_config（SimpleNamespace）可能缺 recommendation 段
        llm_reranker_enabled=bool(getattr(getattr(cfg, "recommendation", None), "llm_reranker_enabled", False)),
        llm_reranker_top_k=int(getattr(getattr(cfg, "recommendation", None), "llm_reranker_top_k", 30)),
        llm_reranker_weight=float(getattr(getattr(cfg, "recommendation", None), "llm_reranker_weight", 0.3)),
        llm_reranker_batch_size=int(getattr(getattr(cfg, "recommendation", None), "llm_reranker_batch_size", 5)),
    )


def _build_dialogue(soul_engine: Any) -> Any:
    """Build the Socratic dialogue helper for interactive chat."""
    from openbiliclaw.soul.dialogue import SocraticDialogue

    return SocraticDialogue(llm=_build_registry(), soul_engine=soul_engine, session="cli")


def _run_api_server(*, host: str = "127.0.0.1", port: int = 8420) -> None:
    """Run the local FastAPI service used by the browser extension."""
    import uvicorn

    from openbiliclaw.api.app import create_app

    api_app = create_app()
    from openbiliclaw.api.chat_analysis_routes import register_chat_analysis_routes

    register_chat_analysis_routes(api_app, getattr(api_app, "state", None))
    state = getattr(api_app, "state", None)
    if bool(getattr(state, "degraded", False)):
        issues = []
        for issue in list(getattr(state, "degraded_issues", [])):
            field = str(getattr(issue, "field", ""))
            message = str(getattr(issue, "message", issue))
            issues.append(f"- {field}: {message}" if field else f"- {message}")
        reason = str(getattr(state, "degraded_reason", ""))
        body = (
            f"reason: {reason or 'unknown'}\n"
            + "\n".join(issues)
            + "\n\nOpen the extension popup settings to fix the LLM credentials, "
            "then restart the daemon."
        )
        _print_status_panel("warning", "降级模式 / Degraded mode", body)
    uvicorn.run(api_app, host=host, port=port, log_level="info")


def _build_memory_manager() -> Any:
    """Build the initialized memory manager for event writes."""
    from openbiliclaw.config import load_config
    from openbiliclaw.memory.manager import MemoryManager

    cached = _RUNTIME_COMPONENTS.get("memory_manager")
    if cached is not None:
        return cached

    config = load_config()
    memory = MemoryManager(config.data_path, database=_get_runtime_database())
    memory.initialize()
    _RUNTIME_COMPONENTS["memory_manager"] = memory
    return memory


def _build_discovery_engine() -> Any:
    """Build the discovery engine with currently implemented strategies."""
    from openbiliclaw.discovery.engine import (
        ContentDiscoveryEngine,
        DiscoveryConcurrencyController,
    )
    from openbiliclaw.discovery.strategies.strategies import (
        ExploreStrategy,
        RelatedChainStrategy,
        SearchStrategy,
        TrendingStrategy,
    )
    from openbiliclaw.llm.service import LLMService, module_overrides_from_config

    memory = _build_memory_manager()
    database = _get_runtime_database()
    bilibili_client = _build_bilibili_client()
    from openbiliclaw.config import load_config

    cfg = load_config()
    registry = _build_registry()
    llm_service = LLMService(
        registry=registry,
        memory=memory,
        usage_recorder=_build_usage_recorder(),
        module_overrides=module_overrides_from_config(cfg),
        concurrency=cfg.llm.concurrency,
    )
    concurrency = DiscoveryConcurrencyController(
        bilibili_request_concurrency=2,
        # Inherit dataclass default (currently 32) — sized so an init
        # discover's ~32 batches all fan out in a single wave instead
        # of queueing behind a tight cap. See engine.py for rationale.
    )

    # Build embedding service from config (optional)
    from openbiliclaw.llm._compat_registry import build_embedding_service

    embedding_service = build_embedding_service(cfg, registry)
    discovery_cfg = getattr(cfg, "discovery", None)

    engine = ContentDiscoveryEngine(
        llm_service=llm_service,
        database=database,
        concurrency=concurrency,
        embedding_service=embedding_service,
        multimodal_evaluation_enabled=bool(getattr(discovery_cfg, "multimodal_evaluation_enabled", False)),
        multimodal_batch_size=int(getattr(discovery_cfg, "multimodal_batch_size", 8)),
        multimodal_image_max_px=int(getattr(discovery_cfg, "multimodal_image_max_px", 384)),
        multimodal_image_quality=int(getattr(discovery_cfg, "multimodal_image_quality", 72)),
        multimodal_image_timeout_seconds=int(getattr(discovery_cfg, "multimodal_image_timeout_seconds", 6)),
    )
    search_strategy = SearchStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        database=database,
    )
    trending_strategy = TrendingStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        concurrency=concurrency,
        database=database,
    )
    related_strategy = RelatedChainStrategy(
        bilibili_client=bilibili_client,
        llm_service=llm_service,
        memory_manager=cast("Any", memory),
        search_strategy=search_strategy,
        trending_strategy=trending_strategy,
        concurrency=concurrency,
        database=database,
    )
    explore_strategy = ExploreStrategy(
        llm_service=llm_service,
        bilibili_client=bilibili_client,
        concurrency=concurrency,
        embedding_service=embedding_service,
        database=database,
    )

    engine.register_strategy(search_strategy)
    engine.register_strategy(trending_strategy)
    engine.register_strategy(related_strategy)
    engine.register_strategy(explore_strategy)
    return engine


def _build_usage_recorder() -> Any:
    """Build or return the shared LLM usage recorder (cost ledger sink).

    CLI commands construct their own ``LLMService`` / ``SoulEngine``
    instead of going through ``runtime_context``, so without this every
    CLI-run LLM call was invisible in ``openbiliclaw cost``.
    """
    cached = _RUNTIME_COMPONENTS.get("usage_recorder")
    if cached is not None:
        return cached

    from openbiliclaw.llm.usage_recorder import UsageRecorder

    recorder = UsageRecorder(sink=_get_runtime_database())
    _RUNTIME_COMPONENTS["usage_recorder"] = recorder
    return recorder


def _runtime_database_path() -> Path:
    from openbiliclaw.config import load_config

    config = load_config()
    return config.data_path / "openbiliclaw.db"


def _runtime_backup_dir() -> Path:
    return _runtime_database_path().parent / "backups"


def _maybe_create_runtime_database_backup() -> None:
    from openbiliclaw.storage.maintenance import maybe_create_scheduled_backup

    db_path = _runtime_database_path()
    if not db_path.exists():
        return
    maybe_create_scheduled_backup(db_path, _runtime_backup_dir())


def _ensure_runtime_database_healthy() -> None:
    from openbiliclaw.storage.maintenance import check_database_integrity

    db_path = _runtime_database_path()
    if not db_path.exists():
        return
    report = check_database_integrity(db_path)
    if report.healthy:
        return
    _print_status_panel(
        "error",
        "数据库损坏",
        "检测到本地数据库损坏，请先执行 `openbiliclaw db-repair` 再启动服务。",
    )
    if report.error:
        console.print(report.error)
    raise typer.Exit(code=1)


def _run_db_repair() -> Any:
    from openbiliclaw.storage.maintenance import repair_database

    return repair_database(_runtime_database_path(), backup_dir=_runtime_backup_dir())


@app.callback()
def main(log_level: str | None = typer.Option(None, "--log-level")) -> None:
    """Global CLI options."""
    _APP_CONTEXT["log_level"] = log_level
    _bootstrap_container_runtime()
    _initialize_logging(log_level_override=log_level)
    _sync_outbound_proxy()


def _sync_outbound_proxy() -> None:
    """Mirror [network].proxy into the process-level source of truth for CLI.

    Runs once per CLI invocation so any command that builds an LLM registry or
    the updater routes overseas traffic through the configured proxy. Guarded
    so a missing/broken config never blocks a command from starting.
    """
    import contextlib

    from openbiliclaw.config import load_config
    from openbiliclaw.network import set_outbound_proxy

    # Config resolution must never block a command from starting.
    with contextlib.suppress(Exception):
        network = load_config().network
        set_outbound_proxy(network.proxy, mode=network.mode)


def _print_config_guidance(messages: list[str]) -> None:
    """Render config hints in a consistent way."""
    if not messages:
        return
    console.print("[bold yellow]配置提示[/bold yellow]")
    for message in messages:
        console.print(f"  - {message}")


def _print_auth_status(status: Any) -> None:
    """Render auth status consistently."""
    state_label = "已认证" if status.authenticated else "未认证"
    _print_page_title("认证概览", "B站认证状态")
    rows = [
        ("状态", state_label),
        ("Cookie 文件", str(status.cookie_path)),
    ]
    if status.username:
        rows.append(("用户名", str(status.username)))
    if status.user_id:
        rows.append(("UID", str(status.user_id)))
    if status.message:
        rows.append(("说明", str(status.message)))
    _print_key_value_table("认证信息", rows)


def _print_browser_status(browser: Any) -> None:
    """Render browser installation status."""
    availability = "已安装" if browser.is_available else "未安装"
    _print_page_title("浏览器集成状态", "agent-browser 状态")
    _print_key_value_table(
        "浏览器信息",
        [
            ("状态", availability),
            ("可执行文件", str(browser.executable)),
        ],
    )


def _require_runtime_config() -> None:
    """Exit with a clear message when runtime config is incomplete."""
    error = _load_runtime_config_error()
    if error is not None:
        raise typer.Exit(code=1)


def _print_runtime_config_error(error: str, hints: list[str] | None = None) -> None:
    """Render runtime config errors consistently."""
    console.print("[bold red]配置错误[/bold red]")
    _print_config_guidance(hints or [])
    console.print(f"  {error}")


def _load_runtime_config_error(*, render: bool = True) -> str | None:
    """Return a user-facing runtime config error and optionally print guidance."""
    from openbiliclaw.config import (
        ConfigError,
        load_config_with_diagnostics,
        validate_runtime_config,
    )

    config, diagnostics = load_config_with_diagnostics()
    try:
        validate_runtime_config(config)
    except ConfigError as exc:
        hints = diagnostics.messages + [f"{issue.field}: {issue.message}" for issue in diagnostics.issues]
        if render:
            _print_runtime_config_error(str(exc), hints)
        return str(exc)
    return None


def _save_runtime_provider_config(
    provider: str,
    *,
    api_key: str = "",
    base_url: str = "",
    model: str = "",
) -> None:
    """Persist the selected provider's full config triple to ``config.toml``.

    Writes ``default_provider`` plus the per-provider ``[llm.<name>]``
    block. ``api_key`` / ``base_url`` / ``model`` are only written when
    non-empty (so existing saved values aren't blown away when the
    wizard's user accepts a default by leaving the prompt blank).
    """
    from openbiliclaw.config import load_config_with_diagnostics, save_config

    config, diagnostics = load_config_with_diagnostics()
    config.llm.default_provider = provider
    provider_config = getattr(config.llm, provider, None)
    if provider_config is None:
        save_config(config, diagnostics.config_path)
        return
    if api_key and hasattr(provider_config, "api_key"):
        provider_config.api_key = api_key.strip()
    if base_url and hasattr(provider_config, "base_url"):
        provider_config.base_url = base_url.strip()
    if model and hasattr(provider_config, "model"):
        provider_config.model = model.strip()
    save_config(config, diagnostics.config_path)


# Default base_url + chat model per provider. The user can always override
# both in the wizard; these are just the "I picked X, what should the
# defaults look like?" answers.
# Last refreshed 2026-05. When a provider rolls a new flagship,
# update the model field here AND the matching ``_LLM_MENU`` /
# ``_PROVIDER_MODEL_HINT`` entries.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    # OpenAI: gpt-4o-mini retired from ChatGPT in Feb 2026; gpt-5-nano
    # is the cheapest current-gen ($0.05 / $0.40 per 1M).
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-5-nano"},
    # Claude: Sonnet 4.6 is the current main-line Sonnet (1M context).
    # Opus 4.7 is top-tier; Haiku 4.5 is the budget option.
    "claude": {"base_url": "", "model": "claude-sonnet-4-6"},
    # Gemini: 2.5-flash is the stable budget default (3-flash is preview;
    # 3.1-pro is reasoning flagship).
    "gemini": {"base_url": "", "model": "gemini-2.5-flash"},
    # DeepSeek: V4 family. deepseek-chat / deepseek-reasoner deprecate
    # 2026-07-24.
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash"},
    # Ollama: project is Chinese-primary; qwen2.5:7b handles Chinese
    # noticeably better than llama3 at the same size.
    "ollama": {"base_url": "http://localhost:11434/v1", "model": "qwen2.5:7b"},
    # OpenRouter: route to OpenAI's cheapest current-gen by default.
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "openai/gpt-5-nano"},
}


_PROVIDER_HINTS: dict[str, str] = {
    "openai": "OpenAI 官方（api.openai.com）",
    "claude": "Anthropic Claude 官方",
    "gemini": "Google Gemini 官方",
    "deepseek": "DeepSeek 官方（OpenAI 兼容协议）",
    "ollama": "本地 Ollama（无需 Key）",
    "openrouter": "OpenRouter 聚合",
}


# One-liner shown right before the model prompt so the user knows
# what's actually on offer, instead of confirming an opaque string.
# Lists current main-line model names per provider — refresh when
# a provider deprecates / renames a model.
_PROVIDER_MODEL_HINT: dict[str, str] = {
    "deepseek": (
        "可选模型: deepseek-v4-flash (默认 / 便宜) / deepseek-v4-pro (更强)。"
        "旧名 deepseek-chat / deepseek-reasoner 将于 2026/07/24 弃用"
    ),
    "openai": (
        "可选模型: gpt-5-nano (默认 / 最便宜) / gpt-5.4-nano / "
        "gpt-5.4-mini / gpt-5.5 (旗舰 4/2026) / gpt-5.5-pro (高精度)。"
        "gpt-4o / gpt-4o-mini 已从 ChatGPT 退役,API 仍可调"
    ),
    "gemini": (
        "可选模型: gemini-2.5-flash (默认 / 稳定) / "
        "gemini-3-flash-preview (新一代 / 推理强) / "
        "gemini-3.1-pro-preview (旗舰 / Public Preview, 需付费项目) / "
        "gemini-3.1-flash-lite-preview (最便宜)"
    ),
    "claude": (
        "可选模型: claude-sonnet-4-6 (默认 / 1M 上下文) / "
        "claude-haiku-4-5 (便宜) / claude-opus-4-7 (旗舰 / agentic 最强)。"
        "claude-sonnet-4-5 仍可调"
    ),
    "openrouter": (
        "默认 openai/gpt-5-nano。OpenRouter 模型名格式: <vendor>/<model>,"
        "如 anthropic/claude-sonnet-4-6 / google/gemini-2.5-flash"
    ),
    "ollama": (
        "常见模型: qwen2.5:7b (默认 / 中文好) / llama3.2 (Meta 新版) / "
        "gemma2 (Google) / mistral (轻量) / deepseek-r1 (开源推理)。"
        "模型名要和 Ollama 库里完全一致 (`ollama list` 看)"
    ),
}


# Sub-menu shown when user picks "OpenAI 协议兼容自建网关" from
# _LLM_MENU. Order = menu order. Each entry pre-fills base_url so the
# user doesn't have to copy from a doc; default_model is a sensible
# starting point but the prompt still lets them change it. ``hint``
# is a one-liner shown right above the model prompt listing real
# main-line models for that service.
#
# When adding a new compat-protocol vendor:
# 1. Verify they speak true OpenAI Chat Completions protocol (Bearer
#    auth + ``/v1/chat/completions`` shape). Many "OpenAI compatible"
#    APIs subtly differ on tools / streaming / function_call format —
#    try a smoke call before listing here.
# 2. Pick a representative low-cost default_model so users get a
#    cheap experience by default; advanced users can switch in
#    Phase 2.
#
# Order rationale (2026-05): the OpenAI-protocol-compat menu's *primary*
# real-world purpose is to plumb in 中转站 / OneAPI / 团队 LLM 网关 keys
# — the user has already bought access from a relay vendor and just
# wants OpenBiliClaw to talk to it. That's why ``relay`` is the
# default (#1). Native Chinese vendor APIs (Kimi / MiniMax / Qwen / GLM
# / Yi / SenseNova) follow because some users do go straight to the vendor; Azure
# and self-hosted are infrastructure-flavor variants for企业 / 玩家;
# ``custom`` is the manual escape hatch.
_OPENAI_COMPAT_PRESETS: tuple[tuple[str, dict[str, str]], ...] = (
    (
        "relay",
        {
            "label": "★ 中转站 / OneAPI / 公司团队 LLM 网关 (大多数人选这个)",
            "description": (
                "中转站 = 第三方代理 OpenAI / Claude 的二级商家(国内付人民币用海外模型)。"
                "OneAPI / 团队 LLM 网关 = 公司自建的多模型聚合 + 计费 + 限流网关。"
                "买中转站 Key 的人选这个就对了"
            ),
            "signup_url": (
                "找你充值的那家中转站官网拿 Key (它们大多有自己的 base_url 和文档)。"
                "OneAPI 是开源自建项目: https://github.com/songquanpeng/one-api"
            ),
            "supports_embedding": "true",  # most relay services proxy embeddings too
            "base_url": "",  # user-supplied — every relay has its own
            "default_model": "gpt-5-nano",
            "hint": (
                "看你中转站后端代理到哪个真实模型。中转站 / OneAPI 通常代理 "
                "OpenAI (gpt-5-nano / gpt-5.4-mini / gpt-5.5) 或 "
                "Claude (claude-sonnet-4-6 / claude-opus-4-7) 或国产模型,"
                "按你充值的那家给你的模型清单填"
            ),
            "embedding_alt": (
                "中转站通常也代理 OpenAI text-embedding-3-small,Phase 3 高级选项里可以指向同一个 base_url"
            ),
        },
    ),
    (
        "kimi",
        {
            "label": "Kimi (Moonshot AI 月之暗面) 官方",
            "description": (
                "国产长上下文老牌 (256K ctx),长文档理解 / 网页爬阅 / "
                "学术阅读这些场景表现好,日常对话也稳。直接从 Moonshot 官方拿 Key"
            ),
            "signup_url": (
                "https://platform.moonshot.cn/console/api-keys （国内）/ https://platform.moonshot.ai （国际）"
            ),
            "supports_embedding": "false",
            "base_url": "https://api.moonshot.ai/v1",
            "default_model": "kimi-k2.6",
            "hint": (
                "kimi-k2.6 (默认 / 最新 / 256K 上下文 / 多模态) / kimi-k2.5。"
                "旧 moonshot-v1-* 和 K2-series 即将停服(K2 系列 2026-05-25 停)"
            ),
            "domain_alt": ("国内用户也可改 base_url 为 https://api.moonshot.cn/v1 (域名不同,Key 通用)"),
        },
    ),
    (
        "minimax",
        {
            "label": "MiniMax 官方",
            "description": (
                "国产代码 / agent 场景的当前 SOTA 之一 (M2.7 在 SWE-Bench 上 80%+),"
                "便宜 ($0.30 / $1.20 per M),适合做推荐这种结构化输出任务"
            ),
            "signup_url": (
                "https://platform.minimaxi.com/user-center/basic-information/interface-key "
                "（国内）/ https://platform.minimax.io （国际）"
            ),
            "supports_embedding": "false",
            "base_url": "https://api.minimax.io/v1",
            "default_model": "MiniMax-M2.7",
            "hint": (
                "MiniMax-M2.7 (默认 / 最新 / 4-2026 / 228K ctx) / "
                "MiniMax-M2.5 / MiniMax-M2.1。"
                "旧 abab 系列 (abab6.5*) 已被 M 系列替代"
            ),
            "domain_alt": ("国内用户改 base_url 为 https://api.minimaxi.com/v1 (旧 .chat 域名将停)"),
        },
    ),
    (
        "qwen",
        {
            "label": "通义千问 (阿里 DashScope) 官方",
            "description": (
                "阿里出品,中文最强档之一 (qwen3.6 系列),qwen-plus 别名"
                "自动跟最新快照,无需手动升级。免费档调用次数有限,商用记得充值"
            ),
            "signup_url": "https://bailian.console.aliyun.com/?apiKey=1#/api-key",
            "supports_embedding": "true",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "default_model": "qwen-plus",
            "hint": (
                "qwen-flash (最便宜) / qwen-plus (默认 / 平衡) / qwen-max (旗舰)。"
                "都是别名,自动跟最新快照(当前 → qwen3.6-*, 2026-04 系列)"
            ),
            "embedding_alt": "DashScope 也支持 text-embedding-v3 (Phase 3 高级选项里可选)",
        },
    ),
    (
        "zhipu",
        {
            "label": "智谱 ChatGLM 官方",
            "description": (
                "清华 + 智谱出品。GLM-4.7-Flash 完全免费(每天调用次数限制),"
                "做推荐 / 画像够用;GLM-5 是付费旗舰 (745B MoE,Claude Opus 级)"
            ),
            "signup_url": "https://www.bigmodel.cn/usercenter/proj-mgmt/apikeys",
            "supports_embedding": "true",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "default_model": "glm-4.7-flash",
            "hint": (
                "glm-4.7-flash (默认 / 免费 / 200K ctx) / glm-5 (付费旗舰 / 4/2026 / 745B MoE) / "
                "glm-4.6。注意: base_url 是 /api/paas/v4 不是 /v1"
            ),
            "embedding_alt": "智谱也有 embedding-3 (Phase 3 高级选项里可选)",
        },
    ),
    (
        "yi",
        {
            "label": "零一万物 (Yi) 官方",
            "description": (
                "李开复创业团队出品,Yi-Large 在 LMSYS 中文榜常年 top 国产之一。"
                "yi-medium 平衡好用,yi-spark 最便宜适合高频小任务"
            ),
            "signup_url": "https://platform.lingyiwanwu.com/apikeys",
            "supports_embedding": "false",
            "base_url": "https://api.lingyiwanwu.com/v1",
            "default_model": "yi-medium",
            "hint": (
                "yi-spark (最便宜) / yi-medium (默认 / 平衡) / yi-lightning (新 / 快) / "
                "yi-large (旗舰) / yi-large-turbo (平衡) / yi-medium-200k (长上下文)"
            ),
        },
    ),
    (
        "sensenova",
        {
            "label": "商汤日日新 (SenseNova) 官方",
            "description": (
                "商汤日日新开放平台,新用户有免费额度,可以零成本体验本项目"
                "(issue #193)。token 推理端点已按 OpenAI 协议实测连通"
                "(deepseek-v4-flash 真实请求验证)"
            ),
            "signup_url": ("https://console.sensecore.cn （日日新开放平台控制台申请 API Key,免费额度以官方页面为准）"),
            "supports_embedding": "false",
            "base_url": "https://token.sensenova.cn/v1",
            "default_model": "deepseek-v4-flash",
            "hint": (
                "deepseek-v4-flash (默认 / 已实测) / 其它可用模型以控制台模型清单为准。"
                "免费额度适合试用与轻度使用;重度使用建议充值或换 DeepSeek 官方"
            ),
            "embedding_alt": ("token 推理端点未验证 /v1/embeddings,Phase 3 默认推荐独立 Ollama bge-m3"),
        },
    ),
    (
        "azure",
        {
            "label": "Azure OpenAI",
            "description": (
                "微软的 OpenAI 企业版。和 OpenAI 官方模型一致,但鉴权 / 模型名 / "
                "endpoint 都按 Azure 的 deployment 模式走。多用于企业合规场景"
            ),
            "signup_url": (
                "Azure portal → 创建 OpenAI resource → 创建 deployment → Keys & Endpoint 取 KEY 和 ENDPOINT"
            ),
            "supports_embedding": "true",
            "base_url": "https://YOUR-RESOURCE.openai.azure.com/openai/deployments/YOUR-DEPLOYMENT",
            "default_model": "",
            "hint": (
                "Azure 模型名 = 你创建 deployment 时指定的 deployment name(不是底层 gpt-5)。"
                "Base URL 把 YOUR-RESOURCE / YOUR-DEPLOYMENT 替换成你自己的"
            ),
            "embedding_alt": (
                "Azure 上 embedding 模型也是单独 deployment,Phase 3 时再起一个 deployment 并填那个的 endpoint"
            ),
        },
    ),
    (
        "self-hosted",
        {
            "label": "自建 vLLM / LMStudio / Ollama 网关",
            "description": (
                "你自己跑的 LLM 服务,常见: vLLM (多卡推理) / LMStudio (Mac M-series) / "
                "Ollama 的 OpenAI 兼容 shim。免费但要自备硬件"
            ),
            "signup_url": "无 (本地服务通常不需要 Key,鉴权可留空)",
            "supports_embedding": "false",  # depends — assume no
            "base_url": "http://localhost:8000/v1",
            "default_model": "",  # force user to type their deployed model
            "hint": (
                "看你网关上部署的是什么。HuggingFace 路径,如 "
                "meta-llama/Llama-3.3-70B-Instruct / Qwen/Qwen2.5-72B-Instruct / "
                "deepseek-ai/DeepSeek-V3"
            ),
            "embedding_alt": (
                "如果你的 vLLM/LMStudio 也部署了 embedding 模型,Phase 3 高级选项里可以指向同一个 base_url"
            ),
        },
    ),
    (
        "custom",
        {
            "label": "其它 (完全手填)",
            "description": (
                "上面 8 个都不匹配的兜底选项。任何 OpenAI Chat Completions 协议兼容的服务"
                "都能填(Bearer auth + /v1/chat/completions 形态)"
            ),
            "signup_url": "看你的服务方文档",
            "supports_embedding": "false",  # unknown
            "base_url": "",
            "default_model": "",
            "hint": ("Base URL 必须以 /v1 (或网关等价路径)结尾。模型名得是网关上真实部署 / 提供的那个,写错会 404"),
        },
    ),
)


def _ollama_has_model(model: str, host: str = "http://localhost:11434") -> bool:
    """Return True if Ollama already has the named model pulled."""
    import httpx

    try:
        with httpx.Client(timeout=5.0, trust_env=False) as client:
            response = client.get(f"{host}/api/tags")
            response.raise_for_status()
            tags = response.json().get("models", [])
            for tag in tags:
                name = str(tag.get("name", "")).strip()
                # Match "bge-m3", "bge-m3:latest", etc.
                if name == model or name.startswith(f"{model}:"):
                    return True
    except Exception:
        return False
    return False


def _ollama_pull_model(model: str, host: str = "http://localhost:11434") -> bool:
    """Stream a model pull from Ollama; print progress to console."""
    import httpx

    try:
        with (
            httpx.Client(timeout=600.0, trust_env=False) as client,
            client.stream(
                "POST",
                f"{host}/api/pull",
                json={"model": model, "stream": True},
            ) as stream,
        ):
            stream.raise_for_status()
            for line in stream.iter_lines():
                if not line:
                    continue
                import json as _json

                try:
                    evt = _json.loads(line)
                except _json.JSONDecodeError:
                    continue
                status = evt.get("status", "")
                if status:
                    console.print(f"  [dim]{status}[/dim]")
                if evt.get("error"):
                    console.print(f"  [red]{evt['error']}[/red]")
                    return False
        return True
    except Exception as exc:
        console.print(f"  [red]拉取失败: {exc}[/red]")
        return False


def _ollama_install_if_missing() -> bool:
    """If Ollama isn't installed, offer to auto-install via package mgr.

    Returns True iff the binary is available after this call. The user
    can decline (we then return False — caller should fall back to
    asking them to install manually). Mirrors agent_bootstrap.py's
    install_ollama, but with an interactive consent prompt because
    invoking package managers is a side-effect users should approve.
    """
    import shutil
    import subprocess

    if shutil.which("ollama"):
        return True

    console.print(
        "[yellow]检测不到 ollama 命令。[/yellow] "
        "OpenBiliClaw 可以帮你装上，过程透明：\n"
        "  • macOS: 通过 brew install ollama\n"
        "  • Windows: 通过 winget install Ollama.Ollama\n"
        "  • Linux: 通过官方 install.sh（curl https://ollama.com/install.sh | sh）"
    )
    if not typer.confirm("是否现在帮你装 Ollama？", default=True):
        console.print("[dim]已跳过自动安装。请手动从 https://ollama.com/download 下载，然后重新跑一遍本命令。[/dim]")
        return False

    if sys.platform == "darwin":
        if not shutil.which("brew"):
            console.print(
                "[red]没找到 brew。请从 https://ollama.com/download 下载 Mac 安装包，装好后重新运行本命令。[/red]"
            )
            return False
        subprocess.run(["brew", "install", "ollama"], check=False)
    elif os.name == "nt":
        if not shutil.which("winget"):
            console.print(
                "[red]没找到 winget。请从 https://ollama.com/download 下载 Windows 安装包，装好后重新运行本命令。[/red]"
            )
            return False
        subprocess.run(
            [
                "winget",
                "install",
                "-e",
                "--id",
                "Ollama.Ollama",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            check=False,
        )
    else:
        # Linux: piped curl | sh — needs sudo for systemd registration.
        subprocess.run(
            "curl -fsSL https://ollama.com/install.sh | sh",
            shell=True,
            check=False,
        )

    if shutil.which("ollama"):
        console.print("[green]Ollama 安装成功。[/green]")
        return True
    console.print("[red]安装似乎没成功。请从 https://ollama.com/download 手动装一下，再重新跑本命令。[/red]")
    return False


def _save_embedding_config(
    *,
    provider: str,
    model: str,
    base_url: str = "",
    api_key: str = "",
) -> None:
    """Persist the embedding provider/model selection to config.toml.

    For OpenAI-compatible providers the wizard may collect a custom
    ``base_url`` / ``api_key`` (e.g. a self-hosted vLLM gateway running
    bge-m3 over the OpenAI protocol). These are written into
    ``[llm.embedding]`` because embedding is independent from chat
    provider configuration.
    """
    from openbiliclaw.config import load_config_with_diagnostics, save_config

    config, diagnostics = load_config_with_diagnostics()
    config.llm.embedding.provider = provider
    config.llm.embedding.model = model
    if base_url:
        config.llm.embedding.base_url = base_url.strip()
    elif provider == "ollama" and not config.llm.embedding.base_url.strip():
        config.llm.embedding.base_url = "http://localhost:11434/v1"
    if api_key:
        config.llm.embedding.api_key = api_key.strip()
    save_config(config, diagnostics.config_path)


def _save_module_overrides(overrides: dict[str, dict[str, str]]) -> None:
    """Persist per-module LLM overrides to config.toml.

    ``overrides`` maps module name (``soul`` / ``discovery`` /
    ``recommendation`` / ``evaluation``) to a dict with optional
    ``provider`` and ``model`` keys. Empty values are written as empty
    strings, which the loader treats as "use global default".
    """
    from openbiliclaw.config import load_config_with_diagnostics, save_config

    config, diagnostics = load_config_with_diagnostics()
    for module, payload in overrides.items():
        module_config = getattr(config.llm, module, None)
        if module_config is None:
            continue
        if "provider" in payload:
            module_config.provider = payload["provider"].strip()
        if "model" in payload:
            module_config.model = payload["model"].strip()
    save_config(config, diagnostics.config_path)


_SUPPORTED_PROVIDERS: tuple[str, ...] = (
    "openai",
    "claude",
    "gemini",
    "deepseek",
    "ollama",
    "openrouter",
)


# Numbered menu shown in Phase 1. Order matters (v0.3.20+):
# DeepSeek first as the default zero-friction recommendation
# (¥0.001/千 token); OpenAI / Gemini / Claude / OpenRouter for users who
# already have those keys; Ollama as the offline-only fallback (slow CPU
# inference, real hardware floor); "OpenAI 协议兼容自建网关" demoted to
# the final "(高级)" entry so 普通用户 don't pick it by mistake — most
# people who think they want it actually want option 2 (OpenAI 官方).
_LLM_MENU: tuple[tuple[str, str, str], ...] = (
    (
        "deepseek",
        "DeepSeek 官方 ★默认推荐",
        "默认 deepseek-v4-flash (V4)。¥0.001/千 token 几乎免费,国内可直连",
    ),
    (
        "openai-compat",
        "★ 第二推荐 — 中转站 / OpenAI 协议兼容服务",
        "买了中转站 Key 选这个。也覆盖 Kimi / 通义 / 智谱 / Yi / MiniMax 官方 / Azure / vLLM",
    ),
    (
        "openai",
        "OpenAI 官方",
        "默认 gpt-5-nano (最便宜的 GPT-5)。api.openai.com,需要 sk- 开头的 Key",
    ),
    (
        "gemini",
        "Gemini 官方",
        "默认 gemini-2.5-flash (稳定 / 便宜)。Google AI Studio 申请 Key,免费档每天 1500 次够用",
    ),
    (
        "claude",
        "Claude 官方",
        "默认 claude-sonnet-4-6。Anthropic console,按 token 付费,质量高",
    ),
    (
        "openrouter",
        "OpenRouter 聚合",
        "默认 openai/gpt-5-nano。一个 Key 跑多家模型,按调用计费",
    ),
    (
        "ollama",
        "本地 Ollama（完全离线）",
        "默认 qwen2.5:7b (中文好)。不要 Key / 完全免费,但需 16GB+ 内存,CPU 推理首次响应 10-60s",
    ),
)


def _print_provider_table() -> None:
    """Render the provider menu — DeepSeek default, 协议兼容 second (v0.3.27+)."""
    console.print("[bold]OpenBiliClaw 需要一个语言模型来理解你的兴趣、写推荐文案。[/bold]")
    console.print("请选一个 LLM 服务：\n")
    table = Table(show_lines=False, show_header=True)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("名称", no_wrap=True)
    table.add_column("说明")
    for index, (_, label, hint) in enumerate(_LLM_MENU, start=1):
        table.add_row(str(index), label, hint)
    console.print(table)
    console.print(
        "[dim]Tip:不确定就选 1 (DeepSeek),¥0.001/千 token 几乎免费,月度通常 ¥0.5-2。"
        "已经买了中转站 / OneAPI Key 选 2 (协议兼容);想完全离线选 7 (Ollama,但 CPU 推理慢)。[/dim]"
    )


def _resolve_menu_choice(raw: str) -> str | None:
    """Map a Phase 1 menu input to the canonical choice key.

    Accepts either the index (1..N) or the canonical name typed directly,
    e.g. "ollama" or "openai-compat". Returns None on unknown input.
    """
    raw = raw.strip().lower()
    if raw.isdigit():
        index = int(raw)
        if 1 <= index <= len(_LLM_MENU):
            return _LLM_MENU[index - 1][0]
        return None
    aliases = {
        "openai-compat": "openai-compat",
        "compat": "openai-compat",
        "openai兼容": "openai-compat",
    }
    if raw in aliases:
        return aliases[raw]
    if raw in {key for key, *_ in _LLM_MENU}:
        return raw
    return None


def _prompt_openai_compat() -> tuple[str, str, str, str]:
    """openai-compat sub-flow — preset menu → intro → base_url → key → model → embedding hint.

    All compat-protocol services write to the ``[llm.openai]`` section
    (the ``openai_provider.OpenAIProvider`` class is the universal
    Bearer-auth + ``/v1/chat/completions`` client). The sub-menu's job
    is to remove the four pain points普通用户 hit when self-configuring:

    1. **Where to register** — every preset surfaces ``signup_url``
       above the API Key prompt so the user can ``cmd-click`` it.
    2. **What this thing actually is** — ``description`` runs as a one-
       paragraph intro after preset selection, framing the strengths /
       sweet spot of the service so the user knows what they signed up
       for.
    3. **Base URL format** — auto-filled from the preset; the user just
       confirms.
    4. **No embedding endpoint** — Kimi / MiniMax / Yi / self-hosted
       don't ship embeddings, so we pre-warn the user that Phase 3
       will fall back to local Ollama bge-m3. For Qwen / GLM / Azure /
       relay (who DO have embeddings), we call out the advanced option
       to point Phase 3 at the same base_url.
    """
    console.print(
        "\n[bold]配置 OpenAI 协议兼容服务[/bold]\n"
        "[dim]这一项主要给三类用户:[/dim]\n"
        "[dim]  1. **买了中转站 / OneAPI Key**(国内付人民币用海外模型,最常见)→ 选 1[/dim]\n"
        "[dim]  2. **用国产大模型官方 API**(Kimi / 通义 / 智谱 / Yi / MiniMax / 商汤) → 选 2-7[/dim]\n"
        "[dim]  3. **企业 Azure / 自建 vLLM-LMStudio** → 选 8-9[/dim]\n"
        r"[dim]后端会按 OpenAI 协议(Bearer 鉴权 + /v1/chat/completions)打你给的 Base URL,"
        r"配置统一写到 config.toml 的 \[llm.openai] 段。[/dim]\n"
    )
    table = Table(show_lines=False, show_header=True)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("服务", no_wrap=True)
    table.add_column("Base URL")
    table.add_column("默认模型")
    for index, (_, preset) in enumerate(_OPENAI_COMPAT_PRESETS, start=1):
        bu = preset["base_url"] or "[dim](需自填)[/dim]"
        dm = preset["default_model"] or "[dim](需自填)[/dim]"
        table.add_row(str(index), preset["label"], bu, dm)
    console.print(table)
    console.print(
        "[dim]Tip: 不知道选哪个就看你的 API Key 是哪家发的—— "
        "买的中转站 / OneAPI(常见)选 1;Kimi/MiniMax/通义/智谱/Yi/商汤官方选 2-7;"
        "Azure 选 8;自建本地服务选 9。[/dim]\n"
    )
    raw = typer.prompt(f"选服务类型 (1-{len(_OPENAI_COMPAT_PRESETS)})", default="1").strip()
    try:
        choice_index = max(1, min(len(_OPENAI_COMPAT_PRESETS), int(raw))) - 1
    except ValueError:
        choice_index = 0
    preset_key, preset = _OPENAI_COMPAT_PRESETS[choice_index]

    # Per-preset intro: what is this service, and where to register.
    console.print(f"\n[bold]→ 已选: {preset['label']}[/bold]")
    if preset.get("description"):
        console.print(f"[dim]  {preset['description']}[/dim]")
    if preset.get("signup_url"):
        console.print(f"[dim]  申请 Key: [cyan]{preset['signup_url']}[/cyan][/dim]")
    if preset.get("domain_alt"):
        console.print(f"[dim]  💡 {preset['domain_alt']}[/dim]")
    console.print()

    base_url_default = preset["base_url"]
    if base_url_default:
        base_url = (
            typer.prompt(
                f"Base URL (回车 = {base_url_default})",
                default=base_url_default,
                show_default=False,
            ).strip()
            or base_url_default
        )
    else:
        base_url = typer.prompt(
            "Base URL (必填,见上面的表格)",
        ).strip()

    api_key = typer.prompt(
        f"{preset['label']} 的 API Key (本地 / 不鉴权服务可留空)",
        hide_input=True,
        default="",
        show_default=False,
    ).strip()

    if preset.get("hint"):
        console.print(f"[dim]  {preset['hint']}[/dim]")
    default_model = preset["default_model"]
    if default_model:
        model = (
            typer.prompt(
                f"模型名 (回车 = {default_model})",
                default=default_model,
                show_default=False,
            ).strip()
            or default_model
        )
    else:
        model = typer.prompt("模型名 (必填,见上面的提示)").strip()

    # Embedding heads-up — most compat-protocol vendors don't ship a
    # /v1/embeddings endpoint. Pre-warn before the user gets to Phase 3
    # so they don't think the wizard is broken when it auto-falls back.
    has_embed = preset.get("supports_embedding", "false") == "true"
    if not has_embed:
        console.print(
            f"\n[yellow]ⓘ {preset['label']} 没有 OpenAI 兼容的 embedding endpoint[/yellow]\n"
            "[dim]  Phase 3 会自动选「本地 Ollama bge-m3」给推荐管线做向量化"
            "(免费 / 离线 / 不影响主 LLM)。回车跳过即可。[/dim]"
        )
    elif preset.get("embedding_alt"):
        console.print(f"\n[dim]💡 embedding 提示: {preset['embedding_alt']}[/dim]")

    # Final confirm: show the canonical triplet so the user catches typos.
    console.print(
        f"\n[bold green]✓ 即将写入 config.toml:[/bold green]\n"
        f"  [llm.openai].base_url = [cyan]{base_url}[/cyan]\n"
        f"  [llm.openai].model    = [cyan]{model}[/cyan]"
    )
    return "openai", base_url, api_key, model


def _prompt_provider_triplet(menu_choice: str) -> tuple[str, str, str, str]:
    """Phase 2 — collect (provider, base_url, api_key, model) for the choice.

    ``menu_choice`` is the value from ``_LLM_MENU`` (e.g. ``"ollama"`` or
    ``"openai-compat"``). For ``openai-compat`` we still write to the
    ``[llm.openai]`` section but force the user to give us a Base URL —
    that's the single field that distinguishes "I'll use OpenAI the
    company" from "I have my own gateway that speaks the OpenAI API."
    """
    if menu_choice == "openai-compat":
        return _prompt_openai_compat()

    provider = menu_choice
    defaults = _PROVIDER_DEFAULTS.get(provider, {})
    default_base_url = defaults.get("base_url", "")
    default_model = defaults.get("model", "")

    if provider == "ollama":
        console.print(
            "\n[bold]配置本地 Ollama[/bold]\n"
            "[dim]我会自动帮你装/启动/拉模型，无需 API Key。第一次拉模型可能要"
            "几分钟（取决于网速）。[/dim]"
        )
        # Phase 1: ensure binary exists (install if missing, with consent).
        if not _ollama_install_if_missing():
            return provider, default_base_url, "", default_model

        # Phase 2: ensure daemon is up.
        if not _ollama_start_serve_background():
            console.print("[red]Ollama 已装好但服务没起来。请手动跑 `ollama serve` 后重试。[/red]")
            return provider, default_base_url, "", default_model

        # Phase 3: ask which model and pull if missing.
        ollama_hint = _PROVIDER_MODEL_HINT.get("ollama")
        if ollama_hint:
            console.print(f"[dim]  {ollama_hint}[/dim]")
        model = (
            typer.prompt(
                "选个 Ollama 模型（按回车 = 默认 llama3）",
                default=default_model,
            ).strip()
            or default_model
        )
        if not _ollama_has_model(model):
            console.print(f"开始拉取 {model}（首次下载耗时几分钟）…")
            if not _ollama_pull_model(model):
                console.print(f"[red]{model} 拉取失败。可以稍后手动跑 `ollama pull {model}` 再重启 backend。[/red]")
        else:
            console.print(f"[green]模型 {model} 已就绪。[/green]")
        return provider, default_base_url, "", model

    # Cloud providers: ask for key (mandatory), let model fall to default.
    console.print(f"\n[bold]配置 {_PROVIDER_HINTS.get(provider, provider)}[/bold]")
    api_key = typer.prompt(
        "API Key",
        prompt_suffix=": ",
        hide_input=True,
        default="",
        show_default=False,
    ).strip()
    # Surface the per-provider model menu before asking, so the user
    # consciously confirms the default rather than just hitting Enter
    # on an opaque string. Particularly important for DeepSeek where
    # deepseek-chat / deepseek-reasoner are deprecating 2026-07-24.
    model_hint = _PROVIDER_MODEL_HINT.get(provider)
    if model_hint:
        console.print(f"[dim]  {model_hint}[/dim]")
    model = (
        typer.prompt(
            "模型名（直接回车 = 用默认）",
            default=default_model,
            show_default=bool(default_model),
        ).strip()
        or default_model
    )
    return provider, default_base_url, api_key, model


def _interactive_embedding_setup(default_provider: str, *, auto_if_ready: bool = False) -> None:
    """Phase 3 — embedding service (v0.3.20+ "有默认值的取舍提问").

    Default = 1 (本地 Ollama bge-m3). Mirrors the question shape used by
    docs/agent-install.md: each option carries a tradeoff explanation,
    "不确定就回 1". Two advanced branches (custom OpenAI-compatible
    endpoint / pin a different provider) are kept but de-emphasized so
    普通用户 don't get derailed.

    ``auto_if_ready`` (v0.3.95+): when a local Ollama is already running
    and serving bge-m3, skip the menu entirely and just wire it up. This
    closes the "confirmed Ollama for chat but embedding stayed disabled"
    gap that silently degrades dedup. Only ``init`` passes this — the
    explicit ``setup-embedding`` command keeps the full menu so users can
    deliberately switch providers.
    """
    if auto_if_ready and _ollama_is_running() and _ollama_has_model("bge-m3"):
        _save_embedding_config(provider="ollama", model="bge-m3")
        console.print(
            "\n[bold green]检测到本地 Ollama 已就绪且装有 bge-m3,已自动启用本地 embedding"
            "(跨视频去重 / 相似度判定)。[/bold green]"
            "\n[dim]想换成 Gemini/OpenAI 或关闭,去插件设置页或重跑 "
            "`openbiliclaw setup-embedding`。[/dim]"
        )
        return
    console.print(
        "\n[bold]Embedding(向量化)服务[/bold]\n"
        "[dim]把视频标题/简介压成向量,跨视频做相似度对比 —— 决定"
        '"这条和你之前喜欢的那条是不是同一类"。和聊天 LLM 是分开的。[/dim]\n'
    )
    options = (
        (
            "1",
            "本地 Ollama bge-m3 ★默认推荐",
            "免费 / 离线 / 不消耗主 LLM 配额(自动装 Ollama + 拉 568MB 模型)",
        ),
        (
            "2",
            "云端 Gemini embedding",
            "质量略高 / 跨语言更稳;免费档每天 1500 次,日常够用,需 Gemini Key",
        ),
        (
            "3",
            "暂不启用 embedding",
            "保留独立配置为空;不会跟随主 LLM,也不会自动 fallback",
        ),
        ("4", "(高级)自定义 OpenAI 兼容服务", "vLLM / OneAPI / 自建网关 —— 自填 base_url"),
        ("5", "(高级)指定其他 provider", "手动选 provider + 模型 + 可选 base_url"),
        ("0", "跳过(不修改当前 embedding 配置)", ""),
    )
    table = Table(show_lines=False, show_header=True)
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("方案", no_wrap=True)
    table.add_column("说明")
    for label, name, desc in options:
        table.add_row(label, name, desc)
    console.print(table)
    console.print(
        "[dim]Tip:不确定就选 1。日常推荐质量已经够用且不消耗主 LLM 配额。"
        "想再准一点选 2(Gemini),需要去 https://aistudio.google.com/apikey 拿 Key。[/dim]"
    )

    choice = typer.prompt("请选择 embedding 方案", default="1").strip()

    if choice in {"0", "skip", "跳过"}:
        console.print("[dim]已跳过 embedding 配置,不修改当前设置。[/dim]")
        return

    if choice in {"1", "ollama", ""}:
        # Auto-install + start + pull. Same flow as Phase 1's Ollama
        # branch — share the helpers so the user doesn't have to learn
        # different setups for chat vs embedding.
        if not _ollama_install_if_missing():
            console.print("[yellow]Ollama 装机失败,未启用本地 embedding。[/yellow]")
            return
        if not _ollama_start_serve_background():
            console.print("[red]Ollama 已装好但服务没起来。请手动跑 `ollama serve` 后重试。[/red]")
            return

        model = "bge-m3"
        if _ollama_has_model(model):
            console.print(f"[green]已检测到本地模型 {model}[/green]")
        else:
            console.print(f"开始拉取 {model}(首次下载约 568MB,几分钟)…")
            if not _ollama_pull_model(model):
                console.print(f"[red]{model} 拉取失败,未启用本地 embedding[/red]")
                return
        _save_embedding_config(provider="ollama", model=model)
        console.print(f"[bold green]已启用本地 Ollama embedding({model})[/bold green]")
        return

    if choice in {"2", "gemini"}:
        from openbiliclaw.config import load_config

        existing_key = ""
        try:
            existing_cfg = load_config()
            existing_key = (existing_cfg.llm.gemini.api_key or "").strip()
        except Exception:
            pass

        if existing_key:
            console.print("[green]复用 [llm.gemini] 段已配置的 API Key,无需再填。[/green]")
            api_key = existing_key
        else:
            console.print(
                "[dim]去 https://aistudio.google.com/apikey 拿一个 Gemini API Key,"
                "复制粘贴到下面(免费档每天 1500 次,日常用足够)。[/dim]"
            )
            api_key = typer.prompt(
                "Gemini API Key",
                hide_input=True,
                default="",
                show_default=False,
            ).strip()
            if not api_key:
                console.print("[yellow]Key 为空,未启用 Gemini embedding。[/yellow]")
                return

        _save_embedding_config(
            provider="gemini",
            model="gemini-embedding-001",
            api_key=api_key,
        )
        console.print("[bold green]已启用 Gemini embedding(gemini-embedding-001)[/bold green]")
        return

    if choice in {"3", "follow"}:
        _save_embedding_config(provider="", model="")
        console.print(
            "[green]已设置为不启用 embedding。需要语义去重/相似度时,可之后运行 "
            "`openbiliclaw setup-embedding` 单独配置。[/green]"
        )
        return

    if choice == "4":
        base_url = typer.prompt("Embedding Base URL(OpenAI 兼容,例如 http://localhost:8000/v1)").strip()
        api_key = typer.prompt(
            "Embedding API Key(如服务无鉴权可留空)",
            hide_input=True,
            default="",
            show_default=False,
        ).strip()
        model = typer.prompt("Embedding 模型名称", default="bge-m3").strip()
        _save_embedding_config(
            provider="openai",
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        console.print(
            "[bold green]已配置自定义 OpenAI 兼容 embedding 服务"
            r"(写入 \[llm.embedding] 段)。[/bold green]"
        )
        return

    if choice == "5":
        target = (
            typer.prompt(
                "选择 provider(claude / gemini / deepseek / openrouter / ollama)",
                default="gemini",
            )
            .strip()
            .lower()
        )
        if target not in _SUPPORTED_PROVIDERS:
            console.print("[red]未知 provider,跳过 embedding 配置。[/red]")
            return
        defaults = _PROVIDER_DEFAULTS.get(target, {})
        base_url = typer.prompt(
            f"{target} Base URL(留空走默认)",
            default=defaults.get("base_url", ""),
            show_default=bool(defaults.get("base_url")),
        ).strip()
        api_key = ""
        if target != "ollama":
            api_key = typer.prompt(
                f"{target} API Key",
                hide_input=True,
                default="",
                show_default=False,
            ).strip()
        model = typer.prompt(
            "Embedding 模型名称",
            default="text-embedding-3-small" if target == "openai" else "",
            show_default=False,
        ).strip()
        if not model:
            console.print("[red]模型名为空,跳过 embedding 配置。[/red]")
            return
        _save_embedding_config(
            provider=target,
            model=model,
            base_url=base_url,
            api_key=api_key,
        )
        console.print(f"[bold green]已配置 {target} 作为 embedding provider。[/bold green]")
        return

    console.print("[red]未识别的选项,跳过 embedding 配置。[/red]")


def _interactive_module_overrides(default_provider: str) -> None:
    """Phase 4 — optional per-module LLM overrides (advanced, skippable)."""
    if not typer.confirm(
        "（高级，可跳过）是否为单个模块单独指定 provider/model？\n"
        "  典型场景：发现/评估走便宜模型，灵魂画像走高质量模型。",
        default=False,
    ):
        return

    overrides: dict[str, dict[str, str]] = {}
    modules = (
        ("soul", "灵魂画像（高质量模型，稳定性优先）"),
        ("discovery", "内容发现（吞吐量大，建议廉价模型）"),
        ("recommendation", "推荐文案（解释生成，平衡质量和成本）"),
        ("evaluation", "内容评估（高频调用，建议廉价模型）"),
    )
    for module, desc in modules:
        if not typer.confirm(f"为 [{module}] {desc} 配置覆盖？", default=False):
            continue
        provider = (
            typer.prompt(
                f"  {module} provider（留空 = 跟随默认 {default_provider}）",
                default="",
                show_default=False,
            )
            .strip()
            .lower()
        )
        if provider and provider not in _SUPPORTED_PROVIDERS:
            console.print(f"  [red]未知 provider「{provider}」，跳过该模块。[/red]")
            continue
        model = typer.prompt(
            f"  {module} 模型（留空 = 跟随 provider 默认）",
            default="",
            show_default=False,
        ).strip()
        overrides[module] = {"provider": provider, "model": model}

    if overrides:
        _save_module_overrides(overrides)
        console.print(f"[green]已写入 {len(overrides)} 个模块的 LLM 覆盖配置。[/green]")
    else:
        console.print("[dim]未配置任何模块覆盖。[/dim]")


def _interactive_runtime_config_setup() -> None:
    """Guide the user through missing LLM config before init.

    Four-phase flow:
      1) Pick LLM service (Ollama-first menu; OpenAI-compat is its own entry,
         not buried inside ``openai``).
      2) Provide the fields that option actually needs.
      3) Choose how embeddings are served (separate question, not bundled).
      4) Optional per-module overrides (advanced, default skip).
    """
    _print_page_title("初始化前配置引导", "选 LLM、配 Embedding、填 B 站 Cookie")
    _print_provider_table()

    while True:
        raw = typer.prompt("\n请输入序号或名称（默认 1=Ollama）", default="1")
        choice = _resolve_menu_choice(raw)
        if choice is None:
            console.print("[bold red]看不懂这个输入，请重新输入序号或名称[/bold red]")
            continue

        provider, base_url, api_key, model = _prompt_provider_triplet(choice)

        _save_runtime_provider_config(
            provider,
            api_key=api_key,
            base_url=base_url,
            model=model,
        )

        error = _load_runtime_config_error(render=False)
        if error is not None:
            console.print("[bold yellow]刚写入的配置仍不完整，请重新选择。[/bold yellow]")
            _print_runtime_config_error(error)
            continue

        console.print(
            "\n[bold]接下来配 Embedding[/bold]"
            "\n[dim]Embedding 是和聊天模型分开的：把视频标题/简介变成向量，"
            "用于跨视频去重和相似度判定。频次很高，所以单独拎出来配。[/dim]"
        )
        _interactive_embedding_setup(provider, auto_if_ready=True)

        console.print(
            "\n[bold]最后是 Per-module 覆盖（高级，默认可跳过）[/bold]"
            "\n[dim]给 soul / discovery / recommendation / evaluation 单独指定模型，"
            "比如发现/评估走便宜模型，画像走高质量。大多数用户不需要。[/dim]"
        )
        _interactive_module_overrides(provider)
        return


def _interactive_auth_setup(auth_manager: Any) -> Any:
    """Guide the user through Bilibili auth before init.

    Two paths since v0.3.12:
      A. Install the browser extension and let it auto-sync the cookie
         via ``POST /api/bilibili/cookie`` (recommended — zero F12).
      B. Paste the cookie manually right here (fallback for users who
         won't install the extension).
    """
    _print_page_title("初始化前认证引导", "补齐 B 站认证")
    console.print(
        "[bold]为什么需要 B 站 Cookie？[/bold]\n"
        "OpenBiliClaw 需要你的 B 站登录态来：\n"
        "  • 拉你的观看历史（用来训练画像）\n"
        "  • 以你的身份调 B 站 API 拿视频详情\n"
        "[dim]Cookie 只存在你本机 data/bilibili_cookie.json，不会上传任何地方。[/dim]\n\n"
        "[bold]两种方式（任选其一）：[/bold]\n"
        "  [cyan]1.[/cyan] 装浏览器扩展，自动同步（推荐，零配置）\n"
        "     下载: https://github.com/whiteguo233/OpenBiliClaw/releases\n"
        "     装好后扩展会几秒内自动把登录 Cookie 推到本地后端。\n"
        "     选这条会先退出 init；扩展同步完再跑 `openbiliclaw init` 即可。\n\n"
        "  [cyan]2.[/cyan] 现在手动贴 Cookie\n"
        "     1) 用 Chrome/Edge/Firefox 登录 https://www.bilibili.com\n"
        "     2) F12 → Network 标签 → 刷新 → 点任意 bilibili.com 请求\n"
        "     3) Headers 区域找到 cookie: 一行，右键复制整行 value\n"
        "     4) 把那一长串（含 SESSDATA / bili_jct / DedeUserID）粘下面\n"
    )
    choice = typer.prompt("请选 [1=装扩展自动同步 / 2=现在手贴]", default="1").strip()
    if choice in {"1", "extension", "ext", ""}:
        console.print(
            "\n[bold green]好的——退出当前 init，让扩展接手。[/bold green]\n"
            "  1. 启动后端：[cyan]openbiliclaw start[/cyan]（或保持当前 docker compose up）\n"
            "  2. 装扩展：[cyan]https://github.com/whiteguo233/OpenBiliClaw/releases[/cyan]\n"
            "  3. 确认你已登录 B 站；扩展会几秒内同步 Cookie\n"
            "  4. 再跑 [cyan]openbiliclaw init[/cyan] 完成画像生成 + 首轮发现\n"
        )
        raise typer.Exit(code=0)

    while True:
        cookie_value = typer.prompt("请粘贴 B 站 Cookie", prompt_suffix=": ")
        status = asyncio.run(auth_manager.validate_cookie(cookie_value))
        if status.authenticated:
            auth_manager.set_cookie(cookie_value)
            console.print("[bold green]登录成功[/bold green]")
            _print_auth_status(status)
            return status

        console.print("[bold red]认证失败 —— Cookie 看起来无效或过期了[/bold red]")
        _print_auth_status(status)
        if not typer.confirm("是否重试？（重新走一遍上面的步骤）", default=True):
            raise typer.Exit(code=1)


def _prepare_init_runtime() -> Any:
    """Ensure runtime config and auth are ready before init proceeds."""
    error = _load_runtime_config_error(render=False)
    if error is not None:
        if not _is_interactive_terminal():
            _print_runtime_config_error(error)
            raise typer.Exit(code=1)
        _interactive_runtime_config_setup()

    auth_manager = _build_auth_manager()
    status = asyncio.run(auth_manager.get_status())
    if status.authenticated:
        return status
    if not _is_interactive_terminal():
        console.print("[bold red]认证失败[/bold red]")
        console.print("请先执行 `openbiliclaw auth login` 完成 B 站认证。")
        raise typer.Exit(code=1)
    return _interactive_auth_setup(auth_manager)


def _format_strategy_group(strategies: list[str]) -> str:
    return " + ".join(strategies)


async def _run_init_discovery_backfill_async(
    profile: Any,
    *,
    target_pool_count: int = 100,
    label_suffix: str = "",
) -> int:
    """Backfill the initial discovery pool in stages until the target is reached."""
    from openbiliclaw.discovery.pool_snapshot import build_cold_start_pool_snapshot

    database = _get_runtime_database()
    discovery_engine = _build_discovery_engine()
    discovered_count = 0

    for index, strategies in enumerate(_INIT_DISCOVERY_PLAN, start=1):
        current_pool_count = database.count_pool_candidates()
        if current_pool_count >= target_pool_count:
            break
        request_limit = max(20, target_pool_count - current_pool_count)
        pool_snapshot = (
            build_cold_start_pool_snapshot(
                profile,
                pool_target_count=target_pool_count,
                source_targets={"bilibili": target_pool_count},
            )
            if current_pool_count <= 0
            else None
        )
        console.print(
            f"补货阶段 {index}/{len(_INIT_DISCOVERY_PLAN)}: {_format_strategy_group(strategies)}{label_suffix}"
        )
        console.print(f"当前池子 {current_pool_count}/{target_pool_count}，本轮请求上限 {request_limit}")
        discovered = await _run_with_progress(
            discovery_engine.discover(
                profile,
                strategies=strategies,
                limit=request_limit,
                # Init is latency-critical — skip the default search-first
                # phase split and let every strategy share the gather.
                fully_parallel=True,
                pool_snapshot=pool_snapshot,
            ),
            label=f"发现内容({_format_strategy_group(strategies)} 并发){label_suffix}",
            eta_seconds=300,
        )
        discovered_count += len(discovered)
        console.print(
            f"阶段完成: 当前池子 {database.count_pool_candidates()}/{target_pool_count}，本轮发现 {len(discovered)} 条"
        )

    return discovered_count


def _import_xhs_bootstrap_events() -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Backwards-compatible single-shot wrapper used by tests.

    For the live ``init`` flow we use the split enqueue/collect API
    above so xhs data collection runs in parallel with B站 fetches
    instead of serialising for a fixed wait. This wrapper preserves
    the old test contract.
    """
    task_id = _enqueue_xhs_bootstrap_task()
    events, counts, _status = _collect_xhs_bootstrap_events(task_id)
    return events, counts


def _event_memory_key(event: dict[str, Any]) -> tuple[str, str, str, str, str]:
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    source = str(metadata.get("source_platform") or "").strip()
    event_type = str(event.get("event_type") or event.get("type") or "").strip()
    url = str(event.get("url") or "").strip()
    content_id = str(metadata.get("content_id") or "").strip()
    import_source = str(metadata.get("import_source") or "").strip()
    title = str(event.get("title") or "").strip()
    identity = content_id or url or title
    return source, event_type, identity, import_source, url


def _load_existing_event_keys(memory: Any, *, limit: int) -> set[tuple[str, str, str, str, str]]:
    query_events = getattr(memory, "query_events", None)
    if not callable(query_events):
        return set()
    try:
        rows = query_events(limit=limit)
    except Exception:
        return set()

    import json as _json

    keys: set[tuple[str, str, str, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = dict(row)
        metadata = event.get("metadata")
        if isinstance(metadata, str):
            try:
                parsed = _json.loads(metadata)
                event["metadata"] = parsed if isinstance(parsed, dict) else {}
            except _json.JSONDecodeError:
                event["metadata"] = {}
        keys.add(_event_memory_key(event))
    return keys


def _write_events_to_memory(events: list[dict[str, Any]], *, source: str = "") -> tuple[int, int]:
    """Persist collected source events to memory with a lightweight duplicate guard."""
    if not events:
        return 0, 0

    memory = _build_memory_manager()
    existing_keys = _load_existing_event_keys(memory, limit=max(10_000, len(events) * 4))
    batch_keys: set[tuple[str, str, str, str, str]] = set()
    fresh: list[dict[str, Any]] = []
    for event in events:
        key = _event_memory_key(event)
        if key in existing_keys or key in batch_keys:
            continue
        if source:
            metadata = event.get("metadata")
            if isinstance(metadata, dict):
                metadata.setdefault("source_platform", source)
        batch_keys.add(key)
        fresh.append(event)

    async def _propagate() -> None:
        for event in fresh:
            await memory.propagate_event(event)

    asyncio.run(_propagate())
    return len(fresh), len(events) - len(fresh)


def _enqueue_zhihu_search_task(
    keywords: tuple[str, ...],
    *,
    max_items_per_keyword: int = 20,
) -> str | None:
    """Enqueue a Zhihu plugin search task for the browser extension."""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    normalized_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        value = str(keyword).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized_keywords.append(value)
    if not normalized_keywords:
        console.print("  [yellow]知乎搜索任务未入队: 关键词为空。[/yellow]")
        return None

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]知乎搜索任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        cfg = load_config()
        budget = int(getattr(getattr(cfg.sources, "zhihu", None), "daily_search_budget", 0))
    except Exception:
        budget = 0

    try:
        queue = ZhihuTaskQueue(database)
        task_id = queue.enqueue_with_id(
            "search",
            {
                "keywords": normalized_keywords,
                "max_items_per_keyword": max(1, int(max_items_per_keyword)),
            },
            daily_budget=budget,
        )
    except Exception as exc:
        console.print(f"  [yellow]知乎搜索任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]知乎搜索任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("zhihu")
    return task_id


def _collect_zhihu_search_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin search task and return raw Zhihu candidates."""
    import json
    import time

    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    if not task_id:
        return [], {}, "skipped"

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = ZhihuTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "zhihu_login_required" or (isinstance(debug, dict) and debug.get("login_required") is True):
            return [], {}, "login_required"
        return [], {}, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"

    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    count = len(items)
    if isinstance(raw_counts, dict):
        with suppress(Exception):
            count = int(raw_counts.get("zhihu_search", count) or count)
    status_label = "ok" if items else "empty"
    return items, {"zhihu_search": count}, status_label


def _enqueue_zhihu_discovery_task(
    task_type: str,
    payload: dict[str, object],
    *,
    daily_budget_key: str,
) -> str | None:
    """Enqueue a non-search Zhihu plugin discovery task."""
    from openbiliclaw.config import load_config
    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]知乎 {task_type} 任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        cfg = load_config()
        budget = int(getattr(getattr(cfg.sources, "zhihu", None), daily_budget_key, 0))
    except Exception:
        budget = 0

    try:
        queue = ZhihuTaskQueue(database)
        task_id = queue.enqueue_with_id(task_type, payload, daily_budget=budget)
    except Exception as exc:
        console.print(f"  [yellow]知乎 {task_type} 任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        console.print(f"  [yellow]知乎 {task_type} 任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("zhihu")
    return task_id


def _collect_zhihu_discovery_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin Zhihu discovery task and return raw candidates."""
    import json
    import time

    from openbiliclaw.sources.zhihu_tasks import ZhihuTaskQueue

    if not task_id:
        return [], {}, "skipped"
    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = ZhihuTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        try:
            result = json.loads(str(task.get("result_json") or "{}"))
        except json.JSONDecodeError:
            result = {}
        debug = result.get("debug", {}) if isinstance(result, dict) else {}
        error = str(result.get("error", "") if isinstance(result, dict) else "")
        if error == "zhihu_login_required" or (isinstance(debug, dict) and debug.get("login_required") is True):
            return [], {}, "login_required"
        return [], {}, "failed"
    if task.get("status") != "completed":
        if str(task.get("status", "")).strip() in {"pending", "in_progress"}:
            with suppress(Exception):
                queue.fail(
                    task_id,
                    error="extension_result_timeout",
                    debug={
                        "wait_seconds": max_wait_seconds,
                        "last_status": str(task.get("status", "")),
                    },
                )
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"
    items = [v for v in result.get("items", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    scope_counts = {str(k): int(v) for k, v in raw_counts.items()} if isinstance(raw_counts, dict) else {}
    return items, scope_counts, "ok" if items else "empty"


def _enqueue_zhihu_discovery_candidates(items: list[dict[str, Any]]) -> tuple[int, list[Any]]:
    """Convert Zhihu search result rows and enqueue them into discovery_candidates."""
    from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
    from openbiliclaw.sources.zhihu_tasks import zhihu_discovery_items_to_contents

    contents = zhihu_discovery_items_to_contents(items)
    if not contents:
        return 0, []
    database = _get_runtime_database()
    writes = [discovered_content_to_candidate_write(item, source_context=item.source_strategy) for item in contents]
    enqueued = int(database.enqueue_discovery_candidates(writes))
    return enqueued, contents


def _enqueue_dy_search_task(
    keywords: tuple[str, ...],
    *,
    max_items_per_keyword: int = 20,
) -> str | None:
    """Enqueue a Douyin plugin search task for the browser extension."""
    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    normalized_keywords = []
    seen: set[str] = set()
    for keyword in keywords:
        value = str(keyword).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        normalized_keywords.append(value)
    if not normalized_keywords:
        console.print("  [yellow]抖音搜索任务未入队: 关键词为空。[/yellow]")
        return None

    try:
        database = _get_runtime_database()
    except Exception as exc:
        console.print(f"  [yellow]抖音搜索任务未入队: 数据库不可用: {exc}[/yellow]")
        return None
    if not hasattr(database, "conn"):
        return None

    try:
        queue = DyTaskQueue(database)
        task_id = queue.enqueue_with_id(
            "search",
            {
                "keywords": normalized_keywords,
                "max_items_per_keyword": max(1, int(max_items_per_keyword)),
            },
            daily_budget=20,
        )
    except Exception as exc:
        console.print(f"  [yellow]抖音搜索任务未入队: {exc}[/yellow]")
        return None
    if not task_id:
        console.print("  [yellow]抖音搜索任务未入队: 今日任务预算已用完。[/yellow]")
        return None
    _kick_task_dispatcher("dy")
    return task_id


def _collect_dy_search_results(
    task_id: str | None,
    *,
    max_wait_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    """Wait for a plugin search task and return raw Douyin video candidates."""
    import json
    import time

    from openbiliclaw.sources.dy_tasks import DyTaskQueue

    if not task_id:
        return [], {}, "skipped"

    try:
        database = _get_runtime_database()
    except Exception:
        return [], {}, "skipped"
    if not hasattr(database, "conn"):
        return [], {}, "skipped"

    queue = DyTaskQueue(database)
    deadline = time.monotonic() + max(0.0, max_wait_seconds)
    task: dict[str, Any] | None = None
    while True:
        task = queue.get(task_id)
        status = str((task or {}).get("status", "")).strip()
        if status in {"completed", "failed"}:
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(0.5)

    if not task:
        return [], {}, "timeout"
    if task.get("status") == "failed":
        return [], {}, "failed"
    if task.get("status") != "completed":
        return [], {}, "timeout"

    try:
        result = json.loads(str(task.get("result_json") or "{}"))
    except json.JSONDecodeError:
        return [], {}, "failed"

    videos = [v for v in result.get("videos", []) if isinstance(v, dict)]
    raw_counts = result.get("scope_counts", {})
    count = len(videos)
    if isinstance(raw_counts, dict):
        with suppress(Exception):
            count = int(raw_counts.get("dy_search", count) or count)
    status_label = "ok" if videos else "empty"
    return videos, {"dy_search": count}, status_label


@app.command("setup-embedding")
def setup_embedding() -> None:
    """配置本地 Ollama 作为 embedding 兜底服务（可选）.

    init 时已经问过；如果当时没启用、之后想加上，跑这条命令再走一次引导。
    """
    _print_page_title("配置本地 embedding", "Ollama + bge-m3")
    from openbiliclaw.config import load_config_with_diagnostics

    config, _ = load_config_with_diagnostics()
    _interactive_embedding_setup(config.llm.default_provider)


@app.command()
def start(
    host: str = typer.Option("", "--host", help="API 监听地址（默认读 config.toml [api].host）"),
    port: int = typer.Option(0, "--port", min=0, max=65535, help="API 监听端口（默认读 config.toml [api].port）"),
) -> None:
    """启动 OpenBiliClaw Agent."""
    from openbiliclaw.config import load_config

    cfg = load_config()
    effective_host = host if host else cfg.api.host
    effective_port = port if port else cfg.api.port
    _print_page_title("启动 OpenBiliClaw", "本地 API 服务")
    _ensure_runtime_database_healthy()
    _print_status_panel(
        "info",
        "API 服务",
        f"正在启动本地后端，当前监听 {effective_host}:{effective_port}。",
    )
    _warn_if_pause_on_disconnect_requires_presence()
    if cfg.api.auth.enabled:
        _print_status_panel(
            "info",
            "🔒 访问控制",
            "局域网/远程访问已启用密码登录（本机访问免登录）。",
        )
        if cfg.api.auth.trust_loopback and not cfg.api.auth.trusted_proxies:
            _print_status_panel(
                "warning",
                "反向代理提醒",
                "如部署在同机反向代理后，请配置 [api.auth].trusted_proxies"
                "（并确保代理覆盖而非透传客户端转发头），或让代理自行鉴权，"
                "否则远程请求可能被误判为本机而绕过密码。",
            )
    _maybe_create_runtime_database_backup()
    _preflight_loopback_ollama(cfg)
    _self_heal_autostart_registration(cfg)
    _run_api_server(host=effective_host, port=effective_port)


def _bump_auth_epoch(cfg: Any) -> bool:
    """Bump the revocation epoch in the runtime DB (immediate logout-all)."""
    from openbiliclaw.storage.database import Database

    db = Database(cfg.data_path / "openbiliclaw.db")
    try:
        db.initialize()
        db.bump_auth_epoch()
        return True
    except Exception:
        return False
    finally:
        with suppress(Exception):
            db.close()


def _rebase_auth_fingerprint(cfg: Any) -> None:
    """Re-store the password fingerprint under cfg's CURRENT signing secret.

    Called after ``--rotate-secret`` so the next startup reconcile sees the
    fingerprint it would itself compute (under the new secret) and does NOT
    perform a redundant epoch bump on top of the one we already did. Best-effort:
    if the DB is unwritable we simply leave the stale fingerprint, which only
    costs one harmless extra reconcile bump on restart. See ``set_password_fingerprint``.
    """
    from openbiliclaw.auth_core import password_fingerprint
    from openbiliclaw.config import get_auth_plain_password
    from openbiliclaw.storage.database import Database

    auth = cfg.api.auth
    if not (auth.password_hash.strip() and auth.session_secret.strip()):
        return
    fingerprint = password_fingerprint(
        auth.session_secret,
        plain=get_auth_plain_password(),
        password_hash=auth.password_hash,
    )
    db = Database(cfg.data_path / "openbiliclaw.db")
    try:
        db.initialize()
        db.set_password_fingerprint(fingerprint)
    except Exception:
        # Best-effort: a stale fingerprint only costs one harmless reconcile bump.
        pass
    finally:
        with suppress(Exception):
            db.close()


@app.command("set-password")
def set_password(
    disable: bool = typer.Option(False, "--disable", help="关闭密码门禁"),
    logout_all: bool = typer.Option(False, "--logout-all", help="使所有设备的登录态立即失效（不改密码/密钥）"),
    rotate_secret: bool = typer.Option(False, "--rotate-secret", help="轮换会话签名密钥（最强撤销，需重启后端生效）"),
) -> None:
    """设置 / 修改局域网访问密码（或关闭门禁 / 登出所有设备）。"""
    import secrets as _secrets

    from openbiliclaw.auth_core import hash_password
    from openbiliclaw.config import load_config, save_config

    cfg = load_config()

    if logout_all:
        # DB-only revocation — always effective, independent of env/config source.
        ok = _bump_auth_epoch(cfg)
        _print_status_panel(
            "success" if ok else "error",
            "已登出所有设备" if ok else "操作失败",
            "所有设备需重新登录。" if ok else "无法访问运行库、未能撤销，请确认 data 目录可写后重试。",
        )
        if not ok:
            raise typer.Exit(code=1)
        return

    # Config-writing paths below all call save_config(cfg), which writes the WHOLE
    # [api.auth] block. cfg came from load_config(), where env vars take precedence
    # over config.toml — so ANY auth env override would be (a) re-applied on restart
    # (the file edit silently lost) and (b) baked into config.toml as a literal,
    # leaving a stale value behind once the env var is later removed (this could
    # quietly shift the trust boundary / session lifetime). Refuse loudly on the
    # full override surface — not just the password — and tell the user to manage
    # via env instead (review r3#2). `--logout-all` returned above, so it stays
    # usable for an emergency revoke even while env-managed.
    from openbiliclaw.config import API_AUTH_ENV_VARS

    _auth_env = [name for name in API_AUTH_ENV_VARS if (os.environ.get(name) or "").strip()]
    if _auth_env:
        _print_status_panel(
            "error",
            "检测到环境变量覆盖，config 修改不会生效",
            f"已设置 {', '.join(_auth_env)}；load_config 中环境变量优先于 config.toml，"
            "改写文件重启后仍会用旧的环境变量值。请改这些环境变量并重启后端；"
            "如只想立即失效现有登录态，用 `openbiliclaw set-password --logout-all`。",
        )
        raise typer.Exit(code=1)

    # config.local.toml is merged OVER config.toml (local wins). If it pins any of
    # the credential fields set-password writes, our config.toml edit silently
    # reverts on restart — refuse loudly rather than report a false success (r9).
    from openbiliclaw.config import config_local_auth_keys

    _local_keys = sorted(config_local_auth_keys() & {"password", "password_hash", "enabled", "session_secret"})
    if _local_keys:
        _print_status_panel(
            "error",
            "config.local.toml 覆盖了 [api.auth] 字段，config.toml 修改不会生效",
            f"config.local.toml 中设置了 {', '.join(_local_keys)}；它会盖过 config.toml，"
            "改写后者重启后仍会被覆盖。请改 config.local.toml 并重启后端；"
            "如只想立即失效现有登录态，用 `openbiliclaw set-password --logout-all`。",
        )
        raise typer.Exit(code=1)

    if disable:
        cfg.api.auth.enabled = False
        save_config(cfg)
        _print_status_panel("success", "已关闭密码门禁", "重启后端 (openbiliclaw start) 后生效。")
        return

    if rotate_secret:
        cfg.api.auth.session_secret = _secrets.token_urlsafe(32)
        save_config(cfg)
        revoked = _bump_auth_epoch(cfg)
        if not revoked:
            _print_status_panel(
                "error",
                "密钥已轮换，但未能立即撤销",
                "新密钥已写入 config，但运行库不可写、现有登录态未即时失效。"
                "请重启后端使其生效，或修复 data 目录后重试。",
            )
            raise typer.Exit(code=1)
        # Re-base the stored fingerprint under the NEW secret so the next restart's
        # reconcile doesn't perform a redundant epoch bump on top of this one.
        _rebase_auth_fingerprint(cfg)
        _print_status_panel(
            "success",
            "已轮换会话密钥",
            "所有设备需重新登录；重启后端使新密钥完全生效。",
        )
        return

    if not _is_interactive_terminal():
        _print_status_panel(
            "error",
            "无法设置密码",
            "请在交互式终端运行，或用 OPENBILICLAW_API_AUTH_PASSWORD 环境变量配置。",
        )
        raise typer.Exit(code=1)

    password = str(typer.prompt("设置访问密码", hide_input=True, confirmation_prompt=True) or "").strip()
    if not password:
        _print_status_panel("error", "密码为空", "未做更改。")
        raise typer.Exit(code=1)

    cfg.api.auth.password_hash = hash_password(password)
    cfg.api.auth.enabled = True
    if not cfg.api.auth.session_secret.strip():
        cfg.api.auth.session_secret = _secrets.token_urlsafe(32)
    save_config(cfg)
    # Revoke all existing sessions immediately (read live from SQLite by any
    # running backend) so a compromised-password rotation does not leave old
    # cookies valid until the next restart. The NEW password itself only takes
    # effect once the backend reloads its config, hence the restart notice.
    revoked = _bump_auth_epoch(cfg)
    if not revoked:
        _print_status_panel(
            "error",
            "密码已保存，但未能立即撤销现有登录态",
            "新密码已写入 config，但运行库不可写、现有 cookie 未即时失效（仍可能有效到重启）。"
            "请重启后端使其生效，或修复 data 目录后重跑 `set-password`。",
        )
        raise typer.Exit(code=1)
    _print_status_panel(
        "success",
        "已设置访问密码",
        "已立即失效所有现有登录态。请重启后端 (openbiliclaw start) 使新密码生效"
        "（运行中的进程仍持旧配置，重启前请勿依赖新密码已启用）。",
    )


@app.command("serve-api")
def serve_api(
    host: str = typer.Option("0.0.0.0", "--host", help="API 监听地址"),
    port: int = typer.Option(8420, "--port", min=1, max=65535, help="API 监听端口"),
) -> None:
    """启动容器友好的 API 服务入口."""
    _print_page_title("启动 OpenBiliClaw", "容器 API 服务")
    _print_status_panel(
        "info",
        "API 服务",
        f"正在启动容器友好的后端入口，当前监听 {host}:{port}。",
    )
    _warn_if_pause_on_disconnect_requires_presence()
    _run_api_server(host=host, port=port)


@app.command("db-repair")
def db_repair() -> None:
    """检查并修复本地 SQLite 数据库。"""
    result = _run_db_repair()
    console.print(result.message)
    if getattr(result, "db_backup", None) is not None:
        console.print(f"备份文件: {result.db_backup}")
    if getattr(result, "wal_backup", None) is not None:
        console.print(f"WAL 备份: {result.wal_backup}")
    if getattr(result, "repaired_db", None) is not None:
        console.print(f"恢复副本: {result.repaired_db}")
    if result.status in {"in_use", "failed"}:
        raise typer.Exit(code=1)


_BILIBILI_STRATEGY_NAMES = ("search", "trending", "explore", "related_chain")


def _normalize_strategy_names(raw: list[str] | None) -> list[str]:
    """Split comma-separated values and validate strategy names."""
    if not raw:
        return []
    names: list[str] = []
    for token in raw:
        for part in token.split(","):
            name = part.strip()
            if name:
                names.append(name)
    unknown = [n for n in names if n not in _BILIBILI_STRATEGY_NAMES]
    if unknown:
        allowed = ", ".join(_BILIBILI_STRATEGY_NAMES)
        raise typer.BadParameter(f"未知的 Bilibili 策略：{', '.join(unknown)}。可选：{allowed}")
    # Preserve first-seen order, drop duplicates.
    seen: set[str] = set()
    deduped: list[str] = []
    for name in names:
        if name not in seen:
            seen.add(name)
            deduped.append(name)
    return deduped


@app.command()
def config_show() -> None:
    """显示当前配置."""
    from openbiliclaw.config import load_config_with_diagnostics
    from openbiliclaw.llm import RegistryBuildError
    from openbiliclaw.llm._compat_registry import summarize_registry

    cfg, diagnostics = load_config_with_diagnostics()
    _print_page_title("当前配置概览", "运行时配置")
    rows = [
        ("语言", cfg.language),
        ("LLM", cfg.llm.default_provider),
        ("LLM 并发", str(cfg.llm.concurrency)),
        ("B站认证", cfg.bilibili.auth_method),
        ("定时任务", "开启" if cfg.scheduler.enabled else "关闭"),
        ("停止后台 LLM 请求", "否" if cfg.scheduler.enabled else "是"),
        (
            "浏览器断开后暂停",
            _format_pause_on_disconnect_status(
                enabled=cfg.scheduler.pause_on_extension_disconnect,
                grace_seconds=cfg.scheduler.extension_disconnect_grace_seconds,
            ),
        ),
        ("开机自启动", _format_autostart_config_status(cfg)),
        ("数据目录", str(cfg.data_path)),
    ]
    if diagnostics.config_path:
        rows.append(("配置文件", str(diagnostics.config_path)))
    _print_key_value_table("配置项", rows)

    try:
        registry = _build_registry()
        summary = summarize_registry(cfg, registry)
        _print_key_value_table(
            "Provider 概览",
            [
                ("已注册 Provider", ", ".join(summary.registered_providers)),
                ("最终默认 Provider", summary.effective_default),
            ],
        )
    except RegistryBuildError as exc:
        _print_key_value_table(
            "Provider 概览",
            [
                ("已注册 Provider", "无"),
                ("Provider 状态", str(exc)),
            ],
        )

    hints = diagnostics.messages + [f"{issue.field}: {issue.message}" for issue in diagnostics.issues]
    _print_config_guidance(hints)


@auth_app.command("login")
def auth_login(
    cookie: str | None = typer.Option(None, "--cookie", help="直接传入完整 Cookie"),
) -> None:
    """交互式设置并验证 B 站 Cookie."""
    manager = _build_auth_manager()
    cookie_value = cookie or typer.prompt("请输入 B 站 Cookie", prompt_suffix=": ")
    status = asyncio.run(manager.validate_cookie(cookie_value))
    if not status.authenticated:
        console.print("[bold red]认证失败[/bold red]")
        _print_auth_status(status)
        raise typer.Exit(code=1)

    manager.set_cookie(cookie_value)
    console.print("[bold green]登录成功[/bold green]")
    _print_auth_status(status)


@auth_app.command("status")
def auth_status() -> None:
    """查看当前 B 站 Cookie 认证状态."""
    manager = _build_auth_manager()
    status = asyncio.run(manager.get_status())
    _print_auth_status(status)


@login_app.command("codex")
def login_codex(
    import_credentials: bool = _CODEX_LOGIN_IMPORT_OPTION,
    source: Path | None = _CODEX_LOGIN_SOURCE_OPTION,
    status: bool = _CODEX_LOGIN_STATUS_OPTION,
    logout: bool = _CODEX_LOGIN_LOGOUT_OPTION,
) -> None:
    """导入或管理 Codex CLI 的 ChatGPT OAuth 凭据."""
    from datetime import datetime

    from openbiliclaw.llm.codex_auth import (
        CodexAuthError,
        CodexCredentials,
        delete_codex_credentials,
        import_codex_credentials,
        load_codex_credentials,
        run_codex_cli_login,
    )

    def _print_codex_credentials(credentials: CodexCredentials) -> None:
        expires = datetime.fromtimestamp(credentials.expires_at).strftime("%Y-%m-%d %H:%M:%S")
        state = "临期/需刷新" if credentials.is_expired() else "有效"
        _print_key_value_table(
            "Codex OAuth",
            [
                ("状态", f"已登录（{state}）"),
                ("账号", credentials.account_id or "（未知）"),
                ("过期时间", expires),
            ],
        )

    if status:
        credentials = load_codex_credentials()
        if credentials is None:
            _print_status_panel(
                "warning",
                "Codex OAuth",
                "未登录。请运行 `openbiliclaw login codex` 或 `openbiliclaw login codex --import`。",
            )
            return
        _print_codex_credentials(credentials)
        return

    if logout:
        deleted = delete_codex_credentials()
        body = "已登出 Codex OAuth。" if deleted else "本地没有 Codex OAuth 凭据。"
        _print_status_panel("success" if deleted else "info", "Codex OAuth", body)
        return

    try:
        if import_credentials or source is not None:
            credentials = import_codex_credentials(source=source)
        else:
            try:
                credentials = import_codex_credentials()
            except CodexAuthError:
                console.print("[dim]未找到可导入的 Codex 凭据，启动 `codex login`...[/dim]")
                run_codex_cli_login()
                credentials = import_codex_credentials()
    except CodexAuthError as exc:
        _print_status_panel("error", "Codex OAuth 登录失败", str(exc))
        raise typer.Exit(code=1) from exc

    _print_status_panel("success", "Codex OAuth", "登录凭据已导入。")
    _print_codex_credentials(credentials)


@app.command("health-check")
def health_check() -> None:
    """检查当前已注册 LLM provider 的可用性."""
    from openbiliclaw.llm import RegistryBuildError

    try:
        registry = _build_registry()
    except RegistryBuildError as exc:
        _print_status_panel("error", "Provider 健康检查失败", str(exc))
        raise typer.Exit(code=1) from exc

    results = asyncio.run(registry.health_check_all())
    _print_page_title("Provider 健康检查", "已注册 LLM Provider 状态")
    for name, result in results.items():
        status = "可用" if result.available else "不可用"
        default_label = " (default)" if result.is_default else ""
        console.print(f"  {name}{default_label}: {status}")
        if result.error:
            console.print(f"    原因: {result.error}")


@browser_app.command("status")
def browser_status() -> None:
    """检查 agent-browser 是否可用."""
    browser = _build_browser()
    _print_browser_status(browser)
    if browser.is_available:
        return
    console.print(f"  安装提示: {browser.get_install_hint()}")
    raise typer.Exit(code=1)


@browser_app.command("open")
def browser_open(url: str) -> None:
    """通过 agent-browser 打开一个页面."""
    from openbiliclaw.bilibili.browser import BrowserCommandError

    browser = _build_browser()
    if not browser.is_available:
        _print_status_panel("error", "agent-browser 未安装", browser.get_install_hint())
        raise typer.Exit(code=1)

    try:
        asyncio.run(browser.navigate(url))
    except BrowserCommandError as exc:
        _print_status_panel("error", "浏览器操作失败", str(exc))
        raise typer.Exit(code=1) from exc

    _print_page_title("浏览器已打开")
    _print_key_value_table("目标地址", [("URL", url)])


@browser_app.command("content")
def browser_content(url: str) -> None:
    """抓取当前页面可见文本."""
    from openbiliclaw.bilibili.browser import BrowserCommandError

    browser = _build_browser()
    if not browser.is_available:
        _print_status_panel("error", "agent-browser 未安装", browser.get_install_hint())
        raise typer.Exit(code=1)

    try:
        content = asyncio.run(browser.get_page_content(url))
    except BrowserCommandError as exc:
        _print_status_panel("error", "浏览器操作失败", str(exc))
        raise typer.Exit(code=1) from exc

    _print_page_title("页面内容")
    console.print(Panel(content, border_style="cyan"))


if __name__ == "__main__":
    app()
