"""面试排期（/schedule）阶段归一化与时间结构化的回归测试。

锁定两个 2026-09-15 修复的真实 bug。两条测试在修复前都会失败
（不是恒绿断言），改动相关逻辑时若回归会被立刻抓住。

Bug 1：``is_upcoming`` 恒为 False
    旧实现 ``status in ("待面", "进行中")`` 硬匹配，但真实 status 形如
    ``'已确认参加(9/17周四 19:00 视频面)'``，永远命不中 → upcoming 永远为空。

Bug 2：阶段误判
    ``'已结束(输给内转,HR留门:新增HC可直接推进谈薪)'`` 里"谈薪"是条件句，
    旧规则顺序让"谈薪"先命中 → 被误判为「谈薪中」（应为「已结束」）。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from openbiliclaw.interview.study import routes as study_routes

TABLE_DDL = """
CREATE TABLE applications (
    id INTEGER PRIMARY KEY,
    company TEXT, role TEXT, interview_at TEXT, status TEXT,
    direction TEXT, prep_dir TEXT, resume_ver TEXT, note TEXT,
    interview_start_at TEXT, round_note TEXT, stage TEXT
)
"""


def _make_db(tmp_path, rows):
    """建一个临时 resume.db（含迁移 001 的结构化列）。"""
    db = tmp_path / "resume.db"
    con = sqlite3.connect(db)
    con.execute(TABLE_DDL)
    for r in rows:
        con.execute(
            "INSERT INTO applications (company, role, interview_at, status,"
            " interview_start_at, round_note, stage) VALUES (?,?,?,?,?,?,?)",
            r,
        )
    con.commit()
    con.close()
    return db


def _future(days: int = 3) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


# ── Bug 1：is_upcoming ────────────────────────────────────────

def test_upcoming_includes_confirmed_status(tmp_path, monkeypatch):
    """status='已确认参加(9/17周四 19:00 视频面)' 的岗位必须出现在 upcoming。

    修复前：旧代码用 ``status in ("待面","进行中")`` 判断，这个值不在元组里，
    is_upcoming 恒 False，upcoming 列表永远为空。
    """
    future = _future()
    db = _make_db(tmp_path, [
        ("深圳灵动", "Data Scientist", f"{future} 19:00 视频面试",
         "已确认参加(9/17周四 19:00 视频面)", f"{future} 19:00", "视频面试", "待面"),
    ])
    monkeypatch.setattr(study_routes, "APPLICATION_DB_PATH", db)

    result = study_routes.get_schedule()

    assert result["upcoming_count"] == 1, "已确认参加的待面岗位必须计入 upcoming"
    assert result["upcoming"][0]["company"] == "深圳灵动"
    assert result["upcoming"][0]["is_upcoming"] is True


def test_upcoming_excludes_terminated_and_finished(tmp_path, monkeypatch):
    """已终止/已结束的岗位即使日期在未来，也不能进 upcoming。"""
    future = _future()
    db = _make_db(tmp_path, [
        ("乐趣无限", "算法", f"{future} 15:00", "已终止(面试体验差/主动放弃)",
         f"{future} 15:00", "", "已终止"),
    ])
    monkeypatch.setattr(study_routes, "APPLICATION_DB_PATH", db)

    result = study_routes.get_schedule()

    assert result["upcoming_count"] == 0
    assert result["history"][0]["stage"] == "已终止"


def test_upcoming_sorted_ascending(tmp_path, monkeypatch):
    """upcoming 按时间升序（最近的在前），无时间的沉底。"""
    d1, d2 = _future(2), _future(5)
    db = _make_db(tmp_path, [
        ("远的公司", "A", f"{d2} 16:00", "待面", f"{d2} 16:00", "", "待面"),
        ("近的公司", "B", f"{d1} 10:00", "待面", f"{d1} 10:00", "", "待面"),
        ("无期公司", "C", "未约面", "待面", "", "", "待面"),
    ])
    monkeypatch.setattr(study_routes, "APPLICATION_DB_PATH", db)

    result = study_routes.get_schedule()

    names = [j["company"] for j in result["upcoming"]]
    assert names == ["近的公司", "远的公司", "无期公司"], f"排序错误：{names}"


# ── Bug 2：阶段归一化顺序（终态优先）──────────────────────────

@pytest.mark.parametrize("status,expected", [
    # 核心回归：'谈薪' 出现在句子里但整体是「已结束」
    ("已结束(输给内转,HR留门:新增HC可直接推进谈薪)", "已结束"),
    ("已结束", "已结束"),
    ("已终止(面试体验差/主动放弃)", "已终止"),
    ("主动放弃", "已终止"),
    ("谈薪中(R1已完成·D1/43K方案)", "谈薪中"),
    ("已确认参加(9/17周四 19:00 视频面)", "待面"),
    ("已投递(AI投递)-静默中", "已投递"),
    ("候选(未投递)-待决策", "候选"),
    ("一面通过，下周二复试", "面试中"),
])
def test_fallback_stage_priority(status, expected):
    """缺结构化列时的兜底推断：终态必须优先于「谈薪」。"""
    assert study_routes._fallback_stage(status) == expected


def test_schedule_falls_back_when_columns_missing(tmp_path, monkeypatch):
    """库里没有迁移 001 的三列时，仍能靠 interview_at/status 兜底工作。"""
    future = _future()
    db = tmp_path / "resume.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE applications (id INTEGER PRIMARY KEY, company TEXT,"
        " role TEXT, interview_at TEXT, status TEXT, direction TEXT,"
        " prep_dir TEXT, resume_ver TEXT, note TEXT)"
    )
    con.execute(
        "INSERT INTO applications (company, interview_at, status) VALUES (?,?,?)",
        ("老库公司", f"{future} 14:00", "已确认参加"),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(study_routes, "APPLICATION_DB_PATH", db)

    result = study_routes.get_schedule()

    assert result["structured"] is False, "应识别出缺少结构化列"
    assert result["upcoming_count"] == 1
    assert result["upcoming"][0]["stage"] == "待面", "应从 status 兜底推断出阶段"
    assert result["upcoming"][0]["interview_start_at"] == future
