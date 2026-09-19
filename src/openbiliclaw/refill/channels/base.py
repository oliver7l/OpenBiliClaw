"""Channel 协议、抓取结果与平台→通道路由。

协议与设计稿 §5 保持一致：channel ``fetch(item) -> (ok, body, detail)``；
成功与否由 Scheduler 依据 ``min_body_len`` 判定，channel 本身只负责「抓」，
不决定「选谁 / 是否入账」。基础设施（桥接/浏览器未就绪）统一抛
``BridgeUnavailableError`` —— Scheduler 捕获后不消耗 attempts、整轮跳过，
对齐既有「基础设施故障不计数」经验（见 docs/refill-module-dev-guide.md §4.3）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from openbiliclaw.refill.queue import RefillItem


class BridgeUnavailableError(RuntimeError):
    """AgentLimb 桥接 / 登录态浏览器不可用（基础设施故障，不计入 attempts）。"""


# ``fetch`` 返回 detail 的前缀约定：
#  PERMANENT → 永久无正文（真不可抓，如 YouTube 无字幕），标 skipped，不消耗重试。
PERMANENT = "PERM:"


@runtime_checkable
class Channel(Protocol):
    """抓取通道协议：按 ``supports`` 承接平台/URL，``fetch`` 产出正文。"""

    name: str
    # 是否依赖 AgentLimb 桥接（登录态 Chrome）。AgentLimb 关断时仅跳过这些通道，
    # 不阻塞 ytdlp / getnote 等独立通道。
    requires_bridge: bool = False

    def supports(self, source_type: str, url: str) -> bool: ...

    def fetch(self, item: RefillItem) -> tuple[bool, str, str]:
        """抓正文。返回 ``(ok, body, detail)``。

        - ``ok=True`` 且 ``body`` 达阈值 → Scheduler 写 content_text、队项标 done；
        - ``ok=False`` 且 detail 以 ``PERMANENT`` 开头 → Scheduler 标 skipped（永久不可抓）；
        - ``ok=False`` 其他 → Scheduler 累加 attempts（NOT_FOUND / 真死链 / 假命中拦截）；
        - 基础设施故障 → 抛 ``BridgeUnavailableError``，不计数、跳过本轮。
        """


@dataclass(frozen=True)
class Route:
    """平台 → 候选通道顺序。"""

    source_type: str
    channels: tuple[str, ...]


# 平台 → 通道路由优先级（M2：search_click 仅小红书无 token 且有标题；
# M3 为 youtube/douyin/wechat/xiaoyuzhou 接入 ytdlp / getnote；M4 接入 bili_cli / zhihu_api；
# M4+ YouTube 接入 yt_bridge——登录态 Chrome 无 POT 问题，放 ytdlp 之前
# （ytdlp 被 bot 判定抛 BridgeUnavailableError 会中断整条候选链））。
_DEFAULT_ROUTES: dict[str, tuple[str, ...]] = {
    "xiaohongshu": ("search_click", "direct", "getnote"),
    "youtube": ("yt_bridge", "ytdlp", "getnote", "direct"),
    "douyin": ("getnote", "direct"),
    "wechat": ("direct", "getnote"),
    "xiaoyuzhou": ("direct", "getnote"),
    "bilibili": ("bili_cli", "getnote", "direct"),
    "zhihu": ("zhihu_api", "getnote", "direct"),
    "default": ("direct",),
}


def route_channels(source_type: str, url: str, title: str) -> tuple[str, ...]:
    """返回某队项应命中的候选通道顺序。

    小红书：``url`` 无 ``xsec_token`` 且 ``title`` 非空 → 搜索点击优先
    （标题做关键词）；否则（带 token 或标题空）→ 直接访问。其余平台 → direct。
    """
    if source_type == "xiaohongshu":
        has_token = "xsec_token" in (url or "")
        has_title = bool((title or "").strip())
        if not has_token and has_title:
            return ("search_click", "direct", "getnote")
        return ("direct", "getnote")
    return _DEFAULT_ROUTES.get(source_type, _DEFAULT_ROUTES["default"])


def is_retryable_dead(attempts: int, max_attempts: int) -> bool:
    return attempts >= max_attempts


_XSEC_TOKEN_RE = re.compile(r"[?&]xsec_token=")


def has_xsec_token(url: str) -> bool:
    """小红书笔记 URL 是否携带新鲜 ``xsec_token``。

    M2 通道路由与既有补抓脚本一致：带 token 走直接访问，裸链走搜索点击。
    """
    return bool(_XSEC_TOKEN_RE.search(url or ""))
