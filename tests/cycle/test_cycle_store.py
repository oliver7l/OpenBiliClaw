"""CycleStore 单元测试。

覆盖周期记录的 CRUD、唯一约束与统计逻辑（此前 0 覆盖）。
存储使用独立的 tmp 库，不触碰 data/cycle.db。
"""

from __future__ import annotations

import sqlite3

import pytest

from openbiliclaw.cycle import CycleStore


@pytest.fixture()
def store(tmp_path) -> CycleStore:
    return CycleStore(db_path=str(tmp_path / "cycle.db"))


def test_init_creates_table_and_index(tmp_path) -> None:
    db = tmp_path / "cycle.db"
    CycleStore(db_path=str(db))
    conn = sqlite3.connect(db)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    finally:
        conn.close()
    assert "cycle_records" in tables
    assert "idx_cycle_records_dt" in indexes


def test_add_and_list_returns_records_ascending(store: CycleStore) -> None:
    store.add_record("2026-03-01", note="first")
    store.add_record("2026-01-01", note="earlier")
    store.add_record("2026-02-01", note="middle")

    rows = store.list_records()
    assert [r["dt"] for r in rows] == ["2026-01-01", "2026-02-01", "2026-03-01"]
    assert rows[0]["note"] == "earlier"
    assert all({"id", "dt", "interval_days", "note"} <= set(r) for r in rows)


def test_add_duplicate_date_raises_integrity_error(store: CycleStore) -> None:
    store.add_record("2026-01-01")
    with pytest.raises(sqlite3.IntegrityError):
        store.add_record("2026-01-01")


def test_update_record_changes_fields_and_reports_hit(store: CycleStore) -> None:
    created = store.add_record("2026-01-01", note="old")
    assert store.update_record(created["id"], "2026-01-02", "new", 31) is True

    row = store.list_records()[0]
    assert row["dt"] == "2026-01-02"
    assert row["note"] == "new"
    assert row["interval_days"] == 31


def test_update_missing_record_returns_false(store: CycleStore) -> None:
    assert store.update_record(9999, "2026-01-01", "", 28) is False


def test_delete_record_reports_hit_and_removes(store: CycleStore) -> None:
    created = store.add_record("2026-01-01")
    assert store.delete_record(created["id"]) is True
    assert store.list_records() == []
    assert store.delete_record(created["id"]) is False


def test_stats_empty_store_returns_zeroed_shape(store: CycleStore) -> None:
    stats = store.stats()
    assert stats["total"] == 0
    assert stats["first_date"] is None
    assert stats["last_date"] is None
    assert stats["avg_interval_days"] is None
    assert stats["months"] == 0


def test_stats_derives_intervals_from_dates_when_absent(store: CycleStore) -> None:
    # 不传 interval_days → 统计应回退到日期差（28 天 / 30 天）
    store.add_record("2026-01-01")
    store.add_record("2026-01-29")
    store.add_record("2026-02-28")

    stats = store.stats()
    assert stats["total"] == 3
    assert stats["first_date"] == "2026-01-01"
    assert stats["last_date"] == "2026-02-28"
    assert stats["min_interval_days"] == 28
    assert stats["max_interval_days"] == 30
    assert stats["avg_interval_days"] == pytest.approx(29.0)
    assert stats["months"] >= 1
    assert stats["monthly_avg"] is not None


def test_stats_uses_date_spans_when_available(store: CycleStore) -> None:
    # 有 ≥2 条记录时，store 的 span_source 取 real_spans（日期差），显式 interval 仅作兜底
    store.add_record("2026-01-01", interval_days=None)
    store.add_record("2026-02-01", interval_days=31)
    store.add_record("2026-03-01", interval_days=28)

    stats = store.stats()
    # 日期差：01-01→02-01 = 31 天，02-01→03-01 = 28 天
    assert stats["min_interval_days"] == 28
    assert stats["max_interval_days"] == 31


def test_store_isolated_between_instances(tmp_path) -> None:
    db_a = tmp_path / "a.db"
    db_b = tmp_path / "b.db"
    CycleStore(db_path=str(db_a)).add_record("2026-01-01")
    store_b = CycleStore(db_path=str(db_b))
    assert store_b.list_records() == []
