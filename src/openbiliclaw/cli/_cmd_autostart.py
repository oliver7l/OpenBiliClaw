"""CLI 开机自启动命令组（``openbiliclaw autostart *``）。

从上帝文件 ``cli/__init__.py`` 抽出（P4 第四刀，2026-09-13）。
本模块顶层**不得** import ``openbiliclaw.cli``（避免循环 import）。

patch 语义说明：测试（tests/cli/test_cli.py autostart 系列与
tests/auth/test_autostart.py）patch 的是**本体模块**
``openbiliclaw.runtime.autostart``（register/unregister/get_manager）
与 ``runtime.autostart.guards``（autostart_shadowed），因此 handler
内保持「函数体内 ``from ... import autostart``」的延迟导入惯例，
patch 点不受抽离影响。
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any

import typer

from openbiliclaw.cli._render import (
    _print_key_value_table,
    _print_page_title,
    _print_status_panel,
)

autostart_app = typer.Typer(help="开机自启动命令")


def _autostart_reason_message(reason: str) -> str:
    if reason == "unsupported_docker_runtime":
        return "当前在 Docker / 容器环境中，不支持注册桌面登录自启动。"
    if reason == "unsupported_platform":
        return "当前平台暂不支持开机自启动。"
    if reason == "env_managed":
        return "检测到环境变量配置，登录会话可能拿不到这些值；请先写入 config.toml。"
    if reason == "shadowed":
        return "config.local.toml 正在覆盖 [autostart].enabled，config.toml 修改不会生效。"
    if reason == "registration_failed":
        return "系统自启动注册失败，config 已回滚。"
    if reason == "unregister_failed":
        return "系统自启动注销失败，config 未修改。"
    return "无法完成开机自启动操作。"


def _autostart_status_rows(cfg: Any) -> list[tuple[str, str]]:
    from openbiliclaw.runtime import autostart

    state = autostart.status()
    enabled = bool(getattr(getattr(cfg, "autostart", None), "enabled", False))
    manage_ollama = bool(getattr(getattr(cfg, "autostart", None), "manage_ollama", True))
    return [
        ("配置", "开启" if enabled else "关闭"),
        ("系统注册", "已注册" if state.registered else "未注册"),
        ("支持状态", "支持" if state.supported else "不支持"),
        ("平台", state.platform),
        ("机制", state.mechanism),
        ("原因", state.reason),
        ("Ollama 预检", "开启" if manage_ollama else "关闭"),
    ]


def _print_autostart_status(cfg: Any) -> None:
    _print_page_title("开机自启动", "登录系统时自动拉起 OpenBiliClaw 后端")
    _print_key_value_table("自启动状态", _autostart_status_rows(cfg))


def _format_autostart_config_status(cfg: Any) -> str:
    from openbiliclaw.runtime import autostart

    try:
        state = autostart.status()
    except Exception:
        return "开启" if bool(getattr(cfg.autostart, "enabled", False)) else "关闭"
    enabled = "开启" if bool(getattr(cfg.autostart, "enabled", False)) else "关闭"
    registered = "已注册" if state.registered else "未注册"
    return f"{enabled}（{registered}，{state.mechanism}）"


def _autostart_manager_or_exit() -> Any:
    from openbiliclaw.runtime import autostart

    manager = autostart.get_manager()
    if manager is not None:
        return manager
    reason = autostart.status().reason
    _print_status_panel("error", "当前环境不支持开机自启动", _autostart_reason_message(reason))
    raise typer.Exit(code=1)


def _save_autostart_authoritative(cfg: Any) -> None:
    from openbiliclaw.config import save_config

    save_config(cfg, autostart_authoritative=True)


def _restore_autostart_enabled(cfg: Any, enabled: bool) -> None:
    cfg.autostart.enabled = enabled
    with suppress(Exception):
        _save_autostart_authoritative(cfg)


def _register_autostart_best_effort(manager: Any, cfg: Any, should_register: bool) -> None:
    if should_register:
        with suppress(Exception):
            manager.register(cfg)


@autostart_app.command("status")
def autostart_status() -> None:
    """显示开机自启动状态。"""
    from openbiliclaw.config import load_config

    _print_autostart_status(load_config())


@autostart_app.command("enable")
def autostart_enable() -> None:
    """开启登录系统后自动拉起后端。"""
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.autostart.guards import (
        active_env_managed_inputs,
        autostart_shadowed,
    )

    cfg = load_config()
    manager = _autostart_manager_or_exit()
    managed = active_env_managed_inputs(cfg)
    if managed:
        _print_status_panel(
            "error",
            "检测到环境变量配置，无法开启自启动",
            f"{_autostart_reason_message('env_managed')}\n命中：{', '.join(managed)}",
        )
        raise typer.Exit(code=1)

    previous_enabled = bool(cfg.autostart.enabled)
    cfg.autostart.enabled = True
    try:
        _save_autostart_authoritative(cfg)
    except Exception as exc:
        cfg.autostart.enabled = previous_enabled
        _print_status_panel("error", "配置保存失败", str(exc))
        raise typer.Exit(code=1) from exc

    if autostart_shadowed(True):
        _restore_autostart_enabled(cfg, previous_enabled)
        _print_status_panel("error", "配置被覆盖", _autostart_reason_message("shadowed"))
        raise typer.Exit(code=1)

    try:
        manager.register(cfg)
    except Exception as exc:
        _restore_autostart_enabled(cfg, previous_enabled)
        _print_status_panel(
            "error",
            "自启动注册失败",
            f"{_autostart_reason_message('registration_failed')}\n{exc}",
        )
        raise typer.Exit(code=1) from exc

    _print_status_panel(
        "success",
        "已开启开机自启动",
        "下次登录系统时会拉起 OpenBiliClaw 后端；当前进程不会被启停。",
    )
    _print_autostart_status(cfg)


@autostart_app.command("disable")
def autostart_disable() -> None:
    """关闭登录系统后自动拉起后端。"""
    from openbiliclaw.config import load_config
    from openbiliclaw.runtime.autostart.guards import autostart_shadowed

    cfg = load_config()
    manager = _autostart_manager_or_exit()
    previous_enabled = bool(cfg.autostart.enabled)
    was_registered = bool(manager.is_registered())

    try:
        manager.unregister()
    except Exception as exc:
        _print_status_panel(
            "error",
            "自启动注销失败",
            f"{_autostart_reason_message('unregister_failed')}\n{exc}",
        )
        raise typer.Exit(code=1) from exc

    cfg.autostart.enabled = False
    try:
        _save_autostart_authoritative(cfg)
    except Exception as exc:
        cfg.autostart.enabled = previous_enabled
        _register_autostart_best_effort(manager, cfg, was_registered)
        _restore_autostart_enabled(cfg, previous_enabled)
        _print_status_panel("error", "配置保存失败", str(exc))
        raise typer.Exit(code=1) from exc

    if autostart_shadowed(False):
        cfg.autostart.enabled = previous_enabled
        _register_autostart_best_effort(manager, cfg, was_registered)
        _restore_autostart_enabled(cfg, previous_enabled)
        _print_status_panel("error", "配置被覆盖", _autostart_reason_message("shadowed"))
        raise typer.Exit(code=1)

    _print_status_panel(
        "success",
        "已关闭开机自启动",
        "系统登录项已移除；当前后端进程不会被停止。",
    )
    _print_autostart_status(cfg)
