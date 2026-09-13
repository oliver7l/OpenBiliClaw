"""FastAPI app for the browser-extension backend."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path as _Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from openbiliclaw.api.models import (
    ArticleNoteIn,
    BehaviorEventBatchIn,
    BilibiliCookieIn,
    BilibiliCookieResponse,
    ChatTurnListResponse,
    ChatTurnOut,
    CognitionUpdateSummary,
    EventIngestResponse,
    EventRejectedOut,
    ExtensionE2EAction,
    ExtensionE2EActionReportOut,
    ExtensionE2EActionStatus,
    ExtensionE2EEventMatchOut,
    ExtensionE2EPlatform,
    ExtensionE2EPlatformReportOut,
    ExtensionE2EResultIn,
    ExtensionE2ERunIn,
    ExtensionE2ERunOut,
    ExtensionE2ERunStatus,
    HealthResponse,
    InitPrerequisitesOut,
    InitStageOut,
    InitStatusOut,
    PendingDelightOut,
    PendingDelightResponse,
    ProfileEditIn,
    ProfileSummaryResponse,
    RecommendationClickIn,
    RecommendationClickResponse,
    RecommendationOut,
)
from openbiliclaw.diary import DiaryService
from openbiliclaw.runtime.feedback_scheduler import FeedbackBatchScheduler
from openbiliclaw.runtime.image_cache import (
    cleanup_image_cache,
)

# Project root: src/openbiliclaw/api/app.py → ../../..
_PROJECT_ROOT = _Path(__file__).resolve().parent.parent.parent.parent

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


logger = logging.getLogger(__name__)
_CONFIG_SAVE_LOCK = asyncio.Lock()
_fire_and_forget_tasks: set[asyncio.Task[None]] = set()

# /api/health embedding readiness: cache the live-probe result for this many
# seconds so Docker healthchecks and popup re-polls don't hit the embedding
# provider on every call. Kept short so a freshly-fixed provider (e.g. right
# after `ollama pull bge-m3`) clears the popup's "semantic dedup off" banner
# quickly. The probe itself is capped by a separate timeout so a hung/retrying
# provider can never stall /api/health. The timeout is generous enough to
# absorb an Ollama cold model-load (bge-m3 unloads after keep_alive idle; the
# first embed re-loads it — measured ~3s), and a timeout is treated as
# "loading, optimistically ready", NOT a hard failure — otherwise the banner
# would flash on every popup-open-after-idle. A genuinely-missing model 404s
# *fast*, so it still resolves to not-ready well within the cap.
_EMBEDDING_READY_TTL_SECONDS = 30.0
# Strict readiness (gui-init): a failure/timeout caches briefly so a service
# that finished a cold model load greens within seconds; the probe timeout is
# generous enough for a cold Ollama load but still fails (does not optimistically
# pass) if the embedding service never answers.
_EMBEDDING_FAIL_TTL_SECONDS = 8.0
_EMBEDDING_PROBE_TIMEOUT_SECONDS = 15.0
_LAN_IP_TTL_SECONDS = 30.0
_FEEDBACK_BATCH_DEBOUNCE_SECONDS = 5.0
_PROFILE_UPDATE_BACKFILL_LIMIT = 200
_PROFILE_UPDATE_BACKFILL_EVENT_TYPES = [
    "view",
    "search",
    "favorite",
    "like",
    "coin",
    "comment",
    "feedback",
    "article_finished",
]

# ── 阅读库兴趣契合度（轻量打分，零 LLM 延迟）───────────────────────
# 读取 soul_profile.json 的兴趣标签（interest.likes），对文章
# title/summary/tags/正文前缀做子串加权匹配，返回 0-1 的 fit_score。
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


def _load_interest_keywords() -> list[tuple[str, float]]:
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


def _article_fit_score(text: str) -> float:
    """Weighted substring match of the interest profile against article text.

    Returns 0.0-1.0. Sums weights of matched interest names, capped so a
    handful of strong hits saturate at 1.0.
    """
    if not text:
        return 0.0
    keywords = _load_interest_keywords()
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
# 主路径是 LLM 分词 / 推断；下面是 LLM 不可用或失败时的纯规则回退，
# 以及两种路径共用的排除过滤。词表与 /api/articles/facets 的真实
# source_type 分布对齐。
_READING_SOURCE_SYNONYMS: dict[str, str] = {
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
_READING_STATUS_SYNONYMS: dict[str, str] = {
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
_READING_VALID_STATUSES = frozenset({"unread", "reading", "finished", "archived"})
# 与 /api/articles/facets 的真实来源分布对齐；LLM 只允许取这些值。
_READING_VALID_SOURCE_TYPES = frozenset(_READING_SOURCE_SYNONYMS.values())
# 口语填充字：规则回退里从关键词中剔除（LLM 路径自带去停用词能力）。
_READING_STOPCHARS = set("的了呢吗啊我你帮找想要看读些点最近有没有推荐一些")


def _rule_parse_reading_intent(q: str) -> dict[str, Any]:
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
    for word, canon in _READING_SOURCE_SYNONYMS.items():
        if word in lower:
            source_type = canon
            break
    for word, canon in _READING_STATUS_SYNONYMS.items():
        if word in lower:
            status = canon
            break

    # 「不要X」「不带X」「排除X」抽排除词（2-10 字）。
    for m in _re.finditer(r"(?:不要|别|不带|排除|去掉)[的]?([\u4e00-\u9fff\w]{2,10})", text):
        token = m.group(1).strip()
        if token and token not in exclude:
            exclude.append(token)

    cleaned = text
    for word in list(_READING_SOURCE_SYNONYMS) + list(_READING_STATUS_SYNONYMS):
        cleaned = _re.sub(_re.escape(word), " ", cleaned, flags=_re.IGNORECASE)
    cleaned = _re.sub(r"(?:不要|别|不带|排除|去掉)[的]?[\u4e00-\u9fff\w]{2,10}", " ", cleaned)

    keywords: list[str] = []
    for raw in _re.split(r"[,，、;；\s]+", cleaned):
        token = raw.strip()
        if not token:
            continue
        # 整段只由口语填充字组成（如「想看」「最近的」残片）→ 丢，避免 LIKE 噪声。
        if all(ch in _READING_STOPCHARS for ch in token):
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


def _apply_reading_exclusions(
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


# Canonical home is openbiliclaw.sources.x_auth (mirrors douyin_auth);
# re-exported here because callers historically imported from api.app.
from openbiliclaw.sources.x_auth import (  # noqa: E402, F401
    XCookieManager,
    resolve_x_cookie,
)

SOURCE_LABELS = {
    "feedback": "推荐反馈",
    "chat": "聊天",
    "profile_refresh": "聚合观察",
}

_SOURCE_SHARE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu")
_INIT_SOURCE_ORDER = ("bilibili", "xiaohongshu", "douyin", "youtube", "twitter", "zhihu")
_PROBE_MODES = {"near", "lateral", "bridge", "wildcard"}
_PROBE_CHALLENGE_MODES = {"lateral", "bridge", "wildcard"}

_RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(net) for net in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
_BENCHMARK_NETWORK = ipaddress.ip_network("198.18.0.0/15")
# Cover-image fetch/whitelist constants live in openbiliclaw.runtime.image_cache
# (shared by the proxy route and the prefetch sweep). Only the disk-cache age cap
# is referenced directly from here, by the startup cleanup call.
_IMAGE_CACHE_MAX_AGE_DAYS = 30

_E2E_STATE_CHANGING_ACTIONS = frozenset({"like", "favorite", "follow", "repost", "bookmark"})
_E2E_DEFAULT_SAFE_ACTIONS: tuple[ExtensionE2EAction, ...] = (
    "snapshot",
    "scroll",
    "click",
    "share",
)
_E2E_ACTION_EVENT_TYPES: dict[ExtensionE2EAction, frozenset[str]] = {
    "snapshot": frozenset({"snapshot"}),
    "scroll": frozenset({"scroll"}),
    "click": frozenset({"click"}),
    "share": frozenset({"click"}),
    "like": frozenset({"like", "favorite"}),
    "favorite": frozenset({"favorite", "bookmark"}),
    "follow": frozenset({"follow"}),
    "repost": frozenset({"share", "repost"}),
    "bookmark": frozenset({"bookmark", "favorite"}),
}


@dataclass
class _ExtensionE2ERunState:
    run_id: str
    token: str
    started_at: float
    after_event_id: int
    expected_actions: dict[ExtensionE2EPlatform, list[ExtensionE2EAction]]
    event: asyncio.Event
    extension_result: ExtensionE2EResultIn | None = None
    error: str = ""


def _default_route_ip() -> str | None:
    """Return the IPv4 address selected for outbound traffic, if usable."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(0.1)
            sock.connect(("8.8.8.8", 80))
            ip = sock.getsockname()[0]
            return str(ip) if ip else None
    except Exception:
        return None


def _interface_ipv4_candidates() -> list[str]:
    """Best-effort local IPv4 enumeration without extra dependencies."""
    commands: list[list[str]] = (
        [["ipconfig"]]
        if os.name == "nt"
        else [["ifconfig"], ["ip", "-4", "addr", "show", "scope", "global"]]
    )

    candidates: list[str] = []
    seen: set[str] = set()
    for command in commands:
        try:
            if os.name == "nt":
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=2,
                    check=False,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            else:
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=2,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0:
            continue
        for ip in re.findall(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])", proc.stdout):
            if ip not in seen:
                candidates.append(ip)
                seen.add(ip)
        if candidates:
            break
    return candidates


def _is_rfc1918_ipv4(addr: ipaddress.IPv4Address) -> bool:
    return any(addr in network for network in _RFC1918_NETWORKS)


def _usable_lan_candidate(ip: str) -> tuple[bool, bool]:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return (False, False)
    if not isinstance(addr, ipaddress.IPv4Address):
        return (False, False)
    if (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr in _BENCHMARK_NETWORK
    ):
        return (False, False)
    return (True, _is_rfc1918_ipv4(addr))


def _detect_lan_ip() -> str | None:
    """Return a likely phone-reachable LAN IPv4 address.

    UDP default-route detection can return VPN/TUN addresses such as
    198.18.0.1 on macOS. Prefer RFC1918 interface addresses and only use
    the default-route result when it is not a benchmark / loopback address.
    """
    candidates = _interface_ipv4_candidates()
    route_ip = _default_route_ip()
    if route_ip:
        candidates.append(route_ip)

    fallback: str | None = None
    for candidate in candidates:
        usable, rfc1918 = _usable_lan_candidate(candidate)
        if not usable:
            continue
        if rfc1918:
            return candidate
        if fallback is None:
            fallback = candidate
    return fallback


_RESETTABLE_CONFIG_FIELDS = {
    "llm.openai.api_key": ("llm", "openai", "api_key"),
    "llm.claude.api_key": ("llm", "claude", "api_key"),
    "llm.gemini.api_key": ("llm", "gemini", "api_key"),
    "llm.deepseek.api_key": ("llm", "deepseek", "api_key"),
    "llm.openrouter.api_key": ("llm", "openrouter", "api_key"),
    "llm.openai_compatible.api_key": ("llm", "openai_compatible", "api_key"),
    "llm.embedding.api_key": ("llm", "embedding", "api_key"),
}


def _config_backup_path(config_path: Path) -> Path:
    return config_path.with_name(f"{config_path.name}.bak")


def _snapshot_config_file(config_path: Path) -> Path | None:
    if not config_path.exists():
        return None
    backup_path = _config_backup_path(config_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(config_path, backup_path)
    return backup_path


def _restore_config_snapshot(backup_path: Path, config_path: Path) -> None:
    shutil.copy2(backup_path, config_path)


def _validate_llm_buildable(cfg: Any, base_issues: list[Any]) -> list[Any]:
    from openbiliclaw.config import ConfigIssue
    from openbiliclaw.llm.registry import RegistryBuildError, build_llm_registry

    issues = list(base_issues)
    try:
        build_llm_registry(cfg)
    except RegistryBuildError as exc:
        issues.append(
            ConfigIssue(
                field="llm",
                message=f"LLM registry would fail to build: {exc}",
                severity="blocking",
            )
        )
    return issues


def _count_events_by_source_platform(database: Any) -> dict[str, int]:
    """Count stored behavior events by normalized source platform."""
    counter = {source: 0 for source in _SOURCE_SHARE_ORDER}
    if hasattr(database, "count_events_by_source_platform"):
        raw_counts = database.count_events_by_source_platform()
        if isinstance(raw_counts, dict):
            for source, count in raw_counts.items():
                source_key = _normalize_source_platform(source)
                counter[source_key] = counter.get(source_key, 0) + int(count)
            return {source: counter.get(source, 0) for source in _SOURCE_SHARE_ORDER}

    rows: list[dict[str, Any]] = []
    if hasattr(database, "conn"):
        try:
            cursor = database.conn.execute("SELECT metadata FROM events")
            rows = [dict(row) for row in cursor.fetchall()]
        except Exception:
            rows = []
    elif hasattr(database, "get_recent_events"):
        try:
            rows = list(database.get_recent_events(limit=10000))
        except Exception:
            rows = []

    for row in rows:
        metadata = row.get("metadata", {})
        if isinstance(metadata, str):
            try:
                import json as _json

                metadata = _json.loads(metadata) if metadata else {}
            except Exception:
                metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        source = metadata.get("source_platform", row.get("source_platform", "bilibili"))
        source_key = _normalize_source_platform(source)
        counter[source_key] = counter.get(source_key, 0) + 1
    return {source: counter.get(source, 0) for source in _SOURCE_SHARE_ORDER}


def _extension_e2e_actions_for_request(
    payload: ExtensionE2ERunIn,
) -> dict[ExtensionE2EPlatform, list[ExtensionE2EAction]]:
    actions_by_platform: dict[ExtensionE2EPlatform, list[ExtensionE2EAction]] = {}
    seen_platforms: set[ExtensionE2EPlatform] = set()
    for platform in payload.platforms:
        if platform in seen_platforms:
            continue
        seen_platforms.add(platform)
        requested_actions = (
            payload.actions[platform]
            if platform in payload.actions
            else list(_E2E_DEFAULT_SAFE_ACTIONS)
        )
        deduped: list[ExtensionE2EAction] = []
        seen_actions: set[ExtensionE2EAction] = set()
        for action in requested_actions:
            if action in seen_actions:
                continue
            seen_actions.add(action)
            deduped.append(action)
        actions_by_platform[platform] = deduped
    return actions_by_platform


def _latest_e2e_event_id(ctx: Any) -> int:
    database = getattr(ctx, "database", None)
    conn = getattr(database, "conn", None)
    if conn is not None:
        try:
            row = conn.execute(
                "SELECT COALESCE(MAX(id), 0) AS max_id FROM events.events"
            ).fetchone()
            if row is not None:
                try:
                    return int(row["max_id"])
                except Exception:
                    return int(row[0])
        except Exception:
            pass

    memory_manager = getattr(ctx, "memory_manager", None)
    query_events = getattr(memory_manager, "query_events", None)
    if callable(query_events):
        try:
            rows = _coerce_e2e_event_rows(query_events(limit=1))
        except Exception:
            rows = []
        if rows:
            return _event_row_id(rows[-1]) or 0
    return 0


def _query_e2e_events(ctx: Any, *, after_event_id: int, limit: int = 1000) -> list[dict[str, Any]]:
    memory_manager = getattr(ctx, "memory_manager", None)
    query_events = getattr(memory_manager, "query_events", None)
    if callable(query_events):
        try:
            return _coerce_e2e_event_rows(
                query_events(after_event_id=after_event_id, limit=limit),
                after_event_id=after_event_id,
            )
        except TypeError:
            try:
                return _coerce_e2e_event_rows(
                    query_events(limit=limit),
                    after_event_id=after_event_id,
                )
            except Exception:
                return []
        except Exception:
            return []

    database = getattr(ctx, "database", None)
    query_events = getattr(database, "query_events", None)
    if callable(query_events):
        try:
            return _coerce_e2e_event_rows(
                query_events(after_event_id=after_event_id, limit=limit),
                after_event_id=after_event_id,
            )
        except Exception:
            return []
    return []


def _match_e2e_event(
    events: list[dict[str, Any]],
    *,
    platform: ExtensionE2EPlatform,
    action: ExtensionE2EAction,
    used_event_ids: set[int],
) -> dict[str, object] | None:
    accepted_event_types = _E2E_ACTION_EVENT_TYPES.get(action, frozenset())
    if not accepted_event_types:
        return None

    for row in sorted(events, key=lambda item: _event_row_id(item) or 0):
        event_id = _event_row_id(row)
        if event_id is None or event_id in used_event_ids:
            continue
        event_type = str(row.get("event_type") or row.get("type") or "").strip()
        if event_type not in accepted_event_types:
            continue
        metadata = _event_row_metadata(row)
        source_platform = _normalize_source_platform(
            metadata.get("source_platform")
            or row.get("source_platform")
            or _infer_source_platform_from_url(row.get("url", ""))
        )
        if source_platform != platform:
            continue
        used_event_ids.add(event_id)
        return {
            "event_id": event_id,
            "event_type": event_type,
            "url": str(row.get("url", "") or ""),
            "title": str(row.get("title", "") or ""),
        }
    return None


def _build_extension_e2e_report(
    state: _ExtensionE2ERunState,
    events: list[dict[str, Any]],
    *,
    timed_out: bool,
    timeout_seconds: int,
) -> ExtensionE2ERunOut:
    result = state.extension_result
    action_results: dict[
        tuple[ExtensionE2EPlatform, ExtensionE2EAction], tuple[ExtensionE2EActionStatus, str]
    ] = {}
    platform_details: dict[ExtensionE2EPlatform, str] = {}
    if result is not None:
        for platform_result in result.platforms:
            platform_details[platform_result.platform] = platform_result.detail
            for action_result in platform_result.actions:
                action_results[(platform_result.platform, action_result.action)] = (
                    action_result.status,
                    action_result.detail,
                )

    used_event_ids: set[int] = set()
    reports: list[ExtensionE2EPlatformReportOut] = []
    total_actions = 0
    complete_actions = 0
    partial_actions = 0
    default_status: ExtensionE2EActionStatus = "skipped" if timed_out else "failed"
    default_detail = "extension result timed out" if timed_out else "extension result missing"

    for platform, actions in state.expected_actions.items():
        action_reports: list[ExtensionE2EActionReportOut] = []
        for action in actions:
            total_actions += 1
            action_status, detail = action_results.get(
                (platform, action),
                (default_status, default_detail),
            )
            match = _match_e2e_event(
                events,
                platform=platform,
                action=action,
                used_event_ids=used_event_ids,
            )
            backend_event = (
                ExtensionE2EEventMatchOut(
                    event_id=cast("int", match["event_id"]),
                    event_type=str(match["event_type"]),
                    url=str(match["url"]),
                    title=str(match["title"]),
                )
                if match is not None
                else None
            )
            extension_executed = action_status == "ok"
            backend_matched = backend_event is not None
            if extension_executed and backend_matched:
                complete_actions += 1
            elif extension_executed or backend_matched:
                partial_actions += 1
            action_reports.append(
                ExtensionE2EActionReportOut(
                    action=action,
                    extension_status=action_status,
                    extension_executed=extension_executed,
                    extension_detail=detail,
                    backend_event_matched=backend_matched,
                    backend_event=backend_event,
                )
            )
        reports.append(
            ExtensionE2EPlatformReportOut(
                platform=platform,
                actions=action_reports,
                detail=platform_details.get(platform, ""),
            )
        )

    error = state.error or (result.error if result is not None else "")
    if timed_out:
        run_status: ExtensionE2ERunStatus = "timeout"
        error = error or "extension e2e result timed out"
    elif error and complete_actions == 0 and partial_actions == 0:
        run_status = "failed"
    elif total_actions == complete_actions:
        run_status = "ok"
    elif complete_actions > 0 or partial_actions > 0:
        run_status = "partial"
    else:
        run_status = "failed"

    return ExtensionE2ERunOut(
        run_id=state.run_id,
        status=run_status,
        platforms=reports,
        error=error,
        timeout_seconds=timeout_seconds,
    )


def _fallback_recommendation_click_url(
    *,
    source_platform: str,
    content_id: str,
    bvid: str,
) -> str:
    """Build a canonical click URL when the recommendation row lacks one."""
    item_id = (content_id or bvid).strip()
    if not item_id:
        return ""
    if source_platform == "youtube":
        return f"https://www.youtube.com/watch?v={quote(item_id, safe='')}"
    if source_platform == "douyin":
        return f"https://www.douyin.com/video/{quote(item_id, safe='')}"
    if source_platform == "twitter":
        return f"https://x.com/i/status/{quote(item_id, safe='')}"
    if source_platform == "bilibili":
        return f"https://www.bilibili.com/video/{quote(bvid or item_id, safe='')}"
    if source_platform == "xiaohongshu":
        return f"https://www.xiaohongshu.com/explore/{quote(item_id, safe='')}"
    return ""


def _normalize_probe_mode_for_payload(value: object) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _PROBE_MODES else "near"


def _probe_metadata_for_payload(item: object) -> tuple[str, bool]:
    probe_mode = _normalize_probe_mode_for_payload(getattr(item, "probe_mode", ""))
    challenge = probe_mode in _PROBE_CHALLENGE_MODES
    with suppress(Exception):
        challenge = challenge or bool(getattr(item, "challenge", False))
    return probe_mode, challenge


def _cap_keeping_user_added(
    items: list[Any], added: list[str], limit: int, key: Any = None
) -> list[Any]:
    """Truncate a merged AI⊕override list for the summary view without ever
    dropping a user-added entry.

    The effective profile appends user edits after the AI-inferred items, so a
    plain ``items[:limit]`` slice silently hides anything the user added past
    the cap — it then shows in edit mode (un-truncated `edit-state`) but not in
    the read-only view, which reads like "my edit didn't take". User edits are
    intentional and few, so they ride past the cap; only AI-inferred items are
    subject to it. ``key`` extracts the comparable string (identity for plain
    string lists, ``lambda d: d.domain`` for interest domains).
    """
    keyfn = key if key is not None else (lambda x: str(x))
    items = list(items)
    if len(items) <= limit:
        return items
    added_keys = {str(a).strip().casefold() for a in added if str(a).strip()}
    if not added_keys:
        return items[:limit]
    head = items[:limit]
    seen = {str(keyfn(x)).strip().casefold() for x in head}
    extra = [
        x
        for x in items[limit:]
        if str(keyfn(x)).strip().casefold() in added_keys
        and str(keyfn(x)).strip().casefold() not in seen
    ]
    return head + extra


def _normalize_cognition_update(item: dict[str, object]) -> CognitionUpdateSummary:
    impact = str(item.get("impact", "")).strip()
    reasoning = str(item.get("reasoning", "")).strip()
    evidence = str(item.get("evidence", "")).strip()
    source = str(item.get("source", "")).strip()
    source_label = str(item.get("source_label", "")).strip() or SOURCE_LABELS.get(source, "")
    expand_hint = str(item.get("expand_hint", "")).strip()
    if expand_hint not in {"expandable", "summary_only"}:
        expand_hint = "expandable" if any((impact, reasoning, evidence)) else "summary_only"
    return CognitionUpdateSummary(
        summary=str(item.get("summary", "")).strip(),
        context_line=str(item.get("context_line", "")).strip() or "基于最近几条相关内容",
        impact=impact,
        reasoning=reasoning,
        evidence=evidence,
        source=source,
        source_label=source_label,
        expand_hint=expand_hint,
        created_at=str(item.get("created_at", "")).strip(),
    )


# ─── 日记统计接口两级缓存（内存 L1 + 磁盘 L2）────────────────
# 统计类接口读多写少、计算密集（如关键词分词、时间线聚合），加短 TTL 缓存；
# 任何写操作自动清空缓存，保证数据一致性。
# 使用统一缓存层 TwoLevelCache：内存 L1（微秒级）+ 磁盘 L2（diskcache，持久化，重启不失效）
# ─── API 通用工具函数（从本文件逐步提取的纯函数）────────────
from openbiliclaw.api.utils import (  # noqa: E402
    coerce_e2e_event_rows as _coerce_e2e_event_rows,
)
from openbiliclaw.api.utils import (  # noqa: E402
    event_row_id as _event_row_id,
)
from openbiliclaw.api.utils import (  # noqa: E402
    event_row_metadata as _event_row_metadata,
)
from openbiliclaw.api.utils import (  # noqa: E402
    infer_source_platform_from_url as _infer_source_platform_from_url,
)
from openbiliclaw.api.utils import (  # noqa: E402
    normalize_source_platform as _normalize_source_platform,
)
from openbiliclaw.api.utils import (  # noqa: E402
    select_init_platforms as _select_init_platforms,
)
from openbiliclaw.storage.cache import get_cache as _get_cache  # noqa: E402

_api_cache = _get_cache()
_DIARY_CACHE_TTL = 30.0
# 缓存命名空间，用于批量失效
_API_CACHE_NAMESPACE = "api_stats"
_DIARY_CACHEABLE_PATHS = {
    # 日记相关
    "/api/diary/stats",
    "/api/diary/tags",
    "/api/diary/persons",
    "/api/diary/extraction-stats",
    "/api/diary/memory/stats",
    "/api/diary/insights/keywords",
    "/api/diary/emotion/stats",
    "/api/diary/emotion/trend",
    "/api/diary/emotion/forecast",
    "/api/diary/emotion/burnout",
    "/api/diary/advanced-memory/overview",
    "/api/diary/advanced-memory/layers",
    "/api/diary/advanced-memory/beliefs",
    "/api/diary/advanced-memory/conflicts",
    "/api/diary/timeline/stats",
    "/api/diary/timeline/milestones",
    "/api/diary/knowledge-graph/stats",
    "/api/diary/knowledge-graph/mixed",
    # 内容池与推荐（大表查询慢，加缓存）
    "/api/pool/all",
    "/api/pool/stats",
    "/api/recommendations",
    "/api/saved",
    "/api/observability",
    "/api/home/feed",
    "/api/library",
}


def create_app(
    *,
    memory_manager: Any | None = None,
    database: Any | None = None,
    soul_engine: Any | None = None,
    dialogue: Any | None = None,
    runtime_controller: Any | None = None,
    recommendation_engine: Any | None = None,
    runtime_event_hub: Any | None = None,
    account_sync_service: Any | None = None,
    auto_update_service: Any | None = None,
) -> FastAPI:
    """Create the local backend API app."""
    # 每个 app 实例拥有独立的 API 统计缓存域：``_api_cache`` 是进程级单例，
    # 测试多次 create_app 时若不清理，同 path 的 GET 会命中前一个 app 的缓存
    # （响应被旧 app 的注入组件数据污染，见 test_recommendations_endpoint_*）。
    _api_cache.invalidate_namespace(_API_CACHE_NAMESPACE)
    from openbiliclaw.api._route_registry import register_all_routes
    from openbiliclaw.api._web_ui_routes import register_web_ui_routes
    from openbiliclaw.api.recommendation_routes import build_recommendation_router
    from openbiliclaw.api.runtime_context import (
        RuntimeContext,
        build_degraded_runtime_context,
        build_runtime_context,
    )
    from openbiliclaw.config import load_config
    from openbiliclaw.llm.registry import RegistryBuildError

    app = FastAPI(title="OpenBiliClaw API", default_response_class=JSONResponse)

    # GZip middleware: only compress responses ≥ 500 bytes.
    # ``minimum_size=0`` was previously used as a sledgehammer workaround
    # for an h11 Content-Length mismatch on CJK text in older starlette
    # versions, but the side-effect was that 204/empty responses were
    # also force-compressed (gzip header alone is ~20 bytes > original
    # body), tripping h11's strict size check on every poll. Modern
    # starlette already encodes JSON bodies as UTF-8 bytes for
    # Content-Length, so the original workaround is no longer needed.
    from starlette.middleware.gzip import GZipMiddleware

    app.add_middleware(GZipMiddleware, minimum_size=500)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Build RuntimeContext ────────────────────────────────────────
    config = load_config()

    # Mirror [network] into the process-level overseas-routing source of
    # truth (openbiliclaw.network). Domestic / local endpoints (DeepSeek /
    # SenseNova / 通义 / self-hosted) always bypass it and connect directly.
    from openbiliclaw.network import set_outbound_proxy

    network_config = getattr(config, "network", None)
    set_outbound_proxy(
        getattr(network_config, "proxy", "") or "",
        mode=getattr(network_config, "mode", "system") or "system",
    )

    # Auto-generate the session signing secret on first enable so login state
    # survives restarts (see docs/plans/2026-05-30-web-password-auth-design.md).
    from openbiliclaw.api.auth import (
        AuthGate,
        _auth_env_overrides,
        authorize_websocket,
        ensure_session_secret,
        make_auth_middleware,
        reconcile_password_fingerprint,
        register_auth_routes,
    )
    from openbiliclaw.config import ApiAuthConfig as _ApiAuthConfig

    # Injection-path test doubles may hand back a config without ``api.auth``;
    # fall back to a disabled gate so the password feature stays inert there.
    _auth_cfg = getattr(getattr(config, "api", None), "auth", None)
    if not isinstance(_auth_cfg, _ApiAuthConfig):
        _auth_cfg = _ApiAuthConfig()

    if ensure_session_secret(_auth_cfg):
        with suppress(Exception):
            from openbiliclaw.config import save_config

            save_config(config)

    if soul_engine is not None:
        # Injection path: caller provides swappable components.
        # Auto-create stable components (database, memory_manager) if missing.
        from openbiliclaw.runtime.events import RuntimeEventHub as _RuntimeEventHub

        _db = database
        _created_db = False
        if _db is None:
            from openbiliclaw.storage.database import Database

            _db = Database(config.data_path / "openbiliclaw.db")
            _db.initialize()
            _created_db = True
        _mm = memory_manager
        if _mm is None:
            from openbiliclaw.memory.manager import MemoryManager

            _mm = MemoryManager(config.data_path, database=_db if _created_db else None)
            _mm.initialize()

        ctx = RuntimeContext(
            database=_db,
            memory_manager=_mm,
            event_hub=runtime_event_hub
            or getattr(runtime_controller, "event_hub", None)
            or _RuntimeEventHub(),
            # config intentionally left None in injection path — matches
            # old behaviour where closures couldn't see config when all
            # core components were provided by the caller.
            soul_engine=soul_engine,
            dialogue=dialogue,
            runtime_controller=runtime_controller,
            recommendation_engine=recommendation_engine,
            account_sync_service=account_sync_service,
            auto_update_service=auto_update_service,
        )
        if ctx.dialogue is None:
            from openbiliclaw.soul.dialogue import SocraticDialogue

            ctx.dialogue = SocraticDialogue(llm=None, soul_engine=soul_engine, session="popup")
        if ctx.auto_update_service is None:
            from openbiliclaw.runtime.updater import AutoUpdateService

            ctx.auto_update_service = AutoUpdateService(
                enabled=False,
                event_publisher=getattr(ctx.event_hub, "publish", None),
            )
    else:
        # Production path: build everything from config.
        try:
            ctx = build_runtime_context(
                config,
                memory_manager=memory_manager,
                database=database,
                event_hub=runtime_event_hub,
            )
        except RegistryBuildError as exc:
            ctx = build_degraded_runtime_context(
                config,
                memory_manager=memory_manager,
                database=database,
                event_hub=runtime_event_hub,
                exc=exc,
            )
            logger.warning(
                "FastAPI started in degraded mode (%s): %s",
                ctx.degraded_reason,
                "; ".join(str(getattr(issue, "message", issue)) for issue in ctx.degraded_issues),
            )
    app.state.runtime_context = ctx
    app.state.degraded = bool(getattr(ctx, "degraded", False))
    app.state.degraded_reason = str(getattr(ctx, "degraded_reason", ""))
    app.state.degraded_issues = list(getattr(ctx, "degraded_issues", []))
    feedback_batch_scheduler = FeedbackBatchScheduler(
        getattr(ctx, "soul_engine", None),
        debounce_seconds=_FEEDBACK_BATCH_DEBOUNCE_SECONDS,
    )
    app.state.feedback_batch_scheduler = feedback_batch_scheduler

    # ── Password gate (LAN/remote auth) ─────────────────────────────
    app.state.auth_gate = AuthGate(_auth_cfg, getattr(ctx, "database", None))
    app.state.extension_e2e_runs = {}

    def _get_auth_gate() -> AuthGate:
        return cast("AuthGate", app.state.auth_gate)

    register_auth_routes(app, _get_auth_gate)

    @app.post("/api/auth/admin")
    async def auth_admin(request: Request) -> JSONResponse:
        """Local-only enable/disable + set/change of the password gate.

        Lives here (not in register_auth_routes) so it shares ``PUT /api/config``'s
        ``_CONFIG_SAVE_LOCK`` + snapshot/rollback — its full-file ``save_config``
        must not race with a concurrent settings save (review r1#3). Callable only
        by a trusted-local client (extension / local UI / CLI), never a remote
        session ("change the lock only from inside the house"); applied live (no
        restart); refused when env-managed.
        """
        import secrets as _secrets

        from openbiliclaw import auth_core as _ac
        from openbiliclaw.config import _default_config_path as _cfg_path
        from openbiliclaw.config import get_auth_plain_password as _get_plain
        from openbiliclaw.config import load_config as _load
        from openbiliclaw.config import save_config as _save

        gate = _get_auth_gate()
        if not gate.is_trusted_local(request):
            return JSONResponse({"ok": False, "error": "local_only"}, status_code=403)
        if gate.database is None:
            return JSONResponse({"ok": False, "error": "unavailable"}, status_code=503)
        env_vars = _auth_env_overrides()
        if env_vars:
            return JSONResponse(
                {"ok": False, "error": "env_managed", "vars": env_vars}, status_code=409
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        enabled = bool(body.get("enabled"))
        password = body.get("password")
        password = str(password) if password is not None else None
        ttl = body.get("session_ttl_hours")

        async with _CONFIG_SAVE_LOCK:
            cfg = _load()  # re-read inside the lock to avoid clobbering a concurrent save
            auth = cfg.api.auth
            was_enabled = auth.enabled
            if enabled:
                if password and password.strip():
                    auth.password_hash = _ac.hash_password(password)
                if not auth.password_hash.strip():
                    return JSONResponse(
                        {"ok": False, "error": "password_required"}, status_code=400
                    )
                auth.enabled = True
                if not auth.session_secret.strip():
                    auth.session_secret = _secrets.token_urlsafe(32)
                if ttl is not None:
                    with suppress(TypeError, ValueError):
                        auth.session_ttl_hours = max(0, int(ttl))
            else:
                auth.enabled = False

            # force_bump revokes on an enabled on/off toggle or an explicit
            # password in this request (neither is guaranteed to change the
            # fingerprint). A credential change the request can't see — e.g. a
            # password_hash that drifted on disk via an out-of-band `set-password`
            # while running — is caught by revoke_and_set_fingerprint comparing the
            # new fingerprint to the stored one inside its transaction (r4#2).
            force_bump = (auth.enabled != was_enabled) or bool(password and password.strip())
            config_path = _cfg_path()
            config_existed = config_path.exists()
            backup_path = _snapshot_config_file(config_path)

            def _rollback_cfg() -> None:
                # Restore config.toml to its pre-save state on any failure path. If
                # it existed, restore the snapshot; if it did NOT (backup is None),
                # remove anything _save created so a failed change leaves no durable
                # config behind (review r11#2).
                if backup_path is not None:
                    with suppress(Exception):
                        _restore_config_snapshot(backup_path, config_path)
                elif not config_existed:
                    with suppress(Exception):
                        config_path.unlink(missing_ok=True)

            # 1) Persist to disk FIRST (snapshot + rollback, like PUT /api/config).
            #    Nothing is published to the live gate or the DB yet, so a write
            #    failure here leaves ALL durable + live state on the old password.
            try:
                _save(cfg)
            except Exception:
                _rollback_cfg()
                logger.warning("auth: admin save_config failed", exc_info=True)
                return JSONResponse({"ok": False, "error": "unavailable"}, status_code=503)
            # 2) Verify the write is EFFECTIVE as startup will see it. config.toml is
            #    not the only layer: load_config merges config.local.toml OVER it
            #    (local wins). If config.local pins an auth field, our config.toml
            #    write silently reverts on restart while the live gate briefly shows
            #    success. Reload the merged effective config; if the intended change
            #    didn't take, roll back and report a conflict instead of a false
            #    success (review r9). (env is refused earlier with 409.)
            effective = _load().api.auth
            shadowed = effective.enabled != cfg.api.auth.enabled
            if enabled and password and password.strip():
                shadowed = shadowed or not _ac.verify_password(password, effective.password_hash)
            if enabled and ttl is not None:
                shadowed = shadowed or effective.session_ttl_hours != cfg.api.auth.session_ttl_hours
            if shadowed:
                _rollback_cfg()
                logger.warning("auth: admin change shadowed by config.local.toml; not applied")
                return JSONResponse({"ok": False, "error": "shadowed"}, status_code=409)
            # 3) Derive the fingerprint from the SAME material the startup reconcile
            #    will read AFTER this save — get_auth_plain_password() on the JUST-
            #    persisted file (env is refused above). save_config may keep an
            #    unchanged plaintext `password` line (→ "pw:"+plain) or persist
            #    hash-only (→ "ph:"+hash); reading post-save makes our stored
            #    fingerprint match reconcile's exactly, so a successful change never
            #    spuriously revokes on the next restart (review r3#1 / r8).
            plain_after = _get_plain()
            fingerprint = (
                _ac.password_fingerprint(
                    auth.session_secret, plain=plain_after, password_hash=auth.password_hash
                )
                if (auth.password_hash.strip() and auth.session_secret.strip())
                else None
            )
            # 4) Durable revocation (atomic). If it fails, roll the config file back
            #    so the persisted password still matches the UNCHANGED DB
            #    fingerprint/epoch, and do NOT publish — old sessions stay valid
            #    under the old password (revoke-first would instead commit an epoch
            #    bump + fingerprint that the config rollback can't undo). A crash
            #    BETWEEN the steps is self-healed by reconcile_password_fingerprint
            #    at startup: config's new password vs the stale DB fingerprint
            #    mismatches → bump + store → the change completes deterministically.
            try:
                gate.database.revoke_and_set_fingerprint(fingerprint, force_bump=force_bump)
            except Exception:
                _rollback_cfg()
                logger.warning("auth: admin revoke failed; change not applied", exc_info=True)
                return JSONResponse({"ok": False, "error": "unavailable"}, status_code=503)
            # 5) Publish live so it takes effect without a restart.
            gate.auth = cfg.api.auth
            gate.reconcile_ok = True

        logger.info("auth: gate %s via local admin", "enabled" if enabled else "disabled")
        return JSONResponse(
            {
                "ok": True,
                "enabled": cfg.api.auth.enabled,
                "trust_loopback": cfg.api.auth.trust_loopback,
            }
        )

    with suppress(Exception):
        from openbiliclaw.config import get_auth_plain_password

        reconcile_password_fingerprint(app.state.auth_gate, plain=get_auth_plain_password())

    def _degraded_issues_payload() -> list[dict[str, str]]:
        return [
            {
                "field": str(getattr(issue, "field", "")),
                "message": str(getattr(issue, "message", issue)),
                "severity": str(getattr(issue, "severity", "warning")),
            }
            for issue in getattr(ctx, "degraded_issues", [])
        ]

    def _degraded_body() -> dict[str, object]:
        return {
            "status": "degraded",
            "reason": str(getattr(ctx, "degraded_reason", "")),
            "issues": _degraded_issues_payload(),
        }

    @app.middleware("http")
    async def _degraded_mode_guard(request: Request, call_next: Any) -> Any:
        if not bool(getattr(ctx, "degraded", False)):
            return await call_next(request)
        path = request.url.path
        method = request.method.upper()
        allowed = (
            method == "OPTIONS"
            or path == "/api/ping"
            or path == "/api/health"
            or path == "/api/runtime-status"
            or path == "/favicon.ico"
            or path == "/api/autostart-status"
            or path == "/api/autostart/apply"
            or path in ("/api/init-status", "/api/init", "/api/init/cancel")
            # Update status + manual check/apply: a backend that can't build its
            # LLM registry is exactly when pulling a fix-carrying release matters,
            # so the recovery surface must stay reachable while degraded.
            or path in ("/api/update-status", "/api/update/check", "/api/update/apply")
            or (path == "/api/config" and method in {"GET", "PUT"})
            # Setup wizard's model discovery. Read-only (no config write) and
            # needed *precisely* while degraded: a first-run install with no
            # usable LLM yet is degraded by definition, so blocking this would
            # 503 the one screen that exists to fix that state.
            or path == "/api/config/discover-models"
            or path.startswith("/api/auth")
            or path.startswith("/m")
        )
        if allowed:
            return await call_next(request)
        return JSONResponse(status_code=503, content=_degraded_body())

    def _init_active_now() -> bool:
        """Defensive ``init_active`` check usable from any handler/middleware.

        Returns False (never raises) when the coordinator/DB is a test stub or
        unavailable, so gating logic degrades to "not active" instead of 500.
        """
        coord = getattr(ctx, "init_coordinator", None)
        if coord is None:
            return False
        try:
            return bool(coord.init_active())
        except Exception:
            return False

    # gui-init D1 — DENY-BY-DEFAULT writer gating. While a guided init is active,
    # every mutating request (POST/PUT/PATCH/DELETE) is rejected with 409 unless
    # it is on the small allowlist of init-essential writers below. An allowlist
    # of *blocked* paths is fragile (every new soul/pool writer must remember to
    # opt in); denying by default means no writer can silently race init.
    #
    # Allowed during init:
    #  - /api/init, /api/init/cancel        — init control itself
    #  - /api/bilibili/cookie               — handler no-ops during init
    #  - /api/auth/*                        — auth-gate management (login/admin)
    #  - /api/sources/*/kick                — init's own dispatcher kick
    #  - /api/sources/*/task-result         — init bootstrap results (the handler
    #                                         self-guards: skips pool writes and
    #                                         only propagates init-owned results)
    # (GET reads — /api/sources/*/next-task, /api/init-status, … — are never
    #  gated since only mutating methods are checked.)
    _init_write_allowlist = frozenset(
        {
            "/api/init",
            "/api/init/cancel",
            "/api/bilibili/cookie",
        }
    )

    def _init_write_allowed(path: str) -> bool:
        if path in _init_write_allowlist or path.startswith("/api/auth"):
            return True
        # Exact-segment match for the bootstrap protocol: only
        # /api/sources/<source>/{kick,task-result}. Split WITHOUT stripping so a
        # trailing slash ("/api/sources/xhs/kick/") yields 6 parts and is NOT
        # allowed, and recipe CRUD like /api/sources/kick (recipe_id="kick")
        # yields 4 parts and is NOT allowed.
        segments = path.split("/")  # "/api/sources/xhs/kick" → ['', api, sources, xhs, kick]
        return (
            len(segments) == 5
            and segments[1] == "api"
            and segments[2] == "sources"
            and segments[4] in ("kick", "task-result")
        )

    @app.middleware("http")
    async def _init_active_write_guard(request: Request, call_next: Any) -> Any:
        if request.method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            path = request.url.path
            if not _init_write_allowed(path) and _init_active_now():
                return JSONResponse(
                    {"error": "init_running", "detail": "初始化进行中，请稍后再试"},
                    status_code=409,
                )
        return await call_next(request)

    @app.middleware("http")
    async def _diary_stats_cache(request: Request, call_next: Any) -> Any:
        path = request.url.path
        # 任何非 GET 请求都清空缓存（写操作后数据可能变化）
        if request.method.upper() != "GET":
            _api_cache.invalidate_namespace(_API_CACHE_NAMESPACE)
            return await call_next(request)
        # 缓存白名单内的 GET 请求
        if path in _DIARY_CACHEABLE_PATHS:
            # 随机抽样的池子请求（shuffle=true）不缓存：相同 URL 命中缓存
            # 会让前端"换一批"拿到完全相同的批次，看起来毫无反应。
            if path == "/api/pool/all" and "shuffle=true" in request.url.query:
                return await call_next(request)
            key = f"{path}?{request.url.query}"
            # 尝试从两级缓存获取
            cached_data = _api_cache.get(key, namespace=_API_CACHE_NAMESPACE)
            if cached_data is not None:
                # cached_data 格式: (body_bytes, media_type, headers_dict)
                body, media_type, headers = cached_data
                return Response(body, media_type=media_type, headers=headers)
            resp = await call_next(request)
            if resp.status_code == 200:
                # 兼容 JSONResponse 与 StreamingResponse（GZip 包装）
                body = (
                    await resp.body()
                    if hasattr(resp, "body")
                    else b"".join([chunk async for chunk in resp.body_iterator])
                )
                # 存储可序列化格式: (body_bytes, media_type, headers_dict)
                cached_data = (body, resp.media_type, dict(resp.headers))
                _api_cache.set(
                    key,
                    cached_data,
                    ttl=_DIARY_CACHE_TTL,
                    namespace=_API_CACHE_NAMESPACE,
                )
                return Response(body, media_type=resp.media_type, headers=dict(resp.headers))
            return resp
        return await call_next(request)

    # Register AFTER the degraded guard so the auth gate is the outermost http
    # middleware (runs first): unauthenticated requests are rejected before any
    # downstream handling. CORS stays inner; 401/403 echo a permissive header.
    app.middleware("http")(make_auth_middleware(_get_auth_gate))

    def _schedule_post_feedback_tasks() -> None:
        with suppress(Exception):
            feedback_batch_scheduler.schedule()

    async def _ingest_profile_update_events(events: list[dict[str, Any]]) -> int:
        """Feed events into the profile-update pipeline when ready.

        Init handles first-run analysis explicitly via ``analyze_events`` +
        ``build_initial_profile``. After a profile exists, ordinary browser
        events and extension task results should also affect the incremental
        update buffers instead of only being persisted to event memory.
        """
        if not events or ctx.soul_engine is None:
            return 0
        is_ready = getattr(ctx.soul_engine, "is_profile_ready", None)
        if callable(is_ready):
            with suppress(Exception):
                if not bool(is_ready()):
                    return 0

        pipeline = getattr(ctx.soul_engine, "pipeline", None)
        if pipeline is None:
            return 0

        from openbiliclaw.soul.pipeline import signals_from_events

        signals = signals_from_events(events)
        if not signals:
            return 0
        try:
            ingest_batch = getattr(pipeline, "ingest_batch", None)
            if callable(ingest_batch):
                await ingest_batch(signals)
                return len(signals)
            ingest = getattr(pipeline, "ingest", None)
            if callable(ingest):
                for signal in signals:
                    await ingest(signal)
                return len(signals)
        except Exception:
            logger.exception("Failed to ingest events into profile pipeline")
        return 0

    def _event_cursor_value(value: object) -> int:
        if isinstance(value, bool):
            return 0
        if not isinstance(value, int | float | str | bytes | bytearray):
            return 0
        try:
            event_id = int(value or 0)
        except (TypeError, ValueError):
            return 0
        return max(0, event_id)

    def _runtime_state_value(state: dict[str, object], key: str) -> int:
        return _event_cursor_value(state.get(key, 0))

    def _query_profile_update_backfill_events(
        *,
        after_event_id: int,
        max_event_id: int,
    ) -> list[dict[str, Any]]:
        query_events_since = getattr(ctx.database, "query_events_since", None)
        if not callable(query_events_since):
            query_events_since = getattr(ctx.memory_manager, "query_events_since", None)
        if not callable(query_events_since):
            return []
        try:
            rows = query_events_since(
                after_event_id=after_event_id,
                event_types=list(_PROFILE_UPDATE_BACKFILL_EVENT_TYPES),
            )
        except Exception:
            logger.exception("Failed to query profile pipeline backfill events")
            return []
        if not isinstance(rows, list | tuple):
            return []

        events: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                with suppress(Exception):
                    row = dict(row)
            if not isinstance(row, dict):
                continue
            event_id = _event_row_id(row)
            if event_id is None:
                continue
            if max_event_id > 0 and event_id > max_event_id:
                continue
            event = dict(row)
            event["metadata"] = _event_row_metadata(event)
            events.append(event)
            if len(events) >= _PROFILE_UPDATE_BACKFILL_LIMIT:
                break
        return events

    async def _backfill_pending_discovery_events_to_profile_pipeline(
        *,
        max_event_id: int,
    ) -> int:
        """Feed discovery-pending event rows that predate the current request.

        Older versions only used ``pending_signal_events`` as a discovery
        refresh watermark. If such rows already exist on disk, this backfill
        lets the next successful ordinary event ingest catch them up without
        advancing the discovery cursor itself.
        """
        if max_event_id <= 0:
            return 0
        load_state = getattr(ctx.memory_manager, "load_discovery_runtime_state", None)
        update_state = getattr(ctx.memory_manager, "update_discovery_runtime_state", None)
        if not callable(load_state) or not callable(update_state):
            return 0
        try:
            state = load_state()
        except Exception:
            logger.exception("Failed to load discovery runtime state for profile backfill")
            return 0
        if not isinstance(state, dict):
            return 0

        discovery_cursor = _runtime_state_value(state, "last_processed_event_id")
        if "last_profile_pipeline_event_id" in state:
            cursor = _runtime_state_value(state, "last_profile_pipeline_event_id")
        elif discovery_cursor > 0 and discovery_cursor < max_event_id:
            cursor = discovery_cursor
        else:
            cursor = max(0, max_event_id - _PROFILE_UPDATE_BACKFILL_LIMIT)
        if cursor >= max_event_id:
            return 0

        # The backfill query scans event rows; run it off the event loop.
        events = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: _query_profile_update_backfill_events(
                after_event_id=cursor,
                max_event_id=max_event_id,
            ),
        )
        if not events:
            return 0

        ingested = await _ingest_profile_update_events(events)
        if ingested <= 0:
            return 0

        latest_backfilled_id = max(_event_row_id(event) or 0 for event in events)
        if latest_backfilled_id <= 0:
            return ingested

        def _advance(runtime_state: dict[str, object]) -> None:
            current = _runtime_state_value(runtime_state, "last_profile_pipeline_event_id")
            runtime_state["last_profile_pipeline_event_id"] = max(current, latest_backfilled_id)

        with suppress(Exception):
            update_state(_advance)
        return ingested

    fallback_chat_turns: dict[str, dict[str, Any]] = {}
    # RAG citations per chat turn, keyed by turn_id. Kept in memory: these are
    # ephemeral UI hints that don't need to survive a restart (the durable
    # turn row itself is the source of truth for the reply text).
    chat_turn_references: dict[str, list[dict[str, Any]]] = {}

    def _normalize_chat_scope(scope: str) -> str:
        normalized = scope.strip().lower()
        if normalized in {"chat", "delight", "probe", "avoidance_probe"}:
            return normalized
        return "chat"

    def _normalize_chat_turn(row: dict[str, Any]) -> ChatTurnOut:
        return ChatTurnOut(
            turn_id=str(row.get("turn_id", "")),
            session=str(row.get("session", "popup") or "popup"),
            scope=_normalize_chat_scope(str(row.get("scope", "chat"))),
            subject_id=str(row.get("subject_id", "") or ""),
            subject_title=str(row.get("subject_title", "") or ""),
            message=str(row.get("message", "") or ""),
            reply=str(row.get("reply", "") or ""),
            status=str(row.get("status", "pending") or "pending"),
            error=str(row.get("error", "") or ""),
            created_at=str(row.get("created_at", "") or ""),
            updated_at=str(row.get("updated_at", "") or ""),
            references=list(chat_turn_references.get(str(row.get("turn_id", "")), [])),
        )

    def _chat_db_method(name: str) -> Any | None:
        method = getattr(ctx.database, name, None)
        return method if callable(method) else None

    def _list_chat_turn_rows(
        *,
        session: str = "popup",
        scope: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        list_chat_turns = _chat_db_method("list_chat_turns")
        if list_chat_turns is not None:
            return cast(
                "list[dict[str, Any]]",
                list_chat_turns(session=session, scope=scope, limit=limit),
            )
        rows = [
            dict(row)
            for row in fallback_chat_turns.values()
            if row.get("session") == session and (not scope or row.get("scope") == scope)
        ]
        rows.sort(key=lambda row: (str(row.get("created_at", "")), str(row.get("turn_id", ""))))
        return rows[-max(1, int(limit)) :]

    def _health_profile_ready() -> bool | None:
        soul_engine = getattr(ctx, "soul_engine", None)
        if soul_engine is None:
            return None
        is_ready_candidate = getattr(soul_engine, "is_profile_ready", None)
        if not callable(is_ready_candidate):
            return None
        is_ready_fn = cast("Callable[[], bool]", is_ready_candidate)
        try:
            return bool(is_ready_fn())
        except Exception:
            logger.debug("Health profile readiness check failed", exc_info=True)
            return None

    _lan_ip_value: str | None = None
    _lan_ip_checked_at = float("-inf")

    def _health_lan_ip() -> str | None:
        nonlocal _lan_ip_value, _lan_ip_checked_at
        if time.monotonic() - _lan_ip_checked_at < _LAN_IP_TTL_SECONDS:
            return _lan_ip_value
        _lan_ip_value = _detect_lan_ip()
        _lan_ip_checked_at = time.monotonic()
        return _lan_ip_value

    # Embedding readiness is probed live (see _health_embedding_ready) and the
    # result cached here so frequent /api/health polls share one provider call.
    _embedding_ready_value = False
    _embedding_ready_checked_at = float("-inf")
    _embedding_ready_lock = asyncio.Lock()

    async def _health_embedding_ready() -> bool:
        """Whether the embedding service can *currently* produce a vector.

        This is a live signal, not a build-time one. A service object that
        was constructed at startup but whose provider now 404s (``bge-m3``
        never pulled, Ollama stopped) reports ``False`` here, so the popup's
        "semantic dedup off" banner reflects reality instead of going green
        while every embed silently fails. Conversely, once a previously
        broken provider is fixed the banner clears within the cache TTL.

        Layers:
          - no service object (provider not configured) -> ``False``;
          - service without a ``probe()`` (legacy/stub) -> build-only ``True``;
          - otherwise a cache-bypassing ``probe()``, result cached for
            ``_EMBEDDING_READY_TTL_SECONDS`` and single-flighted so concurrent
            polls share one provider round-trip.
        """
        nonlocal _embedding_ready_value, _embedding_ready_checked_at

        soul_engine = getattr(ctx, "soul_engine", None)
        service = getattr(soul_engine, "_embedding_service", None)
        if service is None:
            return False
        probe = getattr(service, "probe", None)
        if not callable(probe):
            # Legacy service without a live probe — "built" is the best signal.
            return True

        _embedding_ttl = (
            _EMBEDDING_READY_TTL_SECONDS if _embedding_ready_value else _EMBEDDING_FAIL_TTL_SECONDS
        )
        if time.monotonic() - _embedding_ready_checked_at < _embedding_ttl:
            return _embedding_ready_value

        async with _embedding_ready_lock:
            # Another request may have refreshed the cache while we waited.
            _embedding_ttl = (
                _EMBEDDING_READY_TTL_SECONDS
                if _embedding_ready_value
                else _EMBEDDING_FAIL_TTL_SECONDS
            )
            if time.monotonic() - _embedding_ready_checked_at < _embedding_ttl:
                return _embedding_ready_value
            try:
                ready = bool(
                    await asyncio.wait_for(probe(), timeout=_EMBEDDING_PROBE_TIMEOUT_SECONDS)
                )
            except TimeoutError:
                # Strict (gui-init): a prereq is "ok" only on a confirmed real
                # embedding round-trip. A timeout (even a cold model load) within
                # the generous window means we could NOT confirm it works → report
                # not-ready, and the short fail-TTL re-probes soon so it greens
                # quickly once the load finishes.
                logger.debug("Embedding readiness probe timed out; reporting not ready")
                ready = False
            except Exception:
                logger.debug("Embedding readiness probe errored", exc_info=True)
                ready = False
            _embedding_ready_value = ready
            _embedding_ready_checked_at = time.monotonic()
            return ready

    def _embedding_ready_peek() -> tuple[bool, bool]:
        """Return ``(cached_value, is_stale)`` without blocking on a probe.

        Used by ``GET /api/init-status`` so the poll never awaits a slow
        embedding round-trip — it returns the last cached value and lets the
        real ``_health_embedding_ready()`` refresh the cache in the background.
        """
        nonlocal _embedding_ready_value, _embedding_ready_checked_at
        ttl = (
            _EMBEDDING_READY_TTL_SECONDS if _embedding_ready_value else _EMBEDDING_FAIL_TTL_SECONDS
        )
        return _embedding_ready_value, (time.monotonic() - _embedding_ready_checked_at >= ttl)

    def _embedding_required_for_init() -> bool:
        """Whether guided init must wait for a configured embedding provider."""
        cfg = getattr(ctx, "config", None)
        emb = getattr(getattr(cfg, "llm", None), "embedding", None)
        provider = str(getattr(emb, "provider", "") or "").strip()
        return bool(provider)

    @app.get("/api/health", response_model=HealthResponse, response_model_exclude_none=True)
    async def health() -> HealthResponse | JSONResponse:
        profile_ready = _health_profile_ready()
        lan_ip = _health_lan_ip()
        embedding_ready = await _health_embedding_ready()
        if bool(getattr(ctx, "degraded", False)):
            body: dict[str, object] = {
                "status": "degraded",
                "service": "openbiliclaw-api",
                "reason": str(getattr(ctx, "degraded_reason", "")),
                "issues": _degraded_issues_payload(),
                "embedding_ready": embedding_ready,
            }
            if profile_ready is not None:
                body["profile_ready"] = profile_ready
            if lan_ip is not None:
                body["lan_ip"] = lan_ip
            return JSONResponse(status_code=200, content=body)
        return HealthResponse(
            status="ok",
            service="openbiliclaw-api",
            profile_ready=profile_ready,
            lan_ip=lan_ip,
            embedding_ready=embedding_ready,
        )

    @app.get("/api/init-status", response_model=InitStatusOut)
    async def init_status(request: Request) -> InitStatusOut:
        """Authoritative guided-init status + pre-init checklist (gui-init §3).

        Remote-readable (mirrors autostart-status): a non-local caller still
        sees the state but ``can_manage`` is False. Degraded-mode readable.
        """
        from openbiliclaw.docker_runtime import is_running_in_container

        coord = ctx.init_coordinator
        prereqs = ctx.init_prereqs
        run = coord.get_status()
        # init-status is polled frequently (the extension popup polls ~3s). Each
        # probe is a real (strict) provider round-trip with a generous cold-load
        # timeout, and a cache-miss used to make the HTTP response await the
        # slowest probe — so the poll lagged 1–2s every time a down-provider's
        # fail-TTL re-fired. Instead we return the cached values instantly and
        # refresh only the stale ones in the background, so the poll is always
        # fast and the UI catches up on the next tick.
        embedding, embedding_stale = _embedding_ready_peek()
        chat = prereqs.peek_chat()
        chat_stale = prereqs.chat_is_stale()
        bili = prereqs.peek_bilibili()
        bili_stale = prereqs.bilibili_is_stale()

        def _bg_refresh(coro: Any) -> None:
            task = asyncio.create_task(coro)
            _fire_and_forget_tasks.add(task)
            task.add_done_callback(_fire_and_forget_tasks.discard)

        if chat_stale:
            _bg_refresh(prereqs.chat_ready())
        if bili_stale:
            _bg_refresh(prereqs.bilibili_check())
        if embedding_stale:
            _bg_refresh(_health_embedding_ready())
        platforms = prereqs.enabled_platforms()
        initialized = bool(_health_profile_ready())
        trusted = _get_auth_gate().is_trusted_local(request)
        supported = not is_running_in_container()
        running = bool(run["running"])
        # v0.3.118+: bilibili login is no longer a server-side hard gate —
        # whether it blocks depends on the client's per-run source selection,
        # which only POST /api/init sees. ``bilibili_logged_in`` stays in the
        # prerequisites payload so clients gate the start button themselves
        # when B站 is among the checked sources; POST revalidates regardless.
        embedding_required = _embedding_required_for_init()
        hard_ok = chat and (embedding or not embedding_required)
        # Mirror POST /api/init's guards: an already-initialized profile blocks
        # a (non-force) start, so can_start must reflect that too — otherwise E1
        # and E2 disagree and a client could offer "start" that E2 rejects.
        can_start = trusted and supported and hard_ok and not running and not initialized

        if not supported:
            reason, detail = "unsupported_runtime", "Docker 运行时不支持图形化初始化"
        elif running:
            reason, detail = "already_running", "初始化进行中"
        elif initialized:
            reason, detail = "already_initialized", "已经初始化过了；如需重建请用 force"
        elif not chat:
            reason, detail = "llm_not_ready", "AI 服务还没配好或当前不可用"
        elif embedding_required and not embedding:
            reason, detail = "embedding_not_ready", "向量模型还没就绪"
        elif bili != "ok":
            # Informational (does not flip can_start): blocks only if the
            # client keeps bilibili selected, which the UI enforces.
            reason, detail = "bilibili_not_logged_in", "还没检测到 B站 登录"
        elif run.get("status") in ("failed", "cancelled"):
            # Prereqs are fine and nothing is running, but the last run ended
            # badly — surface why so the UI can show it (can_start stays true so
            # the user can retry) (gui-init review).
            reason = run.get("reason") or str(run.get("status"))
            detail = "上次初始化未完成，可重试"
        else:
            reason, detail = "none", ""

        return InitStatusOut(
            initialized=initialized,
            running=running,
            run_id=run["run_id"],
            sequence=run["sequence"],
            current_stage=run["current_stage"],
            total_stages=run["total_stages"],
            stages=[InitStageOut(**s) for s in run["stages"]],
            partial_success=bool(run["partial_success"]),
            can_start=can_start,
            can_manage=trusted,
            prerequisites=InitPrerequisitesOut(
                bilibili_logged_in=(bili == "ok"),
                bilibili_check=bili,
                llm_ready=chat,
                embedding_ready=embedding,
                embedding_required=embedding_required,
                enabled_platforms=platforms,
            ),
            reason=reason,
            detail=detail,
        )

    def _init_runtime_supported() -> tuple[bool, str]:
        """Cheap guard: GUI init needs a writable host runtime (gui-init §5b,
        review R2 A-7). Docker uses the headless auto-init path instead.
        """
        from openbiliclaw.docker_runtime import is_running_in_container

        if is_running_in_container():
            return False, "Docker 运行时不支持图形化初始化"
        cfg = ctx.config
        if cfg is not None:
            try:
                if not os.access(str(cfg.data_path), os.W_OK):
                    return False, "数据目录不可写"
            except Exception:
                pass
        return True, ""

    async def _persist_guided_init_source_opt_in(effective_sources: set[str]) -> None:
        """Best-effort: checked guided-init sources become enabled settings.

        The run itself uses ``effective_sources`` directly, so a config write
        failure must not block initialization. Persisting keeps the setup page,
        popup, and later background discovery aligned with the user's explicit
        checkbox choice.
        """
        cfg = getattr(ctx, "config", None)
        sources_cfg = getattr(cfg, "sources", None) if cfg is not None else None
        if sources_cfg is None or not effective_sources:
            return

        changed = False
        for source in _INIT_SOURCE_ORDER:
            if source not in effective_sources:
                continue
            source_cfg = getattr(sources_cfg, source, None)
            if source_cfg is not None and not bool(getattr(source_cfg, "enabled", False)):
                source_cfg.enabled = True
                changed = True
        if not changed:
            return

        try:
            from openbiliclaw.config import Config, save_config

            cfg = cast("Config", cfg)

            async with _CONFIG_SAVE_LOCK:
                save_config(cfg)
        except Exception:
            logger.warning("guided init source opt-in save_config failed", exc_info=True)
            return

        if bool(getattr(ctx, "degraded", False)):
            return
        try:
            await ctx.rebuild_from_config(cfg)
            await ctx.restart_background_tasks(app, run_post_reload_llm_work=False)
            with suppress(Exception):
                await ctx.event_hub.publish(
                    {
                        "type": "config_reloaded",
                        "message": "初始化来源选择已写入配置。",
                    }
                )
        except Exception:
            logger.warning("guided init source opt-in hot-reload failed", exc_info=True)

    async def _run_guided_init_wrapper(
        run_id: str, selected_sources: set[str] | None = None
    ) -> None:
        """Sole status/event writer for an API-launched guided init (gui-init
        §5f). Drives the shared ``run_guided_init`` through the coordinator and
        persists the terminal state here — completed / failed / cancelled —
        never via a side path. Imported lazily to avoid an import cycle with
        the CLI module that owns the shared pipeline.

        ``selected_sources`` is the extension's per-run platform choice. When
        present, it is an explicit local opt-in for those sources (see
        :func:`_select_init_platforms`). ``None`` keeps the legacy behaviour of
        using everything enabled.
        """
        # K6：guided-init 管线已收口到 runtime/init_flow，api 直接依赖 runtime
        # （api→runtime 合法），不再反向依赖 cli。
        from openbiliclaw.runtime.init_flow import (
            _INIT_BILIBILI_FAVORITE_LIMIT,
            _INIT_BILIBILI_FOLLOW_LIMIT,
            _INIT_POOL_TARGET_COUNT,
            GuidedInitError,
            run_guided_init,
        )

        coord = ctx.init_coordinator

        async def _api_discover_backfill(
            profile: Any, *, target_pool_count: int, label_suffix: str = ""
        ) -> int:
            # API path backfills through the live controller so it holds the
            # refresh lock (B1); ``label_suffix`` is CLI-only console flavour.
            return int(
                await ctx.runtime_controller.run_init_backfill(
                    profile, target_pool_count, fully_parallel=True
                )
            )

        try:
            await coord.mark_running(run_id)
            enabled = set(ctx.init_prereqs.enabled_platforms())
            effective = _select_init_platforms(enabled, selected_sources)
            result = await run_guided_init(
                client=ctx.bilibili_client,
                memory=ctx.memory_manager,
                soul_engine=ctx.soul_engine,
                favorite_limit=_INIT_BILIBILI_FAVORITE_LIMIT,
                follow_limit=_INIT_BILIBILI_FOLLOW_LIMIT,
                include_bili="bilibili" in effective,
                include_xhs="xiaohongshu" in effective,
                include_dy="douyin" in effective,
                include_yt="youtube" in effective,
                include_x="twitter" in effective,
                include_zhihu="zhihu" in effective,
                target_pool_count=_INIT_POOL_TARGET_COUNT,
                discover_backfill=_api_discover_backfill,
                coordinator=coord,
                run_id=run_id,
            )
            await coord.complete(run_id, partial_success=result.discovery_error)
        except asyncio.CancelledError:
            # Cancel was requested via /api/init/cancel — shield the terminal
            # write so the cancelled status still lands before we propagate.
            with suppress(Exception):
                await asyncio.shield(coord.cancel(run_id))
            raise
        except GuidedInitError as exc:
            logger.warning("guided init %s failed: %s", run_id, exc.reason)
            with suppress(Exception):
                await coord.fail(run_id, exc.reason)
        except Exception:
            logger.exception("guided init %s crashed", run_id)
            with suppress(Exception):
                await coord.fail(run_id, "internal_error")
        finally:
            if not bool(getattr(ctx, "degraded", False)):
                with suppress(Exception):
                    await ctx.restart_background_tasks(app)

    @app.post("/api/init")
    async def start_guided_init(request: Request) -> JSONResponse:
        """Launch guided init in the background (local-only; gui-init §2/§5b).

        Cheap rejections run BEFORE reserving the run so a rejected request
        never leaves a stuck ``starting`` row (review R2 A-2). The single-flight
        guard is the DB reservation inside ``try_start``.
        """
        if not _get_auth_gate().is_trusted_local(request):
            return JSONResponse({"error": "local_only"}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            body = {}
        force = bool(body.get("force", False)) if isinstance(body, dict) else False
        # Optional per-run platform selection from the extension checkboxes. A
        # list (even empty) is an explicit choice; absent → None = use all
        # enabled (CLI / legacy clients). Sent source keys are explicit opt-ins
        # for this local guided-init run.
        raw_sources = body.get("sources") if isinstance(body, dict) else None
        selected_sources = {str(s) for s in raw_sources} if isinstance(raw_sources, list) else None

        coord = ctx.init_coordinator

        supported, detail = _init_runtime_supported()
        if not supported:
            return JSONResponse({"error": "unsupported_runtime", "detail": detail}, status_code=409)
        if not force and _health_profile_ready() is True:
            return JSONResponse(
                {"error": "already_initialized", "detail": "已初始化；重建请传 force"},
                status_code=409,
            )
        # At least one valid source must survive an EXPLICIT per-run selection
        # (v0.3.118+: bilibili is selectable like the rest, so an empty
        # selection is reachable). Cheap rejection, before reserving. Legacy
        # clients (no "sources" key) stay permissive.
        effective_sources = _select_init_platforms(
            set(ctx.init_prereqs.enabled_platforms()), selected_sources
        )
        if selected_sources is not None and not effective_sources:
            return JSONResponse(
                {"error": "no_sources_selected", "detail": "至少选择一个有效数据来源"},
                status_code=409,
            )
        if selected_sources is not None:
            await _persist_guided_init_source_opt_in(effective_sources)

        run_id = uuid.uuid4().hex
        if not coord.try_start(run_id):
            return JSONResponse({"error": "already_running"}, status_code=409)

        # Critical-section revalidation: prereqs may have lapsed between the
        # status poll and now. On a miss, roll the reservation back to idle so
        # no stuck row remains (review R2 A-2). B站 login is only a prerequisite
        # when bilibili is among the selected sources.
        if "bilibili" in effective_sources:
            bili = await ctx.init_prereqs.bilibili_check()
            if bili != "ok":
                coord.reset_to_idle(run_id, reason="bilibili_not_logged_in")
                return JSONResponse({"error": "bilibili_not_logged_in"}, status_code=409)
        chat = await ctx.init_prereqs.chat_ready()
        if not chat:
            coord.reset_to_idle(run_id, reason="llm_not_ready")
            return JSONResponse({"error": "llm_not_ready"}, status_code=409)
        if _embedding_required_for_init() and not await _health_embedding_ready():
            coord.reset_to_idle(run_id, reason="embedding_not_ready")
            return JSONResponse({"error": "embedding_not_ready"}, status_code=409)

        registry = getattr(ctx, "task_registry", None)
        if registry is not None:
            task = registry.track("guided_init", _run_guided_init_wrapper(run_id, selected_sources))
        else:
            task = asyncio.create_task(_run_guided_init_wrapper(run_id, selected_sources))
        coord.attach_task(run_id, task)
        return JSONResponse({"run_id": run_id, **coord.get_status()}, status_code=202)

    @app.post("/api/init/cancel")
    async def cancel_guided_init(request: Request) -> JSONResponse:
        """Cooperatively cancel the in-flight guided init (local-only)."""
        if not _get_auth_gate().is_trusted_local(request):
            return JSONResponse({"error": "local_only"}, status_code=403)
        coord = ctx.init_coordinator
        run = ctx.database.get_latest_init_run() if ctx.database is not None else None
        if run is None or not coord.init_active():
            return JSONResponse({"error": "not_running"}, status_code=409)
        cancelled = await coord.cancel_current_run(run["run_id"])
        if not cancelled:
            return JSONResponse({"error": "not_running"}, status_code=409)
        return JSONResponse({"cancelling": True, "run_id": run["run_id"]}, status_code=202)

    @app.post("/api/bilibili/cookie", response_model=BilibiliCookieResponse)
    async def sync_bilibili_cookie(
        payload: BilibiliCookieIn,
    ) -> BilibiliCookieResponse | JSONResponse:
        """Receive a Bilibili cookie from the browser extension and persist
        it server-side so the backend can call B 站 API as the user.

        Replaces the manual "F12 → Network → copy cookie → paste into
        wizard" flow. The extension already runs on bilibili.com and has
        the ``cookies`` Chrome permission, so it's the natural place to
        get a fresh, valid cookie. We auto-sync on first install and
        whenever ``chrome.cookies.onChanged`` fires.

        Persistence: writes to ``data/bilibili_cookie.json`` (the runtime
        cookie source) AND ``config.toml [bilibili].cookie`` (kept in sync
        as a mirror for ``config-show``). Then rebuilds the runtime
        BilibiliAPIClient via the same ``rebuild_from_config`` path that
        the config-update endpoint uses, so any in-flight handlers see
        the new cookie on their next call.

        Security: the backend is bound to 127.0.0.1 by default, so this
        endpoint is only reachable from the user's own machine. CORS
        already accepts ``*`` (set when the app is built); no auth token
        is needed for an API that lives behind a localhost-only listener.
        Users who flip ``--host 0.0.0.0`` should put their own auth
        layer in front of the backend.
        """
        from openbiliclaw.bilibili.auth import AuthManager
        from openbiliclaw.config import (
            load_config_with_diagnostics,
            save_config,
        )

        cookie_value = payload.cookie.strip()
        if not cookie_value:
            return BilibiliCookieResponse(
                ok=False,
                authenticated=False,
                message="cookie payload is empty",
                error_code="empty_cookie",
            )

        # gui-init D1.2: while guided init runs, the extension keeps auto-syncing
        # the cookie. Don't validate (~30s round-trip) or rebuild the runtime
        # (which would swap the BilibiliAPIClient mid-init). Same effective
        # cookie → silent 200 no-op so the extension's auto-sync doesn't error;
        # a genuinely different cookie → 409 so the user learns the switch
        # didn't take (it applies after init), rather than being dropped.
        if _init_active_now():
            effective_cookie = ""
            try:
                _cfg, _ = load_config_with_diagnostics()
                effective_cookie = (_cfg.bilibili.cookie or "").strip()
                if not effective_cookie:
                    effective_cookie = AuthManager(data_dir=_cfg.data_path).load_cookie().strip()
            except Exception:
                effective_cookie = ""
            if cookie_value == effective_cookie and effective_cookie:
                return BilibiliCookieResponse(
                    ok=True,
                    authenticated=True,
                    message="Cookie 未变，初始化进行中无需重新同步。",
                )
            return JSONResponse(
                {
                    "error": "init_running",
                    "detail": "初始化进行中，暂不能切换 Cookie，请稍后再试。",
                },
                status_code=409,
            )

        config, diagnostics = load_config_with_diagnostics()
        # 1) Validate the cookie if requested. We use the same auth
        # manager the CLI's interactive wizard uses, for consistency.
        auth_manager = AuthManager(data_dir=config.data_path)
        if payload.validate_with_bilibili:
            status = await auth_manager.validate_cookie(cookie_value)
            if not status.authenticated:
                # Distinguish "network couldn't reach api.bilibili.com"
                # (transient — extension should retry quickly) from
                # "Bilibili rejected this cookie" (cookie expired —
                # extension should back off until next login). The
                # heuristic relies on AuthManager.validate_cookie's
                # ``message`` field — connection / timeout / DNS errors
                # surface as the underlying httpx exception text, while
                # an actual logged-out cookie surfaces as the literal
                # "当前 Cookie 未登录或已失效。" message we set in
                # validate_cookie.
                msg = (status.message or "").lower()
                network_markers = (
                    "timeout",
                    "connect",
                    "dns",
                    "ssl",
                    "proxy",
                    "name or service",
                    "connection",
                    "网络",
                    "代理",
                )
                is_network_error = any(m in msg for m in network_markers)
                error_code = "validation_network" if is_network_error else "cookie_invalid"
                return BilibiliCookieResponse(
                    ok=False,
                    authenticated=False,
                    message=status.message or "Cookie validation failed; not saved.",
                    error_code=error_code,
                )
            authenticated = True
            username = status.username or ""
            user_id = int(status.user_id or 0)
        else:
            authenticated = False
            username = ""
            user_id = 0

        # 2) Persist to both stores, but keep repeated extension syncs
        # idempotent. Chrome may POST the same Cookie several times around
        # startup; rebuilding for an unchanged effective cookie cancels and
        # restarts producer loops for no behavioral gain.
        stored_cookie = ""
        with suppress(Exception):
            stored_cookie = auth_manager.load_cookie().strip()
        configured_cookie = config.bilibili.cookie.strip()
        effective_cookie_before = configured_cookie or stored_cookie
        cookie_file_changed = stored_cookie != cookie_value
        config_changed = configured_cookie != cookie_value
        runtime_cookie_changed = effective_cookie_before != cookie_value

        if cookie_file_changed:
            auth_manager.set_cookie(cookie_value)  # → data/bilibili_cookie.json
        if config_changed:
            config.bilibili.cookie = cookie_value
            save_config(config, diagnostics.config_path)

        # 3) Reload runtime so existing in-flight components pick up
        # the new client. ``rebuild_from_config`` is atomic — if it
        # fails partway, the old runtime stays intact.
        runtime_refreshed = False
        if runtime_cookie_changed or config_changed:
            with suppress(Exception):
                await ctx.rebuild_from_config(config)
                await ctx.restart_background_tasks(app)
                runtime_refreshed = True

        # 4) Tell the extension UI the cookie just got refreshed —
        # this is how the popup knows it can stop nagging the user
        # to log in.
        with suppress(Exception):
            await ctx.event_hub.publish(
                {
                    "type": "bilibili_cookie_synced",
                    "username": username,
                    "user_id": user_id,
                    "source": payload.source,
                }
            )

        return BilibiliCookieResponse(
            ok=True,
            authenticated=authenticated,
            username=username,
            user_id=user_id,
            message=(
                "Cookie synced and runtime refreshed."
                if runtime_refreshed
                else "Cookie already synced; runtime unchanged."
            ),
        )

    @app.post("/api/init-completed")
    async def init_completed() -> dict[str, object]:
        """Notify the running server that ``openbiliclaw init`` has finished.

        Called by the CLI at the end of a successful init.  The handler
        broadcasts an ``init_completed`` event via WebSocket so the
        browser extension can immediately re-fetch profile, recommendations
        and activity data.  It also starts a replenishment refresh so the
        discovery pool is picked up without waiting for the next scheduler tick.
        """
        # Broadcast to extension
        with suppress(Exception):
            await ctx.event_hub.publish(
                {
                    "type": "init_completed",
                    "message": "初始化完成，画像与发现池已就绪。",
                }
            )
        await _request_runtime_replenishment(reason="init_completed", force=True)
        return {"ok": True}

    async def _request_runtime_replenishment(
        *,
        reason: str,
        force: bool = False,
    ) -> dict[str, object] | None:
        """Ask the runtime controller to refill the discovery pool.

        Shared by the non-feed handlers below (init_completed / event_ingest)
        and the recommendation router (injected via
        ``build_recommendation_router(request_runtime_replenishment=...)``).
        """
        request = getattr(ctx.runtime_controller, "request_replenishment", None)
        if callable(request):
            with suppress(Exception):
                result = await request(reason=reason, force=force)
                if isinstance(result, dict):
                    return cast("dict[str, object]", result)
            return None
        if force:
            trigger = getattr(ctx.runtime_controller, "trigger_manual_refresh", None)
            if callable(trigger):
                with suppress(Exception):
                    result = await trigger()
                    if isinstance(result, dict):
                        return cast("dict[str, object]", result)
            return None

        legacy_name = {
            "event_ingest": "refresh_after_event_ingest",
            "feedback": "refresh_after_feedback",
            "init_completed": "refresh_after_init",
        }.get(reason)
        if legacy_name:
            legacy = getattr(ctx.runtime_controller, legacy_name, None)
            if callable(legacy):
                with suppress(Exception):
                    result = await legacy()
                    if isinstance(result, dict):
                        return cast("dict[str, object]", result)
        return None

    def _pick_best_xhs_url(database: Any, note_id: str, incoming: str) -> str:
        """Return the most share-worthy URL for a xhs note.

        xhs search-result pages don't render ``xsec_token`` into ``<a href>``
        (React SPA keeps the token in props, not DOM), but explore-feed
        cards do. When the same note arrives both ways, prefer the URL
        that carries a token — without it, outbound links can silently
        dead-end at an xhs login wall.

        Order of preference:
        1. ``incoming`` URL if it already has ``xsec_token=``
        2. Any prior ``xhs_observed_urls`` row for this note with a token
        3. Existing ``content_cache.content_url`` if it has a token
        4. Fall back to ``incoming`` (bare URL — still works for the
           logged-in user on the xhs domain, just not guaranteed for
           share/outbound traffic)
        """
        if "xsec_token=" in incoming:
            return incoming
        try:
            row = database.conn.execute(
                "SELECT url FROM xhs_observed_urls "
                "WHERE url LIKE ? AND url LIKE '%xsec_token=%' "
                "ORDER BY observed_at DESC LIMIT 1",
                (f"%/{note_id}?%",),
            ).fetchone()
            if row and row["url"]:
                return str(row["url"])
        except Exception:
            pass
        try:
            row = database.conn.execute(
                "SELECT content_url FROM content_cache WHERE bvid=?",
                (note_id,),
            ).fetchone()
            if row and isinstance(row["content_url"], str) and "xsec_token=" in row["content_url"]:
                return str(row["content_url"])
        except Exception:
            pass
        try:
            _disc_conn = getattr(database, "_discovery_conn", None) or database.conn
            row = _disc_conn.execute(
                "SELECT content_url FROM discovery_candidates "
                "WHERE source_platform='xiaohongshu' AND content_id=? "
                "  AND content_url LIKE '%xsec_token=%' "
                "ORDER BY last_seen_at DESC LIMIT 1",
                (note_id,),
            ).fetchone()
            if row and row["content_url"]:
                return str(row["content_url"])
        except Exception:
            pass
        return incoming

    def _serialize_recommendation_items(items: list[Any]) -> list[RecommendationOut]:
        return [
            RecommendationOut(
                id=int(item.recommendation_id),
                bvid=str(item.content.bvid),
                title=str(item.content.title),
                up_name=str(item.content.up_name),
                cover_url=str(item.content.cover_url),
                expression=str(item.expression),
                topic_label=str(item.topic_label),
                presented=bool(item.presented),
                feedback_type=str(getattr(item, "feedback_type", "") or ""),
                content_id=str(getattr(item.content, "content_id", "") or item.content.bvid),
                content_url=str(getattr(item.content, "content_url", "") or ""),
                source_platform=str(getattr(item.content, "source_platform", "") or "bilibili"),
                content_type=str(getattr(item.content, "content_type", "") or "video"),
                body_text=str(getattr(item.content, "body_text", "") or ""),
                quality_score=float(getattr(item.content, "quality_score", 0.0) or 0.0),
                quality_reason=str(getattr(item.content, "quality_reason", "") or ""),
            )
            for item in items
        ]

    @app.websocket("/api/runtime-stream")
    async def runtime_stream(websocket: WebSocket) -> None:
        # The http auth middleware does NOT cover the websocket scope, so the
        # password gate must be enforced here before accepting the handshake.
        if not authorize_websocket(_get_auth_gate(), websocket):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        if bool(getattr(ctx, "degraded", False)):
            connected = False
            try:
                ctx.presence.on_connect()
                connected = True
                await websocket.send_json(
                    {
                        "type": "degraded",
                        "reason": str(getattr(ctx, "degraded_reason", "")),
                        "issues": _degraded_issues_payload(),
                    }
                )
                while True:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        raise WebSocketDisconnect
            except WebSocketDisconnect:
                pass
            finally:
                if connected:
                    ctx.presence.on_disconnect()
            return

        # Live revocation: an already-open socket from a remote client must stop
        # receiving events once its token is revoked (logout-all / password change
        # / rotate-secret). The http auth middleware never sees an established ws,
        # so re-check the revocation epoch here per-send and on a watchdog timer.
        _ws_gate = _get_auth_gate()
        _ws_is_local = _ws_gate.is_trusted_local(websocket)
        _ws_token = (
            None
            if (_ws_is_local or not _ws_gate.auth.enabled)
            else _ws_gate.pick_token(websocket)[1]
        )

        def _ws_revoked() -> bool:
            if not _ws_gate.auth.enabled or _ws_is_local:
                return False
            try:
                return not _ws_gate.token_valid(_ws_token)
            except Exception:
                return True  # DB unavailable → fail closed

        subscribe = getattr(ctx.event_hub, "subscribe", None)
        unsubscribe = getattr(ctx.event_hub, "unsubscribe", None)
        if not callable(subscribe) or not callable(unsubscribe):
            await websocket.close()
            return
        queue = await subscribe()
        connected = False

        async def _send_runtime_events() -> None:
            while True:
                event = await queue.get()
                if _ws_revoked():
                    with suppress(Exception):
                        await websocket.close(code=4401)
                    return
                await websocket.send_json(event)

        async def _revocation_watchdog() -> None:
            # Close idle revoked sockets even when no events are flowing.
            while True:
                await asyncio.sleep(15)
                if _ws_revoked():
                    with suppress(Exception):
                        await websocket.close(code=4401)
                    return

        async def _receive_until_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    raise WebSocketDisconnect

        try:
            ctx.presence.on_connect()
            connected = True
            client_name = str(websocket.query_params.get("client", "") or "").strip().lower()
            if client_name in {"background", "extension", "service-worker"}:
                from openbiliclaw.bilibili.auth import resolve_runtime_cookie
                from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

                runtime_config = getattr(ctx, "config", None) or config
                with suppress(Exception):
                    cookie = resolve_runtime_cookie(
                        data_dir=runtime_config.data_path,
                        configured_cookie=runtime_config.bilibili.cookie,
                    )
                    if not str(cookie or "").strip():
                        await websocket.send_json(
                            {
                                "type": "bilibili_cookie_sync_requested",
                                "reason": "missing_cookie",
                                "source": "runtime-stream",
                            }
                        )
                with suppress(Exception):
                    dy_cfg = getattr(runtime_config.sources, "douyin", None)
                    if dy_cfg is not None and bool(getattr(dy_cfg, "enabled", False)):
                        dy_cookie = resolve_douyin_cookie(
                            data_dir=runtime_config.data_path,
                            cookie_env=str(
                                getattr(dy_cfg, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE")
                            ),
                        )
                        if not str(dy_cookie or "").strip():
                            await websocket.send_json(
                                {
                                    "type": "douyin_cookie_sync_requested",
                                    "reason": "missing_cookie",
                                    "source": "runtime-stream",
                                }
                            )

            writer = asyncio.create_task(_send_runtime_events())
            reader = asyncio.create_task(_receive_until_disconnect())
            watchdog = asyncio.create_task(_revocation_watchdog())
            done, pending = await asyncio.wait(
                {writer, reader, watchdog},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                with suppress(asyncio.CancelledError):
                    await task
            for task in done:
                with suppress(WebSocketDisconnect):
                    task.result()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.debug("runtime-stream closed after handler exception", exc_info=True)
        finally:
            if connected:
                ctx.presence.on_disconnect()
            await unsubscribe(queue)

    @app.on_event("startup")
    async def startup_refresh_loop() -> None:
        # Prune the cover-image cache on startup (consumed + unsaved content,
        # plus aged orphans). The periodic pass runs from RefreshRuntime.
        try:
            result = cleanup_image_cache(
                database=getattr(ctx, "database", None),
                max_age_days=_IMAGE_CACHE_MAX_AGE_DAYS,
            )
            if result.removed:
                logger.info(
                    "Image cache cleanup: removed %d cover files (%.1f MB freed; "
                    "%d consumed, %d aged orphans, %d unrefetchable protected)",
                    result.removed,
                    result.freed_bytes / (1024 * 1024),
                    result.removed_consumed,
                    result.removed_aged_orphans,
                    result.protected_unrefetchable,
                )
        except Exception:
            logger.debug("Image cache cleanup failed", exc_info=True)

        # Guided-init crash recovery: fail any run left starting/running by a
        # prior crash so /api/init-status never reports a stuck running=true.
        # Must run even in degraded mode (before the early return below).
        try:
            reconciled = ctx.init_coordinator.reconcile_on_boot()
            if reconciled:
                logger.info("Reconciled %d stale guided-init run(s) on boot", reconciled)
        except Exception:
            logger.debug("Guided-init boot reconciliation failed", exc_info=True)

        if bool(getattr(ctx, "degraded", False)):
            return
        await ctx.restart_background_tasks(app)

        # Warm the reshuffle batch buffer so the first page load / 换一批 is
        # instant instead of blocking on the ~5-11s serve() pipeline. The
        # refill itself runs in the background (fire-and-forget). The soul
        # profile may not be ready at the exact startup instant, so retry
        # for a short window before giving up.
        async def _warm_batch_buffer() -> None:
            _engine = getattr(ctx, "recommendation_engine", None)
            _soul = getattr(ctx, "soul_engine", None)
            if _engine is None or _soul is None:
                return
            for _attempt in range(12):
                _prof = None
                with suppress(Exception):
                    _prof = await _soul.get_profile()
                if _prof is not None:
                    _engine.prefetch_batch_buffer(profile=_prof, platform=None, limit=10)
                    return
                await asyncio.sleep(3)

        try:
            _loop = asyncio.get_running_loop()
            _loop.create_task(_warm_batch_buffer())
        except RuntimeError:
            pass

    @app.on_event("shutdown")
    async def shutdown_refresh_loop() -> None:
        feedback_scheduler = getattr(app.state, "feedback_batch_scheduler", None)
        if feedback_scheduler is not None:
            with suppress(Exception):
                await feedback_scheduler.close()
        # These loops run on their own thread + event loop (see
        # ``RuntimeContext._spawn_background_loop``). Awaiting a task owned by
        # another loop raises "got Future ... attached to a different loop" and
        # aborts the lifespan shutdown ("Application shutdown failed.
        # Exiting."), so cancel them on their own loop and let the owning
        # thread unwind instead.
        for attr in ("refresh_task", "account_sync_task", "auto_update_task"):
            task = getattr(app.state, attr, None)
            if task is None:
                continue
            thread = getattr(app.state, f"{attr}_thread", None)
            try:
                foreign = task.get_loop() is not asyncio.get_running_loop()
            except Exception:
                foreign = True
            if foreign:
                with suppress(RuntimeError):
                    task.get_loop().call_soon_threadsafe(task.cancel)
                if thread is not None and thread.is_alive():
                    thread.join(timeout=2.0)
            else:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    @app.get("/api/profile-summary", response_model=ProfileSummaryResponse)
    async def profile_summary(
        limit: int = Query(default=3, ge=1, le=20),
        cursor: str = "",
    ) -> ProfileSummaryResponse:
        try:
            profile = await ctx.soul_engine.get_profile()
        except Exception:
            return ProfileSummaryResponse(initialized=False)

        overrides_summary: dict[str, object] = {}
        _get_overrides = getattr(ctx.soul_engine, "get_overrides", None)
        if callable(_get_overrides):
            try:
                overrides_summary = _get_overrides().to_dict()
            except Exception:
                overrides_summary = {}

        # User-added entries per field, so the display caps below never hide a
        # manual edit (it would otherwise show in edit mode but not here).
        _list_edits = overrides_summary.get("list_edits", {})
        _interest_edits = overrides_summary.get("interest_edits", {})

        def _added_list(path: str) -> list[str]:
            edit = _list_edits.get(path) if isinstance(_list_edits, dict) else None
            add = edit.get("add", []) if isinstance(edit, dict) else []
            return [str(x) for x in add] if isinstance(add, list) else []

        def _added_domains(polarity: str) -> list[str]:
            edit = _interest_edits.get(polarity) if isinstance(_interest_edits, dict) else None
            domains = edit.get("add_domains", []) if isinstance(edit, dict) else []
            if not isinstance(domains, list):
                return []
            return [str(d.get("domain", "")) for d in domains if isinstance(d, dict)]

        from openbiliclaw.api.models import (
            AwarenessNoteOut,
            ContextModeOut,
            InsightHypothesisOut,
            InterestDomainOut,
            InterestSpecificOut,
            MBTIDimensionOut,
            MBTIOut,
            SpeculativeAvoidanceOut,
            SpeculativeInterestOut,
            SpeculativeSpecificOut,
            StylePreferenceOut,
        )
        from openbiliclaw.soul.avoidance_speculator import load_avoidance_state
        from openbiliclaw.soul.speculator import load_speculative_state

        prefs = profile.preferences

        # ── Core layer ──
        mbti_obj = getattr(getattr(profile, "core", None), "mbti", None)
        mbti_out = MBTIOut()
        mbti_type = str(getattr(mbti_obj, "type", "") or "") if mbti_obj is not None else ""
        if mbti_type:
            mbti_out = MBTIOut(
                type=mbti_type,
                dimensions={
                    k: MBTIDimensionOut(pole=str(v.pole), strength=float(v.strength))
                    for k, v in getattr(mbti_obj, "dimensions", {}).items()
                },
                confidence=float(getattr(mbti_obj, "confidence", 0.0)),
            )

        # ── Interest layer (tree structure) ──
        interest_layer = getattr(profile, "interest", None)

        def _domain_list(raw_domains: object) -> list[InterestDomainOut]:
            if not isinstance(raw_domains, list):
                return []
            return [
                InterestDomainOut(
                    domain=str(getattr(d, "domain", "")),
                    weight=float(getattr(d, "weight", 0.5)),
                    specifics=[
                        InterestSpecificOut(
                            name=str(getattr(s, "name", "")),
                            weight=float(getattr(s, "weight", 0.5)),
                        )
                        for s in getattr(d, "specifics", [])
                        if str(getattr(s, "name", "")).strip()
                    ],
                )
                for d in raw_domains
                if str(getattr(d, "domain", "")).strip()
            ]

        likes_out = _cap_keeping_user_added(
            _domain_list(getattr(interest_layer, "likes", [])),
            _added_domains("likes"),
            12,
            key=lambda d: d.domain,
        )
        dislikes_out = _cap_keeping_user_added(
            _domain_list(getattr(interest_layer, "dislikes", [])),
            _added_domains("dislikes"),
            8,
            key=lambda d: d.domain,
        )

        favorite_ups = _cap_keeping_user_added(
            [
                str(item).strip()
                for item in getattr(prefs, "favorite_up_users", [])
                if str(item).strip()
            ],
            _added_list("interest.favorite_up_users"),
            8,
        )

        # ── Surface layer ──
        style_raw = getattr(prefs, "style", None)
        style_out = StylePreferenceOut()
        if style_raw is not None:
            style_out = StylePreferenceOut(
                preferred_duration=str(getattr(style_raw, "preferred_duration", "")),
                preferred_pace=str(getattr(style_raw, "preferred_pace", "")),
                quality_sensitivity=float(getattr(style_raw, "quality_sensitivity", 0.5)),
                humor_preference=float(getattr(style_raw, "humor_preference", 0.5)),
                depth_preference=float(getattr(style_raw, "depth_preference", 0.5)),
            )
        ctx_raw = getattr(prefs, "context", None)
        ctx_out = ContextModeOut()
        if ctx_raw is not None:
            ctx_out = ContextModeOut(
                weekday_patterns=str(getattr(ctx_raw, "weekday_patterns", "")),
                weekend_patterns=str(getattr(ctx_raw, "weekend_patterns", "")),
                time_of_day_patterns=str(getattr(ctx_raw, "time_of_day_patterns", "")),
                session_type=str(getattr(ctx_raw, "session_type", "")),
            )

        exploration_openness = float(getattr(prefs, "exploration_openness", 0.5))

        # ── Cognition updates ──
        cognition_updates = []
        has_more_cognition_updates = False
        next_cognition_cursor = ""
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            raw_updates = [
                item
                for item in load_cognition_updates()
                if isinstance(item, dict) and str(item.get("summary", "")).strip()
            ]
            raw_updates.sort(key=lambda item: str(item.get("created_at", "")).strip(), reverse=True)
            raw_updates.sort(key=lambda item: bool(item.get("notified", False)))
            try:
                start = max(int(cursor), 0)
            except ValueError:
                start = 0
            end = start + limit
            sliced_updates = raw_updates[start:end]
            has_more_cognition_updates = end < len(raw_updates)
            next_cognition_cursor = str(end) if has_more_cognition_updates else ""
            cognition_updates = [_normalize_cognition_update(item) for item in sliced_updates]

        # ── Speculative interests ──
        spec_items: list[SpeculativeInterestOut] = []
        avoidance_items: list[SpeculativeAvoidanceOut] = []
        runtime_config = getattr(ctx, "config", None) or config
        try:
            spec_state = load_speculative_state(runtime_config.data_path)

            # Filter status="active" only — confirmed/rejected items are
            # technically still in spec_state.active until force_tick rotates
            # them out, but the popup should not surface them: a user who
            # clicked 喜欢 has already given their answer and expects the
            # row to disappear, not to re-render with a "已确认" tag.
            active_specs = [item for item in spec_state.active if item.status == "active"]
            for item in active_specs[:6]:
                probe_mode, challenge = _probe_metadata_for_payload(item)
                spec_items.append(
                    SpeculativeInterestOut(
                        domain=item.domain,
                        reason=item.reason,
                        confidence=item.confidence,
                        probe_mode=probe_mode,
                        challenge=challenge,
                        confirmation_count=item.confirmation_count,
                        confirmation_threshold=item.confirmation_threshold,
                        status=item.status,
                        specifics=[
                            SpeculativeSpecificOut(
                                name=s.name,
                                confirmation_count=s.confirmation_count,
                            )
                            for s in item.specifics
                            if s.name.strip()
                        ],
                    )
                )
        except Exception:
            logger.debug("Failed to load speculative state for profile summary")

        # ── Speculative avoidances ──
        try:
            avoidance_state = load_avoidance_state(runtime_config.data_path)
            active_avoidances = [item for item in avoidance_state.active if item.status == "active"]
            avoidance_items = [
                SpeculativeAvoidanceOut(
                    domain=item.domain,
                    reason=item.reason,
                    confidence=item.confidence,
                    source_mode=item.source_mode,
                    source_signal=item.source_signal,
                    confirmation_count=item.confirmation_count,
                    confirmation_threshold=item.confirmation_threshold,
                    status=item.status,
                    specifics=[
                        SpeculativeSpecificOut(
                            name=s.name,
                            confirmation_count=s.confirmation_count,
                        )
                        for s in item.specifics
                        if s.name.strip()
                    ],
                )
                for item in active_avoidances[:6]
            ]
        except Exception:
            logger.debug("Failed to load avoidance state for profile summary")

        active_insights_out = [
            InsightHypothesisOut(
                hypothesis=str(getattr(ins, "hypothesis", "")),
                evidence=[str(e) for e in getattr(ins, "evidence", [])],
                confidence=float(getattr(ins, "confidence", 0.5)),
                validated=bool(getattr(ins, "validated", False)),
                created_at=str(getattr(ins, "created_at", "")),
            )
            for ins in getattr(profile, "active_insights", [])[:6]
            if str(getattr(ins, "hypothesis", "")).strip()
        ]

        recent_awareness_out = [
            AwarenessNoteOut(
                date=str(getattr(note, "date", "")),
                observation=str(getattr(note, "observation", "")),
                trend=str(getattr(note, "trend", "")),
                emotion_guess=str(getattr(note, "emotion_guess", "")),
            )
            for note in getattr(profile, "recent_awareness", [])[:8]
            if str(getattr(note, "observation", "")).strip()
        ]

        return ProfileSummaryResponse(
            initialized=True,
            personality_portrait=profile.personality_portrait,
            # Core
            core_traits=_cap_keeping_user_added(
                profile.core_traits, _added_list("core.core_traits"), 6
            ),
            deep_needs=_cap_keeping_user_added(
                profile.deep_needs, _added_list("core.deep_needs"), 5
            ),
            mbti=mbti_out,
            # Values
            values=_cap_keeping_user_added(
                list(getattr(profile, "values", [])), _added_list("values_layer.values"), 5
            ),
            motivational_drivers=_cap_keeping_user_added(
                list(getattr(profile, "motivational_drivers", [])),
                _added_list("values_layer.motivational_drivers"),
                4,
            ),
            # Interest
            likes=likes_out,
            dislikes=dislikes_out,
            favorite_up_users=favorite_ups,
            # Role
            life_stage=str(getattr(profile, "life_stage", "")),
            current_phase=str(getattr(profile, "current_phase", "")),
            # Surface
            cognitive_style=_cap_keeping_user_added(
                list(getattr(profile, "cognitive_style", [])),
                _added_list("surface.cognitive_style"),
                5,
            ),
            style=style_out,
            context=ctx_out,
            exploration_openness=exploration_openness,
            # Cross-cutting
            speculative_interests=spec_items,
            speculative_avoidances=avoidance_items,
            recent_cognition_updates=cognition_updates,
            has_more_cognition_updates=has_more_cognition_updates,
            next_cognition_cursor=next_cognition_cursor,
            active_insights=active_insights_out,
            recent_awareness=recent_awareness_out,
            overrides=overrides_summary,
        )

    @app.get("/api/profile/edit-state")
    async def profile_edit_state() -> dict[str, object]:
        """Full (un-truncated) editable profile + overrides + drift.

        The edit UI must use this rather than ``/api/profile-summary`` — the
        latter truncates lists for display, so it cannot reach e.g. the 13th
        interest or 9th UP.
        """
        from openbiliclaw.soul.overrides import build_edit_state

        try:
            raw = await ctx.soul_engine.get_raw_profile()
            effective = await ctx.soul_engine.get_profile()
        except Exception:
            return {"initialized": False}
        return build_edit_state(raw, effective, ctx.soul_engine.get_overrides())

    @app.post("/api/profile/edit")
    async def profile_edit(payload: ProfileEditIn) -> dict[str, object]:
        """Apply one deterministic user edit to the profile overlay.

        Returns the fresh edit-state inline so the client re-renders without
        a second round-trip. Embedding / LLM services for the dislike pool
        purge are resolved inside ``apply_user_edit`` from the soul engine.
        """
        from openbiliclaw.soul.overrides import ProfileEditError, build_edit_state

        try:
            await ctx.soul_engine.apply_user_edit(
                target=payload.target,
                op=payload.op,
                value=payload.value,
                parent=payload.parent,
                weight=payload.weight,
                database=ctx.database,
            )
        except ProfileEditError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        try:
            raw = await ctx.soul_engine.get_raw_profile()
            effective = await ctx.soul_engine.get_profile()
            edit_state: dict[str, object] = build_edit_state(
                raw, effective, ctx.soul_engine.get_overrides()
            )
        except Exception:
            edit_state = {"initialized": False}
        return {"ok": True, "target": payload.target, "op": payload.op, "edit_state": edit_state}

    @app.post("/api/events", response_model=EventIngestResponse)
    async def ingest_events(payload: BehaviorEventBatchIn) -> EventIngestResponse:
        from openbiliclaw.sources.event_format import build_event

        if _health_profile_ready() is False:
            return EventIngestResponse(
                accepted=0,
                rejected=[
                    EventRejectedOut(
                        index=index,
                        type=str(item.type or "").strip(),
                        reason="not_initialized",
                    )
                    for index, item in enumerate(payload.events)
                ],
            )

        latest_event_id_before_ingest = 0
        get_latest_event_id = getattr(ctx.database, "get_latest_event_id", None)
        if callable(get_latest_event_id):
            with suppress(Exception):
                latest_event_id_before_ingest = _event_cursor_value(get_latest_event_id())

        accepted = 0
        accepted_events: list[dict[str, Any]] = []
        rejected: list[EventRejectedOut] = []
        for index, item in enumerate(payload.events):
            source_platform = (item.source_platform or "bilibili").strip() or "bilibili"
            raw_event_type = str(item.type or "").strip()
            event_type = "feedback" if raw_event_type == "dislike" else raw_event_type
            # Coerce context to a string for downstream LLM consumers.
            # Pre-v0.3.22 this passed item.context through verbatim — when
            # the extension sent a dict (e.g. structured click context),
            # database serialization stored it as a JSON blob and prompt
            # builders surfaced "[object Object]"-like noise. build_event
            # fills in a natural-language fallback when context is empty
            # / non-string.
            raw_context = item.context
            if isinstance(raw_context, str):
                context_str = raw_context.strip()
            elif raw_context is None:
                context_str = ""
            else:
                # Dict / list / other — fold into metadata so it's
                # preserved without polluting the LLM-facing context.
                context_str = ""
            metadata = {
                **item.metadata,
                "timestamp": item.timestamp,
            }
            if raw_event_type == "dislike":
                metadata.setdefault("feedback_type", "dislike")
                metadata.setdefault("reaction", "thumbs_down")
            if not isinstance(raw_context, str) and raw_context:
                metadata.setdefault("raw_context", raw_context)
            # v0.3.x event-satisfaction: fold top-level dwell into
            # metadata so the storage classifier sees them in one place.
            # `setdefault` preserves an explicit metadata.watch_seconds
            # the extension might already have set inside metadata.
            if item.watch_seconds is not None:
                metadata.setdefault("watch_seconds", item.watch_seconds)
            if item.video_duration_seconds is not None:
                metadata.setdefault("video_duration_seconds", item.video_duration_seconds)
            event = build_event(
                event_type=event_type,
                source_platform=source_platform,
                title=item.title or "",
                url=item.url or "",
                author=str(metadata.get("author", "") or metadata.get("up_name", "") or ""),
                context=context_str,
                metadata=metadata,
            )
            try:
                await ctx.memory_manager.propagate_event(event)
            except ValueError as exc:
                rejected.append(
                    EventRejectedOut(
                        index=index,
                        type=raw_event_type,
                        reason=str(exc),
                    )
                )
                continue
            accepted += 1
            accepted_events.append(event)
        if accepted_events:
            await _backfill_pending_discovery_events_to_profile_pipeline(
                max_event_id=latest_event_id_before_ingest
            )
            await _ingest_profile_update_events(accepted_events)
        if accepted > 0:
            await _request_runtime_replenishment(reason="event_ingest")
        # Notify popup that the activity feed has new entries so it can
        # refresh its UI without polling. Throttled naturally to once per
        # ingest call (extension batches 10+ events into a single POST).
        if accepted > 0:
            event_hub = getattr(ctx.runtime_controller, "event_hub", None)
            publish = getattr(event_hub, "publish", None)
            if callable(publish):
                with suppress(Exception):
                    await publish(
                        {
                            "type": "activity.added",
                            "count": accepted,
                        }
                    )
        return EventIngestResponse(accepted=accepted, rejected=rejected)

    @app.get("/api/delight/pending", response_model=PendingDelightResponse)
    async def pending_delight() -> PendingDelightResponse:
        get_pending_delight = getattr(ctx.runtime_controller, "get_pending_delight", None)
        item = get_pending_delight() if callable(get_pending_delight) else None
        if item is None:
            return PendingDelightResponse(item=None)
        return PendingDelightResponse(item=PendingDelightOut(**item))

    @app.get("/api/delight/pending-batch")
    async def pending_delight_batch(limit: int | None = None) -> dict[str, Any]:
        """Return un-notified delight candidates.

        When ``limit`` is omitted the shared
        ``scheduler.delight_queue_limit`` setting decides the queue size.
        Unlike ``/api/delight/pending`` this ignores the 4-hour
        notification cooldown — it's intended for the popup to
        re-hydrate the full queue on init, not for active push gating.
        Honors ``disliked_topics`` substring filter same as the singular
        endpoint.

        ``include_liked=True``: a liked delight keeps its queue slot across
        re-hydration (popup reopen / delight.refreshed) instead of silently
        vanishing — positive feedback keeps the card visible until the user
        dismisses it. Such rows come back with ``state="liked"`` so clients
        render the already-liked treatment.
        """
        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        configured_limit = getattr(
            getattr(getattr(ctx, "config", None), "scheduler", None),
            "delight_queue_limit",
            20,
        )
        requested_limit = configured_limit if limit is None else limit
        rows = ctx.database.get_delight_candidates(
            min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            limit=max(1, min(100, int(requested_limit))),
            include_liked=True,
        )
        # Reuse the same disliked-topic filter as get_pending_delight by
        # going through the runtime controller's loader if possible.
        controller = ctx.runtime_controller
        load_phrases = getattr(controller, "_load_disliked_topic_phrases", None)
        disliked_phrases = load_phrases() if callable(load_phrases) else []

        def passes_filter(row: dict[str, Any]) -> bool:
            haystack = f"{str(row.get('title', '')).lower()} {str(row.get('tags', '')).lower()}"
            return not any(p and p in haystack for p in disliked_phrases)

        items = [
            {
                "bvid": str(row.get("bvid", "")),
                "title": str(row.get("title", "")),
                "delight_reason": str(row.get("delight_reason", "")),
                "delight_score": float(row.get("delight_score", 0.0) or 0.0),
                "delight_hook": str(row.get("delight_hook", "")),
                "cover_url": str(row.get("cover_url", "")),
                "content_url": str(row.get("content_url", "")),
                "source_platform": str(row.get("source_platform", "bilibili")),
                "state": (
                    "liked" if str(row.get("feedback_type", "") or "") == "like" else "pending"
                ),
            }
            for row in rows
            if passes_filter(row)
        ]
        return {"items": items}

    @app.post("/api/delight/respond")
    async def respond_to_delight(payload: dict[str, Any]) -> Any:
        """User responds to a delight (surprise) recommendation.

        Body:
        ``{ "bvid": "...", "title": "...", "response": "view"|"like"|"dislike"|"chat",
        "message": "..." }``. ``like`` / ``chat`` update learning signals and keep
        the delight in the queue; ``view`` keeps the card visible in-session but
        marks the candidate read (same semantics as the recommendation pool's
        ``shown`` flag — a browsed surprise doesn't reappear on the next queue
        re-hydration); ``dismiss`` and ``dislike`` consume the candidate
        immediately.
        """
        from fastapi.responses import JSONResponse

        bvid = str(payload.get("bvid", "")).strip()
        title = str(payload.get("title", "")).strip()
        response_type = str(payload.get("response") or "").strip().lower()
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required")
        if response_type not in {"view", "like", "dislike", "chat", "dismiss"}:
            raise HTTPException(
                status_code=422,
                detail="response must be view, like, dislike, chat, or dismiss",
            )

        def mark_delight_consumed() -> None:
            mark_sent = getattr(ctx.runtime_controller, "mark_delight_sent", None)
            if callable(mark_sent):
                mark_sent(bvid)
            else:
                ctx.database.mark_delight_notified(bvid)

        if response_type == "view":
            # Browsing the content marks the candidate read — mirrors the
            # recommendation pool, where a served item flips to 'shown' and
            # is never re-served. The card keeps its in-session "viewed"
            # treatment; it just stops re-hydrating on the next queue load.
            # Direct DB mark (not mark_delight_consumed): viewing must not
            # bump the 4h proactive-push cooldown — engaging with one
            # surprise shouldn't delay discovery of the next.
            try:
                ctx.database.mark_delight_notified(bvid)
            except Exception:
                logger.debug("Failed to mark viewed delight bvid %s", bvid)
            return JSONResponse(content={"ok": True, "action": "viewed", "bvid": bvid})

        if response_type == "dismiss":
            try:
                mark_delight_consumed()
            except Exception:
                logger.debug("Failed to dismiss delight bvid %s", bvid)
            return JSONResponse(content={"ok": True, "action": "dismissed", "bvid": bvid})

        if response_type == "like":
            # User marks this delight as liked WITHOUT having opened the
            # video. Treat as a strong positive feedback signal: boost
            # the row's relevance score and record a cognition update so
            # downstream scoring + UI both reflect the preference.
            try:
                ctx.database._execute_write(
                    "UPDATE content_cache SET feedback_type='like', "
                    "feedback_at=CURRENT_TIMESTAMP, "
                    "relevance_score=MIN(1.0, COALESCE(relevance_score, 0.5) + 0.15) "
                    "WHERE bvid = ?",
                    (bvid,),
                )
            except Exception:
                logger.debug("Failed to record delight like for %s", bvid)
            label = title or bvid
            _record_probe_cognition(
                f"你喜欢惊喜推荐「{label}」，会多挖类似的。",
                bvid,
                "delight_like",
            )
            await _publish_probe_event(
                "delight.liked",
                f"好，「{label}」这类多来点。",
                bvid,
            )
            _record_exploration_buffer_event(
                domain=label,
                source_event="card_more_like",
                evidence_id=bvid,
            )
            return JSONResponse(content={"ok": True, "action": "liked", "bvid": bvid})

        if response_type == "dislike":
            try:
                ctx.database._execute_write(
                    "UPDATE content_cache SET pool_status = 'purged_by_dislike', "
                    "feedback_type='dislike', feedback_at=CURRENT_TIMESTAMP "
                    "WHERE bvid = ?",
                    (bvid,),
                )
                mark_delight_consumed()
            except Exception:
                logger.debug("Failed to purge delight bvid %s", bvid)
            label = title or bvid
            _record_probe_cognition(
                f"你对惊喜推荐「{label}」不感兴趣。",
                bvid,
                "delight_dislike",
            )
            await _publish_probe_event(
                "delight.disliked",
                f"好，「{label}」这类先不推了。",
                bvid,
            )
            _record_exploration_buffer_event(
                domain=label,
                source_event="negative",
                evidence_id=bvid,
            )
            return JSONResponse(content={"ok": True, "action": "disliked", "bvid": bvid})

        # Chat
        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"聊聊你为什么觉得「{title or bvid}」我会喜欢"
        contextual_message = f"[关于惊喜推荐「{title or bvid}」的反馈] {raw_message}"
        if ctx.dialogue is None:
            return JSONResponse(
                content={"ok": False, "action": "chat", "bvid": bvid, "reply": "对话引擎暂不可用。"}
            )
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            reply = await asyncio.wait_for(ctx.dialogue.respond(contextual_message), timeout=30)
        except TimeoutError:
            return JSONResponse(
                content={
                    "ok": False,
                    "action": "chat",
                    "bvid": bvid,
                    "reply": "后台正忙，等一下再聊。",
                }
            )
        except Exception:
            logger.exception("Dialogue failed for delight chat: %s", bvid)
            return JSONResponse(
                content={
                    "ok": False,
                    "action": "chat",
                    "bvid": bvid,
                    "reply": "聊天出了点问题，稍后再试。",
                }
            )
        finally:
            if concurrency is not None:
                concurrency.chat_active = False
        label = title or bvid
        _record_probe_cognition(
            f"关于惊喜推荐「{label}」你说：{raw_message}",
            bvid,
            "delight_chat",
            detail=f"你的反馈：{raw_message}\n阿b的回复：{reply}",
        )
        await _publish_probe_event("delight.chat", f"关于「{label}」你说：{raw_message}", bvid)
        return JSONResponse(content={"ok": True, "action": "chat", "bvid": bvid, "reply": reply})

    # ── Conversational recommendation (生成式推荐第二步) ──
    _chat_recommend_sessions: dict[str, Any] = {}

    def _record_probe_cognition(
        summary: str,
        domain: str,
        action: str,
        *,
        source: str = "interest_probe",
        detail: str = "",
    ) -> None:
        """Write a cognition update so probe feedback shows in '阿b最近记住了什么'."""

        try:
            updates = ctx.memory_manager.load_cognition_updates()
            updates.append(
                {
                    "summary": summary,
                    "detail": detail or f"兴趣探针反馈：{action} — {domain}",
                    "created_at": datetime.now().isoformat(),
                    "source": source,
                    "tone": "success" if action == "confirmed" else "info",
                }
            )
            ctx.memory_manager.save_cognition_updates(updates)
        except Exception:
            logger.exception("Failed to record probe cognition update")

    async def _publish_probe_event(event_type: str, message: str, domain: str) -> None:
        """Push a probe result event via WebSocket."""
        event_hub = getattr(ctx.runtime_controller, "event_hub", None)
        publish = getattr(event_hub, "publish", None)
        if callable(publish):
            await publish(
                {
                    "type": event_type,
                    "phase": "ready",
                    "message": message,
                    "domain": domain,
                }
            )

    def _promote_exploration_buffer_entries(
        promoted: list[dict[str, object]],
    ) -> None:
        if not promoted:
            return
        from openbiliclaw.soul.interest_writeback import merge_confirmed_interest
        from openbiliclaw.soul.profile import OnionProfile

        memory_manager = getattr(ctx, "memory_manager", None)
        get_layer = getattr(memory_manager, "get_layer", None)
        if not callable(get_layer):
            return
        try:
            soul_layer = get_layer("soul")
            raw_profile = getattr(soul_layer, "data", {})
            profile = (
                OnionProfile.from_dict(raw_profile)
                if isinstance(raw_profile, dict) and raw_profile
                else OnionProfile()
            )
            changed = False
            for entry in promoted:
                raw_specifics = entry.get("specifics", [])
                specifics = (
                    [str(item) for item in raw_specifics if str(item).strip()]
                    if isinstance(raw_specifics, list)
                    else []
                )
                changed = (
                    merge_confirmed_interest(
                        profile,
                        domain=str(entry.get("domain", "")),
                        specifics=specifics,
                        source=str(entry.get("confirmation_source", "buffer_promoted")),
                        first_seen=str(entry.get("first_seen", "")),
                        last_seen=str(entry.get("last_seen", "")),
                    )
                    or changed
                )
            if not changed:
                return
            if isinstance(raw_profile, dict):
                raw_profile.clear()
                raw_profile.update(profile.to_dict())
            save = getattr(soul_layer, "save", None)
            if callable(save):
                save()
            sync_profile_files = getattr(memory_manager, "sync_profile_files", None)
            if callable(sync_profile_files):
                sync_profile_files(profile)
        except Exception:
            logger.exception("Failed to promote exploration buffer entries")

    def _record_exploration_buffer_event(
        *,
        domain: str,
        source_event: str,
        specifics: list[str] | None = None,
        evidence_id: str = "",
    ) -> None:
        from datetime import UTC

        from openbiliclaw.soul.exploration_buffer import (
            pop_promotable_buffer_entries,
            record_buffer_event,
        )

        clean_domain = domain.strip()
        if not clean_domain:
            return
        memory_manager = getattr(ctx, "memory_manager", None)
        load_state = getattr(memory_manager, "load_discovery_runtime_state", None)
        save_state = getattr(memory_manager, "save_discovery_runtime_state", None)
        update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
        if not callable(update_state) and (not callable(load_state) or not callable(save_state)):
            return
        try:
            now = datetime.now(UTC)

            promoted: list[dict[str, object]] = []

            def _mutate(state: dict[str, object]) -> None:
                nonlocal promoted
                raw_buffer_state = state.get("short_term_exploration_buffer", {})
                existing_buffer_state = (
                    raw_buffer_state if isinstance(raw_buffer_state, dict) else {}
                )
                buffer_state = record_buffer_event(
                    existing_buffer_state,
                    domain=clean_domain,
                    source_event=source_event,
                    specifics=specifics or [],
                    evidence_id=evidence_id,
                    now=now,
                )
                promoted, buffer_state = pop_promotable_buffer_entries(buffer_state, now=now)
                state["short_term_exploration_buffer"] = buffer_state

            if callable(update_state):
                update_state(_mutate)
            else:
                load_state_fn = cast("Callable[[], dict[str, object]]", load_state)
                save_state_fn = cast("Callable[[dict[str, object]], None]", save_state)
                state = load_state_fn()
                if not isinstance(state, dict):
                    state = {}
                _mutate(state)
                save_state_fn(state)
            _promote_exploration_buffer_entries(promoted)
        except Exception:
            logger.exception("Failed to record exploration buffer event")

    def _recommendation_buffer_domain(row: dict[str, object]) -> tuple[str, list[str]]:
        title = str(row.get("title", "")).strip()
        domain = (
            str(row.get("topic_group", "")).strip()
            or str(row.get("topic_label", "")).strip()
            or str(row.get("topic", "")).strip()
            or str(row.get("topic_key", "")).strip()
            or title
        )
        specifics = [title] if title and title != domain else []
        return domain, specifics

    async def list_chat_turns(
        session: str = "popup",
        scope: str = "",
        limit: int = Query(default=50, ge=1, le=200),
    ) -> ChatTurnListResponse:
        normalized_scope = _normalize_chat_scope(scope) if scope else ""
        rows = _list_chat_turn_rows(
            session=session.strip() or "popup",
            scope=normalized_scope,
            limit=limit,
        )
        return ChatTurnListResponse(items=[_normalize_chat_turn(row) for row in rows])

    async def recommendation_click(
        payload: RecommendationClickIn,
    ) -> RecommendationClickResponse:
        """Ingest a recommendation click-through as a strong profile signal.

        The click is evidence that the user actively chose to watch a
        recommended video. It is treated as a strong signal that bypasses
        the pipeline's min_signals gate and updates Interest + Surface
        immediately. If the recommendation_id resolves to a stored card,
        its metadata (title, topic, up_name) is pulled from the database
        so the payload reaches the pipeline even when the extension sends
        only a bare BV id.
        """
        from openbiliclaw.soul.pipeline import signal_from_recommendation_click

        recommendation: dict[str, object] | None = None
        if payload.recommendation_id is not None:
            recommendation = ctx.database.get_recommendation_by_id(
                payload.recommendation_id,
            )

        bvid = (payload.bvid or "").strip()
        content_id = (payload.content_id or "").strip()
        content_url = (payload.content_url or "").strip()
        source_platform_raw = (payload.source_platform or "").strip()
        title = (payload.title or "").strip()
        topic_label = (payload.topic_label or "").strip()
        up_name = (payload.up_name or "").strip()

        if recommendation is not None:
            bvid = bvid or str(recommendation.get("bvid", "") or "").strip()
            content_id = content_id or str(recommendation.get("content_id", "") or "").strip()
            content_url = content_url or str(recommendation.get("content_url", "") or "").strip()
            source_platform_raw = (
                source_platform_raw or str(recommendation.get("source_platform", "") or "").strip()
            )
            title = title or str(recommendation.get("title", "") or "").strip()
            topic_label = topic_label or str(recommendation.get("topic_label", "") or "").strip()
            up_name = up_name or str(recommendation.get("up_name", "") or "").strip()

        content_id = content_id or bvid
        bvid = bvid or content_id
        if not bvid:
            raise HTTPException(status_code=422, detail="bvid is required.")
        if not source_platform_raw:
            source_platform_raw = _infer_source_platform_from_url(content_url)
        source_platform = _normalize_source_platform(source_platform_raw)
        if not content_url:
            content_url = _fallback_recommendation_click_url(
                source_platform=source_platform,
                content_id=content_id,
                bvid=bvid,
            )

        # Persist the click as an event so history/query paths can see it.
        from openbiliclaw.sources.event_format import (
            build_event,
            format_event_context,
        )

        click_extra_parts: list[str] = []
        if topic_label:
            click_extra_parts.append(f"主题:{topic_label}")
        click_context = format_event_context(
            event_type="click",
            source_platform=source_platform,
            title=title,
            author=up_name,
            extra=",".join(click_extra_parts),
        )
        click_metadata: dict[str, object] = {
            "recommendation_id": payload.recommendation_id,
            "bvid": bvid,
            "content_id": content_id,
            "content_url": content_url,
            "source_platform": source_platform,
            "topic_label": topic_label,
            "up_name": up_name,
            "source": "recommendation_click",
        }
        # v0.3.x event-satisfaction: forward dwell so the persisted
        # click row can be classified as meaningful_dwell vs quick_exit.
        # Absent fields stay absent; storage classifier degrades to
        # unknown / missing_dwell. Storage is the single classification
        # owner — do not classify here.
        if payload.watch_seconds is not None:
            click_metadata["watch_seconds"] = payload.watch_seconds
        if payload.video_duration_seconds is not None:
            click_metadata["video_duration_seconds"] = payload.video_duration_seconds
        with suppress(Exception):
            await ctx.memory_manager.propagate_event(
                build_event(
                    event_type="click",
                    source_platform=source_platform,
                    title=title,
                    url=content_url,
                    author=up_name,
                    context=click_context,
                    metadata=click_metadata,
                )
            )
        buffer_domain, buffer_specifics = _recommendation_buffer_domain(
            {
                "title": title,
                "topic_label": topic_label,
                "bvid": bvid,
            }
        )
        _record_exploration_buffer_event(
            domain=buffer_domain,
            specifics=buffer_specifics,
            source_event="plain_click",
            evidence_id=bvid,
        )

        # Push a strong signal into the profile update pipeline.
        layers_updated: list[str] = []
        pipeline = getattr(ctx.soul_engine, "pipeline", None) if ctx.soul_engine else None
        if pipeline is not None:
            signal = signal_from_recommendation_click(
                bvid=bvid,
                title=title,
                recommendation_id=payload.recommendation_id,
                topic_label=topic_label,
                up_name=up_name,
                content_id=content_id,
                content_url=content_url,
                source_platform=source_platform,
            )
            try:
                ingest_result = await pipeline.ingest(signal)
            except Exception:
                logger.exception("Failed to ingest recommendation_click signal")
            else:
                layers_updated = [r.layer.value for r in ingest_result.layers_updated]

        # E1: close the exposure→click loop. A click-through is consumption —
        # mark the stored card presented+clicked so it stops being re-served
        # (get_recommendations(exclude_processed=True) drops clicked rows) and
        # presented_at/clicked_at yield real CTR data for online metrics.
        if payload.recommendation_id is not None:
            try:
                ctx.database.mark_recommendations_clicked([payload.recommendation_id])
            except Exception:
                logger.exception("mark_recommendations_clicked failed")

        return RecommendationClickResponse(
            ok=True,
            bvid=bvid,
            layers_updated=layers_updated,
        )

    # ── Topics (专题) ─────────────────────────────────────────────
    # User-curated collections (e.g. 广告, 去有风的地方) continuously
    # collected from multiple sites via scripts/collect_topic.py. The API
    # exposes list/detail/create and a manual "collect now" trigger.

    # ── Source recipe management endpoints ──────────────────────────


    # ── XHS observed URL ingestion endpoint ─────────────────────────

    # ── XHS self-author filter (v0.3.48+) ────────────────────────────
    #
    # XHS search / explore / saved-author paths all happily return the
    # logged-in user's own published notes. Without filtering, the
    # recommendation pool fills with content the user posted themselves
    # ("自己发的笔记被推回给自己" — observed in 2026-05-05 logs as
    # 屎屎/三花/etc. cat photos polluting the popup). The extension
    # bootstrap captures self user_id + nickname from XHS state and
    # sends it back via ``debug.xhs_bootstrap.steps[*].self_info``.
    # Backend persists in ``discovery_runtime_state["xhs_self_info"]``
    # and consults it on every ingest path.

    def _load_xhs_self_info() -> dict[str, str]:
        """Load self info from runtime state (returns empty dict on miss)."""
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return {}
        try:
            state = memory_manager.load_discovery_runtime_state()
            existing = state.get("xhs_self_info")
            if isinstance(existing, dict):
                return {
                    "user_id": str(existing.get("user_id", "") or ""),
                    "nickname": str(existing.get("nickname", "") or ""),
                }
        except Exception:
            logger.exception("Failed to load xhs self_info")
        return {}

    def _purge_self_authored_pool_items(
        database: Any,
        self_info: dict[str, str],
    ) -> int:
        """Mark every pool row authored by ``self_info.nickname`` as suppressed.

        v0.3.57+: cleans up content_cache rows that entered before the
        per-path self_info filter was wired in. Idempotent — already-
        suppressed rows are not flipped further. Returns the number of
        rows actually changed in this call.

        ``up_name`` is the column populated by ``_cache_xhs_notes`` from
        the note's ``author`` field, so the comparison mirrors the
        runtime filter exactly.
        """
        if not self_info or not hasattr(database, "conn"):
            return 0
        nickname = (self_info.get("nickname") or "").strip()
        if not nickname:
            return 0
        try:
            cursor = database.conn.execute(
                "UPDATE content_cache "
                "SET pool_status = 'suppressed' "
                "WHERE source_platform = 'xiaohongshu' "
                "  AND COALESCE(pool_status, 'fresh') = 'fresh' "
                "  AND ("
                "    LOWER(COALESCE(up_name, '')) = LOWER(?)"
                "    OR LOWER(COALESCE(author_name, '')) = LOWER(?)"
                "  )",
                (nickname, nickname),
            )
            database.conn.commit()
            return int(cursor.rowcount or 0)
        except Exception:
            logger.exception("Failed to purge self-authored xhs pool items")
            return 0

    # ── Bilibili extension search fallback endpoints ────────────────

    from openbiliclaw.sources.bili_tasks import (
        BiliTaskQueue,
    )

    _bili_task_queue: BiliTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _bili_task_queue = BiliTaskQueue(ctx.database)

    # ── XHS task queue endpoints (extension dispatcher) ──────────────

    from openbiliclaw.sources.xhs_tasks import (
        XhsCreatorStore,
        XhsTaskQueue,
    )

    # Guard: only initialise when ctx.database is a real Database (has .conn).
    # Tests that pass database=object() as a stub won't trigger table creation.
    _xhs_task_queue: XhsTaskQueue | None = None
    _xhs_creator_store: XhsCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _xhs_task_queue = XhsTaskQueue(ctx.database)
        _xhs_creator_store = XhsCreatorStore(ctx.database)

    # ── X (Twitter) account subscriptions ──────────────────────────
    # No extension round-trip: the X producer fetches each subscription
    # server-side via XCreatorStrategy. This block only owns the
    # x_creator_subscriptions table + CRUD (mirrors the XHS creators above).

    from openbiliclaw.sources.x_tasks import XCreatorStore

    _x_creator_store: XCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _x_creator_store = XCreatorStore(ctx.database)


    # ── X (Twitter) source health (spec §7) ────────────────────────
    # Surfaces the persisted health state machine so the settings UI can
    # show login / rate-limit / block status (rendered in Task 12).

    # Window for treating synced 小红书 access tokens as fresh. xsec_tokens
    # die well within a day, so /api/sources/status only reports "ready" when
    # token activity happened inside this window — older-only rows degrade to
    # the yellow "stale" state instead of staying green forever.
    _xhs_token_fresh_hours = 24

    # Human-readable detail for each X (twitter) health state, reused by the
    # unified /api/sources/status chip below.
    _x_state_detail = {
        "ok": "X 来源正常，cookie 有效。",
        "missing_cookie": "未检测到登录 —— 在浏览器登录 x.com，插件会自动同步 cookie。",
        "expired_cookie": "cookie 已过期 —— 请重新登录 x.com。",
        "rate_limited": "被限流，正在退避冷却中，稍后会自动重试。",
        "blocked": "请求被拒绝 (403) —— 账号可能受限或需要重新验证。",
    }

    # ── Douyin task queue endpoints (extension dispatcher) ──────────
    # Independent from the XHS block above by design — see
    # docs/plans/2026-05-06-douyin-bootstrap-import-design.md
    # §"Module Isolation from XHS". Different table (dy_tasks),
    # different queue class, different fail isolation.

    from openbiliclaw.sources.dy_tasks import (
        DyTaskQueue,
    )

    _dy_task_queue: DyTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _dy_task_queue = DyTaskQueue(ctx.database)

    # ── Wake-up kick endpoints ──────────────────────────────────────
    #
    # The extension's task dispatchers normally poll on a 60s
    # chrome.alarms timer. That's fine for the steady state but
    # introduces a 0–60s wait between CLI enqueue and extension pickup,
    # which racing init's 30s collect window is the actual reason init
    # sometimes prints "扩展未连接或任务仍在后台跑". These endpoints let
    # the CLI broadcast a wake-up event over the existing
    # /api/runtime-stream WebSocket so the dispatcher polls immediately
    # instead of waiting for the next alarm. The 60s alarm stays as
    # fallback for the WS-down case.

    # TEMP DEBUG: extension-side log relay. Lets the service-worker
    # dispatcher POST debug events here so they end up in the daemon
    # log alongside backend-side activity. Will be reverted before
    # release.

    # ── YouTube bootstrap endpoints ────────────────────────────────
    from openbiliclaw.sources.yt_tasks import (
        YtTaskQueue,
    )
    from openbiliclaw.sources.zhihu_tasks import (
        ZhihuTaskQueue,
    )

    _zhihu_task_queue: ZhihuTaskQueue | None = None
    db_conn = getattr(ctx.database, "conn", None)
    if hasattr(db_conn, "executescript"):
        _zhihu_task_queue = ZhihuTaskQueue(ctx.database)

    _yt_task_queue: YtTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _yt_task_queue = YtTaskQueue(ctx.database)

    async def extension_e2e_run(
        request: Request,
        payload: ExtensionE2ERunIn,
    ) -> ExtensionE2ERunOut:
        """Local-only control plane for extension E2E simulation runs."""
        if not _get_auth_gate().is_trusted_local(request):
            raise HTTPException(status_code=403, detail="local_only")

        registry = cast("dict[str, _ExtensionE2ERunState]", app.state.extension_e2e_runs)
        if registry:
            raise HTTPException(status_code=409, detail="e2e_run_in_progress")

        expected_actions = _extension_e2e_actions_for_request(payload)
        if not payload.allow_state_changing:
            blocked_actions = sorted(
                {
                    action
                    for actions in expected_actions.values()
                    for action in actions
                    if action in _E2E_STATE_CHANGING_ACTIONS
                }
            )
            if blocked_actions:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "allow_state_changing must be true for actions: "
                        + ", ".join(blocked_actions)
                    ),
                )

        run_id = f"e2e-{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(32)
        after_event_id = _latest_e2e_event_id(ctx)
        state = _ExtensionE2ERunState(
            run_id=run_id,
            token=token,
            started_at=time.time(),
            after_event_id=after_event_id,
            expected_actions=expected_actions,
            event=asyncio.Event(),
        )
        registry[run_id] = state
        timed_out = False

        try:
            publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
            if not callable(publish):
                state.error = "extension_runtime_unavailable"
            else:
                delivered = await publish(
                    {
                        "type": "extension_e2e_run",
                        "source": "api",
                        "run_id": run_id,
                        "token": token,
                        "platforms": list(expected_actions.keys()),
                        "actions": {
                            platform: list(actions)
                            for platform, actions in expected_actions.items()
                        },
                        "allow_state_changing": payload.allow_state_changing,
                        "timeout_seconds": payload.timeout_seconds,
                    }
                )
                if delivered is False:
                    state.error = "extension_runtime_unavailable"

            if not state.error:
                try:
                    await asyncio.wait_for(state.event.wait(), timeout=payload.timeout_seconds)
                except TimeoutError:
                    timed_out = True

            events = _query_e2e_events(ctx, after_event_id=after_event_id)
            return _build_extension_e2e_report(
                state,
                events,
                timed_out=timed_out,
                timeout_seconds=payload.timeout_seconds,
            )
        finally:
            registry.pop(run_id, None)


    # ── Configuration management endpoints ──────────────────────────

    # v0.3.57+: one-shot purge of self-authored xhs pool rows that
    # accumulated before the per-path filter was wired in. No-op on
    # fresh installs (no persisted self_info → nothing to scan against);
    # repairs the pool the first time the user upgrades after having
    # browsed XHS while logged in.
    _existing_self_info = _load_xhs_self_info()
    if _existing_self_info:
        _purged = _purge_self_authored_pool_items(ctx.database, _existing_self_info)
        if _purged:
            logger.info(
                "startup purge: suppressed %d self-authored xhs pool item(s) (nickname=%r)",
                _purged,
                _existing_self_info.get("nickname", ""),
            )

    # ── Subscription management routes ────────────────────────────

    def get_article(article_id: int) -> JSONResponse:
        """Fetch a single article with its full body text for reading."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        row = database.get_article(article_id)
        if row is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        return JSONResponse({"ok": True, "article": row})

    def add_article_note(article_id: int, payload: ArticleNoteIn) -> JSONResponse:
        """Add a note / highlight to an article."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if database.get_article(article_id) is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        note_id = database.add_article_note(
            article_id,
            quote=payload.quote,
            note=payload.note,
            color=payload.color,
        )
        if note_id is None:
            return JSONResponse({"ok": False, "error": "failed to save note"}, status_code=500)
        return JSONResponse({"ok": True, "id": note_id})


    # ── 知识库概念反向索引 API ─────────────────────────────────
    # ── 日记系统 API ─────────────────────────────────────────────

    _diary_service: DiaryService | None = None

    # ─── 日记数据洞察 API ───────────────────────────────────────────


    # ─── 日记反思 API（周报/月度反思/年度回顾/里程碑） ─────────────────

    # ─── 知识图谱 API（标签关联+人物关系+知识网络） ─────────────────







    # ─── 自进化 API（夜间自我改进循环） ─────────────────────────────








    # ─── 主动洞察引擎 API（第二阶段） ────────────────────────────────









    # ─── 三层记忆系统 API（第三阶段） ────────────────────────────────

    # ─── 情绪系统（效价/唤醒二维模型）API ────────────────────────────

    # ─── 高级记忆系统（6层记忆 + 信念 + 巩固）API ───────────────────

    # ─── 智能时间线卡片 API ───────────────────────────────────────────


    # ── 日记标签与人物提取 API ────────────────────────────────



    # ─── RAG 语义搜索与问答 API ─────────────────────────────────────

    _diary_rag_service = None

    # ── Route registration (集中到 _route_registry.py) ─────────
    register_all_routes(
        app,
        ctx,
        config,
        fire_and_forget_tasks=_fire_and_forget_tasks,
        serialize_recommendation_items=_serialize_recommendation_items,
        config_save_lock=_CONFIG_SAVE_LOCK,
        init_active_now=_init_active_now,
        schedule_post_feedback_tasks=_schedule_post_feedback_tasks,
        record_exploration_buffer_event=_record_exploration_buffer_event,
        recommendation_buffer_domain=_recommendation_buffer_domain,
        get_auth_gate=_get_auth_gate,
        ingest_profile_update_events=_ingest_profile_update_events,
        snapshot_config_file=_snapshot_config_file,
        restore_config_snapshot=_restore_config_snapshot,
        pick_best_xhs_url=_pick_best_xhs_url,
        load_interest_keywords=_load_interest_keywords,
        request_runtime_replenishment=_request_runtime_replenishment,
        build_recommendation_router=build_recommendation_router,
    )

    # ── Web UI routes and static mounts ───────────────────────
    register_web_ui_routes(app, ctx)

    return app
