"""健康时间线分页回归测试（2026-09-15）。

缺陷（详见 ``docs/module-review-2026-09-15.md`` §6 H1）：
``HealthStore.get_timeline`` 对 **8 个来源表各自** ``LIMIT ? OFFSET ?``，
合并排序后又 ``events[:limit]`` 二次截断。两个症状：

1. ``offset`` 被逐表应用 ⇒ 并非全局分页，第 2 页起与真实顺序错位；
2. 末尾截断丢事件 ⇒ 翻完所有页也凑不齐全部事件。

正确语义：``offset`` / ``limit`` 作用于**合并排序后的全局序列**，
翻页拼起来必须恰好等于全部事件、不重不漏。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openbiliclaw.health.models import AppointmentCreate, EncounterCreate, PatientCreate
from openbiliclaw.health.store import HealthStore


@pytest.fixture()
def store(tmp_path: Path) -> HealthStore:
    s = HealthStore(db_path=tmp_path / "health.db")
    s.initialize()
    return s


@pytest.fixture()
def patient_id(store: HealthStore) -> int:
    return store.create_patient(PatientCreate(full_name="测试患者")).id


def _seed_cross_source_events(store: HealthStore, patient_id: int) -> None:
    """3 条就诊 + 3 条预约，日期交错 → 全局顺序必须跨来源合并。

    全局降序应为：01-06(预约) 01-05(就诊) 01-04(预约) 01-03(就诊) 01-02(预约) 01-01(就诊)
    """
    for day in (1, 3, 5):
        store.create_encounter(
            EncounterCreate(
                patient_id=patient_id,
                encounter_date=f"2024-01-{day:02d}",
                hospital=f"就诊{day}日",
            )
        )
    for day in (2, 4, 6):
        store.create_appointment(
            AppointmentCreate(
                patient_id=patient_id,
                title=f"预约{day}日",
                scheduled_date=f"2024-01-{day:02d}",
            )
        )


def test_timeline_pages_are_globally_ordered_and_lossless(
    store: HealthStore, patient_id: int
) -> None:
    """limit=2 翻三页：全局有序、无重复、无遗漏。

    修复前的症状在**第 2 页**：逐表 ``OFFSET 2`` 会把就诊表跳到 01-01、
    预约表跳到 01-02，得到 [01-02, 01-01] —— 而正确答案应是 [01-04, 01-03]。
    """
    _seed_cross_source_events(store, patient_id)

    page1 = store.get_timeline(patient_id, limit=2, offset=0)
    page2 = store.get_timeline(patient_id, limit=2, offset=2)
    page3 = store.get_timeline(patient_id, limit=2, offset=4)
    page4 = store.get_timeline(patient_id, limit=2, offset=6)

    assert [(e.date, e.event_type) for e in page1] == [
        ("2024-01-06", "appointment"),
        ("2024-01-05", "encounter"),
    ], "第 1 页应为全局最新的两条（跨来源）"
    assert [(e.date, e.event_type) for e in page2] == [
        ("2024-01-04", "appointment"),
        ("2024-01-03", "encounter"),
    ], "第 2 页错位 —— offset 被逐表应用了"
    assert [(e.date, e.event_type) for e in page3] == [
        ("2024-01-02", "appointment"),
        ("2024-01-01", "encounter"),
    ]

    # 翻完所有页 = 全部事件，不重不漏
    all_dates = [e.date for e in [*page1, *page2, *page3]]
    assert len(all_dates) == 6 and len(set(all_dates)) == 6, "翻页存在重复或遗漏"
    assert page4 == [], "翻完全部事件后应返回空页"


def test_timeline_single_page_returns_all_events(
    store: HealthStore, patient_id: int
) -> None:
    """大 limit 一次取全：条数必须等于种子数（修复前会被 [:limit] 之外的逻辑丢行）。"""
    _seed_cross_source_events(store, patient_id)

    events = store.get_timeline(patient_id, limit=100)

    assert len(events) == 6
    dates = [e.date for e in events]
    assert dates == sorted(dates, reverse=True), "合并后必须全局降序"


def test_timeline_offset_beyond_end_returns_empty(
    store: HealthStore, patient_id: int
) -> None:
    _seed_cross_source_events(store, patient_id)

    assert store.get_timeline(patient_id, limit=10, offset=999) == []
