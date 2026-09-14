"""P4 第七刀守门：soul 画像 / 推荐 / 对话命令组的抽离约束。

防止四类回流：
1. 子模块顶层 import ``openbiliclaw.cli`` 本体（循环依赖）；
2. 被 patch 符号的动态取语义漂移——12 个共享符号必须经 ``_cli.X`` 取用，
   若有人改回模块级 from-import，``monkeypatch.setattr(cli_module, ...)``
   补丁会静默失效（本组命令会退回真实构建器 / 真实数据库）；
3. 顶层命令形状漂移（register 挂载丢失 / 顶层命令数变化）；
4. 渲染 helper 回归——``_print_recommendation_card`` 已迁 ``cli._render``。
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import typer
from typer.testing import CliRunner

import openbiliclaw.cli._cmd_soul as soul_module
import openbiliclaw.cli._render as render_module
from openbiliclaw import cli as cli_module
from openbiliclaw.cli import app

SRC = Path(inspect.getfile(soul_module)).parent

# 抽离后仍须在 cli 命名空间可见的 8 个命令名（`profile` 除外：
# 与 cli 内既有形参 `profile` 同名，re-export 会触发 ruff F811）。
REEXPORTED = (
    "chat",
    "delight",
    "feedback",
    "import_youtube",
    "probe",
    "profile_consolidate",
    "rebuild_profile",
    "recommend",
)

# 外部定义（主文件 / runtime.init_flow / _cmd_init）但测试 patch 到 cli
# 命名空间的符号；本组调用一律必须走 `_cli.` 动态取。
PATCHED_EXTERNAL = (
    "_build_dialogue",
    "_build_memory_manager",
    "_build_recommendation_engine",
    "_build_registry",
    "_build_soul_engine",
    "_build_usage_recorder",
    "_get_runtime_database",
    "_notify_running_server_init_completed",
    "_prepare_init_runtime",
    "_print_init_cost_summary",
    "_require_runtime_config",
    "_run_with_progress",
)

SOUL_COMMANDS = (
    "rebuild-profile",
    "profile-consolidate",
    "import-youtube",
    "recommend",
    "feedback",
    "profile",
    "chat",
    "delight",
    "probe",
)


def test_cmd_soul_top_level_does_not_import_cli() -> None:
    """顶层禁 import cli 本体；兄弟子模块（cli._render）互引允许。"""
    tree = ast.parse((SRC / "_cmd_soul.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            bad = [n for n in names if n in ("openbiliclaw.cli", "openbiliclaw.cli.__init__")]
            assert not bad, f"_cmd_soul 顶层 import 了 cli 本体（循环依赖回流）：{bad}"


def test_soul_registered_and_command_count_unchanged() -> None:
    """9 个 soul 命令经 register(app) 挂载，且顶层命令总数仍为 42（零丢失）。"""
    out = CliRunner().invoke(app, ["--help"]).output
    cmds = sorted(re.findall(r"│ (\S+)", out))
    for name in SOUL_COMMANDS:
        assert name in cmds, f"顶层 --help 缺 soul 命令 {name}（register 挂载回流？）"
    assert len(cmds) == 42, f"顶层命令数漂移：{len(cmds)} != 42 → {cmds}"


def test_reexported_symbols_visible_on_cli_module() -> None:
    """测试 patch/直引的符号必须在 cli 命名空间可见，且与本体同一对象。"""
    for sym in REEXPORTED:
        assert hasattr(cli_module, sym), f"cli 命名空间丢失 re-export：{sym}"
        assert getattr(cli_module, sym) is getattr(soul_module, sym), (
            f"{sym} 与本体模块不是同一对象（re-export 被复制/覆盖？）"
        )


def test_profile_command_reachable_via_body_module_only() -> None:
    """`profile` 不 re-export（F811 规避），但命令本体与 app 挂载必须仍在。"""
    assert callable(soul_module.profile)
    out = CliRunner().invoke(app, ["profile", "--help"]).output
    assert "查看用户画像" in out


def test_recommendation_card_render_helper_moved_to_render_module() -> None:
    """渲染 helper `_print_recommendation_card` 本体在 cli._render，且被 re-export。"""
    assert callable(render_module._print_recommendation_card)
    assert cli_module._print_recommendation_card is render_module._print_recommendation_card  # type: ignore[attr-defined]


def test_dynamic_cli_attribute_access_preserved() -> None:
    """被 patch 符号的调用必须是 ``_cli.X`` 形式（模块级 from-import 会破坏补丁）。"""
    source = inspect.getsource(soul_module)
    for sym in PATCHED_EXTERNAL:
        bare = [
            line
            for line in source.splitlines()
            # 词边界 + 排除后缀子串与 docstring 反引号提及
            if re.search(rf"(?<![\w.`]){sym}\b(?!_)", line)
            and f"_cli.{sym}" not in line
            and not line.lstrip().startswith(("def ", "#"))
        ]
        assert not bare, f"{sym} 存在未走 _cli. 动态取的调用行：{bare}"


def test_recommend_resolves_engines_through_cli_namespace(monkeypatch) -> None:
    """行为锁：``recommend`` 必须经 cli 命名空间解析引擎构建器。

    patch ``cli_module._build_soul_engine`` /
    ``_build_recommendation_engine`` 后命令应使用假引擎并走到「暂无可推荐
    内容」分支；若某次重构改回模块级 from-import，patch 失效、命令会尝试
    真实构建（无配置环境 → 非零退出 / 真实网络调用），该用例即失败。
    """

    class _FakeSoul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class _FakeRecommendationEngine:
        async def generate_recommendations(self, **_: object) -> list[object]:
            return []

        def mark_presented(self, ids: list[int]) -> None:  # pragma: no cover
            raise AssertionError("无推荐时不应标记已展示")

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: _FakeSoul(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_recommendation_engine",
        lambda: _FakeRecommendationEngine(),
        raising=False,
    )

    result = CliRunner().invoke(app, ["recommend"])
    assert result.exit_code == 0, result.output
    assert "暂无可推荐内容" in result.stdout


def test_chat_resolves_dialogue_through_cli_namespace(monkeypatch) -> None:
    """行为锁：``chat`` 必须经 cli 命名空间解析 ``_build_dialogue``。"""

    class _FakeSoul:
        @staticmethod
        async def get_profile() -> object:
            return object()

    class _FakeDialogue:
        async def respond(self, user_message: str) -> str:
            return f"echo:{user_message}"

    monkeypatch.setattr(cli_module, "_require_runtime_config", lambda: None)
    monkeypatch.setattr(cli_module, "_build_soul_engine", lambda: _FakeSoul(), raising=False)
    monkeypatch.setattr(
        cli_module,
        "_build_dialogue",
        lambda soul_engine: _FakeDialogue(),
        raising=False,
    )

    result = CliRunner().invoke(app, ["chat"], input="你好\nexit\n")
    assert result.exit_code == 0, result.output
    assert "echo:你好" in result.stdout


def test_soul_module_register_is_idempotent_on_fresh_app() -> None:
    """register() 可重复挂到独立 app（不依赖主 app 状态）。"""
    fresh = typer.Typer()
    soul_module.register(fresh)
    names = sorted(c.name or c.callback.__name__ for c in fresh.registered_commands)
    assert names == sorted(SOUL_COMMANDS)
