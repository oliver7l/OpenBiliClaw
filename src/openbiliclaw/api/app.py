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
from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path as _Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote, urlparse

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from openbiliclaw.api.models import (
    ActivityFeedItemOut,
    ActivityFeedResponse,
    ArticleNoteIn,
    ArticleUpdateIn,
    AutostartApplyIn,
    AutostartConfigOut,
    AutostartStatusOut,
    BackendUpdateStatusOut,
    BehaviorEventBatchIn,
    BilibiliConfigOut,
    BilibiliCookieIn,
    BilibiliCookieResponse,
    BilibiliSourceConfigOut,
    ChatIn,
    ChatRecommendIn,
    ChatRecommendResponse,
    ChatTurnIn,
    ChatTurnListResponse,
    ChatTurnOut,
    CognitionUpdateSeenIn,
    CognitionUpdateSeenResponse,
    CognitionUpdateSummary,
    ConfigIssueOut,
    ConfigResponse,
    ConfigServiceProbeIn,
    ConfigServiceProbeResponse,
    ConfigUpdateIn,
    ConfigUpdateResponse,
    DelightAckIn,
    DelightAckResponse,
    DiscoveryCandidateStats,
    DiscoveryConfigOut,
    DouyinCookieIn,
    DouyinCookieResponse,
    DouyinSourceConfigOut,
    EmbeddingConfigOut,
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
    FavoriteAddIn,
    FavoriteItem,
    FavoriteListResponse,
    FavoriteStateResponse,
    FeedbackIn,
    FeedbackResponse,
    HealthResponse,
    InitPrerequisitesOut,
    InitStageOut,
    InitStatusOut,
    InsightFeedbackIn,
    InsightFeedbackResponse,
    LLMConfigOut,
    LLMProviderConfigOut,
    LLMUsageSummary,
    LoggingConfigOut,
    ModuleLLMConfigOut,
    NotificationAckIn,
    NotificationAckResponse,
    ObservabilityResponse,
    PendingCognitionUpdateOut,
    PendingCognitionUpdateResponse,
    PendingDelightOut,
    PendingDelightResponse,
    PendingNotificationOut,
    PendingNotificationResponse,
    PlatformPoolStats,
    PoolPipelineStats,
    ProfileEditIn,
    ProfileSummaryResponse,
    RecommendationClickIn,
    RecommendationClickResponse,
    RecommendationOut,
    RuntimeStatusResponse,
    SchedulerConfigOut,
    ScoreDistribution,
    SourceCredentialItem,
    SourcesBrowserConfigOut,
    SourcesConfigOut,
    SourcesCredentialsResponse,
    SourceShareSuggestionIn,
    SourceShareSuggestionResponse,
    SourcesStatusResponse,
    SourceStatusItem,
    StorageConfigOut,
    SubscriptionAddIn,
    SubscriptionDeleteIn,
    SubscriptionItemOut,
    SubscriptionListOut,
    SubscriptionStatsOut,
    TopicCreateIn,
    TopicGroupStats,
    TwitterSourceConfigOut,
    UpdateApplyIn,
    UpdateCheckIn,
    UpdateStatusResponse,
    WatchLaterAddIn,
    WatchLaterItem,
    WatchLaterListResponse,
    WatchLaterStateResponse,
    XCookieIn,
    XCookieResponse,
    XiaohongshuSourceConfigOut,
    XStatusResponse,
    YoutubeSourceConfigOut,
    ZhihuSourceConfigOut,
)
from openbiliclaw.diary import DiaryEntryCreate, DiaryEntryUpdate, DiaryService, MoodLevel
from openbiliclaw.diary.importer import DiaryImporter
from openbiliclaw.health import (
    AllergyCreate,
    AppointmentCreate,
    AppointmentUpdate,
    ConditionCreate,
    ConditionUpdate,
    DoctorCreate,
    DoctorUpdate,
    EncounterCreate,
    EncounterUpdate,
    HealthDocumentCreate,
    HealthDocumentUpdate,
    HealthInsightCreate,
    HealthService,
    ImmunizationCreate,
    LabResultCreate,
    LabResultUpdate,
    MedicationCreate,
    MedicationLogCreate,
    MedicationUpdate,
    PatientCreate,
    PatientUpdate,
    ProcedureCreate,
    ProcedureUpdate,
    VitalsCreate,
)
from openbiliclaw.runtime.feedback_scheduler import FeedbackBatchScheduler
from openbiliclaw.runtime.image_cache import (
    CoverFetchError,
    cleanup_image_cache,
    fetch_cover_bytes,
    save_image_bytes,
)
from openbiliclaw.runtime.image_cache import (
    image_cache_dir as _image_cache_dir,
)
from openbiliclaw.runtime.image_cache import (
    image_cache_key as _image_cache_key,
)
from openbiliclaw.runtime.keyword_fetch import (
    mark_keyword_terminal_from_xhs_task,
    source_keyword_id_from_xhs_task,
)
from openbiliclaw.soul.dislike_writeback import (
    apply_new_dislikes,
    topics_for_confirmed_avoidance,
)

# Project root: src/openbiliclaw/api/app.py → ../../..
_PROJECT_ROOT = _Path(__file__).resolve().parent.parent.parent.parent

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from openbiliclaw.clone import CloneService

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
from openbiliclaw.sources.x_auth import (  # noqa: E402
    X_REQUIRED_COOKIE_NAMES as _X_REQUIRED_COOKIE_NAMES,
)
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
    commands: list[list[str]]
    if os.name == "nt":
        commands = [["ipconfig"]]
    else:
        commands = [["ifconfig"], ["ip", "-4", "addr", "show", "scope", "global"]]

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
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM events").fetchone()
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
from openbiliclaw.api.utils import (
    article_tags_for_context as _article_tags_for_context,
)
from openbiliclaw.api.utils import (
    coerce_e2e_event_rows as _coerce_e2e_event_rows,
)
from openbiliclaw.api.utils import (
    event_row_id as _event_row_id,
)
from openbiliclaw.api.utils import (
    event_row_metadata as _event_row_metadata,
)
from openbiliclaw.api.utils import (
    infer_source_platform_from_url as _infer_source_platform_from_url,
)
from openbiliclaw.api.utils import (
    normalize_source_platform as _normalize_source_platform,
)
from openbiliclaw.api.utils import (
    select_init_platforms as _select_init_platforms,
)
from openbiliclaw.storage.cache import get_cache as _get_cache

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
    from openbiliclaw.api.notes_routes import register_notes_routes
    from openbiliclaw.api.recommendation_routes import build_recommendation_router
    from openbiliclaw.api.runtime_context import (
        RuntimeContext,
        build_degraded_runtime_context,
        build_runtime_context,
    )
    from openbiliclaw.api.saved_sync_routes import register_saved_sync_routes
    from openbiliclaw.api._system_routes import register_system_routes
    from openbiliclaw.api._image_proxy_routes import register_image_proxy_routes
    from openbiliclaw.api._cookie_routes import register_cookie_routes
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

    def _init_owns_task(task_id: str) -> bool:
        """Whether ``task_id`` is a bootstrap task enqueued by the active init
        run (so its task-result is init's own data, not a stale/steady-state
        completion). Defensive — never raises.
        """
        coord = getattr(ctx, "init_coordinator", None)
        if coord is None or not task_id:
            return False
        try:
            return bool(coord.is_owned_bootstrap_task(str(task_id)))
        except Exception:
            return False

    def _init_owned_ids_filter() -> set[str] | None:
        """``next-task`` filter: during an active init, restrict the dispatcher
        to init-owned bootstrap task ids (so a stale pending task can't be
        claimed and starve the run's collectors); None = no restriction.
        """
        if not _init_active_now():
            return None
        coord = getattr(ctx, "init_coordinator", None)
        if coord is None:
            return None
        try:
            return set(coord.owned_task_ids())
        except Exception:
            return None

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

    def _load_source_bootstrap_state() -> dict[str, object]:
        from openbiliclaw.sources.bootstrap_state import (
            default_source_bootstrap_state,
            normalize_source_bootstrap_state,
        )

        load_state = getattr(ctx.memory_manager, "load_source_bootstrap_state", None)
        if not callable(load_state):
            return default_source_bootstrap_state()
        with suppress(Exception):
            return normalize_source_bootstrap_state(load_state())
        return default_source_bootstrap_state()

    def _save_source_bootstrap_state(state: dict[str, object]) -> None:
        from openbiliclaw.sources.bootstrap_state import normalize_source_bootstrap_state

        save_state = getattr(ctx.memory_manager, "save_source_bootstrap_state", None)
        if not callable(save_state):
            return
        with suppress(Exception):
            save_state(normalize_source_bootstrap_state(state))

    def _filter_new_source_bootstrap_items(
        source: str,
        items: list[dict[str, Any]],
        key_func: Callable[[dict[str, Any]], str],
    ) -> tuple[list[dict[str, Any]], dict[int, str]]:
        """Filter bootstrap items that already propagated from an older task."""
        from openbiliclaw.sources.bootstrap_state import (
            as_string_list,
            source_bootstrap_state_key,
        )

        state = _load_source_bootstrap_state()
        state_key = source_bootstrap_state_key(source)
        seen = set(as_string_list(state.get(state_key, [])))
        batch_seen: set[str] = set()
        fresh: list[dict[str, Any]] = []
        fresh_keys_by_index: dict[int, str] = {}
        for item in items:
            key = key_func(item)
            if not key or key in seen or key in batch_seen:
                continue
            batch_seen.add(key)
            fresh_keys_by_index[len(fresh)] = key
            fresh.append(item)
        return fresh, fresh_keys_by_index

    def _mark_source_bootstrap_keys(source: str, keys: list[str]) -> None:
        """Persist bootstrap keys that already entered the source event path."""
        if not keys:
            return
        from datetime import UTC, datetime

        from openbiliclaw.sources.bootstrap_state import (
            as_string_list,
            source_bootstrap_state_key,
        )

        state = _load_source_bootstrap_state()
        state_key = source_bootstrap_state_key(source)
        merged = as_string_list(state.get(state_key, []))
        seen = set(merged)
        for key in keys:
            normalized = str(key).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
        state[state_key] = merged
        state["last_source_bootstrap_sync_at"] = datetime.now(UTC).isoformat()
        _save_source_bootstrap_state(state)

    chat_turn_lock = asyncio.Lock()
    fallback_chat_turns: dict[str, dict[str, Any]] = {}
    running_chat_turn_tasks: set[str] = set()
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

    def _get_chat_turn_row(turn_id: str) -> dict[str, Any] | None:
        get_chat_turn = _chat_db_method("get_chat_turn")
        if get_chat_turn is not None:
            return cast("dict[str, Any] | None", get_chat_turn(turn_id))
        row = fallback_chat_turns.get(turn_id)
        return dict(row) if row else None

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

    def _create_chat_turn_row(payload: ChatTurnIn, *, turn_id: str) -> dict[str, Any]:
        create_chat_turn = _chat_db_method("create_chat_turn")
        if create_chat_turn is not None:
            return cast(
                "dict[str, Any]",
                create_chat_turn(
                    turn_id=turn_id,
                    session=payload.session.strip() or "popup",
                    scope=_normalize_chat_scope(payload.scope),
                    subject_id=payload.subject_id.strip(),
                    subject_title=payload.subject_title.strip(),
                    message=payload.message.strip(),
                ),
            )

        from datetime import datetime

        now = datetime.now().isoformat(sep=" ")
        fallback_chat_turns.setdefault(
            turn_id,
            {
                "turn_id": turn_id,
                "session": payload.session.strip() or "popup",
                "scope": _normalize_chat_scope(payload.scope),
                "subject_id": payload.subject_id.strip(),
                "subject_title": payload.subject_title.strip(),
                "message": payload.message.strip(),
                "status": "pending",
                "reply": "",
                "error": "",
                "created_at": now,
                "updated_at": now,
            },
        )
        return dict(fallback_chat_turns[turn_id])

    def _complete_chat_turn_row(turn_id: str, *, reply: str) -> None:
        complete_chat_turn = _chat_db_method("complete_chat_turn")
        if complete_chat_turn is not None:
            complete_chat_turn(turn_id, reply=reply)
            return
        if turn_id in fallback_chat_turns:
            from datetime import datetime

            fallback_chat_turns[turn_id].update(
                {
                    "status": "completed",
                    "reply": reply,
                    "error": "",
                    "updated_at": datetime.now().isoformat(sep=" "),
                }
            )

    def _fail_chat_turn_row(turn_id: str, *, error: str, reply: str = "") -> None:
        fail_chat_turn = _chat_db_method("fail_chat_turn")
        if fail_chat_turn is not None:
            fail_chat_turn(turn_id, error=error, reply=reply)
            return
        if turn_id in fallback_chat_turns:
            from datetime import datetime

            fallback_chat_turns[turn_id].update(
                {
                    "status": "failed",
                    "reply": reply,
                    "error": error,
                    "updated_at": datetime.now().isoformat(sep=" "),
                }
            )

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
        from openbiliclaw.cli import (
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

    @app.get("/api/activity-feed", response_model=ActivityFeedResponse)
    async def activity_feed(
        limit: int = 10,
        before: str = "",
    ) -> ActivityFeedResponse:
        from openbiliclaw.runtime.activity_feed import ActivityFeedBuilder

        # All of the input collection (runtime counts + cognition updates +
        # feed query) hits SQLite synchronously; keep it off the event loop.
        def _collect_feed_inputs() -> dict[str, object]:
            runtime_status: dict[str, object] = {}
            get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
            if callable(get_runtime_status):
                runtime_status = dict(get_runtime_status())
            get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
            if callable(get_account_sync_status):
                runtime_status.update(get_account_sync_status())

            cognition_updates: list[dict[str, object]] = []
            load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
            if callable(load_cognition_updates):
                cognition_updates = [
                    item for item in load_cognition_updates() if isinstance(item, dict)
                ]

            builder = ActivityFeedBuilder(database=ctx.database)
            return builder.build(
                runtime_status=runtime_status,
                cognition_updates=cognition_updates,
                limit=limit,
                before=before,
            )

        payload = await asyncio.get_running_loop().run_in_executor(None, _collect_feed_inputs)
        payload_items = payload.get("items", [])
        item_dicts = payload_items if isinstance(payload_items, list) else []
        return ActivityFeedResponse(
            live_summary=str(payload.get("live_summary", "")),
            headline=str(payload.get("headline", "")),
            items=[
                ActivityFeedItemOut(
                    id=str(item.get("id", "")),
                    kind=str(item.get("kind", "")),
                    summary=str(item.get("summary", "")),
                    detail=str(item.get("detail", "")),
                    created_at=str(item.get("created_at", "")),
                    tone=str(item.get("tone", "info")),
                )
                for item in item_dicts
                if isinstance(item, dict)
            ],
            has_more=bool(payload.get("has_more", False)),
            next_cursor=str(payload.get("next_cursor", "")),
        )

    async def _classify_new_pool_items() -> None:
        """Legacy recovery for content_cache rows that lack content features.

        Normal source ingest writes ``discovery_candidates`` and lets the
        shared discovery-candidate pipeline evaluate/admit content before it
        reaches ``content_cache``.  This helper remains for old databases or
        explicit repair paths where rows are already cached but still missing
        ``style_key``, ``topic_group``, and ``relevance_score``.

        Silent skip when soul profile hasn't been built yet (init's first
        ~7 minutes). Otherwise events ingested before profile-ready would
        log ERROR-level traces for every batch — the legitimate retry is
        the next-tick + the profile-ready hook in ``SoulEngine``.
        """
        if ctx.recommendation_engine is None or ctx.soul_engine is None:
            return
        if not ctx.soul_engine.is_profile_ready():
            logger.debug("Background pool classification skipped: soul profile not ready")
            return
        try:
            profile = await ctx.soul_engine.get_profile()
            await ctx.recommendation_engine.classify_pool_backlog(
                profile=profile,
                limit=30,
            )
        except Exception:
            logger.exception("Background pool classification failed")

    async def _drain_discovery_candidates_once() -> None:
        """Best-effort drain for newly enqueued source candidates."""
        drain = getattr(ctx.runtime_controller, "drain_discovery_candidates_once", None)
        if not callable(drain):
            return
        try:
            await drain(batch_size=30)
        except Exception:
            logger.exception("Background discovery candidate drain failed")

    @app.get("/api/runtime-status", response_model=RuntimeStatusResponse)
    async def runtime_status() -> RuntimeStatusResponse:
        get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
        if not callable(get_runtime_status):
            return RuntimeStatusResponse(
                initialized=False,
                recommendation_count=0,
                pending_signal_events=0,
                unread_count=0,
            )
        # get_runtime_status() runs several count SQL queries synchronously;
        # keep them off the event loop (frontend polls this endpoint).
        payload = dict(await asyncio.get_running_loop().run_in_executor(None, get_runtime_status))
        get_account_sync_status = getattr(ctx.account_sync_service, "get_runtime_status", None)
        if callable(get_account_sync_status):
            payload.update(get_account_sync_status())
        get_update_status = getattr(ctx.auto_update_service, "get_runtime_status", None)
        if callable(get_update_status):
            payload.update(get_update_status())
        return RuntimeStatusResponse(**payload)

    @app.get("/api/observability", response_model=ObservabilityResponse)
    async def observability() -> ObservabilityResponse:
        """Aggregate observability data for the dashboard page."""
        db = getattr(ctx, "database", None)
        if db is None:
            return ObservabilityResponse(
                pipeline=PoolPipelineStats(
                    total_items=0,
                    fresh=0,
                    shown=0,
                    stale=0,
                    suppressed=0,
                    feedbacked=0,
                    pending=0,
                    discovery_candidates_pending=0,
                    discovery_candidates_evaluated=0,
                    items_with_quality_score=0,
                    items_without_quality_score=0,
                ),
                platforms=[],
                score_distribution=[],
                topic_groups=[],
                llm_usage=LLMUsageSummary(),
                discovery_candidates=[],
            )

        def _query() -> ObservabilityResponse:
            # ── 1. Master content_cache aggregate (single query replaces 9+ separate queries) ──
            master = db.conn.execute("""
                SELECT
                  COUNT(*) AS total,
                  AVG(CASE WHEN quality_score > 0.0 THEN quality_score END) AS avg_score,
                  SUM(CASE WHEN quality_score > 0.0 THEN 1 ELSE 0 END) AS scored_count,
                  SUM(CASE WHEN pool_status = 'fresh' THEN 1 ELSE 0 END) AS fresh,
                  SUM(CASE WHEN pool_status = 'shown' THEN 1 ELSE 0 END) AS shown,
                  SUM(CASE WHEN pool_status = 'stale' THEN 1 ELSE 0 END) AS stale,
                  SUM(CASE WHEN pool_status = 'suppressed' THEN 1 ELSE 0 END) AS suppressed,
                  SUM(CASE WHEN pool_status = 'feedbacked' THEN 1 ELSE 0 END) AS feedbacked,
                  SUM(CASE WHEN pool_status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN COALESCE(pool_expression, '') != '' THEN 1 ELSE 0 END) AS with_expr,
                  SUM(CASE WHEN COALESCE(pool_expression, '') = '' THEN 1 ELSE 0 END) AS without_expr,
                  SUM(CASE WHEN topic_group != '' AND topic_group IS NOT NULL THEN 1 ELSE 0 END) AS with_topic,
                  SUM(CASE WHEN delight_score > 0.0 THEN 1 ELSE 0 END) AS delight_candidates,
                  SUM(CASE WHEN delight_notified = 1 THEN 1 ELSE 0 END) AS delight_notified,
                  SUM(CASE WHEN last_scored_at IS NOT NULL THEN 1 ELSE 0 END) AS candidates_accepted,
                  SUM(CASE WHEN quality_score <= 0.0 THEN 1 ELSE 0 END) AS bucket_0,
                  SUM(CASE WHEN quality_score > 0.0 AND quality_score <= 0.2 THEN 1 ELSE 0 END) AS bucket_02,
                  SUM(CASE WHEN quality_score > 0.2 AND quality_score <= 0.4 THEN 1 ELSE 0 END) AS bucket_04,
                  SUM(CASE WHEN quality_score > 0.4 AND quality_score <= 0.6 THEN 1 ELSE 0 END) AS bucket_06,
                  SUM(CASE WHEN quality_score > 0.6 AND quality_score <= 0.8 THEN 1 ELSE 0 END) AS bucket_08,
                  SUM(CASE WHEN quality_score > 0.8 AND quality_score <= 1.0 THEN 1 ELSE 0 END) AS bucket_10
                FROM content_cache
            """).fetchone()
            m_total = int(master["total"]) if master else 0
            m_avg = (
                float(master["avg_score"]) if master and master["avg_score"] is not None else 0.0
            )
            m_scored = int(master["scored_count"]) if master else 0

            pipeline = PoolPipelineStats(
                total_items=m_total,
                fresh=int(master["fresh"]) if master else 0,
                shown=int(master["shown"]) if master else 0,
                stale=int(master["stale"]) if master else 0,
                suppressed=int(master["suppressed"]) if master else 0,
                feedbacked=int(master["feedbacked"]) if master else 0,
                pending=int(master["pending"]) if master else 0,
                discovery_candidates_pending=0,
                discovery_candidates_evaluated=0,
                items_with_quality_score=m_scored,
                items_without_quality_score=m_total - m_scored,
                avg_quality_score=round(m_avg, 4),
            )

            # ── 2. Per-platform breakdown ──
            plat_rows = db.conn.execute("""
                SELECT source_platform, pool_status, COUNT(*) AS c
                FROM content_cache
                GROUP BY source_platform, pool_status
                ORDER BY source_platform
            """).fetchall()
            plat_map: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
            for r in plat_rows:
                plat = str(r["source_platform"]) or "unknown"
                status = str(r["pool_status"]) or "unknown"
                plat_map[plat][status] += int(r["c"])
            platforms = [
                PlatformPoolStats(
                    platform=plat,
                    total=sum(s.values()),
                    fresh=s.get("fresh", 0),
                    shown=s.get("shown", 0),
                    stale=s.get("stale", 0),
                    suppressed=s.get("suppressed", 0),
                    feedbacked=s.get("feedbacked", 0),
                    pending=s.get("pending", 0),
                )
                for plat, s in sorted(plat_map.items())
            ]

            # ── 3. Score distribution (from master) ──
            score_buckets = [
                ("0 (未评分)", "bucket_0"),
                ("0~0.2", "bucket_02"),
                ("0.2~0.4", "bucket_04"),
                ("0.4~0.6", "bucket_06"),
                ("0.6~0.8", "bucket_08"),
                ("0.8~1.0", "bucket_10"),
            ]
            score_dist = [
                ScoreDistribution(bucket=b, count=int(master[col]) if master else 0)
                for b, col in score_buckets
            ]

            # ── 4. Topic groups ──
            topic_rows = db.conn.execute("""
                SELECT topic_group, COUNT(*) AS c
                FROM content_cache
                WHERE topic_group != '' AND topic_group IS NOT NULL
                GROUP BY topic_group ORDER BY c DESC LIMIT 30
            """).fetchall()
            topic_groups = [
                TopicGroupStats(topic=str(r["topic_group"]), count=int(r["c"])) for r in topic_rows
            ]

            # ── 5. Discovery candidates ──
            disc_rows = db.conn.execute("""
                SELECT status, COUNT(*) AS c
                FROM discovery_candidates GROUP BY status ORDER BY c DESC
            """).fetchall()
            disc_stats = [
                DiscoveryCandidateStats(status=str(r["status"]), count=int(r["c"]))
                for r in disc_rows
            ]

            # Discovery candidates pipeline counts (from disc_rows)
            disc_pending = 0
            disc_evaluated = 0
            for r in disc_rows:
                st = str(r["status"])
                if st in ("pending_eval", "evaluating"):
                    disc_pending += int(r["c"])
                elif st == "evaluated":
                    disc_evaluated += int(r["c"])
            pipeline.discovery_candidates_pending = disc_pending
            pipeline.discovery_candidates_evaluated = disc_evaluated

            # ── 6. LLM usage (combined: by_caller covers all 7d, derive today from it) ──
            caller_rows = db.conn.execute("""
                SELECT caller, COUNT(*) AS calls,
                       COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny,
                       COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                       SUM(CASE WHEN timestamp >= datetime('now', 'start of day', 'localtime') THEN 1 ELSE 0 END) AS today_calls,
                       SUM(CASE WHEN timestamp >= datetime('now', 'start of day', 'localtime') THEN COALESCE(estimated_cost_cny, 0) ELSE 0 END) AS today_cost
                FROM llm_usage
                WHERE timestamp >= datetime('now', '-7 day', 'localtime')
                GROUP BY caller ORDER BY cost_cny DESC LIMIT 20
            """).fetchall()
            by_caller: list[dict[str, object]] = []
            total_7d_calls = 0
            total_7d_cost = 0.0
            total_today_calls = 0
            total_today_cost = 0.0
            for r in caller_rows:
                calls = int(r["calls"])
                cost = float(r["cost_cny"])
                t_calls = int(r["today_calls"])
                t_cost = float(r["today_cost"])
                total_7d_calls += calls
                total_7d_cost += cost
                total_today_calls += t_calls
                total_today_cost += t_cost
                by_caller.append(
                    {
                        "caller": str(r["caller"] or "unknown"),
                        "calls": calls,
                        "cost_cny": round(cost, 4),
                        "prompt_tokens": int(r["prompt_tokens"]),
                        "completion_tokens": int(r["completion_tokens"]),
                    }
                )
            llm_usage = LLMUsageSummary(
                today_calls=total_today_calls,
                today_cost_cny=round(total_today_cost, 4),
                total_calls_7d=total_7d_calls,
                total_cost_7d=round(total_7d_cost, 4),
                by_caller=by_caller,
            )

            # ── 7. Runtime snapshot ──
            runtime: dict[str, object] = {}
            get_runtime_status = getattr(ctx.runtime_controller, "get_runtime_status", None)
            if callable(get_runtime_status):
                with suppress(Exception):
                    st = get_runtime_status()
                    if isinstance(st, dict):
                        for k in (
                            "last_refresh_at",
                            "last_discovered_count",
                            "last_replenished_count",
                            "pool_available_count",
                            "pool_raw_count",
                            "pool_target_count",
                            "recommendation_count",
                            "recent_pool_topics",
                            "pending_delight_count",
                            "last_delight_notification_at",
                            "manual_refresh_state",
                            "pending_signal_events",
                        ):
                            if k in st:
                                runtime[k] = st[k]

            # ── 8. Keywords stats ──
            kw_rows = db.conn.execute("""
                SELECT platform, status, COUNT(*) AS c
                FROM discovery_keywords GROUP BY platform, status ORDER BY platform, status
            """).fetchall()
            keywords = [
                {
                    "platform": str(r["platform"] or "unknown"),
                    "status": str(r["status"] or "unknown"),
                    "count": int(r["c"]),
                }
                for r in kw_rows
            ]

            # ── 9. Eval stats (combined single query) ──
            eval_row = db.conn.execute("""
                SELECT COUNT(*) AS total, COALESCE(SUM(eval_attempts), 0) AS attempts
                FROM discovery_candidates
            """).fetchone()
            eval_total_c = int(eval_row["total"]) if eval_row else 0
            eval_stats: dict[str, object] = {
                "total_candidates": eval_total_c,
                "total_eval_attempts": int(eval_row["attempts"]) if eval_row else 0,
                "candidates_accepted": int(master["candidates_accepted"]) if master else 0,
                "acceptance_rate": round(
                    int(master["candidates_accepted"]) / max(eval_total_c, 1) * 100, 1
                )
                if eval_total_c
                else 0,
            }

            # ── 10. Event stats (combined single query) ──
            event_rows = db.conn.execute("""
                SELECT event_type, source_platform, inferred_satisfaction, COUNT(*) AS c
                FROM events
                GROUP BY event_type, source_platform, inferred_satisfaction
            """).fetchall()
            event_types: dict[str, int] = defaultdict(int)
            event_platforms: dict[str, int] = defaultdict(int)
            sat_map: dict[str, int] = defaultdict(int)
            for r in event_rows:
                et = str(r["event_type"] or "unknown")
                sp = str(r["source_platform"]) or None
                sat = str(r["inferred_satisfaction"]) or None
                c = int(r["c"])
                event_types[et] += c
                if sp:
                    event_platforms[sp] += c
                if sat:
                    sat_map[sat] += c
            event_total = sum(event_types.values())
            event_stats = {
                "total_events": event_total,
                "by_type": dict(event_types),
                "by_platform": dict(event_platforms),
            }
            satisfaction_distribution = [
                {"satisfaction": k, "count": v}
                for k, v in sorted(sat_map.items(), key=lambda x: -x[1])
            ]

            # ── 11. Feedback stats ──
            fb_rows = db.conn.execute("""
                SELECT feedback_type, COUNT(*) AS c FROM content_cache
                WHERE feedback_type != '' AND feedback_type IS NOT NULL
                GROUP BY feedback_type ORDER BY c DESC
            """).fetchall()
            fb_types = {str(r["feedback_type"]): int(r["c"]) for r in fb_rows}
            feedback_stats = {"total_feedback": sum(fb_types.values()), "by_type": fb_types}

            # ── 12. Expression / Delight (from master) ──
            expression_coverage = {
                "with_expression": int(master["with_expr"]) if master else 0,
                "without_expression": int(master["without_expr"]) if master else 0,
                "with_topic_group": int(master["with_topic"]) if master else 0,
                "with_quality_score": m_scored,
            }
            delight_stats = {
                "delight_candidates": int(master["delight_candidates"]) if master else 0,
                "delight_notified": int(master["delight_notified"]) if master else 0,
                "pending_delight": runtime.get("pending_delight_count", 0),
                "last_delight_notification": runtime.get("last_delight_notification_at", ""),
            }

            # ── 13. Soul profile (pre-fetched) ──
            soul_profile = _soul_profile_cache

            # ── 14. Scheduler loop health ──
            scheduler_loops: list[dict[str, object]] = []
            try:
                controller = getattr(ctx, "runtime_controller", None)
                if controller is not None and hasattr(controller, "get_loop_health"):
                    scheduler_loops = controller.get_loop_health()
            except Exception:
                pass

            # ── 15. Auth sources ──
            auth_sources: list[dict[str, object]] = []
            try:
                ss_resp = sources_status()
                if isinstance(ss_resp, SourcesStatusResponse):
                    for s in cast("Any", ss_resp).sources:
                        auth_sources.append(
                            {
                                "platform": s.platform,
                                "label": s.label,
                                "status": s.status,
                                "last_ok_at": s.last_ok_at or "",
                                "error": s.error or "",
                                "cookie_age_hours": s.cookie_age_hours,
                            }
                        )
            except Exception:
                pass

            # ── 16. Style distribution ──
            style_rows = db.conn.execute("""
                SELECT style_key, COUNT(*) AS c FROM content_cache
                WHERE style_key != '' AND style_key IS NOT NULL
                GROUP BY style_key ORDER BY c DESC LIMIT 20
            """).fetchall()
            style_distribution = [
                {"style": str(r["style_key"]), "count": int(r["c"])} for r in style_rows
            ]

            # ── 17. Suppressed (quota-managed) qualification breakdown ──
            # One GROUP BY over source_platform × qualification bucket so the
            # dashboard can explain WHY items sit outside the rotation pool:
            # qualified = passed admission line + full precompute + linkable
            # (could re-enter if reactivated), below_threshold = LLM scored
            # them below the 0.60 admission line, unevaluated = no score yet.
            suppressed_rows = db.conn.execute("""
                SELECT
                    source_platform AS platform,
                    SUM(CASE WHEN COALESCE(relevance_score, 0.0) >= 0.60
                              AND COALESCE(pool_expression, '') != ''
                              AND COALESCE(pool_topic_label, '') != ''
                              AND COALESCE(style_key, '') != ''
                              AND COALESCE(topic_group, '') != ''
                              AND COALESCE(content_url, '') != ''
                        THEN 1 ELSE 0 END) AS qualified,
                    SUM(CASE WHEN COALESCE(relevance_score, 0.0) > 0
                              AND COALESCE(relevance_score, 0.0) < 0.60
                        THEN 1 ELSE 0 END) AS below_threshold,
                    SUM(CASE WHEN COALESCE(relevance_score, 0.0) = 0
                        THEN 1 ELSE 0 END) AS unevaluated,
                    COUNT(*) AS total
                FROM content_cache
                WHERE pool_status = 'suppressed'
                GROUP BY source_platform
                ORDER BY total DESC
            """).fetchall()
            suppressed_breakdown = [
                {
                    "platform": str(r["platform"] or "unknown"),
                    "qualified": int(r["qualified"]),
                    "below_threshold": int(r["below_threshold"]),
                    "unevaluated": int(r["unevaluated"]),
                    "total": int(r["total"]),
                }
                for r in suppressed_rows
            ]

            return ObservabilityResponse(
                pipeline=pipeline,
                platforms=platforms,
                score_distribution=score_dist,
                topic_groups=topic_groups,
                llm_usage=llm_usage,
                discovery_candidates=disc_stats,
                runtime=runtime,
                keywords=keywords,
                eval_stats=eval_stats,
                event_stats=event_stats,
                feedback_stats=feedback_stats,
                expression_coverage=expression_coverage,
                delight_stats=delight_stats,
                soul_profile=soul_profile,
                scheduler_loops=scheduler_loops,
                auth_sources=auth_sources,
                style_distribution=style_distribution,
                satisfaction_distribution=satisfaction_distribution,
                suppressed_breakdown=suppressed_breakdown,
            )

        # Pre-fetch soul profile in async context (not inside thread pool executor)
        _soul_profile_cache: dict[str, object] = {}
        try:
            soul_engine = getattr(ctx, "soul_engine", None)
            if soul_engine is not None and hasattr(soul_engine, "get_profile"):
                profile = await soul_engine.get_profile()
                if profile is not None:
                    interest_tags = (
                        len(getattr(profile.interest, "likes", []))
                        if hasattr(profile, "interest")
                        else 0
                    )
                    awareness_count = len(getattr(profile, "recent_awareness", []))
                    insights_count = len(getattr(profile, "active_insights", []))
                    portrait = getattr(profile, "personality_portrait", "")[:100]
                    _soul_profile_cache = {
                        "interest_tags_count": interest_tags,
                        "awareness_notes_count": awareness_count,
                        "insight_hypotheses_count": insights_count,
                        "personality_traits": portrait,
                    }
        except Exception:
            _soul_profile_cache = {"error": "profile unavailable"}

        return await asyncio.get_running_loop().run_in_executor(None, _query)

    @app.post("/api/delight/trigger")
    async def trigger_delight(payload: dict[str, Any] | None = None) -> Any:
        """Manually push N distinct delight candidates via WebSocket.

        Body: ``{"count": 3}``. For testing the queue UI: pulls the top N
        un-notified candidates from the pool and publishes a
        ``delight.candidate`` event for each one in succession, **without**
        marking any as notified. That way you can re-trigger the same
        batch repeatedly while iterating on the popup-side queue, and
        the popup's own ``/api/delight/pending`` calls still see them
        afterwards.

        Cooldown is cleared at the end so the proactive-push loop
        isn't gated.
        """
        count = 1
        if isinstance(payload, dict):
            try:
                count = max(1, min(20, int(payload.get("count", 1))))
            except (ValueError, TypeError):
                count = 1

        from openbiliclaw.recommendation.delight import DEFAULT_DELIGHT_THRESHOLD

        candidates = ctx.database.get_delight_candidates(
            min_delight_score=DEFAULT_DELIGHT_THRESHOLD,
            limit=count,
        )
        pushed: list[str] = []
        for row in candidates:
            payload_event = {
                "type": "delight.candidate",
                "phase": "ready",
                "message": "发现了一条你可能会意外喜欢的内容",
                "bvid": str(row.get("bvid", "")),
                "title": str(row.get("title", "")),
                "delight_reason": str(row.get("delight_reason", "")),
                "delight_score": float(row.get("delight_score", 0.0) or 0.0),
                "delight_hook": str(row.get("delight_hook", "")),
                "cover_url": str(row.get("cover_url", "")),
                "content_url": str(row.get("content_url", "")),
                "source_platform": str(row.get("source_platform", "bilibili")),
            }
            with suppress(Exception):
                await ctx.event_hub.publish(payload_event)
            pushed.append(str(payload_event["bvid"]))

        # Clear cooldown so the regular push loop isn't gated after manual
        # trigger.
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is not None:
            update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
            if callable(update_state):
                update_state(lambda state: state.pop("last_delight_notification_at", None))
            else:
                state = memory_manager.load_discovery_runtime_state()
                state.pop("last_delight_notification_at", None)
                memory_manager.save_discovery_runtime_state(state)
        return {"ok": True, "pushed_count": len(pushed), "bvids": pushed}

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

    @app.post("/api/delight/sent", response_model=DelightAckResponse)
    async def mark_delight_sent(payload: DelightAckIn) -> DelightAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Delight bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_delight_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_delight_notified(bvid)
        return DelightAckResponse(ok=True, bvid=bvid)

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

    @app.post("/api/notifications/sent", response_model=NotificationAckResponse)
    async def mark_notification_sent(payload: NotificationAckIn) -> NotificationAckResponse:
        bvid = payload.bvid.strip()
        if not bvid:
            raise HTTPException(status_code=422, detail="Notification bvid is required.")
        mark_sent = getattr(ctx.runtime_controller, "mark_notification_sent", None)
        if callable(mark_sent):
            mark_sent(bvid)
        else:
            ctx.database.mark_notification_sent(bvid)
        return NotificationAckResponse(ok=True, bvid=bvid)

    async def _rag_retrieve(
        message: str, top_k: int = 4
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Retrieve RAG context + citations for a chat message.

        Returns ``(context_block, references)``. Both are empty when the
        article index is missing or has nothing relevant, so chat degrades
        cleanly to its normal (non-grounded) behaviour. The blocking embed +
        scan runs off the event loop behind a short budget so a slow embedder
        can never stall a reply.
        """
        if not message or not message.strip():
            return None, []
        try:
            from openbiliclaw.rag.retriever import get_retriever

            retr = get_retriever()
            loop = asyncio.get_running_loop()
            hits = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: retr.retrieve_chunks(message, top_k=top_k)),
                timeout=15,
            )
        except Exception:
            logger.debug("RAG retrieval skipped for this turn", exc_info=True)
            return None, []
        if not hits:
            return None, []
        context = retr.format_context(hits)
        references = [
            {
                "title": str(h.get("title", "") or ""),
                "url": str(h.get("url", "") or ""),
                "author": str(h.get("author", "") or ""),
                "source_table": str(h.get("source_table", "") or "articles"),
                "score": float(h.get("score", 0.0) or 0.0),
            }
            for h in hits
        ]
        logger.info("RAG context injected for chat (%d refs)", len(references))
        return context, references

    @app.post("/api/chat")
    async def chat(payload: ChatIn) -> Any:
        from fastapi.responses import JSONResponse

        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        # Pause discovery LLM calls while user is chatting
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        # RAG: ground the reply in the user's crawled reading library whenever
        # the index has something relevant (no-op while the index is still
        # being built, so chat behaves exactly as before until then).
        retrieval_context, references = await _rag_retrieve(message, top_k=4)
        try:
            # Bumped from 30s to 120s — deepseek with reasoning_effort=max
            # routinely takes 60-90s for one dialogue turn, so a 30s budget
            # truncated essentially every reply. Extension's AbortController
            # is sized to be generous enough to cover this end-to-end.
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(message, retrieval_context=retrieval_context or None),
                timeout=120,
            )
        except TimeoutError:
            reply = "后台正忙，等一下再聊。"
        except Exception:
            logger.exception("Chat dialogue failed")
            reply = "聊天出了点问题，稍后再试。"
        finally:
            if concurrency is not None:
                concurrency.chat_active = False
        return JSONResponse(content={"reply": reply, "references": references})

    # ── Conversational recommendation (生成式推荐第二步) ──
    _chat_recommend_sessions: dict[str, Any] = {}

    @app.post("/api/chat/recommend", response_model=ChatRecommendResponse)
    async def chat_recommend_endpoint(payload: ChatRecommendIn) -> ChatRecommendResponse:
        """Conversational recommendation: talk to the recommendation engine in natural language.

        Supports multi-turn context: "给我推荐几个广告算法视频" → "再来几个" → "讲讲第二个".
        Session state is kept in memory (not persistent); pass session_id to continue a conversation.
        """
        from openbiliclaw.recommendation.chat_recommender import ChatSession, chat_recommend

        if ctx.recommendation_engine is None:
            raise HTTPException(status_code=503, detail="Recommendation engine not initialized.")

        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Message is required.")

        # Get or create session
        session_id = (
            payload.session_id
            or f"rec-{int(asyncio.get_event_loop().time() * 1000)}-{id(message) % 10000}"
        )
        session = _chat_recommend_sessions.get(session_id)
        if session is None:
            # Snapshot user profile at session start
            profile = None
            if ctx.soul_engine is not None:
                try:
                    profile = ctx.soul_engine.load_profile()
                except Exception:
                    logger.exception("Failed to load soul profile for chat recommend session")
            session = ChatSession(session_id=session_id, profile=profile)
            _chat_recommend_sessions[session_id] = session

        try:
            reply = await chat_recommend(
                message,
                session=session,
                engine=ctx.recommendation_engine,
                llm_service=getattr(ctx, "llm_service", None),
                default_limit=payload.limit,
            )
        except Exception:
            logger.exception("Chat recommend failed")
            reply = "推荐出了点问题，稍后再试。"

        # Serialize last recommendations for the frontend
        recs_out: list[RecommendationOut] = []
        last_recs = getattr(session, "last_recommendations", []) or []
        if last_recs:
            try:
                recs_out = _serialize_recommendation_items(last_recs)
            except Exception:
                logger.exception("Failed to serialize chat recommendations")

        return ChatRecommendResponse(reply=reply, session_id=session_id, recommendations=recs_out)

    def _record_probe_cognition(
        summary: str,
        domain: str,
        action: str,
        *,
        source: str = "interest_probe",
        detail: str = "",
    ) -> None:
        """Write a cognition update so probe feedback shows in '阿b最近记住了什么'."""
        from datetime import datetime

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

    def _probe_metadata_from_active_item(
        get_active: Any,
        domain: str,
        *,
        include_category: bool = False,
        include_source_mode: bool = False,
    ) -> dict[str, object]:
        """Read active probe metadata before confirm/reject mutates state."""
        from openbiliclaw.soul.speculator import build_probe_axis

        if not callable(get_active):
            return {"domain": domain}
        try:
            active_items = list(get_active())
        except Exception:
            logger.debug("Failed to read active probe metadata", exc_info=True)
            return {"domain": domain}

        for item in active_items:
            spec_domain = str(getattr(item, "domain", "")).strip()
            if spec_domain.lower() != domain.lower():
                continue
            specifics = [
                str(getattr(specific, "name", "")).strip()
                for specific in getattr(item, "specifics", [])
                if str(getattr(specific, "name", "")).strip()
            ]
            axis = build_probe_axis(
                experience_mode=getattr(item, "experience_mode", ""),
                entry_load=getattr(item, "entry_load", ""),
            )
            metadata: dict[str, object] = {
                "domain": spec_domain or domain,
                "reason": str(getattr(item, "reason", "")).strip(),
            }
            if include_category:
                metadata["category"] = str(getattr(item, "category", "")).strip()
            if include_source_mode:
                source_mode = str(getattr(item, "source_mode", "")).strip()
                source_signal = str(getattr(item, "source_signal", "")).strip()
                if source_mode:
                    metadata["source_mode"] = source_mode
                if source_signal:
                    metadata["source_signal"] = source_signal
            if axis:
                metadata["axis"] = axis
            if specifics:
                metadata["specifics"] = specifics
            return metadata
        return {"domain": domain}

    def _probe_metadata_from_active_speculation(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active interest probe metadata before state mutation."""
        return _probe_metadata_from_active_item(
            getattr(speculator, "get_active_speculations", None),
            domain,
            include_category=True,
        )

    def _probe_metadata_from_active_avoidance(
        speculator: Any,
        domain: str,
    ) -> dict[str, object]:
        """Read active avoidance probe metadata before state mutation."""
        return _probe_metadata_from_active_item(
            getattr(speculator, "get_active_avoidances", None),
            domain,
            include_source_mode=True,
        )

    def _record_probe_feedback_history(
        domain: str,
        response: str,
        *,
        speculator: Any,
        message: str = "",
        classification: str = "",
        classifier: str = "",
        resulting_action: str = "",
        state_key: str = "probe_feedback_history",
        metadata_fn: Any | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        """Persist explicit user feedback for future probe novelty checks."""
        from openbiliclaw.soul.speculator import append_probe_feedback_history

        memory_manager = getattr(ctx, "memory_manager", None)
        if memory_manager is None:
            memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        load_state = getattr(memory_manager, "load_discovery_runtime_state", None)
        save_state = getattr(memory_manager, "save_discovery_runtime_state", None)
        update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
        if not callable(update_state) and (not callable(load_state) or not callable(save_state)):
            return
        try:
            if metadata is not None:
                entry = dict(metadata)
            elif metadata_fn is not None:
                entry = metadata_fn(domain)
            else:
                entry = _probe_metadata_from_active_speculation(speculator, domain)
            entry["response"] = response
            if message:
                entry["message"] = message
                entry["raw_text_excerpt"] = message[:240]
            if classification:
                entry["classification"] = classification
            if classifier:
                entry["classifier"] = classifier
            if resulting_action:
                entry["resulting_action"] = resulting_action

            def _mutate(state: dict[str, object]) -> None:
                state[state_key] = append_probe_feedback_history(
                    state.get(state_key, []),
                    entry,
                )

            if callable(update_state):
                update_state(_mutate)
            else:
                load_state_fn = cast("Callable[[], dict[str, object]]", load_state)
                save_state_fn = cast("Callable[[dict[str, object]], None]", save_state)
                state = load_state_fn()
                _mutate(state)
                save_state_fn(state)
        except Exception:
            logger.exception("Failed to record probe feedback history")

    async def _judge_probe_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """Judge the user's probe chat as a 4-way confirmation signal."""
        sentiment, _classifier = await _classify_probe_sentiment(
            user_message,
            ai_reply,
            domain,
        )
        return sentiment

    async def _classify_probe_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> tuple[str, str]:
        """Return ``(classification, classifier)`` for probe chat feedback."""
        llm_result = await _llm_judge_sentiment(user_message, ai_reply, domain)
        if llm_result in {"strong_positive", "weak_positive", "negative"}:
            return llm_result, "llm"
        keyword_result = _keyword_judge_sentiment(user_message)
        if keyword_result != "neutral":
            return keyword_result, "keyword"
        return "neutral", "neutral_default"

    def _keyword_judge_sentiment(user_message: str) -> str:
        """Fallback keyword-based sentiment detection."""
        msg = user_message.lower()
        negative_terms = {
            "不喜欢",
            "不感兴趣",
            "不是这个意思",
            "别推",
            "没兴趣",
            "不想看",
        }
        strong_positive_terms = {
            "以后多推",
            "这就是我想看的",
            "我就喜欢",
            "加入我的画像",
        }
        weak_positive_terms = {
            "有点意思",
            "可以看看",
            "偶尔看看",
            "还行",
            "先试试",
        }
        if any(kw in msg for kw in negative_terms):
            return "negative"
        if any(kw in msg for kw in strong_positive_terms):
            return "strong_positive"
        if any(kw in msg for kw in weak_positive_terms):
            return "weak_positive"
        return "neutral"

    async def _llm_judge_sentiment(
        user_message: str,
        ai_reply: str,
        domain: str,
    ) -> str:
        """LLM-based sentiment judgment for probe chat."""
        if ctx.recommendation_engine is None:
            return "neutral"
        llm = getattr(ctx.recommendation_engine, "_llm", None)
        if llm is None:
            return "neutral"
        try:
            response = await asyncio.wait_for(
                llm.complete_with_core_memory(
                    system_instruction=(
                        "任务：判断用户对一个兴趣方向的态度。\n\n"
                        "规则：\n"
                        "1. 只输出一个英文标签："
                        "strong_positive、weak_positive、neutral 或 negative\n"
                        "2. 不要输出任何其他内容\n\n"
                        "判断标准：\n"
                        "- strong_positive = 用户明确要加入画像、以后多推、这就是想看的\n"
                        "- weak_positive = 用户表达轻微兴趣、可以看看、偶尔看看，但未直接确认\n"
                        "- negative = 用户表达了不喜欢、不感兴趣、太难、太无聊\n"
                        "- neutral = 态度不明确\n"
                    ),
                    user_input=f"方向：{domain}\n用户：{user_message}",
                    max_tokens=8,
                    temperature=0.0,
                    json_mode=False,
                    caller="api.sentiment",
                    bypass_semaphore=True,
                ),
                timeout=15,
            )
            raw = str(getattr(response, "content", "")).strip().lower()
            # Extract the first recognizable word
            for word in raw.split():
                cleaned = word.strip("\"'.,:;!?")
                if cleaned in (
                    "strong_positive",
                    "weak_positive",
                    "negative",
                    "neutral",
                ):
                    logger.info("Sentiment LLM for '%s': %s (raw=%r)", domain, cleaned, raw)
                    return cleaned
            logger.info(
                "Sentiment LLM for '%s': unrecognized (raw=%r), trying keywords", domain, raw
            )
            return "neutral"
        except Exception:
            logger.info("Sentiment LLM for '%s' failed, trying keywords", domain)
            return "neutral"

    def _confirm_speculation_with_source(
        speculator: Any,
        domain: str,
        *,
        confirmation_source: str,
    ) -> bool:
        confirm = getattr(speculator, "user_confirm_speculation", None)
        if not callable(confirm):
            return False
        try:
            return bool(confirm(domain, confirmation_source=confirmation_source))
        except TypeError:
            return bool(confirm(domain))

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
        from datetime import UTC, datetime

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

    def _contextual_chat_message(turn: ChatTurnOut) -> str:
        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id or "这条惊喜推荐"
            return f"[关于惊喜推荐「{label}」的反馈] {turn.message}"
        if turn.scope == "probe":
            label = turn.subject_title or turn.subject_id or "这个方向"
            return f"[关于猜测兴趣「{label}」的反馈] {turn.message}"
        if turn.scope == "avoidance_probe":
            label = turn.subject_title or turn.subject_id or "这个避雷方向"
            return f"[关于避雷方向「{label}」的反馈] {turn.message}"
        return turn.message

    async def _generate_durable_chat_reply(turn: ChatTurnOut) -> str:
        if ctx.dialogue is None:
            return "对话引擎暂不可用。"

        # RAG: ground the reply in the user's crawled reading library. Only the
        # plain "chat" scope is grounded — delight/probe scopes are feedback
        # about one specific recommendation, where library passages would just
        # be noise. References are stashed per turn so the UI can show the
        # "已参考 N 篇收藏" badge.
        retrieval_context: str | None = None
        references: list[dict[str, Any]] = []
        if turn.scope == "chat":
            retrieval_context, references = await _rag_retrieve(turn.message, top_k=4)
            if references:
                chat_turn_references[str(turn.turn_id)] = references

        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            async with chat_turn_lock:
                reply = await asyncio.wait_for(
                    ctx.dialogue.respond(
                        _contextual_chat_message(turn),
                        retrieval_context=retrieval_context,
                    ),
                    timeout=120,
                )
                reply = str(reply)
        except TimeoutError:
            return "后台正忙，等一下再聊。"
        except Exception:
            logger.exception("Durable chat turn failed: %s", turn.turn_id)
            return "聊天出了点问题，稍后再试。"
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        if turn.scope == "delight":
            label = turn.subject_title or turn.subject_id
            _record_probe_cognition(
                f"关于惊喜推荐「{label}」你说：{turn.message}",
                turn.subject_id or label,
                "delight_chat",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event(
                "delight.chat",
                f"关于「{label}」你说：{turn.message}",
                turn.subject_id or label,
            )
        elif turn.scope == "probe":
            domain = turn.subject_id or turn.subject_title
            sentiment, classifier = await _classify_probe_sentiment(turn.message, reply, domain)
            speculator = getattr(ctx.soul_engine, "_speculator", None)
            chat_response = "chat_neutral"
            resulting_action = "none"
            if sentiment == "negative":
                chat_response = "chat_rejected"
                resulting_action = "rejected"
                if speculator is not None:
                    with suppress(Exception):
                        speculator.user_reject_speculation(domain, cooldown_days=14)
                summary = f"你对「{domain}」的反馈偏负面（{turn.message}），已暂时搁置 14 天。"
            elif sentiment == "strong_positive":
                chat_response = "chat_confirmed"
                resulting_action = "confirmed"
                if speculator is not None:
                    with suppress(Exception):
                        _confirm_speculation_with_source(
                            speculator,
                            domain,
                            confirmation_source="chat_confirmed",
                        )
                summary = f"你明确确认了对「{domain}」的兴趣，已加入画像。"
            elif sentiment == "weak_positive":
                chat_response = "weak_positive"
                resulting_action = "weak_positive_deferred"
                _record_exploration_buffer_event(
                    domain=domain,
                    source_event="weak_positive_chat",
                )
                summary = f"你对「{domain}」有轻微信号，先作为短期探索方向观察。"
            else:
                summary = f"关于「{domain}」你说：{turn.message}"
            if speculator is not None:
                _record_probe_feedback_history(
                    domain,
                    chat_response,
                    speculator=speculator,
                    message=turn.message,
                    classification=sentiment,
                    classifier=classifier,
                    resulting_action=resulting_action,
                )
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event("interest.chat", summary, domain)
        elif turn.scope == "avoidance_probe":
            domain = turn.subject_id or turn.subject_title
            sentiment, classifier = await _classify_probe_sentiment(turn.message, reply, domain)
            speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)
            if sentiment == "negative":
                chat_response = "avoidance_chat_confirmed"
                resulting_action = "confirmed"
                if speculator is not None:
                    with suppress(Exception):
                        speculator.observe(
                            [
                                {
                                    "event_type": "dislike",
                                    "title": domain,
                                    "metadata": {
                                        "feedback_type": "dislike",
                                        "user_message": turn.message,
                                        "source": "avoidance_probe_chat",
                                    },
                                }
                            ]
                        )
                summary = f"你确认「{domain}」偏向不喜欢，确认度 +1。"
            elif sentiment in {"strong_positive", "weak_positive"}:
                chat_response = "avoidance_chat_rejected"
                resulting_action = "rejected"
                if speculator is not None:
                    reject_fn = getattr(speculator, "user_reject_avoidance", None)
                    if callable(reject_fn):
                        with suppress(Exception):
                            reject_fn(domain, cooldown_days=14)
                summary = f"你表示其实不排斥「{domain}」，已暂时搁置 14 天。"
            else:
                chat_response = "avoidance_chat_neutral"
                resulting_action = "none"
                summary = f"关于避雷方向「{domain}」你说：{turn.message}"
            if speculator is not None:
                _record_probe_feedback_history(
                    domain,
                    chat_response,
                    speculator=speculator,
                    message=turn.message,
                    classification=sentiment,
                    classifier=classifier,
                    resulting_action=resulting_action,
                    state_key="avoidance_probe_feedback_history",
                    metadata_fn=lambda item_domain: _probe_metadata_from_active_avoidance(
                        speculator,
                        item_domain,
                    ),
                )
            _record_probe_cognition(
                summary,
                domain,
                "chat",
                source="avoidance_probe",
                detail=f"你的反馈：{turn.message}\n阿b的回复：{reply}",
            )
            await _publish_probe_event("avoidance.chat", summary, domain)

        return reply

    async def _complete_durable_chat_turn(turn_id: str) -> None:
        if turn_id in running_chat_turn_tasks:
            return
        running_chat_turn_tasks.add(turn_id)
        try:
            row = _get_chat_turn_row(turn_id)
            if row is None:
                return
            turn = _normalize_chat_turn(row)
            if turn.status != "pending":
                return
            reply = await _generate_durable_chat_reply(turn)
            _complete_chat_turn_row(turn_id, reply=reply)
        except Exception as exc:
            logger.exception("Failed to complete durable chat turn %s", turn_id)
            _fail_chat_turn_row(turn_id, error=str(exc), reply="聊天出了点问题，稍后再试。")
        finally:
            running_chat_turn_tasks.discard(turn_id)

    @app.post("/api/chat/turns", response_model=ChatTurnOut)
    async def start_chat_turn(payload: ChatTurnIn) -> ChatTurnOut:
        message = payload.message.strip()
        if not message:
            raise HTTPException(status_code=422, detail="Chat message is required.")
        raw_turn_id = payload.turn_id.strip()
        turn_id = raw_turn_id or f"turn-{uuid.uuid4().hex}"
        existing = _get_chat_turn_row(turn_id)
        if existing is not None:
            turn = _normalize_chat_turn(existing)
            if turn.status == "pending":
                asyncio.create_task(_complete_durable_chat_turn(turn.turn_id))
            return turn
        row = _create_chat_turn_row(payload, turn_id=turn_id)
        asyncio.create_task(_complete_durable_chat_turn(turn_id))
        return _normalize_chat_turn(row)

    @app.get("/api/chat/turns", response_model=ChatTurnListResponse)
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

    @app.get("/api/chat/turns/{turn_id}", response_model=ChatTurnOut)
    async def get_chat_turn(turn_id: str) -> ChatTurnOut:
        row = _get_chat_turn_row(turn_id.strip())
        if row is None:
            raise HTTPException(status_code=404, detail="Chat turn not found.")
        turn = _normalize_chat_turn(row)
        if turn.status == "pending":
            asyncio.create_task(_complete_durable_chat_turn(turn.turn_id))
        return turn

    @app.post("/api/interest-probes/trigger")
    async def trigger_interest_probe() -> dict[str, Any]:
        """Manually trigger an interest probe push via WebSocket.

        Useful when ``run_forever`` is blocked by a long refresh cycle
        and the probe wouldn't fire on its own for several minutes.
        """
        controller = ctx.runtime_controller
        if controller is None:
            raise HTTPException(status_code=503, detail="Runtime controller not available")
        publish = getattr(controller, "_publish_interest_probe_if_available", None)
        if not callable(publish):
            raise HTTPException(status_code=503, detail="Probe publisher not available")
        await publish()
        return {"ok": True, "action": "probe_triggered"}

    @app.get("/api/interest-probes/pending")
    async def pending_interest_probes() -> dict[str, Any]:
        """Return active speculative interests that the user hasn't responded to.

        The mobile web UI polls this on page load / bell-click so probes
        survive page refreshes (unlike WebSocket-only delivery).
        """
        try:
            from openbiliclaw.soul.speculator import load_speculative_state

            spec_state = load_speculative_state(ctx.config.data_path)
            active = [item for item in spec_state.active if item.status == "active"]
            items = []
            for item in active[:6]:
                probe_mode, challenge = _probe_metadata_for_payload(item)
                items.append(
                    {
                        "domain": item.domain,
                        "reason": item.reason,
                        "confidence": item.confidence,
                        "status": item.status,
                        "probe_mode": probe_mode,
                        "challenge": challenge,
                    }
                )
            return {"items": items}
        except Exception:
            return {"items": []}

    @app.post("/api/interest-probes/respond")
    async def respond_to_interest_probe(payload: dict[str, Any]) -> Any:
        """User responds to a speculated interest probe.

        Body: { "domain": "...", "response": "confirm" | "reject" | "chat", "message": "..." }

        - confirm: Force-promote the speculation
        - reject: Move to cooldown (30 days)
        - chat: Forward to dialogue engine with probe context, return reply
        """
        domain = str(payload.get("domain", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()

        if not domain:
            raise HTTPException(status_code=422, detail="domain is required")
        if response_type not in {"confirm", "reject", "chat"}:
            raise HTTPException(status_code=422, detail="response must be confirm, reject, or chat")

        speculator = getattr(ctx.soul_engine, "_speculator", None)
        if speculator is None:
            raise HTTPException(status_code=503, detail="Speculator not available")

        if response_type == "confirm":
            requested_source = str(payload.get("confirmation_source", "")).strip()
            surface = str(payload.get("surface", "")).strip().lower()
            confirmation_source = requested_source or (
                "profile_confirmed" if surface == "profile" else "probe_confirmed"
            )
            metadata = _probe_metadata_from_active_speculation(speculator, domain)
            ok = _confirm_speculation_with_source(
                speculator,
                domain,
                confirmation_source=confirmation_source,
            )
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "confirm",
                    speculator=speculator,
                    resulting_action="confirmed",
                    metadata=metadata,
                )
                # Force_tick generates 5 new probes via LLM (~30-60s).
                # Running it inline blocks the response past the
                # browser fetch timeout (35s) — the user gives up,
                # AbortError fires, and the next click hits a stale UI.
                # Schedule it as a background task so the API returns
                # immediately; the new probes will be visible on the
                # next profile-summary refresh.
                tick_fn = getattr(speculator, "force_tick", None)
                if callable(tick_fn):

                    async def _bg_force_tick() -> None:
                        try:
                            profile = await ctx.soul_engine.get_profile()
                            feedback_history: object = []
                            load_runtime_state = getattr(
                                ctx.memory_manager,
                                "load_discovery_runtime_state",
                                None,
                            )
                            if callable(load_runtime_state):
                                runtime_state = load_runtime_state()
                                if isinstance(runtime_state, dict):
                                    feedback_history = runtime_state.get(
                                        "probe_feedback_history",
                                        [],
                                    )

                            def _load_feedback_history() -> object:
                                if not callable(load_runtime_state):
                                    return []
                                runtime_state = load_runtime_state()
                                if not isinstance(runtime_state, dict):
                                    return []
                                return runtime_state.get("probe_feedback_history", [])

                            if asyncio.iscoroutinefunction(tick_fn):
                                try:
                                    await tick_fn(
                                        profile,
                                        feedback_history=feedback_history,
                                        feedback_history_loader=_load_feedback_history,
                                    )
                                except TypeError:
                                    try:
                                        await tick_fn(
                                            profile,
                                            feedback_history=feedback_history,
                                        )
                                    except TypeError:
                                        await tick_fn(profile)
                            else:
                                try:
                                    tick_fn(
                                        profile,
                                        feedback_history=feedback_history,
                                        feedback_history_loader=_load_feedback_history,
                                    )
                                except TypeError:
                                    try:
                                        tick_fn(profile, feedback_history=feedback_history)
                                    except TypeError:
                                        tick_fn(profile)
                        except Exception:
                            logger.exception("Background force_tick after confirm failed")

                    asyncio.create_task(_bg_force_tick())
                # Record cognition update so it shows in "阿b最近记住了什么"
                _record_probe_cognition(
                    f"你确认了对「{domain}」的兴趣，已加入画像。",
                    domain,
                    "confirmed",
                )
                # Notify frontend via WebSocket
                await _publish_probe_event(
                    "interest.confirmed",
                    f"你确认了对「{domain}」的兴趣，已加入画像。",
                    domain,
                )
            return {"ok": ok, "action": "confirmed", "domain": domain}

        if response_type == "reject":
            metadata = _probe_metadata_from_active_speculation(speculator, domain)
            ok = speculator.user_reject_speculation(domain)
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "reject",
                    speculator=speculator,
                    metadata=metadata,
                )
                _record_probe_cognition(
                    f"你对「{domain}」暂时不感兴趣，30 天内不再推送。",
                    domain,
                    "rejected",
                )
                await _publish_probe_event(
                    "interest.rejected",
                    f"已记录：你对「{domain}」暂时不感兴趣，30 天内不再推送。",
                    domain,
                )
            return {"ok": ok, "action": "rejected", "domain": domain}

        # Chat: forward to dialogue with domain context injected
        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"我想聊聊你猜我可能感兴趣的「{domain}」这个方向"
        # Inject domain context so dialogue engine + learn_from_dialogue
        # understand this is feedback on a specific speculated interest
        contextual_message = f"[关于猜测兴趣「{domain}」的反馈] {raw_message}"
        if ctx.dialogue is None:
            return {"ok": False, "action": "chat", "domain": domain, "reply": "对话引擎暂不可用。"}
        # Pause discovery LLM calls while user is chatting
        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(contextual_message),
                timeout=30,
            )
            # Judge sentiment while discovery is still paused
            sentiment, classifier = await _classify_probe_sentiment(raw_message, reply, domain)
        except TimeoutError:
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "后台正忙，等一下再聊。",
            }
        except Exception:
            logger.exception("Dialogue failed for probe chat: %s", domain)
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "聊天出了点问题，稍后再试。",
            }
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        chat_response = "chat_neutral"
        resulting_action = "none"
        if sentiment == "negative":
            chat_response = "chat_rejected"
            resulting_action = "rejected"
            speculator.user_reject_speculation(domain, cooldown_days=14)
            summary = f"你对「{domain}」的反馈偏负面（{raw_message}），已暂时搁置 14 天。"
        elif sentiment == "strong_positive":
            chat_response = "chat_confirmed"
            resulting_action = "confirmed"
            _confirm_speculation_with_source(
                speculator,
                domain,
                confirmation_source="chat_confirmed",
            )
            summary = f"你明确确认了对「{domain}」的兴趣，已加入画像。"
        elif sentiment == "weak_positive":
            chat_response = "weak_positive"
            resulting_action = "weak_positive_deferred"
            _record_exploration_buffer_event(
                domain=domain,
                source_event="weak_positive_chat",
            )
            summary = f"你对「{domain}」有轻微信号，先作为短期探索方向观察。"
        else:
            summary = f"关于「{domain}」你说：{raw_message}"

        _record_probe_feedback_history(
            domain,
            chat_response,
            speculator=speculator,
            message=raw_message,
            classification=sentiment,
            classifier=classifier,
            resulting_action=resulting_action,
        )

        detail = f"你的反馈：{raw_message}\n阿b的回复：{reply}"
        _record_probe_cognition(summary, domain, "chat", detail=detail)
        await _publish_probe_event(
            "interest.chat",
            summary,
            domain,
        )
        from fastapi.responses import JSONResponse

        return JSONResponse(
            content={"ok": True, "action": "chat", "domain": domain, "reply": reply}
        )

    @app.post("/api/avoidance-probes/trigger")
    async def trigger_avoidance_probe() -> dict[str, Any]:
        """Manually trigger an avoidance probe push via WebSocket."""
        controller = ctx.runtime_controller
        if controller is None:
            raise HTTPException(status_code=503, detail="Runtime controller not available")
        publish = getattr(controller, "_publish_avoidance_probe_if_available", None)
        if not callable(publish):
            raise HTTPException(status_code=503, detail="Avoidance probe publisher not available")
        await publish()
        return {"ok": True, "action": "avoidance_probe_triggered"}

    @app.get("/api/avoidance-probes/pending")
    async def pending_avoidance_probes() -> dict[str, Any]:
        """Return active speculative avoidances awaiting user response."""
        try:
            from openbiliclaw.soul.avoidance_speculator import load_avoidance_state

            runtime_config = getattr(ctx, "config", None) or config
            avoidance_state = load_avoidance_state(runtime_config.data_path)
            active = [item for item in avoidance_state.active if item.status == "active"]
            items = [
                {
                    "domain": item.domain,
                    "reason": item.reason,
                    "confidence": item.confidence,
                    "source_mode": item.source_mode,
                    "source_signal": item.source_signal,
                    "status": item.status,
                    "specifics": [
                        {"name": specific.name, "confirmation_count": specific.confirmation_count}
                        for specific in item.specifics
                        if specific.name.strip()
                    ],
                }
                for item in active[:6]
            ]
            return {"items": items}
        except Exception:
            logger.debug("Failed to load pending avoidance probes", exc_info=True)
            return {"items": []}

    @app.post("/api/avoidance-probes/respond")
    async def respond_to_avoidance_probe(payload: dict[str, Any]) -> Any:
        """User responds to a speculated avoidance probe."""
        domain = str(payload.get("domain", "")).strip()
        response_type = str(payload.get("response", "")).strip().lower()

        if not domain:
            raise HTTPException(status_code=422, detail="domain is required")
        if response_type not in {"confirm", "reject", "chat"}:
            raise HTTPException(status_code=422, detail="response must be confirm, reject, or chat")

        speculator = getattr(ctx.soul_engine, "_avoidance_speculator", None)
        if speculator is None:
            raise HTTPException(status_code=503, detail="Avoidance speculator not available")

        def metadata_fn(item_domain: str) -> dict[str, object]:
            return _probe_metadata_from_active_avoidance(
                speculator,
                item_domain,
            )

        if response_type == "confirm":
            metadata = metadata_fn(domain)
            confirm_fn = getattr(speculator, "user_confirm_avoidance", None)
            active_avoidance = confirm_fn(domain) if callable(confirm_fn) else None
            ok = active_avoidance is not None
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "confirm",
                    speculator=speculator,
                    state_key="avoidance_probe_feedback_history",
                    metadata=metadata,
                )
                topics = topics_for_confirmed_avoidance(active_avoidance)
                summary = f"你确认了避开「{domain}」，已开始更新不喜欢方向。"
                _record_probe_cognition(
                    summary,
                    domain,
                    "confirmed",
                    source="avoidance_probe",
                )
                await _publish_probe_event("avoidance.confirmed", summary, domain)

                async def _apply_confirmed_avoidance() -> None:
                    try:
                        changes = await apply_new_dislikes(
                            memory=ctx.memory_manager,
                            database=getattr(ctx, "database", None)
                            or getattr(ctx.memory_manager, "_database", None),
                            embedding_service=getattr(ctx.soul_engine, "_embedding_service", None),
                            llm_service=getattr(ctx, "llm_service", None),
                            topics=topics,
                        )
                        if changes:
                            _record_probe_cognition(
                                f"避雷方向「{domain}」的不喜欢画像已更新。",
                                domain,
                                "confirmed",
                                source="avoidance_probe",
                                detail="\n".join(changes),
                            )
                    except Exception:
                        logger.exception(
                            "Background avoidance dislike writeback failed: %s",
                            domain,
                        )

                task = asyncio.create_task(_apply_confirmed_avoidance())
                _fire_and_forget_tasks.add(task)
                task.add_done_callback(_fire_and_forget_tasks.discard)
            return {"ok": ok, "action": "confirmed", "domain": domain}

        if response_type == "reject":
            metadata = metadata_fn(domain)
            reject_fn = getattr(speculator, "user_reject_avoidance", None)
            ok = bool(reject_fn(domain) if callable(reject_fn) else False)
            if ok:
                _record_probe_feedback_history(
                    domain,
                    "reject",
                    speculator=speculator,
                    state_key="avoidance_probe_feedback_history",
                    metadata=metadata,
                )
                _record_probe_cognition(
                    f"你表示并不需要避开「{domain}」，30 天内不再推送。",
                    domain,
                    "rejected",
                    source="avoidance_probe",
                )
                await _publish_probe_event(
                    "avoidance.rejected",
                    f"已记录：你并不需要避开「{domain}」，30 天内不再推送。",
                    domain,
                )
            return {"ok": ok, "action": "rejected", "domain": domain}

        raw_message = str(payload.get("message", "")).strip()
        if not raw_message:
            raw_message = f"我想聊聊你猜我可能想避开的「{domain}」这个方向"
        contextual_message = f"[关于避雷方向「{domain}」的反馈] {raw_message}"
        if ctx.dialogue is None:
            return {"ok": False, "action": "chat", "domain": domain, "reply": "对话引擎暂不可用。"}

        concurrency = getattr(ctx.discovery_engine, "_concurrency", None)
        if concurrency is not None:
            concurrency.chat_active = True
        try:
            reply = await asyncio.wait_for(
                ctx.dialogue.respond(contextual_message),
                timeout=30,
            )
            sentiment, classifier = await _classify_probe_sentiment(
                raw_message,
                reply,
                domain,
            )
        except TimeoutError:
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "后台正忙，等一下再聊。",
            }
        except Exception:
            logger.exception("Dialogue failed for avoidance probe chat: %s", domain)
            return {
                "ok": False,
                "action": "chat",
                "domain": domain,
                "reply": "聊天出了点问题，稍后再试。",
            }
        finally:
            if concurrency is not None:
                concurrency.chat_active = False

        if sentiment == "negative":
            chat_response = "avoidance_chat_confirmed"
            resulting_action = "confirmed"
            speculator.observe(
                [
                    {
                        "event_type": "dislike",
                        "title": domain,
                        "metadata": {
                            "feedback_type": "dislike",
                            "user_message": raw_message,
                            "source": "avoidance_probe_chat",
                        },
                    }
                ]
            )
            summary = f"你确认「{domain}」偏向不喜欢，确认度 +1。"
        elif sentiment in {"strong_positive", "weak_positive"}:
            chat_response = "avoidance_chat_rejected"
            resulting_action = "rejected"
            reject_fn = getattr(speculator, "user_reject_avoidance", None)
            if callable(reject_fn):
                reject_fn(domain, cooldown_days=14)
            summary = f"你表示其实不排斥「{domain}」，已暂时搁置 14 天。"
        else:
            chat_response = "avoidance_chat_neutral"
            resulting_action = "none"
            summary = f"关于避雷方向「{domain}」你说：{raw_message}"

        _record_probe_feedback_history(
            domain,
            chat_response,
            speculator=speculator,
            message=raw_message,
            classification=sentiment,
            classifier=classifier,
            resulting_action=resulting_action,
            state_key="avoidance_probe_feedback_history",
            metadata_fn=metadata_fn,
        )
        detail = f"你的反馈：{raw_message}\n阿b的回复：{reply}"
        _record_probe_cognition(
            summary,
            domain,
            "chat",
            source="avoidance_probe",
            detail=detail,
        )
        await _publish_probe_event("avoidance.chat", summary, domain)
        return JSONResponse(
            content={"ok": True, "action": "chat", "domain": domain, "reply": reply}
        )

    @app.post("/api/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackIn) -> FeedbackResponse:
        feedback_type = payload.feedback_type.strip().lower()
        note = payload.note.strip()
        if feedback_type not in {"like", "dislike", "comment", "dismiss"}:
            raise HTTPException(status_code=422, detail="Unsupported feedback type.")
        if feedback_type == "comment" and not note:
            raise HTTPException(status_code=422, detail="Comment feedback requires note.")

        recommendation = ctx.database.get_recommendation_by_id(payload.recommendation_id)
        if recommendation is None:
            raise HTTPException(status_code=404, detail="Recommendation not found.")

        ctx.database.update_recommendation_feedback(
            payload.recommendation_id,
            feedback_type=feedback_type,
            feedback_note=note,
        )
        from openbiliclaw.sources.event_format import (
            SOURCE_BILIBILI,
            build_event,
        )

        rec_title = str(recommendation.get("title", ""))
        # Tailor a natural-language context per feedback type — the
        # "feedback" verb in the generic table doesn't capture the
        # like/dislike/comment distinction the LLM cares about.
        feedback_label = {
            "like": "点赞了",
            "dislike": "踩了",
            "comment": "评论了",
            "dismiss": "忽略了",
        }.get(feedback_type, "反馈了")
        feedback_context = f"在 B 站{feedback_label}《{rec_title}》"
        if note:
            feedback_context = f"{feedback_context},备注:{note}"
        await ctx.memory_manager.propagate_event(
            build_event(
                event_type="feedback",
                source_platform=SOURCE_BILIBILI,
                title=rec_title,
                context=feedback_context,
                metadata={
                    "recommendation_id": payload.recommendation_id,
                    "bvid": recommendation.get("bvid", ""),
                    "feedback_type": feedback_type,
                    "feedback_note": note,
                },
            )
        )
        buffer_domain, buffer_specifics = _recommendation_buffer_domain(recommendation)
        if feedback_type == "like":
            _record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="card_like",
                evidence_id=str(recommendation.get("bvid", "")),
            )
        elif feedback_type == "dislike":
            _record_exploration_buffer_event(
                domain=buffer_domain,
                specifics=buffer_specifics,
                source_event="negative",
                evidence_id=str(recommendation.get("bvid", "")),
            )
        record_immediate_feedback_cognition = getattr(
            ctx.soul_engine,
            "record_immediate_feedback_cognition",
            None,
        )
        if callable(record_immediate_feedback_cognition):
            with suppress(Exception):
                record_immediate_feedback_cognition(
                    feedback_type=feedback_type,
                    title=str(recommendation.get("title", "")),
                    note=note,
                )
        _schedule_post_feedback_tasks()
        return FeedbackResponse(
            ok=True,
            recommendation_id=payload.recommendation_id,
            feedback_type=feedback_type,
        )

    @app.post(
        "/api/recommendation-click",
        response_model=RecommendationClickResponse,
    )
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

    @app.get("/api/topics", response_model=None)
    def api_topics_list() -> list[dict[str, object]]:
        """List all topics with item counts (newest first)."""
        import json as _json

        topics = ctx.database.list_topics(include_paused=True)
        out: list[dict[str, object]] = []
        for t in topics:
            out.append(
                {
                    "id": t["id"],
                    "name": t["name"],
                    "slug": t["slug"],
                    "description": t.get("description") or "",
                    "keywords": _json.loads(t.get("keywords") or "[]"),
                    "platforms": _json.loads(t.get("platforms") or '["bilibili"]'),
                    "status": t.get("status") or "active",
                    "item_count": int(t.get("item_count") or 0),
                    "last_collected_at": t.get("last_collected_at"),
                    "created_at": t.get("created_at"),
                    "updated_at": t.get("updated_at"),
                }
            )
        return out

    @app.post("/api/topics", response_model=None)
    async def api_topics_create(payload: TopicCreateIn) -> dict[str, object]:
        """Create a new topic. slug must be url-safe; keywords/platforms optional."""
        import json as _json

        name = (payload.name or "").strip()
        slug = (payload.slug or "").strip().lower()
        if not name or not slug:
            raise HTTPException(status_code=422, detail="name and slug are required.")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            raise HTTPException(status_code=422, detail="slug 只能包含小写字母、数字和连字符。")
        if ctx.database.get_topic_by_slug(slug) is not None:
            raise HTTPException(status_code=409, detail=f"slug 已存在: {slug}")
        keywords = list(payload.keywords or [])
        platforms = list(payload.platforms or ["bilibili"])
        try:
            topic_id = ctx.database.create_topic(
                name=name,
                slug=slug,
                description=(payload.description or "").strip(),
                keywords=keywords,
                platforms=platforms,
            )
        except Exception:
            logger.exception("create_topic failed")
            raise HTTPException(
                status_code=409, detail="专题创建失败（名称或 slug 冲突？）"
            ) from None
        topic = ctx.database.get_topic_by_id(topic_id)
        assert topic is not None  # just created
        return {
            "id": topic["id"],
            "name": topic["name"],
            "slug": topic["slug"],
            "description": topic.get("description") or "",
            "keywords": _json.loads(topic.get("keywords") or "[]"),
            "platforms": _json.loads(topic.get("platforms") or '["bilibili"]'),
            "status": topic.get("status") or "active",
            "item_count": 0,
        }

    @app.get("/api/topics/{slug}", response_model=None)
    def api_topic_detail(slug: str) -> dict[str, object]:
        """Topic detail + collected items (newest first, paginated)."""
        import json as _json

        topic = ctx.database.get_topic_by_slug(slug)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"未找到专题: {slug}")
        items = ctx.database.get_topic_items(topic["id"], limit=200)
        return {
            "id": topic["id"],
            "name": topic["name"],
            "slug": topic["slug"],
            "description": topic.get("description") or "",
            "keywords": _json.loads(topic.get("keywords") or "[]"),
            "platforms": _json.loads(topic.get("platforms") or '["bilibili"]'),
            "status": topic.get("status") or "active",
            "item_count": int(topic.get("item_count") or 0),
            "last_collected_at": topic.get("last_collected_at"),
            "created_at": topic.get("created_at"),
            "items": items,
        }

    @app.post("/api/topics/{slug}/collect", response_model=None)
    async def api_topic_collect(slug: str) -> dict[str, object]:
        """Trigger a collection pass for one topic (autocli, may take ~30s+)."""
        topic = ctx.database.get_topic_by_slug(slug)
        if topic is None:
            raise HTTPException(status_code=404, detail=f"未找到专题: {slug}")
        try:
            import importlib.util as _ilu

            script = _PROJECT_ROOT / "scripts" / "collect_topic.py"
            spec = _ilu.spec_from_file_location("collect_topic", script)
            assert spec and spec.loader is not None
            module = _ilu.module_from_spec(spec)
            spec.loader.exec_module(module)
            stats = module.collect_topic(ctx.database, topic, limit=8)
        except Exception:
            logger.exception("topic collect failed")
            raise HTTPException(status_code=500, detail="专题搜集失败，详见服务日志。") from None
        return {
            "ok": True,
            "slug": slug,
            "new": stats["new"],
            "dup": stats["dup"],
            "failed": stats["failed"],
            "searches": stats["searches"],
            "item_count": ctx.database.count_topic_items(topic["id"]),
        }

    @app.post("/api/insights/feedback", response_model=InsightFeedbackResponse)
    async def insight_feedback(payload: InsightFeedbackIn) -> InsightFeedbackResponse:
        """Calibrate an insight hypothesis from a user confirm/reject.

        The popup's insight cards surface ``active_insights`` (hypothesis +
        confidence). This endpoint routes a confirm/reject back into
        ``SoulEngine.update_from_feedback`` so the hypothesis is validated and
        re-weighted (confirm → confidence ≥0.75; reject → ≤0.35), closing the
        loop that was previously implemented but unwired.
        """
        signal = payload.signal.strip().lower()
        if signal not in {"confirm", "like", "support", "reject", "dislike", "deny"}:
            raise HTTPException(status_code=422, detail="Unsupported insight feedback signal.")
        hypothesis = payload.hypothesis.strip()
        if not hypothesis:
            raise HTTPException(status_code=422, detail="hypothesis is required.")
        if ctx.soul_engine is None:
            raise HTTPException(status_code=503, detail="Soul engine not ready.")

        result = await ctx.soul_engine.update_from_feedback(
            {"hypothesis": hypothesis, "signal": signal}
        )
        return InsightFeedbackResponse(
            ok=True,
            matched=bool(result.get("matched", False)),
            hypothesis=str(result.get("hypothesis", hypothesis)),
            signal=str(result.get("signal", signal)),
            validated=bool(result.get("validated", False)),
            confidence=float(result.get("confidence", 0.0)),
        )

    # ── Source recipe management endpoints ──────────────────────────

    @app.get("/api/sources")
    def list_sources() -> dict[str, Any]:
        """Return all source recipes."""
        recipes = ctx.database.get_all_recipes()
        return {"items": recipes}

    @app.post("/api/sources", status_code=201)
    def create_source(payload: dict[str, Any]) -> dict[str, Any]:
        """Create a new source recipe."""
        import uuid

        recipe_id = payload.get("id") or str(uuid.uuid4())
        source_type = payload.get("source_type", "")
        name = payload.get("name", "")
        strategy = payload.get("strategy", "")
        if not source_type or not name or not strategy:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=422,
                detail="source_type, name, and strategy are required",
            )
        recipe = {
            "id": recipe_id,
            "source_type": source_type,
            "name": name,
            "strategy": strategy,
            "config": payload.get("config", {}),
            "target_share": payload.get("target_share", 4),
            "enabled": payload.get("enabled", True),
            "created_by": payload.get("created_by", "user"),
        }
        ctx.database.save_source_recipe(recipe)
        return {"ok": True, "recipe": recipe}

    @app.put("/api/sources/{recipe_id}")
    def update_source(recipe_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Update fields of an existing source recipe."""
        updated = ctx.database.update_recipe(recipe_id, **payload)
        if not updated:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Recipe not found")
        return {"ok": True, "id": recipe_id}

    @app.delete("/api/sources/{recipe_id}")
    def delete_source(recipe_id: str) -> dict[str, Any]:
        """Delete a source recipe (system recipes cannot be deleted)."""
        # Check if it's a system recipe
        all_recipes = ctx.database.get_all_recipes()
        target = next((r for r in all_recipes if r["id"] == recipe_id), None)
        if target and target.get("created_by") == "system":
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="System recipes cannot be deleted")
        deleted = ctx.database.delete_recipe(recipe_id)
        if not deleted:
            from fastapi import HTTPException

            raise HTTPException(status_code=404, detail="Recipe not found")
        return {"ok": True, "id": recipe_id}

    # ── XHS observed URL ingestion endpoint ─────────────────────────

    xhs_max_urls_per_batch = 50
    xhs_url_prefix = "https://www.xiaohongshu.com/"

    def _discovery_candidate_pending_cap() -> int:
        from openbiliclaw.discovery.candidate_pool import discovery_candidate_pending_cap

        scheduler = getattr(config, "scheduler", None)
        target = int(getattr(scheduler, "pool_target_count", 300) or 300)
        return discovery_candidate_pending_cap(target)

    def _intish(value: Any) -> int:
        if isinstance(value, bool):
            return 0
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _cache_bili_search_videos(
        database: Any,
        videos: list[dict[str, Any]],
        *,
        query: str = "",
        source_keyword_id: int | None = None,
    ) -> int:
        """Enqueue extension-collected Bilibili search videos for evaluation."""
        from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
        from openbiliclaw.discovery.engine import DiscoveredContent

        enqueue = getattr(database, "enqueue_discovery_candidates", None)
        if not callable(enqueue):
            return 0
        writes = []
        for video in videos:
            bvid = str(video.get("bvid") or video.get("content_id") or "").strip()
            if not bvid:
                continue
            title = str(video.get("title") or "").strip()
            if not title:
                continue
            up_name = str(
                video.get("up_name") or video.get("author_name") or video.get("author") or ""
            ).strip()
            content_url = str(video.get("content_url") or video.get("url") or "").strip()
            if not content_url:
                content_url = f"https://www.bilibili.com/video/{bvid}"
            tags_raw = video.get("tags")
            tags = (
                [str(item).strip() for item in tags_raw if str(item).strip()]
                if isinstance(tags_raw, list)
                else []
            )
            item = DiscoveredContent(
                bvid=bvid,
                title=title,
                up_name=up_name,
                up_mid=_intish(video.get("up_mid") or video.get("mid")),
                cover_url=str(video.get("cover_url") or video.get("pic") or "").strip(),
                duration=_intish(video.get("duration")),
                view_count=_intish(video.get("view_count") or video.get("play")),
                like_count=_intish(video.get("like_count") or video.get("likes")),
                favorite_count=_intish(
                    video.get("favorite_count") or video.get("favorites") or video.get("favorite")
                ),
                danmaku_count=_intish(
                    video.get("danmaku_count") or video.get("danmaku") or video.get("video_review")
                ),
                comment_count=_intish(
                    video.get("comment_count") or video.get("reply") or video.get("review")
                ),
                share_count=_intish(video.get("share_count") or video.get("share")),
                tags=tags,
                description=str(video.get("description") or video.get("desc") or "").strip(),
                source_strategy="bili-extension-search",
                content_id=bvid,
                content_url=content_url,
                source_platform="bilibili",
                author_name=up_name,
                score_threshold=0.60,
                source_keyword_id=source_keyword_id,
            )
            writes.append(
                discovered_content_to_candidate_write(
                    item,
                    source_context="bili-extension-search",
                    raw_payload={
                        "bvid": bvid,
                        "query": query,
                        "url": content_url,
                        "admission_policy": "observed",
                        "score_threshold": 0.60,
                    },
                )
            )
        if not writes:
            return 0
        try:
            return int(enqueue(writes, max_pending_per_source=_discovery_candidate_pending_cap()))
        except TypeError:
            return int(enqueue(writes))

    def _mark_bili_task_keyword_terminal(payload_json: str | None, *, success: bool) -> None:
        from openbiliclaw.sources.bili_tasks import source_keyword_id_from_bili_task

        keyword_id = source_keyword_id_from_bili_task(payload_json)
        if keyword_id is None:
            return
        method = "mark_keyword_used" if success else "mark_keyword_failed"
        mark = getattr(ctx.database, method, None)
        if not callable(mark):
            return
        with suppress(Exception):
            mark(keyword_id)

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
            row = database.conn.execute(
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

    def _backfill_xhs_tokens(database: Any, urls: list[str]) -> int:
        """Upgrade cached xhs rows whose content_url lacks xsec_token.

        The extension often observes the same note twice — once from a
        search result page (no token in ``<a href>``) and once from an
        explore-feed card (token present). When a tokenized URL arrives
        later, rewrite the previously-cached bare URL so share links
        don't dead-end at xhs's login wall.
        """
        updated = 0
        for url in urls:
            if "xsec_token=" not in url:
                continue
            try:
                path = urlparse(url).path.strip("/")
                note_id = path.rsplit("/", 1)[-1] if path else ""
            except Exception:
                continue
            if not note_id:
                continue
            try:
                cursor = database.conn.execute(
                    "UPDATE content_cache SET content_url=? "
                    "WHERE bvid=? AND source_platform='xiaohongshu' "
                    "AND (content_url = '' OR content_url NOT LIKE '%xsec_token=%')",
                    (url, note_id),
                )
                updated += cursor.rowcount or 0
            except Exception:
                pass
            try:
                cursor = database.conn.execute(
                    "UPDATE discovery_candidates "
                    "SET content_url=?, last_seen_at=CURRENT_TIMESTAMP "
                    "WHERE source_platform='xiaohongshu' AND content_id=? "
                    "AND (content_url = '' OR content_url NOT LIKE '%xsec_token=%')",
                    (url, note_id),
                )
                updated += cursor.rowcount or 0
            except Exception:
                continue
        # Commit unconditionally: a bare UPDATE (even with zero matched
        # rows) opens a write transaction in sqlite3's default isolation
        # mode and holds a RESERVED lock until commit. Skipping the commit
        # when `updated == 0` leaked the transaction, blocking other
        # connections' writers (e.g. the thread-local connection used by
        # tests / threadpool requests) behind a stale lock.
        if database.conn.in_transaction:
            with suppress(Exception):
                database.conn.commit()
        return updated

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

    def _normalize_self_info(raw: Any) -> dict[str, str] | None:
        """Validate + normalize a self_info-shaped dict.

        Returns ``{"user_id": ..., "nickname": ...}`` if either field is
        non-empty, otherwise ``None``.
        """
        if not isinstance(raw, dict):
            return None
        user_id = str(raw.get("user_id", "") or "").strip()
        nickname = str(raw.get("nickname", "") or "").strip()
        if not user_id and not nickname:
            return None
        return {"user_id": user_id, "nickname": nickname}

    def _extract_self_info_from_payload(payload: Any) -> dict[str, str] | None:
        """Pull self_info from any XHS ingest payload.

        v0.3.57+: extension v0.3.10 sends self_info at the **payload top
        level** for every ingest path (passive ``observed-urls``, search /
        creator ``task-result``, bootstrap_profile ``task-result``). The
        legacy bootstrap-only nested location
        ``debug.xhs_bootstrap.steps[*].self_info`` (v0.3.48 / extension
        v0.3.9) is kept as fallback for older extensions.
        """
        if not isinstance(payload, dict):
            return None
        # 1) New top-level location.
        info = _normalize_self_info(payload.get("self_info"))
        if info is not None:
            return info
        # 2) Legacy bootstrap-debug nested location.
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            return None
        bootstrap = debug.get("xhs_bootstrap")
        if not isinstance(bootstrap, dict):
            return None
        steps = bootstrap.get("steps")
        if not isinstance(steps, list):
            return None
        for step in steps:
            if not isinstance(step, dict):
                continue
            info = _normalize_self_info(step.get("self_info"))
            if info is not None:
                return info
        return None

    def _persist_xhs_self_info(self_info: dict[str, str]) -> None:
        """Save self info into discovery_runtime_state if not already there."""
        memory_manager = getattr(ctx.runtime_controller, "memory_manager", None)
        if memory_manager is None:
            return
        try:
            state = memory_manager.load_discovery_runtime_state()
            existing = state.get("xhs_self_info")
            # Idempotent: only write when content changes (avoid sqlite churn).
            if isinstance(existing, dict) and existing == self_info:
                return
            update_state = getattr(memory_manager, "update_discovery_runtime_state", None)
            if callable(update_state):
                update_state(
                    lambda runtime_state: runtime_state.update({"xhs_self_info": self_info})
                )
            else:
                state["xhs_self_info"] = self_info
                memory_manager.save_discovery_runtime_state(state)
            logger.info(
                "xhs self_info persisted: user_id=%s nickname=%r",
                self_info.get("user_id", ""),
                self_info.get("nickname", ""),
            )
            # Immediately purge any self-authored rows that slipped into
            # the pool before this self_info was known.
            suppressed = _purge_self_authored_pool_items(ctx.database, self_info)
            if suppressed:
                logger.info(
                    "xhs self_info purge: suppressed %d self-authored pool item(s) (nickname=%r)",
                    suppressed,
                    self_info.get("nickname", ""),
                )
        except Exception:
            logger.exception("Failed to persist xhs self_info")

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

    def _is_self_authored_note(note: dict[str, Any], self_info: dict[str, str]) -> bool:
        """Check whether a note's author matches the logged-in user.

        Both user_id and nickname can match — XHS sometimes only ships
        nickname in note metadata (no author user_id), other times both.
        Treat the match as case-insensitive on the trimmed values.
        """
        if not self_info:
            return False
        nickname = self_info.get("nickname", "").strip().lower()
        user_id = self_info.get("user_id", "").strip().lower()
        author = str(note.get("author", "") or "").strip().lower()
        if author and nickname and author == nickname:
            return True
        author_id = str(note.get("author_id", "") or "").strip().lower()
        return bool(author_id and user_id and author_id == user_id)

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

    def _cache_xhs_notes(
        database: Any,
        notes: list[dict[str, Any]],
        page_type: str,
        self_info: dict[str, str] | None = None,
        *,
        source_keyword_id: int | None = None,
    ) -> int:
        """Enqueue xhs note metadata from the extension into discovery_candidates.

        ``self_info`` (v0.3.48+) lets the caller pass the just-extracted
        login fingerprint from the same request — avoids a round-trip
        through ``discovery_runtime_state`` and works against test
        stubs that haven't implemented the runtime-state API.  When
        ``None``, falls back to the persisted state.

        ``source_keyword_id`` (P1.8) is the ``discovery_keywords.id`` carried on
        the originating xhs *search* task payload. XHS is truly async, so the id
        cannot be stamped at search time — it rides the task and is threaded onto
        each ingested candidate here so admission can backfill the keyword's
        yield. ``None`` for passive / observed / non-planner ingests.
        """
        from openbiliclaw.discovery.candidate_pool import discovered_content_to_candidate_write
        from openbiliclaw.discovery.engine import DiscoveredContent

        enqueue = getattr(database, "enqueue_discovery_candidates", None)
        if not callable(enqueue):
            return 0
        if self_info is None:
            self_info = _load_xhs_self_info()
        writes = []
        skipped_self = 0
        for note in notes:
            if _is_self_authored_note(note, self_info):
                skipped_self += 1
                continue
            url = note.get("url", "")
            if not isinstance(url, str) or not url.startswith(xhs_url_prefix):
                continue
            # Extract note ID from URL path
            try:
                path = urlparse(url).path.strip("/")
                note_id = path.rsplit("/", 1)[-1] if path else ""
            except Exception:
                note_id = ""
            if not note_id:
                continue

            title = str(note.get("title", "") or "").strip()
            if not title:
                continue  # Skip notes with empty title — they produce blank recommendation cards
            author = str(note.get("author", "") or "").strip()
            cover_url = str(note.get("cover_url", "") or "").strip()
            best_url = _pick_best_xhs_url(database, note_id, url)

            item = DiscoveredContent(
                bvid=note_id,
                title=title,
                up_name=author,
                cover_url=cover_url,
                view_count=_intish(note.get("view_count") or note.get("views")),
                like_count=_intish(note.get("like_count") or note.get("likes")),
                collect_count=_intish(
                    note.get("collect_count")
                    or note.get("favorite_count")
                    or note.get("favorites")
                    or note.get("collects")
                ),
                comment_count=_intish(note.get("comment_count") or note.get("comments")),
                share_count=_intish(note.get("share_count") or note.get("shares")),
                description=str(
                    note.get("description") or note.get("desc") or note.get("text") or ""
                ),
                source_strategy=f"xhs-extension-{page_type}",
                content_id=note_id,
                content_url=best_url,
                source_platform="xiaohongshu",
                author_name=author,
                source_keyword_id=source_keyword_id,
            )
            writes.append(
                discovered_content_to_candidate_write(
                    item,
                    source_context=page_type,
                    raw_payload={
                        "note_id": note_id,
                        "url": best_url,
                        "page_type": page_type,
                        "title": title,
                        "author": author,
                        "cover_url": cover_url,
                        "admission_policy": "observed",
                    },
                )
            )
        if skipped_self > 0:
            logger.info(
                "xhs ingest filter: dropped %d self-authored note(s) (%s)",
                skipped_self,
                page_type,
            )
        if not writes:
            return 0
        try:
            return int(enqueue(writes, max_pending_per_source=_discovery_candidate_pending_cap()))
        except TypeError:
            return int(enqueue(writes))

    @app.post("/api/sources/xhs/observed-urls")
    async def ingest_xhs_observed_urls(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept xhs note URLs + optional metadata the extension collected.

        Body: ``{ "urls": [...], "notes": [{url, title, author, cover_url}], "page_type": "..." }``

        When ``notes`` is present, metadata is normalized into
        ``discovery_candidates``.  The shared discovery-candidate drain then
        evaluates and admits accepted notes through the same path as other
        platforms.
        """
        from fastapi import HTTPException

        urls_raw: list[str] = payload.get("urls", [])
        notes_raw: list[dict[str, Any]] = payload.get("notes", [])
        page_type: str = payload.get("page_type", "other")

        if not urls_raw and not notes_raw:
            raise HTTPException(status_code=422, detail="urls or notes must be non-empty")
        if len(urls_raw) > xhs_max_urls_per_batch:
            raise HTTPException(
                status_code=422,
                detail=f"Too many URLs (max {xhs_max_urls_per_batch})",
            )

        # v0.3.57+: passive collector (extension v0.3.10) piggybacks
        # self_info on every observed-urls request. Persist on first
        # arrival so subsequent requests without self_info still filter
        # via the loaded state.
        self_info_now = _extract_self_info_from_payload(payload)
        if self_info_now:
            _persist_xhs_self_info(self_info_now)
        self_info_for_filter = self_info_now or _load_xhs_self_info()

        # Filter to valid xhs note URLs
        valid_urls = [
            u
            for u in urls_raw
            if isinstance(u, str) and u.startswith(xhs_url_prefix) and "/explore/" in u
        ]

        # Store bare URLs for tracking
        if valid_urls:
            ctx.database.save_xhs_observed_urls(valid_urls, page_type)
            _backfill_xhs_tokens(ctx.database, valid_urls)

        # Store rich notes into the shared pending evaluation pool.
        enqueued = 0
        if notes_raw:
            enqueued = _cache_xhs_notes(
                ctx.database,
                notes_raw,
                page_type,
                self_info=self_info_for_filter or None,
            )
            if enqueued:
                asyncio.create_task(_drain_discovery_candidates_once())

        return {
            "ok": True,
            "accepted": len(valid_urls),
            "enqueued": enqueued,
        }

    @app.post("/api/sources/xhs/tokens")
    def ingest_xhs_tokens(payload: dict[str, Any]) -> dict[str, Any]:
        """Ingest ``(note_id, xsec_token)`` pairs harvested by the MAIN-
        world fetch sniffer inside ``dist/main/xhs-token-sniffer.js``.

        We rebuild the full tokenized URL from each pair and feed it
        through ``_backfill_xhs_tokens`` so previously-cached bare URLs
        (the typical search-page-sourced ones) get upgraded in place.
        Without this, clicking an xhs recommendation trips xhs's 300031
        access-denied gating because the stored URL lacks xsec_token.
        """
        raw = payload.get("pairs", [])
        if not isinstance(raw, list) or not raw:
            return {"ok": True, "upgraded": 0}
        urls: list[str] = []
        for pair in raw:
            if not isinstance(pair, dict):
                continue
            note_id = str(pair.get("note_id", "") or "").strip()
            token = str(pair.get("xsec_token", "") or "").strip()
            # Guard against the noise the sniffer's deep-walk can surface
            # — e.g. 24-hex ids that aren't notes. The backfill UPDATE is
            # narrow (bvid match), so the worst case of a false id is a
            # no-op, but the token must at least be non-empty.
            if not note_id or not token:
                continue
            urls.append(f"{xhs_url_prefix}explore/{note_id}?xsec_token={token}")
        upgraded = _backfill_xhs_tokens(ctx.database, urls)
        return {"ok": True, "upgraded": upgraded}

    # ── Bilibili extension search fallback endpoints ────────────────

    from openbiliclaw.sources.bili_tasks import (
        BiliTaskQueue,
        source_keyword_id_from_bili_task,
    )

    _bili_task_queue: BiliTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _bili_task_queue = BiliTaskQueue(ctx.database)

    @app.get("/api/sources/bili/next-task")
    def bili_next_task(response: Any = None) -> Any:
        """Claim and return the oldest runnable Bilibili extension task."""
        from starlette.responses import Response

        if _bili_task_queue is None:
            return Response(status_code=204)
        task = _bili_task_queue.next_pending()
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/bili/task-result")
    async def bili_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept Bilibili extension search results and enqueue candidates."""
        task_id = str(payload.get("task_id", "") or "").strip()
        status = str(payload.get("status", "") or "").strip()
        videos = [video for video in payload.get("videos", []) if isinstance(video, dict)]
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _bili_task_queue is None:
            return {"ok": True, "enqueued": 0}

        task = _bili_task_queue.get(task_id)
        task_payload_json = str(task.get("payload_json") or "") if task else ""

        if status in {"partial", "ok", "empty"}:
            is_final = status in {"ok", "empty"}
            added_videos = _bili_task_queue.merge_result(
                task_id,
                videos=videos if videos else None,
                debug=debug,
                complete=is_final,
            )
            if is_final:
                _mark_bili_task_keyword_terminal(task_payload_json, success=status == "ok")
            task_payload: dict[str, Any] = {}
            if task_payload_json:
                with suppress(Exception):
                    parsed = json.loads(task_payload_json)
                    if isinstance(parsed, dict):
                        task_payload = parsed
            query = str(task_payload.get("query") or task_payload.get("keyword") or "").strip()
            source_keyword_id = source_keyword_id_from_bili_task(task_payload_json)
            enqueued = 0
            if added_videos:
                enqueued = _cache_bili_search_videos(
                    ctx.database,
                    added_videos,
                    query=query,
                    source_keyword_id=source_keyword_id,
                )
                if enqueued:
                    asyncio.create_task(_drain_discovery_candidates_once())
            return {"ok": True, "enqueued": enqueued}

        _bili_task_queue.fail(task_id, error=str(payload.get("error", "") or ""), debug=debug)
        _mark_bili_task_keyword_terminal(task_payload_json, success=False)
        return {"ok": True, "enqueued": 0}

    @app.post("/api/sources/bili/kick")
    async def bili_task_kick() -> dict[str, Any]:
        """Broadcast `bili_task_available` over runtime-stream."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "bili_task_available", "source": "task_kick"})
        return {"ok": True}

    # ── XHS task queue endpoints (extension dispatcher) ──────────────

    from openbiliclaw.sources.xhs_tasks import (
        XhsCreatorStore,
        XhsTaskQueue,
        xhs_bootstrap_note_key,
        xhs_bootstrap_notes_to_events,
    )

    # Guard: only initialise when ctx.database is a real Database (has .conn).
    # Tests that pass database=object() as a stub won't trigger table creation.
    _xhs_task_queue: XhsTaskQueue | None = None
    _xhs_creator_store: XhsCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _xhs_task_queue = XhsTaskQueue(ctx.database)
        _xhs_creator_store = XhsCreatorStore(ctx.database)

    @app.get("/api/sources/xhs/next-task")
    def xhs_next_task(response: Any = None) -> Any:
        """Claim and return the oldest runnable xhs task, or 204 if none."""
        from starlette.responses import Response

        # 204 No Content responses MUST NOT carry a body (RFC 7230).
        # JSONResponse(204, None) serialises None to "null" (4 bytes),
        # then GZipMiddleware (minimum_size=0) wraps it into ~20 bytes
        # of gzip stream while Content-Length stays at 4, which trips
        # h11's strict "Too much data for declared Content-Length"
        # check on every poll. Use a body-less Response instead.
        if _xhs_task_queue is None:
            return Response(status_code=204)
        task = _xhs_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/xhs/task-result")
    async def xhs_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a task result from the extension dispatcher."""
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        urls = payload.get("urls", [])
        notes = [note for note in payload.get("notes", []) if isinstance(note, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _xhs_task_queue is None:
            return {"ok": True}

        task = _xhs_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_notes = _xhs_task_queue.merge_result(
                task_id,
                urls=urls,
                notes=notes if notes else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            # Unified keyword planner lifecycle (P1.7): XHS is truly async, so a
            # claimed search word stays ``executing`` until this terminal
            # callback. On a final ``ok`` mark its ``source_keyword_id`` word
            # ``used`` (a ``partial`` is not terminal → leave it ``executing``).
            if is_final and task is not None:
                mark_keyword_terminal_from_xhs_task(
                    ctx.database, task.get("payload_json"), success=True
                )
            # v0.3.48+: piggyback self_info from bootstrap debug payload.
            # v0.3.57+: also accept self_info at the payload top level for
            # search / creator / passive paths via extension v0.3.10.
            # Persist immediately so future requests can also consult it,
            # AND use the just-extracted value in this request's
            # downstream filters (skip a state round-trip that some
            # in-process test stubs don't implement).
            self_info_from_request = _extract_self_info_from_payload(payload)
            if self_info_from_request:
                _persist_xhs_self_info(self_info_from_request)
            self_info_now = self_info_from_request or _load_xhs_self_info()
            # gui-init D1: the result is always persisted above (merge_result)
            # so init's own collector can read it. During an active init:
            #  - skip ALL live discovery-pool writes (stage 4 owns the pool);
            #  - skip profile propagation for tasks NOT owned by this run (stale
            #    / steady-state completions), but DO propagate init-OWNED
            #    bootstrap results through the normal deduped path so the source
            #    signals land in memory exactly once (handles force re-init).
            # Computed BEFORE the URL/token backfill (_backfill_xhs_tokens writes
            # content_cache + discovery_candidates).
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            # Store discovered URLs + metadata
            valid_urls = [u for u in urls if isinstance(u, str) and u.startswith(xhs_url_prefix)]
            if valid_urls and not _init_busy:
                ctx.database.save_xhs_observed_urls(valid_urls, "task")
                _backfill_xhs_tokens(ctx.database, valid_urls)
            if added_notes and not _init_busy:
                # P1.8: a planner-driven xhs *search* task carries its
                # ``source_keyword_id`` on the payload → thread it onto the
                # ingested candidates so admission backfills the keyword's yield.
                # Passive / non-search tasks have no id → plain None.
                task_source_keyword_id = (
                    source_keyword_id_from_xhs_task(task.get("payload_json"))
                    if task is not None
                    else None
                )
                enqueued = _cache_xhs_notes(
                    ctx.database,
                    added_notes,
                    "task",
                    self_info_now,
                    source_keyword_id=task_source_keyword_id,
                )
                if enqueued:
                    asyncio.create_task(_drain_discovery_candidates_once())
            if task_type == "bootstrap_profile" and added_notes and not _skip_profile:
                fresh_notes, note_keys_by_index = _filter_new_source_bootstrap_items(
                    "xhs",
                    added_notes,
                    xhs_bootstrap_note_key,
                )
                # Filter self-authored notes from event propagation —
                # otherwise the user's own posts get treated as their
                # own "favorite/like" signals and warp the soul profile.
                propagated = 0
                skipped_self = 0
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, note in enumerate(fresh_notes):
                    if _is_self_authored_note(note, self_info_now):
                        skipped_self += 1
                        continue
                    for event in xhs_bootstrap_notes_to_events([note]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = note_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                        propagated += 1
                # During init, skip the incremental profile-update pipeline even
                # for owned results — run_guided_init's explicit analyze/build
                # owns the profile this run, and a force re-init has an existing
                # profile that _ingest would otherwise mutate concurrently
                # (gui-init review). Memory propagation above still records the
                # signals; the pipeline resumes for steady-state after init.
                if not _init_busy:
                    await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("xhs", propagated_keys)
                if skipped_self > 0:
                    logger.info(
                        "xhs bootstrap propagate: dropped %d self-authored note(s) (%d propagated)",
                        skipped_self,
                        propagated,
                    )
        else:
            _xhs_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)
            # Unified keyword planner lifecycle (P1.7): the async search failed →
            # mark its ``source_keyword_id`` word ``failed`` (retry via attempts).
            if task is not None:
                mark_keyword_terminal_from_xhs_task(
                    ctx.database, task.get("payload_json"), success=False
                )

        return {"ok": True}

    @app.get("/api/sources/xhs/creators")
    def xhs_list_creators() -> dict[str, Any]:
        """List all xhs creator subscriptions."""
        if _xhs_creator_store is None:
            return {"items": []}
        return {"items": _xhs_creator_store.list_all()}

    @app.post("/api/sources/xhs/creators", status_code=201)
    def xhs_add_creator(payload: dict[str, Any]) -> dict[str, Any]:
        """Add an xhs creator subscription."""
        from fastapi import HTTPException

        creator_id = payload.get("creator_id", "")
        creator_url = payload.get("creator_url", "")
        display_name = payload.get("display_name", "")

        if not creator_id or not creator_url:
            raise HTTPException(
                status_code=422,
                detail="creator_id and creator_url are required",
            )

        if _xhs_creator_store is None:
            raise HTTPException(status_code=503, detail="xhs not configured")
        _xhs_creator_store.add(creator_id, creator_url, display_name)
        return {"ok": True}

    @app.delete("/api/sources/xhs/creators/{sub_id}")
    def xhs_delete_creator(sub_id: int) -> dict[str, Any]:
        """Delete an xhs creator subscription."""
        from fastapi import HTTPException

        if _xhs_creator_store is None:
            raise HTTPException(status_code=503, detail="xhs not configured")
        deleted = _xhs_creator_store.delete(sub_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"ok": True}

    # ── X (Twitter) account subscriptions ──────────────────────────
    # No extension round-trip: the X producer fetches each subscription
    # server-side via XCreatorStrategy. This block only owns the
    # x_creator_subscriptions table + CRUD (mirrors the XHS creators above).

    from openbiliclaw.sources.x_tasks import XCreatorStore, normalize_handle

    _x_creator_store: XCreatorStore | None = None
    if hasattr(ctx.database, "conn"):
        _x_creator_store = XCreatorStore(ctx.database)

    @app.get("/api/sources/x/creators")
    def x_list_creators() -> dict[str, Any]:
        """List all X account subscriptions."""
        if _x_creator_store is None:
            return {"items": []}
        return {"items": _x_creator_store.list_all()}

    @app.post("/api/sources/x/creators", status_code=201)
    def x_add_creator(payload: dict[str, Any]) -> dict[str, Any]:
        """Add an X account subscription (idempotent; leading @ stripped)."""
        from fastapi import HTTPException

        handle = normalize_handle(str(payload.get("handle", "")))
        if not handle:
            raise HTTPException(status_code=422, detail="handle is required")

        if _x_creator_store is None:
            raise HTTPException(status_code=503, detail="x not configured")
        _x_creator_store.add(handle)
        return {"ok": True}

    @app.delete("/api/sources/x/creators/{sub_id}")
    def x_delete_creator(sub_id: int) -> dict[str, Any]:
        """Delete an X account subscription."""
        from fastapi import HTTPException

        if _x_creator_store is None:
            raise HTTPException(status_code=503, detail="x not configured")
        deleted = _x_creator_store.delete(sub_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return {"ok": True}

    # ── X (Twitter) source health (spec §7) ────────────────────────
    # Surfaces the persisted health state machine so the settings UI can
    # show login / rate-limit / block status (rendered in Task 12).

    @app.get("/api/sources/x/status", response_model=XStatusResponse)
    def x_source_status() -> XStatusResponse:
        """Return the current X source health (ok / cookie / rate-limit / block)."""
        if not hasattr(ctx.database, "conn"):
            return XStatusResponse()
        from openbiliclaw.storage.x_health import XSourceHealthStore

        health = XSourceHealthStore(ctx.database).get()
        return XStatusResponse(
            state=str(health.get("state", "ok")),
            consecutive_failures=int(health.get("consecutive_failures", 0)),
            feed_paused=bool(health.get("feed_paused", False)),
            cooldown_until=str(health.get("cooldown_until", "")),
            detail=str(health.get("detail", "")),
            updated_at=str(health.get("updated_at", "")),
        )

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

    @app.get("/api/sources/status", response_model=SourcesStatusResponse)
    def sources_status() -> SourcesStatusResponse:
        """Unified per-source login / cookie readiness for the settings pages.

        Local-only: each source's state is derived from config cookie fields,
        the Douyin cookie file/env, the count of token-bearing 小红书 cache
        rows, and the X health store — no outbound platform requests. See
        :class:`SourcesStatusResponse`. ``ready`` means a credential is present
        and structurally valid (not live-validated); only X reports a真正
        live-validated ``ok``.
        """
        from openbiliclaw.config import load_config

        cfg = load_config()
        srcs = cfg.sources

        # ── Bilibili: cookie present with the core login fields ──
        # config.toml is the mirror, data/bilibili_cookie.json is the runtime
        # store (CLI QR login writes only the file) — check both, like the
        # douyin/x branches resolve env + file.
        bili_cookie = str(getattr(cfg.bilibili, "cookie", "") or "")
        if not bili_cookie.strip():
            with suppress(Exception):
                from openbiliclaw.bilibili.auth import AuthManager

                bili_cookie = AuthManager(data_dir=cfg.data_path).load_cookie()
        bili_enabled = bool(getattr(srcs.bilibili, "enabled", True))
        has_fields = sum(
            1 for f in ("SESSDATA", "bili_jct", "DedeUserID") if f"{f}=" in bili_cookie
        )
        if getattr(cfg.bilibili, "auth_method", "cookie") == "none":
            bilibili = SourceStatusItem(
                enabled=bili_enabled,
                state="no_auth",
                detail="未启用 B 站登录（auth_method=none）。",
                logged_in=True,
            )
        elif has_fields >= 3:
            bilibili = SourceStatusItem(
                enabled=bili_enabled,
                state="ready",
                detail="Cookie 就绪（含 SESSDATA / bili_jct / DedeUserID）。",
                logged_in=True,
            )
        elif bili_cookie.strip():
            bilibili = SourceStatusItem(
                enabled=bili_enabled,
                state="partial",
                detail="Cookie 已配置，但缺少部分登录字段，可能未完整登录。",
            )
        else:
            bilibili = SourceStatusItem(
                enabled=bili_enabled,
                state="missing",
                detail="未配置 Cookie —— 在浏览器登录 bilibili.com，插件会自动同步。",
            )

        # ── 小红书: token-bearing cache rows = extension is syncing tokens ──
        # A bare COUNT(*) is sticky: one token row from weeks ago keeps the
        # status green forever after the extension stops syncing, while the
        # stored tokens are long dead (xhs 300031 access-denied). Gate "ready"
        # on recent activity instead — a token-bearing cache row discovered,
        # or a candidate token-backfilled (``_backfill_xhs_tokens`` refreshes
        # ``last_seen_at`` without touching ``discovered_at``), inside the
        # freshness window. Old-only rows degrade to ``stale``.
        xhs_enabled = bool(getattr(srcs.xiaohongshu, "enabled", False))
        xhs_tokens = 0
        xhs_fresh = 0
        if hasattr(ctx.database, "conn"):
            window = f"-{_xhs_token_fresh_hours} hours"
            try:
                row = ctx.database.conn.execute(
                    "SELECT COUNT(*), "
                    "COALESCE(SUM(discovered_at >= datetime('now', ?)), 0) "
                    "FROM content_cache "
                    "WHERE source_platform = 'xiaohongshu' "
                    "AND content_url LIKE '%xsec_token=%'",
                    (window,),
                ).fetchone()
                xhs_tokens = int(row[0]) if row else 0
                xhs_fresh = int(row[1]) if row else 0
            except Exception:  # pragma: no cover - defensive
                xhs_tokens = 0
                xhs_fresh = 0
            if xhs_tokens and not xhs_fresh:
                try:
                    row = ctx.database.conn.execute(
                        "SELECT COUNT(*) FROM discovery_candidates "
                        "WHERE source_platform = 'xiaohongshu' "
                        "AND content_url LIKE '%xsec_token=%' "
                        "AND last_seen_at >= datetime('now', ?)",
                        (window,),
                    ).fetchone()
                    xhs_fresh = int(row[0]) if row else 0
                except Exception:  # pragma: no cover - defensive
                    xhs_fresh = 0
        if xhs_fresh > 0:
            xiaohongshu = SourceStatusItem(
                enabled=xhs_enabled,
                state="ready",
                detail=(
                    f"访问令牌已同步（最近 {_xhs_token_fresh_hours} 小时内 {xhs_fresh} 条，"
                    f"共 {xhs_tokens} 条带 xsec_token 的缓存内容）。"
                ),
                logged_in=True,
            )
        elif xhs_tokens > 0:
            xiaohongshu = SourceStatusItem(
                enabled=xhs_enabled,
                state="stale",
                detail=(
                    f"令牌可能已失效 —— 超过 {_xhs_token_fresh_hours} 小时未同步新令牌"
                    f"（存量 {xhs_tokens} 条）。在浏览器逛逛小红书即可自动刷新。"
                ),
            )
        else:
            xiaohongshu = SourceStatusItem(
                enabled=xhs_enabled,
                state="missing",
                detail="未检测到访问令牌 —— 在浏览器登录小红书后插件会自动同步。",
            )

        # ── 抖音: cookie resolvable from env / data/douyin_cookie.json ──
        dy_enabled = bool(getattr(srcs.douyin, "enabled", False))
        dy_cookie = ""
        try:
            from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

            dy_cookie = resolve_douyin_cookie(
                data_dir=cfg.data_path,
                cookie_env=getattr(srcs.douyin, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"),
            )
        except Exception:  # pragma: no cover - defensive
            dy_cookie = ""
        if dy_cookie.strip():
            douyin = SourceStatusItem(
                enabled=dy_enabled, state="ready", detail="Cookie 就绪。", logged_in=True
            )
        else:
            douyin = SourceStatusItem(
                enabled=dy_enabled,
                state="missing",
                detail="未配置 Cookie —— 设置环境变量，或登录抖音后由插件同步。",
            )

        # ── YouTube: public, no login required ──
        youtube = SourceStatusItem(
            enabled=bool(getattr(srcs.youtube, "enabled", False)),
            state="no_auth",
            detail="公开源 · 无需登录。",
            logged_in=True,
        )

        # ── X (Twitter): reuse the live health store ──
        tw_enabled = bool(getattr(srcs.twitter, "enabled", False))
        tw_state, tw_feed_paused = "missing_cookie", False
        if hasattr(ctx.database, "conn"):
            from openbiliclaw.storage.x_health import XSourceHealthStore

            h = XSourceHealthStore(ctx.database).get()
            tw_state = str(h.get("state", "ok"))
            tw_feed_paused = bool(h.get("feed_paused", False))
        # The health row defaults to ``ok`` before any fetch has run, so an
        # ``ok`` without an actual cookie would falsely report a logged-in
        # source — gate it on the resolved credential, like the douyin branch.
        if tw_state == "ok":
            tw_cookie = ""
            with suppress(Exception):
                tw_cookie = resolve_x_cookie(
                    data_dir=cfg.data_path,
                    cookie_env=getattr(srcs.twitter, "cookie_env", "OPENBILICLAW_X_COOKIE"),
                )
            if not tw_cookie.strip():
                tw_state = "missing_cookie"
        tw_detail = _x_state_detail.get(tw_state, f"X 来源状态：{tw_state}。")
        if tw_feed_paused:
            tw_detail += " For-You 因连续失败已自动暂停。"
        twitter = SourceStatusItem(
            enabled=tw_enabled,
            state=tw_state,
            detail=tw_detail,
            logged_in=tw_state == "ok",
            feed_paused=tw_feed_paused,
        )

        zh_cfg = getattr(srcs, "zhihu", None)
        zh_enabled = bool(getattr(zh_cfg, "enabled", False))
        zhihu = SourceStatusItem(
            enabled=zh_enabled,
            state="unverified",
            detail=(
                "浏览器插件登录态源 · 尚未看到知乎任务结果，"
                "保存后可运行 init 或 discover 验证登录态。"
            ),
            logged_in=False,
        )
        if hasattr(ctx.database, "conn"):
            try:
                row = ctx.database.conn.execute(
                    """
                    SELECT type, status, result_json, created_at, completed_at
                    FROM zhihu_tasks
                    WHERE status IN ('pending', 'in_progress', 'completed', 'failed')
                    ORDER BY COALESCE(completed_at, created_at) DESC, created_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            except Exception:
                row = None
            if row is not None:
                task_type = str(row["type"] if hasattr(row, "keys") else row[0])
                status = str(row["status"] if hasattr(row, "keys") else row[1])
                result_json = row["result_json"] if hasattr(row, "keys") else row[2]
                payload: dict[str, Any] = {}
                with suppress(Exception):
                    parsed = json.loads(str(result_json or "{}"))
                    if isinstance(parsed, dict):
                        payload = parsed
                items = payload.get("items")
                item_count = len(items) if isinstance(items, list) else 0
                error_code = str(payload.get("error", "") or "").strip()
                debug = payload.get("debug")
                login_required = error_code == "zhihu_login_required" or (
                    isinstance(debug, dict) and bool(debug.get("login_required"))
                )
                if status == "completed":
                    zhihu = SourceStatusItem(
                        enabled=zh_enabled,
                        state="ready",
                        detail=f"最近任务完成（{task_type}，{item_count} 条）。",
                        logged_in=True,
                    )
                elif login_required:
                    zhihu = SourceStatusItem(
                        enabled=zh_enabled,
                        state="missing",
                        detail="最近知乎任务提示需要登录知乎。请在当前浏览器登录知乎后重试。",
                        logged_in=False,
                    )
                elif status == "failed":
                    suffix = f"：{error_code}" if error_code else ""
                    zhihu = SourceStatusItem(
                        enabled=zh_enabled,
                        state="partial",
                        detail=f"最近知乎任务失败{suffix}。可在浏览器登录态正常后重试。",
                        logged_in=False,
                    )
                elif status in {"pending", "in_progress"}:
                    zhihu = SourceStatusItem(
                        enabled=zh_enabled,
                        state="unverified",
                        detail=f"知乎任务正在等待插件执行（{task_type} / {status}）。",
                        logged_in=False,
                    )

        return SourcesStatusResponse(
            bilibili=bilibili,
            xiaohongshu=xiaohongshu,
            douyin=douyin,
            youtube=youtube,
            twitter=twitter,
            zhihu=zhihu,
        )

    def _mask_source_credential(value: str, *, reveal: bool) -> str:
        if reveal or not value:
            return value
        if len(value) <= 8:
            return "*" * len(value)
        return f"{value[:4]}{'*' * max(4, len(value) - 8)}{value[-4:]}"

    def _xhs_token_from_url(url: str) -> str:
        match = re.search(r"(?:[?&])xsec_token=([^&#]+)", str(url or ""))
        return match.group(1) if match else ""

    def _latest_xhs_token() -> str:
        if not hasattr(ctx.database, "conn"):
            return ""
        queries = (
            """
            SELECT content_url
            FROM discovery_candidates
            WHERE source_platform = 'xiaohongshu'
              AND content_url LIKE '%xsec_token=%'
            ORDER BY last_seen_at DESC, id DESC
            LIMIT 1
            """,
            """
            SELECT content_url
            FROM content_cache
            WHERE source_platform = 'xiaohongshu'
              AND content_url LIKE '%xsec_token=%'
            ORDER BY discovered_at DESC, bvid DESC
            LIMIT 1
            """,
        )
        for sql in queries:
            with suppress(Exception):
                row = ctx.database.conn.execute(sql).fetchone()
                if row:
                    url = row["content_url"] if hasattr(row, "keys") else row[0]
                    token = _xhs_token_from_url(str(url))
                    if token:
                        return token
        return ""

    @app.get("/api/sources/credentials", response_model=SourcesCredentialsResponse)
    def sources_credentials(reveal_keys: bool = False) -> SourcesCredentialsResponse:
        """Return current local Cookie / token snapshots for source settings pages."""
        from openbiliclaw.bilibili.auth import resolve_runtime_cookie
        from openbiliclaw.config import load_config
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

        cfg = load_config()
        srcs = cfg.sources

        bili_cookie = resolve_runtime_cookie(
            data_dir=cfg.data_path,
            configured_cookie=str(getattr(cfg.bilibili, "cookie", "") or ""),
        )
        dy_cookie = resolve_douyin_cookie(
            data_dir=cfg.data_path,
            cookie_env=getattr(srcs.douyin, "cookie_env", "OPENBILICLAW_DOUYIN_COOKIE"),
        )
        tw_cookie = resolve_x_cookie(
            data_dir=cfg.data_path,
            cookie_env=getattr(srcs.twitter, "cookie_env", "OPENBILICLAW_X_COOKIE"),
        )
        xhs_token = _latest_xhs_token()

        def item(label: str, value: str, detail: str) -> SourceCredentialItem:
            return SourceCredentialItem(
                label=label,
                value=_mask_source_credential(value, reveal=reveal_keys),
                available=bool(value.strip()),
                detail=detail,
            )

        return SourcesCredentialsResponse(
            bilibili=item("Cookie", bili_cookie, "B 站当前 resolved Cookie。"),
            xiaohongshu=item(
                "xsec_token",
                xhs_token,
                "小红书不保存整站 Cookie；这里展示最近同步内容 URL 中的 xsec_token。",
            ),
            douyin=item("Cookie", dy_cookie, "抖音当前 resolved Cookie。"),
            youtube=SourceCredentialItem(
                label="Cookie",
                available=False,
                detail="YouTube 当前按公开源接入，后端不保存 Cookie。",
            ),
            twitter=item("Cookie", tw_cookie, "X 当前 resolved Cookie。"),
            zhihu=SourceCredentialItem(
                label="Cookie",
                available=False,
                detail="知乎登录态保存在浏览器站点 / 插件上下文中，后端不保存可展示 Cookie。",
            ),
        )

    # ── Douyin task queue endpoints (extension dispatcher) ──────────
    # Independent from the XHS block above by design — see
    # docs/plans/2026-05-06-douyin-bootstrap-import-design.md
    # §"Module Isolation from XHS". Different table (dy_tasks),
    # different queue class, different fail isolation.

    from openbiliclaw.sources.dy_tasks import (
        DyTaskQueue,
        dy_bootstrap_video_key,
        dy_bootstrap_videos_to_events,
    )

    _dy_task_queue: DyTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _dy_task_queue = DyTaskQueue(ctx.database)

    @app.get("/api/sources/dy/next-task")
    def dy_next_task(response: Any = None) -> Any:
        """Return the oldest pending dy task, or 204 if none."""
        from starlette.responses import Response

        if _dy_task_queue is None:
            return Response(status_code=204)
        task = _dy_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/dy/task-result")
    async def dy_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a Douyin task result from the extension dispatcher.

        Status semantics mirror XHS (``ok`` = final, ``partial`` = keep
        pending, ``failed`` = mark failed) but the result schema uses
        ``videos`` instead of ``notes`` and propagation goes through
        ``dy_bootstrap_videos_to_events``. No self-author filtering yet
        (Douyin has its own posts in ``dy_post`` scope which we treat as
        a weak ``view`` signal — they're meant to count as input).
        """
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        videos = [v for v in payload.get("videos", []) if isinstance(v, dict)]
        # TEMP DEBUG: surface incoming partial debug field for the dy
        # bootstrap e2e probe (will be reverted before release).
        logger.info(
            "[dy-debug] task_result IN: status=%s videos=%d debug=%s",
            status,
            len(videos),
            payload.get("debug"),
        )
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _dy_task_queue is None:
            return {"ok": True}

        task = _dy_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_videos = _dy_task_queue.merge_result(
                task_id,
                videos=videos if videos else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            # gui-init D1: persist the result (above) for init's own collector;
            # during init skip profile propagation for non-owned results, but
            # propagate init-OWNED bootstrap results through the deduped path.
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if task_type == "bootstrap_profile" and added_videos and not _skip_profile:
                fresh_videos, video_keys_by_index = _filter_new_source_bootstrap_items(
                    "dy",
                    added_videos,
                    dy_bootstrap_video_key,
                )
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, video in enumerate(fresh_videos):
                    for event in dy_bootstrap_videos_to_events([video]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = video_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                # Skip the incremental pipeline during init (see xhs handler).
                if not _init_busy:
                    await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("dy", propagated_keys)
        else:
            _dy_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

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
    @app.post("/api/sources/_debug/log")
    async def ext_debug_log(payload: dict[str, Any]) -> dict[str, Any]:
        source = str(payload.get("source", "?"))[:8]
        event = str(payload.get("event", "?"))[:80]
        data = payload.get("data")
        logger.warning("[ext-debug] [%s] %s data=%s", source, event, data)
        return {"ok": True}

    @app.post("/api/sources/xhs/kick")
    async def xhs_task_kick() -> dict[str, Any]:
        """Broadcast `xhs_task_available` so any subscribed extension
        service-worker triggers an immediate poll. Idempotent and best
        effort — failures here never affect task state.
        """
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "xhs_task_available", "source": "task_kick"})
        return {"ok": True}

    @app.post("/api/sources/dy/kick")
    async def dy_task_kick() -> dict[str, Any]:
        """Broadcast `dy_task_available` over runtime-stream. See
        xhs_task_kick docstring for rationale.
        """
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "dy_task_available", "source": "task_kick"})
        return {"ok": True}

    # ── YouTube bootstrap endpoints ────────────────────────────────
    from openbiliclaw.sources.yt_tasks import (
        YtTaskQueue,
        yt_bootstrap_item_key,
        yt_bootstrap_items_to_events,
    )
    from openbiliclaw.sources.zhihu_tasks import (
        ZhihuTaskQueue,
        zhihu_bootstrap_item_key,
        zhihu_bootstrap_items_to_events,
    )

    _zhihu_task_queue: ZhihuTaskQueue | None = None
    db_conn = getattr(ctx.database, "conn", None)
    if hasattr(db_conn, "executescript"):
        _zhihu_task_queue = ZhihuTaskQueue(ctx.database)

    @app.get("/api/sources/zhihu/next-task")
    def zhihu_next_task(response: Any = None) -> Any:
        """Return the oldest pending Zhihu task, or 204 if none."""
        from starlette.responses import Response

        if _zhihu_task_queue is None:
            return Response(status_code=204)
        task = _zhihu_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/zhihu/task-result")
    async def zhihu_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a Zhihu task result from the extension dispatcher.

        Plain ``fetch-zhihu`` smoke tasks only record the task payload. Tasks
        explicitly marked ``profile_update`` also propagate bootstrap events to
        memory and, once a profile exists, into the incremental profile-update
        pipeline.
        """
        task_id = str(payload.get("task_id", "") or "").strip()
        status = str(payload.get("status", "") or "").strip()
        items = [v for v in payload.get("items", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _zhihu_task_queue is None:
            return {"ok": True}

        task = _zhihu_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""
        task_payload: dict[str, Any] = {}
        if task and task.get("payload_json"):
            with suppress(Exception):
                parsed_payload = json.loads(str(task.get("payload_json") or "{}"))
                if isinstance(parsed_payload, dict):
                    task_payload = parsed_payload
        profile_update = bool(task_payload.get("profile_update"))

        if status in {"partial", "ok"} or status == "empty":
            is_final = status in {"ok", "empty"}
            added_items = _zhihu_task_queue.merge_result(
                task_id,
                items=items if items else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if (
                task_type == "bootstrap_events"
                and profile_update
                and added_items
                and not _skip_profile
            ):
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "zhihu",
                    added_items,
                    zhihu_bootstrap_item_key,
                )
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, item in enumerate(fresh_items):
                    for event in zhihu_bootstrap_items_to_events([item]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = item_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                if not _init_busy:
                    await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("zhihu", propagated_keys)
        else:
            _zhihu_task_queue.fail(task_id, error=str(payload.get("error", "") or ""), debug=debug)

        return {"ok": True}

    @app.post("/api/sources/zhihu/kick")
    async def zhihu_task_kick() -> dict[str, Any]:
        """Broadcast `zhihu_task_available` over runtime-stream."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "zhihu_task_available", "source": "task_kick"})
        return {"ok": True}

    _yt_task_queue: YtTaskQueue | None = None
    if hasattr(ctx.database, "conn"):
        _yt_task_queue = YtTaskQueue(ctx.database)

    @app.get("/api/sources/yt/next-task")
    def yt_next_task(response: Any = None) -> Any:
        """Return the oldest pending YouTube task, or 204 if none."""
        from starlette.responses import Response

        if _yt_task_queue is None:
            return Response(status_code=204)
        task = _yt_task_queue.next_pending(only_ids=_init_owned_ids_filter())
        if task is None:
            return Response(status_code=204)

        import json as _json

        payload = _json.loads(task["payload_json"]) if task.get("payload_json") else {}
        return {
            "id": task["id"],
            "type": task["type"],
            **payload,
        }

    @app.post("/api/sources/yt/task-result")
    async def yt_task_result(payload: dict[str, Any]) -> dict[str, Any]:
        """Accept a YouTube task result from the extension dispatcher."""
        task_id = payload.get("task_id", "")
        status = payload.get("status", "")
        items = [v for v in payload.get("items", []) if isinstance(v, dict)]
        scope_counts = payload.get("scope_counts")
        if not isinstance(scope_counts, dict):
            scope_counts = None
        debug = payload.get("debug")
        if not isinstance(debug, dict):
            debug = None

        if not task_id:
            from fastapi import HTTPException

            raise HTTPException(status_code=422, detail="task_id is required")

        if _yt_task_queue is None:
            return {"ok": True}

        task = _yt_task_queue.get(task_id)
        task_type = str(task.get("type", "")).strip() if task else ""

        if status in {"partial", "ok"} or (status == "empty" and task_type == "bootstrap_profile"):
            is_final = status == "ok" or (status == "empty" and task_type == "bootstrap_profile")
            added_items = _yt_task_queue.merge_result(
                task_id,
                items=items if items else None,
                scope_counts=scope_counts,
                debug=debug,
                complete=is_final,
            )
            # gui-init D1: persist the result (above) for init's own collector;
            # during init skip profile propagation for non-owned results, but
            # propagate init-OWNED bootstrap results through the deduped path.
            _init_busy = _init_active_now()
            _skip_profile = _init_busy and not _init_owns_task(task_id)
            if task_type == "bootstrap_profile" and added_items and not _skip_profile:
                fresh_items, item_keys_by_index = _filter_new_source_bootstrap_items(
                    "yt",
                    added_items,
                    yt_bootstrap_item_key,
                )
                profile_events: list[dict[str, Any]] = []
                propagated_keys: list[str] = []
                for index, item in enumerate(fresh_items):
                    for event in yt_bootstrap_items_to_events([item]):
                        await ctx.memory_manager.propagate_event(event)
                        profile_events.append(event)
                        key = item_keys_by_index.get(index, "")
                        if key:
                            propagated_keys.append(key)
                # Skip the incremental pipeline during init (see xhs handler).
                if not _init_busy:
                    await _ingest_profile_update_events(profile_events)
                _mark_source_bootstrap_keys("yt", propagated_keys)
        else:
            _yt_task_queue.fail(task_id, error=payload.get("error", ""), debug=debug)

        return {"ok": True}

    @app.post("/api/sources/yt/kick")
    async def yt_task_kick() -> dict[str, Any]:
        """Broadcast `yt_task_available` over runtime-stream."""
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "yt_task_available", "source": "task_kick"})
        return {"ok": True}

    @app.post("/api/extension/e2e/run", response_model=ExtensionE2ERunOut)
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

    @app.post("/api/extension/e2e/result")
    async def extension_e2e_result(
        request: Request,
        payload: ExtensionE2EResultIn,
    ) -> dict[str, object]:
        """Accept a signed callback from the extension E2E runner."""
        if not _get_auth_gate().is_trusted_local(request):
            raise HTTPException(status_code=403, detail="local_only")

        registry = cast("dict[str, _ExtensionE2ERunState]", app.state.extension_e2e_runs)
        state = registry.get(payload.run_id)
        if state is None:
            raise HTTPException(status_code=404, detail="unknown run_id")
        if not secrets.compare_digest(state.token, payload.token):
            raise HTTPException(status_code=403, detail="bad token")

        state.extension_result = payload
        state.event.set()
        return {"ok": True, "run_id": payload.run_id}

    @app.post("/api/extension/reload")
    async def extension_reload() -> dict[str, Any]:
        """Dev-only: broadcast `extension_reload` so the connected
        service-worker calls chrome.runtime.reload() — picks up the
        latest /dist bundle without the user clicking the reload icon
        in chrome://extensions.

        Best-effort — silent when no event-hub is wired.
        """
        publish = getattr(getattr(ctx, "event_hub", None), "publish", None)
        if callable(publish):
            with suppress(Exception):
                await publish({"type": "extension_reload", "source": "dev"})
        return {"ok": True}

    def _autostart_status_out(
        request: Request,
        cfg: Any,
        *,
        reason_override: str | None = None,
        detail_override: str | None = None,
    ) -> AutostartStatusOut:
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.guards import (
            active_env_managed_inputs,
            autostart_shadowed,
        )
        from openbiliclaw.runtime.ollama_supervisor import (
            effective_ollama_endpoint,
            is_loopback,
            ollama_required,
        )

        state = autostart.status()
        managed_env = active_env_managed_inputs(cfg)
        shadowed = autostart_shadowed(cfg.autostart.enabled)
        trusted_local = _get_auth_gate().is_trusted_local(request)
        requires_ollama = ollama_required(cfg)

        reason = "none"
        if not state.supported:
            reason = state.reason
        elif not trusted_local:
            reason = "local_only"
        elif managed_env:
            reason = "env_managed"
        elif shadowed:
            reason = "shadowed"
        if reason_override is not None:
            reason = reason_override

        detail = ""
        if not state.supported:
            detail = "当前运行环境不支持注册开机自启动。"
        elif not trusted_local:
            detail = "仅本机可信请求可以修改开机自启动。"
        elif managed_env:
            detail = "检测到环境变量配置，自启动登录会话可能缺失：" + ", ".join(managed_env)
        elif shadowed:
            detail = "config.local.toml 覆盖了 [autostart].enabled，config.toml 修改不会生效。"
        elif cfg.autostart.enabled and not state.registered:
            detail = "开机自启动配置已开启，但系统自启动项缺失。"
        elif cfg.autostart.enabled:
            detail = "开机自启动已开启。"
        else:
            detail = "尚未开启开机自启动。"
        if detail_override is not None:
            detail = detail_override

        if requires_ollama:
            endpoint = effective_ollama_endpoint(cfg)
            if not is_loopback(endpoint):
                detail = (detail + " " if detail else "") + "Ollama 端点是远端地址，需自行管理。"

        return AutostartStatusOut(
            supported=state.supported,
            enabled=cfg.autostart.enabled,
            registered=state.registered,
            can_manage=trusted_local and state.supported and not managed_env and not shadowed,
            platform=state.platform,
            mechanism=state.mechanism,
            manage_ollama=cfg.autostart.manage_ollama,
            ollama_required=requires_ollama,
            reason=reason,
            detail=detail,
        )

    @app.get("/api/autostart-status", response_model=AutostartStatusOut)
    def autostart_status(request: Request) -> AutostartStatusOut:
        from openbiliclaw.config import load_config

        cfg = load_config()
        return _autostart_status_out(request, cfg)

    @app.post("/api/autostart/apply", response_model=AutostartStatusOut)
    async def autostart_apply(
        payload: AutostartApplyIn, request: Request
    ) -> AutostartStatusOut | JSONResponse:
        from openbiliclaw.config import _default_config_path as _cfg_path
        from openbiliclaw.config import load_config as _load
        from openbiliclaw.config import save_config as _save
        from openbiliclaw.runtime import autostart
        from openbiliclaw.runtime.autostart.guards import active_env_managed_inputs

        cfg = _load()
        if not _get_auth_gate().is_trusted_local(request):
            body = _autostart_status_out(
                request,
                cfg,
                reason_override="local_only",
                detail_override="仅本机可信请求可以修改开机自启动。",
            )
            return JSONResponse(status_code=403, content=body.model_dump(mode="json"))

        current = autostart.status()
        if not current.supported:
            body = _autostart_status_out(
                request,
                cfg,
                reason_override=current.reason,
                detail_override="当前运行环境不支持注册开机自启动。",
            )
            return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

        managed = active_env_managed_inputs(cfg)
        if payload.enabled and managed:
            body = _autostart_status_out(
                request,
                cfg,
                reason_override="env_managed",
                detail_override="检测到环境变量配置，自启动登录会话可能缺失：" + ", ".join(managed),
            )
            return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

        async with _CONFIG_SAVE_LOCK:
            config_path = _cfg_path()
            config_existed = config_path.exists()
            backup_path = _snapshot_config_file(config_path)

            def _rollback_cfg() -> None:
                if backup_path is not None:
                    with suppress(Exception):
                        _restore_config_snapshot(backup_path, config_path)
                elif not config_existed:
                    with suppress(Exception):
                        config_path.unlink(missing_ok=True)

            cfg = _load()
            was_registered = autostart.status().registered

            if payload.enabled:
                cfg.autostart.enabled = True
                try:
                    _save(cfg, autostart_authoritative=True)
                except Exception:
                    _rollback_cfg()
                    logger.warning("autostart: enable save_config failed", exc_info=True)
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="unavailable",
                        detail_override="保存配置失败，开机自启动未修改。",
                    )
                    return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

                effective = _load()
                if effective.autostart.enabled is not True:
                    _rollback_cfg()
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="shadowed",
                        detail_override=(
                            "config.local.toml 覆盖了 [autostart].enabled，"
                            "config.toml 修改不会生效。"
                        ),
                    )
                    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

                try:
                    autostart.register(effective)
                except Exception:
                    _rollback_cfg()
                    logger.warning("autostart: OS registration failed", exc_info=True)
                    body = _autostart_status_out(
                        request,
                        _load(),
                        reason_override="registration_failed",
                        detail_override="系统自启动项注册失败，配置已回滚。",
                    )
                    return JSONResponse(status_code=409, content=body.model_dump(mode="json"))
                return _autostart_status_out(request, _load())

            try:
                autostart.unregister()
            except Exception:
                logger.warning("autostart: OS unregister failed", exc_info=True)
                body = _autostart_status_out(
                    request,
                    cfg,
                    reason_override="unregister_failed",
                    detail_override="系统自启动项移除失败，配置未修改。",
                )
                return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

            cfg.autostart.enabled = False
            try:
                _save(cfg, autostart_authoritative=True)
            except Exception:
                if was_registered:
                    with suppress(Exception):
                        cfg.autostart.enabled = True
                        autostart.register(cfg)
                _rollback_cfg()
                logger.warning("autostart: disable save_config failed", exc_info=True)
                body = _autostart_status_out(
                    request,
                    _load(),
                    reason_override="unavailable",
                    detail_override="保存配置失败，系统自启动项已尝试恢复。",
                )
                return JSONResponse(status_code=503, content=body.model_dump(mode="json"))

            effective = _load()
            if effective.autostart.enabled is not False:
                if was_registered:
                    with suppress(Exception):
                        cfg.autostart.enabled = True
                        autostart.register(cfg)
                _rollback_cfg()
                body = _autostart_status_out(
                    request,
                    _load(),
                    reason_override="shadowed",
                    detail_override=(
                        "config.local.toml 覆盖了 [autostart].enabled，config.toml 修改不会生效。"
                    ),
                )
                return JSONResponse(status_code=409, content=body.model_dump(mode="json"))

            return _autostart_status_out(request, _load())

    # ── Configuration management endpoints ──────────────────────────

    def _config_to_response(
        cfg: Any,
        issues: list[Any] | None = None,
        *,
        mask_keys: bool = True,
        degraded: bool = False,
        degraded_reason: str = "",
    ) -> ConfigResponse:
        """Convert a Config dataclass to a ConfigResponse, optionally masking API keys."""

        def _mask(key: str) -> str:
            if not mask_keys or not key:
                return key
            if len(key) <= 8:
                return "*" * len(key)
            return key[:4] + "*" * (len(key) - 8) + key[-4:]

        # Douyin / X store their cookie in data/*.json (env override wins),
        # not in config.toml — resolve here so the settings pages can show
        # the live credential exactly like the Bilibili card does.
        from openbiliclaw.sources.douyin_auth import resolve_douyin_cookie

        dy_cookie = ""
        with suppress(Exception):
            dy_cookie = resolve_douyin_cookie(
                data_dir=cfg.data_path,
                cookie_env=cfg.sources.douyin.cookie_env,
            )
        tw_cookie = ""
        with suppress(Exception):
            tw_cookie = resolve_x_cookie(
                data_dir=cfg.data_path,
                cookie_env=cfg.sources.twitter.cookie_env,
            )

        def _provider_out(p: Any) -> LLMProviderConfigOut:
            return LLMProviderConfigOut(
                api_key=_mask(p.api_key),
                model=p.model,
                base_url=p.base_url,
                auth_mode=getattr(p, "auth_mode", ""),
                http_referer=getattr(p, "http_referer", ""),
                x_title=getattr(p, "x_title", ""),
                reasoning_effort=getattr(p, "reasoning_effort", ""),
            )

        issue_list = [
            ConfigIssueOut(
                field=i.field,
                message=i.message,
                severity=getattr(i, "severity", "warning"),
            )
            for i in (issues or [])
        ]

        return ConfigResponse(
            language=cfg.language,
            data_dir=cfg.data_dir,
            degraded=degraded,
            degraded_reason=degraded_reason,
            llm=LLMConfigOut(
                default_provider=cfg.llm.default_provider,
                concurrency=int(getattr(cfg.llm, "concurrency", 3)),
                timeout=int(getattr(cfg.llm, "timeout", 300)),
                fallback_enabled=cfg.llm.fallback_enabled,
                fallback_provider=cfg.llm.fallback_provider,
                openai=_provider_out(cfg.llm.openai),
                claude=_provider_out(cfg.llm.claude),
                gemini=_provider_out(cfg.llm.gemini),
                deepseek=_provider_out(cfg.llm.deepseek),
                ollama=_provider_out(cfg.llm.ollama),
                openrouter=_provider_out(cfg.llm.openrouter),
                openai_compatible=_provider_out(cfg.llm.openai_compatible),
                embedding=EmbeddingConfigOut(
                    provider=cfg.llm.embedding.provider,
                    model=cfg.llm.embedding.model,
                    api_key=_mask(cfg.llm.embedding.api_key),
                    base_url=cfg.llm.embedding.base_url,
                    output_dimensionality=cfg.llm.embedding.output_dimensionality,
                    similarity_threshold=cfg.llm.embedding.similarity_threshold,
                    fallback_enabled=cfg.llm.embedding.fallback_enabled,
                    fallback_provider=cfg.llm.embedding.fallback_provider,
                ),
                soul=ModuleLLMConfigOut(
                    provider=cfg.llm.soul.provider,
                    model=cfg.llm.soul.model,
                ),
                discovery=ModuleLLMConfigOut(
                    provider=cfg.llm.discovery.provider,
                    model=cfg.llm.discovery.model,
                ),
                recommendation=ModuleLLMConfigOut(
                    provider=cfg.llm.recommendation.provider,
                    model=cfg.llm.recommendation.model,
                ),
                evaluation=ModuleLLMConfigOut(
                    provider=cfg.llm.evaluation.provider,
                    model=cfg.llm.evaluation.model,
                ),
            ),
            bilibili=BilibiliConfigOut(
                auth_method=cfg.bilibili.auth_method,
                cookie=_mask(cfg.bilibili.cookie),
                browser_executable=cfg.bilibili.browser_executable,
                browser_headed=cfg.bilibili.browser_headed,
            ),
            sources=SourcesConfigOut(
                browser=SourcesBrowserConfigOut(
                    cdp_url=cfg.sources.browser_cdp_url,
                    headed=cfg.sources.browser_headed,
                ),
                bilibili=BilibiliSourceConfigOut(
                    enabled=cfg.sources.bilibili.enabled,
                ),
                xiaohongshu=XiaohongshuSourceConfigOut(
                    enabled=cfg.sources.xiaohongshu.enabled,
                    daily_search_budget=cfg.sources.xiaohongshu.daily_search_budget,
                    daily_creator_budget=cfg.sources.xiaohongshu.daily_creator_budget,
                    task_interval_seconds=cfg.sources.xiaohongshu.task_interval_seconds,
                ),
                douyin=DouyinSourceConfigOut(
                    enabled=cfg.sources.douyin.enabled,
                    mode=cfg.sources.douyin.mode,
                    cookie=_mask(dy_cookie),
                    cookie_env=cfg.sources.douyin.cookie_env,
                    daily_search_budget=cfg.sources.douyin.daily_search_budget,
                    daily_hot_budget=cfg.sources.douyin.daily_hot_budget,
                    daily_feed_budget=cfg.sources.douyin.daily_feed_budget,
                    request_interval_seconds=cfg.sources.douyin.request_interval_seconds,
                ),
                youtube=YoutubeSourceConfigOut(
                    enabled=cfg.sources.youtube.enabled,
                    daily_search_budget=cfg.sources.youtube.daily_search_budget,
                    daily_trending_budget=cfg.sources.youtube.daily_trending_budget,
                    daily_channel_budget=cfg.sources.youtube.daily_channel_budget,
                    request_interval_seconds=cfg.sources.youtube.request_interval_seconds,
                    min_interval_minutes=cfg.sources.youtube.min_interval_minutes,
                ),
                twitter=TwitterSourceConfigOut(
                    enabled=cfg.sources.twitter.enabled,
                    mode=cfg.sources.twitter.mode,
                    cookie=_mask(tw_cookie),
                    cookie_env=cfg.sources.twitter.cookie_env,
                    daily_search_budget=cfg.sources.twitter.daily_search_budget,
                    daily_feed_budget=cfg.sources.twitter.daily_feed_budget,
                    daily_creator_budget=cfg.sources.twitter.daily_creator_budget,
                    request_interval_seconds=cfg.sources.twitter.request_interval_seconds,
                    min_interval_minutes=cfg.sources.twitter.min_interval_minutes,
                ),
                zhihu=ZhihuSourceConfigOut(
                    enabled=cfg.sources.zhihu.enabled,
                    source_modes=list(cfg.sources.zhihu.source_modes),
                    daily_search_budget=cfg.sources.zhihu.daily_search_budget,
                    daily_hot_budget=cfg.sources.zhihu.daily_hot_budget,
                    daily_feed_budget=cfg.sources.zhihu.daily_feed_budget,
                    daily_creator_budget=cfg.sources.zhihu.daily_creator_budget,
                    daily_related_budget=cfg.sources.zhihu.daily_related_budget,
                    request_interval_seconds=cfg.sources.zhihu.request_interval_seconds,
                    min_interval_minutes=cfg.sources.zhihu.min_interval_minutes,
                ),
            ),
            scheduler=SchedulerConfigOut(
                enabled=cfg.scheduler.enabled,
                pause_on_extension_disconnect=cfg.scheduler.pause_on_extension_disconnect,
                extension_disconnect_grace_seconds=cfg.scheduler.extension_disconnect_grace_seconds,
                discovery_cron=cfg.scheduler.discovery_cron,
                pool_target_count=cfg.scheduler.pool_target_count,
                pool_source_shares=dict(cfg.scheduler.pool_source_shares),
                account_sync_interval_hours=cfg.scheduler.account_sync_interval_hours,
                refresh_check_interval_seconds=cfg.scheduler.refresh_check_interval_seconds,
                signal_event_threshold=cfg.scheduler.signal_event_threshold,
                feedback_batch_threshold=cfg.scheduler.feedback_batch_threshold,
                trending_refresh_hours=cfg.scheduler.trending_refresh_hours,
                explore_refresh_hours=cfg.scheduler.explore_refresh_hours,
                discovery_limit=cfg.scheduler.discovery_limit,
                delight_queue_limit=cfg.scheduler.delight_queue_limit,
                proactive_push_interval_seconds=cfg.scheduler.proactive_push_interval_seconds,
                speculator_idle_interval_minutes=cfg.scheduler.speculator_idle_interval_minutes,
                speculation_interval_minutes=cfg.scheduler.speculation_interval_minutes,
                speculation_ttl_days=cfg.scheduler.speculation_ttl_days,
                speculation_cooldown_days=cfg.scheduler.speculation_cooldown_days,
                speculation_confirmation_threshold=(
                    cfg.scheduler.speculation_confirmation_threshold
                ),
                speculation_max_active=cfg.scheduler.speculation_max_active,
                speculation_max_primary_interests=(cfg.scheduler.speculation_max_primary_interests),
                speculation_max_secondary_interests=(
                    cfg.scheduler.speculation_max_secondary_interests
                ),
                avoidance_speculation_interval_minutes=(
                    cfg.scheduler.avoidance_speculation_interval_minutes
                ),
                avoidance_speculation_ttl_days=cfg.scheduler.avoidance_speculation_ttl_days,
                avoidance_speculation_cooldown_days=(
                    cfg.scheduler.avoidance_speculation_cooldown_days
                ),
                avoidance_speculation_confirmation_threshold=(
                    cfg.scheduler.avoidance_speculation_confirmation_threshold
                ),
                avoidance_speculation_max_active=cfg.scheduler.avoidance_speculation_max_active,
                auto_update_enabled=cfg.scheduler.auto_update_enabled,
                auto_update_check_interval_hours=cfg.scheduler.auto_update_check_interval_hours,
                auto_update_allow_prerelease=cfg.scheduler.auto_update_allow_prerelease,
                auto_update_allowed_remotes=list(cfg.scheduler.auto_update_allowed_remotes),
                rss_subscriptions=list(cfg.scheduler.rss_subscriptions),
                xiaoyuzhou_subscriptions=list(cfg.scheduler.xiaoyuzhou_subscriptions),
                wechat_subscriptions=list(cfg.scheduler.wechat_subscriptions),
            ),
            discovery=DiscoveryConfigOut(
                unified_keyword_planner_enabled=cfg.discovery.unified_keyword_planner_enabled,
                kw_cache_high=cfg.discovery.kw_cache_high,
                kw_cache_low=cfg.discovery.kw_cache_low,
                gen_batch=cfg.discovery.gen_batch,
                fetch_batch=cfg.discovery.fetch_batch,
                history_window_size=cfg.discovery.history_window_size,
                history_window_hours=cfg.discovery.history_window_hours,
                claim_lease_minutes=cfg.discovery.claim_lease_minutes,
                planner_poll_seconds=cfg.discovery.planner_poll_seconds,
                plan_ttl_hours=cfg.discovery.plan_ttl_hours,
                admission_min_score=cfg.discovery.admission_min_score,
                multimodal_evaluation_enabled=cfg.discovery.multimodal_evaluation_enabled,
                multimodal_batch_size=cfg.discovery.multimodal_batch_size,
                multimodal_image_max_px=cfg.discovery.multimodal_image_max_px,
                multimodal_image_quality=cfg.discovery.multimodal_image_quality,
                multimodal_image_timeout_seconds=(cfg.discovery.multimodal_image_timeout_seconds),
            ),
            autostart=AutostartConfigOut(
                enabled=cfg.autostart.enabled,
                manage_ollama=cfg.autostart.manage_ollama,
            ),
            storage=StorageConfigOut(db_path=cfg.storage.db_path),
            logging=LoggingConfigOut(
                level=cfg.logging.level,
                file_level=cfg.logging.file_level,
                directory=cfg.logging.directory,
                filename=cfg.logging.filename,
                file_path=str(cfg.logging.file_path),
                max_file_size_mb=cfg.logging.max_file_size_mb,
                backup_count=cfg.logging.backup_count,
                aggregate_budget_mb=cfg.logging.aggregate_budget_mb,
                unmanaged_truncate_mb=cfg.logging.unmanaged_truncate_mb,
                unmanaged_max_age_days=cfg.logging.unmanaged_max_age_days,
            ),
            issues=issue_list,
        )

    @app.get("/api/config", response_model=ConfigResponse)
    def get_config(reveal_keys: bool = False) -> ConfigResponse:
        """Return the current configuration (API keys masked by default)."""
        from openbiliclaw.config import (
            _collect_config_issues,
            load_config,
        )

        cfg = load_config()
        issues = list(_collect_config_issues(cfg))
        if bool(getattr(ctx, "degraded", False)):
            issues.extend(getattr(ctx, "degraded_issues", []))
        return _config_to_response(
            cfg,
            issues,
            mask_keys=not reveal_keys,
            degraded=bool(getattr(ctx, "degraded", False)),
            degraded_reason=str(getattr(ctx, "degraded_reason", "")),
        )

    def _as_bool(value: object) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value if str(item).strip()]

    def _apply_llm_update(cfg: Any, llm_data: object) -> None:
        """Apply the LLM subset of a config update to an in-memory config."""
        if not isinstance(llm_data, dict):
            return
        from openbiliclaw.config import _normalize_llm_concurrency, _normalize_llm_timeout

        if "default_provider" in llm_data:
            cfg.llm.default_provider = str(llm_data["default_provider"])
        if "concurrency" in llm_data:
            cfg.llm.concurrency = _normalize_llm_concurrency(llm_data["concurrency"])
        if "timeout" in llm_data:
            cfg.llm.timeout = _normalize_llm_timeout(llm_data["timeout"])
        if "fallback_enabled" in llm_data:
            cfg.llm.fallback_enabled = _as_bool(llm_data["fallback_enabled"])
        if "fallback_provider" in llm_data:
            cfg.llm.fallback_provider = str(llm_data["fallback_provider"]).strip()
        for provider_name in (
            "openai",
            "claude",
            "gemini",
            "deepseek",
            "ollama",
            "openrouter",
            "openai_compatible",
        ):
            if provider_name in llm_data and isinstance(llm_data[provider_name], dict):
                provider_cfg = getattr(cfg.llm, provider_name)
                pdata = llm_data[provider_name]
                skipped_fields: list[str] = []
                for field_name in (
                    "api_key",
                    "model",
                    "base_url",
                    "auth_mode",
                    "http_referer",
                    "x_title",
                    "reasoning_effort",
                ):
                    if field_name in pdata:
                        new_value = str(pdata[field_name])
                        if field_name == "api_key" and "*" in new_value:
                            skipped_fields.append(f"{field_name}=masked")
                            continue
                        existing = getattr(provider_cfg, field_name, "")
                        if (
                            field_name not in {"auth_mode", "reasoning_effort"}
                            and not new_value.strip()
                            and isinstance(existing, str)
                            and existing.strip()
                        ):
                            skipped_fields.append(f"{field_name}=empty_skip")
                            continue
                        setattr(provider_cfg, field_name, new_value)
                if skipped_fields:
                    logger.debug(
                        "Config LLM update: provider %s skipped fields: %s",
                        provider_name,
                        ", ".join(skipped_fields),
                    )
        if "embedding" in llm_data and isinstance(llm_data["embedding"], dict):
            emb = llm_data["embedding"]
            if "provider" in emb:
                cfg.llm.embedding.provider = str(emb["provider"])
            if "model" in emb:
                new_model = str(emb["model"])
                if new_model.strip() or not cfg.llm.embedding.model.strip():
                    cfg.llm.embedding.model = new_model
            if "api_key" in emb:
                new_key = str(emb["api_key"])
                if "*" not in new_key and (
                    new_key.strip() or not cfg.llm.embedding.api_key.strip()
                ):
                    cfg.llm.embedding.api_key = new_key
            if "base_url" in emb:
                new_base_url = str(emb["base_url"])
                if new_base_url.strip() or not cfg.llm.embedding.base_url.strip():
                    cfg.llm.embedding.base_url = new_base_url
            if "output_dimensionality" in emb:
                try:
                    cfg.llm.embedding.output_dimensionality = max(
                        0,
                        int(emb["output_dimensionality"] or 0),
                    )
                except (TypeError, ValueError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="llm.embedding.output_dimensionality must be an integer",
                    ) from exc
            if "similarity_threshold" in emb:
                cfg.llm.embedding.similarity_threshold = float(emb["similarity_threshold"])
            if "fallback_enabled" in emb:
                cfg.llm.embedding.fallback_enabled = _as_bool(emb["fallback_enabled"])
            if "fallback_provider" in emb:
                cfg.llm.embedding.fallback_provider = str(emb["fallback_provider"]).strip()
        for module_name in ("soul", "discovery", "recommendation", "evaluation"):
            if module_name in llm_data and isinstance(llm_data[module_name], dict):
                mod_cfg = getattr(cfg.llm, module_name)
                mdata = llm_data[module_name]
                if "provider" in mdata:
                    mod_cfg.provider = str(mdata["provider"])
                if "model" in mdata:
                    mod_cfg.model = str(mdata["model"])

    async def _probe_llm_config(cfg: Any) -> ConfigServiceProbeResponse:
        from openbiliclaw.llm.base import LLM_CONNECTIVITY_PROBE_MAX_TOKENS
        from openbiliclaw.llm.registry import build_llm_registry

        started = time.perf_counter()
        provider = str(getattr(cfg.llm, "default_provider", "") or "").strip().lower()
        model = ""
        try:
            registry = build_llm_registry(cfg)
            provider = provider or str(getattr(registry, "default_provider", "") or "")
            provider_cfg = getattr(cfg.llm, provider, None)
            model = str(getattr(provider_cfg, "model", "") or "").strip()
            if not registry.is_chat_capable(provider):
                return ConfigServiceProbeResponse(
                    ok=False,
                    kind="llm",
                    provider=provider,
                    model=model,
                    error=f"LLM provider {provider!r} is not registered or not chat-capable.",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            timeout_s = min(max(float(getattr(cfg.llm, "timeout", 300) or 300), 10.0), 30.0)
            response = await asyncio.wait_for(
                registry.complete_provider(
                    provider,
                    [
                        {"role": "system", "content": "Reply with only OK."},
                        {"role": "user", "content": "OpenBiliClaw connectivity probe."},
                    ],
                    temperature=0,
                    max_tokens=LLM_CONNECTIVITY_PROBE_MAX_TOKENS,
                    reasoning_effort="",
                    model=model or None,
                ),
                timeout=timeout_s,
            )
            ok = bool(str(getattr(response, "content", "") or "").strip())
            response_model = str(getattr(response, "model", "") or model)
            return ConfigServiceProbeResponse(
                ok=ok,
                kind="llm",
                provider=provider,
                model=response_model,
                message="LLM provider is available." if ok else "",
                error="" if ok else "LLM provider returned an empty response.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="llm",
                provider=provider,
                model=model,
                error=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    async def _probe_embedding_config(cfg: Any) -> ConfigServiceProbeResponse:
        from openbiliclaw.llm.base import LLMRegistry
        from openbiliclaw.llm.registry import build_embedding_service

        started = time.perf_counter()
        emb_cfg = getattr(getattr(cfg, "llm", None), "embedding", None)
        provider = str(getattr(emb_cfg, "provider", "") or "").strip().lower()
        model = str(getattr(emb_cfg, "model", "") or "").strip()
        if not provider:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="embedding",
                provider="",
                model=model,
                error="Embedding provider is not configured.",
            )
        try:
            service = build_embedding_service(cfg, LLMRegistry())
            if service is None:
                return ConfigServiceProbeResponse(
                    ok=False,
                    kind="embedding",
                    provider=provider,
                    model=model,
                    error="Embedding service could not be built from the submitted config.",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                )
            probe = getattr(service, "probe", None)
            if not callable(probe):
                # Legacy/stub embedding service without a live probe —
                # building it successfully is the best signal we have.
                ok = True
            else:
                ok = bool(await asyncio.wait_for(probe(), timeout=15.0))
            return ConfigServiceProbeResponse(
                ok=ok,
                kind="embedding",
                provider=provider,
                model=model,
                message="Embedding provider is available." if ok else "",
                error="" if ok else "Embedding provider returned no vector.",
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except Exception as exc:
            return ConfigServiceProbeResponse(
                ok=False,
                kind="embedding",
                provider=provider,
                model=model,
                error=str(exc),
                latency_ms=int((time.perf_counter() - started) * 1000),
            )

    @app.post("/api/config/probe-service", response_model=ConfigServiceProbeResponse)
    async def probe_config_service(payload: ConfigServiceProbeIn) -> ConfigServiceProbeResponse:
        """Probe submitted LLM / embedding settings without saving config.toml."""
        from copy import deepcopy

        from openbiliclaw.config import load_config

        cfg = deepcopy(load_config())
        update = payload.config if isinstance(payload.config, dict) else {}
        llm_data = update.get("llm")
        if isinstance(llm_data, dict):
            _apply_llm_update(cfg, llm_data)
        if payload.kind == "llm":
            return await _probe_llm_config(cfg)
        return await _probe_embedding_config(cfg)

    @app.put("/api/config", response_model=ConfigUpdateResponse)
    async def update_config(payload: ConfigUpdateIn) -> ConfigUpdateResponse | JSONResponse:
        """Update configuration, persist to config.toml, and hot-reload runtime.

        Only the fields included in the request body are modified.
        After persisting, the backend attempts to rebuild all swappable
        runtime components so the new settings take effect immediately.
        """
        from openbiliclaw.config import (
            _DEFAULT_ADMISSION_MIN_SCORE,
            _DEFAULT_DELIGHT_QUEUE_LIMIT,
            _DEFAULT_DISCOVERY_LIMIT,
            _DEFAULT_EXPLORE_REFRESH_HOURS,
            _DEFAULT_FEEDBACK_BATCH_THRESHOLD,
            _DEFAULT_MULTIMODAL_BATCH_SIZE,
            _DEFAULT_MULTIMODAL_IMAGE_MAX_PX,
            _DEFAULT_MULTIMODAL_IMAGE_QUALITY,
            _DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS,
            _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
            _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
            _DEFAULT_SIGNAL_EVENT_THRESHOLD,
            _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
            _DEFAULT_TRENDING_REFRESH_HOURS,
            _collect_config_issues,
            _default_config_path,
            _normalize_extension_disconnect_grace,
            _normalize_pool_source_shares,
            _normalize_probability,
            _normalize_scheduler_int,
            load_config,
            save_config,
        )

        cfg = load_config()
        update = payload.model_dump(exclude_none=True)
        reset_fields = [str(field) for field in update.pop("reset_fields", [])]
        suppress_background_llm_work = bool(update.pop("suppress_background_llm_work", False))
        unknown_reset_fields = [
            field for field in reset_fields if field not in _RESETTABLE_CONFIG_FIELDS
        ]
        if unknown_reset_fields:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_reset_fields",
                    "fields": unknown_reset_fields,
                },
            )

        # Apply top-level scalars
        if "language" in update:
            cfg.language = str(update["language"])
        if "data_dir" in update:
            cfg.data_dir = str(update["data_dir"])

        # Apply LLM updates
        if "llm" in update:
            _apply_llm_update(cfg, update["llm"])

        # A masked GET echo looks like ``SESS****abcd`` — a long asterisk run
        # never appears in a genuine Cookie header, so use it (not a single
        # ``*``, which cookie values may legally contain) to detect echoes.
        def _is_masked_echo(value: str) -> bool:
            return "****" in value

        # Apply bilibili updates
        if "bilibili" in update:
            bdata = update["bilibili"]
            if "auth_method" in bdata:
                cfg.bilibili.auth_method = str(bdata["auth_method"])
            if "cookie" in bdata:
                # Mirror the api_key guards: never persist a masked echo, and
                # an empty field never wipes an existing cookie (the browser
                # extension's auto-sync owns refresh; a blank textarea on save
                # must not log the backend out).
                new_cookie = str(bdata["cookie"])
                if not _is_masked_echo(new_cookie) and (
                    new_cookie.strip() or not cfg.bilibili.cookie.strip()
                ):
                    cfg.bilibili.cookie = new_cookie
            if "browser_executable" in bdata:
                cfg.bilibili.browser_executable = str(bdata["browser_executable"])
            if "browser_headed" in bdata:
                cfg.bilibili.browser_headed = _as_bool(bdata["browser_headed"])

        # Apply source updates
        if "sources" in update:
            sources_data = update["sources"]
            if isinstance(sources_data, dict):
                browser_data = sources_data.get("browser")
                if isinstance(browser_data, dict):
                    if "cdp_url" in browser_data:
                        cfg.sources.browser_cdp_url = str(browser_data["cdp_url"])
                    if "headed" in browser_data:
                        cfg.sources.browser_headed = _as_bool(browser_data["headed"])

                bilibili_data = sources_data.get("bilibili")
                if isinstance(bilibili_data, dict) and "enabled" in bilibili_data:
                    cfg.sources.bilibili.enabled = _as_bool(bilibili_data["enabled"])

                xhs_data = sources_data.get("xiaohongshu")
                if isinstance(xhs_data, dict):
                    if "enabled" in xhs_data:
                        cfg.sources.xiaohongshu.enabled = _as_bool(xhs_data["enabled"])
                    for key in (
                        "daily_search_budget",
                        "daily_creator_budget",
                        "task_interval_seconds",
                    ):
                        if key in xhs_data:
                            setattr(cfg.sources.xiaohongshu, key, int(xhs_data[key]))

                dy_data = sources_data.get("douyin")
                if isinstance(dy_data, dict):
                    if "enabled" in dy_data:
                        cfg.sources.douyin.enabled = _as_bool(dy_data["enabled"])
                    if "mode" in dy_data:
                        cfg.sources.douyin.mode = str(dy_data["mode"])
                    if "cookie_env" in dy_data:
                        # The env var name has a sensible default; an emptied
                        # field keeps the current name rather than wiping it.
                        new_env = str(dy_data["cookie_env"]).strip()
                        if new_env:
                            cfg.sources.douyin.cookie_env = new_env
                    if "cookie" in dy_data:
                        # Manual paste from the settings pages. Routed to
                        # data/douyin_cookie.json (same store the extension
                        # auto-sync writes) — secrets never land in config.toml.
                        from openbiliclaw.sources.douyin_auth import (
                            DouyinCookieManager,
                            resolve_douyin_cookie,
                        )

                        new_cookie = str(dy_data["cookie"]).strip()
                        if new_cookie and not _is_masked_echo(new_cookie):
                            current = ""
                            with suppress(Exception):
                                current = resolve_douyin_cookie(
                                    data_dir=cfg.data_path,
                                    cookie_env=cfg.sources.douyin.cookie_env,
                                )
                            # Unchanged form echo → no write (env override
                            # must not be copied into the file needlessly).
                            if new_cookie != current:
                                DouyinCookieManager(cfg.data_path).set_cookie(
                                    new_cookie, source="config-update"
                                )
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_feed_budget",
                        "request_interval_seconds",
                    ):
                        if key in dy_data:
                            setattr(cfg.sources.douyin, key, int(dy_data[key]))

                yt_data = sources_data.get("youtube")
                if isinstance(yt_data, dict):
                    if "enabled" in yt_data:
                        cfg.sources.youtube.enabled = _as_bool(yt_data["enabled"])
                    for key in (
                        "daily_search_budget",
                        "daily_trending_budget",
                        "daily_channel_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in yt_data:
                            setattr(cfg.sources.youtube, key, int(yt_data[key]))

                tw_data = sources_data.get("twitter")
                if isinstance(tw_data, dict):
                    if "enabled" in tw_data:
                        cfg.sources.twitter.enabled = _as_bool(tw_data["enabled"])
                    if "mode" in tw_data:
                        cfg.sources.twitter.mode = str(tw_data["mode"])
                    if "cookie_env" in tw_data:
                        new_env = str(tw_data["cookie_env"]).strip()
                        if new_env:
                            cfg.sources.twitter.cookie_env = new_env
                    if "cookie" in tw_data:
                        # Manual paste — routed to data/x_cookie.json like the
                        # extension auto-sync; never lands in config.toml.
                        new_cookie = str(tw_data["cookie"]).strip()
                        if new_cookie and not _is_masked_echo(new_cookie):
                            current = ""
                            with suppress(Exception):
                                current = resolve_x_cookie(
                                    data_dir=cfg.data_path,
                                    cookie_env=cfg.sources.twitter.cookie_env,
                                )
                            if new_cookie != current:
                                XCookieManager(cfg.data_path).set_cookie(
                                    new_cookie, source="config-update"
                                )
                                # A pasted valid cookie is a re-login signal,
                                # same as the extension sync endpoint: lift any
                                # missing/expired/blocked health block so
                                # discovery retries instead of staying parked.
                                from openbiliclaw.sources.douyin_direct import (
                                    parse_cookie_header,
                                )

                                pairs = parse_cookie_header(new_cookie)
                                if all(
                                    name in pairs for name in _X_REQUIRED_COOKIE_NAMES
                                ) and hasattr(ctx.database, "conn"):
                                    with suppress(Exception):
                                        from openbiliclaw.storage.x_health import (
                                            XSourceHealthStore,
                                        )

                                        XSourceHealthStore(ctx.database).clear_relogin_block()
                    for key in (
                        "daily_search_budget",
                        "daily_feed_budget",
                        "daily_creator_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in tw_data:
                            setattr(cfg.sources.twitter, key, int(tw_data[key]))

                zh_data = sources_data.get("zhihu")
                if isinstance(zh_data, dict):
                    if "enabled" in zh_data:
                        cfg.sources.zhihu.enabled = _as_bool(zh_data["enabled"])
                    if "source_modes" in zh_data:
                        raw_modes = zh_data["source_modes"]
                        if isinstance(raw_modes, str):
                            modes = [part.strip() for part in raw_modes.split(",")]
                        elif isinstance(raw_modes, list):
                            modes = [str(part).strip() for part in raw_modes]
                        else:
                            modes = []
                        selected = [
                            mode
                            for mode in modes
                            if mode in {"search", "hot", "feed", "creator", "related"}
                        ]
                        if selected:
                            cfg.sources.zhihu.source_modes = tuple(dict.fromkeys(selected))
                    for key in (
                        "daily_search_budget",
                        "daily_hot_budget",
                        "daily_feed_budget",
                        "daily_creator_budget",
                        "daily_related_budget",
                        "request_interval_seconds",
                        "min_interval_minutes",
                    ):
                        if key in zh_data:
                            setattr(cfg.sources.zhihu, key, int(zh_data[key]))

        # Apply scheduler updates
        if "scheduler" in update:
            sdata = update["scheduler"]
            scheduler_int_limits = {
                "refresh_check_interval_seconds": (
                    _DEFAULT_REFRESH_CHECK_INTERVAL_SECONDS,
                    15,
                    None,
                ),
                "signal_event_threshold": (_DEFAULT_SIGNAL_EVENT_THRESHOLD, 1, None),
                "trending_refresh_hours": (_DEFAULT_TRENDING_REFRESH_HOURS, 1, None),
                "explore_refresh_hours": (_DEFAULT_EXPLORE_REFRESH_HOURS, 1, None),
                "discovery_limit": (_DEFAULT_DISCOVERY_LIMIT, 1, 60),
                "delight_queue_limit": (_DEFAULT_DELIGHT_QUEUE_LIMIT, 1, 100),
                "proactive_push_interval_seconds": (
                    _DEFAULT_PROACTIVE_PUSH_INTERVAL_SECONDS,
                    30,
                    None,
                ),
                "speculator_idle_interval_minutes": (
                    _DEFAULT_SPECULATOR_IDLE_INTERVAL_MINUTES,
                    5,
                    None,
                ),
                "feedback_batch_threshold": (
                    _DEFAULT_FEEDBACK_BATCH_THRESHOLD,
                    1,
                    None,
                ),
                "avoidance_speculation_interval_minutes": (10, 1, None),
                "avoidance_speculation_ttl_days": (3, 1, None),
                "avoidance_speculation_cooldown_days": (7, 1, None),
                "avoidance_speculation_confirmation_threshold": (3, 1, None),
                "avoidance_speculation_max_active": (5, 1, None),
            }
            for key in (
                "enabled",
                "pause_on_extension_disconnect",
                "extension_disconnect_grace_seconds",
                "discovery_cron",
                "pool_target_count",
                "account_sync_interval_hours",
                "refresh_check_interval_seconds",
                "signal_event_threshold",
                "trending_refresh_hours",
                "explore_refresh_hours",
                "discovery_limit",
                "delight_queue_limit",
                "proactive_push_interval_seconds",
                "speculator_idle_interval_minutes",
                "speculation_interval_minutes",
                "speculation_ttl_days",
                "speculation_cooldown_days",
                "speculation_confirmation_threshold",
                "speculation_max_active",
                "speculation_max_primary_interests",
                "speculation_max_secondary_interests",
                "avoidance_speculation_interval_minutes",
                "avoidance_speculation_ttl_days",
                "avoidance_speculation_cooldown_days",
                "avoidance_speculation_confirmation_threshold",
                "avoidance_speculation_max_active",
                "auto_update_enabled",
                "auto_update_check_interval_hours",
                "auto_update_allow_prerelease",
                "auto_update_allowed_remotes",
                "feedback_batch_threshold",
            ):
                if key in sdata:
                    current_val = getattr(cfg.scheduler, key)
                    if key == "auto_update_allowed_remotes":
                        next_remotes = _string_list(sdata[key])
                        if next_remotes:
                            setattr(cfg.scheduler, key, next_remotes)
                    elif key == "extension_disconnect_grace_seconds":
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_extension_disconnect_grace(sdata[key]),
                        )
                    elif key in scheduler_int_limits:
                        default, min_value, max_value = scheduler_int_limits[key]
                        setattr(
                            cfg.scheduler,
                            key,
                            _normalize_scheduler_int(
                                sdata[key],
                                default=default,
                                min_value=min_value,
                                max_value=max_value,
                            ),
                        )
                    elif isinstance(current_val, bool):
                        setattr(cfg.scheduler, key, _as_bool(sdata[key]))
                    elif isinstance(current_val, int):
                        setattr(cfg.scheduler, key, int(sdata[key]))
                    else:
                        setattr(cfg.scheduler, key, str(sdata[key]))
            if "pool_source_shares" in sdata:
                cfg.scheduler.pool_source_shares = _normalize_pool_source_shares(
                    sdata["pool_source_shares"]
                )
            # Update subscription lists (rss, xiaoyuzhou, wechat)
            if "rss_subscriptions" in sdata:
                cfg.scheduler.rss_subscriptions = list(sdata["rss_subscriptions"])
            if "xiaoyuzhou_subscriptions" in sdata:
                cfg.scheduler.xiaoyuzhou_subscriptions = list(sdata["xiaoyuzhou_subscriptions"])
            if "wechat_subscriptions" in sdata:
                cfg.scheduler.wechat_subscriptions = list(sdata["wechat_subscriptions"])

        # Apply discovery planner / evaluator updates
        if "discovery" in update:
            ddata = update["discovery"]
            if isinstance(ddata, dict):
                discovery_int_limits = {
                    "multimodal_batch_size": (
                        _DEFAULT_MULTIMODAL_BATCH_SIZE,
                        1,
                        12,
                    ),
                    "multimodal_image_max_px": (
                        _DEFAULT_MULTIMODAL_IMAGE_MAX_PX,
                        128,
                        768,
                    ),
                    "multimodal_image_quality": (
                        _DEFAULT_MULTIMODAL_IMAGE_QUALITY,
                        40,
                        90,
                    ),
                    "multimodal_image_timeout_seconds": (
                        _DEFAULT_MULTIMODAL_IMAGE_TIMEOUT_SECONDS,
                        1,
                        20,
                    ),
                }
                if "multimodal_evaluation_enabled" in ddata:
                    cfg.discovery.multimodal_evaluation_enabled = _as_bool(
                        ddata["multimodal_evaluation_enabled"]
                    )
                if "admission_min_score" in ddata:
                    cfg.discovery.admission_min_score = _normalize_probability(
                        ddata["admission_min_score"],
                        default=_DEFAULT_ADMISSION_MIN_SCORE,
                    )
                for key, (default, min_value, max_value) in discovery_int_limits.items():
                    if key in ddata:
                        setattr(
                            cfg.discovery,
                            key,
                            _normalize_scheduler_int(
                                ddata[key],
                                default=default,
                                min_value=min_value,
                                max_value=max_value,
                            ),
                        )

        # Apply storage updates
        if "storage" in update:
            stdata = update["storage"]
            if "db_path" in stdata:
                cfg.storage.db_path = str(stdata["db_path"])

        # Apply logging updates
        if "logging" in update:
            ldata = update["logging"]
            for key in ("level", "file_level", "directory", "filename"):
                if key in ldata:
                    setattr(cfg.logging, key, str(ldata[key]))
            for key in (
                "max_file_size_mb",
                "backup_count",
                "aggregate_budget_mb",
                "unmanaged_truncate_mb",
                "unmanaged_max_age_days",
            ):
                if key in ldata:
                    setattr(cfg.logging, key, int(ldata[key]))

        for field in reset_fields:
            target = _RESETTABLE_CONFIG_FIELDS[field]
            section = getattr(cfg, target[0])
            subsection = getattr(section, target[1])
            setattr(subsection, target[2], "")

        issues = _validate_llm_buildable(cfg, _collect_config_issues(cfg))
        if any(getattr(issue, "severity", "warning") == "blocking" for issue in issues):
            response = ConfigUpdateResponse(
                ok=False,
                config=_config_to_response(
                    cfg,
                    issues,
                    mask_keys=True,
                    degraded=bool(getattr(ctx, "degraded", False)),
                    degraded_reason=str(getattr(ctx, "degraded_reason", "")),
                ),
                message="配置校验失败，未写入 config.toml。",
                reloaded=False,
                rollback_applied=False,
                restart_required=False,
            )
            return JSONResponse(
                status_code=400,
                content=response.model_dump(mode="json"),
            )

        async with _CONFIG_SAVE_LOCK:
            # gui-init D1 / spec §5b: re-check inside the lock. The middleware
            # gated this path on init_active before the handler ran, but a run
            # could have been reserved in between; saving + rebuilding config
            # mid-init would swap components the run is using.
            if _init_active_now():
                return JSONResponse(
                    {"error": "init_running", "detail": "初始化进行中，请稍后再保存配置"},
                    status_code=409,
                )
            config_path = _default_config_path()
            try:
                backup_path = _snapshot_config_file(config_path)
            except Exception as exc:
                logger.exception("Config snapshot failed — refusing to overwrite config.toml")
                return JSONResponse(
                    status_code=500,
                    content={
                        "error": "config_snapshot_failed",
                        "message": f"couldn't snapshot config, refusing to risk overwrite: {exc}",
                    },
                )

            saved_path = save_config(cfg)
            logger.info("Configuration saved to %s", saved_path)

            if bool(getattr(ctx, "degraded", False)):
                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(
                        cfg,
                        issues,
                        mask_keys=True,
                        degraded=True,
                        degraded_reason=str(getattr(ctx, "degraded_reason", "")),
                    ),
                    message=(
                        f"配置已保存到 {saved_path}。当前后端处于降级模式，"
                        "请 restart daemon 后让新配置生效。"
                    ),
                    reloaded=False,
                    rollback_applied=False,
                    restart_required=True,
                )

            # ── Hot-reload: rebuild runtime components ──────────────
            reload_message = f"配置已保存到 {saved_path}。"
            try:
                await ctx.rebuild_from_config(cfg)
                await ctx.restart_background_tasks(
                    app,
                    run_post_reload_llm_work=not suppress_background_llm_work,
                )
                reload_message += " 运行时组件已热重载，新配置立即生效。"
                logger.info("Config hot-reload succeeded")
                # Notify WebSocket subscribers so the extension re-fetches data
                with suppress(Exception):
                    await ctx.event_hub.publish(
                        {
                            "type": "config_reloaded",
                            "message": "配置已热重载，运行时组件已重建。",
                        }
                    )
                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(cfg, issues, mask_keys=True),
                    message=reload_message,
                    reloaded=True,
                    rollback_applied=False,
                    restart_required=False,
                )
            except Exception as exc:
                logger.exception("Config hot-reload failed — attempting config rollback")
                if backup_path is None:
                    rollback_message = (
                        f" 热重载失败（{str(exc)[:200]}），未找到可回滚的 config.toml.bak。"
                    )
                    rollback_cfg = cfg
                    rollback_applied = False
                else:
                    try:
                        _restore_config_snapshot(backup_path, saved_path)
                    except Exception as restore_exc:
                        logger.critical(
                            "Config rollback failed after hot-reload exception",
                            exc_info=True,
                        )
                        return JSONResponse(
                            status_code=500,
                            content={
                                "error": "config_persistence_corrupted",
                                "message": (
                                    "config.toml may be in inconsistent state after hot-reload "
                                    f"failure and rollback failure: {restore_exc}"
                                ),
                                "manual_recovery": (
                                    "config.toml may be in inconsistent state; if "
                                    "config.toml.bak exists, manually copy it back."
                                ),
                            },
                        )
                    rollback_cfg = load_config(saved_path)
                    rollback_message = (
                        f" 热重载失败（{str(exc)[:200]}），已从 config.toml.bak 回滚。"
                    )
                    rollback_applied = True

                return ConfigUpdateResponse(
                    ok=True,
                    config=_config_to_response(rollback_cfg, _collect_config_issues(rollback_cfg)),
                    message=reload_message + rollback_message,
                    reloaded=False,
                    rollback_applied=rollback_applied,
                    restart_required=False,
                )

    def _normalize_enabled_sources_override(
        raw_enabled: dict[str, bool] | None,
        fallback: dict[str, bool],
    ) -> dict[str, bool]:
        if raw_enabled is None:
            return fallback
        enabled: dict[str, bool] = {}
        for source in _SOURCE_SHARE_ORDER:
            enabled[source] = bool(raw_enabled.get(source, fallback.get(source, False)))
        return {source: enabled.get(source, False) for source in _SOURCE_SHARE_ORDER}

    def _build_source_share_suggestion_response(
        payload: SourceShareSuggestionIn | None = None,
    ) -> SourceShareSuggestionResponse:
        """Suggest pool source shares from observed platform event counts."""
        from openbiliclaw.config import load_config
        from openbiliclaw.runtime.source_policy import (
            source_enabled_map,
            suggest_pool_source_shares,
        )

        cfg = load_config()
        event_counts = _count_events_by_source_platform(ctx.database)
        enabled_sources = _normalize_enabled_sources_override(
            payload.enabled_sources if payload else None,
            source_enabled_map(cfg),
        )
        suggested_shares = suggest_pool_source_shares(
            event_counts,
            enabled_sources=enabled_sources,
            configured_shares=(
                payload.configured_shares
                if payload and payload.configured_shares is not None
                else cfg.scheduler.pool_source_shares
            ),
        )
        return SourceShareSuggestionResponse(
            event_counts=event_counts,
            enabled_sources=enabled_sources,
            suggested_shares=suggested_shares,
        )

    @app.get(
        "/api/config/source-share-suggestion",
        response_model=SourceShareSuggestionResponse,
    )
    def source_share_suggestion() -> SourceShareSuggestionResponse:
        """Suggest pool source shares from saved config switches."""
        return _build_source_share_suggestion_response()

    @app.post(
        "/api/config/source-share-suggestion",
        response_model=SourceShareSuggestionResponse,
    )
    def source_share_suggestion_for_form(
        payload: SourceShareSuggestionIn,
    ) -> SourceShareSuggestionResponse:
        """Suggest pool source shares from unsaved settings form state."""
        return _build_source_share_suggestion_response(payload)

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
    @app.get("/api/subscriptions", response_model=SubscriptionListOut)
    def list_subscriptions() -> SubscriptionListOut:
        """List all subscription sources grouped by type."""
        from openbiliclaw.config import load_config as _load_cfg

        _cfg = _load_cfg()
        return SubscriptionListOut(
            rss=list(_cfg.scheduler.rss_subscriptions),
            xiaoyuzhou=list(_cfg.scheduler.xiaoyuzhou_subscriptions),
            wechat=list(_cfg.scheduler.wechat_subscriptions),
        )

    @app.get("/api/articles")
    def list_articles(
        q: str = "",
        source_type: str = "",
        status: str = "",
        tag: str = "",
        limit: int = 50,
        offset: int = 0,
        random: bool = False,
        sort: str = "",
    ) -> JSONResponse:
        """List reading-library articles, optionally filtered.

        Supports keyword search (``q``) over the full-text index, plus
        filtering by source type, reading status and tag. When ``q`` is given
        the results are ranked by FTS relevance (bm25); otherwise they are
        ordered by recency. ``sort=relevance`` re-ranks by interest-profile
        fit (each item carries a ``fit_score`` 0-1); the re-rank samples the
        top ``max(limit*4, 200)`` recent rows so the per-request cost stays
        tiny while still surfacing the best matches.
        """
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"items": [], "total": 0})
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        if q and q.strip():
            items = database.search_articles(
                q=q.strip(),
                limit=limit,
                offset=offset,
                source_type=source_type or None,
                status=status or None,
                tag=tag or None,
            )
            total = len(items)
        else:
            items = database.get_recent_articles(
                limit=limit,
                offset=offset,
                source_type=source_type or None,
                status=status or None,
                tag=tag or None,
                random_order=random,
            )
            total = database.count_articles(
                source_type=source_type or None,
                status=status or None,
                tag=tag or None,
            )
            if sort and sort.strip().lower() == "relevance" and not random:
                candidate_limit = max(limit * 4, 200)
                cands = database.get_recent_articles(
                    limit=candidate_limit,
                    offset=0,
                    source_type=source_type or None,
                    status=status or None,
                    tag=tag or None,
                )
                for item in cands:
                    text = " ".join(
                        [
                            str(item.get("title") or ""),
                            str(item.get("summary") or ""),
                            str(item.get("tags") or ""),
                        ]
                    )
                    item["fit_score"] = _article_fit_score(text)
                cands.sort(key=lambda it: it.get("fit_score", 0.0), reverse=True)
                items = cands[offset : offset + limit]
        return JSONResponse({"items": items, "total": total, "query": q})

    @app.get("/api/articles/facets", response_model=None)
    def list_article_facets() -> JSONResponse:
        """Source-type distribution for the reading-library filter UI."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"source_types": [], "total": 0})
        try:
            rows = database.conn.execute(
                "SELECT source_type, COUNT(*) AS n FROM articles "
                "WHERE COALESCE(status, 'unread') != 'hidden' "
                "GROUP BY source_type ORDER BY n DESC"
            ).fetchall()
        except Exception:
            logger.exception("Failed to read article facets")
            return JSONResponse({"source_types": [], "total": 0})
        total = sum(int(r["n"]) for r in rows)
        return JSONResponse(
            {
                "source_types": [{"type": r["source_type"], "count": int(r["n"])} for r in rows],
                "total": total,
            }
        )

    @app.get("/api/articles/{article_id}")
    def get_article(article_id: int) -> JSONResponse:
        """Fetch a single article with its full body text for reading."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        row = database.get_article(article_id)
        if row is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        return JSONResponse({"ok": True, "article": row})

    @app.get("/api/read-archive/articles/{article_id}")
    def get_read_archive_article(article_id: int) -> JSONResponse:
        """Fetch a single read-archive article with its full body text."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        row = database.get_readarchive_article(article_id)
        if row is None:
            return JSONResponse(
                {"ok": False, "error": "read-archive article not found"}, status_code=404
            )
        return JSONResponse({"ok": True, "article": row})

    @app.patch("/api/articles/{article_id}")
    async def update_article(article_id: int, payload: ArticleUpdateIn) -> JSONResponse:
        """Update an article's reading status and/or tags."""
        # Mirrors Database.ARTICLE_STATUSES — keep the two in sync.
        valid = {"unread", "reading", "finished", "archived", "hidden"}
        if payload.status is not None and payload.status not in valid:
            return JSONResponse(
                {
                    "ok": False,
                    "error": f"invalid status: {payload.status}; expected one of {sorted(valid)}",
                },
                status_code=400,
            )
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = True
        pool_purged = 0
        pool_revived = 0
        previous_status: str | None = None
        if payload.status is not None:
            previous_row = database.get_article(article_id)
            previous_status = str(previous_row.get("status") or "") if previous_row else None
            ok = bool(database.update_article_status(article_id, payload.status))
            # 读完回流画像：finished 是强正向信号，插入事件由 soul 管道
            # 自然消费（classify_event_satisfaction 已将其归为 positive）。
            # 失败不阻塞主操作。
            if ok and payload.status == "finished":
                try:
                    row = database.get_article(article_id)
                    if row:
                        from openbiliclaw.sources.event_format import (
                            format_event_context,
                        )

                        # E2: fold the article's tags into the context so the
                        # preference analyzer sees topic-level evidence, not
                        # just the title (tags were previously metadata-only
                        # and never reached the LLM prompt).
                        _tags = _article_tags_for_context(row.get("tags"))
                        context = format_event_context(
                            event_type="article_finished",
                            source_platform=str(row.get("source_type") or "阅读库"),
                            title=str(row.get("title") or ""),
                            author=str(row.get("author") or ""),
                            extra=_tags,
                        )
                        database.insert_event(
                            "article_finished",
                            url=str(row.get("url") or ""),
                            title=str(row.get("title") or ""),
                            context=context,
                            metadata={
                                "article_id": article_id,
                                "source_type": str(row.get("source_type") or ""),
                                "source_name": str(row.get("source_name") or ""),
                                "author": str(row.get("author") or ""),
                                "tags": row.get("tags") or "[]",
                                "signal_strength": 0.8,
                            },
                        )
                except Exception:
                    logger.exception("Failed to record article_finished event")
            # 屏蔽回流画像：hidden 是用户主动表达的负向信号，插入事件由
            # soul 管道作为「避开这类内容」的证据消费。失败不阻塞主操作。
            if ok and payload.status == "hidden":
                blocked_url = ""
                try:
                    row = database.get_article(article_id)
                    if row:
                        from openbiliclaw.sources.event_format import (
                            format_event_context,
                        )

                        # E2: same tag-folding as article_finished so the
                        # negative signal carries topic-level evidence too.
                        _tags = _article_tags_for_context(row.get("tags"))
                        context = format_event_context(
                            event_type="article_dismissed",
                            source_platform=str(row.get("source_type") or "阅读库"),
                            title=str(row.get("title") or ""),
                            author=str(row.get("author") or ""),
                            extra=_tags,
                        )
                        database.insert_event(
                            "article_dismissed",
                            url=str(row.get("url") or ""),
                            title=str(row.get("title") or ""),
                            context=context,
                            metadata={
                                "article_id": article_id,
                                "source_type": str(row.get("source_type") or ""),
                                "source_name": str(row.get("source_name") or ""),
                                "author": str(row.get("author") or ""),
                                "tags": row.get("tags") or "[]",
                                "signal_strength": 0.8,
                            },
                        )
                        blocked_url = str(row.get("url") or "")
                except Exception:
                    logger.exception("Failed to record article_dismissed event")
                # 同步清洗候选池：被屏蔽文章若同时是推荐池里的 fresh 候选
                # （RSS 注入 / 推荐后存入库），立即置为 suppressed，让
                # 「屏蔽」当场生效而不是等 soul 管道异步学习。suppressed
                # 会在重新发现时自动复活，与「恢复」操作配对。
                try:
                    pool_purged = database.suppress_pool_rows_by_url(blocked_url)
                except Exception:
                    logger.exception("Failed to suppress pool rows for blocked article")
            elif ok and previous_status == "hidden":
                # 取消屏蔽：把此前被同一 URL 连坐抑制的候选放回 fresh。
                try:
                    row = database.get_article(article_id)
                    pool_revived = database.revive_suppressed_pool_rows_by_url(
                        str((row or {}).get("url") or "")
                    )
                except Exception:
                    logger.exception("Failed to revive suppressed pool rows")
        if payload.tags is not None and ok:
            ok = bool(database.update_article_tags(article_id, payload.tags))
        if (payload.percent is not None or payload.progress is not None) and ok:
            ok = bool(
                database.update_article_reading_progress(
                    article_id,
                    percent=payload.percent if payload.percent is not None else 0.0,
                    progress=payload.progress or "",
                )
            )
        if payload.favorited is not None and ok:
            ok = bool(database.set_article_favorited(article_id, payload.favorited))
        return JSONResponse(
            {
                "ok": ok,
                "id": article_id,
                "purged_pool_count": pool_purged,
                "revived_pool_count": pool_revived,
            }
        )

    @app.get("/api/articles/{article_id}/notes")
    def list_article_notes(article_id: int) -> JSONResponse:
        """List notes / highlights for an article."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if database.get_article(article_id) is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        notes = database.get_article_notes(article_id)
        return JSONResponse({"ok": True, "notes": notes})

    @app.post("/api/articles/{article_id}/notes")
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

    @app.delete("/api/notes/{note_id}")
    def delete_article_note(note_id: int) -> JSONResponse:
        """Delete one note by its own id."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = database.delete_article_note(note_id)
        return JSONResponse({"ok": ok, "id": note_id})

    @app.post("/api/articles/{article_id}/summarize")
    async def summarize_article(article_id: int) -> JSONResponse:
        """Generate / refresh an AI summary (one-liner + 3 key points) via LLM.

        Idempotent: an existing summary is returned as-is unless ``force``
        is passed. Requires a body of at least 200 chars; short articles
        fall back to their stored ``summary`` field.
        """
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        row = database.get_article(article_id)
        if row is None:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        if row.get("ai_summary"):
            try:
                return JSONResponse(
                    {"ok": True, "summary": json.loads(row["ai_summary"]), "cached": True}
                )
            except Exception:
                pass  # 损坏则重新生成
        content = str(row.get("content_text") or "").strip()
        if len(content) < 200:
            return JSONResponse(
                {"ok": False, "error": "正文过短，无法生成摘要", "article_id": article_id},
                status_code=422,
            )
        try:
            from openbiliclaw.config import load_config as _sum_cfg
            from openbiliclaw.llm.registry import build_llm_registry as _build_reg

            registry = _build_reg(_sum_cfg())
            system = (
                "你是个人阅读助手。为下面这篇文章生成中文摘要，"
                "只输出 JSON，不要多余文字，格式："
                '{"one_liner":"不超过30字的一句话总结","points":["要点1，一句话","要点2，一句话","要点3，一句话"]}'
            )
            user = (
                f"标题：{row.get('title') or ''}\n"
                f"作者：{row.get('author') or ''}\n"
                f"来源：{row.get('source_name') or row.get('source_type') or ''}\n"
                f"标签：{row.get('tags') or '[]'}\n\n"
                f"正文（截取前 3000 字）：\n{content[:3000]}"
            )
            resp = await registry.complete(
                [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,
                # sensenova-6.8 is a reasoning model: its thinking process
                # routinely eats 1000+ tokens, so a 700-token budget was
                # exhausted by reasoning alone and content came back empty
                # (finish_reason=length). 3000 leaves room for reasoning +
                # the actual summary.
                max_tokens=3000,
                json_mode=True,
            )
            raw = (resp.content or "").strip()
            summary = json.loads(raw)
            if not isinstance(summary, dict):
                raise ValueError("non-dict summary")
            summary.setdefault("one_liner", "")
            summary.setdefault("points", [])
            summary["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            database.update_article_ai_summary(article_id, json.dumps(summary, ensure_ascii=False))
            return JSONResponse({"ok": True, "summary": summary, "cached": False})
        except Exception:
            logger.exception("AI summary generation failed for article %d", article_id)
            return JSONResponse(
                {"ok": False, "error": "摘要生成失败，请稍后重试", "article_id": article_id},
                status_code=502,
            )

    def _rank_unread_by_interest(database: Any, *, limit: int) -> list[dict[str, Any]]:
        """未读文章按兴趣契合度排序（今日建议与每日简报共用）。

        抽样最近未读文章（优先有正文），按 soul 兴趣画像关键词打分，
        返回 top ``limit``，附人读得懂的命中理由。
        """
        keywords = _load_interest_keywords()
        cands = database.get_recent_articles(limit=300, status="unread")
        scored: list[tuple[float, dict[str, Any]]] = []
        for item in cands:
            text = " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("summary") or ""),
                    str(item.get("tags") or ""),
                ]
            ).lower()
            score = 0.0
            reasons: list[str] = []
            for name, weight in keywords:
                if name and name.lower() in text:
                    score += weight
                    if len(reasons) < 2:
                        reasons.append(name)
            if score > 0:
                item["fit_score"] = round(min(1.0, score / 1.5), 3)
                item["fit_reason"] = reasons
                scored.append((score, item))
        scored.sort(key=lambda kv: kv[0], reverse=True)
        return [it for _, it in scored[: max(1, int(limit))]]

    @app.get("/api/reading/suggestions")
    def daily_reading_suggestions(limit: int = 5) -> JSONResponse:
        """Today's reading picks: unread articles ranked by interest fit."""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        limit = max(1, min(int(limit), 20))
        items = _rank_unread_by_interest(database, limit=limit)
        return JSONResponse(
            {
                "ok": True,
                "items": items,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    @app.get("/api/reading/daily-brief")
    def reading_daily_brief() -> JSONResponse:
        """每日简报（确定性聚合，零 LLM）：阅读库页顶部卡片的数据源。

        三个板块：
        - ``reading``: 今日已读回顾——当天标记 finished 的文章数、来源
          分布、主题标签（``Database.get_daily_reading_summary``）。
        - ``profile``: 画像今天学到什么——今天的认知更新（含手动纠偏），
          与画像页共用 ``memory_manager.load_cognition_updates``；soul
          未初始化时该板块为空，不阻塞其余板块。
        - ``tomorrow``: 明日值得看——未读文章按兴趣契合度 top 5
          （与今日建议共用 ``_rank_unread_by_interest``）。
        """
        import datetime as _dt

        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        today = _dt.datetime.now().strftime("%Y-%m-%d")

        reading = database.get_daily_reading_summary(day=today)

        profile_updates: list[dict[str, Any]] = []
        load_cognition_updates = getattr(ctx.memory_manager, "load_cognition_updates", None)
        if callable(load_cognition_updates):
            with suppress(Exception):
                for item in load_cognition_updates():
                    created = str(item.get("created_at") or "")
                    if not created.startswith(today):
                        continue
                    summary_text = str(item.get("summary") or "").strip()
                    if not summary_text:
                        continue
                    profile_updates.append(
                        {
                            "summary": summary_text,
                            "source_label": str(item.get("source_label") or ""),
                            "created_at": created,
                        }
                    )
                    if len(profile_updates) >= 5:
                        break

        tomorrow: list[dict[str, Any]] = []
        with suppress(Exception):
            tomorrow = [
                {
                    "id": it.get("id"),
                    "title": it.get("title"),
                    "source_type": it.get("source_type"),
                    "fit_score": it.get("fit_score"),
                    "fit_reason": it.get("fit_reason", []),
                }
                for it in _rank_unread_by_interest(database, limit=5)
            ]

        return JSONResponse(
            {
                "ok": True,
                "date": today,
                "reading": reading,
                "profile": {"updates": profile_updates},
                "tomorrow": tomorrow,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    @app.get("/api/reading/intent-search")
    async def reading_intent_search(
        q: str = "",
        limit: int = 30,
        source_type: str = "",
        status: str = "",
    ) -> JSONResponse:
        """自然语言意图搜索阅读库：把口语查询解析成关键词 / 排除 / 来源 / 状态。

        与旧的 ``/api/articles?q=`` 纯子串匹配不同，这里先「理解」查询：

        - **主路径 LLM**：用 ``soul_engine.llm_ask`` 把 ``q`` 拆成
          ``{keywords, exclude, source_type, status}``（能处理同义词、
          「不要营销号」这类排除、「最近想读点轻松的」这类口语）。
        - **规则回退**：LLM 不可用 / 未配置 / 解析失败时走
          :func:`_rule_parse_reading_intent`，按词表剥离来源、状态与
          「不要 X」排除，剩余作关键词。
        - 关键词并集检索（复用 FTS ``search_articles``）→ 排除过滤 →
          按兴趣画像契合度（``_article_fit_score``）重排。

        显式传入的 ``source_type`` / ``status`` 覆盖模型推断值，保证与
        前端来源页 / 状态下拉一致。返回附 ``intent`` 供前端回显「我理解成
        了什么」，让纠偏有据可依。
        """
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        q = (q or "").strip()
        limit = max(1, min(int(limit), 60))
        if not q:
            return JSONResponse({"ok": True, "items": [], "total": 0, "intent": {}})

        intent: dict[str, Any] | None = None
        soul_engine = getattr(ctx, "soul_engine", None)
        llm_ask = getattr(soul_engine, "llm_ask", None) if soul_engine is not None else None
        if callable(llm_ask) and len(q) >= 3:
            with suppress(Exception):
                sys_prompt = (
                    "你是阅读库搜索的意图解析器。把用户的自然语言查询拆成结构化检索意图，"
                    '只输出 JSON：{"keywords":[检索关键词],'
                    '"exclude":[要排除的词，如『不要营销号』里的『营销号』],'
                    '"source_type":来源或null,'
                    '"status":unread|reading|finished|archived 之一或null}。'
                    f"source_type 只能取这些值之一：{sorted(_READING_VALID_SOURCE_TYPES)}；"
                    "不符合的填 null。keywords 用具体、聚焦的词，去掉停用词。"
                )
                raw = await llm_ask(sys_prompt, q)
                if raw:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        kws = [
                            str(k).strip() for k in (parsed.get("keywords") or []) if str(k).strip()
                        ]
                        exc = [
                            str(e).strip() for e in (parsed.get("exclude") or []) if str(e).strip()
                        ]
                        src = str(parsed.get("source_type") or "").strip().lower()
                        stt = str(parsed.get("status") or "").strip().lower()
                        intent = {
                            "keywords": kws[:6],
                            "exclude": exc,
                            "source_type": src if src in _READING_VALID_SOURCE_TYPES else "",
                            "status": stt if stt in _READING_VALID_STATUSES else "",
                            "llm_used": True,
                        }
        if intent is None:
            intent = _rule_parse_reading_intent(q)

        # 显式查询参数优先于模型推断，避免与前端筛选下拉打架。
        if source_type.strip():
            intent["source_type"] = source_type.strip().lower()
        if status.strip():
            intent["status"] = status.strip().lower()

        source_type_filter: str | None = intent["source_type"] or None
        st = intent["status"] or None
        terms = intent["keywords"] or [q]

        merged: dict[int, dict[str, Any]] = {}
        order: list[int] = []
        per_term_limit = max(limit, 30)
        for term in terms:
            rows = database.search_articles(
                q=term,
                limit=per_term_limit,
                offset=0,
                source_type=source_type_filter,
                status=st,
            )
            for row in rows:
                try:
                    rid = int(row.get("id"))
                except (TypeError, ValueError):
                    continue
                if rid not in merged:
                    merged[rid] = row
                    order.append(rid)
        items = [merged[rid] for rid in order]
        items = _apply_reading_exclusions(items, intent.get("exclude") or [])

        for item in items:
            text = " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("summary") or ""),
                    str(item.get("tags") or ""),
                ]
            )
            item["fit_score"] = _article_fit_score(text)
        items.sort(
            key=lambda it: (
                float(it.get("fit_score") or 0.0),
                str(it.get("published_at") or ""),
            ),
            reverse=True,
        )
        items = items[:limit]
        return JSONResponse(
            {
                "ok": True,
                "items": items,
                "total": len(items),
                "intent": intent,
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        )

    @app.get("/api/reading/stats")
    def reading_stats() -> JSONResponse:
        """Reading-library dashboard: totals, monthly finished trend,
        source mix, top tags, notes count, and a light interest-shift
        view (recently-read tags vs the current interest profile).
        """
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = database.get_article_reading_stats()
        # 兴趣迁移：最近在读/读完文章打到的画像关键词
        recent = database.get_articles_for_reading_stats(limit=100)
        interest_shift: dict[str, Any] = {"matched": [], "recent_articles": len(recent)}
        keywords = _load_interest_keywords()
        hit_counter: dict[str, float] = {}
        for item in recent:
            text = " ".join(
                [
                    str(item.get("title") or ""),
                    str(item.get("tags") or ""),
                ]
            ).lower()
            for name, weight in keywords:
                if name and name.lower() in text:
                    hit_counter[name] = hit_counter.get(name, 0.0) + weight
        interest_shift["matched"] = sorted(hit_counter.items(), key=lambda kv: kv[1], reverse=True)[
            :10
        ]
        return JSONResponse({"ok": True, "stats": stats, "interest_shift": interest_shift})

    # ── 知识库概念反向索引 API ─────────────────────────────────
    @app.get("/api/knowledge/concepts")
    def knowledge_concepts(
        q: str = "",
        source: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """搜索概念反向索引。"""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            conn = database.conn
            where = []
            params: list = []
            if q:
                where.append("kc.concept LIKE ?")
                params.append(f"%{q}%")
            if source:
                where.append("kc.source_site = ?")
                params.append(source)
            where_clause = " AND ".join(where) if where else "1=1"

            # 统计
            total = conn.execute(
                f"SELECT COUNT(DISTINCT kc.concept) FROM knowledge_concepts kc WHERE {where_clause}",
                params,
            ).fetchone()[0]

            # 分组查询
            rows = conn.execute(
                f"""SELECT kc.concept, kc.concept_type, kc.source_site,
                           COUNT(*) as ref_count
                    FROM knowledge_concepts kc
                    WHERE {where_clause}
                    GROUP BY kc.concept, kc.source_site
                    ORDER BY ref_count DESC
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

            items = [
                {
                    "concept": r[0],
                    "type": r[1],
                    "source": r[2],
                    "ref_count": r[3],
                }
                for r in rows
            ]

            return JSONResponse({"ok": True, "items": items, "total": total})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/knowledge/concepts/{concept_name}")
    def knowledge_concept_detail(concept_name: str, source: str = "") -> JSONResponse:
        """查看某个概念在哪些文章中被提及。"""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            conn = database.conn
            where = ["kb.target_concept = ?"]
            params: list = [concept_name]
            if source:
                where.append("kb.source_site = ?")
                params.append(source)

            where_clause = " AND ".join(where)

            rows = conn.execute(
                f"""SELECT kb.source_article_id, kb.source_title, kb.source_url,
                           kb.source_site, kb.target_type, a.summary, a.tags
                    FROM knowledge_backlinks kb
                    LEFT JOIN articles a ON kb.source_article_id = a.id
                    WHERE {where_clause}
                    ORDER BY kb.source_site, kb.source_title""",
                params,
            ).fetchall()

            # 获取概念类型从 knowledge_concepts
            concept_type = "concept"
            if source:
                ct_row = conn.execute(
                    "SELECT DISTINCT concept_type FROM knowledge_concepts WHERE concept = ? AND source_site = ? LIMIT 1",
                    (concept_name, source),
                ).fetchone()
                if ct_row:
                    concept_type = ct_row[0] or "concept"
            else:
                ct_row = conn.execute(
                    "SELECT DISTINCT concept_type FROM knowledge_concepts WHERE concept = ? LIMIT 1",
                    (concept_name,),
                ).fetchone()
                if ct_row:
                    concept_type = ct_row[0] or "concept"

            # 按来源站点分组
            by_source: dict[str, list[dict]] = {}
            for r in rows:
                site = r[3] or "unknown"
                if site not in by_source:
                    by_source[site] = []
                by_source[site].append(
                    {
                        "article_id": r[0],
                        "title": r[1],
                        "url": r[2],
                        "summary": r[5] or "",
                        "tags": json.loads(r[6]) if r[6] else [],
                    }
                )

            return JSONResponse(
                {
                    "ok": True,
                    "concept": concept_name,
                    "type": concept_type,
                    "total": len(rows),
                    "by_source": by_source,
                }
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/knowledge/stats")
    def knowledge_stats() -> JSONResponse:
        """知识库统计数据。"""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            conn = database.conn
            total_concepts = conn.execute(
                "SELECT COUNT(DISTINCT concept) FROM knowledge_concepts"
            ).fetchone()[0]
            total_backlinks = conn.execute("SELECT COUNT(*) FROM knowledge_backlinks").fetchone()[0]

            sources = conn.execute(
                "SELECT source_site, COUNT(*) as cnt FROM knowledge_backlinks "
                "GROUP BY source_site ORDER BY cnt DESC"
            ).fetchall()
            source_stats = {r[0]: r[1] for r in sources}

            return JSONResponse(
                {
                    "ok": True,
                    "total_concepts": total_concepts,
                    "total_backlinks": total_backlinks,
                    "sources": source_stats,
                }
            )
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.get("/api/knowledge/graph")
    def knowledge_graph(limit: int = 50) -> JSONResponse:
        """知识图谱数据（节点 + 边），用于可视化。"""
        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            conn = database.conn
            # 取 TOP 概念作为节点
            nodes_raw = conn.execute(
                "SELECT concept, concept_type, source_site, COUNT(*) as w "
                "FROM knowledge_concepts "
                "GROUP BY concept "
                "ORDER BY w DESC LIMIT ?",
                (limit,),
            ).fetchall()
            nodes = [
                {"id": r[0], "type": r[1] or "concept", "source": r[2], "weight": r[3]}
                for r in nodes_raw
            ]

            node_names = [n["id"] for n in nodes]

            # 取边：同一篇文章中同时出现的概念对
            if not node_names:
                return JSONResponse({"ok": True, "nodes": [], "edges": []})

            # 从 backlinks 构建边
            edges_raw = conn.execute(
                """SELECT kb1.target_concept as c1, kb2.target_concept as c2, COUNT(*) as w
                FROM knowledge_backlinks kb1
                JOIN knowledge_backlinks kb2 ON kb1.source_article_id = kb2.source_article_id
                    AND kb1.target_concept < kb2.target_concept
                WHERE kb1.target_concept IN ({}) AND kb2.target_concept IN ({})
                GROUP BY c1, c2
                ORDER BY w DESC
                LIMIT 200""".format(
                    ",".join("?" * len(node_names)), ",".join("?" * len(node_names))
                ),
                node_names + node_names,
            ).fetchall()
            edges = [{"source": r[0], "target": r[1], "weight": r[2]} for r in edges_raw]

            return JSONResponse({"ok": True, "nodes": nodes, "edges": edges})
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    @app.post("/api/reading/auto-tag")
    def reading_auto_tag(
        limit: int = 500,
        status: str | None = None,
        only_sparse: bool = True,
        max_new: int = 5,
        min_weight: float = 0.15,
    ) -> JSONResponse:
        """给阅读库补打轻量兴趣标签（确定性规则：画像关键词 + ``#话题``）。

        命中即 merge 进现有 ``tags``（保留来源标签、大小写去重、幂等），
        零 LLM、零网络。冷画像（无兴趣词）时直接跳过，不臆造标签。
        """
        import json as _json

        from openbiliclaw.reading.tags import generate_tags, merge_tag_lists

        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        keywords = _load_interest_keywords()
        if not keywords:
            return JSONResponse(
                {
                    "ok": True,
                    "scanned": 0,
                    "updated": 0,
                    "added": 0,
                    "note": "no interest profile yet; skipped",
                },
                status_code=200,
            )
        rows = database.iter_articles_for_tagging(
            limit=limit, status=status, only_sparse=only_sparse
        )
        updated = 0
        added_total = 0
        for row in rows:
            try:
                existing = _json.loads(row.get("tags") or "[]")
            except Exception:
                existing = []
            if not isinstance(existing, list):
                existing = []
            new_tags = generate_tags(
                title=str(row.get("title") or ""),
                summary=str(row.get("summary") or ""),
                content_text=str(row.get("content_text") or ""),
                interest_keywords=keywords,
                existing=[str(t) for t in existing],
                max_new=max_new,
                min_weight=min_weight,
            )
            if not new_tags:
                continue
            merged = merge_tag_lists([str(t) for t in existing], new_tags)
            try:
                if database.update_article_tags(int(row["id"]), merged):
                    updated += 1
                    added_total += len(new_tags)
            except Exception:
                logger.exception("auto-tag write failed for article id=%s", row.get("id"))
        return JSONResponse(
            {"ok": True, "scanned": len(rows), "updated": updated, "added": added_total}
        )

    @app.get("/api/reading/similar")
    def reading_similar(id: int, k: int = 8, candidate_limit: int = 500) -> JSONResponse:
        """库内找相似：按 tag + 标题 bigram 的确定性相似度（零 LLM / 零网络）。"""
        from openbiliclaw.reading.tags import similarity

        database = getattr(ctx, "database", None)
        if database is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        target = database.get_article(id)
        if not target:
            return JSONResponse({"ok": False, "error": "article not found"}, status_code=404)
        k = max(1, min(int(k), 30))
        cands = database.get_recent_articles(limit=max(1, min(int(candidate_limit), 1000)))
        t_title = str(target.get("title") or "")
        t_tags = target.get("tags")
        scored: list[tuple[float, dict[str, Any]]] = []
        for c in cands:
            if str(c.get("id")) == str(id):
                continue
            if str(c.get("status") or "") == "hidden":
                continue
            s = similarity(
                t_title,
                t_tags,
                str(c.get("title") or ""),
                c.get("tags"),
            )
            if s > 0:
                scored.append((s, c))
        scored.sort(key=lambda kv: kv[0], reverse=True)
        items = [
            {
                "id": c.get("id"),
                "title": c.get("title"),
                "url": c.get("url"),
                "source_type": c.get("source_type"),
                "tags": c.get("tags"),
                "similarity": round(s, 3),
            }
            for s, c in scored[:k]
        ]
        return JSONResponse({"ok": True, "target_id": id, "items": items})

    # ── 日记系统 API ─────────────────────────────────────────────

    _diary_service: DiaryService | None = None

    def _get_diary_service() -> DiaryService | None:
        """获取或创建日记服务实例（懒加载）。"""
        nonlocal _diary_service
        if _diary_service is not None:
            return _diary_service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        llm_service = getattr(ctx, "llm_service", None)
        _diary_service = DiaryService(database=database, llm_service=llm_service)
        return _diary_service

    @app.get("/api/diary")
    def diary_list(
        limit: int = 50,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        mood: str | None = None,
        source: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        sort_by: str = "entry_date",
        sort_order: str = "DESC",
    ) -> JSONResponse:
        """列出日记，支持多条件筛选与搜索。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        mood_enum = MoodLevel(mood) if mood else None
        entries, total = svc.list_entries(
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            start_date=start_date,
            end_date=end_date,
            mood=mood_enum,
            source=source,
            tag=tag,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [e.model_dump(mode="json") for e in entries],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/diary/stats")
    def diary_stats() -> JSONResponse:
        """获取日记统计信息。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = svc.get_stats()
        return JSONResponse({"ok": True, "stats": stats.model_dump(mode="json")})

    @app.get("/api/diary/timeline")
    def diary_timeline(year: int | None = None, month: int | None = None) -> JSONResponse:
        """获取日记时间线视图。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        entries = svc.get_timeline(year=year, month=month)
        return JSONResponse(
            {
                "ok": True,
                "items": [e.model_dump(mode="json") for e in entries],
                "year": year,
                "month": month,
            }
        )

    @app.get("/api/diary/search")
    def diary_search(q: str, limit: int = 50) -> JSONResponse:
        """全文搜索日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        entries = svc.search_entries(q, limit=max(1, min(int(limit), 200)))
        return JSONResponse(
            {
                "ok": True,
                "query": q,
                "items": [e.model_dump(mode="json") for e in entries],
                "total": len(entries),
            }
        )

    @app.get("/api/diary/{entry_id:int}")
    def diary_get(entry_id: int) -> JSONResponse:
        """获取单篇日记详情（含分析结果）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            entry = svc.get_entry(entry_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        analysis = svc.get_analysis(entry_id)
        return JSONResponse(
            {
                "ok": True,
                "entry": entry.model_dump(mode="json"),
                "analysis": analysis.model_dump(mode="json") if analysis else None,
            }
        )

    @app.post("/api/diary")
    def diary_create(payload: dict[str, Any]) -> JSONResponse:
        """创建一篇日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = DiaryEntryCreate(**payload)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"invalid payload: {exc}"}, status_code=400)
        entry = svc.create_entry(data)
        return JSONResponse({"ok": True, "entry": entry.model_dump(mode="json")}, status_code=201)

    @app.put("/api/diary/{entry_id}")
    def diary_update(entry_id: int, payload: dict[str, Any]) -> JSONResponse:
        """更新日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = DiaryEntryUpdate(**payload)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"invalid payload: {exc}"}, status_code=400)
        try:
            entry = svc.update_entry(entry_id, data)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        return JSONResponse({"ok": True, "entry": entry.model_dump(mode="json")})

    @app.delete("/api/diary/{entry_id}")
    def diary_delete(entry_id: int) -> JSONResponse:
        """删除日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_entry(entry_id)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        return JSONResponse({"ok": True, "deleted": entry_id})

    @app.get("/api/diary/{entry_id}/analysis")
    def diary_get_analysis(entry_id: int) -> JSONResponse:
        """获取日记的分析结果。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        analysis = svc.get_analysis(entry_id)
        if analysis is None:
            return JSONResponse({"ok": True, "analysis": None, "message": "未分析"})
        return JSONResponse({"ok": True, "analysis": analysis.model_dump(mode="json")})

    @app.post("/api/diary/{entry_id}/analyze")
    async def diary_analyze(entry_id: int, force: bool = False) -> JSONResponse:
        """分析单篇日记（调用 LLM）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if svc.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM service 未配置"}, status_code=503)
        try:
            analysis = await svc.analyze_entry(entry_id, force=force)
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        if analysis is None:
            return JSONResponse({"ok": False, "error": "分析失败"}, status_code=500)
        return JSONResponse({"ok": True, "analysis": analysis.model_dump(mode="json")})

    @app.post("/api/diary/analyze-batch")
    async def diary_analyze_batch(limit: int = 50, concurrency: int = 3) -> JSONResponse:
        """批量分析所有未分析的日记。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if svc.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM service 未配置"}, status_code=503)
        results = await svc.analyze_unanalyzed(
            limit=max(1, min(int(limit), 200)), concurrency=max(1, min(int(concurrency), 10))
        )
        success = sum(1 for v in results.values() if v is not None)
        return JSONResponse(
            {
                "ok": True,
                "total": len(results),
                "success": success,
                "failed": len(results) - success,
                "results": {
                    str(k): (v.model_dump(mode="json") if v else None) for k, v in results.items()
                },
            }
        )

    @app.post("/api/diary/import")
    def diary_import(payload: dict[str, Any]) -> JSONResponse:
        """导入日记文件。

        请求体：
        - file_path: 本地文件路径
        - format: lele / text / markdown（默认自动检测）
        - source: 来源标识
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        file_path = payload.get("file_path", "")
        if not file_path:
            return JSONResponse({"ok": False, "error": "缺少 file_path"}, status_code=400)
        fmt = payload.get("format", "auto")
        source = payload.get("source", "")
        importer = DiaryImporter(svc)
        try:
            if fmt == "lele" or (fmt == "auto" and "lele" in file_path.lower()):
                count, entries = importer.import_lele_diary(
                    file_path, source=source or "import_lele"
                )
            elif fmt == "markdown" or (fmt == "auto" and file_path.lower().endswith(".md")):
                count, entries = importer.import_markdown_file(
                    file_path, source=source or "import_markdown"
                )
            else:
                count, entries = importer.import_text_file(
                    file_path, source=source or "import_text"
                )
        except FileNotFoundError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            logger.exception("日记导入失败")
            return JSONResponse({"ok": False, "error": f"导入失败: {exc}"}, status_code=500)
        return JSONResponse(
            {
                "ok": True,
                "imported": count,
                "entries": [e.model_dump(mode="json") for e in entries],
                "format": fmt,
                "file_path": file_path,
            }
        )

    # ─── 日记数据洞察 API ───────────────────────────────────────────

    @app.get("/api/diary/insights/mood-trend")
    def diary_insights_mood_trend(
        granularity: str = "month",
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JSONResponse:
        """获取情绪趋势数据。

        Args:
            granularity: month / year
            start_date: 起始日期 YYYY-MM-DD
            end_date: 结束日期 YYYY-MM-DD

        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        trend = insights.get_mood_trend(granularity, start_date, end_date)
        return JSONResponse(
            {
                "ok": True,
                "granularity": granularity,
                "data": [
                    {
                        "period": p.period,
                        "avg_score": p.avg_score,
                        "entry_count": p.entry_count,
                        "mood_distribution": p.mood_distribution,
                    }
                    for p in trend
                ],
            }
        )

    @app.get("/api/diary/insights/streak")
    def diary_insights_streak() -> JSONResponse:
        """获取写作连续打卡统计。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        streak = insights.get_writing_streak()
        return JSONResponse(
            {
                "ok": True,
                "current_streak": streak.current_streak,
                "longest_streak": streak.longest_streak,
                "total_days": streak.total_days,
                "this_week_count": streak.this_week_count,
                "this_month_count": streak.this_month_count,
            }
        )

    @app.get("/api/diary/insights/word-trend")
    def diary_insights_word_trend(granularity: str = "month") -> JSONResponse:
        """获取字数趋势数据。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        trend = insights.get_word_trend(granularity)
        return JSONResponse({"ok": True, "granularity": granularity, "data": trend})

    @app.get("/api/diary/insights/keywords")
    def diary_insights_keywords(
        limit: int = 50,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JSONResponse:
        """获取高频关键词（词云数据）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        keywords = insights.get_top_keywords(limit, start_date, end_date)
        return JSONResponse(
            {
                "ok": True,
                "data": [{"word": w, "count": c} for w, c in keywords],
            }
        )

    @app.get("/api/diary/insights/yearly/{year}")
    def diary_insights_yearly(year: int) -> JSONResponse:
        """获取年度洞察统计数据。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        stats = insights.get_yearly_insight_stats(year)
        return JSONResponse({"ok": True, "year": year, "data": stats})

    @app.post("/api/diary/insights/yearly/{year}/generate")
    async def diary_insights_yearly_generate(year: int) -> JSONResponse:
        """生成年度洞察报告（调用 LLM）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import DiaryInsightsService

        insights = DiaryInsightsService(svc.store)
        stats = insights.get_yearly_insight_stats(year)
        if stats.get("entry_count", 0) == 0:
            return JSONResponse({"ok": False, "error": f"{year}年暂无日记"}, status_code=404)

        prompt = DiaryInsightsService.build_yearly_report_prompt(year, stats)
        try:
            report = await svc._call_llm(prompt)  # noqa: SLF001
            return JSONResponse({"ok": True, "year": year, "report": report, "stats": stats})
        except Exception as exc:
            logger.exception("年度洞察报告生成失败")
            return JSONResponse(
                {"ok": False, "error": f"生成失败: {exc}", "stats": stats},
                status_code=500,
            )

    # ─── 日记反思 API（周报/月度反思/年度回顾/里程碑） ─────────────────

    @app.get("/api/diary/reflection/weekly")
    def diary_reflection_weekly(week_start: str | None = None) -> JSONResponse:
        """获取周报统计数据。

        Query:
        - week_start: 周开始日期（YYYY-MM-DD），默认本周一
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        report = reflection.generate_weekly_report(week_start)
        return JSONResponse({"ok": True, "data": report.__dict__})

    @app.post("/api/diary/reflection/weekly/generate")
    async def diary_reflection_weekly_generate(
        payload: dict[str, Any] | None = None,
    ) -> JSONResponse:
        """生成 AI 周报。

        请求体（可选）：
        - week_start: 周开始日期（YYYY-MM-DD），默认本周一
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        payload = payload or {}
        reflection = ReflectionService(svc.store)
        report = reflection.generate_weekly_report(payload.get("week_start"))
        if report.entry_count == 0:
            return JSONResponse({"ok": False, "error": "本周暂无日记"}, status_code=404)

        prompt = reflection.build_weekly_report_prompt(report)
        try:
            ai_result = await svc._call_llm(prompt)  # noqa: SLF001
            return JSONResponse(
                {
                    "ok": True,
                    "data": report.__dict__,
                    "ai_result": ai_result,
                }
            )
        except Exception as exc:
            logger.exception("周报生成失败")
            return JSONResponse(
                {"ok": False, "error": f"生成失败: {exc}", "data": report.__dict__},
                status_code=500,
            )

    @app.get("/api/diary/reflection/monthly/{year}/{month}")
    def diary_reflection_monthly(year: int, month: int) -> JSONResponse:
        """获取月度反思统计数据。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        result = reflection.generate_monthly_reflection(year, month)
        return JSONResponse({"ok": True, "data": result.__dict__})

    @app.post("/api/diary/reflection/monthly/{year}/{month}/generate")
    async def diary_reflection_monthly_generate(year: int, month: int) -> JSONResponse:
        """生成 AI 月度反思。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        result = reflection.generate_monthly_reflection(year, month)
        if result.entry_count == 0:
            return JSONResponse(
                {"ok": False, "error": f"{year}年{month}月暂无日记"}, status_code=404
            )

        prompt = reflection.build_monthly_reflection_prompt(result)
        try:
            ai_result = await svc._call_llm(prompt)  # noqa: SLF001
            return JSONResponse(
                {
                    "ok": True,
                    "data": result.__dict__,
                    "ai_result": ai_result,
                }
            )
        except Exception as exc:
            logger.exception("月度反思生成失败")
            return JSONResponse(
                {"ok": False, "error": f"生成失败: {exc}", "data": result.__dict__},
                status_code=500,
            )

    @app.get("/api/diary/reflection/yearly/{year}")
    def diary_reflection_yearly(year: int) -> JSONResponse:
        """获取年度回顾统计数据。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        result = reflection.generate_yearly_review(year)
        return JSONResponse({"ok": True, "data": result.__dict__})

    @app.post("/api/diary/reflection/yearly/{year}/generate")
    async def diary_reflection_yearly_generate(year: int) -> JSONResponse:
        """生成 AI 年度回顾。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        result = reflection.generate_yearly_review(year)
        if result.entry_count == 0:
            return JSONResponse({"ok": False, "error": f"{year}年暂无日记"}, status_code=404)

        prompt = reflection.build_yearly_review_prompt(result)
        try:
            ai_result = await svc._call_llm(prompt)  # noqa: SLF001
            return JSONResponse(
                {
                    "ok": True,
                    "data": result.__dict__,
                    "ai_result": ai_result,
                }
            )
        except Exception as exc:
            logger.exception("年度回顾生成失败")
            return JSONResponse(
                {"ok": False, "error": f"生成失败: {exc}", "data": result.__dict__},
                status_code=500,
            )

    @app.get("/api/diary/reflection/milestones")
    def diary_reflection_milestones(
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        """获取人生里程碑列表。

        Query:
        - start_date: 开始日期（YYYY-MM-DD），默认 2000-01-01
        - end_date: 结束日期（YYYY-MM-DD），默认今天
        - limit: 返回数量上限，默认 50
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import ReflectionService

        reflection = ReflectionService(svc.store)
        milestones = reflection.detect_milestones(start_date, end_date)
        milestones = milestones[:limit]
        return JSONResponse(
            {
                "ok": True,
                "data": [m.__dict__ for m in milestones],
                "total": len(milestones),
            }
        )

    # ─── 知识图谱 API（标签关联+人物关系+知识网络） ─────────────────

    @app.get("/api/diary/knowledge-graph/tag-network")
    def diary_kg_tag_network(
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 2,
        max_nodes: int = 50,
    ) -> JSONResponse:
        """获取标签关联网络。

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        - min_count: 最小出现次数，默认 2
        - max_nodes: 最大节点数，默认 50
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        graph = kg.build_tag_network(start_date, end_date, min_count, max_nodes)
        return JSONResponse({"ok": True, "data": graph.to_dict()})

    @app.get("/api/diary/knowledge-graph/person-network")
    def diary_kg_person_network(
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 1,
        max_nodes: int = 30,
    ) -> JSONResponse:
        """获取人物关系图谱。

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        - min_count: 最小出现次数，默认 1
        - max_nodes: 最大节点数，默认 30
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        graph = kg.build_person_network(start_date, end_date, min_count, max_nodes)
        return JSONResponse({"ok": True, "data": graph.to_dict()})

    @app.get("/api/diary/knowledge-graph/mixed")
    def diary_kg_mixed(
        start_date: str | None = None,
        end_date: str | None = None,
        min_count: int = 2,
        max_nodes: int = 60,
    ) -> JSONResponse:
        """获取混合知识网络（标签 + 人物 + 标签-人物关联）。

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        - min_count: 最小出现次数，默认 2
        - max_nodes: 最大节点数，默认 60
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        graph = kg.build_mixed_network(start_date, end_date, min_count, max_nodes)
        return JSONResponse({"ok": True, "data": graph.to_dict()})

    @app.get("/api/diary/knowledge-graph/stats")
    def diary_kg_stats(
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JSONResponse:
        """获取知识网络统计信息。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        stats = kg.get_network_stats(start_date, end_date)
        return JSONResponse({"ok": True, "data": stats})

    @app.get("/api/diary/knowledge-graph/node/{node_id}")
    def diary_kg_node_detail(
        node_id: str,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 20,
    ) -> JSONResponse:
        """获取知识节点详情。

        Path:
        - node_id: 节点 ID（格式：tag:xxx 或 person:xxx）

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        - limit: 相关日记数量上限，默认 20
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        detail = kg.get_node_detail(node_id, start_date, end_date, limit)
        if detail is None:
            return JSONResponse({"ok": False, "error": "节点不存在或无相关日记"}, status_code=404)
        return JSONResponse({"ok": True, "data": detail.__dict__})

    @app.get("/api/diary/knowledge-graph/person/{person_name}/relations")
    def diary_kg_person_relations(
        person_name: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> JSONResponse:
        """分析某个人物与其他人物的关系。

        Path:
        - person_name: 人物名称

        Query:
        - start_date: 开始日期
        - end_date: 结束日期
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import KnowledgeGraphService

        kg = KnowledgeGraphService(svc.store)
        relations = kg.analyze_person_relations(person_name, start_date, end_date)
        return JSONResponse(
            {
                "ok": True,
                "data": [r.__dict__ for r in relations],
                "total": len(relations),
            }
        )

    # ─── 自进化 API（夜间自我改进循环） ─────────────────────────────

    @app.get("/api/diary/self-evolution/profile")
    def diary_se_profile(
        target_date: str | None = None,
    ) -> JSONResponse:
        """获取用户画像。

        Query:
        - target_date: 目标日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        profile = se.get_user_profile(target_date)
        if profile is None:
            return JSONResponse(
                {"ok": False, "error": "用户画像不存在，请先运行夜间循环"}, status_code=404
            )
        return JSONResponse({"ok": True, "data": profile.to_dict()})

    @app.get("/api/diary/self-evolution/profile/history")
    def diary_se_profile_history(
        limit: int = 30,
    ) -> JSONResponse:
        """获取画像历史快照。

        Query:
        - limit: 返回数量上限，默认 30
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        history = se.get_profile_history(limit)
        return JSONResponse({"ok": True, "data": history, "total": len(history)})

    @app.get("/api/diary/self-evolution/drifts")
    def diary_se_drifts(
        drift_type: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        """获取漂移事件列表。

        Query:
        - drift_type: 按类型筛选（behavior/emotion/focus/relationship/writing）
        - severity: 按严重程度筛选（info/warning/alert）
        - status: 按状态筛选（new/acknowledged/dismissed）
        - limit: 返回数量上限，默认 50
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        drifts = se.get_drifts(drift_type, severity, status, limit)
        return JSONResponse(
            {
                "ok": True,
                "data": [d.to_dict() for d in drifts],
                "total": len(drifts),
            }
        )

    @app.get("/api/diary/self-evolution/nightly-logs")
    def diary_se_nightly_logs(
        limit: int = 30,
    ) -> JSONResponse:
        """获取夜间日志列表。

        Query:
        - limit: 返回数量上限，默认 30
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        logs = se.get_nightly_logs(limit)
        return JSONResponse({"ok": True, "data": logs, "total": len(logs)})

    @app.get("/api/diary/self-evolution/nightly-logs/{log_date}")
    def diary_se_nightly_log_detail(
        log_date: str,
    ) -> JSONResponse:
        """获取指定日期的夜间日志详情。

        Path:
        - log_date: 日志日期（YYYY-MM-DD）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        log = se.get_nightly_log(log_date)
        if log is None:
            return JSONResponse({"ok": False, "error": "夜间日志不存在"}, status_code=404)
        return JSONResponse({"ok": True, "data": log.to_dict()})

    @app.post("/api/diary/self-evolution/run-nightly")
    def diary_se_run_nightly(
        target_date: str | None = None,
    ) -> JSONResponse:
        """手动触发夜间自我改进循环。

        Query:
        - target_date: 目标日期，默认昨天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        nightly_log = se.run_nightly_cycle(target_date)
        return JSONResponse(
            {
                "ok": True,
                "message": "夜间循环完成",
                "data": nightly_log.to_dict(),
            }
        )

    @app.get("/api/diary/self-evolution/tag-optimizations")
    def diary_se_tag_optimizations(
        target_date: str | None = None,
    ) -> JSONResponse:
        """获取标签优化建议。

        Query:
        - target_date: 目标日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from dataclasses import asdict

        from openbiliclaw.diary import SelfEvolutionService

        se = SelfEvolutionService(svc.store)
        if target_date is None:
            target_date = datetime.now().strftime("%Y-%m-%d")
        optimization = se.optimize_tags(target_date)
        return JSONResponse({"ok": True, "data": asdict(optimization)})

    # ─── 主动洞察引擎 API（第二阶段） ────────────────────────────────

    @app.get("/api/diary/insights/memory-on-this-day")
    def diary_insights_memory_on_this_day(
        target_date: str | None = None,
    ) -> JSONResponse:
        """获取历史上的今天。

        Query:
        - target_date: 目标日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        memory = engine.get_memory_on_this_day(target_date)
        return JSONResponse({"ok": True, "data": asdict(memory)})

    @app.get("/api/diary/insights/patterns")
    def diary_insights_patterns(
        lookback_days: int = 90,
    ) -> JSONResponse:
        """发现日记中的模式。

        Query:
        - lookback_days: 回溯天数，默认 90
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        patterns = engine.discover_patterns(lookback_days)
        return JSONResponse(
            {
                "ok": True,
                "data": [asdict(p) for p in patterns],
                "total": len(patterns),
            }
        )

    @app.get("/api/diary/insights/morning-briefing")
    def diary_insights_morning_briefing(
        briefing_date: str | None = None,
    ) -> JSONResponse:
        """获取晨间简报。

        Query:
        - briefing_date: 简报日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        briefing = engine.get_morning_briefing(briefing_date)
        if briefing is None:
            # 如果不存在，生成一个
            briefing = engine.generate_morning_briefing(briefing_date)
        return JSONResponse({"ok": True, "data": briefing.to_dict()})

    @app.post("/api/diary/insights/morning-briefing/generate")
    def diary_insights_generate_morning_briefing(
        briefing_date: str | None = None,
    ) -> JSONResponse:
        """生成晨间简报。

        Query:
        - briefing_date: 简报日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        briefing = engine.generate_morning_briefing(briefing_date)
        return JSONResponse({"ok": True, "data": briefing.to_dict()})

    @app.get("/api/diary/insights/open-loops")
    def diary_insights_open_loops(
        status: str | None = None,
        loop_type: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        """获取开放循环列表。

        Query:
        - status: 状态筛选（open/in_progress/completed/abandoned）
        - loop_type: 类型筛选（promise/goal/todo/question/idea）
        - priority: 优先级筛选（high/medium/low）
        - limit: 返回数量上限，默认 50
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        loops = engine.get_open_loops(status, loop_type, priority, limit)
        return JSONResponse(
            {
                "ok": True,
                "data": [l.to_dict() for l in loops],
                "total": len(loops),
            }
        )

    @app.post("/api/diary/insights/open-loops/scan")
    def diary_insights_scan_open_loops(
        lookback_days: int = 365,
    ) -> JSONResponse:
        """扫描日记中的开放循环。

        Query:
        - lookback_days: 回溯天数，默认 365
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        loops = engine.scan_open_loops(lookback_days)
        return JSONResponse(
            {
                "ok": True,
                "data": [l.to_dict() for l in loops],
                "total": len(loops),
                "message": f"扫描完成，发现 {len(loops)} 个开放循环",
            }
        )

    @app.put("/api/diary/insights/open-loops/{loop_id}/status")
    def diary_insights_update_open_loop_status(
        loop_id: str,
        status: str,
    ) -> JSONResponse:
        """更新开放循环状态。

        Path:
        - loop_id: 循环 ID

        Query:
        - status: 新状态（open/in_progress/completed/abandoned）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        success = engine.update_open_loop_status(loop_id, status)
        if success:
            return JSONResponse({"ok": True, "message": "状态更新成功"})
        return JSONResponse({"ok": False, "error": "状态更新失败"}, status_code=400)

    @app.get("/api/diary/insights/report")
    def diary_insights_report(
        target_date: str | None = None,
    ) -> JSONResponse:
        """生成综合洞察报告。

        Query:
        - target_date: 目标日期，默认今天
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import InsightEngineService

        engine = InsightEngineService(svc.store)
        report = engine.generate_insight_report(target_date)
        return JSONResponse({"ok": True, "data": report.to_dict()})

    # ─── 三层记忆系统 API（第三阶段） ────────────────────────────────

    @app.get("/api/diary/memory/stats")
    def diary_memory_stats() -> JSONResponse:
        """获取记忆系统统计。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        stats = memory.get_memory_stats()
        return JSONResponse({"ok": True, "data": stats.to_dict()})

    @app.post("/api/diary/memory/update-tiers")
    def diary_memory_update_tiers() -> JSONResponse:
        """更新所有日记的记忆层级。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        updated = memory.update_memory_tiers()
        return JSONResponse(
            {"ok": True, "updated": updated, "message": f"更新了 {updated} 个记忆条目的层级"}
        )

    @app.post("/api/diary/memory/compress-cold")
    def diary_memory_compress_cold() -> JSONResponse:
        """压缩所有 Cold 层记忆。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        result = memory.compress_all_cold_memories()
        return JSONResponse({"ok": True, "data": result.to_dict()})

    @app.post("/api/diary/memory/maintenance")
    def diary_memory_maintenance() -> JSONResponse:
        """运行记忆维护（分层更新 + 压缩 + 重要性重算）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        result = memory.run_memory_maintenance()
        return JSONResponse({"ok": True, "data": result.to_dict()})

    @app.get("/api/diary/memory/search")
    def diary_memory_search(
        query: str = "",
        tier: str | None = None,
        limit: int = 20,
        min_importance: float = 0.0,
    ) -> JSONResponse:
        """搜索记忆。

        Query:
        - query: 搜索关键词
        - tier: 记忆层级过滤（hot/warm/cold）
        - limit: 返回数量上限，默认 20
        - min_importance: 最低重要性评分，默认 0
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        results = memory.search_memories(query, tier, limit, min_importance)
        return JSONResponse(
            {
                "ok": True,
                "data": results,
                "total": len(results),
            }
        )

    @app.get("/api/diary/memory/{diary_id}")
    def diary_memory_get_by_id(diary_id: int) -> JSONResponse:
        """根据 ID 获取记忆条目。

        Path:
        - diary_id: 日记 ID
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import MemorySystemService

        memory = MemorySystemService(svc.store)
        entry = memory.get_memory_by_id(diary_id)
        if entry is None:
            return JSONResponse({"ok": False, "error": "记忆条目不存在"}, status_code=404)
        return JSONResponse({"ok": True, "data": entry.to_dict()})

    # ─── 情绪系统（效价/唤醒二维模型）API ────────────────────────────

    @app.get("/api/diary/emotion/stats")
    def diary_emotion_stats() -> JSONResponse:
        """获取情绪统计概览。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        stats = analyzer.get_emotion_stats()
        return JSONResponse({"ok": True, "data": stats})

    @app.get("/api/diary/emotion/trend")
    def diary_emotion_trend(days: int = 30) -> JSONResponse:
        """获取情绪趋势（按天聚合）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        trend = analyzer.get_emotion_trend(days=days)
        return JSONResponse({"ok": True, "data": [t.to_dict() for t in trend]})

    @app.get("/api/diary/emotion/forecast")
    def diary_emotion_forecast(days: int = 7) -> JSONResponse:
        """预测未来 N 天的情绪。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        forecasts = analyzer.forecast_emotion(days=days)
        return JSONResponse({"ok": True, "data": [f.to_dict() for f in forecasts]})

    @app.get("/api/diary/emotion/burnout")
    def diary_emotion_burnout(days: int = 30) -> JSONResponse:
        """倦怠评估。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        assessment = analyzer.assess_burnout(days=days)
        return JSONResponse({"ok": True, "data": assessment.to_dict()})

    @app.post("/api/diary/emotion/analyze-all")
    def diary_emotion_analyze_all() -> JSONResponse:
        """分析所有日记的情绪。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import EmotionAnalyzer

        analyzer = EmotionAnalyzer(svc.store)
        count = analyzer.analyze_all_diaries()
        return JSONResponse({"ok": True, "data": {"analyzed": count}})

    # ─── 高级记忆系统（6层记忆 + 信念 + 巩固）API ───────────────────

    @app.get("/api/diary/advanced-memory/overview")
    def diary_advanced_memory_overview() -> JSONResponse:
        """获取高级记忆系统概览。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        overview = am.get_overview()
        return JSONResponse({"ok": True, "data": overview})

    @app.get("/api/diary/advanced-memory/layers")
    def diary_advanced_memory_layers() -> JSONResponse:
        """获取各记忆层统计。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        stats = am.get_memory_layer_stats()
        return JSONResponse({"ok": True, "data": [s.to_dict() for s in stats]})

    @app.get("/api/diary/advanced-memory/beliefs")
    def diary_advanced_memory_beliefs(category: str | None = None) -> JSONResponse:
        """获取信念列表。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        beliefs = am.get_beliefs(category=category)
        return JSONResponse({"ok": True, "data": [b.to_dict() for b in beliefs]})

    @app.get("/api/diary/advanced-memory/conflicts")
    def diary_advanced_memory_conflicts() -> JSONResponse:
        """检测并获取信念冲突。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        conflicts = am.detect_belief_conflicts()
        return JSONResponse({"ok": True, "data": [c.to_dict() for c in conflicts]})

    @app.post("/api/diary/advanced-memory/build")
    def diary_advanced_memory_build() -> JSONResponse:
        """从日记构建 6 层记忆。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        result = am.build_memory_layers()
        return JSONResponse({"ok": True, "data": result})

    @app.post("/api/diary/advanced-memory/consolidate")
    def diary_advanced_memory_consolidate() -> JSONResponse:
        """运行记忆巩固（遗忘曲线 + 提升/降级/遗忘）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        result = am.consolidate_memories()
        return JSONResponse({"ok": True, "data": result.to_dict()})

    @app.post("/api/diary/advanced-memory/dream-review")
    def diary_advanced_memory_dream_review() -> JSONResponse:
        """运行夜间梦境状态回顾（验证教训）。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        result = am.dream_state_review()
        return JSONResponse({"ok": True, "data": result.to_dict()})

    @app.get("/api/diary/advanced-memory/search")
    def diary_advanced_memory_search(
        query: str = "",
        layer: str | None = None,
        min_importance: float = 0.0,
        limit: int = 20,
    ) -> JSONResponse:
        """搜索记忆。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import AdvancedMemoryService

        am = AdvancedMemoryService(svc.store)
        results = am.search_memories(
            query=query, layer=layer, min_importance=min_importance, limit=limit
        )
        return JSONResponse({"ok": True, "data": results})

    # ─── 智能时间线卡片 API ───────────────────────────────────────────

    @app.get("/api/diary/timeline/cards")
    def diary_timeline_cards(
        card_type: str | None = None,
        entity: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        min_importance: float = 0.0,
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        """查询时间线卡片。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import TimelineService

        tl = TimelineService(svc.store)
        cards = tl.get_cards(
            card_type=card_type,
            entity=entity,
            start_date=start_date,
            end_date=end_date,
            min_importance=min_importance,
            limit=limit,
            offset=offset,
        )
        return JSONResponse({"ok": True, "data": [c.to_dict() for c in cards]})

    @app.get("/api/diary/timeline/stats")
    def diary_timeline_stats() -> JSONResponse:
        """获取时间线统计。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import TimelineService

        tl = TimelineService(svc.store)
        stats = tl.get_stats()
        return JSONResponse({"ok": True, "data": stats.to_dict()})

    @app.get("/api/diary/timeline/milestones")
    def diary_timeline_milestones(limit: int = 20) -> JSONResponse:
        """获取里程碑卡片。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import TimelineService

        tl = TimelineService(svc.store)
        milestones = tl.get_milestones(limit=limit)
        return JSONResponse({"ok": True, "data": [m.to_dict() for m in milestones]})

    @app.post("/api/diary/timeline/generate-all")
    def diary_timeline_generate_all() -> JSONResponse:
        """为所有日记生成时间线卡片。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        from openbiliclaw.diary import TimelineService

        tl = TimelineService(svc.store)
        total = tl.generate_all_cards()
        return JSONResponse({"ok": True, "data": {"total_cards": total}})

    # ─── 碎片（随手记）API ───────────────────────────────────────────

    @app.get("/api/diary/fragments")
    def diary_fragments_list(
        fragment_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
        fragment_type: str | None = None,
    ) -> JSONResponse:
        """列出碎片，可按日期和类型筛选。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        fragments = svc.list_fragments(fragment_date, limit, offset, fragment_type)
        total = svc.store.count_fragments(fragment_date)
        return JSONResponse(
            {
                "ok": True,
                "data": [f.model_dump(mode="json") for f in fragments],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.post("/api/diary/fragments")
    def diary_fragments_create(payload: dict[str, Any]) -> JSONResponse:
        """创建一条碎片。

        请求体：
        - content: 碎片内容（必填）
        - mood: 情绪标签（可选）
        - fragment_date: 日期（可选，默认今天）
        - source: 来源（可选）
        - fragment_type: 碎片类型（可选，text/image/voice/link）
        - media_path: 媒体文件路径（可选）
        - media_description: 媒体内容描述（可选）
        - tags: 标签列表（可选）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        content = payload.get("content", "").strip()
        if not content:
            return JSONResponse({"ok": False, "error": "缺少 content"}, status_code=400)
        mood_str = payload.get("mood", "unknown")
        try:
            from openbiliclaw.diary import MoodLevel

            mood = MoodLevel(mood_str)
        except (ValueError, KeyError):
            mood = MoodLevel.UNKNOWN
        fragment = svc.create_fragment(
            content=content,
            mood=mood,
            fragment_date=payload.get("fragment_date"),
            source=payload.get("source", "manual"),
            fragment_type=payload.get("fragment_type", "text"),
            media_path=payload.get("media_path", ""),
            media_description=payload.get("media_description", ""),
            tags=payload.get("tags", []),
        )
        return JSONResponse({"ok": True, "data": fragment.model_dump(mode="json")})

    @app.delete("/api/diary/fragments/{fragment_id}")
    def diary_fragments_delete(fragment_id: int) -> JSONResponse:
        """删除一条碎片。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = svc.delete_fragment(fragment_id)
        return JSONResponse({"ok": ok, "id": fragment_id})

    @app.post("/api/diary/fragments/{fragment_id}/auto-tag")
    async def diary_fragments_auto_tag(fragment_id: int) -> JSONResponse:
        """对单条碎片执行 AI 自动标签和情绪识别。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            fragment = await svc.auto_tag_fragment(fragment_id)
            if fragment is None:
                return JSONResponse({"ok": False, "error": "碎片不存在"}, status_code=404)
            return JSONResponse({"ok": True, "data": fragment.model_dump(mode="json")})
        except Exception as exc:
            logger.exception(f"碎片 {fragment_id} 自动标签失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/diary/fragments/auto-tag-batch")
    async def diary_fragments_auto_tag_batch(payload: dict[str, Any] | None = None) -> JSONResponse:
        """批量对未标注的碎片执行自动标签和情绪识别。

        请求体（可选）：
        - limit: 处理数量上限（默认 50）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        payload = payload or {}
        try:
            stats = await svc.batch_auto_tag_fragments(limit=payload.get("limit", 50))
            return JSONResponse({"ok": True, "data": stats})
        except Exception as exc:
            logger.exception("碎片批量自动标签失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/diary/fragments/generate-diary")
    async def diary_fragments_generate_diary(payload: dict[str, Any] | None = None) -> JSONResponse:
        """从当天碎片 AI 聚合生成一篇完整日记（证据驱动版）。

        请求体（可选）：
        - fragment_date: 碎片日期（默认今天）
        - auto_delete: 生成后是否删除碎片（默认 true）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        payload = payload or {}
        try:
            entry = await svc.generate_diary_from_fragments(
                fragment_date=payload.get("fragment_date"),
                auto_delete=payload.get("auto_delete", True),
            )
            if entry is None:
                return JSONResponse({"ok": False, "error": "当天没有碎片"}, status_code=404)
            return JSONResponse({"ok": True, "data": entry.model_dump(mode="json")})
        except Exception as exc:
            logger.exception("碎片生成日记失败")
            return JSONResponse({"ok": False, "error": f"生成失败: {exc}"}, status_code=500)

    # ── 日记标签与人物提取 API ────────────────────────────────

    @app.get("/api/diary/tags")
    def diary_tags_list(
        type: str | None = None,
        limit: int = 200,
        min_count: int = 1,
    ) -> JSONResponse:
        """获取标签列表，可按类型筛选。

        Query:
        - type: 标签类型（emotion/topic/event/location/work/family/health/finance/other）
        - limit: 返回数量上限
        - min_count: 最小使用次数
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            from openbiliclaw.diary.models import TagType

            tag_type = TagType(type) if type else None
            tags = svc.get_tags(tag_type=tag_type, limit=limit, min_count=min_count)
            return JSONResponse(
                {
                    "ok": True,
                    "data": [t.model_dump(mode="json") for t in tags],
                    "total": len(tags),
                }
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/diary/persons")
    def diary_persons_list(
        relation: str | None = None,
        limit: int = 200,
        min_appearances: int = 1,
    ) -> JSONResponse:
        """获取人物列表，可按关系筛选。

        Query:
        - relation: 关系筛选（家人/朋友/同事等）
        - limit: 返回数量上限
        - min_appearances: 最小出现次数
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            persons = svc.get_persons(
                relation=relation, limit=limit, min_appearances=min_appearances
            )
            return JSONResponse(
                {
                    "ok": True,
                    "data": [p.model_dump(mode="json") for p in persons],
                    "total": len(persons),
                }
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/diary/persons/{person_id}")
    def diary_persons_detail(person_id: int) -> JSONResponse:
        """获取人物详情，包含相关日记列表。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            detail = svc.get_person_detail(person_id)
            if detail is None:
                return JSONResponse({"ok": False, "error": "人物不存在"}, status_code=404)
            return JSONResponse({"ok": True, "data": detail})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/diary/{entry_id}/tags")
    def diary_entry_tags(entry_id: int) -> JSONResponse:
        """获取某篇日记的标签和人物。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = svc.get_entry_tags_and_persons(entry_id)
            return JSONResponse({"ok": True, "data": data})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.post("/api/diary/{entry_id}/extract")
    async def diary_entry_extract(entry_id: int) -> JSONResponse:
        """对单篇日记执行 AI 标签和人物提取。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            result = await svc.extract_tags_and_persons(entry_id)
            if result is None:
                return JSONResponse({"ok": False, "error": "提取失败或日记不存在"}, status_code=404)
            return JSONResponse({"ok": True, "data": result.model_dump(mode="json")})
        except Exception as exc:
            logger.exception(f"提取日记 {entry_id} 失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/diary/extract-batch")
    async def diary_extract_batch(payload: dict[str, Any] | None = None) -> JSONResponse:
        """批量提取日记的标签和人物。

        请求体（可选）：
        - limit: 处理数量上限（默认 100）
        - start_id: 起始日记 ID
        - only_unextracted: 只处理未提取过的日记（默认 true）
        """
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        payload = payload or {}
        try:
            stats = await svc.batch_extract(
                limit=payload.get("limit", 100),
                start_id=payload.get("start_id"),
                only_unextracted=payload.get("only_unextracted", True),
            )
            return JSONResponse({"ok": True, "data": stats})
        except Exception as exc:
            logger.exception("批量提取失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.get("/api/diary/extraction-stats")
    def diary_extraction_stats() -> JSONResponse:
        """获取标签和人物提取的统计信息。"""
        svc = _get_diary_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            stats = svc.get_extraction_stats()
            return JSONResponse({"ok": True, "data": stats})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    # ─── RAG 语义搜索与问答 API ─────────────────────────────────────

    _diary_rag_service = None

    def _get_diary_rag_service():
        """获取或创建日记 RAG 服务实例（懒加载）。"""
        nonlocal _diary_rag_service
        if _diary_rag_service is not None:
            return _diary_rag_service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        from openbiliclaw.diary import DiaryRAGService

        rag = DiaryRAGService(database=database)
        # 注入 embedding 和 llm 服务
        embedding_service = getattr(ctx, "embedding_service", None)
        llm_service = getattr(ctx, "llm_service", None)
        if embedding_service is not None:
            rag.set_embedding_service(embedding_service)
        if llm_service is not None:
            rag.set_llm_service(llm_service)
        _diary_rag_service = rag
        return rag

    @app.get("/api/diary/rag/stats")
    def diary_rag_stats() -> JSONResponse:
        """获取向量生成统计信息。"""
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = rag.get_embedding_stats()
        return JSONResponse({"ok": True, "data": stats})

    @app.post("/api/diary/rag/generate-embeddings")
    async def diary_rag_generate_embeddings(payload: dict[str, Any] | None = None) -> JSONResponse:
        """批量为日记生成 embedding 向量。

        请求体（可选）：
        - limit: 最多处理多少篇（默认 100）
        - batch_size: 每批并发数（默认 10）
        """
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if rag.embedding_service is None:
            return JSONResponse(
                {"ok": False, "error": "Embedding 服务未配置，请先配置 LLM provider"},
                status_code=400,
            )
        payload = payload or {}
        try:
            stats = await rag.batch_generate_embeddings(
                limit=payload.get("limit", 100),
                batch_size=payload.get("batch_size", 10),
            )
            return JSONResponse({"ok": True, "data": stats})
        except Exception as exc:
            logger.exception("批量生成 embedding 失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.get("/api/diary/rag/search")
    async def diary_rag_search(
        q: str,
        top_k: int = 10,
        min_score: float = 0.3,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> JSONResponse:
        """语义搜索日记（用自然语言搜索，按语义相似度排序）。

        参数：
        - q: 搜索查询（自然语言）
        - top_k: 返回最多多少条（默认 10）
        - min_score: 最低相似度阈值 0-1（默认 0.3）
        - start_date / end_date: 日期范围过滤
        - source: 来源过滤
        """
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if rag.embedding_service is None:
            return JSONResponse({"ok": False, "error": "Embedding 服务未配置"}, status_code=400)
        if not q.strip():
            return JSONResponse({"ok": False, "error": "缺少搜索关键词 q"}, status_code=400)
        try:
            results = await rag.semantic_search(
                query=q,
                top_k=top_k,
                min_score=min_score,
                start_date=start_date,
                end_date=end_date,
                source=source,
            )
            return JSONResponse(
                {
                    "ok": True,
                    "query": q,
                    "count": len(results),
                    "results": [
                        {
                            "id": r.entry.id,
                            "date": r.entry.entry_date,
                            "title": r.entry.title,
                            "content": r.entry.content[:500]
                            + ("..." if len(r.entry.content) > 500 else ""),
                            "source": r.entry.source,
                            "mood": r.entry.mood.value,
                            "score": round(r.score, 4),
                            "highlight": r.highlight,
                        }
                        for r in results
                    ],
                }
            )
        except Exception as exc:
            logger.exception("语义搜索失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.get("/api/diary/rag/similar/{entry_id}")
    def diary_rag_similar(entry_id: int, top_k: int = 5, min_score: float = 0.5) -> JSONResponse:
        """查找与指定日记相似的历史日记。

        参数：
        - entry_id: 目标日记 ID
        - top_k: 返回最多多少条（默认 5）
        - min_score: 最低相似度阈值（默认 0.5）
        """
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            results = rag.find_similar_entries(entry_id=entry_id, top_k=top_k, min_score=min_score)
            return JSONResponse(
                {
                    "ok": True,
                    "entry_id": entry_id,
                    "count": len(results),
                    "results": [
                        {
                            "id": r.entry.id,
                            "date": r.entry.entry_date,
                            "title": r.entry.title,
                            "content": r.entry.content[:300]
                            + ("..." if len(r.entry.content) > 300 else ""),
                            "score": round(r.score, 4),
                        }
                        for r in results
                    ],
                }
            )
        except Exception as exc:
            logger.exception("相似日记查询失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/diary/rag/ask")
    async def diary_rag_ask(payload: dict[str, Any]) -> JSONResponse:
        """基于日记内容回答问题（RAG 问答）。

        请求体：
        - question: 用户问题（必填）
        - top_k: 检索多少篇相关日记（默认 8）
        """
        rag = _get_diary_rag_service()
        if rag is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if rag.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM 服务未配置"}, status_code=400)
        question = payload.get("question", "").strip()
        if not question:
            return JSONResponse({"ok": False, "error": "缺少 question"}, status_code=400)
        try:
            answer = await rag.ask_question(
                question=question,
                top_k=payload.get("top_k", 8),
            )
            return JSONResponse(
                {
                    "ok": True,
                    "question": question,
                    "answer": answer.answer,
                    "sources": answer.sources,
                    "related_questions": answer.related_questions,
                }
            )
        except Exception as exc:
            logger.exception("RAG 问答失败")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    # ── 健康管理系统 API ───────────────────────────────────────

    _health_service: HealthService | None = None

    def _get_health_service() -> HealthService | None:
        """获取或创建健康管理服务实例（懒加载）。"""
        nonlocal _health_service
        if _health_service is not None:
            return _health_service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        llm_service = getattr(ctx, "llm_service", None)
        _health_service = HealthService(database=database, llm_service=llm_service)
        return _health_service

    # ── 统计概览 ──

    @app.get("/api/health/stats")
    def health_stats() -> JSONResponse:
        """获取健康档案统计概览。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = svc.get_stats()
        return JSONResponse({"ok": True, "stats": stats.model_dump(mode="json")})

    # ── 患者档案 ──

    @app.get("/api/health/patients")
    def health_patients_list() -> JSONResponse:
        """列出所有患者档案。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        patients = svc.list_patients()
        return JSONResponse({"ok": True, "items": [p.model_dump(mode="json") for p in patients]})

    @app.post("/api/health/patients")
    def health_patients_create(payload: dict[str, Any]) -> JSONResponse:
        """创建患者档案。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = PatientCreate(**payload)
            patient = svc.create_patient(data)
            return JSONResponse({"ok": True, "data": patient.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/patients/{patient_id}")
    def health_patients_get(patient_id: int) -> JSONResponse:
        """获取患者档案详情（含各模块统计）。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            summary = svc.get_patient_summary(patient_id)
            return JSONResponse({"ok": True, "data": summary})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/patients/{patient_id}")
    def health_patients_update(patient_id: int, payload: dict[str, Any]) -> JSONResponse:
        """更新患者档案。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = PatientUpdate(**payload)
            patient = svc.update_patient(patient_id, data)
            return JSONResponse({"ok": True, "data": patient.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/patients/{patient_id}")
    def health_patients_delete(patient_id: int) -> JSONResponse:
        """删除患者档案。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_patient(patient_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 就诊记录 ──

    @app.get("/api/health/encounters")
    def health_encounters_list(
        patient_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        encounter_type: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        search: str | None = None,
    ) -> JSONResponse:
        """列就诊记录，支持筛选与搜索。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_encounters(
            patient_id=patient_id,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            encounter_type=encounter_type,
            start_date=start_date,
            end_date=end_date,
            search=search,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [e.model_dump(mode="json") for e in items],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.post("/api/health/encounters")
    def health_encounters_create(payload: dict[str, Any]) -> JSONResponse:
        """创建就诊记录。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = EncounterCreate(**payload)
            encounter = svc.create_encounter(data)
            return JSONResponse({"ok": True, "data": encounter.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/encounters/{encounter_id}")
    def health_encounters_get(encounter_id: int) -> JSONResponse:
        """获取单条就诊记录详情。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            encounter = svc.get_encounter(encounter_id)
            return JSONResponse({"ok": True, "data": encounter.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/encounters/{encounter_id}")
    def health_encounters_update(encounter_id: int, payload: dict[str, Any]) -> JSONResponse:
        """更新就诊记录。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = EncounterUpdate(**payload)
            encounter = svc.update_encounter(encounter_id, data)
            return JSONResponse({"ok": True, "data": encounter.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/encounters/{encounter_id}")
    def health_encounters_delete(encounter_id: int) -> JSONResponse:
        """删除就诊记录。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_encounter(encounter_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 健康问题 ──

    @app.get("/api/health/conditions")
    def health_conditions_list(
        patient_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        """列出健康问题。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_conditions(
            patient_id=patient_id,
            status=status,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [c.model_dump(mode="json") for c in items],
                "total": total,
            }
        )

    @app.post("/api/health/conditions")
    def health_conditions_create(payload: dict[str, Any]) -> JSONResponse:
        """创建健康问题。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ConditionCreate(**payload)
            condition = svc.create_condition(data)
            return JSONResponse({"ok": True, "data": condition.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/conditions/{condition_id}")
    def health_conditions_get(condition_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            condition = svc.get_condition(condition_id)
            return JSONResponse({"ok": True, "data": condition.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/conditions/{condition_id}")
    def health_conditions_update(condition_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ConditionUpdate(**payload)
            condition = svc.update_condition(condition_id, data)
            return JSONResponse({"ok": True, "data": condition.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/conditions/{condition_id}")
    def health_conditions_delete(condition_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_condition(condition_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 用药记录 ──

    @app.get("/api/health/medications")
    def health_medications_list(
        patient_id: int | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_medications(
            patient_id=patient_id,
            status=status,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [m.model_dump(mode="json") for m in items],
                "total": total,
            }
        )

    @app.post("/api/health/medications")
    def health_medications_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = MedicationCreate(**payload)
            medication = svc.create_medication(data)
            return JSONResponse({"ok": True, "data": medication.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/medications/{medication_id}")
    def health_medications_get(medication_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            medication = svc.get_medication(medication_id)
            return JSONResponse({"ok": True, "data": medication.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/medications/{medication_id}")
    def health_medications_update(medication_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = MedicationUpdate(**payload)
            medication = svc.update_medication(medication_id, data)
            return JSONResponse({"ok": True, "data": medication.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/medications/{medication_id}")
    def health_medications_delete(medication_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_medication(medication_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 化验结果 ──

    @app.get("/api/health/lab-results")
    def health_lab_results_list(
        patient_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
    ) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_lab_results(
            patient_id=patient_id,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            search=search,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [l.model_dump(mode="json") for l in items],
                "total": total,
            }
        )

    @app.post("/api/health/lab-results")
    def health_lab_results_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = LabResultCreate(**payload)
            lab_result = svc.create_lab_result(data)
            return JSONResponse({"ok": True, "data": lab_result.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/lab-results/{lab_result_id}")
    def health_lab_results_get(lab_result_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            lab_result = svc.get_lab_result(lab_result_id)
            return JSONResponse({"ok": True, "data": lab_result.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/lab-results/{lab_result_id}")
    def health_lab_results_update(lab_result_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = LabResultUpdate(**payload)
            lab_result = svc.update_lab_result(lab_result_id, data)
            return JSONResponse({"ok": True, "data": lab_result.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/lab-results/{lab_result_id}")
    def health_lab_results_delete(lab_result_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_lab_result(lab_result_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.get("/api/health/lab-trend")
    def health_lab_trend(patient_id: int, test_name: str, limit: int = 50) -> JSONResponse:
        """获取某个化验项目的历史趋势。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        trend = svc.get_lab_trend(patient_id=patient_id, test_name=test_name, limit=limit)
        return JSONResponse({"ok": True, "test_name": test_name, "items": trend})

    # ── 检查 / 手术 ──

    @app.get("/api/health/procedures")
    def health_procedures_list(
        patient_id: int | None = None,
        procedure_type: str | None = None,
        needs_follow_up: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_procedures(
            patient_id=patient_id,
            procedure_type=procedure_type,
            needs_follow_up=needs_follow_up,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [p.model_dump(mode="json") for p in items],
                "total": total,
            }
        )

    @app.post("/api/health/procedures")
    def health_procedures_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ProcedureCreate(**payload)
            procedure = svc.create_procedure(data)
            return JSONResponse({"ok": True, "data": procedure.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/procedures/{procedure_id}")
    def health_procedures_get(procedure_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            procedure = svc.get_procedure(procedure_id)
            return JSONResponse({"ok": True, "data": procedure.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/procedures/{procedure_id}")
    def health_procedures_update(procedure_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ProcedureUpdate(**payload)
            procedure = svc.update_procedure(procedure_id, data)
            return JSONResponse({"ok": True, "data": procedure.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/procedures/{procedure_id}")
    def health_procedures_delete(procedure_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_procedure(procedure_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 过敏史 ──

    @app.get("/api/health/allergies")
    def health_allergies_list(patient_id: int | None = None) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_allergies(patient_id=patient_id)
        return JSONResponse({"ok": True, "items": [a.model_dump(mode="json") for a in items]})

    @app.post("/api/health/allergies")
    def health_allergies_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = AllergyCreate(**payload)
            allergy = svc.create_allergy(data)
            return JSONResponse({"ok": True, "data": allergy.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/allergies/{allergy_id}")
    def health_allergies_delete(allergy_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_allergy(allergy_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 生命体征 ──

    @app.get("/api/health/vitals")
    def health_vitals_list(patient_id: int, limit: int = 100, offset: int = 0) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_vitals(
            patient_id=patient_id,
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [v.model_dump(mode="json") for v in items],
                "total": total,
            }
        )

    @app.post("/api/health/vitals")
    def health_vitals_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = VitalsCreate(**payload)
            vitals = svc.create_vitals(data)
            return JSONResponse({"ok": True, "data": vitals.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/vitals/{vitals_id}")
    def health_vitals_delete(vitals_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_vitals(vitals_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 疫苗接种 ──

    @app.get("/api/health/immunizations")
    def health_immunizations_list(patient_id: int | None = None) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_immunizations(patient_id=patient_id)
        return JSONResponse({"ok": True, "items": [i.model_dump(mode="json") for i in items]})

    @app.post("/api/health/immunizations")
    def health_immunizations_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = ImmunizationCreate(**payload)
            immunization = svc.create_immunization(data)
            return JSONResponse({"ok": True, "data": immunization.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/immunizations/{immunization_id}")
    def health_immunizations_delete(immunization_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_immunization(immunization_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 医生信息 ──

    @app.get("/api/health/doctors")
    def health_doctors_list(
        specialty: str | None = None, search: str | None = None
    ) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_doctors(specialty=specialty, search=search)
        return JSONResponse({"ok": True, "items": [d.model_dump(mode="json") for d in items]})

    @app.post("/api/health/doctors")
    def health_doctors_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = DoctorCreate(**payload)
            doctor = svc.create_doctor(data)
            return JSONResponse({"ok": True, "data": doctor.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/doctors/{doctor_id}")
    def health_doctors_get(doctor_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            doctor = svc.get_doctor(doctor_id)
            return JSONResponse({"ok": True, "data": doctor.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/doctors/{doctor_id}")
    def health_doctors_update(doctor_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = DoctorUpdate(**payload)
            doctor = svc.update_doctor(doctor_id, data)
            return JSONResponse({"ok": True, "data": doctor.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/doctors/{doctor_id}")
    def health_doctors_delete(doctor_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_doctor(doctor_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 文档 / 附件 ──

    @app.get("/api/health/documents")
    def health_documents_list(
        patient_id: int | None = None,
        document_type: str | None = None,
        encounter_id: int | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_documents(
            patient_id=patient_id,
            document_type=document_type,
            encounter_id=encounter_id,
            search=search,
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [d.model_dump(mode="json") for d in items],
                "total": total,
            }
        )

    @app.post("/api/health/documents")
    def health_documents_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = HealthDocumentCreate(**payload)
            doc = svc.create_document(data)
            return JSONResponse({"ok": True, "data": doc.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/documents/{document_id}")
    def health_documents_get(document_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            doc = svc.get_document(document_id)
            return JSONResponse({"ok": True, "data": doc.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/documents/{document_id}")
    def health_documents_update(document_id: int, payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = HealthDocumentUpdate(**payload)
            doc = svc.update_document(document_id, data)
            return JSONResponse({"ok": True, "data": doc.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/documents/{document_id}")
    def health_documents_delete(document_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_document(document_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── AI 健康洞察 ──

    @app.get("/api/health/insights")
    def health_insights_list(
        patient_id: int | None = None,
        target_type: str | None = None,
        target_id: int | None = None,
        limit: int = 50,
    ) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items = svc.list_insights(
            patient_id=patient_id,
            target_type=target_type,
            target_id=target_id,
            limit=max(1, min(int(limit), 200)),
        )
        return JSONResponse({"ok": True, "items": [i.model_dump(mode="json") for i in items]})

    @app.post("/api/health/insights")
    def health_insights_create(payload: dict[str, Any]) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            data = HealthInsightCreate(**payload)
            insight = svc.create_insight(data)
            return JSONResponse({"ok": True, "data": insight.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/health/insights/{insight_id}")
    def health_insights_delete(insight_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_insight(insight_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 健康时间线 ──

    @app.get("/api/health/timeline")
    def health_timeline(patient_id: int, limit: int = 100, offset: int = 0) -> JSONResponse:
        """获取患者健康时间线，聚合所有类型的健康事件。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        events = svc.get_timeline(
            patient_id=patient_id,
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "patient_id": patient_id,
                "items": [e.model_dump(mode="json") for e in events],
                "total": len(events),
            }
        )

    # ── 预约 / 复诊 ──

    @app.get("/api/health/appointments")
    def health_appointments_list(
        patient_id: int | None = None,
        status: str | None = None,
        upcoming_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_appointments(
            patient_id=patient_id,
            status=status,
            upcoming_only=upcoming_only,
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [a.model_dump(mode="json") for a in items],
                "total": total,
            }
        )

    @app.post("/api/health/appointments")
    def health_appointments_create(data: AppointmentCreate) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.create_appointment(data)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/appointments/{appointment_id}")
    def health_appointments_get(appointment_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.get_appointment(appointment_id)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.put("/api/health/appointments/{appointment_id}")
    def health_appointments_update(appointment_id: int, data: AppointmentUpdate) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.update_appointment(appointment_id, data)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.delete("/api/health/appointments/{appointment_id}")
    def health_appointments_delete(appointment_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_appointment(appointment_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    # ── 服药记录 / 用药依从性 ──

    @app.get("/api/health/medication-logs")
    def health_medication_logs_list(
        patient_id: int | None = None,
        medication_id: int | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        items, total = svc.list_medication_logs(
            patient_id=patient_id,
            medication_id=medication_id,
            start_date=start_date,
            end_date=end_date,
            limit=max(1, min(int(limit), 500)),
            offset=max(0, int(offset)),
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [m.model_dump(mode="json") for m in items],
                "total": total,
            }
        )

    @app.post("/api/health/medication-logs")
    def health_medication_logs_create(data: MedicationLogCreate) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.create_medication_log(data)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/health/medication-logs/{log_id}")
    def health_medication_logs_get(log_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            item = svc.get_medication_log(log_id)
            return JSONResponse({"ok": True, "item": item.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.delete("/api/health/medication-logs/{log_id}")
    def health_medication_logs_delete(log_id: int) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            svc.delete_medication_log(log_id)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.get("/api/health/medication-adherence")
    def health_medication_adherence(patient_id: int, days: int = 30) -> JSONResponse:
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        result = svc.get_medication_adherence(patient_id, max(1, min(int(days), 365)))
        return JSONResponse({"ok": True, **result})

    # ── 药物相互作用检查 ──

    @app.post("/api/health/check-drug-interactions")
    def health_check_drug_interactions(drug_name: str, existing_drugs: str) -> JSONResponse:
        """检查新药与现有药物的相互作用。existing_drugs 用逗号分隔。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        drugs = [d.strip() for d in existing_drugs.split(",") if d.strip()]
        interactions = svc.check_drug_interactions(drug_name, drugs)
        return JSONResponse(
            {
                "ok": True,
                "drug_name": drug_name,
                "existing_drugs": drugs,
                "interactions": [i.model_dump(mode="json") for i in interactions],
                "has_interaction": len(interactions) > 0,
            }
        )

    # ── AI 报告解读 ──

    @app.post("/api/health/lab-results/{lab_result_id}/interpret")
    async def health_lab_interpret(lab_result_id: int) -> JSONResponse:
        """使用 AI 解读化验报告。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if svc.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM service unavailable"}, status_code=503)
        try:
            insight = await svc.interpret_lab_result(lab_result_id)
            if insight is None:
                return JSONResponse(
                    {"ok": False, "error": "interpretation failed"}, status_code=500
                )
            return JSONResponse({"ok": True, "data": insight.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.post("/api/health/procedures/{procedure_id}/interpret")
    async def health_procedure_interpret(procedure_id: int) -> JSONResponse:
        """使用 AI 解读检查报告。"""
        svc = _get_health_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        if svc.llm_service is None:
            return JSONResponse({"ok": False, "error": "LLM service unavailable"}, status_code=503)
        try:
            insight = await svc.interpret_procedure(procedure_id)
            if insight is None:
                return JSONResponse(
                    {"ok": False, "error": "interpretation failed"}, status_code=500
                )
            return JSONResponse({"ok": True, "data": insight.model_dump(mode="json")})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @app.get("/api/subscriptions/stats", response_model=SubscriptionStatsOut)
    def list_subscriptions_with_stats() -> SubscriptionStatsOut:
        """List all subscriptions with item count and last fetch statistics."""
        from openbiliclaw.config import load_config as _load_cfg

        _cfg = _load_cfg()
        stats_map: dict[str, tuple[int, str]] = {}  # url -> (count, last_fetched)

        # Query from content_cache
        database = getattr(ctx, "database", None)
        conn = getattr(database, "conn", None) if database else None

        if conn is not None:
            for platform, config_list in [
                ("rss", _cfg.scheduler.rss_subscriptions),
                ("xiaoyuzhou", _cfg.scheduler.xiaoyuzhou_subscriptions),
                ("wechat", _cfg.scheduler.wechat_subscriptions),
            ]:
                for item in config_list:
                    name = item.get("name", "")
                    url = item.get("url", "")
                    if not url:
                        continue
                    cursor = conn.execute(
                        """
                        SELECT COUNT(*) AS item_count, MAX(discovered_at) AS last_fetched
                        FROM content_cache
                        WHERE source_platform = ? AND up_name = ?
                        """,
                        (platform, name),
                    )
                    row = cursor.fetchone()
                    if row:
                        stats_map[f"{platform}:{url}"] = (int(row[0] or 0), str(row[1] or ""))

        # Build response
        result = SubscriptionStatsOut()

        def map_subs(subs: list[dict[str, str]], platform: str) -> list[SubscriptionItemOut]:
            out = []
            for sub in subs:
                key = f"{platform}:{sub['url']}"
                count, last = stats_map.get(key, (0, ""))
                out.append(
                    SubscriptionItemOut(
                        name=sub["name"],
                        url=sub["url"],
                        item_count=int(count),
                        last_fetched_at=last or "",
                    )
                )
            return out

        result.rss = map_subs(list(_cfg.scheduler.rss_subscriptions), "rss")
        result.xiaoyuzhou = map_subs(list(_cfg.scheduler.xiaoyuzhou_subscriptions), "xiaoyuzhou")
        result.wechat = map_subs(list(_cfg.scheduler.wechat_subscriptions), "wechat")
        return result

    @app.post("/api/subscriptions")
    async def add_subscription(payload: SubscriptionAddIn) -> JSONResponse:
        """Add a new subscription source."""
        from openbiliclaw.config import load_config as _load_cfg
        from openbiliclaw.config import save_config as _save_cfg

        async with _CONFIG_SAVE_LOCK:
            _cfg = _load_cfg()
            source_type = payload.source_type
            source_map = {
                "rss": "rss_subscriptions",
                "xiaoyuzhou": "xiaoyuzhou_subscriptions",
                "wechat": "wechat_subscriptions",
            }
            field_name = source_map.get(source_type)
            if field_name is None:
                return JSONResponse(
                    {"ok": False, "error": f"unknown source_type: {source_type}"},
                    status_code=400,
                )
            subscriptions: list[dict[str, str]] = getattr(_cfg.scheduler, field_name)
            # Check if already exists
            for sub in subscriptions:
                if sub.get("url") == payload.url:
                    return JSONResponse(
                        {"ok": False, "error": "subscription already exists"},
                        status_code=409,
                    )
            subscriptions.append({"name": payload.name, "url": payload.url})
            setattr(_cfg.scheduler, field_name, subscriptions)
            _save_cfg(_cfg)
        return JSONResponse({"ok": True})

    @app.delete("/api/subscriptions")
    async def delete_subscription(payload: SubscriptionDeleteIn) -> JSONResponse:
        """Delete a subscription source by type and URL."""
        from openbiliclaw.config import load_config as _load_cfg
        from openbiliclaw.config import save_config as _save_cfg

        async with _CONFIG_SAVE_LOCK:
            _cfg = _load_cfg()
            source_type = payload.source_type
            source_map = {
                "rss": "rss_subscriptions",
                "xiaoyuzhou": "xiaoyuzhou_subscriptions",
                "wechat": "wechat_subscriptions",
            }
            field_name = source_map.get(source_type)
            if field_name is None:
                return JSONResponse(
                    {"ok": False, "error": f"unknown source_type: {source_type}"},
                    status_code=400,
                )
            subscriptions: list[dict[str, str]] = getattr(_cfg.scheduler, field_name)
            new_list = [s for s in subscriptions if s.get("url") != payload.url]
            if len(new_list) == len(subscriptions):
                return JSONResponse(
                    {"ok": False, "error": "subscription not found"},
                    status_code=404,
                )
            setattr(_cfg.scheduler, field_name, new_list)
            _save_cfg(_cfg)
        return JSONResponse({"ok": True})

    # ── Notes CRUD routes ──────────────────────────────────────
    register_notes_routes(app, ctx)

    # ── Saved-sync (reading library) routes ─────────────────────
    register_saved_sync_routes(app, ctx)

    # ── System-level routes (update status, notifications, cognition) ──
    register_system_routes(app, ctx)

    # ── Image proxy routes ───────────────────────────────────────
    register_image_proxy_routes(app, ctx)

    # ── Cookie management routes (douyin/x) ─────────────────────
    register_cookie_routes(app, ctx, config=config)

    # ── 拆分后未接线的路由注册（K3 孤儿路由修复）──────────────────
    for _mod_name, _fn_name in [
        ("article_routes", "register_article_routes"),
        ("diary_routes", "register_diary_routes"),
        ("health_routes", "register_health_routes"),
        ("knowledge_routes", "register_knowledge_routes"),
        ("library_routes", "register_library_routes"),
        ("reading_routes", "register_reading_routes"),
    ]:
        try:
            _mod = __import__(f"openbiliclaw.api.{_mod_name}", fromlist=[_fn_name])
            getattr(_mod, _fn_name)(app, ctx)
        except Exception:  # noqa: BLE001
            logger.exception("%s registration failed", _fn_name)

    # ── 有额外依赖参数的路由注册 ────────────────────────────────
    try:
        from openbiliclaw.api.chat_probe_routes import register_chat_probe_routes

        register_chat_probe_routes(
            app,
            ctx,
            fire_and_forget_tasks=_fire_and_forget_tasks,
            serialize_recommendation_items=_serialize_recommendation_items,
        )
    except Exception:  # noqa: BLE001
        logger.exception("chat_probe_routes registration failed")

    try:
        from openbiliclaw.api.chat_recommend_routes import register_chat_recommend_routes

        register_chat_recommend_routes(
            app,
            ctx,
            serialize_recommendation_items=_serialize_recommendation_items,
        )
    except Exception:  # noqa: BLE001
        logger.exception("chat_recommend_routes registration failed")

    try:
        from openbiliclaw.api.config_routes import register_config_routes

        register_config_routes(
            app,
            ctx,
            config_save_lock=_CONFIG_SAVE_LOCK,
            init_active_now=_init_active_now,
        )
    except Exception:  # noqa: BLE001
        logger.exception("config_routes registration failed")

    try:
        from openbiliclaw.api.feedback_topics_routes import (
            register_feedback_topics_routes,
        )

        register_feedback_topics_routes(
            app,
            ctx,
            schedule_post_feedback_tasks=_schedule_post_feedback_tasks,
            record_exploration_buffer_event=_record_exploration_buffer_event,
            recommendation_buffer_domain=_recommendation_buffer_domain,
        )
    except Exception:  # noqa: BLE001
        logger.exception("feedback_topics_routes registration failed")

    try:
        from openbiliclaw.api.source_routes import register_source_routes

        register_source_routes(
            app,
            ctx,
            _CONFIG_SAVE_LOCK,
            get_auth_gate=_get_auth_gate,
            init_active_now=_init_active_now,
            ingest_profile_update_events=_ingest_profile_update_events,
            snapshot_config_file=_snapshot_config_file,
            restore_config_snapshot=_restore_config_snapshot,
        )
    except Exception:  # noqa: BLE001
        logger.exception("source_routes registration failed")

    try:
        from openbiliclaw.api.subscription_routes import register_subscription_routes

        register_subscription_routes(
            app,
            ctx,
            config_save_lock=_CONFIG_SAVE_LOCK,
        )
    except Exception:  # noqa: BLE001
        logger.exception("subscription_routes registration failed")

    # ── Knowledge Forge routes ─────────────────────────────────
    try:
        from openbiliclaw.api.knowledge_forge_routes import (
            register_knowledge_forge_routes,
        )

        register_knowledge_forge_routes(app, ctx)
    except Exception:  # noqa: BLE001
        logger.exception("Knowledge Forge routes registration failed")

    # ── Recommendation feed routes (M1 extraction from this file) ──
    app.include_router(
        build_recommendation_router(
            ctx=ctx,
            config=config,
            fire_and_forget_tasks=_fire_and_forget_tasks,
            init_active_now=_init_active_now,
            pick_best_xhs_url=_pick_best_xhs_url,
            serialize_recommendation_items=_serialize_recommendation_items,
            load_interest_keywords=_load_interest_keywords,
            request_runtime_replenishment=_request_runtime_replenishment,
        )
    )

    # ── 旅行预算 API ─────────────────────────────────────────────
    from openbiliclaw.travel.routes import build_travel_router

    _travel_cfg = getattr(config, "travel", None)
    app.include_router(
        build_travel_router(
            data_path=getattr(_travel_cfg, "data_path", "") or "",
            budget_doc=getattr(_travel_cfg, "budget_doc", "新疆旅行预算.md"),
            flights_json=getattr(
                _travel_cfg, "flights_json", "ctrip-ticket-crawler/our_routes_results.json"
            ),
        )
    )

    # ── 求职面试备战 API ─────────────────────────────────────────
    try:
        from openbiliclaw.interview.routes import build_interview_router

        _interview_cfg = getattr(config, "interview", None)
        app.include_router(
            build_interview_router(
                root=str(getattr(_interview_cfg, "root", "") or "") or None,
            )
        )
    except Exception:  # noqa: BLE001 — 可选模块导入失败不阻塞主 API
        logger.exception("Interview routes registration failed")

    # ── 面试复盘记录 API ─────────────────────────────────────────
    try:
        from openbiliclaw.interview.review_routes import build_review_router

        app.include_router(build_review_router())
    except Exception:  # noqa: BLE001
        logger.exception("Interview review routes registration failed")

    # ── 克隆系统 API ─────────────────────────────────────────────

    _clone_service: CloneService | None = None

    def _get_clone_service() -> CloneService | None:
        """获取或创建克隆服务实例（懒加载）。"""
        nonlocal _clone_service
        if _clone_service is not None:
            return _clone_service
        database = getattr(ctx, "database", None)
        if database is None:
            return None
        from openbiliclaw.clone import CloneService as _CloneService
        from openbiliclaw.clone.store import CloneStore as _CloneStore

        _web_dir = _Path(__file__).resolve().parent.parent / "web"
        _sites_dir = _web_dir / "clone" / "sites"
        store = _CloneStore(database=database)
        _clone_service = _CloneService(store=store, sites_dir=_sites_dir)
        return _clone_service

    @app.get("/api/clone/sites")
    def clone_list(
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> JSONResponse:
        """列出克隆站点，支持筛选和搜索。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        sites, total = svc.list_sites(
            limit=max(1, min(int(limit), 200)),
            offset=max(0, int(offset)),
            category=category,
            status=status,
            tag=tag,
            search=search,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [s.model_dump(mode="json") for s in sites],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )

    @app.get("/api/clone/stats")
    def clone_stats() -> JSONResponse:
        """获取克隆系统统计信息。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        stats = svc.get_stats()
        return JSONResponse({"ok": True, "stats": stats.model_dump(mode="json")})

    @app.get("/api/clone/sites/{site_id:int}")
    def clone_get(site_id: int) -> JSONResponse:
        """获取单个克隆站点详情。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        site = svc.get_site(site_id)
        if site is None:
            return JSONResponse({"ok": False, "error": "site not found"}, status_code=404)
        return JSONResponse({"ok": True, "site": site.model_dump(mode="json")})

    @app.post("/api/clone/sites")
    def clone_create(payload: dict[str, Any]) -> JSONResponse:
        """创建新克隆站点记录。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            from openbiliclaw.clone import CloneSiteCreate, CloneStatus

            status = (
                CloneStatus(payload.get("status", "cloned"))
                if payload.get("status")
                else CloneStatus.CLONED
            )
            data = CloneSiteCreate(
                name=payload["name"],
                slug=payload.get("slug", ""),
                source_url=payload.get("source_url", ""),
                local_path=payload.get("local_path", ""),
                description=payload.get("description", ""),
                category=payload.get("category", "other"),
                status=status,
                tags=payload.get("tags", []),
            )
            site = svc.create_site(data)
            return JSONResponse({"ok": True, "site": site.model_dump(mode="json")}, status_code=201)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.put("/api/clone/sites/{site_id:int}")
    def clone_update(site_id: int, payload: dict[str, Any]) -> JSONResponse:
        """更新克隆站点信息。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            from openbiliclaw.clone import CloneSiteUpdate

            data = CloneSiteUpdate(
                name=payload.get("name"),
                description=payload.get("description"),
                category=payload.get("category"),
                status=payload.get("status"),
                tags=payload.get("tags"),
            )
            site = svc.update_site(site_id, data)
            if site is None:
                return JSONResponse({"ok": False, "error": "site not found"}, status_code=404)
            return JSONResponse({"ok": True, "site": site.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.delete("/api/clone/sites/{site_id:int}")
    def clone_delete(site_id: int) -> JSONResponse:
        """删除克隆站点记录。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        ok = svc.delete_site(site_id)
        if not ok:
            return JSONResponse({"ok": False, "error": "site not found"}, status_code=404)
        return JSONResponse({"ok": True})

    @app.post("/api/clone/import")
    def clone_import() -> JSONResponse:
        """扫描 clone/sites/ 目录，批量导入已有站点。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            _web_dir = _Path(__file__).resolve().parent.parent / "web"
            sites_dir = _web_dir / "clone" / "sites"
            imported = svc.import_existing_sites(sites_dir)
            return JSONResponse(
                {
                    "ok": True,
                    "imported": [s.model_dump(mode="json") for s in imported],
                    "count": len(imported),
                }
            )
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.post("/api/clone/clone")
    def clone_new(payload: dict[str, Any]) -> JSONResponse:
        """克隆新网站。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        try:
            from openbiliclaw.clone import CloneRequest

            request = CloneRequest(
                url=payload["url"],
                name=payload["name"],
                category=payload.get("category", "website"),
                tags=payload.get("tags", []),
                depth=min(int(payload.get("depth", 1)), 3),
            )
            site = svc.clone_new_site(request)
            return JSONResponse({"ok": True, "site": site.model_dump(mode="json")})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @app.get("/api/clone/tags")
    def clone_tags() -> JSONResponse:
        """列出所有克隆站点标签。"""
        svc = _get_clone_service()
        if svc is None:
            return JSONResponse({"ok": False, "error": "database unavailable"}, status_code=503)
        tags = svc.list_tags()
        return JSONResponse({"ok": True, "tags": tags})

    # ── Mobile Web UI ───────────────────────────────────────────
    from pathlib import Path as _Path

    from fastapi.staticfiles import StaticFiles as _StaticFiles

    _web_dir = _Path(__file__).resolve().parent.parent / "web"
    if _web_dir.is_dir():
        _favicon_path = _web_dir / "icon-192.png"

        @app.get("/favicon.ico", include_in_schema=False)
        def _favicon() -> FileResponse:
            if not _favicon_path.is_file():
                raise HTTPException(status_code=404, detail="favicon not found")
            return FileResponse(_favicon_path, media_type="image/png")

        app.mount("/m", _StaticFiles(directory=_web_dir, html=True), name="mobile-web")

    # ── Desktop Web UI ───────────────────────────────────────────
    _desktop_dir = _Path(__file__).resolve().parent.parent / "web" / "desktop"
    if _desktop_dir.is_dir():
        _desktop_index_path = _desktop_dir / "index.html"

        def _desktop_asset_version() -> str:
            import hashlib

            digest = hashlib.sha256()
            # 扫描整个 assets 目录（含日记模块 JS），任何前端文件变更都会刷新版本号，
            # 保证浏览器不会命中旧缓存
            assets_dir = _desktop_dir / "assets"
            if assets_dir.is_dir():
                for path in sorted(assets_dir.rglob("*")):
                    if path.is_file():
                        rel = path.relative_to(_desktop_dir).as_posix()
                        stat = path.stat()
                        digest.update(rel.encode("utf-8"))
                        digest.update(str(stat.st_mtime_ns).encode("ascii"))
                        digest.update(str(stat.st_size).encode("ascii"))
            else:
                for relative in ("assets/css/app.css", "assets/js/app.js"):
                    path = _desktop_dir / relative
                    if not path.is_file():
                        continue
                    stat = path.stat()
                    digest.update(relative.encode("utf-8"))
                    digest.update(str(stat.st_mtime_ns).encode("ascii"))
                    digest.update(str(stat.st_size).encode("ascii"))
            return digest.hexdigest()[:12]

        def _desktop_index_response() -> Response:
            if not _desktop_index_path.is_file():
                raise HTTPException(status_code=404, detail="desktop web index not found")
            version = _desktop_asset_version()
            html = _desktop_index_path.read_text(encoding="utf-8")
            html = html.replace(
                'href="/web/assets/css/app.css"',
                f'href="/web/assets/css/app.css?v={version}"',
            )
            html = html.replace(
                'src="/web/assets/js/app.js"',
                f'src="/web/assets/js/app.js?v={version}"',
            )
            html = html.replace(
                'src="/web/assets/js/self-evolution.js"',
                f'src="/web/assets/js/self-evolution.js?v={version}"',
            )
            # 注入版本号全局变量，供动态加载的日记模块脚本做缓存控制
            html = html.replace(
                "</head>",
                f'<script>window.__ASSET_VERSION="{version}";</script></head>',
            )
            return Response(
                html,
                media_type="text/html; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )

        @app.get("/web", include_in_schema=False)
        def _desktop_index_no_slash() -> Response:
            return _desktop_index_response()

        @app.get("/web/", include_in_schema=False)
        def _desktop_index_slash() -> Response:
            return _desktop_index_response()

        _desktop_page_names = {
            "home",
            "delight",
            "saved",
            "profile",
            "chat",
            "diary",
            "clone",
            "library",
            "read-archive",
            "settings",
            "watchLater",
            "watchlater",
            "custom-filter",
            "pool-all",
            "pool-filter",
            "observability",
            "pool-explore",
            "xhs-feed",
            "zhihu-feed",
            "bili-feed",
            "youtube-feed",
            "v2ex-feed",
            "xiaoyuzhou-feed",
            "agent-recommend",
            "self-evolution",
        }

        @app.get("/web/{page}", include_in_schema=False)
        def _desktop_page(page: str) -> Response:
            """Bookmarkable desktop page routes (e.g. /web/library, /web/chat).

            Returns the SPA shell; the client reads location.pathname and opens
            the matching view. `page` is a single path segment, so /web/assets/*
            static requests are never intercepted. Unknown pages 404.
            """
            if page not in _desktop_page_names:
                raise HTTPException(status_code=404, detail="unknown desktop page")
            return _desktop_index_response()

        app.mount("/web", _StaticFiles(directory=_desktop_dir, html=True), name="desktop-web")

        @app.get("/", include_in_schema=False)
        def _root_redirect() -> RedirectResponse:
            return RedirectResponse(url="/web", status_code=302)

    # ── First-run Setup Wizard ──────────────────────────────────
    # Self-contained onboarding page opened on first launch by the packaged
    # app (packaging/entry.py). Guides provider/key + B站 + done, then sends
    # the user to /web. Kept isolated from the main desktop SPA on purpose.
    _setup_dir = _Path(__file__).resolve().parent.parent / "web" / "setup"
    if _setup_dir.is_dir():
        app.mount("/setup", _StaticFiles(directory=_setup_dir, html=True), name="setup-wizard")

    # ── Standalone Reading Library ───────────────────────────────
    # Independent, bookmarkable reading-library page. Surfaces the `articles`
    # table (the 阅读库) with full-text search + source/status/tag filters,
    # decoupled from the mobile/desktop SPAs so it can be opened on its own.
    _reading_dir = _web_dir / "reading-library"

    @app.get("/library/{source}", include_in_schema=False)
    def reading_library_platform(source: str) -> Response:
        """Bookmarkable per-platform reading page. Reuses the same SPA,
        injecting the active source so the client filters and labels by it.
        """
        import re

        from fastapi.responses import Response

        html_path = _reading_dir / "index.html"
        try:
            html = html_path.read_text(encoding="utf-8")
        except Exception:
            return Response("reading-library page not found", status_code=500)
        safe = re.sub(r"[^a-zA-Z0-9_]", "", source or "")
        injected = '<script>window.__SOURCE__="' + safe + '";</script>'
        html = html.replace("</head>", injected + "</head>", 1)
        return Response(html, media_type="text/html")

    if _reading_dir.is_dir():
        app.mount(
            "/library", _StaticFiles(directory=_reading_dir, html=True), name="reading-library"
        )

    # ── Knowledge Forge 知识图谱可视化（设计 3.1）─────────────────
    # 独立可书签页面：实体-概念-文章关系网络（ECharts 关系图），
    # 支持类型筛选与节点详情（关联文章列表）。数据来自 /api/knowledge-graph。
    _kg_dir = _web_dir / "knowledge-graph"
    if _kg_dir.is_dir():
        app.mount(
            "/knowledge-graph",
            _StaticFiles(directory=_kg_dir, html=True),
            name="knowledge-graph",
        )

    # ── Knowledge Forge 前端页面（设计 §5）───────────────────────
    # 作者/主题/概念：通用实体浏览页（列表+详情双视图），按类型注入。
    # 注意：/web 已被 desktop SPA mount 占用，KF 页面与 /knowledge-graph
    # 一致采用顶级独立前缀，可书签直达。
    _entity_dir = _web_dir / "entity-browser"
    if _entity_dir.is_dir():
        for _etype, _slug in (
            ("author", "authors"),
            ("topic", "topics"),
            ("concept", "concepts"),
        ):
            _html = _entity_dir / "index.html"

            def _make_entity_page(entity_type: str, html_path: Path) -> Callable[[], Response]:
                def _page() -> Response:
                    import re

                    from fastapi.responses import Response

                    try:
                        html = html_path.read_text(encoding="utf-8")
                    except Exception:
                        return Response("page not found", status_code=500)
                    safe = re.sub(r"[^a-zA-Z0-9_]", "", entity_type or "")
                    injected = '<script>window.__ENTITY_TYPE__="' + safe + '";</script>'
                    return Response(
                        html.replace("</head>", injected + "</head>", 1),
                        media_type="text/html",
                    )

                return _page

            app.get("/" + _slug, include_in_schema=False)(_make_entity_page(_etype, _html))
        app.mount(
            "/entities",
            _StaticFiles(directory=_entity_dir, html=True),
            name="entity-browser",
        )

    # 质量审计 / 缺口分析 / 矛盾报告：独立静态页
    for _slug in ("audit", "gap-analysis", "contradictions"):
        _dir = _web_dir / _slug
        if _dir.is_dir():
            app.mount(
                "/" + _slug,
                _StaticFiles(directory=_dir, html=True),
                name="knowledge-forge-" + _slug,
            )

    # ── Clone Sites static mount ──────────────────────────────────
    # Serves cloned sites under /clone/sites/{slug} so they can be
    # previewed in the browser.
    _clone_sites_dir = _web_dir / "clone" / "sites"
    if _clone_sites_dir.is_dir():
        app.mount(
            "/clone/sites",
            _StaticFiles(directory=_clone_sites_dir, html=True),
            name="clone-sites",
        )

    # ── Standalone Health (健康档案) page ────────────────────────
    # Personal/family medical records: encounters, conditions, medications,
    # lab results, procedures, allergies, vitals, immunizations.
    _health_dir = _web_dir / "health"
    if _health_dir.is_dir():
        app.mount("/health", _StaticFiles(directory=_health_dir, html=True), name="health-page")

    # ── Standalone Topics (专题) page ────────────────────────────
    # Bookmarkable /topics page listing user-curated topic collections with
    # their continuously collected items; create + collect-now actions hit
    # the /api/topics* endpoints above.
    _topics_dir = _web_dir / "topics"
    if _topics_dir.is_dir():
        app.mount("/topics", _StaticFiles(directory=_topics_dir, html=True), name="topics-page")

    # ── URL Content Extraction (单篇URL内容提取) ───────────────────
    # Inspired by Agent-SaveMark's processor pattern. Extracts structured
    # content from a single URL using platform-specific processors.
    # Supported: Zhihu, V2EX, Hupu, Bilibili, Xiaohongshu, YouTube,
    # Xiaoyuzhou, and generic web pages (fallback).

    @app.get("/api/url/processors")
    async def list_url_processors():
        """List all registered URL content processors."""
        from openbiliclaw.sources.url_processors import get_all_source_types, list_processors

        processors = list_processors()
        return {
            "processors": processors,
            "source_types": get_all_source_types(),
            "total": len(processors),
        }

    @app.post("/api/url/extract")
    async def extract_url_content(payload: dict[str, Any]):
        """Extract content from a single URL.

        Request body:
            url: The URL to extract content from.
            cookies: Optional dict of authentication cookies.
            save: Whether to save the extracted content to the articles table (default: false).
        """
        from openbiliclaw.sources.url_processors import match_processor
        from openbiliclaw.sources.url_processors.base import ProcessorStatus

        url = payload.get("url", "").strip()
        if not url:
            return JSONResponse({"error": "url is required"}, status_code=400)

        cookies = payload.get("cookies", {})
        should_save = payload.get("save", False)

        try:
            processor = match_processor(url)
            result = await processor.process(url, cookies=cookies)

            response_data = {
                "status": result.status.value,
                "source_type": result.source_type,
                "source_name": result.source_name,
                "title": result.title,
                "author": result.author,
                "summary": result.summary,
                "content_text": result.content_text,
                "content_html": result.content_html,
                "published_at": result.published_at,
                "tags": result.tags,
                "url": result.url,
                "metadata": result.metadata,
                "error": result.error,
            }

            # Save to articles table if requested
            if should_save and result.status == ProcessorStatus.success:
                article_id = await _save_extracted_article(result)
                response_data["saved"] = True
                response_data["article_id"] = article_id
            else:
                response_data["saved"] = False

            return response_data

        except Exception as e:
            logger.exception("URL extraction failed for %s", url)
            return JSONResponse({"error": str(e)}, status_code=500)

    async def _save_extracted_article(result) -> int | None:
        """Save extracted content to the articles table.

        Args:
            result: ProcessorResult with extracted content.

        Returns:
            The inserted article ID, or None if insertion failed.

        """
        import sqlite3

        # Get database path from config
        db_path = "data/openbiliclaw.db"
        try:
            cfg = getattr(ctx, "config", None)
            if cfg and hasattr(cfg, "storage") and cfg.storage:
                db_path = str(cfg.storage.db_path)
        except Exception:
            pass

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            article_dict = result.to_article_dict()

            # Check for duplicate by content_hash
            if article_dict.get("content_hash"):
                existing = cursor.execute(
                    "SELECT id FROM articles WHERE content_hash = ? LIMIT 1",
                    (article_dict["content_hash"],),
                ).fetchone()
                if existing:
                    logger.info("Article already exists (id=%s), skipping insert", existing[0])
                    conn.close()
                    return existing[0]

            # Insert article
            columns = ", ".join(article_dict.keys())
            placeholders = ", ".join(["?"] * len(article_dict))
            values = list(article_dict.values())

            cursor.execute(
                f"INSERT INTO articles ({columns}) VALUES ({placeholders})",
                values,
            )
            article_id = cursor.lastrowid

            # 保存离线存档（HTML 快照）
            if result.content_html or result.content_text:
                try:
                    cursor.execute(
                        """INSERT INTO article_snapshots
                           (article_id, url, content_html, content_text, fetch_source)
                           VALUES (?, ?, ?, ?, 'url_extractor')""",
                        (
                            article_id,
                            result.url,
                            result.content_html or "",
                            result.content_text or "",
                        ),
                    )
                    logger.info("Saved snapshot for article (id=%s)", article_id)
                except Exception as snap_err:
                    logger.warning(
                        "Failed to save snapshot for article %s: %s", article_id, snap_err
                    )

            conn.commit()
            conn.close()

            # 自动注册到阅读调度系统
            try:
                from openbiliclaw.self_evolution.reading_schedule import ReadingScheduler

                scheduler = ReadingScheduler(db_path)
                scheduler.register_article(article_id, initial_delay_days=1.0)
            except Exception as sched_err:
                logger.warning(
                    "Failed to register article %s to reading schedule: %s", article_id, sched_err
                )

            logger.info("Saved extracted article (id=%s): %s", article_id, result.title)
            return article_id

        except Exception as e:
            logger.exception("Failed to save extracted article: %s", e)
            return None

    @app.get("/api/articles/{article_id}/snapshot")
    async def get_article_snapshot(article_id: int):
        """Get the offline HTML snapshot for an article.

        Args:
            article_id: The article ID.

        Returns:
            JSON with snapshot data (content_html, content_text, fetched_at).

        """
        import sqlite3

        db_path = "data/openbiliclaw.db"
        try:
            cfg = getattr(ctx, "config", None)
            if cfg and hasattr(cfg, "storage") and cfg.storage:
                db_path = str(cfg.storage.db_path)
        except Exception:
            pass

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            # 先查文章基本信息
            article = cursor.execute(
                "SELECT id, title, url, source_type FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()
            if not article:
                conn.close()
                return JSONResponse({"error": "Article not found"}, status_code=404)

            # 查快照
            snapshot = cursor.execute(
                """SELECT id, content_html, content_text, fetch_source, fetched_at
                   FROM article_snapshots WHERE article_id = ? ORDER BY id DESC LIMIT 1""",
                (article_id,),
            ).fetchone()
            conn.close()

            if not snapshot:
                return {
                    "article_id": article_id,
                    "title": article[1],
                    "url": article[2],
                    "source_type": article[3],
                    "has_snapshot": False,
                    "message": "No offline snapshot available for this article",
                }

            return {
                "article_id": article_id,
                "title": article[1],
                "url": article[2],
                "source_type": article[3],
                "has_snapshot": True,
                "snapshot_id": snapshot[0],
                "content_html": snapshot[1],
                "content_text": snapshot[2],
                "fetch_source": snapshot[3],
                "fetched_at": snapshot[4],
            }

        except Exception as e:
            logger.exception("Failed to get snapshot for article %s: %s", article_id, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    @app.get("/api/snapshots/stats")
    async def get_snapshots_stats():
        """Get statistics about offline snapshots."""
        import sqlite3

        db_path = "data/openbiliclaw.db"
        try:
            cfg = getattr(ctx, "config", None)
            if cfg and hasattr(cfg, "storage") and cfg.storage:
                db_path = str(cfg.storage.db_path)
        except Exception:
            pass

        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()

            total = cursor.execute("SELECT COUNT(*) FROM article_snapshots").fetchone()[0]
            with_html = cursor.execute(
                "SELECT COUNT(*) FROM article_snapshots WHERE content_html != '' AND content_html IS NOT NULL"
            ).fetchone()[0]
            sources = cursor.execute(
                "SELECT fetch_source, COUNT(*) FROM article_snapshots GROUP BY fetch_source"
            ).fetchall()
            conn.close()

            return {
                "total_snapshots": total,
                "with_html": with_html,
                "by_source": {s[0]: s[1] for s in sources},
            }

        except Exception as e:
            logger.exception("Failed to get snapshot stats: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── LLM Quota Monitoring ──────────────────────────────────────
    @app.get("/api/llm/quota")
    async def llm_quota_status(hours: int = 5):
        """查询 LLM 配额使用情况。

        Args:
            hours: 查询时间窗口（小时），默认 5 小时（与商汤 API 限流窗口对齐）。

        """
        try:
            import sqlite3
            from datetime import datetime, timedelta

            _quota_db_path = "data/openbiliclaw.db"
            try:
                cfg = getattr(ctx, "config", None)
                if cfg and hasattr(cfg, "storage") and cfg.storage:
                    _quota_db_path = str(cfg.storage.db_path)
            except Exception:
                pass

            conn = sqlite3.connect(_quota_db_path)
            cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

            # 总调用次数
            total_calls = conn.execute(
                "SELECT COUNT(*) FROM llm_usage WHERE timestamp >= ?", (cutoff,)
            ).fetchone()[0]

            # 总 token 数
            total_tokens = conn.execute(
                "SELECT COALESCE(SUM(prompt_tokens + completion_tokens), 0) FROM llm_usage WHERE timestamp >= ?",
                (cutoff,),
            ).fetchone()[0]

            # 按模型分组
            by_model = [
                {"model": r[0], "calls": r[1], "tokens": r[2]}
                for r in conn.execute(
                    "SELECT model, COUNT(*), SUM(prompt_tokens + completion_tokens) "
                    "FROM llm_usage WHERE timestamp >= ? GROUP BY model ORDER BY COUNT(*) DESC",
                    (cutoff,),
                ).fetchall()
            ]

            # 按模块分组
            by_module = [
                {"module": r[0], "calls": r[1], "tokens": r[2]}
                for r in conn.execute(
                    "SELECT "
                    "  CASE "
                    "    WHEN caller LIKE 'soul.%' THEN 'soul' "
                    "    WHEN caller LIKE 'discovery.%' THEN 'discovery' "
                    "    WHEN caller LIKE 'recommendation.%' THEN 'recommendation' "
                    "    WHEN caller LIKE 'diary.%' THEN 'diary' "
                    "    WHEN caller LIKE 'chat_analysis%' THEN 'chat_analysis' "
                    "    WHEN caller LIKE 'notes.%' THEN 'notes' "
                    "    WHEN caller LIKE 'self_evolution.%' OR caller LIKE '%tldr%' OR caller LIKE '%knowledge_card%' THEN 'self_evolution' "
                    "    ELSE 'other' "
                    "  END as module, "
                    "  COUNT(*), SUM(prompt_tokens + completion_tokens) "
                    "FROM llm_usage WHERE timestamp >= ? GROUP BY module ORDER BY COUNT(*) DESC",
                    (cutoff,),
                ).fetchall()
            ]

            # 商汤日日新配额估算（假设 1 point ≈ 1000 tokens）
            sum(m["calls"] for m in by_model if "sensenova" in m["model"])
            sensenova_tokens = sum(m["tokens"] for m in by_model if "sensenova" in m["model"])
            estimated_points = max(1, sensenova_tokens // 1000)
            quota_limit = 60000
            quota_usage_pct = round(estimated_points / quota_limit * 100, 1)

            conn.close()

            return JSONResponse(
                {
                    "ok": True,
                    "window_hours": hours,
                    "total_calls": total_calls,
                    "total_tokens": total_tokens,
                    "estimated_sensenova_points": estimated_points,
                    "sensenova_quota_limit": quota_limit,
                    "sensenova_quota_usage_pct": quota_usage_pct,
                    "by_model": by_model,
                    "by_module": by_module,
                }
            )
        except Exception as e:
            logger.exception("LLM quota query failed")
            return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    # ── Self-Evolution (自进化) API endpoints ─────────────────────
    # 已独立为 src/openbiliclaw/self_evolution/api.py，此处仅注册路由
    from openbiliclaw.self_evolution.api import create_self_evolution_router

    _self_evo_db_path = (
        str(getattr(getattr(ctx, "config", None), "storage", None).db_path)
        if getattr(getattr(ctx, "config", None), "storage", None)
        else "data/openbiliclaw.db"
    )
    app.include_router(
        create_self_evolution_router(_self_evo_db_path, getattr(ctx, "llm_service", None))
    )

    # ── Synthesis (迭代合成) API endpoints ─────────────────────────
    from openbiliclaw.synthesis.api import create_synthesis_router

    app.include_router(
        create_synthesis_router(_self_evo_db_path, getattr(ctx, "llm_service", None))
    )

    return app
