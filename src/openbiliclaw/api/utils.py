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


# 引导初始化的平台顺序（与 app.py 中 _INIT_SOURCE_ORDER 保持一致）
INIT_SOURCE_ORDER: tuple[str, ...] = (
    "bilibili",
    "xiaohongshu",
    "douyin",
    "youtube",
    "twitter",
    "zhihu",
)


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


def select_init_platforms(
    enabled: set[str],
    selected: set[str] | None,
    *,
    source_order: tuple[str, ...] = INIT_SOURCE_ORDER,
) -> set[str]:
    """Effective platform sources for a guided-init run.

    ``enabled`` is the config-enabled set; ``selected`` is the extension's
    per-run checkbox choice (``None`` when no selection was sent — CLI / legacy
    clients — meaning "use everything enabled"). A sent selection is an
    explicit local opt-in for those sources, not just a filter over old config.
    Bilibili flows through here like every other source (v0.3.118+): legacy
    clients keep their config-enabled behaviour, but deselecting it skips the
    B站 fetch.
    """
    if selected is None:
        return {
            normalized
            for source in enabled
            if (normalized := normalize_init_source_key(source)) in source_order
        }
    return {
        normalized
        for source in selected
        if (normalized := normalize_init_source_key(source)) in source_order
    }


def event_row_id(row: dict[str, Any]) -> int | None:
    """Extract a positive integer event id from a row dict, else None."""
    try:
        event_id = int(row.get("id", 0) or 0)
    except (TypeError, ValueError):
        return None
    return event_id if event_id > 0 else None


def event_row_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Extract and normalize the metadata dict from an event row."""
    metadata = row.get("metadata", {})
    if isinstance(metadata, str):
        try:
            parsed = json.loads(metadata) if metadata else {}
        except Exception:
            parsed = {}
        metadata = parsed
    return metadata if isinstance(metadata, dict) else {}


def coerce_e2e_event_rows(
    rows: object,
    *,
    after_event_id: int = 0,
) -> list[dict[str, Any]]:
    """Coerce raw event rows into sorted dicts with fresh event ids."""
    if not isinstance(rows, list | tuple):
        return []
    coerced: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict):
            item = dict(row)
        else:
            try:
                item = dict(row)
            except Exception:
                continue
        event_id = event_row_id(item)
        if event_id is not None and event_id <= after_event_id:
            continue
        coerced.append(item)
    return sorted(coerced, key=lambda item: event_row_id(item) or 0)
