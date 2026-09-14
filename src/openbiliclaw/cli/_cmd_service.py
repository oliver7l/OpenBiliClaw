"""CLI 服务与运维命令组（start / serve-api / config-show / auth / login / browser
/ set-password / db-repair / health-check / setup-embedding）。

从上帝文件 ``cli/__init__.py`` 抽出（P4 第九刀，2026-09-14）。
本模块顶层**不得** import ``openbiliclaw.cli``（避免循环依赖）；
13 个命令经 ``register(app, auth_app, login_app, browser_app)`` 挂回主 app
与三个子 Typer（``auth`` / ``login`` / ``browser``，命令名与形状不变）。

patch 语义保留：被测试 patch 到 cli 命名空间的共享符号（``_run_db_repair`` /
``_bump_auth_epoch`` / ``_is_interactive_terminal`` / ``_build_registry`` /
``_build_auth_manager`` / ``_build_browser`` / ``_run_api_server`` /
``_ensure_runtime_database_healthy`` / ``_maybe_create_runtime_database_backup``
/ ``console`` 等）一律在**函数体内**经 ``from openbiliclaw import cli as _cli``
动态取属性——顶层 from-import 会绑定旧值并破坏
``monkeypatch.setattr(cli_module, ...)``。

``_bump_auth_epoch`` / ``_rebase_auth_fingerprint`` 属 A′ 类：定义在本模块，
但 ``set_password`` 调用时仍走 ``_cli.X``，以便测试替换 cli 命名空间
（cli 侧对这两个符号 re-export）。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import suppress
from pathlib import Path
from typing import Any

import typer
from rich.panel import Panel

from openbiliclaw.cli._render import (
    _print_key_value_table,
    _print_page_title,
    _print_status_panel,
)

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


def setup_embedding() -> None:
    """配置本地 Ollama 作为 embedding 兜底服务（可选）.

    init 时已经问过；如果当时没启用、之后想加上，跑这条命令再走一次引导。
    """
    from openbiliclaw import cli as _cli

    _print_page_title("配置本地 embedding", "Ollama + bge-m3")
    from openbiliclaw.config import load_config_with_diagnostics

    config, _ = load_config_with_diagnostics()
    _cli._interactive_embedding_setup(config.llm.default_provider)


def start(
    host: str = typer.Option("", "--host", help="API 监听地址（默认读 config.toml [api].host）"),
    port: int = typer.Option(0, "--port", min=0, max=65535, help="API 监听端口（默认读 config.toml [api].port）"),
) -> None:
    """启动 OpenBiliClaw Agent."""
    from openbiliclaw import cli as _cli
    from openbiliclaw.config import load_config

    cfg = load_config()
    effective_host = host if host else cfg.api.host
    effective_port = port if port else cfg.api.port
    _print_page_title("启动 OpenBiliClaw", "本地 API 服务")
    _cli._ensure_runtime_database_healthy()
    _print_status_panel(
        "info",
        "API 服务",
        f"正在启动本地后端，当前监听 {effective_host}:{effective_port}。",
    )
    _cli._warn_if_pause_on_disconnect_requires_presence()
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
    _cli._maybe_create_runtime_database_backup()
    _cli._preflight_loopback_ollama(cfg)
    _cli._self_heal_autostart_registration(cfg)
    _cli._run_api_server(host=effective_host, port=effective_port)


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


def set_password(
    disable: bool = typer.Option(False, "--disable", help="关闭密码门禁"),
    logout_all: bool = typer.Option(False, "--logout-all", help="使所有设备的登录态立即失效（不改密码/密钥）"),
    rotate_secret: bool = typer.Option(False, "--rotate-secret", help="轮换会话签名密钥（最强撤销，需重启后端生效）"),
) -> None:
    """设置 / 修改局域网访问密码（或关闭门禁 / 登出所有设备）。"""
    import secrets as _secrets

    from openbiliclaw import cli as _cli
    from openbiliclaw.auth_core import hash_password
    from openbiliclaw.config import load_config, save_config

    cfg = load_config()

    if logout_all:
        # DB-only revocation — always effective, independent of env/config source.
        ok = _cli._bump_auth_epoch(cfg)
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
        revoked = _cli._bump_auth_epoch(cfg)
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
        _cli._rebase_auth_fingerprint(cfg)
        _print_status_panel(
            "success",
            "已轮换会话密钥",
            "所有设备需重新登录；重启后端使新密钥完全生效。",
        )
        return

    if not _cli._is_interactive_terminal():
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
    revoked = _cli._bump_auth_epoch(cfg)
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


def serve_api(
    host: str = typer.Option("0.0.0.0", "--host", help="API 监听地址"),
    port: int = typer.Option(8420, "--port", min=1, max=65535, help="API 监听端口"),
) -> None:
    """启动容器友好的 API 服务入口."""
    from openbiliclaw import cli as _cli

    _print_page_title("启动 OpenBiliClaw", "容器 API 服务")
    _print_status_panel(
        "info",
        "API 服务",
        f"正在启动容器友好的后端入口，当前监听 {host}:{port}。",
    )
    _cli._warn_if_pause_on_disconnect_requires_presence()
    _cli._run_api_server(host=host, port=port)


def db_repair() -> None:
    """检查并修复本地 SQLite 数据库。"""
    from openbiliclaw import cli as _cli

    result = _cli._run_db_repair()
    _cli.console.print(result.message)
    if getattr(result, "db_backup", None) is not None:
        _cli.console.print(f"备份文件: {result.db_backup}")
    if getattr(result, "wal_backup", None) is not None:
        _cli.console.print(f"WAL 备份: {result.wal_backup}")
    if getattr(result, "repaired_db", None) is not None:
        _cli.console.print(f"恢复副本: {result.repaired_db}")
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


def config_show() -> None:
    """显示当前配置."""
    from obc_llm import RegistryBuildError

    from openbiliclaw import cli as _cli
    from openbiliclaw.config import load_config_with_diagnostics
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
            _cli._format_pause_on_disconnect_status(
                enabled=cfg.scheduler.pause_on_extension_disconnect,
                grace_seconds=cfg.scheduler.extension_disconnect_grace_seconds,
            ),
        ),
        ("开机自启动", _cli._format_autostart_config_status(cfg)),
        ("数据目录", str(cfg.data_path)),
    ]
    if diagnostics.config_path:
        rows.append(("配置文件", str(diagnostics.config_path)))
    _print_key_value_table("配置项", rows)

    try:
        registry = _cli._build_registry()
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
    _cli._print_config_guidance(hints)


def auth_login(
    cookie: str | None = typer.Option(None, "--cookie", help="直接传入完整 Cookie"),
) -> None:
    """交互式设置并验证 B 站 Cookie."""
    from openbiliclaw import cli as _cli

    manager = _cli._build_auth_manager()
    cookie_value = cookie or typer.prompt("请输入 B 站 Cookie", prompt_suffix=": ")
    status = asyncio.run(manager.validate_cookie(cookie_value))
    if not status.authenticated:
        _cli.console.print("[bold red]认证失败[/bold red]")
        _cli._print_auth_status(status)
        raise typer.Exit(code=1)

    manager.set_cookie(cookie_value)
    _cli.console.print("[bold green]登录成功[/bold green]")
    _cli._print_auth_status(status)


def auth_status() -> None:
    """查看当前 B 站 Cookie 认证状态."""
    from openbiliclaw import cli as _cli

    manager = _cli._build_auth_manager()
    status = asyncio.run(manager.get_status())
    _cli._print_auth_status(status)


def login_codex(
    import_credentials: bool = _CODEX_LOGIN_IMPORT_OPTION,
    source: Path | None = _CODEX_LOGIN_SOURCE_OPTION,
    status: bool = _CODEX_LOGIN_STATUS_OPTION,
    logout: bool = _CODEX_LOGIN_LOGOUT_OPTION,
) -> None:
    """导入或管理 Codex CLI 的 ChatGPT OAuth 凭据."""
    from datetime import datetime

    from obc_llm.codex_auth import (
        CodexAuthError,
        CodexCredentials,
        delete_codex_credentials,
        import_codex_credentials,
        load_codex_credentials,
        run_codex_cli_login,
    )

    from openbiliclaw import cli as _cli

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
                _cli.console.print("[dim]未找到可导入的 Codex 凭据，启动 `codex login`...[/dim]")
                run_codex_cli_login()
                credentials = import_codex_credentials()
    except CodexAuthError as exc:
        _print_status_panel("error", "Codex OAuth 登录失败", str(exc))
        raise typer.Exit(code=1) from exc

    _print_status_panel("success", "Codex OAuth", "登录凭据已导入。")
    _print_codex_credentials(credentials)


def health_check() -> None:
    """检查当前已注册 LLM provider 的可用性."""
    from obc_llm import RegistryBuildError

    from openbiliclaw import cli as _cli

    try:
        registry = _cli._build_registry()
    except RegistryBuildError as exc:
        _print_status_panel("error", "Provider 健康检查失败", str(exc))
        raise typer.Exit(code=1) from exc

    results = asyncio.run(registry.health_check_all())
    _print_page_title("Provider 健康检查", "已注册 LLM Provider 状态")
    for name, result in results.items():
        status = "可用" if result.available else "不可用"
        default_label = " (default)" if result.is_default else ""
        _cli.console.print(f"  {name}{default_label}: {status}")
        if result.error:
            _cli.console.print(f"    原因: {result.error}")


def browser_status() -> None:
    """检查 agent-browser 是否可用."""
    from openbiliclaw import cli as _cli

    browser = _cli._build_browser()
    _cli._print_browser_status(browser)
    if browser.is_available:
        return
    _cli.console.print(f"  安装提示: {browser.get_install_hint()}")
    raise typer.Exit(code=1)


def browser_open(url: str) -> None:
    """通过 agent-browser 打开一个页面."""
    from openbiliclaw import cli as _cli
    from openbiliclaw.bilibili.browser import BrowserCommandError

    browser = _cli._build_browser()
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


def browser_content(url: str) -> None:
    """抓取当前页面可见文本."""
    from openbiliclaw import cli as _cli
    from openbiliclaw.bilibili.browser import BrowserCommandError

    browser = _cli._build_browser()
    if not browser.is_available:
        _print_status_panel("error", "agent-browser 未安装", browser.get_install_hint())
        raise typer.Exit(code=1)

    try:
        content = asyncio.run(browser.get_page_content(url))
    except BrowserCommandError as exc:
        _print_status_panel("error", "浏览器操作失败", str(exc))
        raise typer.Exit(code=1) from exc

    _print_page_title("页面内容")
    _cli.console.print(Panel(content, border_style="cyan"))


def register(
    app: typer.Typer,
    auth_app: typer.Typer,
    login_app: typer.Typer,
    browser_app: typer.Typer,
) -> None:
    """挂载服务与运维命令到主 app 与三个子 Typer（命令名与形状不变）。"""
    app.command("setup-embedding")(setup_embedding)
    app.command("start")(start)
    app.command("set-password")(set_password)
    app.command("serve-api")(serve_api)
    app.command("db-repair")(db_repair)
    app.command("config-show")(config_show)
    app.command("health-check")(health_check)
    auth_app.command("login")(auth_login)
    auth_app.command("status")(auth_status)
    login_app.command("codex")(login_codex)
    browser_app.command("status")(browser_status)
    browser_app.command("open")(browser_open)
    browser_app.command("content")(browser_content)
