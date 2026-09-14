"""周末怎么玩模块测试。

覆盖：数据模型、存储层、引擎方案生成、周五触发判定、CLI 种子导入。
不依赖真实 diary/douban 库即可验证核心逻辑（diary/douban 用临时空库）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pytest

from openbiliclaw.weekend.engine import WeekendEngine
from openbiliclaw.weekend.models import PlanMode, WeekendSpot
from openbiliclaw.weekend.store import WeekendStore

_SEED = Path(__file__).resolve().parent.parent.parent / "data" / "weekend_seed_activities.json"


@pytest.fixture()
def tmp_weekend_db(tmp_path: Path) -> str:
    return str(tmp_path / "weekend.db")


@pytest.fixture()
def empty_diary_db(tmp_path: Path) -> str:
    """构造一个无情绪记录的 diary 库，供引擎 fallback 测试。"""
    p = tmp_path / "diary.db"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE diary_entries (id INTEGER PRIMARY KEY, entry_date TEXT, mood TEXT, mood_score REAL)")
    conn.commit()
    conn.close()
    return str(p)


@pytest.fixture()
def empty_douban_db(tmp_path: Path) -> str:
    p = tmp_path / "douban.db"
    conn = sqlite3.connect(p)
    conn.execute(
        "CREATE TABLE douban_items (id INTEGER PRIMARY KEY, category TEXT, status TEXT, name TEXT, rating TEXT)"
    )
    conn.commit()
    conn.close()
    return str(p)


def _load_spots() -> list[WeekendSpot]:
    data = json.loads(_SEED.read_text(encoding="utf-8"))
    return [WeekendSpot(**a) for a in data["activities"]]


def test_seed_file_has_six_activities() -> None:
    spots = _load_spots()
    assert len(spots) == 6
    assert all(s.district for s in spots)


def test_import_spots_and_counts(tmp_weekend_db: str) -> None:
    store = WeekendStore(tmp_weekend_db)
    n = store.import_spots(_load_spots())
    assert n == 6
    assert store.spot_count() == 6
    # 过期项默认被过滤（循环派对 valid_until=2026-09-12，今天若晚于则排除）
    # 至少香菜节/沙井（无 valid_until）应出现
    visible = store.list_spots()
    titles = {s.title for s in visible}
    assert "沙井古墟 + 老街一日游" in titles


def test_import_spots_idempotent_clear(tmp_weekend_db: str) -> None:
    store = WeekendStore(tmp_weekend_db)
    store.import_spots(_load_spots())
    store.import_spots(_load_spots(), clear=True)
    assert store.spot_count() == 6  # 清后重导，仍是 6（非 12）


def test_list_spots_filter_by_suitable_for(tmp_weekend_db: str) -> None:
    store = WeekendStore(tmp_weekend_db)
    store.import_spots(_load_spots())
    # include_expired=True：种子里唯一带精确「带娃」标签的「香菜节」有
    # valid_until=2026-09-13，默认过滤会随日期推移把它剔除 → 断言变脆
    # （2026-09-14 起全量必红）。本测试只验证 LIKE 精确匹配人群标签的能力，
    # 与有效期无关，故显式放开过期项。
    fam = store.list_spots(suitable_for="带娃", include_expired=True)
    assert fam
    assert all("带娃" in s.suitable_for for s in fam)


def test_generate_mixed_three_options(tmp_weekend_db: str, empty_diary_db: str, empty_douban_db: str) -> None:
    store = WeekendStore(tmp_weekend_db)
    store.import_spots(_load_spots())
    engine = WeekendEngine(store, diary_db=empty_diary_db, douban_db=empty_douban_db)
    plan = engine.generate(mode=PlanMode.AUTO.value)
    # auto 恒定 mixed，凑满 3 个方案
    assert plan.mode == "mixed"
    assert len(plan.options) == 3
    # 至少一个出门、至少一个宅家
    modes = {o.mode for o in plan.options}
    assert "outdoor" in modes
    assert "indoor" in modes
    # 每个方案都有「为什么适合你」
    assert all(o.why for o in plan.options)
    # 已落库
    assert store.get_plan(plan.week_of) is not None


def test_generate_explicit_outdoor_only(tmp_weekend_db: str, empty_diary_db: str, empty_douban_db: str) -> None:
    store = WeekendStore(tmp_weekend_db)
    store.import_spots(_load_spots())
    engine = WeekendEngine(store, diary_db=empty_diary_db, douban_db=empty_douban_db)
    plan = engine.generate(mode=PlanMode.OUTDOOR.value)
    assert plan.mode == "outdoor"
    assert all(o.mode == "outdoor" for o in plan.options)


def test_decide_and_checkin(tmp_weekend_db: str, empty_diary_db: str, empty_douban_db: str) -> None:
    store = WeekendStore(tmp_weekend_db)
    store.import_spots(_load_spots())
    engine = WeekendEngine(store, diary_db=empty_diary_db, douban_db=empty_douban_db)
    plan = engine.generate(mode=PlanMode.AUTO.value)
    decided = store.decide_plan(plan.week_of, "confirmed")
    assert decided is not None and decided.status == "confirmed"
    # 打卡
    from openbiliclaw.weekend.models import CheckIn

    store.add_checkin(CheckIn(plan_id=plan.id, option_index=0, rating=5, note="娃很开心"))
    rows = store.list_checkins(plan_id=plan.id)
    assert len(rows) == 1
    assert rows[0]["rating"] == 5


def test_friday_window_judgement() -> None:
    """用真实日期验证周五窗口。

    2026-09-11 是周五；2026-09-12 是周六。
    """
    from openbiliclaw.weekend.store import WeekendStore

    engine = WeekendEngine(WeekendStore(":memory:"))
    assert engine.is_friday_push_window(datetime(2026, 9, 11, 20, 0)) is True
    assert engine.is_friday_push_window(datetime(2026, 9, 12, 20, 0)) is False
    assert engine.is_friday_push_window(datetime(2026, 9, 11, 10, 0)) is False
    assert engine.is_friday_push_window(datetime(2026, 9, 11, 23, 30)) is False  # 超过 +3h


def test_saturday_of_week() -> None:
    assert WeekendEngine.saturday_of_week(date(2026, 9, 9)) == "2026-09-12"  # 周三→本周六
    assert WeekendEngine.saturday_of_week(date(2026, 9, 12)) == "2026-09-12"  # 周六→当天
    assert WeekendEngine.saturday_of_week(date(2026, 9, 13)) == "2026-09-12"  # 周日→上周六


def test_generate_for_friday_gates_on_window(tmp_weekend_db: str, empty_diary_db: str, empty_douban_db: str) -> None:
    store = WeekendStore(tmp_weekend_db)
    store.import_spots(_load_spots())
    engine = WeekendEngine(store, diary_db=empty_diary_db, douban_db=empty_douban_db)
    # 非周五 → 不生成
    assert engine.generate_for_friday(datetime(2026, 9, 12, 20, 0)) is None
    # 周五且本周未生成 → 生成，来源标记 friday_push
    plan = engine.generate_for_friday(datetime(2026, 9, 11, 20, 0))
    assert plan is not None
    assert plan.source == "friday_push"
    # 同周五再触发 → 已存在，返回 None（不重复）
    assert engine.generate_for_friday(datetime(2026, 9, 11, 21, 0)) is None
