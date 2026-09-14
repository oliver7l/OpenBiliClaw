"""AgentLimb（真 Chrome 桥，127.0.0.1:7791）适配器——所有平台的最终兜底通道。

实战约束（来自 2026-09 归档实践）：
- CF 挑战页的 <h1>/title 也是域名，判定必须用「内容选择器存在性」；
- 首次挑战需人工在 AgentLimb 浏览器点一次，cookie 种下后后续页面自动放行；
- DOM 抽取拿不到精确发帖时间等元数据 → published_at=None + confidence=partial，不瞎编。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

from .core import ChannelError, Reply, UnifiedDoc, now_bj

BRIDGE = os.environ.get("OBC_AGENTLIMB_BRIDGE", "http://127.0.0.1:7791").rstrip("/")
NAV_TIMEOUT_MS = int(os.environ.get("OBC_AGENTLIMB_NAV_TIMEOUT_MS", "25000"))


def _post(payload: dict, timeout: int = 40) -> dict:
    req = urllib.request.Request(
        BRIDGE + "/api/mvp/browser/call",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _get_call(call_id: str, timeout: int = 40) -> dict:
    with urllib.request.urlopen(f"{BRIDGE}/api/mvp/browser/calls/{call_id}", timeout=timeout) as r:
        return json.load(r)


def _find_value(obj):
    """在桥的返回结构里递归找 result.value（结构版本间可能变动，防御式查找）。"""
    if isinstance(obj, dict):
        v = obj.get("value")
        if isinstance(v, str):
            return v
        for x in obj.values():
            found = _find_value(x)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for x in obj:
            found = _find_value(x)
            if found is not None:
                return found
    return None


def js_eval(expression: str, *, poll_rounds: int = 6, poll_interval: float = 1.5) -> str:
    """在桥控浏览器里执行 JS（须返回 JSON.stringify 字符串），轮询取结果。"""
    try:
        r = _post({"tool": "javascript_eval", "params": {"expression": expression}})
    except Exception as e:
        raise ChannelError("network", f"AgentLimb js_eval 请求失败: {e}", "agentlimb") from e
    cid = (r.get("call") or {}).get("id")
    if not cid:
        raise ChannelError("network", f"AgentLimb 无 call id: {json.dumps(r, ensure_ascii=False)[:200]}", "agentlimb")
    for _ in range(poll_rounds):
        time.sleep(poll_interval)
        try:
            res = _get_call(cid)
        except Exception as e:
            raise ChannelError("network", f"AgentLimb 取结果失败: {e}", "agentlimb") from e
        val = _find_value(res)
        if val is not None:
            return val
    raise ChannelError("network", "AgentLimb js_eval 轮询超时无结果", "agentlimb")


def navigate(url: str) -> None:
    try:
        _post({"tool": "navigate", "params": {"url": url, "waitForLoad": True, "timeout": NAV_TIMEOUT_MS}})
    except Exception as e:
        raise ChannelError("network", f"AgentLimb navigate 失败: {e}", "agentlimb") from e


def _probe_selector(selector: str) -> dict:
    expr = (
        "(function(){return JSON.stringify({ok: !!document.querySelector("
        + json.dumps(selector)
        + "), title: document.title});})()"
    )
    val = js_eval(expr, poll_rounds=4, poll_interval=1.0)
    try:
        return json.loads(val) if val else {}
    except json.JSONDecodeError:
        return {}


def wait_content(selector: str, *, rounds: int = 5, first_wait: int = 10, interval: int = 10) -> None:
    """导航后等待内容选择器出现；全程未出现 → 按挑战页处理。"""
    time.sleep(first_wait)
    for _ in range(rounds):
        try:
            if _probe_selector(selector).get("ok"):
                return
        except ChannelError:
            pass
        time.sleep(interval)
    raise ChannelError(
        "challenge",
        f"等待内容选择器 {selector} 超时（疑似 CF 挑战页）——"
        f"请在 AgentLimb 浏览器中手动打开目标页过一次挑战后重试（cookie 种下后可自动放行数天）",
        "agentlimb",
    )


# ---- 平台抽取 JS -----------------------------------------------------------

V2EX_TOPIC_JS = """(function(){
  var q = function(s){ return document.querySelector(s); };
  var replies = Array.prototype.map.call(
    document.querySelectorAll('#Main .cell[id^="r_"]'),
    function(c){
      var dark = c.querySelector('.dark');
      var ago = c.querySelector('.ago');
      var txt = c.querySelector('.reply_content');
      return { author: dark ? dark.innerText : '',
               time: ago ? (ago.title || ago.innerText) : '',
               text: txt ? txt.innerText : '' };
    });
  var h1 = q('h1');
  var node = q('.header a.node');
  var smallA = q('.header small a');
  var content = q('.topic_content');
  return JSON.stringify({
    title: h1 ? h1.innerText : (document.title || ''),
    node: node ? node.innerText : '',
    author: smallA ? smallA.innerText : '',
    content: content ? content.innerText : '',
    replyCount: replies.length,
    replies: replies
  });
})()"""

PAGE_TEXT_JS = """(function(){
  return JSON.stringify({
    title: document.title || '',
    text: document.body ? document.body.innerText.slice(0, 20000) : ''
  });
})()"""

_CHALLENGE_RE = re.compile(r"just a moment|请稍候|请验证|attention required", re.I)


# ---- 通道实现 ---------------------------------------------------------------

def v2ex_fetch(url: str) -> UnifiedDoc:
    """v2ex 兜底：真浏览器开页 + DOM 抽取。拿不到发帖时间 → partial。"""
    from . import v2ex as v2ex_mod

    tid = v2ex_mod.parse_topic_id(url)
    canonical = f"https://www.v2ex.com/t/{tid}"
    navigate(canonical)
    wait_content(".topic_content")
    val = js_eval(V2EX_TOPIC_JS)
    try:
        d = json.loads(val) if val else {}
    except json.JSONDecodeError as e:
        raise ChannelError("parse", f"v2ex DOM 抽取结果解析失败: {e}", "agentlimb-v2ex") from e
    title = (d.get("title") or "").strip()
    content = (d.get("content") or "").strip()
    if not content:
        raise ChannelError("parse", "v2ex DOM 抽取到空正文", "agentlimb-v2ex")
    replies = [
        Reply((r.get("author") or "").strip() or None, (r.get("time") or "").strip() or None, (r.get("text") or "").strip())
        for r in d.get("replies") or []
    ]
    return UnifiedDoc(
        url=url,
        platform="v2ex",
        title=title,
        author=(d.get("author") or "").strip() or None,
        published_at=None,
        content_md=content,
        replies=replies,
        images=[],
        fetched_via="agentlimb-v2ex",
        fetched_at=now_bj(),
        confidence="partial",
        extra={"node": (d.get("node") or "").strip(), "reply_count": d.get("replyCount"), "note": "发布时间与节点名可能缺失（DOM 抓取限制）"},
    )


def text_fetch(url: str) -> UnifiedDoc:
    """通用兜底：真浏览器打开页面抽正文文本。限流/能力所限，恒为 partial。"""
    navigate(url)
    time.sleep(10)
    val = js_eval(PAGE_TEXT_JS)
    try:
        d = json.loads(val) if val else {}
    except json.JSONDecodeError as e:
        raise ChannelError("parse", f"页面文本抽取解析失败: {e}", "agentlimb-text") from e
    title = (d.get("title") or "").strip()
    text = (d.get("text") or "").strip()
    if _CHALLENGE_RE.search(title) or not text:
        raise ChannelError(
            "challenge",
            f"页面疑似挑战页或空内容（title={title[:60]!r}）——请在 AgentLimb 浏览器手动过一次挑战后重试",
            "agentlimb-text",
        )
    return UnifiedDoc(
        url=url,
        platform="generic",
        title=title,
        author=None,
        published_at=None,
        content_md=text,
        replies=[],
        images=[],
        fetched_via="agentlimb-text",
        fetched_at=now_bj(),
        confidence="partial",
        extra={"note": "AgentLimb 文本兜底：无结构化元数据"},
    )
