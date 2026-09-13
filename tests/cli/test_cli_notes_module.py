"""cli/_cmd_notes.py 守门测试（P4 第二刀，2026-09-13）。

背景：note 命令组从上帝文件 ``cli/__init__.py`` 抽出。旧实现读取
``_APP_CONTEXT["data_dir"] / ["config"]``，但该 dict 从未有这两键的
写入点 → 全部 note 命令静默 no-op；且 ``Database(...)`` 构造后未调
``initialize()``（即便路径存在也会崩）。本文件锁死三类回流：

1. note 子命令对账（9 个命令一个不少）；
2. 静默 no-op 根因（``_APP_CONTEXT``）禁止回流到命令组模块；
3. 命令组模块顶层禁止 import ``openbiliclaw.cli``（循环依赖）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from openbiliclaw.cli import _cmd_notes as notes_module
from openbiliclaw.cli import app

NOTE_COMMANDS = {
    "list",
    "get",
    "create",
    "delete",
    "search",
    "stats",
    "import-read-archive",
    "video",
    "tasks",
}


def _registered_note_commands() -> set[str]:
    """从 note_app 拿真实注册的命令名集合。"""
    from openbiliclaw.cli._cmd_notes import note_app

    names: set[str] = set()
    for cmd in note_app.registered_commands:
        name = cmd.name or (cmd.callback and cmd.callback.__name__)
        if name:
            names.add(name)
    return names


def test_note_group_registers_all_nine_commands() -> None:
    assert _registered_note_commands() == NOTE_COMMANDS


def test_note_group_is_attached_to_root_app() -> None:
    runner = CliRunner()
    result = runner.invoke(app, ["note", "--help"])
    assert result.exit_code == 0
    for name in NOTE_COMMANDS:
        assert name in result.output


def test_notes_module_does_not_reference_app_context() -> None:
    """静默 no-op 根因禁止回流：命令组不得再引用 cli._APP_CONTEXT。

    用 AST 扫描标识符引用（而非全文文本），避免模块 docstring 中
    对该缺陷的描述文字造成误报。
    """
    import ast

    tree = ast.parse(Path(notes_module.__file__).read_text(encoding="utf-8"))
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "_APP_CONTEXT"
    ]
    assert offenders == [], f"_APP_CONTEXT 回流至行 {offenders}"


def test_notes_module_has_no_top_level_cli_import() -> None:
    """顶层 import openbiliclaw.cli 会与 cli/__init__ 形成循环依赖。

    只扫描模块顶层（缩进为 0）的 import 行；函数体内的延迟 import
    需按需另行评估，不在本守门范围内。
    """
    module_file = Path(notes_module.__file__)
    for lineno, line in enumerate(
        module_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(line) - len(line.lstrip()) > 0:
            continue  # 有缩进 = 函数/类体内，放行
        assert not stripped.startswith(("from openbiliclaw.cli", "import openbiliclaw.cli")), (
            f"第 {lineno} 行顶层 import cli 会形成循环依赖"
        )


def test_get_note_service_uses_runtime_database_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_get_note_service 必须经 load_config().data_path 取库且库已初始化。"""

    class _FakeConfig:
        data_path = tmp_path

    monkeypatch.setattr("openbiliclaw.config.load_config", lambda: _FakeConfig())

    svc = notes_module._get_note_service()
    assert svc is not None
    # 存储层可真正执行（Database 已 initialize，不再抛 RuntimeError）
    stats = svc.get_stats()
    assert stats.total == 0
