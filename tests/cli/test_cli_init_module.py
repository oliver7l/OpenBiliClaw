"""P4 第六刀守门：init 引导命令组的抽离约束。

防止三类回流：
1. 子模块顶层 import ``openbiliclaw.cli`` 本体（循环依赖）；
2. 被 patch 符号的动态取语义漂移——``init`` 及其问询 helper 经 ``_cli.X``
   动态取共享符号，若有人改回模块级 from-import，
   ``monkeypatch.setattr(cli_module, ...)`` 补丁（4 个本组符号 + 7 个外部
   符号）会静默失效；
3. 顶层命令形状漂移（register 挂载丢失 / 命令数变化）。
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import typer

import openbiliclaw.cli._cmd_init as init_module
from openbiliclaw import cli as cli_module
from openbiliclaw.cli import app

SRC = Path(inspect.getfile(init_module)).parent

# 抽离后仍须在 cli 命名空间可见的符号：4 个测试 patch 补丁点 +
# tests/cli 直引 + 主文件其它命令（rebuild-profile）仍在调用。
REEXPORTED = (
    "_ask_dy_inclusion",
    "_ask_network_binding",
    "_ask_xhs_inclusion",
    "_ask_yt_inclusion",
    "_maybe_setup_password_in_init",
    "_notify_running_server_init_completed",
    "_persist_api_host_choice",
    "_persist_init_source_enabled_flags",
    "_print_init_cost_summary",
    "init",
)

# 本组定义但测试 patch 到 cli 命名空间的补丁点（调用必须走 _cli.）。
PATCHED_INTERNAL = (
    "_ask_network_binding",
    "_maybe_setup_password_in_init",
    "_notify_running_server_init_completed",
    "_persist_api_host_choice",
)

# 外部定义（runtime.init_flow / 主文件）但测试 patch 到 cli 命名空间的
# 符号（本组调用同样必须走 _cli.）。
PATCHED_EXTERNAL = (
    "_build_bilibili_client",
    "_build_memory_manager",
    "_build_soul_engine",
    "_get_runtime_database",
    "_is_interactive_terminal",
    "_prepare_init_runtime",
    "_run_init_discovery_backfill_async",
)


def test_cmd_init_top_level_does_not_import_cli() -> None:
    """顶层禁 import cli 本体；兄弟子模块（cli._render）互引允许。"""
    tree = ast.parse((SRC / "_cmd_init.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            bad = [n for n in names if n in ("openbiliclaw.cli", "openbiliclaw.cli.__init__")]
            assert not bad, f"_cmd_init 顶层 import 了 cli 本体（循环依赖回流）：{bad}"


def test_init_registered_and_command_count_unchanged() -> None:
    """init 经 register(app) 挂载，且顶层命令总数仍为 42（零丢失）。"""
    from typer.testing import CliRunner

    out = CliRunner().invoke(app, ["--help"]).output
    cmds = sorted(re.findall(r"│ (\S+)", out))
    assert "init" in cmds, "顶层 --help 缺 init（register 挂载回流？）"
    assert len(cmds) == 42, f"顶层命令数漂移：{len(cmds)} != 42 → {cmds}"


def test_reexported_symbols_visible_on_cli_module() -> None:
    """测试 patch/直引的符号必须在 cli 命名空间可见，且与本体同一对象。"""
    for sym in REEXPORTED:
        assert hasattr(cli_module, sym), f"cli 命名空间丢失 re-export：{sym}"
        assert getattr(cli_module, sym) is getattr(init_module, sym), (
            f"{sym} 与本体模块不是同一对象（re-export 被复制/覆盖？）"
        )


def test_init_signature_reexport_keeps_yes_x_flag() -> None:
    """``cli_module.init`` 的签名可检查（--yes-x 等 typer 选项不丢）。"""
    sig = inspect.signature(cli_module.init)  # type: ignore[attr-defined]
    assert "skip_x_prompt" in sig.parameters
    decls = getattr(sig.parameters["skip_x_prompt"].default, "param_decls", ())
    assert "--yes-x" in decls


def test_dynamic_cli_attribute_access_preserved() -> None:
    """被 patch 符号的调用必须是 ``_cli.X`` 形式（模块级 from-import 会破坏补丁）。"""
    source = inspect.getsource(init_module)
    for sym in PATCHED_INTERNAL + PATCHED_EXTERNAL:
        bare = [
            line
            for line in source.splitlines()
            # 词边界 + 排除后缀子串与 docstring 反引号提及
            if re.search(rf"(?<![\w.`]){sym}\b(?!_)", line)
            and f"_cli.{sym}" not in line
            and not line.lstrip().startswith(("def ", "#"))
        ]
        assert not bare, f"{sym} 存在未走 _cli. 动态取的调用行：{bare}"


def test_terminal_check_resolved_through_cli_namespace(monkeypatch) -> None:
    """行为锁：``_ask_xhs_inclusion`` 必须经 cli 命名空间解析终端判定。

    patch ``cli_module._is_interactive_terminal`` 为 True 后，函数应越过
    非交互早退分支、走到 ``typer.confirm``；若某次重构改回模块级
    from-import，则 patch 失效、函数会在非交互环境提前返回 False，
    探针不会被触达——该用例即失败。
    """
    monkeypatch.delenv("OPENBILICLAW_NO_XHS", raising=False)
    monkeypatch.setattr(cli_module, "_is_interactive_terminal", lambda: True)

    probed: list[str] = []

    def _fake_confirm(*args: object, **kwargs: object) -> bool:
        probed.append("confirm")
        return False  # 立即否定，避免进入扩展 bootstrap 流程

    monkeypatch.setattr(typer, "confirm", _fake_confirm)

    assert cli_module._ask_xhs_inclusion() is False  # type: ignore[attr-defined]
    assert probed == ["confirm"], "终端判定未走 cli 命名空间（动态取语义漂移）"
