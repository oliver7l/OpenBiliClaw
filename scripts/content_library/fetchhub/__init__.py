"""Fetch Hub：统一数据获取通道（多平台声明式降级链，AgentLimb 为最终兜底）。"""
from .core import (
    CHANNEL_CHAINS,
    AllChannelsFailed,
    ChannelError,
    Reply,
    UnifiedDoc,
    detect_platform,
    fetch,
    health,
)

__all__ = [
    "AllChannelsFailed",
    "CHANNEL_CHAINS",
    "ChannelError",
    "Reply",
    "UnifiedDoc",
    "detect_platform",
    "fetch",
    "health",
]
