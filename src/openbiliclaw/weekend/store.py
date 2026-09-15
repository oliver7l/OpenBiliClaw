"""周末玩法模块存储层（SQLite，data/weekend.db）。

表结构：
- weekend_spots：本地可玩活动/地点（种子库或联网 provider 写入）
- weekend_plans：生成的周末计划
- weekend_checkins：事后打卡复盘
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from openbiliclaw.config import _project_root
from openbiliclaw.storage.database import open_db_conn

from .models import CheckIn, WeekendPlan, WeekendSpot

DEFAULT_DB_PATH = "data/weekend.db"


def resolve_weekend_path(p: str | Path) -> Path:
    """把 weekend 相关路径锚定到项目根（CWD 无关）。

    2026-09-15 修复：原先 ``DEFAULT_DB_PATH`` 等是 CWD 相对字符串，非仓库根
    启动时会静默读写到别处（表现为 spots 为 0 却仍照常输出「为什么适合你」）。
    """
    path = Path(p)
    return path if path.is_absolute() else _project_root() / path

SCHEMA = """
CREATE TABLE IF NOT EXISTS weekend_spots (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT '',
    district     TEXT NOT NULL DEFAULT '',
    location     TEXT NOT NULL DEFAULT '',
    detail       TEXT NOT NULL DEFAULT '',
    date_start   TEXT,
    date_end     TEXT,
    time_text    TEXT NOT NULL DEFAULT '',
    transit      TEXT NOT NULL DEFAULT '',
    free         INTEGER NOT NULL DEFAULT 1,
    tags         TEXT NOT NULL DEFAULT '[]',
    suitable_for TEXT NOT NULL DEFAULT '[]',
    notes        TEXT NOT NULL DEFAULT '',
    source_url   TEXT NOT NULL DEFAULT '',
    source_name  TEXT NOT NULL DEFAULT '',
    valid_until  TEXT,
    imported_at  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_spot_district ON weekend_spots(district);
CREATE INDEX IF NOT EXISTS idx_spot_category ON weekend_spots(category);
CREATE INDEX IF NOT EXISTS idx_spot_valid ON weekend_spots(valid_until);

CREATE TABLE IF NOT EXISTS weekend_plans (
    id          TEXT PRIMARY KEY,
    week_of     TEXT NOT NULL,
    mode        TEXT NOT NULL,
    mood_basis  TEXT NOT NULL DEFAULT '',
    options     TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'pending',
    source      TEXT NOT NULL DEFAULT 'local',
    created_at  TEXT NOT NULL,
    decided_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_plan_week ON weekend_plans(week_of);
CREATE INDEX IF NOT EXISTS idx_plan_status ON weekend_plans(status);

CREATE TABLE IF NOT EXISTS weekend_checkins (
    id           TEXT PRIMARY KEY,
    plan_id      TEXT NOT NULL,
    option_index INTEGER NOT NULL,
    rating       INTEGER NOT NULL,
    note         TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_checkin_plan ON weekend_checkins(plan_id);
"""


class WeekendStore:
    """周末玩法数据访问层。"""

    def __init__(self, db_path: str | Path | None = None) -> None:
        # 默认路径在**构造时**解析（而非 import 时），测试里先设
        # OPENBILICLAW_PROJECT_ROOT 再构造也能生效。
        self.db_path = str(resolve_weekend_path(db_path or DEFAULT_DB_PATH))
        self.conn = open_db_conn(self.db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ── 本地活动（spots）──────────────────────────────────────────
    def import_spots(self, spots: list[WeekendSpot], *, clear: bool = False) -> int:
        """批量写入活动种子；clear=True 时先清空再写（用于整库重建）。"""
        if clear:
            self.conn.execute("DELETE FROM weekend_spots")
        now = datetime.now().isoformat(timespec="seconds")
        count = 0
        for s in spots:
            row = s.to_row()
            row["imported_at"] = now
            self.conn.execute(
                """
                INSERT INTO weekend_spots (
                    id, title, category, district, location, detail,
                    date_start, date_end, time_text, transit, free,
                    tags, suitable_for, notes, source_url, source_name, valid_until, imported_at
                ) VALUES (
                    :id, :title, :category, :district, :location, :detail,
                    :date_start, :date_end, :time_text, :transit, :free,
                    :tags, :suitable_for, :notes, :source_url, :source_name, :valid_until, :imported_at
                )
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title, category=excluded.category,
                    district=excluded.district, location=excluded.location,
                    detail=excluded.detail, date_start=excluded.date_start,
                    date_end=excluded.date_end, time_text=excluded.time_text,
                    transit=excluded.transit, free=excluded.free,
                    tags=excluded.tags, suitable_for=excluded.suitable_for,
                    notes=excluded.notes, source_url=excluded.source_url,
                    source_name=excluded.source_name, valid_until=excluded.valid_until
                """,
                row,
            )
            count += 1
        self.conn.commit()
        return count

    def list_spots(
        self,
        *,
        district: str | None = None,
        category: str | None = None,
        suitable_for: str | None = None,
        include_expired: bool = False,
        limit: int = 100,
    ) -> list[WeekendSpot]:
        """列出活动；可按区域/类别/适合人群过滤，默认排除已过期项。"""
        sql = "SELECT * FROM weekend_spots WHERE 1=1"
        params: dict[str, Any] = {}
        if district:
            sql += " AND district = :district"
            params["district"] = district
        if category:
            sql += " AND category = :category"
            params["category"] = category
        if suitable_for:
            # suitable_for 存为 JSON 数组，用 LIKE 匹配包含
            sql += " AND suitable_for LIKE :sf"
            params["sf"] = f'%"{suitable_for}"%'
        if not include_expired:
            sql += " AND (valid_until IS NULL OR valid_until >= date('now'))"
        sql += " ORDER BY (valid_until IS NULL), valid_until LIMIT :limit"
        params["limit"] = limit
        cur = self.conn.execute(sql, params)
        return [WeekendSpot.from_row(dict(r)) for r in cur.fetchall()]

    def spot_count(self) -> int:
        return int(self.conn.execute("SELECT count(*) FROM weekend_spots").fetchone()[0])

    # ── 计划（plans）──────────────────────────────────────────────
    def save_plan(self, plan: WeekendPlan) -> None:
        self.conn.execute(
            """
            INSERT INTO weekend_plans (
                id, week_of, mode, mood_basis, options, status, source, created_at, decided_at
            ) VALUES (:id, :week_of, :mode, :mood_basis, :options, :status, :source, :created_at, :decided_at)
            ON CONFLICT(id) DO UPDATE SET
                mode=excluded.mode, mood_basis=excluded.mood_basis,
                options=excluded.options, status=excluded.status,
                source=excluded.source, decided_at=excluded.decided_at
            """,
            {
                "id": plan.id,
                "week_of": plan.week_of,
                "mode": plan.mode,
                "mood_basis": plan.mood_basis,
                "options": json.dumps([o.to_dict() for o in plan.options], ensure_ascii=False),
                "status": plan.status,
                "source": plan.source,
                "created_at": plan.created_at,
                "decided_at": plan.decided_at,
            },
        )
        self.conn.commit()

    def get_plan(self, week_of: str) -> WeekendPlan | None:
        row = self.conn.execute(
            "SELECT * FROM weekend_plans WHERE week_of = ? ORDER BY created_at DESC LIMIT 1",
            (week_of,),
        ).fetchone()
        return WeekendPlan.from_row(dict(row)) if row else None

    def get_plan_by_id(self, plan_id: str) -> WeekendPlan | None:
        row = self.conn.execute("SELECT * FROM weekend_plans WHERE id = ?", (plan_id,)).fetchone()
        return WeekendPlan.from_row(dict(row)) if row else None

    def list_plans(self, *, limit: int = 20) -> list[WeekendPlan]:
        rows = self.conn.execute("SELECT * FROM weekend_plans ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [WeekendPlan.from_row(dict(r)) for r in rows]

    def decide_plan(self, week_of: str, status: str, *, option_index: int | None = None) -> WeekendPlan | None:
        """确认/跳过本周计划，并返回更新后的计划。"""
        plan = self.get_plan(week_of)
        if plan is None:
            return None
        plan.status = status
        plan.decided_at = datetime.now().isoformat(timespec="seconds")
        self.save_plan(plan)
        return plan

    def plan_exists_for_week(self, week_of: str) -> bool:
        return self.get_plan(week_of) is not None

    # ── 打卡复盘（checkins）───────────────────────────────────────
    def add_checkin(self, checkin: CheckIn) -> None:
        self.conn.execute(
            """
            INSERT INTO weekend_checkins (id, plan_id, option_index, rating, note, created_at)
            VALUES (:id, :plan_id, :option_index, :rating, :note, :created_at)
            """,
            checkin.to_row(),
        )
        self.conn.commit()

    def list_checkins(self, *, plan_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM weekend_checkins"
        params: tuple = ()
        if plan_id:
            sql += " WHERE plan_id = ?"
            params = (plan_id,)
        sql += " ORDER BY created_at DESC LIMIT ?"
        rows = self.conn.execute(sql, (*params, limit)).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self.conn:
            pass
        self.conn.close()
