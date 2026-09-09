"""Tests for the view_history implicit-feedback (dwell) storage layer.

Covers the v0.3.x dwell column migration and the aggregation backing the
RankAgent implicit-feedback path: insert/update dwell and the
get_dwell_scores / get_total_view_count windows.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING

import pytest

from openbiliclaw.storage.database import Database

if TYPE_CHECKING:
    from pathlib import Path


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "dwell.db")
    db.initialize()
    return db


def _insert(
    db: Database, *, bvid: str, topic: str, dwell: float, when: datetime.datetime | None = None
) -> int:
    db.insert_view_history({"bvid": bvid, "topic_group": topic, "dwell_seconds": dwell})
    row_id = int(db.conn.execute("SELECT MAX(id) FROM events.view_history").fetchone()[0])
    if when is not None:
        db.conn.execute(
            "UPDATE events.view_history SET viewed_at = ? WHERE id = ?",
            (when.strftime("%Y-%m-%d %H:%M:%S"), row_id),
        )
        db.conn.commit()
    return row_id


def _dwell_of(db: Database, bvid: str) -> list[float]:
    return [
        float(r[0])
        for r in db.conn.execute(
            "SELECT dwell_seconds FROM events.view_history WHERE bvid = ? ORDER BY id", (bvid,)
        ).fetchall()
    ]


def test_schema_migrates_dwell_seconds_column_and_is_idempotent(tmp_path: Path) -> None:
    db = Database(tmp_path / "migrate.db")
    db.initialize()
    cols = {r["name"] for r in db.conn.execute("PRAGMA events.table_info(view_history)").fetchall()}
    assert "dwell_seconds" in cols
    db.initialize()  # second pass must not error on the ALTER TABLE guard


def test_insert_stores_dwell_seconds(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _insert(db, bvid="v1", topic="科技", dwell=12.5)
    assert _dwell_of(db, "v1") == [pytest.approx(12.5)]


def test_update_view_dwell_only_touches_latest_row(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _insert(db, bvid="v1", topic="A", dwell=0)
    _insert(db, bvid="v1", topic="A", dwell=0)
    assert db.update_view_dwell("v1", 42.0) is True
    vals = _dwell_of(db, "v1")
    assert vals[0] == 0.0 and vals[-1] == pytest.approx(42.0)
    assert db.update_view_dwell("missing", 10.0) is False


def test_get_dwell_scores_math(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = datetime.datetime.now()
    # T1: 700→capped 600 (deep), 5 (quick exit), 30 (neither deep nor quick)
    for dwell in (700, 5, 30):
        _insert(db, bvid="a", topic="T1", dwell=dwell, when=now)
    # T2: single 2000 → capped 600, deep
    _insert(db, bvid="b", topic="T2", dwell=2000, when=now)
    # empty topic is ignored
    _insert(db, bvid="c", topic="", dwell=500, when=now)

    scores = db.get_dwell_scores(days=14)
    assert "" not in scores
    # T1: base=(600+5+30)/1800=0.3528, +0.1(deep) -0.05(quick) ≈ 0.4028
    assert scores["T1"] == pytest.approx(0.4028, abs=0.001)
    # T2: base=600/1800=0.3333, +0.1(deep) ≈ 0.4333
    assert scores["T2"] == pytest.approx(0.4333, abs=0.001)


def test_get_dwell_scores_respects_window(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _insert(
        db,
        bvid="o",
        topic="OLD",
        dwell=1000,
        when=datetime.datetime.now() - datetime.timedelta(days=100),
    )
    assert "OLD" not in db.get_dwell_scores(days=14)
    assert "OLD" in db.get_dwell_scores(days=365)


def test_get_dwell_scores_zero_dwell_clamps_to_zero(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _insert(db, bvid="z", topic="Z", dwell=0, when=datetime.datetime.now())
    # dwell=0 → base 0, no deep, not quick (needs >0) → 0.0, still present
    assert db.get_dwell_scores(days=14)["Z"] == pytest.approx(0.0)


def test_get_total_view_count_window(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for _ in range(3):
        _insert(db, bvid="n", topic="A", dwell=20, when=datetime.datetime.now())
    _insert(
        db,
        bvid="old",
        topic="A",
        dwell=20,
        when=datetime.datetime.now() - datetime.timedelta(days=100),
    )
    assert db.get_total_view_count(days=30) == 3
    assert db.get_total_view_count(days=365) == 4
