"""通用页面通道：直连抓取 + 极简 HTML 转文本。CF/登录墙/动态渲染交给链尾 AgentLimb。"""
from __future__ import annotations

import html as html_mod
import re
import urllib.error
import urllib.request

from .core import ChannelError, UnifiedDoc, now_bj

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_CHALLENGE_RE = re.compile(r"just a moment|请稍候|请验证|attention required|cf-browser-verification", re.I)


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?i)</(p|div|li|h[1-6]|tr)>", "\n", raw)
    raw = re.sub(r"<[^>]+>", " ", raw)
    raw = html_mod.unescape(raw)
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r"\n\s*\n\s*", "\n\n", raw)
    return raw.strip()


def webfetch(url: str) -> UnifiedDoc:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"})
    try:
        with opener.open(req, timeout=25) as r:
            final_url = r.geturl()
            ctype = (r.headers.get("Content-Type") or "").lower()
            data = r.read(500_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        kind = "challenge" if e.code in (403, 503) else "network"
        raise ChannelError(kind, f"HTTP {e.code}", "generic-webfetch") from e
    except Exception as e:
        raise ChannelError("network", f"请求失败: {str(e)[:200]}", "generic-webfetch") from e

    title = ""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", data)
    if m:
        title = html_mod.unescape(m.group(1)).strip()
    if _CHALLENGE_RE.search(title) or _CHALLENGE_RE.search(data[:600]):
        raise ChannelError("challenge", f"疑似 CF 挑战页（title={title[:60]!r}）——交给 AgentLimb 兜底", "generic-webfetch")

    text = html_to_text(data) if ("html" in ctype or "<html" in data[:400].lower()) else data.strip()  # json/纯文本等
    if not text:
        raise ChannelError("parse", "页面内容为空", "generic-webfetch")
    return UnifiedDoc(
        url=url,
        platform="generic",
        title=title or final_url,
        author=None,
        published_at=None,
        content_md=text[:15000],
        replies=[],
        images=[],
        fetched_via="generic-webfetch",
        fetched_at=now_bj(),
        confidence="partial",
        extra={"resolved_url": final_url, "note": "直连文本抽取：无结构化元数据，渲染型页面请走 AgentLimb"},
    )


import urllib.error  # noqa: E402 （webfetch 函数体内引用；集中放顶部也可）
