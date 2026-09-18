"""Scrapling 通道：TLS 指纹伪装 HTTP（Fetcher）+ Cloudflare 隐身突破（StealthyFetcher）。

定位：降级链中 AgentLimb 之前的通用兜底。v2ex 有专用解析（结构化主题+回复），
其余平台走 generic 解析（标题+正文文本）。

依赖：scrapling[fetchers]（.venv 内）+ 浏览器依赖（scrapling install，本机已装）。
环境变量：
    OBC_SCRAPE_PROXY   外网站（v2ex 等）代理，默认 http://127.0.0.1:7890
    OBC_SCRAPE_NOPROXY 逗号分隔的免代理域名关键词（默认国内站直连）
"""
from __future__ import annotations

import os
import re

from .core import ChannelError, Reply, UnifiedDoc, bj_ts, now_bj

_SCRAPE_PROXY = os.environ.get("OBC_SCRAPE_PROXY", "http://127.0.0.1:7890").rstrip("/")
_DIRECT = {"http": "", "https": ""}

_CHALLENGE_RE = re.compile(
    r"just a moment|请稍候|attention required|cf-browser-verification|cf-challenge", re.I
)


def _proxies_for(url: str) -> dict:
    """国内主流站直连；其余走 OBC_SCRAPE_PROXY（空串显式覆盖环境变量代理）。"""
    u = (url or "").lower()
    for kw in ("zhihu.com", "xiaohongshu.com", "weibo.com", "bilibili.com", "b23.tv", "qq.com", "163.com", "douban.com"):
        if kw in u:
            return _DIRECT
    return {"http": _SCRAPE_PROXY, "https": _SCRAPE_PROXY}


def _is_challenge(status: int, page) -> bool:
    if status in (403, 503, 429):
        return True
    title = (page.css("title::text").get() or "") if page else ""
    return bool(_CHALLENGE_RE.search(title or ""))


def _fetcher_get(url: str, timeout_ms: int = 30000):
    """轻量首选：curl_cffi 模拟 Chrome TLS 指纹。返回 Response 或抛 ChannelError(network)。"""
    try:
        from scrapling.fetchers import Fetcher

        return Fetcher.get(url, stealthy_headers=True, timeout=timeout_ms // 1000,
                           proxies=_proxies_for(url))
    except Exception as e:
        raise ChannelError("network", f"Fetcher 失败: {str(e)[:150]}", "scrapling") from e


def _stealth_fetch(url: str, timeout_ms: int = 120000):
    """重型兜底：隐身浏览器 + 自动过 Cloudflare。返回 Response 或抛 ChannelError(challenge/network)。"""
    try:
        from scrapling.fetchers import StealthyFetcher

        proxy = _proxies_for(url).get("https") or None
        return StealthyFetcher.fetch(
            url,
            headless=True,
            solve_cloudflare=True,
            proxy=proxy,
            timeout=timeout_ms,
        )
    except Exception as e:
        raise ChannelError("challenge", f"隐身突破失败: {str(e)[:150]}", "scrapling") from e


def _challenge_or_stealth(url: str, page, platform: str, timeout_ms: int = 120000):
    """Fetcher 结果若是 CF 挑战，升级 StealthyFetcher 再试一次。"""
    if _is_challenge(page.status, page):
        return _stealth_fetch(url, timeout_ms)
    return page


def _page_to_doc(url: str, platform: str, page, *, confidence: str = "full",
                 note: str = "") -> UnifiedDoc:
    title = (page.css("title::text").get() or "").strip()
    if not title or "mp.weixin.qq.com" in url:
        og_nodes = page.css('meta[property="og:title"]')
        og = og_nodes[0].attrib.get("content", "").strip() if og_nodes else ""
        title = og or title
    # 正文：优先语义容器，退回 body 全文本（get_all_text 含子孙节点文本）
    body_nodes = (page.css("article") or page.css("main")
                  or page.css("#js_content") or page.css("body"))
    text = re.sub(r"\n{3,}", "\n\n", body_nodes[0].get_all_text()) if body_nodes else ""
    if not text:
        raise ChannelError("parse", "页面无可提取正文", "scrapling")
    return UnifiedDoc(
        url=url,
        platform=platform,
        title=title or url,
        author=None,
        published_at=None,
        content_md=text[:20000],
        replies=[],
        images=[],
        fetched_via="scrapling",
        fetched_at=now_bj(),
        confidence=confidence,
        extra={"note": note or "Scrapling 通道（TLS 指纹伪装/隐身突破）"},
    )


def _v2ex_doc(url: str, page) -> UnifiedDoc:
    """v2ex 主题页结构化解析：标题/作者/节点/正文/回复。"""
    tid_m = re.search(r"/t/(\d+)", url)
    title = (page.css(".header h1::text").get()
             or page.css("title::text").get() or "").strip()
    author = (page.css(".header a[href^='/member/']::text").get()
              or (page.css("a[href^='/member/']::text").getall() or [None])[0])
    topic_nodes = page.css("div.topic_content")
    if not topic_nodes:
        raise ChannelError("parse", "未找到主题正文（div.topic_content）", "scrapling-v2ex")
    body = re.sub(r"\n{3,}", "\n\n", topic_nodes[0].get_all_text().strip())
    if not body:
        raise ChannelError("parse", "主题正文为空", "scrapling-v2ex")

    node = (page.css(".header a[href^='/go/']::text").get() or "").strip()
    # 回复：每个 .cell[id^=r_] 一个回复；宽松解析，失败不致命
    replies = []
    for cell in page.css("#Main .box .cell[id^='r_']"):
        r_author = (cell.css("a[href^='/member/']::text").getall() or [None])[0]
        r_text = cell.css(".reply_content")
        if r_text:
            replies.append(Reply(r_author, None, re.sub(r"\n{3,}", "\n", r_text[0].get_all_text().strip())[:1000]))

    created_rel = (page.css(".header .fade::text").getall() or [])
    return UnifiedDoc(
        url=url,
        platform="v2ex",
        title=title or url,
        author=author,
        published_at=None,  # HTML 页无精确时间戳，宁缺毋假
        content_md=body[:20000],
        replies=replies[:200],
        images=[],
        fetched_via="v2ex-scrapling",
        fetched_at=now_bj(),
        confidence="full" if replies or True else "partial",
        extra={
            "node": node,
            "reply_count": len(replies),
            "note": "StealthyFetcher 过 CF 后页面级解析；无结构化时间戳",
        },
    )


def v2ex_scrapling_fetch(url: str) -> UnifiedDoc:
    """v2ex 专用：Fetcher 直试 → 挑战则隐身突破 → 结构化解析。"""
    page = _fetcher_get(url)
    page = _challenge_or_stealth(url, page, "v2ex")
    if page.status != 200:
        raise ChannelError("network", f"HTTP {page.status}", "v2ex-scrapling")
    doc = _v2ex_doc(url, page)
    doc.fetched_via = "v2ex-scrapling"
    doc.fetched_at = now_bj()
    return doc


def generic_scrapling_fetch(url: str, platform: str = "generic") -> UnifiedDoc:
    """通用：Fetcher（TLS 伪装）→ CF 挑战升级 StealthyFetcher → 文本抽取。"""
    page = _fetcher_get(url)
    page = _challenge_or_stealth(url, page, platform)
    if page.status >= 400:
        raise ChannelError("network", f"HTTP {page.status}", "scrapling")
    doc = _page_to_doc(url, platform, page)
    doc.fetched_via = "scrapling"
    doc.fetched_at = now_bj()
    return doc
