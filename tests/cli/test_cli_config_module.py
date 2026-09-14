"""P4 第八刀守门：``cli/_cmd_config.py`` 抽取后的结构与 monkeypatch 语义。

抽取动机：上帝文件 ``cli/__init__.py`` 的「运行时配置写入 + 交互引导」族
（provider 落盘 / embedding / module overrides / 交互向导）整体迁出。

本测试锁住四件事：
1. 新模块顶层**不** import ``openbiliclaw.cli`` 本体（防循环依赖）；
2. 该族**不含 typer 命令**（纯 helper + 菜单常量），因此不需要 ``register()``；
3. cli 侧 re-export 的 20 个符号与本体模块**同对象**（tests/cli 直引与
   ``monkeypatch.setattr(cli_module, ...)`` 补丁点都依赖这条）；
4. 跨模块调用、且被 tests/cli patch 到 cli 命名空间的 6 个符号，一律经
   ``_cli.X`` **动态取属性**——顶层 from-import 会绑定旧值并使补丁失效。
   第 4 条另配两条行为锁（真跑一遍，断言补丁被命中）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import typer

from openbiliclaw import cli as cli_module
from openbiliclaw.cli import _cmd_config as config_module

SRC = Path(config_module.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)

REEXPORTED = (
    # 菜单常量
    "_LLM_MENU",
    "_OPENAI_COMPAT_PRESETS",
    "_PROVIDER_DEFAULTS",
    "_PROVIDER_HINTS",
    "_PROVIDER_MODEL_HINT",
    "_SUPPORTED_PROVIDERS",
    # helper
    "_interactive_auth_setup",
    "_interactive_embedding_setup",
    "_interactive_module_overrides",
    "_interactive_runtime_config_setup",
    "_ollama_has_model",
    "_ollama_install_if_missing",
    "_ollama_pull_model",
    "_print_provider_table",
    "_prompt_openai_compat",
    "_prompt_provider_triplet",
    "_resolve_menu_choice",
    "_save_embedding_config",
    "_save_module_overrides",
    "_save_runtime_provider_config",
)

# 跨模块调用 + 被 tests/cli patch 到 cli 命名空间 → 必须 _cli.X 动态取
DYNAMIC_NAMES = (
    "_load_runtime_config_error",
    "_print_auth_status",
    "_print_runtime_config_error",
    "_save_embedding_config",
    "_save_module_overrides",
    "_save_runtime_provider_config",
)


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


def test_module_registers_no_typer_commands() -> None:
    """该族是纯 helper + 常量，不应挂任何 typer 命令。"""
    for node in ast.walk(TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                target = dec.func if isinstance(dec, ast.Call) else dec
                attr = getattr(target, "attr", "")
                assert attr not in {"command", "callback"}, f"{node.name} 不应挂 typer 命令"
                name = getattr(target, "id", "")
                assert name not in {"command", "callback"}, f"{node.name} 不应挂 typer 命令"


def test_reexported_symbols_are_same_objects() -> None:
    """cli 命名空间的 re-export 必须与本体模块同对象（补丁点 + tests/cli 直引）。"""
    for name in REEXPORTED:
        assert hasattr(config_module, name), f"{name} 未在 _cmd_config 定义"
        assert getattr(cli_module, name, None) is getattr(config_module, name), name


def test_patch_sensitive_calls_go_through_cli_indirection() -> None:
    """被 patch 的跨模块符号：源码中不得出现裸调用（只能 `_cli.X(`）。"""
    for name in DYNAMIC_NAMES:
        bare = re.findall(rf"(?<![\w.])(?<!def ){re.escape(name)}\(", SRC)
        assert bare == [], f"{name} 存在裸调用，会破坏 monkeypatch 语义: {len(bare)} 处"
        assert f"_cli.{name}(" in SRC or name not in SRC, name


def test_embedding_auto_path_hits_cli_patch(monkeypatch) -> None:
    """行为锁：patch cli 命名空间的 `_save_embedding_config` 必须被命中。

    `auto_if_ready` 路径（本地 Ollama 已就绪 + 有 bge-m3）是 init 默认走的
    一条，最容易被顶层 from-import 静默破坏。
    """
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(cli_module, "_save_embedding_config", lambda **kw: calls.append(kw), raising=False)
    monkeypatch.setattr(config_module, "_ollama_is_running", lambda *a, **k: True)
    monkeypatch.setattr(config_module, "_ollama_has_model", lambda *a, **k: True)

    config_module._interactive_embedding_setup("openai", auto_if_ready=True)

    assert calls == [{"provider": "ollama", "model": "bge-m3"}]


def test_module_overrides_hits_cli_patch(monkeypatch) -> None:
    """行为锁：patch cli 命名空间的 `_save_module_overrides` 必须被命中。"""
    captured: dict[str, dict[str, str]] = {}
    monkeypatch.setattr(cli_module, "_save_module_overrides", lambda o: captured.update(o), raising=False)
    monkeypatch.setattr(typer, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(typer, "prompt", lambda *a, **k: "gemini")

    config_module._interactive_module_overrides("openai")

    assert set(captured) == {"soul", "discovery", "recommendation", "evaluation"}
    assert all(entry["provider"] == "gemini" for entry in captured.values())
