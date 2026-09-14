"""v2ex 通道：MindBack 内部路由 → 官方 API（走代理）→（链尾）AgentLimb。"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .core import ChannelError, Reply, UnifiedDoc, bj_ts, now_bj

MINDBACK = os.environ.get("OBC_MINDBACK_URL", "http://localhost:13001").rstrip("/")
PROXY = os.environ.get("OBC_V2EX_PROXY", "http://127.0.0.1:7890")
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def parse_topic_id(url: str) -> str:
    m = re.search(r"/t/(\d+)", url or "")
    if not m:
        raise ChannelError("parse", f"无法从 URL 提取 v2ex 主题 id: {url}", "v2ex")
    return m.group(1)


def _get_json(req_url: str, *, proxy: bool = False, timeout: int = 25):
    """直连时显式清空代理（避免环境变量代理干扰 localhost）；走代理时强制指定。"""
    proxy_cfg = {"http": PROXY, "https": PROXY} if proxy else {}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxy_cfg))
    req = urllib.request.Request(req_url, headers={"User-Agent": UA, "Accept": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def mindback_fetch(url: str) -> UnifiedDoc:
    """主通道：本机 MindBack（13001）内部路由，自带缓存/代理通道。"""
    tid = parse_topic_id(url)
    try:
        t = _get_json(f"{MINDBACK}/api/v2ex/topics/show?id={tid}")
    except Exception as e:
        raise ChannelError("network", f"MindBack 不可达: {e}", "v2ex-mindback") from e
    if not (isinstance(t, dict) and t.get("success") and t.get("data")):
        raise ChannelError("parse", f"MindBack 返回异常: {json.dumps(t, ensure_ascii=False)[:200]}", "v2ex-mindback")
    topic = t["data"][0]
    replies = []
    try:
        r = _get_json(f"{MINDBACK}/api/v2ex/replies/show?topic_id={tid}")
        if isinstance(r, dict) and r.get("success"):
            replies = r.get("data") or []
    except Exception:
        pass  # 回复拉取失败不致命，主题是主体
    return _build_doc(url, topic, replies, "v2ex-mindback")


def api_proxy_fetch(url: str) -> UnifiedDoc:
    """备用通道：走代理直连 v2ex 官方 API。403/503 视为 CF 挑战。"""
    tid = parse_topic_id(url)
    try:
        data = _get_json(f"https://www.v2ex.com/api/topics/show.json?id={tid}", proxy=True)
    except urllib.error.HTTPError as e:
        kind = "challenge" if e.code in (403, 503) else "network"
        raise ChannelError(kind, f"官方 API HTTP {e.code}", "v2ex-api-proxy") from e
    except Exception as e:
        raise ChannelError("network", f"官方 API 请求失败: {str(e)[:200]}", "v2ex-api-proxy") from e
    if not isinstance(data, list) or not data:
        raise ChannelError("parse", "官方 API 返回空/非列表", "v2ex-api-proxy")
    topic = data[0]
    replies = []
    try:
        rr = _get_json(f"https://www.v2ex.com/api/replies/show.json?topic_id={tid}", proxy=True)
        if isinstance(rr, list):
            replies = rr
    except Exception:
        pass
    return _build_doc(url, topic, replies, "v2ex-api-proxy")


def _build_doc(url: str, topic: dict, replies: list, via: str) -> UnifiedDoc:
    member = topic.get("member") or {}
    node = topic.get("node") or {}
    body = (topic.get("content") or "").strip() or (topic.get("content_rendered") or "").strip()
    if not body:
        raise ChannelError("parse", "主题正文为空", via)
    doc = UnifiedDoc(
        url=url,
        platform="v2ex",
        title=(topic.get("title") or "").strip(),
        author=member.get("username"),
        published_at=bj_ts(topic.get("created")),
        content_md=body,
        replies=[
            Reply(
                ((rp.get("member") or {}).get("username")),
                bj_ts(rp.get("created")),
                (rp.get("content") or "").strip(),
            )
            for rp in replies
        ],
        images=[],
        fetched_via=via,
        fetched_at=now_bj(),
        confidence="full",
        extra={
            "node": node.get("title") or "",
            "reply_count": topic.get("replies"),
            "location": member.get("location") or "",
        },
    )
    return doc
