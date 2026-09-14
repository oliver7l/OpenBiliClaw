"""P4 第十刀守门：``cli/_collect.py`` 抽取后的结构、re-export 与 monkeypatch 语义。

抽取动机：上帝文件 ``cli/__init__.py`` 的「知乎 / 抖音任务入队-收集 + 事件落库」
helper 族（11 个无 typer 命令的顶层函数）整体迁出。

本测试锁住五件事：
1. 新模块顶层**不** import ``openbiliclaw.cli`` 本体（防循环依赖）；
2. 该模块**不含 typer 命令**（无 ``register``），符号靠 cli 顶层 re-export；
3. cli 侧 re-export 的 11 个符号与本体模块**同对象**；
4. 被 tests/cli patch 到 cli 命名空间的符号一律经 ``_cli.X`` **动态取属性**——
   顶层 from-import 会绑定旧值并使补丁失效；
5. 三条行为锁（真跑一遍，断言补丁被命中）。
"""

from __future__ import annotations

import ast
import re
from io import StringIO
from pathlib import Path

import pytest
from rich.console import Console

from openbiliclaw import cli as cli_module
from openbiliclaw.cli import _collect as collect_module

SRC = Path(collect_module.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)

REEXPORTED = (
    "_collect_dy_search_results",
    "_collect_zhihu_discovery_results",
    "_collect_zhihu_search_results",
    "_enqueue_dy_search_task",
    "_enqueue_zhihu_discovery_candidates",
    "_enqueue_zhihu_discovery_task",
    "_enqueue_zhihu_search_task",
    "_event_memory_key",
    "_import_xhs_bootstrap_events",
    "_load_existing_event_keys",
    "_write_events_to_memory",
)

# 被 tests/cli patch 到 **cli 命名空间** → 必须 _cli.X 动态取
DYNAMIC_NAMES = (
    "_build_memory_manager",
    "_collect_xhs_bootstrap_events",
    "_enqueue_xhs_bootstrap_task",
    "_get_runtime_database",
    "console",
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


def test_module_has_no_typer_commands() -> None:
    """纯 helper 模块：不含 ``register``，也不含任何 app.command 装饰。"""
    assert not hasattr(collect_module, "register")
    assert "@app.command" not in SRC
    defined = {n.name for n in TREE.body if isinstance(n, ast.FunctionDef)}
    assert defined == set(REEXPORTED)


def test_reexported_symbols_are_same_objects() -> None:
    """cli 命名空间的 re-export 必须与本体模块同对象（补丁点 / 直引依赖这条）。"""
    for name in REEXPORTED:
        assert hasattr(collect_module, name), f"{name} 未在 _collect 定义"
        assert getattr(cli_module, name, None) is getattr(collect_module, name), name


def test_patch_sensitive_calls_go_through_cli_indirection() -> None:
    """被 patch 的跨模块符号：源码中不得出现裸调用（只能 `_cli.X`）。"""
    for name in DYNAMIC_NAMES:
        bare = re.findall(rf"(?<![\w.])(?<!def ){re.escape(name)}\(", SRC)
        assert bare == [], f"{name} 存在裸调用，会破坏 monkeypatch 语义: {len(bare)} 处"
        assert f"_cli.{name}" in SRC, name


def test_enqueue_dy_search_task_hits_cli_patches(monkeypatch: pytest.MonkeyPatch) -> None:
    """行为锁：``_get_runtime_database`` / ``console`` 的 cli 命名空间补丁必须命中。

    若块内直调本地名字，``monkeypatch.setattr(cli_module, ...)`` 会静默失效，
    函数将走真实运行库——这里把 db 取用改成抛错，只有补丁命中才会走到「未入队」分支。
    """
    output = StringIO()
    fake_console = Console(file=output, force_terminal=False, width=120)

    def _boom() -> object:
        raise RuntimeError("patched-db-unavailable")

    monkeypatch.setattr(cli_module, "_get_runtime_database", _boom, raising=False)
    monkeypatch.setattr(cli_module, "console", fake_console, raising=False)

    task_id = collect_module._enqueue_dy_search_task(("猫",), max_items_per_keyword=3)

    assert task_id is None
    assert "抖音搜索任务未入队" in output.getvalue()
    assert "patched-db-unavailable" in output.getvalue()


def test_write_events_to_memory_hits_cli_memory_manager_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行为锁：``_build_memory_manager`` 的 cli 命名空间补丁必须被落库 helper 命中。"""

    class _FakeMemory:
        def __init__(self) -> None:
            self.propagated: list[dict] = []

        def query_events(self, *, limit: int) -> list[dict]:
            assert limit >= 1
            return []

        async def propagate_event(self, event: dict) -> None:
            self.propagated.append(event)

    fake = _FakeMemory()
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: fake, raising=False)

    written, skipped = collect_module._write_events_to_memory(
        [{"title": "T", "metadata": {"content_id": "1"}}],
        source="zhihu",
    )

    assert (written, skipped) == (1, 0)
    assert len(fake.propagated) == 1
    assert fake.propagated[0]["metadata"]["source_platform"] == "zhihu"


def test_import_xhs_bootstrap_events_hits_cli_bootstrap_patches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """行为锁：``_import_xhs_bootstrap_events`` 走 cli 命名空间取入队 / 收集入口。"""
    monkeypatch.setattr(cli_module, "_enqueue_xhs_bootstrap_task", lambda: "xhs-task-1", raising=False)
    monkeypatch.setattr(
        cli_module,
        "_collect_xhs_bootstrap_events",
        lambda task_id, **_: ([{"title": "小红书收藏咖啡"}], {"saved": 1}, "ok"),
        raising=False,
    )

    events, counts = collect_module._import_xhs_bootstrap_events()

    assert events == [{"title": "小红书收藏咖啡"}]
    assert counts == {"saved": 1}
