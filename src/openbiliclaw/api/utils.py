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
import logging
import time
from typing import Any, cast
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


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
            tags = (
                [str(t).strip() for t in parsed if str(t).strip()]
                if isinstance(parsed, list)
                else []
            )
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


# ── 阅读库兴趣契合度（轻量打分，零 LLM 延迟）───────────────────────
_interest_keywords_cache: dict[str, Any] = {"at": 0.0, "keywords": []}
_INTEREST_KEYWORDS_TTL_SECONDS = 120.0


def _soul_profile_candidates() -> list[str]:
    from pathlib import Path

    here = Path(__file__).resolve()
    roots = [here.parents[3], Path.cwd()]
    out: list[str] = []
    for root in roots:
        p = root / "data" / "memory" / "soul_profile.json"
        if p.exists():
            out.append(str(p))
    return out


def load_interest_keywords() -> list[tuple[str, float]]:
    """Return ``[(interest_name, weight), ...]`` from the soul profile.

    Cached for a short TTL so the list endpoint stays fast; a missing or
    unparsable profile degrades to an empty list (fit_score = 0 for all).
    """
    now = time.monotonic()
    if _interest_keywords_cache["at"] and (
        now - _interest_keywords_cache["at"] < _INTEREST_KEYWORDS_TTL_SECONDS
    ):
        return cast("list[tuple[str, float]]", _interest_keywords_cache["keywords"])
    keywords: list[tuple[str, float]] = []
    for path in _soul_profile_candidates():
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            likes = (data.get("interest") or {}).get("likes") or []
            for item in likes:
                if not isinstance(item, dict):
                    continue
                domain = str(item.get("domain") or "").strip()
                d_weight = float(item.get("weight") or 0.5)
                if domain:
                    keywords.append((domain, max(0.05, d_weight)))
                for spec in item.get("specifics") or []:
                    if not isinstance(spec, dict):
                        continue
                    name = str(spec.get("name") or "").strip()
                    s_weight = float(spec.get("weight") or 0.3)
                    if name:
                        keywords.append((name, max(0.05, s_weight)))
        except Exception:
            logger.debug("Failed to load soul profile keywords from %s", path, exc_info=True)
    # 去重：同一名称保留最大权重
    merged: dict[str, float] = {}
    for name, weight in keywords:
        merged[name] = max(merged.get(name, 0.0), weight)
    keywords = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
    _interest_keywords_cache["at"] = now
    _interest_keywords_cache["keywords"] = keywords
    return keywords


def article_fit_score(text: str) -> float:
    """Weighted substring match of the interest profile against article text.

    Returns 0.0-1.0. Sums weights of matched interest names, capped so a
    handful of strong hits saturate at 1.0.
    """
    if not text:
        return 0.0
    keywords = load_interest_keywords()
    if not keywords:
        return 0.0
    haystack = text.lower()
    total = 0.0
    matched: list[str] = []
    for name, weight in keywords:
        if name and name.lower() in haystack:
            total += weight
            if len(matched) < 3:
                matched.append(name)
    if total <= 0:
        return 0.0
    return round(min(1.0, total / 1.5), 3)


# ── 阅读库意图搜索（自然语言 → 结构化检索）────────────────────────
READING_SOURCE_SYNONYMS: dict[str, str] = {
    "b站": "bilibili",
    "哔哩哔哩": "bilibili",
    "bilibili": "bilibili",
    "知乎": "zhihu",
    "zhihu": "zhihu",
    "小红书": "xiaohongshu",
    "红书": "xiaohongshu",
    "xhs": "xiaohongshu",
    "xiaohongshu": "xiaohongshu",
    "youtube": "youtube",
    "油管": "youtube",
    "v2ex": "v2ex",
    "小宇宙": "xiaoyuzhou",
    "播客": "xiaoyuzhou",
    "podcast": "xiaoyuzhou",
    "xiaoyuzhou": "xiaoyuzhou",
    "抖音": "douyin",
    "douyin": "douyin",
    "微信": "wechat",
    "公众号": "wechat",
    "wechat": "wechat",
    "rss": "rss",
    "订阅": "rss",
    "getnote": "getnote",
    "便签": "getnote",
    "reddit": "reddit",
    "豆瓣": "douban",
    "douban": "douban",
    "豆瓣feed": "douban_feed",
    "豆瓣评论": "douban_feed",
    "豆瓣文章": "douban_feed",
    "豆瓣小组": "douban_feed",
    "已读库": "read-archive",
}
READING_STATUS_SYNONYMS: dict[str, str] = {
    "未读": "unread",
    "没读": "unread",
    "没看过": "unread",
    "unread": "unread",
    "在读": "reading",
    "正在读": "reading",
    "看了一半": "reading",
    "reading": "reading",
    "读完": "finished",
    "已读": "finished",
    "看过": "finished",
    "读过": "finished",
    "finished": "finished",
    "归档": "archived",
    "archived": "archived",
}
READING_VALID_STATUSES = frozenset({"unread", "reading", "finished", "archived"})
READING_VALID_SOURCE_TYPES = frozenset(READING_SOURCE_SYNONYMS.values())
READING_STOPCHARS = set("的了呢吗啊我你帮找想要看读些点最近有没有推荐一些")


def rule_parse_reading_intent(q: str) -> dict[str, Any]:
    """纯规则解析阅读库查询（LLM 回退路径）。

    从自然语言里剥离来源 / 阅读状态 / 「不要 X」排除，剩余碎片作为
    关键词。中文无空格分词，长串整体保留交给 FTS trigram 兜底子串匹配。
    """
    import re as _re

    text = (q or "").strip()
    source_type = ""
    status = ""
    exclude: list[str] = []

    lower = text.lower()
    for word, canon in READING_SOURCE_SYNONYMS.items():
        if word in lower:
            source_type = canon
            break
    for word, canon in READING_STATUS_SYNONYMS.items():
        if word in lower:
            status = canon
            break

    # 「不要X」「不带X」「排除X」抽排除词（2-10 字）。
    for m in _re.finditer(r"(?:不要|别|不带|排除|去掉)[的]?([\u4e00-\u9fff\w]{2,10})", text):
        token = m.group(1).strip()
        if token and token not in exclude:
            exclude.append(token)

    cleaned = text
    for word in list(READING_SOURCE_SYNONYMS) + list(READING_STATUS_SYNONYMS):
        cleaned = _re.sub(_re.escape(word), " ", cleaned, flags=_re.IGNORECASE)
    cleaned = _re.sub(r"(?:不要|别|不带|排除|去掉)[的]?[\u4e00-\u9fff\w]{2,10}", " ", cleaned)

    keywords: list[str] = []
    for raw in _re.split(r"[,，、;；\s]+", cleaned):
        token = raw.strip()
        if not token:
            continue
        # 整段只由口语填充字组成（如「想看」「最近的」残片）→ 丢，避免 LIKE 噪声。
        if all(ch in READING_STOPCHARS for ch in token):
            continue
        if token not in keywords:
            keywords.append(token)

    return {
        "keywords": keywords,
        "exclude": exclude,
        "source_type": source_type,
        "status": status,
        "llm_used": False,
    }


def apply_reading_exclusions(
    items: list[dict[str, Any]], exclude: list[str]
) -> list[dict[str, Any]]:
    """按排除词过滤文章（大小写不敏感地扫标题/摘要/标签/作者）。"""
    terms = [t.lower() for t in exclude if t.strip()]
    if not terms:
        return items
    kept: list[dict[str, Any]] = []
    for item in items:
        haystack = " ".join(
            [
                str(item.get("title") or ""),
                str(item.get("summary") or ""),
                str(item.get("tags") or ""),
                str(item.get("author") or ""),
            ]
        ).lower()
        if any(term in haystack for term in terms):
            continue
        kept.append(item)
    return kept
