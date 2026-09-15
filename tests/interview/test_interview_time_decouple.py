"""面试时间去双写的回归测试（迁移 002）。

真值唯一：``applications.interview_start_at``。待办的 ``due_date`` 只对
``kind='interview'`` 的「面试时间类待办」派生，跟进类待办不受影响。

锁定 2026-09-15 的真实事故：深圳灵动改期后，applications 写 9/17、待办
``due_date`` 还停在 9/16 —— 两处各改各的必然漂移。改动相关逻辑时这些
断言会立刻抓住回归（已做鉴别力校验：摘掉同步调用 → 3 条红；把冲突检测
换成 return [] → 2 条红）。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from openbiliclaw.interview.study import routes as study_routes

APP_DDL = """
CREATE TABLE applications (
    id INTEGER PRIMARY KEY,
    company TEXT, role TEXT, interview_at TEXT, status TEXT,
    direction TEXT, prep_dir TEXT, resume_ver TEXT, note TEXT,
    interview_start_at TEXT, round_note TEXT, stage TEXT
)
"""

TODO_DDL = """
CREATE TABLE todo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    company TEXT DEFAULT '',
    due_date TEXT DEFAULT '',
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    done_at TEXT DEFAULT '',
    kind TEXT DEFAULT 'followup'
)
"""


def _make_dbs(tmp_path, apps, todos):
    """建临时 resume.db（applications）与 interview.db（todo）。"""
    app_db = tmp_path / "resume.db"
    con = sqlite3.connect(app_db)
    con.execute(APP_DDL)
    for r in apps:
        con.execute(
            "INSERT INTO applications (id, company, role, interview_at, status,"
            " interview_start_at, round_note, stage) VALUES (?,?,?,?,?,?,?,?)",
            r,
        )
    con.commit()
    con.close()

    todo_db = tmp_path / "interview.db"
    con = sqlite3.connect(todo_db)
    con.execute(TODO_DDL)
    for r in todos:
        con.execute(
            "INSERT INTO todo (id, title, company, due_date, status, created_at, kind)"
            " VALUES (?,?,?,?,?,?,?)",
            r,
        )
    con.commit()
    con.close()
    return app_db, todo_db


def _bind(monkeypatch, tmp_path, apps, todos):
    app_db, todo_db = _make_dbs(tmp_path, apps, todos)
    monkeypatch.setattr(study_routes, "APPLICATION_DB_PATH", app_db)
    monkeypatch.setattr(study_routes, "INTERVIEW_DB_PATH", todo_db)
    return app_db, todo_db


def _future(days: int = 3) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


def _app_row(app_id=1, company="深圳灵动", start="2026-09-17 19:00",
             round_note="视频面试", stage="待面", raw="2026-09-17 19:00 视频面试"):
    return (app_id, company, "某岗位", raw, "已确认参加", start, round_note, stage)


def _todo(tid, title, company, due, status="pending", kind="interview"):
    return (tid, title, company, due, status, "2026-09-15 19:00:00", kind)


# ── 改期：真值 + 派生 ────────────────────────────────────────

def test_patch_time_updates_truth_and_derived_columns(tmp_path, monkeypatch):
    """改期要同时维护 interview_start_at / interview_at / round_note。"""
    app_db, _ = _bind(monkeypatch, tmp_path, [_app_row()], [])

    resp = study_routes.patch_interview_time(
        1, study_routes.ScheduleTimePatch(interview_start_at="2026-09-18 20:30", round_note="现场面")
    )

    assert resp["interview_start_at"] == "2026-09-18 20:30"
    assert resp["interview_at"] == "2026-09-18 20:30 现场面"
    con = sqlite3.connect(app_db)
    row = con.execute(
        "SELECT interview_start_at, interview_at, round_note FROM applications WHERE id=1"
    ).fetchone()
    con.close()
    assert row[0] == "2026-09-18 20:30"
    assert row[1] == "2026-09-18 20:30 现场面"
    assert row[2] == "现场面"


def test_patch_time_keeps_round_note_when_omitted(tmp_path, monkeypatch):
    """不传 round_note 时沿用原值，不能把场次备注清空。"""
    _bind(monkeypatch, tmp_path, [_app_row()], [])
    resp = study_routes.patch_interview_time(
        1, study_routes.ScheduleTimePatch(interview_start_at="2026-09-18 20:30")
    )
    assert resp["round_note"] == "视频面试"


def test_patch_time_syncs_interview_todo_only(tmp_path, monkeypatch):
    """只同步该公司 kind='interview' 的待办，跟进类待办不受影响。"""
    _app, todo_db = _bind(
        monkeypatch, tmp_path,
        [_app_row()],
        [
            _todo(10, "深圳灵动 面试 19:00", "深圳灵动", "2026-09-17"),
            _todo(11, "深圳灵动：问清股权关系", "深圳灵动", "2026-09-20", kind="followup"),
            _todo(12, "别的公司 面试 10:00", "别的公司", "2026-09-17"),
        ],
    )

    resp = study_routes.patch_interview_time(
        1, study_routes.ScheduleTimePatch(interview_start_at="2026-09-18 20:30")
    )
    assert resp["synced_todo_ids"] == [10]
    assert resp["synced_todo_count"] == 1

    con = sqlite3.connect(todo_db)
    due = dict(con.execute("SELECT id, due_date FROM todo").fetchall())
    con.close()
    assert due[10] == "2026-09-18", "面试类待办应跟着改期"
    assert due[11] == "2026-09-20", "跟进类待办不应被卷走"
    assert due[12] == "2026-09-17", "别家公司的待办不应被动"


def test_patch_time_skips_done_todo(tmp_path, monkeypatch):
    """已完成的面试待办不再同步（历史记录保持原样）。"""
    _app, todo_db = _bind(
        monkeypatch, tmp_path,
        [_app_row()],
        [_todo(10, "深圳灵动 面试 19:00", "深圳灵动", "2026-09-17", status="done")],
    )
    study_routes.patch_interview_time(
        1, study_routes.ScheduleTimePatch(interview_start_at="2026-09-18 20:30")
    )
    con = sqlite3.connect(todo_db)
    due = con.execute("SELECT due_date FROM todo WHERE id=10").fetchone()[0]
    con.close()
    assert due == "2026-09-17"


def test_patch_time_no_sync_when_disabled(tmp_path, monkeypatch):
    """sync_todo=False 时只动 applications。"""
    _app, todo_db = _bind(
        monkeypatch, tmp_path,
        [_app_row()],
        [_todo(10, "深圳灵动 面试 19:00", "深圳灵动", "2026-09-17")],
    )
    resp = study_routes.patch_interview_time(
        1, study_routes.ScheduleTimePatch(interview_start_at="2026-09-18 20:30", sync_todo=False)
    )
    assert resp["synced_todo_count"] == 0
    con = sqlite3.connect(todo_db)
    due = con.execute("SELECT due_date FROM todo WHERE id=10").fetchone()[0]
    con.close()
    assert due == "2026-09-17"


def test_patch_time_rejects_bad_format(tmp_path, monkeypatch):
    """非 ISO 时间直接 422，避免再把自由文本写进结构化列。"""
    _bind(monkeypatch, tmp_path, [_app_row()], [])
    with pytest.raises(HTTPException) as e:
        study_routes.patch_interview_time(
            1, study_routes.ScheduleTimePatch(interview_start_at="9月18号晚上")
        )
    assert e.value.status_code == 422


def test_patch_time_404_on_missing_application(tmp_path, monkeypatch):
    _bind(monkeypatch, tmp_path, [_app_row()], [])
    with pytest.raises(HTTPException) as e:
        study_routes.patch_interview_time(
            999, study_routes.ScheduleTimePatch(interview_start_at="2026-09-18 20:30")
        )
    assert e.value.status_code == 404


# ── 冲突检测 ────────────────────────────────────────────────

def test_schedule_reports_time_conflict(tmp_path, monkeypatch):
    """两边不一致时 /schedule 必须报警（否则又回到静默漂移）。"""
    _bind(
        monkeypatch, tmp_path,
        [_app_row(start="2026-09-17 19:00", raw="2026-09-17 19:00 视频面试")],
        [_todo(10, "深圳灵动 面试 19:00", "深圳灵动", "2026-09-16")],
    )
    data = study_routes.get_schedule()
    assert len(data["time_conflicts"]) == 1, "双写不一致却没报警"
    c = data["time_conflicts"][0]
    assert c["company"] == "深圳灵动"
    assert c["interview_start_at"] == "2026-09-17 19:00"
    assert c["todo_due_date"] == "2026-09-16"
    assert c["todo_id"] == 10


def test_schedule_no_conflict_when_aligned(tmp_path, monkeypatch):
    """日期一致时不得误报。"""
    _bind(
        monkeypatch, tmp_path,
        [_app_row()],
        [_todo(10, "深圳灵动 面试 19:00", "深圳灵动", "2026-09-17")],
    )
    assert study_routes.get_schedule()["time_conflicts"] == []


def test_schedule_ignores_followup_todo_when_detecting_conflict(tmp_path, monkeypatch):
    """跟进类待办日期不同不算冲突（它本来就不该跟面试日期一致）。"""
    _bind(
        monkeypatch, tmp_path,
        [_app_row()],
        [_todo(11, "谈薪 R3 电话提级别", "深圳灵动", "2026-09-18", kind="followup")],
    )
    assert study_routes.get_schedule()["time_conflicts"] == []


def test_schedule_exposes_application_id(tmp_path, monkeypatch):
    """/schedule 必须回传 id，否则前端没法调改期接口。"""
    _bind(monkeypatch, tmp_path, [_app_row()], [])
    data = study_routes.get_schedule()
    assert data["upcoming"] and data["upcoming"][0]["id"] == 1


def test_schedule_conflict_silent_without_kind_column(tmp_path, monkeypatch):
    """待办表未跑迁移 002（缺 kind 列）时宁可不报，不能把跟进类误判成冲突。"""
    app_db, todo_db = _make_dbs(tmp_path, [], [])
    con = sqlite3.connect(todo_db)
    con.execute("ALTER TABLE todo DROP COLUMN kind")
    con.execute("INSERT INTO todo (id,title,company,due_date,status,created_at)"
                " VALUES (1,'深圳灵动：问清股权关系','深圳灵动','2026-09-20','pending','')")
    con.commit()
    con.close()
    con = sqlite3.connect(app_db)
    con.execute(
        "INSERT INTO applications (id, company, role, interview_at, status,"
        " interview_start_at, stage) VALUES (1,'深圳灵动','r','x','已确认参加','2026-09-17 19:00','待面')"
    )
    con.commit()
    con.close()
    monkeypatch.setattr(study_routes, "APPLICATION_DB_PATH", app_db)
    monkeypatch.setattr(study_routes, "INTERVIEW_DB_PATH", todo_db)
    assert study_routes.get_schedule()["time_conflicts"] == []


def test_schedule_conflict_also_works_without_structured_column(tmp_path, monkeypatch):
    """未跑迁移 001 时，从 interview_at 自由文本抠出的日期同样能检出冲突。"""
    app_db, todo_db = _make_dbs(tmp_path, [], [])
    con = sqlite3.connect(app_db)
    con.execute("ALTER TABLE applications DROP COLUMN interview_start_at")
    con.execute(
        "INSERT INTO applications (id, company, role, interview_at, status, stage)"
        " VALUES (1,'X','r','2026-09-17 19:00 视频面','已确认参加','待面')"
    )
    con.commit()
    con.close()
    con = sqlite3.connect(todo_db)
    con.execute("INSERT INTO todo (id,title,company,due_date,status,created_at,kind)"
                " VALUES (1,'X 面试 19:00','X','2026-09-16','pending','','interview')")
    con.commit()
    con.close()
    monkeypatch.setattr(study_routes, "APPLICATION_DB_PATH", app_db)
    monkeypatch.setattr(study_routes, "INTERVIEW_DB_PATH", todo_db)
    conflicts = study_routes.get_schedule()["time_conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["interview_start_at"] == "2026-09-17"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
