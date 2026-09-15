"""面试题学习追踪的回归测试（队列消费 + 每日进度记账）。

锁定两个 2026-09-15 修复的真实 bug。两组断言在修复前都会失败（已做
鉴别力校验：把 ``get_today_queue`` 退回「只读全表」的旧实现后，队列相关
3 条断言立刻变红；把 ``_bump_daily`` 调用摘掉后，进度相关 3 条立刻变红）。

Bug A：``iq_queue`` 从不消费
    旧 ``get_today_queue`` 直接从 ``iq_questions`` 全表按掌握度/难度挑
    ``daily_target`` 条，**完全不看队列** → 库里 48 条排队题目永远推不
    出去，学习追踪空转。

Bug B：``iq_daily`` 无写入方
    ``mark_read`` 只写 ``iq_records`` 并出队，从不累加 ``iq_daily`` →
    ``/progress`` 恒返回空数组，用户打卡后看不到今日进度。
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from openbiliclaw.interview.study.models import MasteryLevel, QuestionCategory, QuestionCreate
from openbiliclaw.interview.study.store import InterviewQuestionStore


def _store(tmp_path) -> InterviewQuestionStore:
    return InterviewQuestionStore(tmp_path / "iq.db")


def _add(store: InterviewQuestionStore, title: str, difficulty: int = 3):
    return store.add_question(
        QuestionCreate(title=title, answer="", category=QuestionCategory.RECOMMENDATION, difficulty=difficulty)
    )


# ── Bug A：今日待读必须消费队列 ──────────────────────────────

def test_today_queue_consumes_queued_questions(tmp_path):
    """队列里的题必须优先出现在今日待读里。

    修复前：只从全表按 (掌握度, 难度 DESC) 挑 5 条，队列完全不参与 ——
    这里 3 条难度 1 的队列题会被难度 5 的全表题挤掉。
    """
    store = _store(tmp_path)
    plan = store.create_plan("冲刺", daily_target=5)
    # 5 条「高难度」全表题：旧实现只认它们
    for i in range(5):
        _add(store, f"全表难题-{i}", difficulty=5)
    # 3 条低难度但已入队的题：新实现必须优先推它们
    queued = [_add(store, f"队列题-{i}", difficulty=1) for i in range(3)]
    for q in queued:
        store.add_to_queue(q.id)

    ids = [q.id for q in store.get_today_queue(plan)]
    for q in queued:
        assert q.id in ids, f"队列题 {q.title} 未被推给今日待读（旧 bug：队列不消费）"


def test_today_queue_fills_from_catalog_when_queue_short(tmp_path):
    """队列不足 daily_target 时从全表补齐，且不重复。"""
    store = _store(tmp_path)
    plan = store.create_plan("冲刺", daily_target=4)
    q1 = _add(store, "队列题-1")
    store.add_to_queue(q1.id)
    for i in range(6):
        _add(store, f"全表题-{i}")

    picked = store.get_today_queue(plan)
    assert len(picked) == 4, "应补齐到 daily_target 条"
    assert q1.id in [q.id for q in picked]
    assert len({q.id for q in picked}) == 4, "队列题与全表题不得重复"


def test_today_queue_skips_future_planned(tmp_path):
    """planned_date 未到期的排队题不应提前消耗。"""
    store = _store(tmp_path)
    plan = store.create_plan("冲刺", daily_target=5)
    soon = _add(store, "已排期-今天")
    later = _add(store, "已排期-下周")
    store.add_to_queue(soon.id, planned_date=date.today())
    store.add_to_queue(later.id, planned_date=date.today() + timedelta(days=7))

    ids = [q.id for q in store.get_today_queue(plan)]
    assert soon.id in ids
    assert later.id not in ids, "未到期的排队题被提前推送"


def test_queue_count_reflects_remaining(tmp_path):
    """queue_count 反映真实余量（/today 用它给用户看还剩多少）。"""
    store = _store(tmp_path)
    a, b = _add(store, "A"), _add(store, "B")
    store.add_to_queue(a.id)
    store.add_to_queue(b.id)
    assert store.queue_count() == 2
    store.mark_read(a.id)
    assert store.queue_count() == 1


# ── Bug B：打卡必须记进 iq_daily ─────────────────────────────

def test_mark_read_writes_daily_progress(tmp_path):
    """打卡后 iq_daily 必须有一行（旧 bug：表恒 0 行，/progress 永远空）。"""
    store = _store(tmp_path)
    plan = store.create_plan("冲刺", daily_target=5)
    q = _add(store, "题1")

    store.mark_read(q.id)
    store.mark_read(q.id)  # 二次复习

    daily = store.get_daily_progress(plan.id, days=7)
    assert len(daily) == 1, f"iq_daily 应有 1 行，实际 {len(daily)}（旧 bug：无写入方）"
    assert daily[0].questions_read == 2, "同一天打两次卡应累加为 2"
    assert daily[0].target == 5, "target 应取计划的 daily_target"


def test_mark_mastered_counts_into_mastered(tmp_path):
    """标记掌握要累加 questions_mastered，而不是只算已读。"""
    store = _store(tmp_path)
    plan = store.create_plan("冲刺", daily_target=3)
    q1, q2 = _add(store, "题1"), _add(store, "题2")

    store.mark_read(q1.id, mastery=MasteryLevel.MASTERED)
    store.mark_read(q2.id, mastery=MasteryLevel.UNDERSTOOD)

    daily = store.get_daily_progress(plan.id, days=7)
    assert daily[0].questions_read == 2
    assert daily[0].questions_mastered == 1


def test_today_read_count(tmp_path):
    """/today 用的今日已读数应来自 iq_daily。"""
    store = _store(tmp_path)
    plan = store.create_plan("冲刺", daily_target=5)
    assert store.today_read_count(plan.id) == 0
    store.mark_read(_add(store, "题1").id)
    assert store.today_read_count(plan.id) == 1


def test_mark_read_without_active_plan_does_not_crash(tmp_path):
    """无活跃计划时打卡不得抛错（_bump_daily 静默跳过）。"""
    store = _store(tmp_path)
    q = _add(store, "题1")
    store.mark_read(q.id)
    assert store.today_read_count() == 0


def test_mark_read_removes_from_queue(tmp_path):
    """打卡即出队（既有语义，防止改 daily 记账时被破坏）。"""
    store = _store(tmp_path)
    store.create_plan("冲刺", daily_target=5)
    q = _add(store, "题1")
    store.add_to_queue(q.id)
    store.mark_read(q.id)
    assert store.queue_count() == 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
