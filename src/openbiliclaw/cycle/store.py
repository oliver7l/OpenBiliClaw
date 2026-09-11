"""周期记录（Cycle）SQLite 存储层。

独立小模块：记录一次周期事件（日期、间隔天数、备注），并提供简单的
频率/间隔统计。数据存独立 ``cycle.db``（与健康库同目录，隔离锁域）。
连接遵循项目约定：timeout + check_same_thread=False + PRAGMA
（WAL / busy_timeout / synchronous）。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any


class CycleStore:
    def __init__(self, db_path: str = "data/cycle.db"):
        self._db_path = str(Path(db_path).expanduser())
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cycle_records (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    dt             TEXT NOT NULL UNIQUE,   -- 事件日期 YYYY-MM-DD
                    interval_days  INTEGER,                -- 距上次天数（可为空）
                    note           TEXT NOT NULL DEFAULT '',
                    created_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cycle_records_dt ON cycle_records(dt)"
            )

    def list_records(self) -> list[dict[str, Any]]:
        """按日期升序返回全部记录。"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, dt, interval_days, note FROM cycle_records ORDER BY dt ASC"
            ).fetchall()
        return [dict(r) for r in rows]

    def add_record(self, dt: str, note: str = "", interval_days: int | None = None) -> dict[str, Any]:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO cycle_records(dt, note, interval_days) VALUES(?,?,?)",
                (dt, note, interval_days),
            )
            record_id = cur.lastrowid
        return {"id": record_id, "dt": dt, "note": note, "interval_days": interval_days}

    def update_record(self, record_id: int, dt: str, note: str, interval_days: int | None) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE cycle_records SET dt=?, note=?, interval_days=? WHERE id=?",
                (dt, note, interval_days, record_id),
            )
        return cur.rowcount > 0

    def delete_record(self, record_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM cycle_records WHERE id=?", (record_id,))
        return cur.rowcount > 0

    # ── 统计 ──
    def stats(self) -> dict[str, Any]:
        rows = self.list_records()
        n = len(rows)
        intervals = [r["interval_days"] for r in rows if r.get("interval_days")]
        total_interval_days = sum(intervals)
        result: dict[str, Any] = {
            "total": 0,
            "avg_interval_days": None,
            "max_interval_days": None,
            "min_interval_days": None,
            "months": 0,
            "monthly_avg": None,
            "first_date": None,
            "last_date": None,
        }
        if n == 0:
            return result
        # 用日期差计算实际间隔（对自述 interval_days 缺失的记录做兜底）
        dates = [datetime.strptime(r["dt"], "%Y-%m-%d") for r in rows]
        real_spans: list[int] = []
        for a, b in zip(dates, dates[1:]):
            real_spans.append((b - a).days)
        real_spans = [s for s in real_spans if s > 0]
        all_spans = [s for s in intervals if s and s > 0] or real_spans
        span_source = real_spans if real_spans else intervals
        result["total"] = n
        result["first_date"] = rows[0]["dt"]
        result["last_date"] = rows[-1]["dt"]
        if span_source:
            result["avg_interval_days"] = round(sum(span_source) / len(span_source), 1)
            result["max_interval_days"] = max(span_source)
            result["min_interval_days"] = min(span_source)
        if len(dates) >= 2:
            days_span = (dates[-1] - dates[0]).days
            months = max(1, round(days_span / 30.44, 1))
            result["months"] = months
            result["monthly_avg"] = round(n / months, 2)
        return result