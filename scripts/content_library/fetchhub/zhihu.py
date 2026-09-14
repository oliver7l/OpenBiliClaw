"""知乎通道：zhihu CLI（已登录）为主通道。

注意：CLI 调用必须剥离 PYTHONHOME/PYTHONPATH（WorkBuddy 注入的 sitecustomize shim
会让 CLI 在 import 阶段误判 PermissionError 崩溃）。API 直连通道留待需要时再加。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from .core import ChannelError, UnifiedDoc, bj_ts, now_bj

ZHIHU_BIN = os.environ.get("OBC_ZHIHU_BIN", "/Users/imac/.local/bin/zhihu")


def _kind(url: str):
    if "/answer/" in url:
        return "answer"
    if "zhuanlan.zhihu.com" in url or re.search(r"/p/\d+", url):
        return "article"
    if re.search(r"/question/\d+", url):
        return "question"
    return None


def _clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONHOME", "PYTHONPATH")}


def cli_fetch(url: str) -> UnifiedDoc:
    kind = _kind(url)
    if not kind:
        raise ChannelError("parse", f"无法识别知乎 URL 类型（answer/article/question）: {url}", "zhihu-cli")
    if not Path(ZHIHU_BIN).exists():
        raise ChannelError("network", f"zhihu CLI 不存在: {ZHIHU_BIN}", "zhihu-cli")
    try:
        proc = subprocess.run(
            [ZHIHU_BIN, "browse", kind, url, "--json"],
            capture_output=True,
            text=True,
            env=_clean_env(),
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise ChannelError("network", "zhihu CLI 超时（120s）", "zhihu-cli") from None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:200]
        raise ChannelError("parse", f"zhihu CLI 退出码 {proc.returncode}: {err}", "zhihu-cli")
    try:
        d = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ChannelError("parse", f"zhihu CLI 输出非 JSON: {e}; head={proc.stdout[:150]!r}", "zhihu-cli") from e
    meta = d.get("metadata") or {}
    content = (d.get("content_md") or "").strip()
    if not content:
        raise ChannelError("parse", "zhihu CLI 无正文", "zhihu-cli")
    author = (
        meta.get("author")
        or meta.get("author_name")
        or ((meta.get("author_info") or {}).get("name") if isinstance(meta.get("author_info"), dict) else None)
    )
    created = meta.get("created")
    published = created if isinstance(created, str) and created[:1].isdigit() else bj_ts(created)
    extra = {k: meta[k] for k in ("vote", "comment", "favorite", "id", "question_title") if meta.get(k) is not None}
    return UnifiedDoc(
        url=url,
        platform="zhihu",
        title=(meta.get("title") or meta.get("question_title") or "").strip() or "(无标题)",
        author=author,
        published_at=published,
        content_md=content,
        replies=[],
        images=[],
        fetched_via="zhihu-cli",
        fetched_at=now_bj(),
        confidence="full" if published else "partial",
        extra=extra,
    )
