"""周末玩法 API 路由。

Endpoints（prefix ``/api/weekend``）：
- GET  /api/weekend/status        — 模块状态（spots 数、最近计划、配置）
- POST /api/weekend/generate      — 生成计划 {mode?, week_of?}
- GET  /api/weekend/plans         — 历史计划列表 ?limit=
- GET  /api/weekend/plans/{week_of} — 某周末计划
- POST /api/weekend/plans/{week_of}/decide — 确认/跳过 {status, option_index?}
- POST /api/weekend/checkin       — 打卡复盘 {plan_id, option_index, rating, note?}
- GET  /api/weekend/spots         — 活动种子列表 ?district=&suitable_for=
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .engine import WeekendEngine
from .models import PlanMode, PlanStatus
from .store import WeekendStore

logger = logging.getLogger(__name__)


class GenerateIn(BaseModel):
    mode: str = Field(default=PlanMode.AUTO.value, description="auto/outdoor/indoor")
    week_of: str | None = Field(default=None, description="周六日期 YYYY-MM-DD，缺省取本周")


class DecideIn(BaseModel):
    status: str = Field(..., description="confirmed/skipped")
    option_index: int | None = Field(default=None)


class CheckInIn(BaseModel):
    plan_id: str
    option_index: int = Field(ge=0)
    rating: int = Field(ge=1, le=5)
    note: str = Field(default="")


def build_weekend_router(
    *,
    store: WeekendStore | None = None,
    diary_db: str = "data/diary.db",
    douban_db: str = "data/douban.db",
    use_llm: bool = False,
) -> APIRouter:
    """创建 weekend 路由。store 为空时按默认路径新建。"""
    router = APIRouter(prefix="/api/weekend", tags=["weekend"])
    _store = store or WeekendStore()
    engine = WeekendEngine(_store, diary_db=diary_db, douban_db=douban_db, use_llm=use_llm)

    @router.get("/status")
    def status() -> dict[str, Any]:
        plan = _store.list_plans(limit=1)
        return {
            "enabled": True,
            "spot_count": _store.spot_count(),
            "latest_plan_week": plan[0].week_of if plan else None,
            "latest_plan_status": plan[0].status if plan else None,
            "modes": [m.value for m in PlanMode],
            "statuses": [s.value for s in PlanStatus],
        }

    @router.post("/generate")
    def generate(body: GenerateIn) -> dict[str, Any]:
        try:
            plan = engine.generate(mode=body.mode, week_of=body.week_of)
        except Exception as exc:  # noqa: BLE001
            logger.exception("weekend generate failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return plan.to_dict()

    @router.get("/plans")
    def list_plans(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
        return [p.to_dict() for p in _store.list_plans(limit=limit)]

    @router.get("/plans/{week_of}")
    def get_plan(week_of: str) -> dict[str, Any]:
        plan = _store.get_plan(week_of)
        if plan is None:
            raise HTTPException(status_code=404, detail="no plan for week")
        return plan.to_dict()

    @router.post("/plans/{week_of}/decide")
    def decide(week_of: str, body: DecideIn) -> dict[str, Any]:
        if body.status not in (PlanStatus.CONFIRMED.value, PlanStatus.SKIPPED.value):
            raise HTTPException(status_code=400, detail="status must be confirmed/skipped")
        plan = _store.decide_plan(week_of, body.status)
        if plan is None:
            raise HTTPException(status_code=404, detail="no plan for week")
        return plan.to_dict()

    @router.post("/checkin")
    def checkin(body: CheckInIn) -> dict[str, Any]:
        from .models import CheckIn

        if _store.get_plan_by_id(body.plan_id) is None:
            raise HTTPException(status_code=404, detail="plan not found")
        _store.add_checkin(
            CheckIn(
                plan_id=body.plan_id,
                option_index=body.option_index,
                rating=body.rating,
                note=body.note,
            )
        )
        return {"ok": True}

    @router.get("/spots")
    def list_spots(
        district: str | None = Query(default=None),
        suitable_for: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return [s.__dict__ for s in _store.list_spots(district=district, suitable_for=suitable_for, limit=limit)]

    return router
