"""架构契约回归：禁止新增「路径漂移」。

背景（详见 ``docs/architecture-map.md`` §7/§9）：六模块盘点发现 **34 处**路径类隐患，
它们是同一族缺陷，且**不会报错**——只在「CWD 不是仓库根」时静默读写到别处：

- ``Path(__file__).resolve().parents[N]`` —— 自数层级取项目根，挪一次目录就错位
- ``Path("data/xxx.db")`` —— CWD 相对路径，换个启动目录就读写别的库

本文件用 **ratchet（棘轮）** 策略：既有的违规做基线放行，**新增即失败**。
修掉一处就从基线里删一行——基线只应该变短。这样：

- 不需要一次性大改（34 处分布在 8 个包，逐个改风险高）
- 后续任何人（包括 AI）新增同类写法会立刻红灯，而不是等半年后「数据怎么不对」

判据刻意基于**文件 + 出现次数**而不是行号：行号会随无关编辑漂移，次数才是语义。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"

# ── 两个模式 ──────────────────────────────────────────────────────
# 项目根自算（走 config._project_root() 才是唯一受支持的锚点）
_PATTERN_PARENTS = re.compile(r"Path\(__file__\)\.resolve\(\)\.parents\[\d+\]")
# CWD 相对的数据路径
_PATTERN_CWD_DATA = re.compile(r"""Path\(\s*["']data/""")

# ── 设计上就必须这么写的地方（不是违规）──────────────────────────
# interview/_paths.py 是本项目「项目根锚点」的唯一实现，它的职责就是数出根目录；
# 其余模块必须 import 它，而不是各写一份。
_ALLOWED_BY_DESIGN = {
    "src/openbiliclaw/interview/_paths.py",
}

# ── 基线：2026-09-15 盘点时的现状（只允许减少）───────────────────
_BASELINE_PARENTS: dict[str, int] = {
    "src/openbiliclaw/api/conversation_archive_routes.py": 1,
    "src/openbiliclaw/api/oss_research_routes.py": 1,
    "src/openbiliclaw/diary/sources/_browser.py": 1,
    "src/openbiliclaw/integrations/openclaw/cli.py": 1,
    "src/openbiliclaw/interview/study/cli.py": 1,
    "src/openbiliclaw/interview/study/seed_iq_questions.py": 1,
    "src/openbiliclaw/rag/retriever.py": 1,
}

_BASELINE_CWD_DATA: dict[str, int] = {
    "src/openbiliclaw/diary/store.py": 2,
    "src/openbiliclaw/douban/import_data.py": 2,
    "src/openbiliclaw/douban/insight.py": 1,
    "src/openbiliclaw/eval/persona_pool.py": 1,
    "src/openbiliclaw/knowledge_forge/auto_fixer.py": 1,
    "src/openbiliclaw/knowledge_forge/batch_processor.py": 1,
    "src/openbiliclaw/knowledge_forge/contradiction_detector.py": 1,
    "src/openbiliclaw/knowledge_forge/dead_link_checker.py": 1,
    "src/openbiliclaw/knowledge_forge/entity_description_updater.py": 1,
    "src/openbiliclaw/knowledge_forge/entity_extractor.py": 1,
    "src/openbiliclaw/knowledge_forge/entity_relation_builder.py": 1,
    "src/openbiliclaw/knowledge_forge/gap_analyst.py": 1,
    "src/openbiliclaw/knowledge_forge/gap_filler.py": 1,
    "src/openbiliclaw/knowledge_forge/low_quality_detector.py": 1,
    "src/openbiliclaw/knowledge_forge/quality_auditor.py": 1,
    "src/openbiliclaw/knowledge_forge/schedule.py": 1,
    "src/openbiliclaw/knowledge_forge/summary_engine.py": 1,
    "src/openbiliclaw/knowledge_forge/wiki_builder.py": 1,
    "src/openbiliclaw/runtime/rate_limit_guard.py": 1,
    "src/openbiliclaw/self_evolution/loop_engine.py": 1,
    "src/openbiliclaw/travel/routes.py": 4,
}

# 基线里已经修完的文件可以留空，但**不许**出现不存在的文件（防止基线腐烂成噪音）。
_BASELINE_FILES = set(_BASELINE_PARENTS) | set(_BASELINE_CWD_DATA) | _ALLOWED_BY_DESIGN


def _scan(pattern: re.Pattern[str]) -> dict[str, int]:
    """按文件统计命中次数（路径相对仓库根）。"""
    counts: dict[str, int] = {}
    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        hits = sum(
            1
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
            if pattern.search(line)
        )
        if hits:
            counts[str(path.relative_to(_REPO_ROOT))] = hits
    return counts


def _new_offenders(
    actual: dict[str, int],
    baseline: dict[str, int],
) -> tuple[list[str], list[str]]:
    """返回 (新增的文件, 既有文件里新增的次数)。"""
    added = sorted(set(actual) - set(baseline) - _ALLOWED_BY_DESIGN)
    grew = sorted(
        f"{name}（基线 {baseline[name]} → 实际 {count}）"
        for name, count in actual.items()
        if name in baseline and count > baseline[name]
    )
    return added, grew


def test_no_new_project_root_guessing() -> None:
    """禁止新增 ``Path(__file__).parents[N]`` 式项目根自算。

    项目根只应来自 ``config._project_root()`` 或 ``interview/_paths.PROJECT_ROOT``。
    自算的写法在「文件挪层级」时会静默指向错误目录，典型症状是数据写到别的库、
    而运行时不报任何错。
    """
    actual = _scan(_PATTERN_PARENTS)
    added, grew = _new_offenders(actual, _BASELINE_PARENTS)
    assert added == [] and grew == [], (
        "新增了项目根自算写法（请改用 config._project_root()）：\n"
        f"  新文件：{added}\n"
        f"  既有文件新增：{grew}\n"
        "若确需豁免，请加入 _ALLOWED_BY_DESIGN 并写明理由。"
    )


def test_no_new_cwd_relative_data_paths() -> None:
    """禁止新增 ``Path("data/...")`` 式 CWD 相对数据路径。

    典型症状：从别的目录启动时读写到另一份数据库，接口 200 但数据是空的
    （旅游模块的 4 处就是这样：行程接口 404 而预算接口正常）。
    """
    actual = _scan(_PATTERN_CWD_DATA)
    added, grew = _new_offenders(actual, _BASELINE_CWD_DATA)
    assert added == [] and grew == [], (
        "新增了 CWD 相对数据路径（请改用 config._project_root() / load_config()）：\n"
        f"  新文件：{added}\n"
        f"  既有文件新增：{grew}"
    )


def test_baseline_has_no_stale_entries() -> None:
    """基线与豁免清单不得指向已不存在的文件，否则会掩盖真实新增。"""
    stale = sorted(name for name in _BASELINE_FILES if not (_REPO_ROOT / name).exists())
    assert stale == [], f"基线条目已失效，请清理：{stale}"


@pytest.mark.parametrize(
    "sample",
    [
        'ROOT = Path(__file__).resolve().parents[3]',
        'root = Path(__file__).resolve().parents[1]',
    ],
)
def test_parents_pattern_matches(sample: str) -> None:
    """自检：正则必须真的命中违规写法（防止写错导致本文件变成永远绿灯的装饰）。"""
    assert _PATTERN_PARENTS.search(sample)


@pytest.mark.parametrize(
    "sample",
    [
        'db = Path("data/travel.db")',
        "db = Path('data/weekend.db')",
        'db = Path( "data/x.db" )',
    ],
)
def test_cwd_data_pattern_matches(sample: str) -> None:
    """自检：CWD 相对路径的各种写法都要能命中。"""
    assert _PATTERN_CWD_DATA.search(sample)


@pytest.mark.parametrize(
    "sample",
    [
        'db = Path(root) / "data" / "travel.db"',
        'db = Path("/abs/path/data/travel.db")',
        'path = "data/travel.db"',
    ],
)
def test_patterns_do_not_over_match(sample: str) -> None:
    """自检：合规写法不得误报（否则闸门会变成噪音，最后被人为关掉）。"""
    assert not _PATTERN_CWD_DATA.search(sample)
    assert not _PATTERN_PARENTS.search(sample)
