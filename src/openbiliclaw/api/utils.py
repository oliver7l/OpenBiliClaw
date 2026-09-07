"""API 层通用工具函数。

从 app.py 逐步提取的纯工具函数。每个函数必须满足：
1. 无副作用（不修改全局状态）
2. 只依赖参数和标准库
3. 不依赖 app.py 的闭包变量

提取流程（谨慎原则）：
1. 确认函数是纯函数（无副作用、无闭包依赖）
2. 复制到本文件
3. 在 app.py 顶部导入（调用方式不变）
4. 删除 app.py 中的原定义
5. 运行完整测试验证
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse


def normalize_source_platform(source: object) -> str:
    """Normalize a source/platform key to the canonical platform name."""
    source_key = str(source or "").strip().lower()
    if source_key in {"x", "twitter"}:
        return "twitter"
    if source_key in {"xhs", "rednote"}:
        return "xiaohongshu"
    if source_key in {"yt", "youtube"}:
        return "youtube"
    if source_key in {"douyin", "tiktok"}:
        return "douyin"
    if source_key in {"zhihu", "知乎"}:
        return "zhihu"
    if source_key in {"bilibili", "bili", ""}:
        return "bilibili"
    return source_key


def normalize_init_source_key(source: object) -> str:
    """Normalize an init source key, empty when missing."""
    source_key = str(source or "").strip().lower()
    if not source_key:
        return ""
    return normalize_source_platform(source_key)


def article_tags_for_context(value: object, *, max_tags: int = 8) -> str:
    """Render an article's tags JSON as a short comma-joined context suffix.

    E2 (reading feedback loop): article tags were persisted in event
    metadata but never reached the preference-analyzer LLM prompt (only
    title/url/source are compacted). Folding the top-N tags into the
    natural-language context gives the analyzer topic-level evidence for
    both positive (finished) and negative (hidden) reading signals.
    """
    if isinstance(value, list):
        tags = [str(t).strip() for t in value if str(t).strip()]
    elif isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                tags = [str(t).strip() for t in parsed if str(t).strip()]
            else:
                tags = []
        except json.JSONDecodeError:
            tags = [t.strip() for t in value.replace("，", ",").split(",") if t.strip()]
    else:
        tags = []
    if not tags:
        return ""
    return "标签:" + ",".join(tags[:max_tags])


def infer_source_platform_from_url(url: object) -> str:
    """Infer the source platform from a URL."""
    text = str(url or "").strip().lower()
    if "youtube.com" in text or "youtu.be" in text:
        return "youtube"
    host = (urlparse(text if "://" in text else f"https://{text}").hostname or "").lower()
    if host in {"x.com", "twitter.com"} or host.endswith(".x.com") or host.endswith(".twitter.com"):
        return "twitter"
    if "xiaohongshu.com" in text or "xhslink.com" in text:
        return "xiaohongshu"
    if "douyin.com" in text:
        return "douyin"
    if "zhihu.com" in text:
        return "zhihu"
    if "bilibili.com" in text or "b23.tv" in text:
        return "bilibili"
    return ""
