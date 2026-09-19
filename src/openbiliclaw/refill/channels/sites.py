"""站点识别与防假命中守卫（镜像 ``16_浏览器自动化/web_capture.py``）。

- ``sniff(url)``：按域名后缀识别 (source_type, source_name)。
- ``FAKE_SIGNATURES``：token 失效/未登录重定向到站点首页时，标题命中签名 →
  判定失败、不写库。空列表 = 该源不设守卫。
"""

from __future__ import annotations

from urllib.parse import urlparse

# 域名后缀 → (source_type, source_name)。默认 ("web", host)。
_SUFFIX: dict[str, tuple[str, str]] = {
    "linux.do": ("linuxdo", "Linux.do"),
    "zhihu.com": ("zhihu", "知乎"),
    "xiaohongshu.com": ("xiaohongshu", "小红书"),
    "xhslink.com": ("xiaohongshu", "小红书"),
    "douyin.com": ("douyin", "抖音"),
    "youtube.com": ("youtube", "YouTube"),
    "youtu.be": ("youtube", "YouTube"),
    "bilibili.com": ("bilibili", "B站"),
    "b23.tv": ("bilibili", "B站"),
    "v2ex.com": ("v2ex", "V2EX"),
    "douban.com": ("douban", "豆瓣"),
    "mp.weixin.qq.com": ("wechat", "微信公众号"),
    "weread.qq.com": ("wechat", "微信读书"),
    "xiaoyuzhoufm.com": ("xiaoyuzhou", "小宇宙"),
    "reddit.com": ("reddit", "Reddit"),
    "getpocket.com": ("web", "Pocket"),
}

# token 失效/未登录被重定向到站点首页，标题命中签名即判失败（镜像 web_capture）。
FAKE_SIGNATURES: dict[str, list[str]] = {
    "xiaohongshu": ["小红书 - 你的生活兴趣社区", "小红书 - 送你一个年度好物集合"],
    "douyin": ["分享视频"],
    "zhihu": [],
    "linux.do": [],
}


def sniff(url: str) -> tuple[str, str]:
    host = (urlparse(url or "").netloc or "").lower()
    for suffix, pair in _SUFFIX.items():
        if host == suffix or host.endswith("." + suffix):
            return pair
    return ("web", host or "Web")


def fake_signature_hit(source_type: str, title: str) -> bool:
    """标题命中站点签名 → 判定为失效/重定向噪音页。"""
    title = title or ""
    for bad in FAKE_SIGNATURES.get(source_type, []):
        if bad and bad in title:
            return True
    return False