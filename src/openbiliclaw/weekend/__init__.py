"""周末怎么玩模块（weekend）。

把日记情绪、豆瓣想看/想读、灵魂画像、本地活动种子聚合成一份带
「为什么适合你」理由的周末计划，支持出门/宅家两种模式与事后打卡复盘。
CLI（``openbiliclaw weekend``）与 API（``/api/weekend``）共用同一引擎。
"""

from __future__ import annotations

from .engine import WeekendEngine
from .models import CheckIn, PlanMode, PlanOption, PlanStatus, WeekendPlan, WeekendSpot
from .routes import build_weekend_router
from .store import DEFAULT_DB_PATH, WeekendStore

__all__ = [
    "PlanMode",
    "PlanStatus",
    "WeekendSpot",
    "WeekendPlan",
    "PlanOption",
    "CheckIn",
    "WeekendStore",
    "WeekendEngine",
    "DEFAULT_DB_PATH",
    "build_weekend_router",
]
