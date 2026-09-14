"""P4 第九刀守门：``cli/_cmd_service.py`` 抽取后的结构、注册形状与 monkeypatch 语义。

抽取动机：上帝文件 ``cli/__init__.py`` 的「服务与运维命令」族
（start / serve-api / config-show / set-password / db-repair / health-check /
setup-embedding，以及 auth / login / browser 三个子命令组）整体迁出。

本测试锁住五件事：
1. 新模块顶层**不** import ``openbiliclaw.cli`` 本体（防循环依赖）；
2. ``register(app, auth_app, login_app, browser_app)`` 挂载 13 条命令
   （主 app 7 + auth 2 + login 1 + browser 3），命令名与形状不变；
3. cli 侧 re-export 的 3 个符号与本体模块**同对象**
   （测试 patch 补丁点 + ``_cmd_fetch`` 经 cli 命名空间调用）；
4. 跨模块调用、且被 tests/cli patch 到 cli 命名空间的符号，一律经
   ``_cli.X`` **动态取属性**——顶层 from-import 会绑定旧值并使补丁失效；
5. 两条行为锁（真跑一遍，断言补丁被命中）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from openbiliclaw import cli as cli_module
from openbiliclaw.cli import _cmd_service as service_module

SRC = Path(service_module.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)

REEXPORTED = (
    "_bump_auth_epoch",  # A′ 类：块内定义 + 测试 patch 补丁点
    "_normalize_strategy_names",  # _cmd_fetch 经 cli 命名空间调用
    "_rebase_auth_fingerprint",  # set-password 经 _cli.X 调用
)

# 跨模块调用 / 被 tests/cli patch 到 cli 命名空间 → 必须 _cli.X 动态取
DYNAMIC_NAMES = (
    "_build_auth_manager",
    "_build_browser",
    "_build_registry",
    "_bump_auth_epoch",
    "_ensure_runtime_database_healthy",
    "_format_autostart_config_status",
    "_format_pause_on_disconnect_status",
    "_interactive_embedding_setup",
    "_is_interactive_terminal",
    "_maybe_create_runtime_database_backup",
    "_preflight_loopback_ollama",
    "_print_auth_status",
    "_print_browser_status",
    "_print_config_guidance",
    "_rebase_auth_fingerprint",
    "_run_api_server",
    "_run_db_repair",
    "_self_heal_autostart_registration",
    "_warn_if_pause_on_disconnect_requires_presence",
    "console",
)


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _names(app: typer.Typer) -> list[str]:
    return sorted((c.name or c.callback.__name__).replace("_", "-") for c in app.registered_commands)


def test_module_does_not_import_cli_at_top_level() -> None:
    """新模块顶层禁 import 本包（`from openbiliclaw import cli` / `openbiliclaw.cli`）。"""
    for node in TREE.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("openbiliclaw.cli"), alias.name
                assert alias.name != "openbiliclaw", alias.name
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "openbiliclaw.cli", node.module
            if node.module == "openbiliclaw":
                assert all(alias.name != "cli" for alias in node.names), "顶层不得 import cli 本体"


def test_register_mounts_expected_commands() -> None:
    """register 把 13 条命令按原形状挂到主 app 与三个子 Typer。"""
    app = typer.Typer()
    auth_app = typer.Typer()
    login_app = typer.Typer()
    browser_app = typer.Typer()

    service_module.register(app, auth_app, login_app, browser_app)

    assert _names(app) == [
        "config-show",
        "db-repair",
        "health-check",
        "serve-api",
        "set-password",
        "setup-embedding",
        "start",
    ]
    assert _names(auth_app) == ["login", "status"]
    assert _names(login_app) == ["codex"]
    assert _names(browser_app) == ["content", "open", "status"]


def test_reexported_symbols_are_same_objects() -> None:
    """cli 命名空间的 re-export 必须与本体模块同对象（补丁点依赖这条）。"""
    for name in REEXPORTED:
        assert hasattr(service_module, name), f"{name} 未在 _cmd_service 定义"
        assert getattr(cli_module, name, None) is getattr(service_module, name), name


def test_patch_sensitive_calls_go_through_cli_indirection() -> None:
    """被 patch 的跨模块符号：源码中不得出现裸调用（只能 `_cli.X`）。"""
    for name in DYNAMIC_NAMES:
        bare = re.findall(rf"(?<![\w.])(?<!def ){re.escape(name)}\(", SRC)
        assert bare == [], f"{name} 存在裸调用，会破坏 monkeypatch 语义: {len(bare)} 处"
        assert f"_cli.{name}" in SRC or name not in SRC, name


def test_db_repair_command_hits_cli_patch(monkeypatch: pytest.MonkeyPatch, runner: CliRunner) -> None:
    """行为锁：patch cli 命名空间的 `_run_db_repair` 必须被 db-repair 命中。"""

    class _Result:
        status = "healthy"
        message = "数据库完整，无需修复。"
        repaired_db = None
        db_backup = None
        wal_backup = None

    monkeypatch.setattr(cli_module, "_run_db_repair", lambda: _Result(), raising=False)
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)

    result = runner.invoke(cli_module.app, ["db-repair"])

    assert result.exit_code == 0
    assert "数据库完整，无需修复。" in result.stdout


def test_set_password_logout_all_hits_cli_patch(
    monkeypatch: pytest.MonkeyPatch, runner: CliRunner, tmp_path: Path
) -> None:
    """行为锁：patch cli 命名空间的 `_bump_auth_epoch` 必须被 set-password 命中。

    `_bump_auth_epoch` 属 A′ 类（定义在新模块内）——若块内直调本地名字，
    `monkeypatch.setattr(cli_module, ...)` 会静默失效并真写运行库。
    """
    from openbiliclaw.config import Config, save_config

    root = tmp_path / "runtime"
    monkeypatch.setenv("OPENBILICLAW_PROJECT_ROOT", str(root))
    save_config(Config(), root / "config.toml")
    monkeypatch.setattr(cli_module, "_initialize_logging", lambda log_level_override=None: None)
    calls: list[object] = []
    monkeypatch.setattr(
        cli_module,
        "_bump_auth_epoch",
        lambda cfg: (calls.append(cfg), False)[1],
        raising=False,
    )

    result = runner.invoke(cli_module.app, ["set-password", "--logout-all"])

    assert calls, "补丁未命中：块内直调本地名字，绕过 cli 命名空间"
    assert result.exit_code == 1
    assert "未能撤销" in result.stdout
