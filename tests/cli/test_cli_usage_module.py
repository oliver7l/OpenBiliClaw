"""cli/_cmd_usage.py 守门测试（P4 第三刀，2026-09-13）。

``cost`` / ``logs-prune`` 自上帝文件 ``cli/__init__.py`` 抽出。锁两类回流：

1. 命令注册对账（顶层 ``cost`` / ``logs-prune`` 一个不少）；
2. 顶层 ``import openbiliclaw.cli`` 禁止出现（循环依赖）；
3. cost 对被测试 patch 的共享符号（``_get_runtime_database`` 等）必须经
   ``from openbiliclaw import cli as _cli`` 函数内动态取 —— 顶层 from-import
   绑定会破坏 ``monkeypatch.setattr(cli_module, ...)``（项目已知坑）。
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from openbiliclaw.cli import _cmd_usage as usage_module
from openbiliclaw.cli import app


def _top_level_help_output() -> str:
    return CliRunner().invoke(app, ["--help"]).output


def test_cost_and_logs_prune_registered() -> None:
    out = _top_level_help_output()
    assert "cost" in out
    assert "logs-prune" in out


def test_usage_module_has_no_top_level_cli_import() -> None:
    module_file = Path(usage_module.__file__)
    for lineno, line in enumerate(
        module_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if len(line) - len(line.lstrip()) > 0:
            continue
        assert not stripped.startswith(("from openbiliclaw.cli", "import openbiliclaw.cli")), (
            f"第 {lineno} 行顶层 import cli 会形成循环依赖"
        )


def test_cost_reads_patched_symbols_via_cli_module() -> None:
    """被 patch 的符号必须经 `_cli.` 动态取，禁止顶层 from-import 绑定。"""
    source = Path(usage_module.__file__).read_text(encoding="utf-8")
    assert "from openbiliclaw import cli as _cli" in source
    assert "_cli._get_runtime_database()" in source
    assert "_cli._ensure_runtime_database_healthy()" in source
