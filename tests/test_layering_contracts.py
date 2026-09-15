"""分层依赖棘轮 —— 禁止把已经解掉的环重新接回去（K5 / K6b）。

2026-09-15 的模块盘点里有两处**同层/反向依赖**是靠「把共享类型下沉到中性层」
解掉的，但它们没有任何机制防止复发：

- **K5**：``storage.x_health`` 需要捕获 X 客户端异常，而异常定义在
  ``sources.x_client`` → 形成 storage → sources 的反向依赖。
  解法：异常下沉到 ``openbiliclaw.core.x_errors``，两侧各自 import core。
- **K6b**：sources 侧 17 个 adapter 引用 ``obc_discovery.engine`` 的
  ``DiscoveredContent`` / ``DiscoveryStrategy`` → sources 与 discovery 同层耦合。
  解法：合同类型下沉到 ``openbiliclaw.core.contracts``，engine 再导出。

为什么必须用测试守住：这两个环**不会报错**，只会让「谁可以先加载」变得脆弱
（循环 import 在某个加载顺序下才炸），以及让分层图失去意义。后人重构时很容易
顺手 ``from openbiliclaw.sources.x_client import XAuthError`` 又接回去。

判据基于 **AST 而不是正则**：要能区分三种完全不同的写法——

1. 模块级 ``import x`` —— 运行时真依赖，最严重
2. ``if TYPE_CHECKING:`` 里的 import —— 只用于注解，运行时不加载，放行
3. 函数体内的延迟 import —— 运行时才触发，且常用于**刻意**打破加载期耦合，放行

正则区分不了 2/3 与 1，所以这里解析语法树。
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src" / "openbiliclaw"

#: 允许出现「跨层 import」的位置（路径相对仓库根）→ 理由。
#: 只应该为空，或只包含**设计上就如此**的文件；不得用来掩盖新增违规。
_ALLOWED_BY_DESIGN: dict[str, str] = {}


class _ImportCollector(ast.NodeVisitor):
    """收集 import，并按「是否运行时执行」分成两类。"""

    def __init__(self) -> None:
        self.runtime: list[tuple[int, str]] = []
        self.deferred: list[tuple[int, str]] = []
        self._defer_depth = 0

    # 函数体 / TYPE_CHECKING 块内的 import 属于「延迟」，放行
    def _visit_scoped(self, node: ast.AST) -> None:
        self._defer_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._defer_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_scoped(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_scoped(node)

    def visit_If(self, node: ast.If) -> None:  # noqa: N802
        # ``if TYPE_CHECKING:`` / ``if False:`` 这类静态条件分支不产生运行时依赖
        test = node.test
        is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_type_checking:
            self._visit_scoped(node)
        else:
            self.generic_visit(node)

    def _record(self, lineno: int, module: str) -> None:
        target = self.runtime if self._defer_depth == 0 else self.deferred
        target.append((lineno, module))

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        for alias in node.names:
            self._record(node.lineno, alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if node.level and node.level > 0:
            # 相对 import：``from ..sources import x`` → 用 level 与当前包拼绝对名
            self._record(node.lineno, "." * node.level + (node.module or ""))
        elif node.module:
            self._record(node.lineno, node.module)


def _collect(path: Path) -> _ImportCollector:
    collector = _ImportCollector()
    collector.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    return collector


def _scan_source(source: str, prefixes: tuple[str, ...]) -> tuple[list[str], list[str]]:
    """对一段源码跑同一套判据，返回 ``(运行时命中, 延迟命中)``。

    自检用它来验证「阳性命中、阴性不误报」，避免自检写成一个假的通过。
    """
    collector = _ImportCollector()
    collector.visit(ast.parse(source))
    runtime = [m for _, m in collector.runtime if _matches(m, prefixes, relative_aware=True)]
    deferred = [m for _, m in collector.deferred if _matches(m, prefixes, relative_aware=True)]
    return runtime, deferred


def _matches(module: str, prefixes: tuple[str, ...], *, relative_aware: bool = False) -> bool:
    """``.`` 开头的相对名也参与匹配（``from ..sources import x`` 形如 ``..sources``）。"""
    if module in prefixes:
        return True
    if any(module.startswith(p + ".") for p in prefixes):
        return True
    if relative_aware and module.startswith("."):
        # 只看被 import 的尾段包名：``..sources`` / ``..sources.x_client``
        tail = module.lstrip(".")
        return any(tail == p.split(".")[-1] or tail.startswith(p.split(".")[-1] + ".") for p in prefixes)
    return False


def _offenders(
    subdir: str,
    prefixes: tuple[str, ...],
    *,
    runtime_only: bool = True,
    relative_aware: bool = True,
) -> list[str]:
    """返回形如 ``相对路径:行号 → 被 import 的模块`` 的违规清单。"""
    found: list[str] = []
    for path in sorted((_SRC / subdir).rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(_REPO_ROOT))
        if rel in _ALLOWED_BY_DESIGN:
            continue
        collector = _collect(path)
        entries = collector.runtime if runtime_only else collector.runtime + collector.deferred
        for lineno, module in entries:
            if _matches(module, prefixes, relative_aware=relative_aware):
                found.append(f"{rel}:{lineno} → {module}")
    return found


def test_storage_does_not_depend_on_sources() -> None:
    """K5：``storage`` 不得 import ``openbiliclaw.sources``（任何形态）。

    鉴别力：在 ``storage/x_health.py`` 里写回
    ``from openbiliclaw.sources.x_client import XAuthError``，本用例立即红。
    """
    offenders = _offenders("storage", ("openbiliclaw.sources",))
    assert offenders == [], (
        "storage 反向依赖了 sources（K5 已解：共享异常应放 openbiliclaw.core）：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_sources_do_not_pull_discovery_or_soul_at_import_time() -> None:
    """K6b：``sources`` 在**加载期**不得引入 ``obc_discovery`` / ``obc_soul``。

    注解用（``TYPE_CHECKING``）与函数内延迟 import 是刻意豁免的：前者运行时不加载，
    后者是既有的「打破加载期耦合」手法（见 ``sources/xhs_keyword_gen.py``）。
    """
    offenders = _offenders("sources", ("obc_discovery", "obc_soul", "openbiliclaw.discovery"))
    assert offenders == [], (
        "sources 在模块加载期就依赖了 discovery/soul（应改为 TYPE_CHECKING 注解或函数内延迟 import）：\n"
        + "\n".join(f"  {o}" for o in offenders)
    )


def test_sources_deferred_imports_are_actually_deferred() -> None:
    """自检：上面的「运行时期」判据不是恒空的（否则那条闸门是装饰）。

    xhs_keyword_gen 里的 ``obc_discovery`` 必须被识别为「延迟」，而不是「运行时」；
    同时必须**被扫描到过**——否则说明收集器根本没进那个文件。
    """
    path = _SRC / "sources" / "xhs_keyword_gen.py"
    collector = _collect(path)
    deferred = [m for _, m in collector.deferred]
    runtime = [m for _, m in collector.runtime]
    assert any("obc_discovery" in m for m in deferred), f"未识别到延迟 import：{collector.deferred}"
    assert not any("obc_discovery" in m for m in runtime), f"误判为运行时 import：{runtime}"


def test_core_does_not_import_the_project_at_runtime() -> None:
    """``core`` 是分层图的最底层：运行时只允许标准库与第三方包。

    鉴别力：在 ``core/contracts.py`` 顶层写 ``from openbiliclaw.config import Config``
    会把 core 变成上层的一部分，随后任何「上层 import core」都会绕成环。
    """
    offenders = _offenders(
        "core",
        ("openbiliclaw", "obc_discovery", "obc_soul", "obc_llm"),
        relative_aware=True,
    )
    assert offenders == [], "core 运行时依赖了项目内其它包（应下沉或改为 TYPE_CHECKING 注解）：\n" + "\n".join(
        f"  {o}" for o in offenders
    )


class TestDetectorSelfCheck:
    """判据自检：阳性必须命中、阴性不得误报（否则闸门会被当成噪音关掉）。"""

    _PREFIXES = ("openbiliclaw.sources", "obc_discovery", "obc_soul")

    @classmethod
    def _scan(cls, source: str) -> tuple[list[str], list[str]]:
        return _scan_source(source, cls._PREFIXES)

    def test_module_level_import_is_runtime(self) -> None:
        runtime, deferred = self._scan("from openbiliclaw.sources.x_client import XAuthError\n")
        assert runtime == ["openbiliclaw.sources.x_client"]
        assert deferred == []

    def test_type_checking_import_is_deferred(self) -> None:
        runtime, deferred = self._scan(
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from obc_soul.profile import SoulProfile\n"
        )
        assert runtime == []
        assert deferred == ["obc_soul.profile"]

    def test_function_level_import_is_deferred(self) -> None:
        runtime, deferred = self._scan(
            "def f():\n    from obc_discovery.strategies._utils import build_profile_summary\n"
        )
        assert runtime == []
        assert deferred == ["obc_discovery.strategies._utils"]

    def test_relative_import_is_recorded_with_dots(self) -> None:
        runtime, _ = self._scan("from ..sources import x_client\n")
        assert runtime == ["..sources"]

    def test_relative_prefix_matching_finds_the_package(self) -> None:
        assert _matches("..sources", ("openbiliclaw.sources",), relative_aware=True)
        assert _matches("..sources.x_client", ("openbiliclaw.sources",), relative_aware=True)
        assert not _matches("..storage.database", ("openbiliclaw.sources",), relative_aware=True)

    def test_import_inside_plain_conditional_is_still_runtime(self) -> None:
        """``if platform.system() == "Darwin":`` 里的 import 是运行时执行的。"""
        runtime, deferred = self._scan(
            "import sys\nif sys.platform == 'darwin':\n    import openbiliclaw.sources\n"
        )
        assert runtime == ["openbiliclaw.sources"]
        assert deferred == []

    def test_unrelated_imports_are_not_reported(self) -> None:
        """阴性：``from typing import ...`` / 第三方包不得被误报。"""
        runtime, deferred = self._scan(
            "import json\nfrom pathlib import Path\nfrom obc_llm.json_utils import parse_llm_json_tolerant\n"
        )
        assert runtime == [] and deferred == []

    def test_allowlist_entries_point_at_existing_files(self) -> None:
        stale = sorted(rel for rel in _ALLOWED_BY_DESIGN if not (_REPO_ROOT / rel).exists())
        assert stale == [], f"豁免清单指向已不存在的文件，请清理：{stale}"
