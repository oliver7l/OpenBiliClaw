"""P4 第五刀守门：fetch-*/discover-* 命令组的抽离约束。

防止两类回流：
1. 子模块顶层 import ``openbiliclaw.cli`` 本体（循环依赖）；
2. 被 patch 符号的动态取语义漂移——``register(app)`` 挂载的 13 个平铺
   命令经 ``_cli.X`` 动态取共享符号，若有人改回模块级 from-import，
   ``monkeypatch.setattr(cli_module, ...)`` 补丁会静默失效。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import ModuleType

import openbiliclaw.cli._cmd_fetch as fetch_module
from openbiliclaw import cli as cli_module
from openbiliclaw.cli import app

SRC = Path(inspect.getfile(fetch_module)).parent

EXPECTED_COMMANDS = {
    "fetch-douyin": "fetch_douyin",
    "search-douyin": "search_douyin",
    "fetch-xhs": "fetch_xhs",
    "fetch-youtube": "fetch_youtube",
    "fetch-zhihu": "fetch_zhihu",
    "discover-zhihu": "discover_zhihu",
    "discover-zhihu-hot": "discover_zhihu_hot",
    "discover-zhihu-feed": "discover_zhihu_feed",
    "discover-zhihu-creator": "discover_zhihu_creator",
    "discover-zhihu-related": "discover_zhihu_related",
    "fetch-x": "fetch_x",
    "discover-douyin": "discover_douyin",
    "discover": "discover",
}


def test_cmd_fetch_top_level_does_not_import_cli() -> None:
    tree = ast.parse((SRC / "_cmd_fetch.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            bad = [n for n in names if n in ("openbiliclaw.cli", "openbiliclaw.cli.__init__")]
            assert not bad, f"_cmd_fetch 顶层 import 了 cli 本体（循环依赖回流）：{bad}"


def test_all_fetch_commands_registered_on_root_app() -> None:
    from typer.testing import CliRunner

    out = CliRunner().invoke(app, ["--help"]).output
    for cmd_name in EXPECTED_COMMANDS:
        assert cmd_name in out, f"顶层 --help 缺命令 {cmd_name}（register 挂载回流？）"


def test_patched_symbols_reexported_on_cli_module() -> None:
    """测试经 cli_module patch/直引的符号必须在 cli 命名空间可见。"""
    for sym in (
        "_run_zhihu_discovery",
        "_run_douyin_discovery",
        "_run_xhs_discovery",
        "_normalize_douyin_discovery_sources",
    ):
        assert hasattr(cli_module, sym), f"cli 命名空间丢失 re-export：{sym}"


def test_dynamic_cli_attribute_access_preserved() -> None:
    """被 patch 符号的调用必须是 ``_cli.X`` 形式（模块级 from-import 会破坏补丁）。"""
    import re

    source = inspect.getsource(fetch_module)
    for sym in (
        "_run_zhihu_discovery",
        "_run_douyin_discovery",
        "_enqueue_dy_bootstrap_task",
        "_enqueue_zhihu_search_task",
        "_prepare_init_runtime",
    ):
        bare = [
            line
            for line in source.splitlines()
            # 词边界 + 排除后缀子串（_smoke 等）与 docstring 反引号提及
            if re.search(rf"(?<![\w.`]){sym}\b(?!_)", line)
            and f"_cli.{sym}" not in line
            and not line.lstrip().startswith(("def ", "#"))
        ]
        assert not bare, f"{sym} 存在未走 _cli. 动态取的调用行：{bare}"


def test_cmd_fetch_module_smoke(monkeypatch) -> None:
    """register 真实挂载：patch 掉 discovery runtime 后 discover 命令可跑通。"""
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_run_douyin_discovery", lambda **kw: captured.update(kw))
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["discover-douyin", "--limit", "3"])
    assert result.exit_code == 0, result.output
    assert captured.get("limit") == 3  # type: ignore[typeddict-item]
    assert isinstance(fetch_module, ModuleType)
