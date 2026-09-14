"""P4 第十一刀守门：``cli/_build.py`` 抽取后的结构、re-export 与 monkeypatch 语义。

抽取动机：上帝文件 ``cli/__init__.py`` 的「运行时构建族」（LLM registry /
各引擎构建器 / 记忆管理器 / API 服务运行 / 数据库备份与健康检查，16 个
无 typer 命令的顶层函数）整体迁出。

本测试锁住五件事：
1. 新模块顶层**不** import ``openbiliclaw.cli`` 本体（防循环依赖）；
2. 该模块**不含 typer 命令**（无 ``register``），符号靠 cli 顶层 re-export；
3. cli 侧 re-export 的 16 个符号与本体模块**同对象**；
4. 被 tests/cli patch 到 cli 命名空间的符号一律经 ``_cli.X`` **动态取属性**——
   顶层 from-import 会绑定旧值并使补丁失效；
5. 三条行为锁（真跑一遍，断言补丁被命中）。
"""

from __future__ import annotations

import ast
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from rich.console import Console

from openbiliclaw import cli as cli_module
from openbiliclaw.cli import _build as build_module
from openbiliclaw.cli import _render as render_module

SRC = Path(build_module.__file__).read_text(encoding="utf-8")
TREE = ast.parse(SRC)

REEXPORTED = (
    "_build_auth_manager",
    "_build_bilibili_client",
    "_build_browser",
    "_build_dialogue",
    "_build_discovery_engine",
    "_build_memory_manager",
    "_build_recommendation_engine",
    "_build_registry",
    "_build_soul_engine",
    "_build_usage_recorder",
    "_ensure_runtime_database_healthy",
    "_maybe_create_runtime_database_backup",
    "_run_api_server",
    "_run_db_repair",
    "_runtime_backup_dir",
    "_runtime_database_path",
)

# 被 tests/cli patch 到 **cli 命名空间** → 必须 _cli.X 动态取
DYNAMIC_NAMES = (
    "_build_bilibili_client",
    "_build_memory_manager",
    "_build_registry",
    "_build_usage_recorder",
    "_RUNTIME_COMPONENTS",
    "_get_runtime_database",
    "console",
)


def test_module_does_not_import_cli_at_top_level() -> None:
    """新模块顶层禁 import 本包（`from openbiliclaw import cli` / `openbiliclaw.cli`）。"""
    for node in TREE.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("openbiliclaw.cli"), alias.name
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            assert not mod.startswith("openbiliclaw.cli") or mod.startswith("openbiliclaw.cli._render"), mod


def test_module_has_no_register_and_cli_still_has_app() -> None:
    """该族不含 typer 命令 → 无 register；主 app / main / callback 仍在 cli 本体。"""
    names = {n.name for n in TREE.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "register" not in names
    assert hasattr(cli_module, "app")
    assert hasattr(cli_module, "main")


def test_reexports_are_same_objects() -> None:
    """cli 命名空间 re-export 的 16 个符号与 _build 本体同对象。"""
    for name in REEXPORTED:
        assert getattr(cli_module, name) is getattr(build_module, name), name


def test_dynamic_names_accessed_via_cli_namespace() -> None:
    """被 patch 到 cli 命名空间的 7 个符号，在源码中一律以 ``_cli.X`` 出现。

    用 AST 扫 ``ast.Name`` 节点（docstring / 属性名不算裸引用）。
    """
    bare: list[tuple[str, int]] = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.Name) and node.id in DYNAMIC_NAMES:
            bare.append((node.id, node.lineno))
    assert not bare, f"以下符号被裸引用，须改为 _cli.X：{sorted(set(bare))}"


def test_build_memory_manager_honors_cli_namespace_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """行为锁 A：``_build_memory_manager`` 须走 ``_cli._RUNTIME_COMPONENTS`` +
    ``_cli._get_runtime_database``——补丁失效会走真库 / 真缓存并断言失败。"""
    sentinel_db = object()
    created: list[dict[str, object]] = []

    class FakeUsageRecorder:
        def __init__(self, *, sink: object) -> None:
            created.append({"sink": sink})

    monkeypatch.setattr("obc_llm.usage_recorder.UsageRecorder", FakeUsageRecorder)
    monkeypatch.setattr(cli_module, "_get_runtime_database", lambda: sentinel_db, raising=False)
    # 两个命名空间的缓存 dict 都清空（本体在 runtime.init_flow，cli 是 import 引用）
    import openbiliclaw.runtime.init_flow as init_flow_module

    monkeypatch.setattr(init_flow_module, "_RUNTIME_COMPONENTS", {}, raising=False)
    monkeypatch.setattr(cli_module, "_RUNTIME_COMPONENTS", {}, raising=False)

    build_module._build_usage_recorder()

    assert created and created[0]["sink"] is sentinel_db


def test_build_soul_engine_honors_cli_namespace_patch(monkeypatch: pytest.MonkeyPatch) -> None:
    """行为锁 B：``_build_soul_engine`` 须走 ``_cli._build_memory_manager`` /
    ``_cli._build_registry`` / ``_cli._build_usage_recorder``。"""

    class FakeSoulEngine:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    fake_memory = object()
    fake_registry = object()
    fake_recorder = object()

    monkeypatch.setattr("obc_soul.engine.SoulEngine", FakeSoulEngine)
    monkeypatch.setattr(
        "openbiliclaw.config.load_config",
        lambda: SimpleNamespace(
            soul=SimpleNamespace(preference=SimpleNamespace(satisfaction_filter_enabled=False)),
            scheduler=SimpleNamespace(
                speculation_interval_minutes=0,
                speculation_ttl_days=0,
                speculation_cooldown_days=0,
                speculation_confirmation_threshold=0,
                speculation_max_active=0,
                speculation_max_primary_interests=0,
                speculation_max_secondary_interests=0,
                avoidance_speculation_interval_minutes=0,
                avoidance_speculation_ttl_days=0,
                avoidance_speculation_cooldown_days=0,
                avoidance_speculation_confirmation_threshold=0,
                avoidance_speculation_max_active=0,
                speculator_idle_interval_minutes=0,
                profile_consolidation_enabled=False,
                profile_consolidation_interval_hours=0,
                profile_consolidation_like_target_upper=0,
                profile_consolidation_like_target_soft=0,
                profile_consolidation_archive_enabled=False,
            ),
            llm=SimpleNamespace(concurrency=1),
        ),
    )
    monkeypatch.setattr(cli_module, "_build_memory_manager", lambda: fake_memory, raising=False)
    monkeypatch.setattr(cli_module, "_build_registry", lambda: fake_registry, raising=False)
    monkeypatch.setattr(cli_module, "_build_usage_recorder", lambda: fake_recorder, raising=False)

    engine = build_module._build_soul_engine()

    assert isinstance(engine, FakeSoulEngine)
    assert engine.kwargs["memory"] is fake_memory
    assert engine.kwargs["llm"] is fake_registry
    assert engine.kwargs["usage_recorder"] is fake_recorder


def test_ensure_runtime_database_healthy_routes_console_via_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """行为锁 C：损坏库报告须经 ``_cli.console``（cli 命名空间补丁）与
    ``_print_status_panel``（_render 直取）输出，并抛 ``typer.Exit``。"""
    db_path = tmp_path / "openbiliclaw.db"
    db_path.write_bytes(b"not a sqlite file")

    fake_cli_console = Console(file=StringIO(), force_terminal=False)
    fake_render_console = Console(file=StringIO(), force_terminal=False)

    cfg = SimpleNamespace(data_path=tmp_path)
    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: cfg)
    monkeypatch.setattr(cli_module, "console", fake_cli_console, raising=False)
    monkeypatch.setattr(render_module, "console", fake_render_console, raising=False)
    monkeypatch.setattr(
        "openbiliclaw.storage.maintenance.check_database_integrity",
        lambda _p: SimpleNamespace(healthy=False, error="boom: corrupted"),
    )

    with pytest.raises(typer.Exit):
        build_module._ensure_runtime_database_healthy()

    assert "boom: corrupted" in fake_cli_console.file.getvalue()  # type: ignore[attr-defined]
    assert "数据库损坏" in fake_render_console.file.getvalue()  # type: ignore[attr-defined]
