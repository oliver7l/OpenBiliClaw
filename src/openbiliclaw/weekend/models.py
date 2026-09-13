"""周末玩法模块数据模型。

纯离线的「周末怎么玩」规划：把日记情绪、豆瓣想看/想读、灵魂画像、
本地活动种子（weekend_spots）聚合成一份带「为什么适合你」理由的周末计划，
支持出门 / 宅家两种模式，以及事后打卡复盘。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class PlanMode(StrEnum):
    """计划模式。"""

    AUTO = "auto"  # 由情绪基线自动决定 出门/宅家
    OUTDOOR = "outdoor"  # 出门
    INDOOR = "indoor"  # 宅家


class PlanStatus(StrEnum):
    """计划生命周期状态。"""

    PENDING = "pending"  # 已生成未确认
    CONFIRMED = "confirmed"  # 用户确认采用
    SKIPPED = "skipped"  # 用户跳过本周
    DONE = "done"  # 已执行并复盘


@dataclass
class WeekendSpot:
    """一个本地可玩的活动/地点（来自种子库或联网 provider）。"""

    id: str
    title: str
    category: str
    district: str
    location: str
    detail: str = ""
    date_start: str | None = None
    date_end: str | None = None
    time_text: str = ""
    transit: str = ""
    free: bool = True
    tags: list[str] = field(default_factory=list)
    suitable_for: list[str] = field(default_factory=list)
    notes: str = ""
    source_url: str = ""
    source_name: str = ""
    valid_until: str | None = None

    @property
    def is_expired(self) -> bool:
        if not self.valid_until:
            return False
        try:
            return date.fromisoformat(self.valid_until) < date.today()
        except ValueError:
            return False

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "district": self.district,
            "location": self.location,
            "detail": self.detail,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "time_text": self.time_text,
            "transit": self.transit,
            "free": 1 if self.free else 0,
            "tags": json.dumps(self.tags, ensure_ascii=False),
            "suitable_for": json.dumps(self.suitable_for, ensure_ascii=False),
            "notes": self.notes,
            "source_url": self.source_url,
            "source_name": self.source_name,
            "valid_until": self.valid_until,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> WeekendSpot:
        return cls(
            id=row["id"],
            title=row["title"],
            category=row["category"],
            district=row["district"],
            location=row["location"],
            detail=row.get("detail", "") or "",
            date_start=row.get("date_start"),
            date_end=row.get("date_end"),
            time_text=row.get("time_text", "") or "",
            transit=row.get("transit", "") or "",
            free=bool(row.get("free", 1)),
            tags=json.loads(row["tags"]) if row.get("tags") else [],
            suitable_for=json.loads(row["suitable_for"]) if row.get("suitable_for") else [],
            notes=row.get("notes", "") or "",
            source_url=row.get("source_url", "") or "",
            source_name=row.get("source_name", "") or "",
            valid_until=row.get("valid_until"),
        )


@dataclass
class PlanOption:
    """计划里的一个具体方案（带理由与可执行卡片）。"""

    title: str
    mode: str  # outdoor / indoor
    why: str  # 为什么适合「你」
    energy: str = "medium"  # low / medium / high 消耗
    actions: list[str] = field(default_factory=list)  # 可执行卡片
    spot_ids: list[str] = field(default_factory=list)  # 关联本地活动
    douban_picks: list[str] = field(default_factory=list)  # 关联豆瓣想看/想读

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "mode": self.mode,
            "why": self.why,
            "energy": self.energy,
            "actions": self.actions,
            "spot_ids": self.spot_ids,
            "douban_picks": self.douban_picks,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> PlanOption:
        return cls(
            title=d["title"],
            mode=d.get("mode", "outdoor"),
            why=d.get("why", ""),
            energy=d.get("energy", "medium"),
            actions=list(d.get("actions", [])),
            spot_ids=list(d.get("spot_ids", [])),
            douban_picks=list(d.get("douban_picks", [])),
        )


@dataclass
class WeekendPlan:
    """一份周末计划（含 1~3 个方案）。"""

    week_of: str  # 该周末的周六日期，如 2026-09-12
    mode: str  # auto / outdoor / indoor（最终判定）
    mood_basis: str  # 生成依据的情绪描述
    options: list[PlanOption]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = PlanStatus.PENDING.value
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    decided_at: str | None = None
    source: str = "local"  # local / friday_push

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "week_of": self.week_of,
            "mode": self.mode,
            "mood_basis": self.mood_basis,
            "options": [o.to_dict() for o in self.options],
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "source": self.source,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> WeekendPlan:
        opts = json.loads(row["options"]) if row.get("options") else []
        return cls(
            id=row["id"],
            week_of=row["week_of"],
            mode=row["mode"],
            mood_basis=row.get("mood_basis", "") or "",
            options=[PlanOption.from_dict(o) for o in opts],
            status=row.get("status", PlanStatus.PENDING.value),
            created_at=row.get("created_at", ""),
            decided_at=row.get("decided_at"),
            source=row.get("source", "local"),
        )


@dataclass
class CheckIn:
    """事后打卡复盘。"""

    plan_id: str
    option_index: int
    rating: int  # 1~5
    note: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def to_row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "plan_id": self.plan_id,
            "option_index": self.option_index,
            "rating": self.rating,
            "note": self.note,
            "created_at": self.created_at,
        }
