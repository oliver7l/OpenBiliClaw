"""SQLite database management.

Provides async-compatible SQLite operations for event logs,
content cache, and recommendation history.
"""

from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from openbiliclaw.storage._chat_turn_mixin import ChatTurnMixin
from openbiliclaw.storage._content_cache_mixin import ContentCacheMixin
from openbiliclaw.storage._discovery_candidates_mixin import DiscoveryCandidatesMixin
from openbiliclaw.storage._recommendation_mixin import RecommendationMixin
from openbiliclaw.storage._pool_candidate_mixin import PoolCandidateMixin
from openbiliclaw.storage._prune_mixin import PruneMixin
from openbiliclaw.storage._quality_mixin import QualityMixin
from openbiliclaw.storage._view_history_mixin import ViewHistoryMixin
from openbiliclaw.storage._topic_mixin import TopicMixin
from openbiliclaw.storage._native_sync_mixin import NativeSyncMixin
from openbiliclaw.storage._watch_later_mixin import WatchLaterMixin
from openbiliclaw.storage._delight_mixin import DelightMixin
from openbiliclaw.storage._source_recipe_mixin import SourceRecipeMixin
from openbiliclaw.storage._cover_mixin import CoverMixin
from openbiliclaw.storage._article_mixin import ArticleMixin
from openbiliclaw.storage._favorites_mixin import FavoritesMixin
from openbiliclaw.storage._user_feedback_mixin import UserFeedbackMixin
from openbiliclaw.storage._events_mixin import EventsMixin
from openbiliclaw.storage._llm_usage_mixin import LLMUsageMixin

if TYPE_CHECKING:
    from datetime import datetime

logger = logging.getLogger(__name__)
# v0.3.62+: retry budget tightened from 5×100ms (worst-case 500ms
# blocking the asyncio event loop on lock contention) to 8×20ms
# (worst-case 160ms). Same total absolute timeout floor (~160-500ms)
# is preserved by raising attempt count; per-attempt sleep is short
# enough that even if it fires inside an async context the event-loop
# stutter is below human-perception thresholds. Most writes succeed
# on the first try anyway — this only matters under heavy concurrent
# write load (refresh tick + ingest + classify all hammering pool
# rows simultaneously). A future rewrite can move to asyncio.to_thread
# for true non-blocking DB I/O, but that's a larger refactor (every
# caller must become async) — for now this constant tweak is the
# pragmatic middle ground.
_LOCK_RETRY_ATTEMPTS = 8
_LOCK_RETRY_SLEEP_SECONDS = 0.02
_BVID_PATTERN = re.compile(r"(BV[0-9A-Za-z]+)")
_VIEW_CONTENT_ID_METADATA_KEYS = (
    "content_id",
    "bvid",
    "note_id",
    "aweme_id",
    "video_id",
    "yt_video_id",
)


def _unique_clean_strings(values: Sequence[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _chunks(values: Sequence[str], size: int) -> list[list[str]]:
    chunk_size = max(1, int(size))
    return [list(values[index : index + chunk_size]) for index in range(0, len(values), chunk_size)]


# Mirrors recommendation.delight.DEFAULT_DELIGHT_THRESHOLD. Storage stays a
# leaf module (no openbiliclaw imports), so the value is duplicated here and
# pinned by tests/test_delight_scorer.py::test_delight_claim_threshold_in_sync.
_DELIGHT_CLAIM_MIN_SCORE = 0.70
_DEFAULT_ADMISSION_MIN_SCORE = 0.60

# Cross-circle explore candidates get a lower admission floor than the
# configured default. Explore content is intentionally far from the user's
# profile, so its relevance_score runs systematically low; a uniform floor
# would bar the whole explore pool from being served (delight surprises
# included). Mirrors upstream discovery/admission.EXPLORE_ADMISSION_MIN_SCORE.
_EXPLORE_ADMISSION_MIN_SCORE = 0.58
_EXPLORE_STRATEGY = "explore"

# Rows claimed by the surprise (delight) channel: already delivered as a
# delight, or currently delight-eligible (the pending-queue predicate). The
# regular feed's servable gate excludes them so the same content never shows
# up in both the recommendation list and the surprise tray.
_DELIGHT_CLAIM_GUARD_SQL = f"""
                  AND NOT (
                    COALESCE(delight_notified, 0) = 1
                    OR (
                      COALESCE(delight_score, 0.0) >= {_DELIGHT_CLAIM_MIN_SCORE}
                      AND COALESCE(delight_reason, '') != ''
                      AND COALESCE(delight_hook, '') != ''
                    )
                  )
"""

# ── Exposure cooldown vs. manual dislike ─────────────────────────────
# Exposure alone must not permanently filter an item out of the rotation.
# v0.3.153+ (dd09b3d0): the window shrank from 24h to 1 second, so served
# ('shown') and feedbacked rows recycle essentially immediately — that is
# what lets the six-platform feed producers' content flow into
# recommendations without a lockout. Only a *manual* dislike
# (feedback_type='dislike', or the pool purge it triggers) filters an
# item out for good.
_POOL_RESHOWN_COOLDOWN_SQL = "datetime('now', '+1 seconds')"

# Servable pool_status predicate: fresh rows, plus shown/feedbacked rows
# whose last exposure or feedback is older than the cooldown. Legacy rows
# without a timestamp revive immediately (2000-01-01 sentinel). Feedback
# rows re-enter the rotation too — only 'dislike' feedback (handled
# separately below and by the pool purge) is a permanent filter.
_POOL_SERVABLE_STATUS_SQL = f"""
    (
      COALESCE(pool_status, 'fresh') = 'fresh'
      OR (
        pool_status IN ('shown', 'feedbacked')
        AND COALESCE(feedback_type, '') != 'dislike'
        AND COALESCE(recommended_at, feedback_at, '2000-01-01')
              < {_POOL_RESHOWN_COOLDOWN_SQL}
      )
    )
"""

# The recommendations-history guard is bounded by the same cooldown: an
# item is only blocked while a recommendation row for it is younger than
# the cooldown (previously *any* historical recommendation row excluded
# the item forever, which made exposure permanent filtering).
_POOL_NOT_RECENTLY_RECOMMENDED_SQL = f"""
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
                  AND r.created_at >= {_POOL_RESHOWN_COOLDOWN_SQL}
              )
"""

_LEGACY_STYLE_KEY_MAP: dict[str, str] = {
    "deep_dive": "deep_focus",
    "tech_analysis": "deep_focus",
    "music_analysis": "deep_focus",
    "news_brief": "quick_scan",
    "practical_guide": "hands_on",
    "tutorial_short": "hands_on",
    "game_strategy": "hands_on",
    "review_roundup": "decision_support",
    "unboxing_experience": "decision_support",
    "story_doc": "story_immersion",
    "emotional_narrative": "story_immersion",
    "true_crime": "story_immersion",
    "opinion_stand": "opinion_sparring",
    "light_chat": "social_chat",
    "lifestyle": "daily_wander",
    "fun_variety": "mood_release",
    "parody_remix": "mood_release",
    "visual_showcase": "aesthetic_browse",
    "audio_background": "ambient_companion",
    "music_live": "live_pulse",
    "live_moment": "live_pulse",
    "sports_highlight": "live_pulse",
    "sci_fact": "curiosity_spark",
}

_XHS_SOURCE_FAMILY = "xiaohongshu"
_XHS_SOURCE_PREFIXES = ("xhs-", "xhs_", "xiaohongshu")
_DOUYIN_SOURCE_FAMILY = "douyin"
_DOUYIN_SOURCE_PREFIXES = ("dy-", "dy_", "douyin")
_BILIBILI_SOURCE_FAMILY = "bilibili"
_BILIBILI_SOURCE_KEYS = ("search", "related_chain", "trending", "explore")
_YOUTUBE_SOURCE_FAMILY = "youtube"
_YOUTUBE_SOURCE_PREFIXES = ("yt-", "yt_", "youtube")
_TWITTER_SOURCE_FAMILY = "twitter"
_TWITTER_SOURCE_PREFIXES = ("x-", "x_", "twitter")
_EXPLORE_HIGH_RISK_CLUSTERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "manufacturing",
        ("制造", "工艺", "工厂", "工业", "材料", "金属", "芯片", "显微", "纳米", "疲劳"),
    ),
    (
        "game_theory",
        ("博弈", "桌游", "纳什", "机制", "策略模型", "平衡性"),
    ),
)

# Schema version for migrations
_SCHEMA_VERSION = 2

_SCHEMA_SQL = """
-- Event log (behavioral data from browser extension)
CREATE TABLE IF NOT EXISTS events (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type            TEXT NOT NULL,        -- click, search, scroll, comment, etc.
    url                   TEXT,
    title                 TEXT,
    context               TEXT,                 -- JSON: DOM snapshot reference, viewport, etc.
    metadata              TEXT,                 -- JSON: additional event-specific data
    -- v0.3.x event-satisfaction signal: deterministic classification
    -- written at insert time by ``classify_event_satisfaction``. NULL on
    -- pre-migration rows; consumers treat NULL as ``unknown``.
    inferred_satisfaction TEXT,                 -- "positive" | "neutral" | "negative" | "unknown"
    satisfaction_reason   TEXT,                 -- short snake_case reason; see event_format.py
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Content cache (discovered/evaluated content)
-- 推荐流子库 schema：pool.db（总库连接 ATTACH 后使用 pool. 前缀）
CREATE TABLE IF NOT EXISTS pool.content_cache (
    bvid        TEXT PRIMARY KEY,
    title       TEXT,
    up_name     TEXT,
    up_mid      INTEGER,
    duration    INTEGER,
    tags        TEXT,                 -- JSON array
    topic_key   TEXT DEFAULT '',
    style_key   TEXT DEFAULT '',
    franchise_key TEXT DEFAULT '',  -- LLM IP/series; see _ensure_content_cache_topic_columns
    description TEXT,
    cover_url   TEXT,
    view_count  INTEGER DEFAULT 0,
    like_count  INTEGER DEFAULT 0,
    favorite_count INTEGER DEFAULT 0,
    collect_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    share_count INTEGER DEFAULT 0,
    danmaku_count INTEGER DEFAULT 0,
    reply_count INTEGER DEFAULT 0,
    retweet_count INTEGER DEFAULT 0,
    bookmark_count INTEGER DEFAULT 0,
    relevance_score REAL DEFAULT 0.0,
    relevance_reason TEXT DEFAULT '',
    pool_expression TEXT DEFAULT '',
    pool_topic_label TEXT DEFAULT '',
    candidate_tier TEXT DEFAULT 'primary',
    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_scored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    notification_sent INTEGER DEFAULT 0,
    notified_at TIMESTAMP,
    pool_status TEXT DEFAULT 'fresh',
    recommended_at TIMESTAMP,
    feedback_type TEXT,
    feedback_at TIMESTAMP,
    source      TEXT,                -- Which discovery strategy found it
    body_text   TEXT DEFAULT '',     -- Full text body for text-first sources (X tweet/thread)
    content_type TEXT DEFAULT 'video',  -- Content shape: "video"|"note"|"tweet"|"thread"
    -- P1.8 yield provenance: discovery_keywords.id that produced this row;
    -- NULL for legacy / non-search / flag-off content.
    source_keyword_id INTEGER
);

-- Unified raw discovery candidate queue.
-- Producers enqueue platform-specific raw content here; evaluators claim
-- mixed-source batches and only accepted items advance into content_cache.
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_key         TEXT NOT NULL UNIQUE,
    status                TEXT NOT NULL DEFAULT 'pending_eval',
    source_platform       TEXT NOT NULL DEFAULT '',
    source_strategy       TEXT NOT NULL DEFAULT '',
    source_context        TEXT NOT NULL DEFAULT '',
    content_type          TEXT NOT NULL DEFAULT 'video',
    body_text             TEXT NOT NULL DEFAULT '',
    bvid                  TEXT NOT NULL DEFAULT '',
    content_id            TEXT NOT NULL DEFAULT '',
    content_url           TEXT NOT NULL DEFAULT '',
    title                 TEXT NOT NULL DEFAULT '',
    author_name           TEXT NOT NULL DEFAULT '',
    up_name               TEXT NOT NULL DEFAULT '',
    up_mid                INTEGER NOT NULL DEFAULT 0,
    description           TEXT NOT NULL DEFAULT '',
    cover_url             TEXT NOT NULL DEFAULT '',
    duration              INTEGER NOT NULL DEFAULT 0,
    view_count            INTEGER NOT NULL DEFAULT 0,
    like_count            INTEGER NOT NULL DEFAULT 0,
    favorite_count        INTEGER NOT NULL DEFAULT 0,
    collect_count         INTEGER NOT NULL DEFAULT 0,
    comment_count         INTEGER NOT NULL DEFAULT 0,
    share_count           INTEGER NOT NULL DEFAULT 0,
    danmaku_count         INTEGER NOT NULL DEFAULT 0,
    reply_count           INTEGER NOT NULL DEFAULT 0,
    retweet_count         INTEGER NOT NULL DEFAULT 0,
    bookmark_count        INTEGER NOT NULL DEFAULT 0,
    tags                  TEXT NOT NULL DEFAULT '[]',
    candidate_tier        TEXT NOT NULL DEFAULT 'primary',
    score_threshold       REAL NOT NULL DEFAULT 0.0,
    raw_payload           TEXT NOT NULL DEFAULT '{}',
    source_keyword_id     INTEGER,
    topic_key             TEXT NOT NULL DEFAULT '',
    topic_group           TEXT NOT NULL DEFAULT '',
    style_key             TEXT NOT NULL DEFAULT '',
    franchise_key         TEXT NOT NULL DEFAULT '',
    relevance_score       REAL NOT NULL DEFAULT 0.0,
    relevance_reason      TEXT NOT NULL DEFAULT '',
    pool_expression       TEXT NOT NULL DEFAULT '',
    pool_topic_label      TEXT NOT NULL DEFAULT '',
    eval_error            TEXT NOT NULL DEFAULT '',
    eval_attempts         INTEGER NOT NULL DEFAULT 0,
    batch_eval_attempts   INTEGER NOT NULL DEFAULT 0,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    claimed_at            TIMESTAMP,
    evaluated_at          TIMESTAMP,
    cached_at             TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_status_seen
    ON discovery_candidates(status, last_seen_at, id);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_source_status
    ON discovery_candidates(source_platform, status);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_content_id
    ON discovery_candidates(source_platform, content_id);

-- Recommendation history
CREATE TABLE IF NOT EXISTS pool.recommendations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid        TEXT NOT NULL,
    expression  TEXT,                -- Friend-style recommendation text
    topic       TEXT,                -- Personal topic label
    confidence  REAL DEFAULT 0.0,
    presented   INTEGER DEFAULT 0,   -- Boolean
    feedback    TEXT,                -- User feedback (like/dislike/comment)
    feedback_type TEXT,
    feedback_note TEXT,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    presented_at TIMESTAMP,
    feedback_at TIMESTAMP,
    FOREIGN KEY (bvid) REFERENCES content_cache(bvid)
);

-- Durable popup chat turns.  These let the side panel recover in-flight
-- and completed replies after Chrome reloads or discards the panel page.
CREATE TABLE IF NOT EXISTS chat_turns (
    turn_id       TEXT PRIMARY KEY,
    session       TEXT NOT NULL DEFAULT 'popup',
    scope         TEXT NOT NULL DEFAULT 'chat',
    subject_id    TEXT NOT NULL DEFAULT '',
    subject_title TEXT NOT NULL DEFAULT '',
    message       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',
    reply         TEXT NOT NULL DEFAULT '',
    error         TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_session_created
    ON chat_turns(session, created_at, turn_id);
CREATE INDEX IF NOT EXISTS idx_chat_turns_scope_subject
    ON chat_turns(scope, subject_id, created_at);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- Per-call LLM usage ledger. Populated by ``UsageRecorder`` after every
-- successful provider response. Used by ``openbiliclaw cost`` to print
-- daily spend summaries and by future per-module attribution work.
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    caller TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens INTEGER NOT NULL DEFAULT 0,
    -- v0.3.28+: portion of prompt_tokens served from provider-side
    -- prompt cache. Always <= prompt_tokens. 0 means cache miss / no
    -- caching. Used to compute cache hit rate per caller.
    cached_input_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_cny REAL NOT NULL DEFAULT 0.0,
    success INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp ON llm_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON llm_usage(provider, model);

-- 知识库概念索引：记录每个概念出现在哪些文章中
CREATE TABLE IF NOT EXISTS knowledge_concepts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    concept         TEXT NOT NULL,
    concept_type    TEXT NOT NULL DEFAULT '',
    source_site     TEXT NOT NULL DEFAULT '',
    source_article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    source_article_url TEXT NOT NULL DEFAULT '',
    context_snippet TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_knowledge_concepts_concept
    ON knowledge_concepts(concept);
CREATE INDEX IF NOT EXISTS idx_knowledge_concepts_type
    ON knowledge_concepts(concept_type);
CREATE INDEX IF NOT EXISTS idx_knowledge_concepts_source_article
    ON knowledge_concepts(source_article_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_concepts_site
    ON knowledge_concepts(source_site);

-- 知识库反向链接：A 文章（source）引用了 B 文章（target）
CREATE TABLE IF NOT EXISTS knowledge_backlinks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_article_id  INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    source_title    TEXT NOT NULL DEFAULT '',
    source_url      TEXT NOT NULL DEFAULT '',
    source_site     TEXT NOT NULL DEFAULT '',
    target_concept  TEXT NOT NULL,
    target_type     TEXT NOT NULL DEFAULT '',
    target_url      TEXT NOT NULL DEFAULT '',
    context_snippet TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_knowledge_backlinks_source
    ON knowledge_backlinks(source_article_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_backlinks_target
    ON knowledge_backlinks(target_concept);
CREATE INDEX IF NOT EXISTS idx_knowledge_backlinks_site
    ON knowledge_backlinks(source_site);
"""


# 推荐流子库（pool.db）专用 DDL：xhs_observed_urls / user_feedback 两表定义。
# 主连接 ATTACH pool.db 后以 pool. 前缀建到子库；_ensure_pool_database 兜底
# 建空子库时提取此处定义并去掉 pool. 前缀执行。
_XHS_OBSERVED_URLS_DDL = """
    CREATE TABLE IF NOT EXISTS pool.xhs_observed_urls (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        url         TEXT NOT NULL,
        page_type   TEXT NOT NULL DEFAULT 'other',
        observed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        enriched    INTEGER DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS pool.idx_xhs_observed_urls_url ON xhs_observed_urls (url);
"""

_USER_FEEDBACK_DDL = """
    CREATE TABLE IF NOT EXISTS pool.user_feedback (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        bvid        TEXT NOT NULL,
        action      TEXT NOT NULL CHECK(action IN ('like', 'dislike')),
        source_platform TEXT DEFAULT '',
        title       TEXT DEFAULT '',
        topic_group TEXT DEFAULT '',
        body_text   TEXT DEFAULT '',
        created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS pool.idx_user_feedback_bvid ON user_feedback(bvid);
    CREATE INDEX IF NOT EXISTS pool.idx_user_feedback_action ON user_feedback(action);
    CREATE UNIQUE INDEX IF NOT EXISTS pool.idx_user_feedback_bvid_action ON user_feedback(bvid, action);
"""


def _pool_source_family(source: object, source_platform: object = "") -> str:
    """Return the source family key used by pool share accounting."""
    platform = str(source_platform or "").strip().lower()
    raw_source = str(source or "").strip()
    source_key = raw_source.lower()
    if platform in {_XHS_SOURCE_FAMILY, "xhs"} or source_key.startswith(_XHS_SOURCE_PREFIXES):
        return _XHS_SOURCE_FAMILY
    if platform in {_DOUYIN_SOURCE_FAMILY, "dy"} or source_key.startswith(_DOUYIN_SOURCE_PREFIXES):
        return _DOUYIN_SOURCE_FAMILY
    if platform in {_YOUTUBE_SOURCE_FAMILY, "yt"} or source_key.startswith(
        _YOUTUBE_SOURCE_PREFIXES
    ):
        return _YOUTUBE_SOURCE_FAMILY
    if platform in {_TWITTER_SOURCE_FAMILY, "x"} or source_key.startswith(_TWITTER_SOURCE_PREFIXES):
        return _TWITTER_SOURCE_FAMILY
    if platform in {_BILIBILI_SOURCE_FAMILY, "bili"} or source_key in _BILIBILI_SOURCE_KEYS:
        return _BILIBILI_SOURCE_FAMILY
    return raw_source or "unknown"


def _normalize_source_platform_key(source_platform: object) -> str:
    """Return the canonical source key used in cross-source content IDs."""
    raw = str(source_platform or "").strip().lower()
    if raw in {_XHS_SOURCE_FAMILY, "xhs"}:
        return _XHS_SOURCE_FAMILY
    if raw in {_DOUYIN_SOURCE_FAMILY, "dy"}:
        return _DOUYIN_SOURCE_FAMILY
    if raw in {_YOUTUBE_SOURCE_FAMILY, "yt"}:
        return _YOUTUBE_SOURCE_FAMILY
    if raw in {_TWITTER_SOURCE_FAMILY, "x"}:
        return _TWITTER_SOURCE_FAMILY
    if raw in {_BILIBILI_SOURCE_FAMILY, "bili"}:
        return _BILIBILI_SOURCE_FAMILY
    return raw


def _normalize_style_key_for_storage(value: object) -> str:
    """Canonicalize known style_key values while preserving unknown legacy rows."""
    token = re.sub(r"[\s-]+", "_", str(value or "").strip().lower())
    if not token:
        return ""
    return _LEGACY_STYLE_KEY_MAP.get(token, token)


def _is_linkable_pool_source(
    source: object,
    source_platform: object,
    content_url: object,
) -> bool:
    """Return False for xhs rows that cannot be opened from recommendations."""
    if _pool_source_family(source, source_platform) != _XHS_SOURCE_FAMILY:
        return True
    return "xsec_token=" in str(content_url or "")


def _xhs_self_author_guard_sql(table_alias: str = "content_cache") -> str:
    """Return a SQL AND clause that excludes self-authored XHS rows.

    The clause takes 3 positional ``?`` parameters (all the same nickname
    string). When the nickname is empty the clause is a no-op.
    """
    prefix = f"{table_alias}." if table_alias else ""
    return (
        "AND ("
        "? = '' "
        f"OR COALESCE({prefix}source_platform, '') != 'xiaohongshu' "
        "OR ("
        f"LOWER(COALESCE({prefix}up_name, '')) != LOWER(?) "
        f"AND LOWER(COALESCE({prefix}author_name, '')) != LOWER(?)"
        ")"
        ")"
    )


def _xhs_self_author_guard_params(xhs_self_nickname: str | None) -> tuple[str, str, str]:
    """Return the 3 bind values for ``_xhs_self_author_guard_sql``."""
    nickname = str(xhs_self_nickname or "").strip()
    return (nickname, nickname, nickname)


def _normalize_admission_min_score(value: object) -> float:
    if isinstance(value, bool):
        return _DEFAULT_ADMISSION_MIN_SCORE
    if not isinstance(value, (int, float, str)):
        return _DEFAULT_ADMISSION_MIN_SCORE
    try:
        score = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_ADMISSION_MIN_SCORE
    if score <= 0.0 or score > 1.0:
        return _DEFAULT_ADMISSION_MIN_SCORE
    return score


class Database(ViewHistoryMixin, QualityMixin, PruneMixin, PoolCandidateMixin, TopicMixin, NativeSyncMixin, WatchLaterMixin, DelightMixin, SourceRecipeMixin, CoverMixin, ArticleMixin, FavoritesMixin, UserFeedbackMixin, RecommendationMixin, DiscoveryCandidatesMixin, ContentCacheMixin, ChatTurnMixin, EventsMixin, LLMUsageMixin):
    """Lightweight SQLite wrapper for OpenBiliClaw.

    Manages the event log, content cache, and recommendation history.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        # 推荐流子库：content_cache/recommendations/user_feedback/xhs_observed_urls
        # 独立存放于 pool.db（总库+子库）。所有 Database 连接 ATTACH 该子库，
        # 无前缀 SQL 自动落到 pool schema（主库已不含这些表）。
        self._pool_db_path = self._db_path.with_name("pool.db")
        self._conn: sqlite3.Connection | None = None
        # v0.3.x: per-thread connection slot. The same Database instance is
        # now touched from more than one OS thread — the FastAPI request
        # event-loop thread AND the dedicated background-refresh thread (plus
        # any asyncio.to_thread executor threads). A single shared sqlite3
        # connection is NOT safe to use concurrently from multiple threads,
        # so each thread lazily opens its own connection to the same file.
        # SQLite serializes writers via WAL + busy_timeout, so cross-thread
        # reads/writes stay consistent without sharing a connection object.
        self._thread_local = threading.local()
        self._admission_min_score = _DEFAULT_ADMISSION_MIN_SCORE
        # count_pool_readiness 短期缓存（300秒）：该函数做 4~5 次查询 +
        # events 表 2000 行 JSON 解析，冷算约 2~4s；available/raw/pending 是
        # 库存概览数，5 分钟新鲜度足够。配合 allow_stale 后接口永不阻塞冷算。
        self._pool_readiness_cache: tuple[float, dict[str, int]] | None = None
        self._pool_readiness_cache_ttl = 300.0
        self._pool_readiness_refreshing = False

    def set_admission_min_score(self, value: object) -> None:
        """Set the unified recommendation-pool admission floor."""
        self._admission_min_score = _normalize_admission_min_score(value)

    # ── 推荐流子库（pool.db）──────────────────────────────────────────
    # 主库连接 ATTACH pool.db；content_cache/recommendations/user_feedback/
    # xhs_observed_urls 只存在于子库，无前缀 SQL 自动解析到 pool schema，
    # 与主库（events/日记/阅读库等）完全隔离锁域。

    @staticmethod
    def _extract_create_table_sql(script: str, table_name: str) -> str:
        """Extract ``CREATE TABLE IF NOT EXISTS <table_name> (...)`` from a SQL script."""
        import re

        m = re.search(
            rf"CREATE TABLE IF NOT EXISTS {re.escape(table_name)} \([^;]*\);",
            script,
            re.DOTALL,
        )
        return m.group(0) if m else ""

    def _ensure_pool_database(self) -> None:
        """Ensure the recommendation sub-database (pool.db) exists.

        Normal path: migration already created pool.db (total+sub DB split).
        Fallback: pool.db missing — copy the four pool tables from the main
        DB if they still exist there, otherwise create minimal empty tables
        so the system boots instead of crashing.
        """
        if self._pool_db_path.exists():
            return
        import sqlite3 as _sqlite3

        pool_conn = _sqlite3.connect(str(self._pool_db_path), timeout=30.0)
        try:
            # 从主库完整 schema 提取 4 张推荐流表的 DDL（与生产结构一致），
            # 保证测试/全新环境后续 ALTER/UPDATE 不缺列。
            pool_conn.executescript(
                "\n".join(
                    ddl
                    for ddl in (
                        self._extract_create_table_sql(_SCHEMA_SQL, "content_cache"),
                        self._extract_create_table_sql(_SCHEMA_SQL, "recommendations"),
                        self._extract_create_table_sql(_XHS_OBSERVED_URLS_DDL, "xhs_observed_urls"),
                        self._extract_create_table_sql(_USER_FEEDBACK_DDL, "user_feedback"),
                    )
                    if ddl
                ).replace("pool.", "")
            )
            pool_conn.commit()
            self._logger().warning(
                "pool.db 不存在，已按完整 schema 创建空推荐流子库。"
                "若主库存在旧 content_cache，请先执行 scripts/migrate_pool_db.py。"
            )
        finally:
            pool_conn.close()

    def _attach_pool(self, conn: sqlite3.Connection) -> None:
        """ATTACH the recommendation sub-database to a connection (idempotent)."""
        with suppress(sqlite3.OperationalError):
            conn.execute("ATTACH DATABASE ? AS pool", (str(self._pool_db_path),))

    def _logger(self):
        import logging

        return logging.getLogger(__name__)

    def initialize(self) -> None:
        """Initialize the database and run migrations if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        # 增加页面缓存到 64MB（负数表示 KB），减少磁盘 IO，提升查询性能
        self._conn.execute("PRAGMA cache_size = -65536")
        # 提升 WAL 检查点阈值，减少频繁检查点
        self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
        # 推荐流子库：确保 pool.db 存在后 ATTACH，使无前缀 SQL 落到 pool schema
        self._ensure_pool_database()
        self._attach_pool(self._conn)
        # Bind the primary connection to the initializing thread so it is
        # reused (not duplicated) by later `self.conn` accesses on this thread.
        self._thread_local.conn = self._conn
        self._conn.executescript(_SCHEMA_SQL)
        self._ensure_event_satisfaction_columns()
        self._ensure_recommendation_feedback_columns()
        self._ensure_recommendation_clicked_column()
        self._ensure_content_cache_runtime_columns()
        self._ensure_content_cache_relevance_columns()
        self._ensure_content_cache_topic_columns()
        self._ensure_content_cache_pool_copy_columns()
        self._ensure_content_cache_delight_columns()
        self._ensure_content_cache_quality_columns()
        self._ensure_content_cache_multisource_columns()
        self._ensure_recommendation_read_indexes()
        self._ensure_event_read_indexes()
        self._ensure_content_cache_read_indexes()
        self._ensure_source_recipes_table()
        self._ensure_xhs_observed_urls_table()
        self._ensure_discovery_candidate_columns()
        self._normalize_legacy_style_keys()
        self._ensure_llm_usage_cache_columns()
        self._ensure_chat_turns_table()
        self._ensure_watch_later_table()
        self._ensure_discovery_keywords_table()
        self._ensure_favorites_table()
        self._ensure_saved_sync_tables()
        self._ensure_auth_state_table()
        self._ensure_init_runs_table()
        self._ensure_user_feedback_table()
        self._ensure_view_history_table()
        self._ensure_topic_tables()
        self._ensure_knowledge_forge_tables()
        self.reset_stale_discovery_candidate_evaluations()
        self.suppress_low_score_pool_items()
        self.suppress_low_confidence_recommendations()

        # Set schema version
        self._conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (_SCHEMA_VERSION,),
        )
        self._conn.commit()
        logger.info("Database initialized at %s", self._db_path)

    def _ensure_saved_sync_tables(self) -> None:
        """Create the saved-sync (reading library) tables.

        Cross-platform unified saved-item management:
        - saved_items: normalized metadata for each item (shared across lists)
        - saved_memberships: membership in favorite/watch_later lists
        - native_save_states: native-sync (to platform) execution state
        - native_save_task_items: per-item membership within a sync batch
        - saved_item_removals: history of removed items for retention
        """
        from openbiliclaw.saved_sync.models import NATIVE_SAVE_STATUSES

        self.conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS saved_item_removals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_kind TEXT NOT NULL,
                item_key TEXT NOT NULL,
                source_platform TEXT NOT NULL,
                content_id TEXT NOT NULL,
                content_url TEXT NOT NULL,
                content_type TEXT NOT NULL,
                title TEXT NOT NULL,
                author_name TEXT NOT NULL,
                cover_url TEXT NOT NULL,
                removed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_saved_item_removals_removed
                ON saved_item_removals(removed_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_saved_item_removals_item
                ON saved_item_removals(item_key, removed_at DESC);

            CREATE TABLE IF NOT EXISTS saved_items (
                item_key TEXT PRIMARY KEY,
                source_platform TEXT NOT NULL,
                content_id TEXT NOT NULL,
                content_url TEXT NOT NULL,
                content_type TEXT NOT NULL,
                title TEXT NOT NULL,
                author_name TEXT NOT NULL,
                cover_url TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS saved_memberships (
                list_kind TEXT NOT NULL,
                item_key  TEXT NOT NULL REFERENCES saved_items(item_key) ON DELETE CASCADE,
                note      TEXT DEFAULT '',
                added_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (list_kind, item_key)
            );
            CREATE INDEX IF NOT EXISTS idx_saved_memberships_item_key
                ON saved_memberships(item_key);

            CREATE TABLE IF NOT EXISTS native_save_states (
                list_kind TEXT NOT NULL,
                item_key TEXT NOT NULL,
                requested_action TEXT NOT NULL,
                resolved_action TEXT NOT NULL,
                resolved_target TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ({", ".join(f"'{s}'" for s in NATIVE_SAVE_STATUSES)})),
                task_id TEXT NOT NULL,
                execution_id TEXT NOT NULL,
                last_error_code TEXT NOT NULL,
                last_error_message TEXT NOT NULL,
                last_attempt_at TIMESTAMP,
                synced_at TIMESTAMP,
                PRIMARY KEY (list_kind, item_key)
            );

            CREATE TABLE IF NOT EXISTS native_save_task_items (
                task_id TEXT NOT NULL,
                item_key TEXT NOT NULL,
                list_kind TEXT NOT NULL,
                status TEXT NOT NULL,
                is_live INTEGER NOT NULL DEFAULT 1,
                last_error_code TEXT NOT NULL,
                last_error_message TEXT NOT NULL,
                started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (task_id, item_key)
            );
            CREATE INDEX IF NOT EXISTS idx_native_save_task_items_order
                ON native_save_task_items(updated_at DESC);
        """)

    @staticmethod
    def _saved_list_kind(value: str) -> str:
        if value not in {"favorite", "watch_later"}:
            raise ValueError(f"invalid saved list kind: {value}, expected favorite/watch_later")
        return value

    def count_saved_memberships(self, list_kind: str) -> int:
        """Count items in a saved list."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM saved_memberships WHERE list_kind = ?",
            (normalized_kind,),
        ).fetchone()
        return int(row[0] if row is not None else 0)

    def get_saved_membership(self, list_kind: str, item_key: str) -> dict[str, Any] | None:
        """Return one normalized membership with its current native-sync state."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT
                m.list_kind,
                i.item_key,
                i.source_platform,
                i.content_id,
                i.content_url,
                i.content_type,
                COALESCE(NULLIF(i.title, ''), (
                    SELECT cc.title FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.title) AS title,
                COALESCE(NULLIF(i.author_name, ''), (
                    SELECT COALESCE(NULLIF(cc.up_name, ''), cc.author_name)
                    FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.author_name) AS author_name,
                COALESCE(NULLIF(i.cover_url, ''), (
                    SELECT cc.cover_url FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), '') AS cover_url,
                i.created_at,
                i.updated_at,
                m.note,
                m.added_at,
                COALESCE(n.requested_action, '') AS requested_action,
                COALESCE(n.resolved_action, '') AS resolved_action,
                COALESCE(n.resolved_target, '') AS resolved_target,
                COALESCE(n.status, 'pending') AS sync_status,
                COALESCE(n.task_id, '') AS sync_task_id,
                COALESCE(n.last_error_code, '') AS last_error_code,
                COALESCE(n.last_error_message, '') AS last_error_message,
                n.last_attempt_at,
                n.synced_at
            FROM saved_memberships AS m
            JOIN saved_items AS i ON i.item_key = m.item_key
            LEFT JOIN native_save_states AS n
                ON n.list_kind = m.list_kind AND n.item_key = m.item_key
            WHERE m.list_kind = ? AND m.item_key = ?
            """,
            (normalized_kind, item_key.strip()),
        ).fetchone()
        return dict(row) if row is not None else None

    def list_saved_memberships(
        self,
        list_kind: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List normalized memberships newest first with native-sync state."""
        normalized_kind = self._saved_list_kind(list_kind)
        self._ensure_fresh_read()
        rows = self.conn.execute(
            """
            SELECT
                m.list_kind,
                i.item_key,
                i.source_platform,
                i.content_id,
                i.content_url,
                i.content_type,
                COALESCE(NULLIF(i.title, ''), (
                    SELECT cc.title FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.title) AS title,
                COALESCE(NULLIF(i.author_name, ''), (
                    SELECT COALESCE(NULLIF(cc.up_name, ''), cc.author_name)
                    FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), i.author_name) AS author_name,
                COALESCE(NULLIF(i.cover_url, ''), (
                    SELECT cc.cover_url FROM content_cache cc
                    WHERE cc.bvid = i.content_id OR cc.content_id = i.content_id
                    LIMIT 1
                ), '') AS cover_url,
                i.created_at,
                i.updated_at,
                m.note,
                m.added_at,
                COALESCE(n.requested_action, '') AS requested_action,
                COALESCE(n.resolved_action, '') AS resolved_action,
                COALESCE(n.resolved_target, '') AS resolved_target,
                COALESCE(n.status, 'pending') AS sync_status,
                COALESCE(n.task_id, '') AS sync_task_id,
                COALESCE(n.last_error_code, '') AS last_error_code,
                COALESCE(n.last_error_message, '') AS last_error_message,
                n.last_attempt_at,
                n.synced_at
            FROM saved_memberships AS m
            JOIN saved_items AS i ON i.item_key = m.item_key
            LEFT JOIN native_save_states AS n
                ON n.list_kind = m.list_kind AND n.item_key = m.item_key
            WHERE m.list_kind = ?
            ORDER BY m.added_at DESC, m.item_key ASC
            LIMIT ? OFFSET ?
            """,
            (normalized_kind, limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]

    def upsert_saved_membership(
        self,
        list_kind: str,
        item: Any,  # SavedItemInput
        note: str = "",
    ) -> dict[str, Any]:
        """Atomically upsert an item snapshot and its local list membership."""
        normalized_kind = self._saved_list_kind(list_kind)
        item_key = item.item_key
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO saved_items (
                    item_key, source_platform, content_id, content_url, content_type,
                    title, author_name, cover_url
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_key) DO UPDATE SET
                    source_platform = excluded.source_platform,
                    content_id = excluded.content_id,
                    content_url = excluded.content_url,
                    content_type = excluded.content_type,
                    title = excluded.title,
                    author_name = excluded.author_name,
                    cover_url = excluded.cover_url,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    item_key,
                    item.platform,
                    item.content_id.strip(),
                    item.content_url.strip(),
                    item.content_type.strip() or "video",
                    item.title.strip(),
                    item.author_name.strip(),
                    item.cover_url.strip(),
                ),
            )
            conn.execute(
                """
                INSERT INTO saved_memberships (list_kind, item_key, note)
                VALUES (?, ?, ?)
                ON CONFLICT(list_kind, item_key) DO UPDATE SET
                    note = excluded.note,
                    added_at = CURRENT_TIMESTAMP
                """,
                (normalized_kind, item_key, note),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        row = self.get_saved_membership(normalized_kind, item_key)
        if row is None:
            raise RuntimeError("saved membership disappeared after upsert")
        return row

    def remove_saved_membership(self, list_kind: str, item_key: str) -> bool:
        """Remove a normalized membership and any matching legacy compatibility row."""
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        legacy_table = "favorites" if normalized_kind == "favorite" else "watch_later"
        legacy_bvid = (
            normalized_key.removeprefix("bilibili:")
            if normalized_key.startswith("bilibili:")
            else None
        )
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            removed_snapshot = conn.execute(
                """
                SELECT m.list_kind, i.item_key, i.source_platform, i.content_id, i.content_url, i.content_type
                FROM saved_memberships AS m
                JOIN saved_items AS i ON i.item_key = m.item_key
                WHERE m.list_kind = ? AND m.item_key = ?
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            active_state = conn.execute(
                """
                SELECT task_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                  AND status IN ('pending', 'syncing') AND task_id != ''
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            if active_state is not None:
                conn.execute(
                    """
                    UPDATE native_save_task_items
                    SET status = 'failed', is_live = 0,
                        last_error_code = 'not_saved_locally',
                        last_error_message = 'Item is not saved locally',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE task_id = ? AND item_key = ? AND is_live = 1
                      AND status IN ('pending', 'syncing')
                    """,
                    (str(active_state["task_id"]), normalized_key),
                )
            cursor = conn.execute(
                "DELETE FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            )
            removed = int(cursor.rowcount or 0) > 0
            if removed and removed_snapshot is not None:
                conn.execute(
                    """
                    INSERT INTO saved_item_removals (
                        list_kind, item_key, source_platform, content_id,
                            content_url, content_type, title, author_name, cover_url
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_kind,
                        normalized_key,
                        str(removed_snapshot["source_platform"] or ""),
                        str(removed_snapshot["content_id"] or ""),
                        str(removed_snapshot["content_url"] or ""),
                        str(removed_snapshot["content_type"] or "video"),
                        str(removed_snapshot["title"] or ""),
                        str(removed_snapshot["author_name"] or ""),
                        str(removed_snapshot["cover_url"] or ""),
                    ),
                )
            conn.execute(
                """
                DELETE FROM saved_item_removals
                WHERE removed_at < datetime('now', '-30 days')
                """,
            )
            direct_bilibili_clause = "bvid = ? OR" if legacy_bvid else ""
            legacy_params = (legacy_bvid, normalized_key) if legacy_bvid else (normalized_key,)
            legacy_cursor = conn.execute(
                f"""
                DELETE FROM {legacy_table}
                WHERE {direct_bilibili_clause} item_key = ?
                """,
                legacy_params,
            )
            removed = removed or int(legacy_cursor.rowcount or 0) > 0
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        return removed

    def ensure_native_save_state(
        self,
        list_kind: str,
        item_key: str,
        requested_action: str,
    ) -> dict[str, Any]:
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """
                SELECT status, task_id
                FROM native_save_states
                WHERE list_kind = ? AND item_key = ?
                """,
                (normalized_kind, normalized_key),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO native_save_states (
                        list_kind, item_key, requested_action, resolved_action, resolved_target,
                        status, task_id, execution_id, last_error_code, last_error_message
                    ) VALUES (?, ?, ?, ?, ?, 'pending', '', '', '', '')
                    """,
                    (normalized_kind, normalized_key, requested_action, "", ""),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT status, task_id FROM native_save_states
            WHERE list_kind = ? AND item_key = ?
            """,
            (normalized_kind, normalized_key),
        ).fetchone()
        return dict(row) if row is not None else {"status": "pending", "task_id": ""}

    def upsert_native_save_state(
        self,
        list_kind: str,
        item_key: str,
        requested_action: str,
        resolved_action: str = "",
        resolved_target: str = "",
        status: str = "pending",
        task_id: str = "",
        execution_id: str = "",
        last_error_code: str = "",
        last_error_message: str = "",
    ) -> None:
        normalized_kind = self._saved_list_kind(list_kind)
        normalized_key = item_key.strip()
        normalized_task_id = task_id.strip()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            membership = conn.execute(
                "SELECT 1 FROM saved_memberships WHERE list_kind = ? AND item_key = ?",
                (normalized_kind, normalized_key),
            ).fetchone()
            if membership is None:
                raise ValueError(
                    f"saved membership does not exist: {normalized_kind}/{normalized_key}"
                )
            conn.execute(
                """
                INSERT INTO native_save_states (
                    list_kind, item_key, requested_action, resolved_action, resolved_target,
                    status, task_id, execution_id, last_error_code, last_error_message,
                    last_attempt_at, synced_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CASE WHEN ? = 'pending' THEN NULL ELSE CURRENT_TIMESTAMP END,
                    CASE WHEN ? IN ('synced', 'already_synced')
                        THEN CURRENT_TIMESTAMP ELSE NULL END
                )
                ON CONFLICT(list_kind, item_key) DO UPDATE SET
                    requested_action = excluded.requested_action,
                    resolved_action = excluded.resolved_action,
                    resolved_target = excluded.resolved_target,
                    status = excluded.status,
                    task_id = excluded.task_id,
                    execution_id = excluded.execution_id,
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message,
                    last_attempt_at = CASE
                        WHEN excluded.status = 'pending' THEN native_save_states.last_attempt_at
                        ELSE CURRENT_TIMESTAMP
                    END,
                    synced_at = CASE
                        WHEN excluded.status IN ('synced', 'already_synced')
                            THEN CURRENT_TIMESTAMP
                        ELSE native_save_states.synced_at
                    END
                """,
                (
                    normalized_kind,
                    normalized_key,
                    requested_action,
                    resolved_action,
                    resolved_target,
                    status,
                    normalized_task_id,
                    execution_id,
                    last_error_code,
                    last_error_message,
                    status,
                    status,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        self._ensure_fresh_read()

    def count_favorites_legacy(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM favorites").fetchone()[0])

    def count_watch_later_legacy(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM watch_later").fetchone()[0])

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        # Per-thread connection (see __init__). Lazily opened on first access
        # from any thread other than the initializing one; the initializing
        # thread reuses the primary connection bound in initialize().
        local_conn = getattr(self._thread_local, "conn", None)
        if local_conn is None:
            local_conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
            local_conn.row_factory = sqlite3.Row
            local_conn.execute("PRAGMA journal_mode=WAL")
            local_conn.execute("PRAGMA busy_timeout = 30000")
            # 增加页面缓存到 64MB，减少磁盘 IO
            local_conn.execute("PRAGMA cache_size = -65536")
            self._attach_pool(local_conn)
            self._thread_local.conn = local_conn
        return local_conn

    def _pool_admission_min_score(self) -> float:
        return _normalize_admission_min_score(self._admission_min_score)

    def _admission_predicate_sql(
        self,
        score_expr: str = "COALESCE(relevance_score, 0.0)",
    ) -> tuple[str, tuple[Any, ...]]:
        """Return a SQL predicate and params for the shared admission policy.

        ``explore``-source candidates get a lower floor (``_EXPLORE_STRATEGY``
        content is intentionally far from the profile, so its relevance score
        runs systematically low — a uniform floor would bar the whole explore
        pool from service). Everything else keeps the configured admission
        score. Mirrors upstream ``_pool_admission_sql``.
        """
        predicate = f"""
            {score_expr} >= CASE
                WHEN LOWER(TRIM(COALESCE(source, ''))) = ? THEN ?
                ELSE ?
            END
        """
        return predicate, (
            _EXPLORE_STRATEGY,
            _EXPLORE_ADMISSION_MIN_SCORE,
            self._pool_admission_min_score(),
        )

    def open_connection(self) -> sqlite3.Connection:
        """Open a short-lived connection to the initialized database.

        Use this for explicit transactions that may run from FastAPI's
        threadpool. A separate connection lets SQLite serialize writers
        with ``busy_timeout`` instead of nesting transactions on the
        process-wide connection.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        self._attach_pool(conn)
        return conn

    def _ensure_fresh_read(self) -> None:
        """Close any implicit transaction so the next SELECT sees the latest WAL state.

        When a CLI command (a separate process) writes to the same database,
        this server process may still hold a stale read snapshot inside an
        implicit transaction.  Committing closes that transaction so the next
        query starts a new one against the current WAL head.
        """
        if self.conn.in_transaction:
            self.conn.commit()

    def _execute_write(
        self,
        sql: str,
        params: tuple[Any, ...] | list[Any] = (),
    ) -> sqlite3.Cursor:
        """Execute a write with short retry on transient SQLite locks."""
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.execute(sql, params)
                self.conn.commit()
                # 写操作后失效 pool_readiness 缓存
                self._pool_readiness_cache = None
                return cursor
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite write locked, retrying (%s attempts left): %s",
                    attempts,
                    sql.splitlines()[0].strip() if sql.strip() else "<empty-sql>",
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def _execute_many_write(
        self,
        sql: str,
        seq_of_params: Sequence[tuple[Any, ...] | list[Any]],
    ) -> sqlite3.Cursor:
        """Batch-execute a write with the same transient-lock retry as ``_execute_write``."""
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.executemany(sql, seq_of_params)
                self.conn.commit()
                return cursor
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                if "database is locked" not in message or attempts <= 1:
                    raise
                attempts -= 1
                logger.warning(
                    "SQLite batch write locked, retrying (%s attempts left): %s",
                    attempts,
                    sql.splitlines()[0].strip() if sql.strip() else "<empty-sql>",
                )
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    # ------------------------------------------------------------------
    # LLM usage ledger
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_source_keyword_id(value: Any) -> int | None:
        """Normalize a ``source_keyword_id`` kwarg to ``int`` or ``None``.

        Tolerates the field being absent / blank / non-numeric so any caller
        that has not been threaded through the P1.8 provenance path stays a
        plain NULL write (no behavior change vs. the pre-P1.8 schema).
        """
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def get_unrecommended_content(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get cached content that has not been recommended yet."""
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT c.*
            FROM content_cache AS c
            WHERE COALESCE(c.relevance_score, 0.0) >= ?
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = c.bvid
            )
            ORDER BY
                CASE c.candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                c.relevance_score DESC,
                c.last_scored_at DESC,
                c.view_count DESC,
                c.bvid ASC
            LIMIT ?
            """,
            (min_score, max(limit * 5, 50)),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return self._balance_pool_rows(rows, limit=limit)

    def suppress_low_score_pool_items(self, min_score: float | None = None) -> int:
        """Suppress cached pool rows below the unified admission floor."""
        threshold = (
            self._pool_admission_min_score()
            if min_score is None
            else _normalize_admission_min_score(min_score)
        )
        cursor = self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE COALESCE(relevance_score, 0.0) < ?
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
            """,
            (threshold,),
        )
        return int(cursor.rowcount or 0)

    def suppress_low_confidence_recommendations(self, min_score: float | None = None) -> int:
        """Mark old low-confidence recommendation rows as suppressed."""
        threshold = (
            self._pool_admission_min_score()
            if min_score is None
            else _normalize_admission_min_score(min_score)
        )
        cursor = self._execute_write(
            """
            UPDATE recommendations
            SET feedback_type = 'suppressed_low_score'
            WHERE COALESCE(confidence, 0.0) < ?
              AND COALESCE(feedback_type, '') = ''
            """,
            (threshold,),
        )
        return int(cursor.rowcount or 0)

    def get_latest_event_id(self) -> int:
        """Return the latest event primary key."""
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS latest_id FROM events")
        row = cursor.fetchone()
        return int(row["latest_id"]) if row is not None else 0

    def query_events_since(
        self,
        *,
        after_event_id: int,
        event_types: list[str],
    ) -> list[dict[str, Any]]:
        """Query events newer than a given id for selected event types."""
        if not event_types:
            return []
        placeholders = ", ".join("?" for _ in event_types)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM events
            WHERE id > ? AND event_type IN ({placeholders})
            ORDER BY id ASC
            """,
            [after_event_id, *event_types],
        )
        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the database connection(s)."""
        local_conn = getattr(self._thread_local, "conn", None)
        if local_conn is not None and local_conn is not self._conn:
            with suppress(Exception):
                local_conn.close()
            self._thread_local.conn = None
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_llm_usage_cache_columns(self) -> None:
        """Backfill v0.3.28+ prompt-cache columns on existing llm_usage tables."""
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(llm_usage)").fetchall()
        }
        required_columns = {
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE llm_usage ADD COLUMN {column_name} {column_type}")

    def _ensure_event_satisfaction_columns(self) -> None:
        """Backfill v0.3.x event-satisfaction columns for pre-migration DBs.

        Existing rows keep ``NULL`` in both columns; consumers treat NULL
        as ``unknown`` so the upgrade is non-blocking.
        """
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(events)").fetchall()
        }
        required_columns = {
            "inferred_satisfaction": "TEXT",
            "satisfaction_reason": "TEXT",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE events ADD COLUMN {column_name} {column_type}")

    def _ensure_recommendation_feedback_columns(self) -> None:
        """Backfill recommendation feedback columns for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(recommendations)").fetchall()
        }
        required_columns = {
            "feedback_type": "TEXT",
            "feedback_note": "TEXT",
            "feedback_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE recommendations ADD COLUMN {column_name} {column_type}")

    def _ensure_recommendation_clicked_column(self) -> None:
        """Backfill the recommendation click-through column for existing DBs.

        E1 (real-time feedback loop): a click is the strongest consumption
        signal — it closes the exposure→click loop so clicked items stop
        being re-served and the history table carries true CTR data.
        """
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(recommendations)").fetchall()
        }
        if "clicked_at" in existing_columns:
            return
        self.conn.execute("ALTER TABLE pool.recommendations ADD COLUMN clicked_at TIMESTAMP")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS pool.idx_recommendations_clicked ON recommendations(clicked_at)"
        )

    def _ensure_content_cache_runtime_columns(self) -> None:
        """Backfill content-cache runtime columns for continuous refresh."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "last_scored_at": "TIMESTAMP",
            "notification_sent": "INTEGER DEFAULT 0",
            "notified_at": "TIMESTAMP",
            "pool_status": "TEXT DEFAULT 'fresh'",
            "recommended_at": "TIMESTAMP",
            "feedback_type": "TEXT",
            "feedback_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_relevance_columns(self) -> None:
        """Backfill relevance fields for existing content-cache rows."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "relevance_score": "REAL DEFAULT 0.0",
            "relevance_reason": "TEXT DEFAULT ''",
            "candidate_tier": "TEXT DEFAULT 'primary'",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_topic_columns(self) -> None:
        """Backfill topic bucketing fields for existing content-cache rows."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        if "topic_key" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN topic_key TEXT DEFAULT ''")
        if "topic_group" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN topic_group TEXT DEFAULT ''")
        if "style_key" not in existing_columns:
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN style_key TEXT DEFAULT ''")
        if "franchise_key" not in existing_columns:
            # v0.3.18: LLM-tagged IP / franchise / series. Empty string for
            # general-interest content non-empty rows let the curator
            # propagate dislikes within an IP and let
            # /api/recommendations cap how many same-franchise items
            # appear in a single response window — without relying on
            # any title-string heuristic or hardcoded alias list.
            self.conn.execute("ALTER TABLE content_cache ADD COLUMN franchise_key TEXT DEFAULT ''")

    def _ensure_content_cache_pool_copy_columns(self) -> None:
        """Backfill precomputed pool-copy fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "pool_expression": "TEXT DEFAULT ''",
            "pool_topic_label": "TEXT DEFAULT ''",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_delight_columns(self) -> None:
        """Backfill proactive delight scoring fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "delight_score": "REAL DEFAULT 0.0",
            "delight_reason": "TEXT DEFAULT ''",
            "delight_hook": "TEXT DEFAULT ''",
            "delight_notified": "INTEGER DEFAULT 0",
            "delight_notified_at": "TIMESTAMP",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_quality_columns(self) -> None:
        """Backfill LLM quality scoring fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "quality_score": "REAL DEFAULT 0.0",
            "quality_reason": "TEXT DEFAULT ''",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")

    def _ensure_content_cache_multisource_columns(self) -> None:
        """Add multi-source content identity fields for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(content_cache)").fetchall()
        }
        required_columns = {
            "content_id": "TEXT DEFAULT ''",
            "content_url": "TEXT DEFAULT ''",
            "source_platform": "TEXT DEFAULT 'bilibili'",
            "author_name": "TEXT DEFAULT ''",
            "body_text": "TEXT DEFAULT ''",
            "content_type": "TEXT DEFAULT 'video'",
            "favorite_count": "INTEGER DEFAULT 0",
            "collect_count": "INTEGER DEFAULT 0",
            "comment_count": "INTEGER DEFAULT 0",
            "share_count": "INTEGER DEFAULT 0",
            "danmaku_count": "INTEGER DEFAULT 0",
            "reply_count": "INTEGER DEFAULT 0",
            "retweet_count": "INTEGER DEFAULT 0",
            "bookmark_count": "INTEGER DEFAULT 0",
            # P1.8 yield provenance: the discovery_keywords.id that produced this
            # row (NULL for legacy / non-search / flag-off). Nullable, additive.
            "source_keyword_id": "INTEGER",
        }
        added = False
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE content_cache ADD COLUMN {column_name} {column_type}")
            added = True
        if added:
            self.conn.execute("UPDATE content_cache SET content_id = bvid WHERE content_id = ''")

    def _ensure_discovery_candidate_columns(self) -> None:
        """Backfill discovery-candidate lifecycle columns for existing databases."""
        existing_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(discovery_candidates)").fetchall()
        }
        required_columns = {
            "score_threshold": "REAL NOT NULL DEFAULT 0.0",
            "eval_attempts": "INTEGER NOT NULL DEFAULT 0",
            "batch_eval_attempts": "INTEGER NOT NULL DEFAULT 0",
            "body_text": "TEXT NOT NULL DEFAULT ''",
            "favorite_count": "INTEGER NOT NULL DEFAULT 0",
            "collect_count": "INTEGER NOT NULL DEFAULT 0",
            "comment_count": "INTEGER NOT NULL DEFAULT 0",
            "share_count": "INTEGER NOT NULL DEFAULT 0",
            "danmaku_count": "INTEGER NOT NULL DEFAULT 0",
            "reply_count": "INTEGER NOT NULL DEFAULT 0",
            "retweet_count": "INTEGER NOT NULL DEFAULT 0",
            "bookmark_count": "INTEGER NOT NULL DEFAULT 0",
            # P1.8 yield provenance: nullable, additive (existing rows stay NULL).
            "source_keyword_id": "INTEGER",
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(
                f"ALTER TABLE discovery_candidates ADD COLUMN {column_name} {column_type}"
            )

    def _normalize_legacy_style_keys(self) -> None:
        """Rewrite known legacy content-form style keys to viewing-mode keys."""
        targets = (
            ("content_cache", "style_key"),
            ("discovery_candidates", "style_key"),
        )
        for table_name, column_name in targets:
            existing_columns = {
                str(row["name"])
                for row in self.conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            }
            if column_name not in existing_columns:
                continue
            for legacy_key, style_key in _LEGACY_STYLE_KEY_MAP.items():
                self.conn.execute(
                    f"UPDATE {table_name} SET {column_name} = ? WHERE {column_name} = ?",
                    (style_key, legacy_key),
                )

    def _ensure_recommendation_read_indexes(self) -> None:
        """Create indexes used by recommendation and activity-feed reads."""
        # ``idx_recommendations_bvid`` is load-bearing for pool latency: the
        # candidate-pool queries filter with
        #   NOT EXISTS (SELECT 1 FROM recommendations AS r
        #               WHERE r.bvid = content_cache.bvid)
        # in ~22 places (get_pool_candidates, count_pool_candidates,
        # count_pool_readiness, replenishment checks, ...). Without this index
        # that subquery degrades to a full scan of ``recommendations`` for
        # EVERY row of ``content_cache`` — O(content_cache x recommendations)
        # work, ~31M row visits at current sizes and growing quadratically.
        # Measured on the live DB: pool query 131ms -> 8.5ms (platform-filtered
        # 85ms -> 6.6ms).
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS pool.idx_recommendations_created_id ON recommendations (created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS pool.idx_recommendations_bvid ON recommendations (bvid);
        """)
        # pool.content_cache 在旧版本/纯 pool 连接中可能没有 content_id 列，
        # 建索引前先检查列存在性，避免 no such column 阻断整个 initialize。
        try:
            pool_cols = {
                str(row["name"])
                for row in self.conn.execute("PRAGMA pool.table_info(content_cache)").fetchall()
            }
            if "content_id" in pool_cols:
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS pool.idx_content_cache_content_id ON content_cache (content_id)"
                )
        except Exception:  # noqa: BLE001 — 索引缺失不阻断启动
            pass

    def _ensure_event_read_indexes(self) -> None:
        """Create indexes for the high-volume ``events`` table.

        ``events`` is by far the largest table (816k+ rows at time of writing)
        and shipped with no indexes at all, so every read was a full scan plus
        a sort:

        * ``SELECT * FROM events ORDER BY created_at DESC LIMIT ?``
          (recent-events feed) — 249ms of scanning on every call.
        * ``SELECT event_type, COUNT(*) FROM events GROUP BY event_type``
          (event-type breakdown) — 198ms.

        ``events.id`` needs no index: it is the INTEGER PRIMARY KEY, so
        ``id > ?`` / ``MAX(id)`` already resolve via the rowid B-tree.
        Measured after adding these: 249ms -> 0.1ms and 198ms -> 33ms.

        ``source_platform`` is stored only inside the ``metadata`` JSON, so any
        GROUP BY / WHERE on it forced a full 815k-row scan + JSON parse — the
        source-share suggestion endpoint blocked the event loop ~1s every time
        it opened. Materialize it as a VIRTUAL generated column and index it:
        the ALTER is instant (no table rewrite) and the index collapses the
        platform count to ~50ms and point lookups to <1ms. VIRTUAL (not
        STORED) is required here — SQLite forbids ``ADD COLUMN ... STORED`` via
        ``ALTER TABLE``.
        """
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_events_created_at
                ON events (created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_events_event_type
                ON events (event_type);
        """)
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(events)").fetchall()}
        if "source_platform" not in cols:
            try:
                self.conn.execute(
                    "ALTER TABLE events ADD COLUMN source_platform TEXT "
                    "GENERATED ALWAYS AS "
                    "(COALESCE(json_extract(metadata, '$.source_platform'), 'unknown')) VIRTUAL"
                )
            except sqlite3.OperationalError as exc:
                # Tolerate a concurrent initialize() (e.g. a leftover process
                # still booting when pm2 starts a replacement) that already
                # added the column — the race would otherwise abort startup
                # with "duplicate column name".
                if "duplicate column" not in str(exc).lower():
                    raise
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_source_platform ON events (source_platform)"
        )
        # Composite index for event-stats aggregation (observability: GROUP BY event_type, source_platform, inferred_satisfaction)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_agg_stats ON events (event_type, source_platform, inferred_satisfaction)"
        )

    def _ensure_content_cache_read_indexes(self) -> None:
        """Create indexes for content_cache columns used in observability / read queries.

        The observability endpoint runs multiple GROUP BY / filter queries on
        ``content_cache`` (``source_platform``, ``pool_status``, ``topic_group``,
        ``feedback_type``, ``style_key``). Without indexes each query scans the
        full table, multiplying the wall-clock time by the number of GROUP BY queries.

        ``pool_status`` is also heavily used by the recommendation engine
        (``WHERE pool_status = 'fresh'`` etc.), so the index benefits more than
        just the observability page.

        防御性：旧版本/纯 pool 连接的 content_cache 可能缺部分列，逐个创建索引，
        缺列的跳过而不是阻断整个 initialize。
        """
        # 先收集 pool.content_cache 的现有列
        try:
            pool_cols = {
                str(row["name"])
                for row in self.conn.execute("PRAGMA pool.table_info(content_cache)").fetchall()
            }
        except Exception:  # noqa: BLE001
            pool_cols = set()

        index_defs = [
            ("pool.idx_content_cache_pool_status", ["pool_status"]),
            ("pool.idx_content_cache_source_platform", ["source_platform", "pool_status"]),
            # v0.3.19x: pool 浏览（/api/pool/all?source=…）按 source 过滤时
            # 之前全表 SCAN 75244 行取 rowid，1.4s；此索引后走索引查找毫秒级。
            ("pool.idx_content_cache_source", ["source"]),
            ("pool.idx_content_cache_topic_group", ["topic_group"]),
            ("pool.idx_content_cache_feedback_type", ["feedback_type"]),
            ("pool.idx_content_cache_style_key", ["style_key"]),
        ]
        for idx_name, cols in index_defs:
            if not all(c in pool_cols for c in cols):
                continue  # 旧库缺列，跳过该索引
            try:
                self.conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON pool.content_cache ({', '.join(cols)})"
                )
            except Exception:  # noqa: BLE001 — 单个索引失败不阻断启动
                pass

    def _ensure_source_recipes_table(self) -> None:
        """Create the source_recipes table if it does not exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS source_recipes (
                id            TEXT PRIMARY KEY,
                source_type   TEXT NOT NULL,
                name          TEXT NOT NULL,
                strategy      TEXT NOT NULL,
                config        TEXT DEFAULT '{}',
                target_share  INTEGER DEFAULT 4,
                enabled       INTEGER DEFAULT 1,
                created_by    TEXT DEFAULT 'system',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_fetched_at TIMESTAMP
            );
        """)

    def _ensure_xhs_observed_urls_table(self) -> None:
        """Create the xhs_observed_urls table if it does not exist."""
        self.conn.executescript(_XHS_OBSERVED_URLS_DDL)

    def _ensure_chat_turns_table(self) -> None:
        """Create durable popup chat-turn storage for existing databases."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS chat_turns (
                turn_id       TEXT PRIMARY KEY,
                session       TEXT NOT NULL DEFAULT 'popup',
                scope         TEXT NOT NULL DEFAULT 'chat',
                subject_id    TEXT NOT NULL DEFAULT '',
                subject_title TEXT NOT NULL DEFAULT '',
                message       TEXT NOT NULL DEFAULT '',
                status        TEXT NOT NULL DEFAULT 'pending',
                reply         TEXT NOT NULL DEFAULT '',
                error         TEXT NOT NULL DEFAULT '',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_chat_turns_session_created
                ON chat_turns(session, created_at, turn_id);
            CREATE INDEX IF NOT EXISTS idx_chat_turns_scope_subject
                ON chat_turns(scope, subject_id, created_at);
        """)

    def _ensure_watch_later_table(self) -> None:
        """Create the watch_later bookmarks table for existing databases."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS watch_later (
                bvid     TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note     TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_watch_later_added
                ON watch_later(added_at DESC);
        """)

    def _ensure_discovery_keywords_table(self) -> None:
        """Create the unified search-keyword store + planner single-flight lock.

        ``discovery_keywords`` is the generation-side cache/history/yield
        ledger for the unified keyword planner (Discover backpressure
        refactor, P1). It carries the same atomic-claim + lease-reclaim
        semantics as the ``xhs_tasks`` / ``dy_tasks`` execution queues
        (``BEGIN IMMEDIATE`` claim, ``pending → claimed`` transition,
        ``claimed_at`` lease), but tracks *which words to search* rather
        than *which tabs to open*.

        The uniqueness constraint is **partial** — it only covers the
        in-flight states (``pending`` / ``claimed`` / ``executing``) so a
        word that has already been ``used`` (or ``expired``) does not block
        the planner from re-generating the same word on a later cycle once
        it has rolled out of the dedup window.

        ``discovery_planner_lock`` is a tiny CAS row used to single-flight
        the planner across loops / restarts. It is held only for *short*
        transactions (acquire → commit → run LLM unlocked → reacquire to
        write), never across the LLM call, so it cannot block other
        SQLite writers.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS discovery_keywords (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                platform          TEXT NOT NULL,
                keyword           TEXT NOT NULL,
                profile_kw_digest TEXT NOT NULL DEFAULT '',
                status            TEXT NOT NULL DEFAULT 'pending',
                created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                claimed_at        TIMESTAMP,
                executing_at      TIMESTAMP,
                used_at           TIMESTAMP,
                attempts          INTEGER NOT NULL DEFAULT 0,
                yield_count       INTEGER NOT NULL DEFAULT 0
            );
            -- Partial uniqueness: only the in-flight triplet is unique, so
            -- used/expired history never blocks re-generating the same word.
            CREATE UNIQUE INDEX IF NOT EXISTS uq_discovery_keywords_inflight
                ON discovery_keywords (platform, keyword, profile_kw_digest)
                WHERE status IN ('pending', 'claimed', 'executing');
            CREATE INDEX IF NOT EXISTS idx_discovery_keywords_status_digest
                ON discovery_keywords (platform, status, profile_kw_digest);
            CREATE INDEX IF NOT EXISTS idx_discovery_keywords_status_used
                ON discovery_keywords (platform, status, used_at);

            CREATE TABLE IF NOT EXISTS discovery_planner_lock (
                lock_name    TEXT PRIMARY KEY,
                owner        TEXT NOT NULL DEFAULT '',
                locked_until TIMESTAMP NOT NULL,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- P1.8 yield ledger. One row per (keyword, admitted content) the
            -- keyword produced. The composite primary key makes the yield
            -- backfill idempotent: a retried / out-of-order / duplicate admit
            -- of the SAME (keyword, content) is an INSERT-OR-IGNORE no-op, so
            -- ``discovery_keywords.yield_count`` is only ever bumped once per
            -- distinct produced content. Decoupled from ``used`` (P1.7).
            CREATE TABLE IF NOT EXISTS discovery_keyword_yield (
                keyword_id  INTEGER NOT NULL,
                content_id  TEXT NOT NULL,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (keyword_id, content_id)
            );
        """)

    # ── Discovery keyword store (unified search-keyword planner) ──
    #
    # Status machine:
    #   pending → claimed → (inline:    used / failed)
    #                     → (async: executing → used / failed)
    #   any in-flight state → pending (lease reclaim / budget rollback)
    #   pending (stale digest) → expired
    # ``used`` only ever lands at the terminal (never at enqueue time); the
    # word stays "in flight" until its fetch actually completes. yield_count
    # is backfilled later (P1.8) at admission time; P1.1 only stores the column.

    def insert_pending_keywords(
        self,
        platform: str,
        keywords: Sequence[str],
        profile_kw_digest: str,
    ) -> int:
        """Batch-insert ``pending`` keywords, ignoring in-flight duplicates.

        The partial unique index ``uq_discovery_keywords_inflight`` means a
        word already ``pending`` / ``claimed`` / ``executing`` for the same
        ``(platform, profile_kw_digest)`` is silently skipped (``OR IGNORE``);
        a word that is only present as ``used`` / ``expired`` history does
        **not** conflict, so the same word can be regenerated. Blank /
        duplicate words within ``keywords`` are de-duplicated up front.

        Returns the number of rows actually inserted.
        """
        platform_key = platform.strip()
        digest = profile_kw_digest.strip()
        seen: set[str] = set()
        rows: list[tuple[str, str, str]] = []
        for raw in keywords:
            word = str(raw).strip()
            if not word or word in seen:
                continue
            seen.add(word)
            rows.append((platform_key, word, digest))
        if not rows:
            return 0
        before = self.conn.total_changes
        self._execute_many_write(
            """
            INSERT OR IGNORE INTO discovery_keywords
                (platform, keyword, profile_kw_digest, status)
            VALUES (?, ?, ?, 'pending')
            """,
            rows,
        )
        return self.conn.total_changes - before

    def count_pending_keywords(self, platform: str, profile_kw_digest: str) -> int:
        """Return how many ``pending`` keywords exist for this digest."""
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM discovery_keywords
            WHERE platform = ? AND status = 'pending' AND profile_kw_digest = ?
            """,
            (platform.strip(), profile_kw_digest.strip()),
        ).fetchone()
        return int(row["n"]) if row is not None else 0

    def claim_keywords(self, platform: str, n: int) -> list[dict[str, Any]]:
        """Atomically claim up to ``n`` ``pending`` keywords for a platform.

        Uses a short-lived connection + ``BEGIN IMMEDIATE`` so two concurrent
        callers serialize and never receive overlapping rows: the second
        writer blocks until the first commits, after which the just-claimed
        rows are no longer ``pending`` and cannot be re-selected. Mirrors the
        ``xhs_tasks`` / ``dy_tasks`` ``next_pending`` claim, generalized to a
        batch. Returns the claimed rows (``status='claimed'``), oldest first.
        """
        claim_n = max(0, int(n))
        if claim_n <= 0:
            return []
        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            pending = conn.execute(
                """
                SELECT id
                FROM discovery_keywords
                WHERE platform = ? AND status = 'pending'
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (platform.strip(), claim_n),
            ).fetchall()
            if not pending:
                conn.commit()
                return []
            ids = [int(row["id"]) for row in pending]
            placeholders = ", ".join("?" for _ in ids)
            conn.execute(
                f"""
                UPDATE discovery_keywords
                SET status = 'claimed', claimed_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders}) AND status = 'pending'
                """,
                ids,
            )
            claimed = conn.execute(
                f"""
                SELECT *
                FROM discovery_keywords
                WHERE id IN ({placeholders}) AND status = 'claimed'
                ORDER BY claimed_at ASC, id ASC
                """,
                ids,
            ).fetchall()
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return [dict(row) for row in claimed]

    def mark_keyword_executing(self, keyword_id: int) -> None:
        """Move a ``claimed`` keyword to ``executing`` (async fetch enqueued)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'executing', executing_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def mark_keyword_used(self, keyword_id: int) -> None:
        """Mark a keyword ``used`` (terminal — its fetch has completed)."""
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'used', used_at = CURRENT_TIMESTAMP
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )

    def mark_keyword_failed(self, keyword_id: int) -> int:
        """Mark a keyword ``failed`` and bump ``attempts``.

        Returns the new ``attempts`` count so the caller can decide whether
        to retry (re-pend) or treat the word as terminally failed.
        """
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'failed',
                attempts = attempts + 1
            WHERE id = ? AND status IN ('claimed', 'executing')
            """,
            (int(keyword_id),),
        )
        row = self.conn.execute(
            "SELECT attempts FROM discovery_keywords WHERE id = ?",
            (int(keyword_id),),
        ).fetchone()
        return int(row["attempts"]) if row is not None else 0

    def rollback_keyword_to_pending(self, keyword_id: int) -> None:
        """Return a ``claimed`` keyword to ``pending`` (budget-rejection rollback).

        Used when a claim succeeded but the downstream enqueue was rejected
        (e.g. daily budget exhausted) so no fetch ever ran — the word must go
        back into the pool rather than be burned. Only ``claimed`` rolls back;
        ``executing`` rows already have an in-flight task and are left alone.
        """
        self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending', claimed_at = NULL
            WHERE id = ? AND status = 'claimed'
            """,
            (int(keyword_id),),
        )

    def reclaim_leased_keywords(
        self,
        claim_lease_minutes: float,
        executing_timeout_minutes: float,
    ) -> int:
        """Reclaim leaked in-flight keywords back to ``pending``.

        ``claimed`` rows whose ``claimed_at`` is older than
        ``claim_lease_minutes`` (a loop crashed between claim and fetch) and
        ``executing`` rows whose ``executing_at`` is older than
        ``executing_timeout_minutes`` (an async task never reported back) are
        returned to ``pending`` so the word is not lost. Returns the number
        of rows reclaimed.
        """
        from datetime import UTC, datetime, timedelta

        now = datetime.now(UTC)
        claimed_cutoff = (now - timedelta(minutes=max(0.0, claim_lease_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        executing_cutoff = (now - timedelta(minutes=max(0.0, executing_timeout_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'pending', claimed_at = NULL, executing_at = NULL
            WHERE (status = 'claimed' AND claimed_at IS NOT NULL AND claimed_at <= ?)
               OR (status = 'executing' AND executing_at IS NOT NULL AND executing_at <= ?)
            """,
            (claimed_cutoff, executing_cutoff),
        )
        return int(cursor.rowcount or 0)

    def history_keywords(
        self,
        platform: str,
        window_size: int,
        window_hours: float,
    ) -> list[str]:
        """Return recent in-flight + used keywords for dedup, newest first.

        Includes ``claimed`` / ``executing`` (in-flight, so the planner does
        not regenerate a word a fetch is about to consume) and ``used``
        (recently searched) within the rolling window. Capped at
        ``window_size`` and bounded to the last ``window_hours``.
        """
        from datetime import UTC, datetime, timedelta

        cap = max(0, int(window_size))
        if cap <= 0:
            return []
        self._ensure_fresh_read()
        cutoff = (datetime.now(UTC) - timedelta(hours=max(0.0, window_hours))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        rows = self.conn.execute(
            """
            SELECT keyword
            FROM discovery_keywords
            WHERE platform = ?
              AND status IN ('claimed', 'executing', 'used')
              AND COALESCE(used_at, executing_at, claimed_at, created_at) >= ?
            ORDER BY COALESCE(used_at, executing_at, claimed_at, created_at) DESC, id DESC
            LIMIT ?
            """,
            (platform.strip(), cutoff, cap),
        ).fetchall()
        return [str(row["keyword"]) for row in rows]

    def recycle_oldest_used(
        self,
        platform: str,
        n: int,
        profile_kw_digest: str,
    ) -> int:
        """Recycle the oldest ``used`` keywords back to ``pending``.

        Sparse-profile safety valve: when generation can only produce words
        already in history, the planner recycles the least-recently-used words
        so the cache does not starve. Recycled rows are re-stamped with the
        current ``profile_kw_digest`` and become ``pending`` again. Rows that
        would collide with an existing in-flight row (same word already
        pending/claimed/executing for this digest) are skipped to respect the
        partial unique index. Returns the number of rows recycled.
        """
        recycle_n = max(0, int(n))
        if recycle_n <= 0:
            return 0
        digest = profile_kw_digest.strip()
        self._ensure_fresh_read()
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            candidates = conn.execute(
                """
                SELECT id, keyword
                FROM discovery_keywords
                WHERE platform = ? AND status = 'used'
                ORDER BY used_at ASC, id ASC
                """,
                (platform.strip(),),
            ).fetchall()
            recycled = 0
            for row in candidates:
                if recycled >= recycle_n:
                    break
                clash = conn.execute(
                    """
                    SELECT 1
                    FROM discovery_keywords
                    WHERE platform = ?
                      AND keyword = ?
                      AND profile_kw_digest = ?
                      AND status IN ('pending', 'claimed', 'executing')
                    LIMIT 1
                    """,
                    (platform.strip(), str(row["keyword"]), digest),
                ).fetchone()
                if clash is not None:
                    continue
                conn.execute(
                    """
                    UPDATE discovery_keywords
                    SET status = 'pending',
                        profile_kw_digest = ?,
                        claimed_at = NULL,
                        executing_at = NULL,
                        used_at = NULL
                    WHERE id = ? AND status = 'used'
                    """,
                    (digest, int(row["id"])),
                )
                recycled += 1
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return recycled

    def expire_pending_by_digest(self, platform: str, current_digest: str) -> int:
        """Expire ``pending`` keywords generated under a stale profile digest.

        When the profile changes the planner expires any ``pending`` word from
        an older digest so the next generation uses the fresh profile.
        ``used`` / ``claimed`` / ``executing`` rows are left untouched
        (dedup history + in-flight work are preserved). Returns the count
        expired.
        """
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'expired'
            WHERE platform = ? AND status = 'pending' AND profile_kw_digest != ?
            """,
            (platform.strip(), current_digest.strip()),
        )
        return int(cursor.rowcount or 0)

    def purge_archived_keywords(
        self,
        retention_hours: float,
        *,
        platform: str | None = None,
    ) -> int:
        """Delete archived (``used`` / ``expired`` / ``failed``) rows past retention.

        Cleanup for rows that have left the dedup window and are no longer
        needed for yield accounting. Only terminal-archive states are purged;
        in-flight rows are never deleted. Returns the number of rows removed.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(hours=max(0.0, retention_hours))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        params: list[Any] = [cutoff]
        platform_clause = ""
        if platform is not None:
            platform_clause = " AND platform = ?"
            params.append(platform.strip())
        cursor = self._execute_write(
            f"""
            DELETE FROM discovery_keywords
            WHERE status IN ('used', 'expired', 'failed')
              AND COALESCE(used_at, executing_at, claimed_at, created_at) < ?
              {platform_clause}
            """,
            params,
        )
        return int(cursor.rowcount or 0)

    # ── Discovery keyword yield (P1.8 admit-time backfill) ───────

    def increment_keyword_yield(self, keyword_id: int, content_id: str) -> bool:
        """Idempotently credit one admitted content to the keyword that produced it.

        Called at admission (the single ``_cache_results`` convergence) for every
        pool item whose ``source_keyword_id`` is set. Idempotency is keyed on
        ``(keyword_id, content_id)`` via the ``discovery_keyword_yield`` ledger:
        the ledger ``INSERT OR IGNORE`` only fires once per distinct produced
        content, so a retried / partial / out-of-order admit of the same item
        does **not** double-count. ``yield_count`` is bumped only on a genuinely
        new ledger row. Decoupled from ``used`` (P1.7) — a word can be ``used``
        and still accrue yield later.

        Returns True if this call recorded a new yield (counter bumped), False
        if it was a duplicate / invalid no-op.
        """
        kid = int(keyword_id)
        cid = str(content_id or "").strip()
        if kid <= 0 or not cid:
            return False
        before = self.conn.total_changes
        self._execute_write(
            """
            INSERT OR IGNORE INTO discovery_keyword_yield (keyword_id, content_id)
            VALUES (?, ?)
            """,
            (kid, cid),
        )
        if self.conn.total_changes == before:
            # Ledger row already existed → this (keyword, content) was already
            # credited. Do not touch the counter.
            return False
        self._execute_write(
            "UPDATE discovery_keywords SET yield_count = yield_count + 1 WHERE id = ?",
            (kid,),
        )
        return True

    def keyword_yield_count(self, keyword_id: int) -> int:
        """Return the stored ``yield_count`` for a keyword (0 if unknown)."""
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT yield_count FROM discovery_keywords WHERE id = ?",
            (int(keyword_id),),
        ).fetchone()
        return int(row["yield_count"]) if row is not None else 0

    def keyword_yield_total(self, platform: str) -> int:
        """Return the platform-wide sum of ``yield_count`` across all keywords.

        Cheap single aggregate (the ``(platform, status, …)`` index already
        covers the scan) used only for the planner's per-cycle observability
        ledger (P1.9): the merged LLM call is one ``discovery.keyword_planner``
        caller (token cost can't be split per platform), so the ledger surfaces
        per-platform keyword *production* (generated) + cumulative *yield* so
        operators can still see which platform's search words actually land
        content. Counts every row's stored ``yield_count`` (used / expired
        history included) — it is a running production total, not a live-pool
        gauge. Returns 0 on any error so it never breaks a generation pass.
        """
        try:
            self._ensure_fresh_read()
            row = self.conn.execute(
                "SELECT COALESCE(SUM(yield_count), 0) AS total "
                "FROM discovery_keywords WHERE platform = ?",
                (platform.strip(),),
            ).fetchone()
        except Exception:
            logger.debug("keyword_yield_total failed for %s", platform, exc_info=True)
            return 0
        return int(row["total"]) if row is not None else 0

    def used_keyword_count(self, platform: str) -> int:
        """Count ``used`` keywords for a platform (P3.2 dynamic-cap denominator).

        Paired with :meth:`keyword_yield_total` to derive the platform's observed
        average yield-per-keyword (total yield / used count). Cheap single
        aggregate; returns 0 on any error so it never breaks a generation pass.
        """
        try:
            self._ensure_fresh_read()
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM discovery_keywords "
                "WHERE platform = ? AND status = 'used'",
                (platform.strip(),),
            ).fetchone()
        except Exception:
            logger.debug("used_keyword_count failed for %s", platform, exc_info=True)
            return 0
        return int(row["n"]) if row is not None else 0

    def retire_zero_yield_keywords(
        self,
        platform: str,
        *,
        min_age_minutes: float = 60.0,
    ) -> int:
        """Retire ``used`` words that have produced nothing, conservatively.

        A word that has been ``used`` for at least ``min_age_minutes`` and still
        has ``yield_count == 0`` is moved to ``expired`` so the recycler does not
        keep re-pending a search term that demonstrably never lands content.

        The age floor is the safety valve against retiring a *freshly* used word
        whose admit is still pending: inline-admit credits yield synchronously,
        but fetch-only (X / YouTube) and async (XHS) words are marked ``used`` at
        handoff and only accrue yield once the shared pipeline admits — minutes
        later. ``min_age_minutes`` must comfortably exceed that admit latency.
        Only ``used`` rows are touched; in-flight / pending / already-expired
        rows are left alone. Returns the number of rows retired.
        """
        from datetime import UTC, datetime, timedelta

        cutoff = (datetime.now(UTC) - timedelta(minutes=max(0.0, min_age_minutes))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_keywords
            SET status = 'expired'
            WHERE platform = ?
              AND status = 'used'
              AND yield_count = 0
              AND used_at IS NOT NULL
              AND used_at <= ?
            """,
            (platform.strip(), cutoff),
        )
        return int(cursor.rowcount or 0)

    # ── Discovery keyword planner single-flight lock ─────────────

    def acquire_planner_lock(self, owner: str, lease_seconds: float) -> bool:
        """Try to acquire the planner single-flight lock via CAS.

        ``BEGIN IMMEDIATE`` serializes the check-and-set: the lock is granted
        if it is unheld, already owned by ``owner``, or its ``locked_until``
        has elapsed (the previous holder crashed). On success ``locked_until``
        is extended by ``lease_seconds`` and the row's ``owner`` is set.
        **Short transaction only** — acquire, commit, then run the LLM call
        *without* holding any DB lock; reacquire/``renew`` to write results.
        Returns True if the lock is now held by ``owner``.
        """
        from datetime import UTC, datetime, timedelta

        lock_name = "keyword_planner"
        now = datetime.now(UTC)
        now_text = now.strftime("%Y-%m-%d %H:%M:%S")
        new_until = (now + timedelta(seconds=max(0.0, lease_seconds))).strftime("%Y-%m-%d %H:%M:%S")
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner, locked_until FROM discovery_planner_lock WHERE lock_name = ?",
                (lock_name,),
            ).fetchone()
            if row is None:
                conn.execute(
                    """
                    INSERT INTO discovery_planner_lock
                        (lock_name, owner, locked_until, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    """,
                    (lock_name, owner, new_until),
                )
                conn.commit()
                return True
            held_by = str(row["owner"] or "")
            locked_until = str(row["locked_until"] or "")
            if held_by and held_by != owner and locked_until > now_text:
                # Still validly held by someone else.
                conn.commit()
                return False
            conn.execute(
                """
                UPDATE discovery_planner_lock
                SET owner = ?, locked_until = ?, updated_at = CURRENT_TIMESTAMP
                WHERE lock_name = ?
                """,
                (owner, new_until, lock_name),
            )
            conn.commit()
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        finally:
            conn.close()
        return True

    def renew_planner_lock(self, owner: str, lease_seconds: float) -> bool:
        """Extend the planner lock lease if still owned by ``owner``.

        Returns True if the lease was extended, False if the lock has been
        taken over by another owner in the meantime.
        """
        from datetime import UTC, datetime, timedelta

        new_until = (datetime.now(UTC) + timedelta(seconds=max(0.0, lease_seconds))).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        cursor = self._execute_write(
            """
            UPDATE discovery_planner_lock
            SET locked_until = ?, updated_at = CURRENT_TIMESTAMP
            WHERE lock_name = 'keyword_planner' AND owner = ?
            """,
            (new_until, owner),
        )
        return int(cursor.rowcount or 0) > 0

    def release_planner_lock(self, owner: str) -> bool:
        """Release the planner lock if still owned by ``owner``.

        Clears the owner and expires ``locked_until`` so the next acquirer
        can take it immediately. Returns True if a row was released.
        """
        from datetime import UTC, datetime

        now_text = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
        cursor = self._execute_write(
            """
            UPDATE discovery_planner_lock
            SET owner = '', locked_until = ?, updated_at = CURRENT_TIMESTAMP
            WHERE lock_name = 'keyword_planner' AND owner = ?
            """,
            (now_text, owner),
        )
        return int(cursor.rowcount or 0) > 0

    # ── Watch-later CRUD ─────────────────────────────────────────

    def _ensure_favorites_table(self) -> None:
        """Create the favorites (收藏夹) table for existing databases.

        Favorites are a permanent, curated keep — distinct from the
        ephemeral ``watch_later`` queue. The two tables are independent so
        a video can be in one, both, or neither.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS favorites (
                bvid     TEXT PRIMARY KEY,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note     TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_favorites_added
                ON favorites(added_at DESC);
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_name TEXT DEFAULT '',
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                author TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                content_text TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                status TEXT DEFAULT 'unread',
                reading_percent REAL DEFAULT 0,
                reading_progress TEXT DEFAULT '',
                favorited INTEGER DEFAULT 0,
                ai_summary TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_articles_published_at
                ON articles(published_at);
            CREATE INDEX IF NOT EXISTS idx_articles_source_type
                ON articles(source_type);
            CREATE INDEX IF NOT EXISTS idx_articles_source_type_status
                ON articles(source_type, status);
            CREATE INDEX IF NOT EXISTS idx_articles_source_type_published
                ON articles(source_type, published_at);

            CREATE TABLE IF NOT EXISTS read_archive (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                source_name TEXT DEFAULT '',
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                author TEXT DEFAULT '',
                summary TEXT DEFAULT '',
                content_text TEXT DEFAULT '',
                published_at TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_readarchive_published_at
                ON read_archive(published_at);
            CREATE INDEX IF NOT EXISTS idx_readarchive_source_type
                ON read_archive(source_type);
            CREATE INDEX IF NOT EXISTS idx_readarchive_published
                ON read_archive(published_at);

            CREATE VIRTUAL TABLE IF NOT EXISTS read_archive_fts USING fts5(
                title, content_text, tags, author, summary,
                content='read_archive', content_rowid='id', tokenize='trigram'
            );
            CREATE TRIGGER IF NOT EXISTS read_archive_fts_ai AFTER INSERT ON read_archive BEGIN
                INSERT INTO read_archive_fts(rowid, title, content_text, tags, author, summary)
                VALUES (new.id, new.title, new.content_text, new.tags, new.author, new.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS read_archive_fts_ad AFTER DELETE ON read_archive BEGIN
                INSERT INTO read_archive_fts(read_archive_fts, rowid, title, content_text, tags, author, summary)
                VALUES ('delete', old.id, old.title, old.content_text, old.tags, old.author, old.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS read_archive_fts_au AFTER UPDATE ON read_archive BEGIN
                INSERT INTO read_archive_fts(read_archive_fts, rowid, title, content_text, tags, author, summary)
                VALUES ('delete', old.id, old.title, old.content_text, old.tags, old.author, old.summary);
                INSERT INTO read_archive_fts(rowid, title, content_text, tags, author, summary)
                VALUES (new.id, new.title, new.content_text, new.tags, new.author, new.summary);
            END;

            CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
                title, content_text, tags, author, summary,
                content='articles', content_rowid='id', tokenize='trigram'
            );
            CREATE TRIGGER IF NOT EXISTS articles_fts_ai AFTER INSERT ON articles BEGIN
                INSERT INTO articles_fts(rowid, title, content_text, tags, author, summary)
                VALUES (new.id, new.title, new.content_text, new.tags, new.author, new.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS articles_fts_ad AFTER DELETE ON articles BEGIN
                INSERT INTO articles_fts(articles_fts, rowid, title, content_text, tags, author, summary)
                VALUES ('delete', old.id, old.title, old.content_text, old.tags, old.author, old.summary);
            END;
            CREATE TRIGGER IF NOT EXISTS articles_fts_au AFTER UPDATE ON articles BEGIN
                INSERT INTO articles_fts(articles_fts, rowid, title, content_text, tags, author, summary)
                VALUES ('delete', old.id, old.title, old.content_text, old.tags, old.author, old.summary);
                INSERT INTO articles_fts(rowid, title, content_text, tags, author, summary)
                VALUES (new.id, new.title, new.content_text, new.tags, new.author, new.summary);
            END;
        """)
        # Backfill columns for existing tables
        for col, typ, default in [
            ("tags", "TEXT", "'[]'"),
            ("status", "TEXT", "'unread'"),
            ("body_fetch_attempts", "INTEGER", "0"),
            ("reading_percent", "REAL", "0"),
            ("reading_progress", "TEXT", "''"),
            ("favorited", "INTEGER", "0"),
            ("ai_summary", "TEXT", "''"),
        ]:
            with suppress(Exception):
                self.conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typ} DEFAULT {default}")

        # 阅读笔记/摘录/高亮：绑定 articles.id，无外键约束（与 favorites 同风格）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS article_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                quote TEXT DEFAULT '',
                note TEXT DEFAULT '',
                color TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_article_notes_article
                ON article_notes(article_id);

            -- 离线存档：保存文章原始 HTML 快照，防止链接失效
            CREATE TABLE IF NOT EXISTS article_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                article_id INTEGER NOT NULL,
                url TEXT NOT NULL,
                content_html TEXT DEFAULT '',
                content_text TEXT DEFAULT '',
                fetch_source TEXT DEFAULT 'url_extractor',
                fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_article
                ON article_snapshots(article_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_url
                ON article_snapshots(url);
        """)

        # 全文索引：首次或为空时从 articles 重建（trigram 适配中文子串）
        try:
            if self.conn.execute("SELECT count(*) FROM articles_fts").fetchone()[0] == 0:
                self.conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
                self.conn.commit()
        except Exception:
            logger.exception("Failed to rebuild articles FTS")

    # ── Auth state (password gate revocation epoch) ──────────────

    def _ensure_auth_state_table(self) -> None:
        """Create the auth_state key/value table.

        Holds the global revocation epoch (``auth_epoch``) and the password
        fingerprint, kept out of ``config.toml`` so that revocation is a
        cross-process atomic counter rather than a whole-file rewrite. See
        ``docs/plans/2026-05-30-web-password-auth-design.md`` §4.7.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS auth_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def _ensure_init_runs_table(self) -> None:
        """Create the init_runs table backing guided (GUI) initialization.

        One row per guided-init run; the latest row is the authoritative
        progress source for ``GET /api/init-status`` (docs/specs/gui-init.md
        §5a). State survives restarts so a crashed / hot-reloaded run is
        reconciled to ``failed`` on boot rather than leaving a stuck
        ``running`` flag.
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS init_runs (
                run_id          TEXT PRIMARY KEY,
                -- status: idle|starting|running|completed|failed|cancelled
                status          TEXT NOT NULL,
                stage           INTEGER NOT NULL DEFAULT 0,  -- 0..4
                stages_json     TEXT,  -- JSON: per-stage [{n,status,reason}]
                partial_success INTEGER NOT NULL DEFAULT 0,
                error_reason    TEXT,
                sequence        INTEGER NOT NULL DEFAULT 0,
                started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at     TIMESTAMP
            );
        """)

    def get_latest_init_run(self) -> dict[str, Any] | None:
        """Return the most recent init run as a dict, or None if none exist.

        Reads fresh WAL state so a run written by the background task / another
        process is visible immediately.
        """
        self._ensure_fresh_read()
        row = self.conn.execute(
            "SELECT * FROM init_runs ORDER BY started_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row is not None else None

    def try_reserve_init_starting(self, run_id: str) -> bool:
        """Atomically reserve a new init run in ``starting`` state.

        Single-flight via ``BEGIN IMMEDIATE`` CAS (like ``bump_auth_epoch``):
        succeeds only when no run is currently ``starting``/``running``.
        Returns False when an init is already active, so concurrent
        ``POST /api/init`` callers cannot double-start (spec §5b TOCTOU).
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            active = conn.execute(
                "SELECT 1 FROM init_runs WHERE status IN ('starting','running') LIMIT 1"
            ).fetchone()
            if active is not None:
                conn.rollback()
                return False
            conn.execute(
                """
                INSERT INTO init_runs (run_id, status, stage, sequence, started_at, updated_at)
                VALUES (?, 'starting', 0, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(run_id) DO UPDATE SET
                    status='starting', stage=0, sequence=0, partial_success=0,
                    error_reason=NULL, finished_at=NULL, updated_at=CURRENT_TIMESTAMP
                """,
                (run_id,),
            )
            conn.commit()
            return True
        finally:
            conn.close()

    def update_init_run(self, run_id: str, **fields: Any) -> None:
        """Update mutable columns of an init run (the single status writer).

        Only whitelisted columns are accepted and ``updated_at`` is always
        bumped; unknown keys raise so a typo cannot silently no-op.
        """
        allowed = {
            "status",
            "stage",
            "stages_json",
            "partial_success",
            "error_reason",
            "sequence",
            "finished_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"update_init_run: unknown columns {sorted(unknown)}")
        if not fields:
            return
        assignments = ", ".join(f"{col} = ?" for col in fields)
        params = [*fields.values(), run_id]
        self._execute_write(
            f"UPDATE init_runs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE run_id = ?",
            params,
        )

    def reconcile_init_runs_on_boot(self) -> int:
        """Fail any run left ``starting``/``running`` by a crash/restart.

        No init task survives a process restart, so a persisted active status
        is necessarily stale. Returns the number of rows reconciled (spec §5a).
        """
        cursor = self._execute_write(
            """
            UPDATE init_runs
               SET status = 'failed', error_reason = 'interrupted',
                   finished_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
             WHERE status IN ('starting','running')
            """
        )
        return cursor.rowcount

    def get_auth_epoch(self) -> int:
        """Return the current revocation epoch. Reads fresh WAL state.

        A missing row means "never bumped" → 0. A present-but-corrupt value
        RAISES (never silently 0) so the auth gate fails closed instead of
        resurrecting tokens minted before a prior revocation. See §4.7.
        """
        self._ensure_fresh_read()
        row = self.conn.execute("SELECT value FROM auth_state WHERE key = 'auth_epoch'").fetchone()
        if row is None:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"corrupt auth_epoch value: {row[0]!r}") from exc

    def bump_auth_epoch(self) -> int:
        """Atomically increment and return the revocation epoch.

        Uses a short-lived connection with ``BEGIN IMMEDIATE`` so concurrent
        bumps (or another process) cannot lose an increment.
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM auth_state WHERE key = 'auth_epoch'").fetchone()
            # Missing → 0; corrupt → raise (never reset a damaged epoch downward).
            current = 0 if row is None else int(row[0])
            new_value = current + 1
            conn.execute(
                """
                INSERT INTO auth_state (key, value) VALUES ('auth_epoch', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(new_value),),
            )
            conn.commit()
            return new_value
        finally:
            conn.close()

    def reconcile_password_fingerprint(self, fingerprint: str) -> bool:
        """Detect a password change and bump the epoch if needed.

        Compares ``fingerprint`` (derived from stable credential material, see
        ``auth_core.password_fingerprint``) against the stored value, inside a
        single ``BEGIN IMMEDIATE`` transaction (CAS). Returns ``True`` when the
        epoch was bumped. First enable (no prior fingerprint) records it WITHOUT
        bumping. See §4.7.
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT value FROM auth_state WHERE key = 'password_fingerprint'"
            ).fetchone()
            stored = row[0] if row is not None else None
            bumped = False
            if stored is None:
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
            elif stored != fingerprint:
                epoch_row = conn.execute(
                    "SELECT value FROM auth_state WHERE key = 'auth_epoch'"
                ).fetchone()
                # Missing → 0; corrupt → raise (the caller fails closed).
                current = 0 if epoch_row is None else int(epoch_row[0])
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', ?)",
                    (str(current + 1),),
                )
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
                bumped = True
            conn.commit()
            return bumped
        finally:
            conn.close()

    def set_password_fingerprint(self, fingerprint: str) -> None:
        """Overwrite the stored fingerprint without touching the epoch.

        Used after ``--rotate-secret`` re-bases the fingerprint under a new
        signing secret, so the next reconcile does not double-bump.
        """
        self._execute_write(
            "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('password_fingerprint', ?)",
            (fingerprint,),
        )

    def revoke_and_set_fingerprint(self, fingerprint: str | None, *, force_bump: bool) -> None:
        """Atomically (single ``BEGIN IMMEDIATE``) set the fingerprint, bumping the
        epoch when the credential changed or ``force_bump`` is set.

        Used by the local admin endpoint so a password change's revocation
        (epoch bump) and fingerprint update commit together — never a half state
        where the new password is live but old sessions survive (review r1#2).

        The bump decision is made INSIDE the transaction by comparing ``fingerprint``
        to the stored one (CAS), mirroring ``reconcile_password_fingerprint``: a
        first-ever set (no stored fingerprint) never bumps, but any *change* from an
        existing fingerprint always does — even when the caller's ``force_bump`` is
        false. This catches an effective credential change the caller can't see in
        its request, e.g. admin hot-publishing a ``password_hash`` that drifted on
        disk via an out-of-band ``set-password`` (review r4#2). ``force_bump`` adds a
        revoke for enabled on/off toggles, which carry no fingerprint change.

        Raises on a corrupt epoch (caller fails closed). The caller persists the new
        config FIRST (rolling it back if this raises) and publishes to the live gate
        only AFTER this commits, so a failure here leaves the durable DB state
        untouched and the persisted/live auth on the old password; a crash between
        the config write and this call is healed by the startup fingerprint
        reconcile (review r2#1).
        """
        conn = self.open_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            stored_row = conn.execute(
                "SELECT value FROM auth_state WHERE key = 'password_fingerprint'"
            ).fetchone()
            stored = stored_row[0] if stored_row is not None else None
            credential_changed = (
                fingerprint is not None and stored is not None and stored != fingerprint
            )
            if force_bump or credential_changed:
                row = conn.execute(
                    "SELECT value FROM auth_state WHERE key = 'auth_epoch'"
                ).fetchone()
                current = 0 if row is None else int(row[0])  # corrupt → raise
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES ('auth_epoch', ?)",
                    (str(current + 1),),
                )
            if fingerprint is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO auth_state (key, value) VALUES "
                    "('password_fingerprint', ?)",
                    (fingerprint,),
                )
            conn.commit()
        finally:
            conn.close()

    # ── Favorites CRUD ───────────────────────────────────────────

    def _decode_event_metadata(row: dict[str, Any]) -> dict[str, Any]:
        metadata_raw = row.get("metadata", "")
        if isinstance(metadata_raw, str) and metadata_raw:
            try:
                metadata = json.loads(metadata_raw)
            except json.JSONDecodeError:
                metadata = {}
            if isinstance(metadata, dict):
                return metadata
        if isinstance(metadata_raw, dict):
            return metadata_raw
        return {}

    # ── user feedback (like / dislike) ─────────────────────────────────

    def _ensure_user_feedback_table(self) -> None:
        self.conn.executescript(_USER_FEEDBACK_DDL)

    # ── view history (implicit feedback) ────────────────────────────

    def _ensure_view_history_table(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS view_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                bvid        TEXT NOT NULL,
                title       TEXT DEFAULT '',
                source_platform TEXT DEFAULT '',
                topic_group TEXT DEFAULT '',
                content_url TEXT DEFAULT '',
                up_name     TEXT DEFAULT '',
                quality_score REAL DEFAULT 0.0,
                fit_score   REAL DEFAULT 0.0,
                viewed_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_view_history_bvid
                ON view_history(bvid);
            CREATE INDEX IF NOT EXISTS idx_view_history_viewed_at
                ON view_history(viewed_at);
        """)
        # v0.3.x implicit feedback: dwell seconds per view
        existing_cols = {
            r["name"] for r in self.conn.execute("PRAGMA table_info(view_history)").fetchall()
        }
        if "dwell_seconds" not in existing_cols:
            self.conn.execute("ALTER TABLE view_history ADD COLUMN dwell_seconds REAL DEFAULT 0")
            self.conn.commit()

    def _ensure_topic_tables(self) -> None:
        """Create the topic (专题) tables.

        A topic is a user-curated collection: a name + keyword set + source
        platforms whose matching content is continuously collected into
        ``topic_items``. Multiple topics can coexist (e.g. 广告, 去有风的地方).
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS topics (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL UNIQUE,
                slug        TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                keywords    TEXT DEFAULT '[]',
                platforms   TEXT DEFAULT '["bilibili"]',
                status      TEXT NOT NULL DEFAULT 'active',
                item_count  INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_collected_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS topic_items (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                topic_id    INTEGER NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
                content_key TEXT NOT NULL,
                title       TEXT NOT NULL,
                url         TEXT DEFAULT '',
                source_platform TEXT DEFAULT '',
                source_name  TEXT DEFAULT '',
                cover_url   TEXT DEFAULT '',
                summary     TEXT DEFAULT '',
                topic_label TEXT DEFAULT '',
                collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(topic_id, content_key)
            );
            CREATE INDEX IF NOT EXISTS idx_topic_items_topic
                ON topic_items(topic_id, collected_at DESC);
            CREATE INDEX IF NOT EXISTS idx_topic_items_key
                ON topic_items(content_key);
        """)

    def _ensure_knowledge_forge_tables(self) -> None:
        """Create Knowledge Forge (知识锻造炉) columns and tables.

        Docs: docs/knowledge-forge-design.md. 渐进式迁移：articles 表只加列
        不删改，旧数据/旧字段完全保留；新表全部 CREATE IF NOT EXISTS，幂等。
        本方法在 Database.initialize() 中调用，每次启动自动补齐缺失结构。
        """
        # 1. articles 表新增正文清理器字段（3.0.5）+ 分层摘要字段（3.1.3）
        existing_columns = {
            str(row["name"]) for row in self.conn.execute("PRAGMA table_info(articles)").fetchall()
        }
        required_columns = {
            "content_cleaned": "TEXT",  # 清理后的正文
            "content_clean_score": "REAL",  # 清理质量评分 0-100
            "content_clean_log": "TEXT",  # 清理日志（JSON）
            "content_verified": "INTEGER DEFAULT 0",  # 是否通过验证 0/1
            "content_verify_result": "TEXT",  # 验证结果（JSON）
            "summary_detailed": "TEXT",  # 详细版摘要
            "summary_compact": "TEXT",  # 精简版摘要
            "summary_ultra_compact": "TEXT",  # 超精简版摘要
            "summary_quality": "REAL",  # 摘要质量评分（0-1）
            "summary_version": "INTEGER DEFAULT 0",  # 摘要版本号
            "summary_generated_at": "TEXT",  # 摘要生成时间
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(f"ALTER TABLE articles ADD COLUMN {column_name} {column_type}")

        # 2. 实体表（3.2.2）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE,
                type TEXT,
                description TEXT,
                article_count INTEGER DEFAULT 0,
                first_seen_at TEXT,
                last_updated_at TEXT,
                metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
            CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);

            -- 文章-实体关联
            CREATE TABLE IF NOT EXISTS article_entities (
                article_id INTEGER,
                entity_id INTEGER,
                relevance REAL,
                context TEXT,
                PRIMARY KEY (article_id, entity_id),
                FOREIGN KEY (article_id) REFERENCES articles(id),
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );
            CREATE INDEX IF NOT EXISTS idx_article_entities_article ON article_entities(article_id);
            CREATE INDEX IF NOT EXISTS idx_article_entities_entity ON article_entities(entity_id);

            -- 实体间关联
            CREATE TABLE IF NOT EXISTS entity_relations (
                entity_id_a INTEGER,
                entity_id_b INTEGER,
                relation_type TEXT,
                confidence REAL,
                description TEXT,
                co_occur INTEGER DEFAULT 1,
                PRIMARY KEY (entity_id_a, entity_id_b, relation_type)
            );
        """)
        # 兼容旧库：entity_relations 补 co_occur 列（幂等）
        _er_columns = {
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(entity_relations)").fetchall()
        }
        if "co_occur" not in _er_columns:
            self.conn.execute("ALTER TABLE entity_relations ADD COLUMN co_occur INTEGER DEFAULT 1")

        # 3. 文章间关联（3.3.2）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS article_relations (
                article_id_a INTEGER,
                article_id_b INTEGER,
                relation_type TEXT,
                confidence REAL,
                description TEXT,
                created_at TEXT,
                PRIMARY KEY (article_id_a, article_id_b, relation_type),
                FOREIGN KEY (article_id_a) REFERENCES articles(id),
                FOREIGN KEY (article_id_b) REFERENCES articles(id)
            );
            CREATE INDEX IF NOT EXISTS idx_article_relations_a ON article_relations(article_id_a);
            CREATE INDEX IF NOT EXISTS idx_article_relations_b ON article_relations(article_id_b);
            CREATE INDEX IF NOT EXISTS idx_article_relations_type ON article_relations(relation_type);
        """)

        # 4. 质量审计表（3.5.3）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS audit_tasks (
                id INTEGER PRIMARY KEY,
                task_type TEXT,
                status TEXT,
                started_at TEXT,
                completed_at TEXT,
                total_articles INTEGER,
                issues_found INTEGER,
                issues_fixed INTEGER,
                report_path TEXT,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_issues (
                id INTEGER PRIMARY KEY,
                article_id INTEGER,
                issue_type TEXT,
                severity TEXT,
                description TEXT,
                details TEXT,
                status TEXT DEFAULT 'open',
                fix_suggestion TEXT,
                fixed_at TEXT,
                created_at TEXT,
                FOREIGN KEY (article_id) REFERENCES articles(id)
            );
            CREATE INDEX IF NOT EXISTS idx_audit_issues_article ON audit_issues(article_id);
            CREATE INDEX IF NOT EXISTS idx_audit_issues_type ON audit_issues(issue_type);
            CREATE INDEX IF NOT EXISTS idx_audit_issues_status ON audit_issues(status);

            CREATE TABLE IF NOT EXISTS article_quality_scores (
                article_id INTEGER PRIMARY KEY,
                overall_score REAL,
                completeness_score REAL,
                content_score REAL,
                link_score REAL,
                uniqueness_score REAL,
                last_audited_at TEXT,
                FOREIGN KEY (article_id) REFERENCES articles(id)
            );

            CREATE TABLE IF NOT EXISTS audit_config (
                id INTEGER PRIMARY KEY,
                config_key TEXT UNIQUE,
                config_value TEXT,
                description TEXT
            );
        """)
        self.conn.executescript("""
            INSERT OR IGNORE INTO audit_config (config_key, config_value, description) VALUES
            ('min_content_length', '200', '最小内容长度（低于此值标记为过短）'),
            ('min_summary_length', '100', '最小摘要长度（低于此值标记为缺失）'),
            ('simhash_threshold', '0.9', 'simhash相似度阈值（高于此值标记为重复）'),
            ('dead_link_timeout', '10', '死链检测超时时间（秒）'),
            ('batch_size', '500', '批处理大小'),
            ('auto_fix_enabled', 'false', '是否启用自动修复'),
            ('dead_link_concurrency', '5', '死链检测并发数');
        """)

        # 5. 知识缺口分析表（3.4.4）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS gap_analysis_tasks (
                id INTEGER PRIMARY KEY,
                status TEXT,
                started_at TEXT,
                completed_at TEXT,
                report_path TEXT,
                total_topics INTEGER,
                gaps_found INTEGER,
                created_at TEXT
            );

            CREATE TABLE IF NOT EXISTS gap_records (
                id INTEGER PRIMARY KEY,
                task_id INTEGER,
                gap_type TEXT,
                entity_id INTEGER,
                severity TEXT,
                description TEXT,
                current_count INTEGER,
                suggested_count INTEGER,
                suggestion TEXT,
                status TEXT DEFAULT 'open',
                created_at TEXT,
                FOREIGN KEY (task_id) REFERENCES gap_analysis_tasks(id),
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );
        """)

    # ------------------------------------------------------------------ #
    # Topics CRUD
    # ------------------------------------------------------------------ #

