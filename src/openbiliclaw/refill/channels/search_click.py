"""search_click 通道：小红书标题搜索 + CDP 点击抓正文。

复用 ``二创/xhs_refill/agentlimb_xhs_batch.py`` 内核（标题搜索 → ``note-item``
真实点击 → 读 ``noteDetailMap``）。仅承接「小红书、无 xsec_token、有标题」的裸链
（带 token 或标题空交给 direct 通道）。

语义对齐旧脚本：``NOT_FOUND``（搜不到 / 404）属真缺内容 → ``ok=False`` 计数重试；
桥接异常 → 抛 :class:`BridgeUnavailableError` 不计。按题搜索需要标题做关键词，
``item.title`` 为空时本通道不承接。
"""

from __future__ import annotations

import re

from openbiliclaw.refill.channels.base import BridgeUnavailableError
from openbiliclaw.refill.channels.bridge import AgentLimbBridge

_NAME = "search_click"
_EXPLORE_RE = re.compile(r"/explore/([0-9a-zA-Z]+)")


class SearchClickChannel:
    """小红书标题搜索点击通道。"""

    name = _NAME
    requires_bridge = True

    def __init__(self, bridge: AgentLimbBridge) -> None:
        self.bridge = bridge

    def supports(self, source_type: str, url: str) -> bool:
        # 仅小红书且无 xsec_token（需要标题做关键词，token 提供者交 direct）。
        if source_type != "xiaohongshu" or "xsec_token" in (url or ""):
            return False
        return _EXPLORE_RE.search(url or "") is not None

    def fetch(self, item) -> tuple[bool, str, str]:
        url = (item.get("url") or "").strip()
        title = (item.get("title") or "").strip()
        if not title:
            return False, "", "缺标题，无法按关键词搜索"
        match = _EXPLORE_RE.search(url)
        if not match:
            return False, "", "URL 非 /explore/ 形态"
        nid = match.group(1)
        status, body = self.bridge.xhs_search_click(nid, title)
        if status == "OK":
            return True, body, f"title={title[:24]!r}"
        if status == "NOT_FOUND":
            return False, "", "搜索无结果/404"
        raise BridgeUnavailableError(f"xhs 搜索点击执行失败({status})")