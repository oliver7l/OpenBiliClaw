"""refill channels 通道注册表与工厂。

M2 交付 ``direct``（登录态 Chrome 直接访问）与 ``search_click``（小红书标题搜索
点击），M3 新增 ``ytdlp``（YouTube 字幕/简介）与 ``getnote``（得到大脑服务端兜底）。

``direct`` / ``search_click`` 依赖 AgentLimb 桥接（``requires_bridge=True``），
``ytdlp`` / ``getnote`` 用本机子进程，独立于桥接。通道只抓不选，路由与入账由
scheduler 负责。
"""

from __future__ import annotations

from openbiliclaw.refill.channels.base import (
    PERMANENT,
    BridgeUnavailableError,
    Channel,
)
from openbiliclaw.refill.channels.bili_cli import BiliCliChannel
from openbiliclaw.refill.channels.bridge import AgentLimbBridge
from openbiliclaw.refill.channels.direct import DirectChannel
from openbiliclaw.refill.channels.getnote import GetnoteChannel, Runner
from openbiliclaw.refill.channels.search_click import SearchClickChannel
from openbiliclaw.refill.channels.yt_bridge import YtBridgeChannel
from openbiliclaw.refill.channels.ytdlp import YtdlpChannel
from openbiliclaw.refill.channels.zhihu_api import ZhihuApiChannel

__all__ = [
    "AgentLimbBridge",
    "BridgeUnavailableError",
    "PERMANENT",
    "Channel",
    "DirectChannel",
    "SearchClickChannel",
    "YtdlpChannel",
    "YtBridgeChannel",
    "GetnoteChannel",
    "BiliCliChannel",
    "ZhihuApiChannel",
    "Runner",
    "build_channels",
]


def build_channels(
    bridge: AgentLimbBridge | None = None,
    *,
    getnote_runner: Runner | None = None,
) -> dict[str, Channel]:
    """构造通道注册表 ``name -> Channel``。

    提供 bridge 时含 AgentLimb 通道（direct / search_click）；ytdlp / getnote
    恒注册（本机子进程，不依赖桥接）。``bridge=None`` 仅用于纯 youtube/getnote
    调度（无需登录态 Chrome）。``getnote_runner`` 供测试注入 getnote 子进程。
    """
    channels: dict[str, Channel] = {}
    if bridge is not None:
        channels["direct"] = DirectChannel(bridge)
        channels["search_click"] = SearchClickChannel(bridge)
        channels["yt_bridge"] = YtBridgeChannel(bridge)
    channels["ytdlp"] = YtdlpChannel()
    channels["getnote"] = GetnoteChannel(runner=getnote_runner) if getnote_runner else GetnoteChannel()
    channels["bili_cli"] = BiliCliChannel()
    channels["zhihu_api"] = ZhihuApiChannel()
    return channels
