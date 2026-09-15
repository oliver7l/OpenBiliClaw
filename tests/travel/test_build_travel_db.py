"""``scripts/travel/build_travel_db.py`` 的行为测试。

核心守的是三条**不能退化的政策**（``docs/modules/travel.md`` §同步范围）：

1. 默认 dry-run —— 没给 ``--apply`` 就一个字节都不许改；
2. 幂等 —— 同一份 md 连跑两次，第二次应当「零新增零更新」；
3. db 自己的状态不许被 md 冲掉 —— ``trip_checklist.done`` / ``trips.status`` /
   ``trip_members.id_card`` 这些 md 里没有的列永远不进 UPDATE。

每条都问了「改成错的实现会红吗」，并在 docstring 里写明反例。
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "travel" / "build_travel_db.py"
_SPEC = importlib.util.spec_from_file_location("obc_build_travel_db", _SCRIPT_PATH)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - 路径固定存在
    raise RuntimeError(f"无法加载脚本：{_SCRIPT_PATH}")
_MODULE = importlib.util.module_from_spec(_SPEC)
# ⚠️ 必须先登记再 exec：脚本里用了 @dataclass，dataclass 要靠 sys.modules 解析类型注解，
# 少了这一步会报 'NoneType' object has no attribute '__dict__'。
sys.modules["obc_build_travel_db"] = _MODULE
_SPEC.loader.exec_module(_MODULE)

from tests.travel.test_md_parser import SAMPLE_MD  # noqa: E402  （源文件本身就是最好的样本）

SCHEMA = """
CREATE TABLE trips (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, destination TEXT,
    start_date TEXT, end_date TEXT, people_count INTEGER, status TEXT DEFAULT 'planning',
    budget REAL, notes TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE trip_days (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER NOT NULL, day_number INTEGER NOT NULL,
    date TEXT, title TEXT, description TEXT, transport TEXT, accommodation TEXT, meals TEXT,
    highlights TEXT, notes TEXT);
CREATE TABLE trip_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER NOT NULL, name TEXT, relation TEXT,
    age INTEGER, id_card TEXT, notes TEXT);
CREATE TABLE trip_checklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER NOT NULL, category TEXT, item TEXT,
    owner TEXT, done INTEGER DEFAULT 0, notes TEXT);
CREATE TABLE trip_expenses (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER, category TEXT, item TEXT, detail TEXT,
    amount REAL, currency TEXT DEFAULT 'CNY', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE trip_flights (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER, flight_type TEXT, flight_no TEXT,
    airline TEXT, departure_city TEXT, arrival_city TEXT, departure_time TEXT, arrival_time TEXT,
    departure_airport TEXT, arrival_airport TEXT, aircraft TEXT, duration TEXT, passengers TEXT,
    passenger_count INTEGER, price REAL, order_no TEXT, notes TEXT);
CREATE TABLE trip_hotels (
    id INTEGER PRIMARY KEY AUTOINCREMENT, trip_id INTEGER, day_number INTEGER, date TEXT, city TEXT,
    hotel_name TEXT, star_rating TEXT, address TEXT, check_in TEXT, check_out TEXT, room_type TEXT,
    room_count INTEGER, price REAL, included_in_tour INTEGER, guest_count INTEGER, breakfast INTEGER,
    notes TEXT);
"""

ALL_TABLES = (
    "trips",
    "trip_days",
    "trip_members",
    "trip_checklist",
    "trip_expenses",
    "trip_flights",
    "trip_hotels",
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "travel.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO trips (id, title, destination, start_date, end_date, people_count, status,"
        " budget, notes) VALUES (1, '老标题', '人工填的目的地', '2026-10-02', '2026-10-03',"
        " 3, '已定稿', 66159.0, '人工沉淀的合同号')"
    )
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def md_path(tmp_path: Path) -> Path:
    path = tmp_path / "sample.md"
    path.write_text(SAMPLE_MD, encoding="utf-8")
    return path


def snapshot(db: Path) -> dict[str, list[tuple[Any, ...]]]:
    conn = sqlite3.connect(db)
    try:
        return {
            table: sorted(
                conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608 - 表名是常量
            )
            for table in ALL_TABLES
        }
    finally:
        conn.close()


def rows_of(db: Path, table: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        return list(conn.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
    finally:
        conn.close()


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any]]:
    """跑一次脚本并取回 JSON 摘要。"""
    code = _MODULE.main([*args, "--json"])
    payload = capsys.readouterr().out.strip()
    return code, (json.loads(payload) if payload.startswith("{") else {})


# ---------------------------------------------------------------------------
# 政策 1：默认不写库
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing_but_still_reports_the_work(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """dry-run 必须一个字节都不改，同时必须如实报出「本来会改什么」。

    鉴别力：两段断言缺一不可 —— 只断言「没改」会在脚本忘了提交事务时也绿；
    加上「必须报出工作量」才能挡住「什么都不做却假装成功」。
    """
    before = snapshot(db_path)
    code, stats = run(capsys, "--db", str(db_path), "--md", str(md_path))

    assert code == 0
    assert snapshot(db_path) == before
    planned = sum(t["create"] + t["update"] for t in stats["tables"].values())
    assert planned > 0, "样本 md 与空库之间必然有差异，报 0 说明压根没算 diff"
    assert all(t["applied"] == {} for t in stats["tables"].values())


# ---------------------------------------------------------------------------
# 政策 2：幂等
# ---------------------------------------------------------------------------


def test_applying_twice_changes_nothing_the_second_time(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """连跑两次 --apply，第二次必须零新增零更新（否则每次同步都在复制数据）。"""
    first_code, first = run(capsys, "--db", str(db_path), "--md", str(md_path), "--mode", "apply")
    after_first = snapshot(db_path)
    second_code, second = run(capsys, "--db", str(db_path), "--md", str(md_path), "--mode", "apply")

    assert first_code == second_code == 0
    assert sum(t["create"] + t["update"] for t in first["tables"].values()) > 0
    for table, stat in second["tables"].items():
        assert stat["create"] == 0, f"{table} 第二次还在插入 → 幂等键失效"
        assert stat["update"] == 0, f"{table} 第二次还在更新 → 写入了不稳定字段"
    assert snapshot(db_path) == after_first


def test_flights_same_number_different_passengers_stay_two_rows(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """HU7832 在 md 里是 3 人一行；把它拆成「同航班两组人」也不该互相覆盖。

    鉴别力：幂等键若不含 ``passengers``，第二组会把第一组更新掉（或者干脆
    被判成重复键），行数就不是 2 了。
    """
    twin = SAMPLE_MD.replace(
        "| HU7832 | 重庆→乌鲁木齐 | 08:45-12:45 | 李四、田嘉和、赵六（3人） | ¥3,544 |",
        "| HU7832 | 重庆→乌鲁木齐 | 08:45-12:45 | 李四、田嘉和（2人） | ¥3,544 |\n"
        "| HU7832 | 重庆→乌鲁木齐 | 08:45-12:45 | 赵六（1人） | ¥3,544 |",
    )
    (md_path.parent / "twin.md").write_text(twin, encoding="utf-8")
    code, _ = run(
        capsys, "--db", str(db_path), "--md", str(md_path.parent / "twin.md"), "--mode", "apply"
    )

    assert code == 0
    hu7832 = [r for r in rows_of(db_path, "trip_flights") if r["flight_no"] == "HU7832"]
    assert {(r["passengers"], r["passenger_count"]) for r in hu7832} == {
        ("李四,田嘉和", 2),
        ("赵六", 1),
    }


# ---------------------------------------------------------------------------
# 政策 3：db 的状态列不能被冲掉
# ---------------------------------------------------------------------------


def test_checklist_done_and_owner_survive_sync(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """已勾选的清单项不能被 md 重置 —— 这是整个方案最关键的行为锁。

    鉴别力：若把 checklist 当普通表 upsert（哪怕 md 没给 done），一旦将来解析器
    多输出一个字段，已勾的项就会被改写；下面的第二个断言直接锁住 insert_only 策略。
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trip_checklist (trip_id, category, item, owner, done)"
            " VALUES (1, '证件', '身份证', '童力代办', 1)"
        )
    before = rows_of(db_path, "trip_checklist")

    code, _ = run(
        capsys, "--db", str(db_path), "--md", str(md_path), "--mode", "apply", "--include", "checklist"
    )
    assert code == 0

    rows = rows_of(db_path, "trip_checklist")
    assert len(rows) == 4  # md 4 项，其中「身份证」已存在 → 不重复插入
    mine = next(r for r in rows if r["item"] == "身份证")
    assert (mine["owner"], mine["done"]) == ("童力代办", 1)

    # 策略层直测：即便 md 将来多产出一个字段，checklist 也不许改已有行
    plans = _MODULE._plans()
    assert (
        _MODULE._writable_values(
            plans["checklist"],
            {"done": 0, "notes": "md 里来的"},
            dict(before[0]),
            ["done", "notes", "category", "item"],
        )
        == {}
    )
    assert (
        _MODULE._writable_values(plans["members"], {"relation": "本人"}, {"id": 1, "relation": "旧"}, ["relation"])
        == {"relation": "本人"}
    )


def test_checklist_is_not_synced_unless_asked(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """默认范围不含 checklist：md 粒度比 db 粗，自动同步会制造一堆近似重复项。"""
    code, _ = run(capsys, "--db", str(db_path), "--md", str(md_path), "--mode", "apply")
    assert code == 0
    assert rows_of(db_path, "trip_checklist") == []


def test_trips_row_keeps_hand_written_columns(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """``trips`` 只用来定位行程，绝不回写：destination / status / notes 都是 md 里没有的。

    鉴别力：一旦有人「顺手」把 trips 也同步了，这三条人工沉淀会被 md 的空值冲掉。
    """
    code, _ = run(capsys, "--db", str(db_path), "--md", str(md_path), "--mode", "apply")
    assert code == 0

    trip = rows_of(db_path, "trips")[0]
    assert trip["destination"] == "人工填的目的地"
    assert trip["status"] == "已定稿"
    assert trip["notes"] == "人工沉淀的合同号"
    assert trip["budget"] == 66159.0


def test_id_card_column_is_not_written(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """md 里没有身份证号，库里已经填了的就不许被清空。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trip_members (trip_id, name, relation, id_card) VALUES (1, '张三', '旧', '5101...')"
        )
    code, _ = run(capsys, "--db", str(db_path), "--md", str(md_path), "--mode", "apply")
    assert code == 0

    rows = rows_of(db_path, "trip_members")
    assert len(rows) == 3  # 预插的张三 + md 里的李四/王五，不重复
    zhangsan = next(r for r in rows if r["name"] == "张三")
    assert zhangsan["id_card"] == "5101..."
    assert zhangsan["relation"] == "本人"  # 内容列仍然跟随 md


# ---------------------------------------------------------------------------
# 幂等键 / 错误路径
# ---------------------------------------------------------------------------


def test_hotel_rows_match_by_night_number_not_by_hotel_name(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """db 里被人工加了后缀（亚朵S→亚朵S酒店）的住宿行必须被认出来是同一晚。

    鉴别力：幂等键若带上 ``hotel_name``，apply 一遍就会把 7 晚复制成 14 晚 ——
    这正是第一版实现踩到的坑，本用例就是它的回归锁。
    """
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trip_hotels (trip_id, day_number, hotel_name, guest_count)"
            " VALUES (1, 1, '亚朵S酒店', 9)"
        )
    code, _ = run(capsys, "--db", str(db_path), "--md", str(md_path), "--mode", "apply", "--only", "hotels")
    assert code == 0

    rows = rows_of(db_path, "trip_hotels")
    assert len(rows) == 2, "变成 3 行说明旧行没被认出来，等同原地复制"
    first = next(r for r in rows if r["day_number"] == 1)
    assert first["hotel_name"] == "亚朵S"
    assert first["guest_count"] == 3


def test_duplicate_night_number_aborts_the_whole_run(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """md 里同一晚出现两次（比如分两间房）必须报错，不许后一行悄悄覆盖前一行。"""
    broken = SAMPLE_MD.replace("| 10-03 | 第2晚 |", "| 10-03 | 第1晚 |")
    broken_path = md_path.parent / "broken.md"
    broken_path.write_text(broken, encoding="utf-8")

    before = snapshot(db_path)
    code, _ = run(capsys, "--db", str(db_path), "--md", str(broken_path), "--mode", "apply")

    assert code == 2
    assert snapshot(db_path) == before


def test_missing_section_fails_loudly(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """章节改名导致解析不出表时，必须非 0 退出，不能「0 行要同步 → 成功」。"""
    broken_path = md_path.parent / "no-section.md"
    broken_path.write_text(
        SAMPLE_MD.replace("## 六、住宿安排（V2 更新）", "## 六、住哪里"), encoding="utf-8"
    )
    before = snapshot(db_path)
    code, stats = run(capsys, "--db", str(db_path), "--md", str(broken_path), "--mode", "apply")

    assert code == 2
    assert snapshot(db_path) == before
    assert stats == {}


def test_prune_stale_removes_rows_missing_from_markdown(
    capsys: pytest.CaptureFixture[str], db_path: Path, md_path: Path
) -> None:
    """``--prune-stale`` 是唯一会把旧库清干净的手段，必须真的删（也只在显式时删）。"""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO trip_hotels (trip_id, day_number, hotel_name) VALUES (1, 9, '上一版遗留酒店')"
        )
    run(capsys, "--db", str(db_path), "--md", str(md_path), "--mode", "apply", "--only", "hotels")
    assert len(rows_of(db_path, "trip_hotels")) == 3, "不给 --prune-stale 就不许删"

    code, stats = run(
        capsys,
        "--db",
        str(db_path),
        "--md",
        str(md_path),
        "--mode",
        "apply",
        "--only",
        "hotels",
        "--prune-stale",
    )
    assert code == 0
    assert stats["tables"]["trip_hotels"]["applied"]["delete"] == 1
    assert len(rows_of(db_path, "trip_hotels")) == 2
