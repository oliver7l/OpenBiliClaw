"""阅读库正文统一回补模块（refill）。

把散落各处的补抓脚本收敛为「中央队列 refill_queue + 可插拔通道 + 单调度」。
M1 交付中央队列与 CLI 观测；M2 引入 Scheduler 与 direct / search_click 通道
（见 docs/refill-module-design.md）。
"""

from __future__ import annotations

from openbiliclaw.refill.queue import RefillItem, RefillQueue, StatusRow
from openbiliclaw.refill.scheduler import RefillScheduler

__all__ = ["RefillQueue", "RefillScheduler", "RefillItem", "StatusRow"]