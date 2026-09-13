"""P4 第四刀守门：autostart 命令组与渲染 helper 的抽离约束。

防止两类回流：
1. 子模块顶层 import ``openbiliclaw.cli``（循环依赖）；
2. 渲染 helper 的 console 补丁点漂移——测试必须 patch **本体模块**
   （``openbiliclaw.cli._render``），helper 不得回读调用方模块属性。
"""

from __future__ import annotations

import ast
import inspect
import io
from pathlib import Path

from rich.console import Console

import openbiliclaw.cli._render as render_module
from openbiliclaw.cli import app
from openbiliclaw.cli._cmd_autostart import autostart_app
from openbiliclaw.cli._render import _print_status_panel

SRC = Path(inspect.getfile(render_module)).parent


def _module_defers_cli_import(module_path: Path) -> None:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            # 禁止 import cli 包本体（__init__.py，循环依赖）；
            # 兄弟子模块（cli._render / cli._cmd_*）互引是允许的。
            bad = [n for n in names if n == "openbiliclaw.cli" or n == "openbiliclaw.cli.__init__"]
            assert not bad, f"{module_path.name} 顶层 import 了 cli 本体（循环依赖回流）：{bad}"


def test_cmd_autostart_top_level_does_not_import_cli() -> None:
    _module_defers_cli_import(SRC / "_cmd_autostart.py")


def test_render_module_top_level_does_not_import_cli() -> None:
    _module_defers_cli_import(SRC / "_render.py")


def test_autostart_group_mounted() -> None:
    commands = {c.name for c in autostart_app.registered_commands}
    assert commands == {"status", "enable", "disable"}


def test_autostart_group_reachable_from_root_app() -> None:
    subcommands = {c.name for c in app.registered_groups if c.name == "autostart"}
    assert subcommands == {"autostart"}


def test_render_helpers_write_to_render_module_console(monkeypatch) -> None:
    """锁定回归修复：helper 读 _render.console（本体），补丁点可生效。

    回归背景：helper 抽离前定义在 cli/__init__.py，测试 patch
    cli.console 即可；抽离后若 helper 误从调用方模块取 console，
    本体补丁会失效（tests/cli/test_cli.py 降级面板测试曾因此挂）。
    """
    output = io.StringIO()
    monkeypatch.setattr(
        render_module, "console", Console(file=output, force_terminal=False, width=120)
    )
    _print_status_panel("warning", "降级模式 / Degraded mode", "body-marker")
    rendered = output.getvalue()
    assert "降级模式" in rendered
    assert "body-marker" in rendered
