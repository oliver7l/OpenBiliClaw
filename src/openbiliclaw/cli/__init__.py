"""CLI interface for OpenBiliClaw.

Provides the command-line entry point using Typer.
"""

from __future__ import annotations

import asyncio
import os
import sys
from contextlib import suppress
from typing import Any

import typer

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
    _format_autostart_config_status,  # noqa: F401 — _cmd_service 经 cli 命名空间动态取
    autostart_app,
)
from openbiliclaw.cli._cmd_notes import note_app  # noqa: E402,F401

app.add_typer(autostart_app, name="autostart")
app.add_typer(note_app, name="note")

# 运行时配置写入 + 交互引导族已抽至 cli/_cmd_config.py（P4 第八刀）；该模块
# 顶层不 import 本包，此处顶层导入无循环依赖。该族不含 typer 命令（纯 helper +
# 菜单常量），故无需 register()。re-export 的 20 个符号：4 个 tests/cli 直引、
# 1 个测试 patch 补丁点、3 个本文件其它命令仍在调用、6 个菜单常量兼容外部导入。
from openbiliclaw.cli._cmd_config import (  # noqa: E402,F401
    _LLM_MENU,  # 菜单常量（外部 `from openbiliclaw.cli import _LLM_MENU` 兼容）
    _OPENAI_COMPAT_PRESETS,  # 菜单常量
    _PROVIDER_DEFAULTS,  # 菜单常量
    _PROVIDER_HINTS,  # 菜单常量
    _PROVIDER_MODEL_HINT,  # 菜单常量
    _SUPPORTED_PROVIDERS,  # 菜单常量
    _interactive_auth_setup,  # _prepare_init_runtime 调用
    _interactive_embedding_setup,  # setup-embedding 命令调用
    _interactive_module_overrides,
    _interactive_runtime_config_setup,  # _prepare_init_runtime 调用
    _ollama_has_model,  # re-export：tests/cli 直引
    _ollama_install_if_missing,
    _ollama_pull_model,
    _print_provider_table,
    _prompt_openai_compat,
    _prompt_provider_triplet,
    _resolve_menu_choice,
    _save_embedding_config,  # re-export：tests/cli 直引 + patch 补丁点
    _save_module_overrides,  # re-export：tests/cli 直引 + patch 补丁点
    _save_runtime_provider_config,  # re-export：tests/cli 直引 + patch 补丁点
)

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

# 服务与运维命令组（start / serve-api / config-show / auth / login / browser /
# set-password / db-repair / health-check / setup-embedding）已抽至
# cli/_cmd_service.py（P4 第九刀）；该模块顶层不 import 本包，此处顶层注册无
# 循环依赖。三个子 Typer（auth / login / browser）由本文件定义并挂载，注册时传入。
from openbiliclaw.cli._cmd_service import (  # noqa: E402,F401
    _bump_auth_epoch,  # re-export：测试 patch 补丁点（A′ 类，块内调用走 _cli.X）
    _normalize_strategy_names,  # re-export：_cmd_fetch 经 cli 命名空间调用
    _rebase_auth_fingerprint,  # re-export：set-password 经 _cli.X 调用
)
from openbiliclaw.cli._cmd_service import register as _register_service_commands  # noqa: E402

_register_service_commands(app, auth_app, login_app, browser_app)

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

# 运行时构建族已抽至 cli/_build.py（P4 第十一刀）；该模块不含 typer 命令，
# 故无需 register()。re-export 全部 16 个符号：14 个是测试 patch 到 cli
# 命名空间的补丁点（_build_* / _run_api_server / _run_db_repair / DB 健康族）
# 或既有 _cli.X 动态取调用方的解析目标；_runtime_database_path /
# _runtime_backup_dir 供本文件与子模块直引。
from openbiliclaw.cli._build import (  # noqa: E402,F401
    _build_auth_manager,
    _build_bilibili_client,
    _build_browser,
    _build_dialogue,
    _build_discovery_engine,
    _build_memory_manager,
    _build_recommendation_engine,
    _build_registry,
    _build_soul_engine,
    _build_usage_recorder,
    _ensure_runtime_database_healthy,
    _maybe_create_runtime_database_backup,
    _run_api_server,
    _run_db_repair,
    _runtime_backup_dir,
    _runtime_database_path,
)

# 知乎 / 抖音任务入队-收集 + 事件落库 helper 已抽至 cli/_collect.py（P4 第十刀）；
# 该模块不含 typer 命令，故无需 register()。re-export 全部 11 个符号：
# 9 个是测试 patch 到 cli 命名空间的补丁点 / tests/cli 直引，其余供
# _cmd_fetch 经 cli 命名空间动态取（_write_events_to_memory / 各 _enqueue_*）。
from openbiliclaw.cli._collect import (  # noqa: E402,F401
    _collect_dy_search_results,
    _collect_zhihu_discovery_results,
    _collect_zhihu_search_results,
    _enqueue_dy_search_task,
    _enqueue_zhihu_discovery_candidates,  # 测试 patch 补丁点
    _enqueue_zhihu_discovery_task,  # 测试 patch 补丁点
    _enqueue_zhihu_search_task,  # 测试 patch 补丁点
    _event_memory_key,
    _import_xhs_bootstrap_events,  # 测试 patch 补丁点 / tests/cli 直引
    _load_existing_event_keys,
    _write_events_to_memory,  # _cmd_fetch 经 _cli. 调用
)

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


if __name__ == "__main__":
    app()
