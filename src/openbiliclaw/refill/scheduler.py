"""单轮调度：按配额 pick → 路由 → 派发 → 写正文 → 收口队列。

抓取成功且正文达 ``min_body_len`` → 写 content_text（注入 ``write_content``）并把
队项标 ``done``；``ok=False`` 且 detail 以 ``PERMANENT`` 开头 → 标 ``skipped``
（永久不可抓）；其余失败 → ``attempts+1``，达 ``max_attempts`` 置 ``dropped``；
基础设施故障（抛 :class:`BridgeUnavailableError`）→ 不计数、跳过该条继续。

AgentLimb 桥接关断时，只跳过依赖它的 ``direct`` / ``search_click``，不阻塞
``ytdlp`` / ``getnote`` 等独立通道（multi-infra 下不再整轮 abort）。状态全落
refill_queue → 进程异常退出重启后按队列续跑，不丢队。
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from openbiliclaw.refill import channels as channels_pkg
from openbiliclaw.refill.channels.base import PERMANENT, BridgeUnavailableError, route_channels
from openbiliclaw.refill.queue import RefillItem, RefillQueue

# 写入正文的回调：``(item, body) -> None``。默认由 CLI 注入基于 Database 的实现。
WriteCallback = Callable[[RefillItem, str], None]

_EMPTY_COUNTS = {"picked": 0, "done": 0, "failed": 0, "dropped": 0, "skipped": 0, "bridge_off": 0}


class RefillScheduler:
    """按配置配额跑一轮回补（PM2 cron 每触发一次调 ``run_cycle`` 一次）。"""

    def __init__(
        self,
        queue: RefillQueue,
        *,
        min_body_len: int = 30,
        quota: dict[str, Any] | None = None,
        write_content: WriteCallback,
        bridge: Any | None = None,
        jitter_max_min: int = 0,
        logger: Callable[[str], None] = print,
        getnote_runner: Any | None = None,
        channels: dict[str, Any] | None = None,
    ) -> None:
        self.queue = queue
        self.min_body_len = min_body_len
        self.quota: dict[str, Any] = quota or {}
        self.write_content = write_content
        self.bridge = bridge
        self.logger = logger
        self.jitter_max_min = max(0, int(jitter_max_min or 0))
        # 显式注入 channels（如测试注入带 runner 的通道）优先；否则由 build_channels 构建。
        if channels is not None:
            self.channels = channels
        else:
            self.channels = self._build_channels(bridge, getnote_runner=getnote_runner)

    def _build_channels(self, bridge: Any, *, getnote_runner: Any = None) -> dict[str, Any]:
        return channels_pkg.build_channels(bridge, getnote_runner=getnote_runner)

    def _quota_active_sources(self) -> list[str]:
        """有配额（per_cycle>0）且在配置里的平台，按固定顺序返回。"""
        return [s for s, c in self.quota.items() if int(getattr(c, "per_cycle", 0) or 0) > 0]

    def run_cycle(
        self,
        *,
        sources: tuple[str, ...] | None = None,
        quota_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """跑一轮，返回 {source: {...counts}}。桥接探测只影响 AgentLimb 通道。"""
        # 探测 AgentLimb 桥接：关断则仅跳过依赖它的通道，不阻塞 ytdlp/getnote。
        self._agentlimb_ok = self._probe_agentlimb()

        summary: dict[str, Any] = {}
        active = list(sources) if sources else self._quota_active_sources()
        if not active:
            for src in self._empty_sources(sources):
                summary[src] = dict(_EMPTY_COUNTS)
            return summary

        jitter = self._jitter()
        if jitter:
            self.logger(f"[refill] 首段随机憩志 {jitter}s 防风控。")
            self._sleep(jitter)

        effective_quota = self.quota if not quota_override else {**self.quota, **quota_override}
        picked_by_source = self.queue.pending_items(sources=tuple(active))
        for source_type in active:
            cfg = effective_quota.get(source_type)
            per_cycle = max(0, int(getattr(cfg, "per_cycle", 0) or 0))
            items = (picked_by_source.get(source_type) or [])[:per_cycle] if per_cycle else []
            summary[source_type] = self._process_items(source_type, items)
        return summary

    def _probe_agentlimb(self) -> bool:
        probe = getattr(self.bridge, "probe", None)
        if probe is None or not callable(probe):
            return False
        try:
            available = bool(probe())
        except Exception:  # noqa: BLE001
            available = False
        if not available:
            self.logger("[refill] AgentLimb 桥接不可用：direct / search_click 本轮跳过（不计数）。")
        return available

    def _empty_sources(self, sources: tuple[str, ...] | None) -> list[str]:
        if sources:
            return list(sources)
        return self._quota_active_sources()

    def _process_items(self, source_type: str, items: list[RefillItem]) -> dict[str, int]:
        counts = dict(_EMPTY_COUNTS, picked=len(items))
        for item in items:
            try:
                carrier = self._fetch_one(item)
            except BridgeUnavailableError as exc:
                # 基础设施故障：不计数、跳过该条继续（其余条目别的通道可能可用）。
                counts["bridge_off"] += 1
                self.logger(f"[refill] infra id={item.get('id')}: {exc}")
                continue
            if carrier == "success":
                counts["done"] += 1
            elif carrier == "dropped":
                counts["dropped"] += 1
            elif carrier == "skipped":
                counts["skipped"] += 1
            elif carrier == "bridge_off":
                counts["bridge_off"] += 1
            else:
                counts["failed"] += 1
        return counts

    def _fetch_one(self, item: RefillItem) -> str:
        """对单条队项执行 路由 → 抓 → 写。

        返回 success / pending / dropped / skipped / bridge_off。
        """
        row_id = int(item["id"])
        source_type = item["source_type"]
        url = item.get("url") or ""
        title = item.get("title") or ""
        max_attempts = max(1, int(item.get("max_attempts", 3)))

        any_attempted = False
        skipped_bridge = False
        for channel_name in route_channels(source_type, url, title):
            channel = self.channels.get(channel_name)
            if channel is None or not channel.supports(source_type, url):
                continue
            # AgentLimb 关断时跳过依赖它的通道，不阻塞 ytdlp/getnote。
            if getattr(channel, "requires_bridge", False) and not self._agentlimb_ok:
                skipped_bridge = True
                continue
            any_attempted = True
            try:
                ok, body, detail = channel.fetch(item)
            except BridgeUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001
                return self._record_failure(row_id, channel_name, max_attempts, f"{type(exc).__name__}: {exc}")
            if ok and body and len(body) >= self.min_body_len:
                self._commit_success(item, channel_name, body)
                self.logger(
                    f"[refill] OK   {source_type} id={row_id} @{channel_name} len={len(body)} title={title[:24]!r}"
                )
                return "success"
            if str(detail or "").startswith(PERMANENT):
                self.queue.mark_skipped(row_id, channel_name, last_error=detail)
                self.logger(f"[refill] SKIP {source_type} id={row_id} @{channel_name} {detail}")
                return "skipped"
            # 抓到但未达标 或 明确失败 → 计数重试（本条通道失效，转入下一候选通道）。
            outcome = self.queue.mark_attempt(row_id, channel_name, max_attempts, last_error=detail or "正文过短")
            kind = "MISS" if outcome == "pending" else "DROP"
            self.logger(f"[refill] {kind} {source_type} id={row_id} @{channel_name} {detail}")
            if outcome == "dropped":
                return "dropped"
        if not any_attempted and skipped_bridge:
            return "bridge_off"
        return "pending"

    def _commit_success(self, item: RefillItem, channel_name: str, body: str) -> None:
        """写正文（content_text）并把队项标 done。"""
        self.write_content(item, body)
        self.queue.mark_done(int(item["id"]), channel_name, len(body))

    def _record_failure(self, row_id: int, channel_name: str, max_attempts: int, last_error: str) -> str:
        outcome = self.queue.mark_attempt(row_id, channel_name, max_attempts, last_error=last_error)
        self.logger(f"[refill] ERR  id={row_id} @{channel_name} {last_error}")
        return "dropped" if outcome == "dropped" else "pending"

    def _jitter(self) -> int:
        return random.randint(0, max(0, self.jitter_max_min * 60))

    def _sleep(self, seconds: int) -> None:
        import time

        time.sleep(seconds)