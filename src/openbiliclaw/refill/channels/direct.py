"""direct 通道：登录态 Chrome 直接访问抓正文。

复用 ``16_浏览器自动化/web_capture.py`` 内核（导航 + 抽取 + 防假命中守卫）。
对本机可直连、已登录（或公开）的任意平台有效；小红书无 token 裸链由
search_click 通道优先处理（标题可检索），否则也走本通道。
"""

from __future__ import annotations

from openbiliclaw.refill.channels.bridge import AgentLimbBridge
from openbiliclaw.refill.channels.sites import fake_signature_hit, sniff

_NAME = "direct"
_SUPPORTED: tuple[str, ...] = (
    "linuxdo", "zhihu", "xiaohongshu", "douyin", "youtube", "bilibili",
    "v2ex", "douban", "wechat", "xiaoyuzhou", "reddit", "web",
)


class DirectChannel:
    """直接访问通道。

    ``fetch`` 导航目标 URL 抽取正文；标题命中站点签名（失效重定向）→ 返回
    ``(False, "", "假命中拦截")``（真缺内容，可计数重试）；桥接不可用 → 抛
    :class:`BridgeUnavailableError`。
    """

    name = _NAME
    requires_bridge = True

    def __init__(self, bridge: AgentLimbBridge) -> None:
        self.bridge = bridge

    def supports(self, source_type: str, url: str) -> bool:
        return source_type in _SUPPORTED

    def fetch(self, item) -> tuple[bool, str, str]:
        url = (item.get("url") or "").strip()
        if not url:
            return False, "", "空 URL"
        data = self.bridge.grab(url)
        title = (data.get("title") or "").strip()
        source_type = item.get("source_type") or ""
        # 防假命中：失效 token 重定向回首页，标题命中站点签名 → 拒收（可计数）。
        if fake_signature_hit(source_type, title):
            return False, "", f"假命中拦截(标题为失效/重定向页)"
        # 规范 title 尾部平台后缀（镜像 web_capture）。
        _, source_name = sniff(url)
        if title.endswith(f" - {source_name}"):
            title = title[: -len(f" - {source_name}")].rstrip()
        body = (data.get("text") or "").strip()
        return bool(body), body, f"title={title[:40]!r}"