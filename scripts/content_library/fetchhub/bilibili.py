"""B站通道：bili CLI（已登录）为主通道；番剧 ep 链接先经 PGC 接口换 bvid。"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request

from .core import ChannelError, UnifiedDoc, bj_ts, now_bj

BILI_BIN = os.environ.get("OBC_BILI_BIN") or shutil.which("bili") or "/Users/imac/.local/bin/bili"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"


def _get_json(req_url: str, timeout: int = 25):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(req_url, headers={"User-Agent": UA})
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def resolve_pgc(url: str) -> str:
    """番剧 ep 链接（/ep<id>）→ 普通 video 链接（bili CLI 不认 ep）。"""
    m = re.search(r"/ep(\d+)", url or "")
    if not m:
        return url
    ep_id = m.group(1)
    try:
        data = _get_json(f"https://api.bilibili.com/pgc/view/web/season?ep_id={ep_id}")
    except Exception as e:
        raise ChannelError("network", f"PGC 接口请求失败: {e}", "bili-cli") from e
    episodes = ((data.get("result") or {}).get("episodes")) or []
    for ep in episodes:
        if str(ep.get("id")) == ep_id and ep.get("bvid"):
            return f"https://www.bilibili.com/video/{ep['bvid']}"
    raise ChannelError("parse", f"PGC 接口未找到 ep{ep_id} 对应分集", "bili-cli")


def _deep_first(obj, key, depth=0):
    """在未知结构的 JSON 里递归找第一个非空标量值。"""
    if depth > 6:
        return None
    if isinstance(obj, dict):
        v = obj.get(key)
        if isinstance(v, (str, int, float)) and v != "":
            return v
        for x in obj.values():
            r = _deep_first(x, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for x in obj:
            r = _deep_first(x, key, depth + 1)
            if r is not None:
                return r
    return None


def cli_fetch(url: str) -> UnifiedDoc:
    if not shutil.which(BILI_BIN) and not os.path.exists(BILI_BIN):
        raise ChannelError("network", f"bili CLI 不存在: {BILI_BIN}", "bili-cli")
    video_url = resolve_pgc(url)
    try:
        proc = subprocess.run(
            [BILI_BIN, "video", video_url, "--json"],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except subprocess.TimeoutExpired:
        raise ChannelError("network", "bili CLI 超时（90s）", "bili-cli") from None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        raise ChannelError("parse", f"bili CLI 退出码 {proc.returncode}: {err}", "bili-cli")
    try:
        d = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ChannelError("parse", f"bili CLI 输出非 JSON: {e}", "bili-cli") from e
    title = _deep_first(d, "title")
    desc = _deep_first(d, "desc") or _deep_first(d, "description")
    if not title and not desc:
        raise ChannelError("parse", f"bili CLI 输出中未找到 title/desc: {json.dumps(d, ensure_ascii=False)[:200]}", "bili-cli")
    owner = _deep_first(d, "owner") or _deep_first(d, "author") or _deep_first(d, "name")
    pubts = _deep_first(d, "pubdate") or _deep_first(d, "pubtime") or _deep_first(d, "created")
    return UnifiedDoc(
        url=url,
        platform="bilibili",
        title=str(title or "(无标题)"),
        author=str(owner) if owner else None,
        published_at=bj_ts(pubts),
        content_md=str(desc or "").strip(),
        replies=[],
        images=[],
        fetched_via="bili-cli",
        fetched_at=now_bj(),
        confidence="full" if (title and desc) else "partial",
        extra={"resolved_url": video_url, "note": "字幕/AI总结需 bili video 附加参数另行抓取"},
    )
