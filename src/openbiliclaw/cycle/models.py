"""周期记录（Cycle）数据模型。

极小化的 REST payload 校验，避免引入健康模块的复杂患者维度。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CyclePayload(BaseModel):
    """新增/更新周期记录请求体。dt 必填；interval_days、note 可选。"""

    dt: str = Field(..., description="事件日期 YYYY-MM-DD")
    note: str = Field(default="", description="备注")
    interval_days: int | None = Field(default=None, ge=0, description="距上次天数")


class CycleStats(BaseModel):
    total: int = 0
    avg_interval_days: float | None = None
    max_interval_days: int | None = None
    min_interval_days: int | None = None
    months: float = 0
    monthly_avg: float | None = None
    first_date: str | None = None
    last_date: str | None = None


__all__ = ["CyclePayload", "CycleStats"]
