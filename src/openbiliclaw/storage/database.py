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


class Database:
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
                        self._extract_create_table_sql(
                            _XHS_OBSERVED_URLS_DDL, "xhs_observed_urls"
                        ),
                        self._extract_create_table_sql(
                            _USER_FEEDBACK_DDL, "user_feedback"
                        ),
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

    def release_stale_pending_native_sync_tasks(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
    ) -> None:
        pass

    def reconcile_stale_native_save_claims_for_list(
        self,
        list_kind: str,
        item_keys: Sequence[str] | None,
    ) -> None:
        conn = self.open_connection()
        try:
            conn.commit()
        finally:
            conn.close()

    def create_native_sync_task_snapshot(
        self,
        list_kind: str,
        selected_keys: Sequence[str] | None,
        task_id: str,
        trigger: str,
    ) -> list[dict[str, Any]]:
        return []

    def has_sync_task(self, task_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM native_save_task_items WHERE task_id = ? AND is_live = 1",
            (task_id,),
        ).fetchone()
        return bool(row)

    def get_sync_task(self, task_id: str) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT task_id, item_key, list_kind, status, is_live FROM native_save_task_items WHERE task_id = ? AND is_live = 1",
            (task_id,),
        ).fetchall()
        return {
            "task_id": task_id,
            "items": [dict(row) for row in rows],
        }

    def release_native_sync_task(self, task_id: str) -> None:
        conn = self.open_connection()
        try:
            conn.execute(
                "UPDATE native_save_task_items SET is_live = 0, updated_at = CURRENT_TIMESTAMP WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

    def discard_native_sync_task(self, task_id: str) -> None:
        conn = self.open_connection()
        try:
            conn.execute(
                "DELETE FROM native_save_task_items WHERE task_id = ? AND is_live = 0",
                (task_id,),
            )
            conn.commit()
        finally:
            conn.close()

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

    def insert_event(self, event_type: str, **kwargs: Any) -> int:
        """Insert a behavioral event.

        v0.3.23+: ``context`` is now a natural-language string (from
        ``event_format.build_event()``). It's stored as raw text — no
        outer JSON wrapping — so consumers reading via SELECT get back
        the same string they put in. Pre-v0.3.22 callers that passed
        dict-shaped context still work: dicts / lists / other non-string
        values are JSON-encoded for storage so older code paths don't
        suddenly lose data.

        Args:
            event_type: Type of event.
            **kwargs: Additional event fields. ``context`` may be str,
                dict, list, or None.

        Returns:
            Inserted row ID.
        """
        import json

        from openbiliclaw.sources.event_format import classify_event_satisfaction

        raw_context = kwargs.get("context", "")
        if isinstance(raw_context, str):
            context_text = raw_context
        elif raw_context is None:
            context_text = ""
        else:
            # Legacy dict / list payload — JSON-encode for storage.
            context_text = json.dumps(raw_context, ensure_ascii=False)

        metadata_payload = kwargs.get("metadata", {})

        # Single classification owner. Reconstruct the event dict shape
        # the classifier expects (event_type + url + title + metadata).
        # API ingest may set dwell fields at the top level as well; pass
        # those through so the click rules read either location.
        classifier_event: dict[str, Any] = {
            "event_type": event_type,
            "url": kwargs.get("url", ""),
            "title": kwargs.get("title", ""),
            "metadata": metadata_payload if isinstance(metadata_payload, dict) else {},
        }
        for top_level_key in ("watch_seconds", "video_duration_seconds"):
            if top_level_key in kwargs and kwargs[top_level_key] is not None:
                classifier_event[top_level_key] = kwargs[top_level_key]
        inferred_satisfaction, satisfaction_reason = classify_event_satisfaction(classifier_event)

        cursor = self._execute_write(
            "INSERT INTO events "
            "(event_type, url, title, context, metadata, "
            " inferred_satisfaction, satisfaction_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event_type,
                kwargs.get("url", ""),
                kwargs.get("title", ""),
                context_text,
                json.dumps(metadata_payload, ensure_ascii=False),
                inferred_satisfaction,
                satisfaction_reason,
            ),
        )
        return cursor.lastrowid or 0

    def get_recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent events.

        Args:
            limit: Maximum number of events.

        Returns:
            List of event dicts.
        """
        cursor = self.conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Durable popup chat turns
    # ------------------------------------------------------------------

    def create_chat_turn(
        self,
        *,
        turn_id: str,
        message: str,
        session: str = "popup",
        scope: str = "chat",
        subject_id: str = "",
        subject_title: str = "",
    ) -> dict[str, Any]:
        """Create a pending popup chat turn if it does not already exist."""
        self._execute_write(
            """
            INSERT OR IGNORE INTO chat_turns (
                turn_id, session, scope, subject_id, subject_title, message, status
            )
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (
                turn_id,
                session or "popup",
                scope or "chat",
                subject_id or "",
                subject_title or "",
                message,
            ),
        )
        row = self.get_chat_turn(turn_id)
        if row is None:
            raise RuntimeError(f"Failed to create chat turn {turn_id!r}")
        return row

    def complete_chat_turn(self, turn_id: str, *, reply: str) -> None:
        """Mark a pending popup chat turn as completed."""
        self._execute_write(
            """
            UPDATE chat_turns
            SET status = 'completed',
                reply = ?,
                error = '',
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
            """,
            (reply, turn_id),
        )

    def fail_chat_turn(self, turn_id: str, *, error: str, reply: str = "") -> None:
        """Mark a popup chat turn as failed while preserving visible copy."""
        self._execute_write(
            """
            UPDATE chat_turns
            SET status = 'failed',
                reply = ?,
                error = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE turn_id = ?
            """,
            (reply, error, turn_id),
        )

    def get_chat_turn(self, turn_id: str) -> dict[str, Any] | None:
        """Return one durable popup chat turn by id."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT turn_id, session, scope, subject_id, subject_title, message,
                   status, reply, error, created_at, updated_at
            FROM chat_turns
            WHERE turn_id = ?
            """,
            (turn_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_chat_turns(
        self,
        *,
        session: str = "popup",
        scope: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Return recent popup chat turns in display order."""
        self._ensure_fresh_read()
        clauses = ["session = ?"]
        params: list[Any] = [session or "popup"]
        if scope:
            clauses.append("scope = ?")
            params.append(scope)
        params.append(max(1, int(limit)))
        cursor = self.conn.execute(
            f"""
            SELECT turn_id, session, scope, subject_id, subject_title, message,
                   status, reply, error, created_at, updated_at
            FROM (
                SELECT turn_id, session, scope, subject_id, subject_title, message,
                       status, reply, error, created_at, updated_at
                FROM chat_turns
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, turn_id DESC
                LIMIT ?
            )
            ORDER BY created_at ASC, turn_id ASC
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # LLM usage ledger
    # ------------------------------------------------------------------

    def insert_llm_usage(
        self,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        estimated_cost_cny: float,
        caller: str = "",
        success: bool = True,
        cached_input_tokens: int = 0,
    ) -> int:
        """Append one LLM-call usage record.

        ``cached_input_tokens`` (v0.3.28+) is the portion of
        ``prompt_tokens`` served from provider-side prompt cache —
        always ``<= prompt_tokens``. 0 means no cache use. Used by
        ``cost --by caller`` to compute hit rates and by
        ``estimate_cost`` to discount cached tokens correctly.
        """
        total = max(0, prompt_tokens) + max(0, completion_tokens)
        cursor = self._execute_write(
            """INSERT INTO llm_usage
               (provider, model, caller, prompt_tokens, completion_tokens,
                total_tokens, cached_input_tokens, estimated_cost_cny,
                success)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                provider or "",
                model or "",
                caller or "",
                int(max(0, prompt_tokens)),
                int(max(0, completion_tokens)),
                int(total),
                int(max(0, cached_input_tokens)),
                float(estimated_cost_cny),
                1 if success else 0,
            ),
        )
        return cursor.lastrowid or 0

    def query_llm_usage_by_day(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-day aggregates for the last ``days`` days.

        Each row: {day, calls, prompt_tokens, completion_tokens,
        total_tokens, cost_cny}. Days with zero usage are omitted —
        the CLI fills gaps for display.
        """
        cursor = self.conn.execute(
            """
            SELECT date(timestamp, 'localtime') AS day,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY day
            ORDER BY day DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_provider(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-(provider, model) totals over the last ``days`` days."""
        cursor = self.conn.execute(
            """
            SELECT provider,
                   model,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY provider, model
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_by_caller(
        self,
        *,
        days: int = 7,
    ) -> list[dict[str, Any]]:
        """Return per-caller totals over the last ``days`` days.

        ``caller`` is a free-form string the LLM service tags into each
        row (e.g. ``discovery.evaluate`` / ``recommendation.write`` /
        ``soul.profile``). Untagged calls land under ``""`` which the
        CLI renders as ``(untagged)``. Result is sorted by cost so the
        first row is the most expensive caller.

        v0.3.28+ also returns ``cached_input_tokens`` so the CLI can
        compute and surface per-caller cache hit rates — a low rate
        (< 30%) signals prompt-prefix instability worth investigating.
        """
        cursor = self.conn.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (max(1, int(days)),),
        )
        return [dict(row) for row in cursor.fetchall()]

    def query_llm_usage_total(self, *, days: int = 7) -> dict[str, Any]:
        """Return a single-row total for the last ``days`` days."""
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(total_tokens), 0) AS total_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE timestamp >= datetime('now', '-' || ? || ' day', 'localtime')
            """,
            (max(1, int(days)),),
        )
        row = cursor.fetchone()
        return (
            dict(row)
            if row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

    def max_llm_usage_id(self) -> int:
        """Return the highest currently-stored ``llm_usage.id`` (0 if empty).

        Used as a checkpoint for "what's been billed since this point"
        queries — the init / discovery cycle wrappers snapshot it on
        entry and pass it to ``query_llm_usage_since_id`` on exit to
        scope the cost summary to that single phase.
        """
        cursor = self.conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM llm_usage")
        row = cursor.fetchone()
        return int(row["m"]) if row else 0

    def query_llm_usage_since_id(self, *, since_id: int) -> dict[str, Any]:
        """Return per-caller breakdown + totals for rows ``id > since_id``.

        Output: ``{"total": {calls, prompt_tokens, completion_tokens,
        cost_cny}, "by_caller": [{caller, calls, ...}, ...]}``. Bound
        to a single phase by passing ``max_llm_usage_id()`` taken at
        the phase entry.
        """
        total_cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            """,
            (int(since_id),),
        )
        total_row = total_cursor.fetchone()
        total = (
            dict(total_row)
            if total_row
            else {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_input_tokens": 0,
                "cost_cny": 0.0,
            }
        )

        caller_cursor = self.conn.execute(
            """
            SELECT COALESCE(caller, '') AS caller,
                   COUNT(*) AS calls,
                   COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
                   COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
                   COALESCE(SUM(cached_input_tokens), 0) AS cached_input_tokens,
                   COALESCE(SUM(estimated_cost_cny), 0) AS cost_cny
            FROM llm_usage
            WHERE id > ?
            GROUP BY caller
            ORDER BY cost_cny DESC
            """,
            (int(since_id),),
        )
        return {
            "total": total,
            "by_caller": [dict(row) for row in caller_cursor.fetchall()],
        }

    def query_events(
        self,
        *,
        event_types: list[str] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        keyword: str = "",
        limit: int = 100,
        satisfaction_modes: frozenset[str] | None = None,
        after_event_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Query events with optional filters.

        ``satisfaction_modes`` filters by ``inferred_satisfaction``. When
        the set includes ``"unknown"``, rows with a NULL classification
        (pre-migration legacy rows) are also returned.

        ``after_event_id`` restricts to rows with ``id`` strictly greater
        than the given watermark — used by the cognition cycle to read only
        events not yet folded into awareness. Result order is unchanged
        (newest-first); callers that need chronological order reverse it.
        """
        sql = "SELECT * FROM events"
        clauses: list[str] = []
        params: list[Any] = []

        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            clauses.append(f"event_type IN ({placeholders})")
            params.extend(event_types)

        if after_event_id is not None:
            clauses.append("id > ?")
            params.append(after_event_id)

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if keyword:
            like = f"%{keyword}%"
            clauses.append("(url LIKE ? OR title LIKE ? OR metadata LIKE ?)")
            params.extend([like, like, like])

        if satisfaction_modes is not None:
            modes = list(satisfaction_modes)
            mode_clauses: list[str] = []
            if modes:
                placeholders = ", ".join("?" for _ in modes)
                mode_clauses.append(f"inferred_satisfaction IN ({placeholders})")
                params.extend(modes)
            if "unknown" in satisfaction_modes:
                mode_clauses.append("inferred_satisfaction IS NULL")
            if mode_clauses:
                clauses.append("(" + " OR ".join(mode_clauses) + ")")
            else:
                # Empty modes set explicitly requested → match nothing.
                clauses.append("1 = 0")

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def count_events_by_type(
        self,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, int]:
        """Count events grouped by event type."""
        sql = "SELECT event_type, COUNT(*) AS count FROM events"
        clauses: list[str] = []
        params: list[Any] = []

        if start_time is not None:
            clauses.append("created_at >= ?")
            params.append(start_time.isoformat(sep=" "))

        if end_time is not None:
            clauses.append("created_at <= ?")
            params.append(end_time.isoformat(sep=" "))

        if clauses:
            sql = f"{sql} WHERE {' AND '.join(clauses)}"

        sql = f"{sql} GROUP BY event_type ORDER BY event_type ASC"
        cursor = self.conn.execute(sql, params)
        return {str(row["event_type"]): int(row["count"]) for row in cursor.fetchall()}

    def count_events_by_source_platform(self) -> dict[str, int]:
        """Count behavior events grouped by normalized source platform.

        Uses the ``source_platform`` generated column + index added in
        :meth:`_ensure_event_read_indexes`, so this is an indexed GROUP BY
        (~50ms over 800k+ rows) instead of the fallback in
        ``api.app._count_events_by_source_platform`` which materialized every
        row and parsed each ``metadata`` JSON in Python — ~1s and a full
        event-loop block every time the source-share suggestion endpoint opened.

        Returns platform key -> count, including an ``unknown`` bucket for
        legacy events whose ``metadata`` predates the field.
        """
        cursor = self.conn.execute(
            "SELECT source_platform, COUNT(*) AS n FROM events GROUP BY source_platform"
        )
        return {str(row["source_platform"]): int(row["n"]) for row in cursor.fetchall()}

    def cache_content(self, bvid: str, **kwargs: Any) -> None:
        """Cache discovered content.

        Args:
            bvid: Video BV ID.
            **kwargs: Content fields.
        """
        import json

        self._execute_write(
            """
            INSERT INTO content_cache (
                bvid,
                title,
                up_name,
                up_mid,
                duration,
                tags,
                topic_key,
                topic_group,
                style_key,
                franchise_key,
                description,
                cover_url,
                view_count,
                like_count,
                favorite_count,
                collect_count,
                comment_count,
                share_count,
                danmaku_count,
                reply_count,
                retweet_count,
                bookmark_count,
                relevance_score,
                relevance_reason,
                pool_expression,
                pool_topic_label,
                candidate_tier,
                last_scored_at,
                source,
                content_id,
                content_url,
                source_platform,
                author_name,
                body_text,
                content_type,
                source_keyword_id
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                CURRENT_TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(bvid) DO UPDATE SET
                title = excluded.title,
                up_name = excluded.up_name,
                up_mid = excluded.up_mid,
                duration = excluded.duration,
                tags = excluded.tags,
                -- Preserve LLM-classified fields: when the incoming value
                -- is empty/zero, keep the existing DB value.  This prevents
                -- re-ingest from raw sources (e.g. xhs extension re-sending
                -- the same notes on every page load) from wiping out
                -- classifications that classify_pool_backlog has written.
                topic_key = COALESCE(
                    NULLIF(excluded.topic_key, ''),
                    content_cache.topic_key,
                    ''
                ),
                topic_group = COALESCE(
                    NULLIF(excluded.topic_group, ''),
                    content_cache.topic_group,
                    ''
                ),
                style_key = COALESCE(
                    NULLIF(excluded.style_key, ''),
                    content_cache.style_key,
                    ''
                ),
                franchise_key = COALESCE(
                    NULLIF(excluded.franchise_key, ''),
                    content_cache.franchise_key,
                    ''
                ),
                description = excluded.description,
                cover_url = excluded.cover_url,
                view_count = excluded.view_count,
                like_count = excluded.like_count,
                favorite_count = excluded.favorite_count,
                collect_count = excluded.collect_count,
                comment_count = excluded.comment_count,
                share_count = excluded.share_count,
                danmaku_count = excluded.danmaku_count,
                reply_count = excluded.reply_count,
                retweet_count = excluded.retweet_count,
                bookmark_count = excluded.bookmark_count,
                relevance_score = CASE
                    WHEN excluded.relevance_score > 0 THEN excluded.relevance_score
                    ELSE COALESCE(content_cache.relevance_score, 0)
                END,
                relevance_reason = COALESCE(
                    NULLIF(excluded.relevance_reason, ''),
                    content_cache.relevance_reason,
                    ''
                ),
                pool_expression = COALESCE(
                    NULLIF(excluded.pool_expression, ''),
                    content_cache.pool_expression,
                    ''
                ),
                pool_topic_label = COALESCE(
                    NULLIF(excluded.pool_topic_label, ''),
                    content_cache.pool_topic_label,
                    ''
                ),
                candidate_tier = excluded.candidate_tier,
                last_scored_at = CURRENT_TIMESTAMP,
                -- Re-fresh items previously trim-suppressed: 'suppressed' is
                -- an internal diversity decision (over-quota cuts, topic cap),
                -- not a user signal. When a discovery strategy re-finds the
                -- item it deserves another shot. Without this, B站 trending
                -- (which churns slowly) stays bottlenecked because most hot
                -- BVIDs are already cached as 'suppressed' from earlier
                -- trim cycles. User-driven states ('shown', 'feedbacked',
                -- 'purged_by_dislike') are preserved. Low-score suppressed
                -- rows only revive after a fresh/effective score meets the
                -- unified admission floor.
                pool_status = CASE
                    WHEN content_cache.pool_status = 'suppressed'
                         AND (
                            CASE
                                WHEN excluded.relevance_score > 0 THEN excluded.relevance_score
                                ELSE COALESCE(content_cache.relevance_score, 0)
                            END
                         ) >= ?
                    THEN 'fresh'
                    ELSE content_cache.pool_status
                END,
                source = excluded.source,
                content_id = excluded.content_id,
                content_url = excluded.content_url,
                source_platform = excluded.source_platform,
                author_name = COALESCE(
                    NULLIF(excluded.author_name, ''),
                    content_cache.author_name,
                    ''
                ),
                body_text = COALESCE(
                    NULLIF(excluded.body_text, ''),
                    content_cache.body_text,
                    ''
                ),
                content_type = COALESCE(
                    NULLIF(excluded.content_type, ''),
                    content_cache.content_type,
                    'video'
                ),
                -- P1.8: keep the producing-keyword provenance once set; a later
                -- re-ingest from a source that doesn't carry the id (NULL) must
                -- not wipe it.
                source_keyword_id = COALESCE(
                    excluded.source_keyword_id,
                    content_cache.source_keyword_id
                )
            """,
            (
                bvid,
                kwargs.get("title", ""),
                kwargs.get("up_name", ""),
                kwargs.get("up_mid", 0),
                kwargs.get("duration", 0),
                json.dumps(kwargs.get("tags", []), ensure_ascii=False),
                kwargs.get("topic_key", ""),
                kwargs.get("topic_group", ""),
                _normalize_style_key_for_storage(kwargs.get("style_key", "")),
                kwargs.get("franchise_key", ""),
                kwargs.get("description", ""),
                kwargs.get("cover_url", ""),
                kwargs.get("view_count", 0),
                kwargs.get("like_count", 0),
                kwargs.get("favorite_count", 0),
                kwargs.get("collect_count", 0),
                kwargs.get("comment_count", 0),
                kwargs.get("share_count", 0),
                kwargs.get("danmaku_count", 0),
                kwargs.get("reply_count", 0),
                kwargs.get("retweet_count", 0),
                kwargs.get("bookmark_count", 0),
                kwargs.get("relevance_score", self._pool_admission_min_score()),
                kwargs.get("relevance_reason", ""),
                kwargs.get("pool_expression", ""),
                kwargs.get("pool_topic_label", ""),
                kwargs.get("candidate_tier", "primary"),
                kwargs.get("source", ""),
                kwargs.get("content_id", bvid),
                kwargs.get("content_url", ""),
                kwargs.get("source_platform", "bilibili"),
                kwargs.get("author_name", ""),
                kwargs.get("body_text", ""),
                kwargs.get("content_type", "video") or "video",
                self._coerce_source_keyword_id(kwargs.get("source_keyword_id")),
                self._pool_admission_min_score(),
            ),
        )

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

    @staticmethod
    def _candidate_value(candidate: object, key: str, default: Any = "") -> Any:
        if isinstance(candidate, Mapping):
            return candidate.get(key, default)
        return getattr(candidate, key, default)

    @staticmethod
    def _candidate_json_payload(value: object, *, default: object) -> str:
        if isinstance(value, str):
            try:
                json.loads(value)
            except json.JSONDecodeError:
                return json.dumps(default, ensure_ascii=False)
            return value
        try:
            return json.dumps(default if value is None else value, ensure_ascii=False)
        except TypeError:
            return json.dumps(default, ensure_ascii=False)

    def enqueue_discovery_candidates(
        self,
        candidates: Sequence[Any],
        *,
        max_pending_per_source: int | None = None,
    ) -> int:
        """Insert raw discovery candidates into the pending evaluation queue.

        Existing ``candidate_key`` rows are treated as rediscovery signals: the
        row is not duplicated, but ``last_seen_at`` is refreshed so active
        sources do not look stale.
        """

        inserted = 0
        touched_sources: set[str] = set()
        for candidate in candidates:
            candidate_key = str(self._candidate_value(candidate, "candidate_key", "") or "").strip()
            if not candidate_key:
                continue
            source_platform = str(self._candidate_value(candidate, "source_platform", "") or "")
            tags = self._candidate_json_payload(
                self._candidate_value(candidate, "tags", []),
                default=[],
            )
            raw_payload = self._candidate_json_payload(
                self._candidate_value(candidate, "raw_payload", {}),
                default={},
            )
            score_threshold = float(self._candidate_value(candidate, "score_threshold", 0.0) or 0.0)
            cursor = self._execute_write(
                """
                INSERT OR IGNORE INTO discovery_candidates (
                    candidate_key,
                    status,
                    source_platform,
                    source_strategy,
                    source_context,
                    content_type,
                    body_text,
                    bvid,
                    content_id,
                    content_url,
                    title,
                    author_name,
                    up_name,
                    up_mid,
                    description,
                    cover_url,
                    duration,
                    view_count,
                    like_count,
                    favorite_count,
                    collect_count,
                    comment_count,
                    share_count,
                    danmaku_count,
                    reply_count,
                    retweet_count,
                    bookmark_count,
                    tags,
                    candidate_tier,
                    score_threshold,
                    raw_payload,
                    source_keyword_id
                )
                VALUES (
                    ?, 'pending_eval', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    candidate_key,
                    source_platform,
                    str(self._candidate_value(candidate, "source_strategy", "") or ""),
                    str(self._candidate_value(candidate, "source_context", "") or ""),
                    str(self._candidate_value(candidate, "content_type", "video") or "video"),
                    str(self._candidate_value(candidate, "body_text", "") or ""),
                    str(self._candidate_value(candidate, "bvid", "") or ""),
                    str(self._candidate_value(candidate, "content_id", "") or ""),
                    str(self._candidate_value(candidate, "content_url", "") or ""),
                    str(self._candidate_value(candidate, "title", "") or ""),
                    str(self._candidate_value(candidate, "author_name", "") or ""),
                    str(self._candidate_value(candidate, "up_name", "") or ""),
                    int(self._candidate_value(candidate, "up_mid", 0) or 0),
                    str(self._candidate_value(candidate, "description", "") or ""),
                    str(self._candidate_value(candidate, "cover_url", "") or ""),
                    int(self._candidate_value(candidate, "duration", 0) or 0),
                    int(self._candidate_value(candidate, "view_count", 0) or 0),
                    int(self._candidate_value(candidate, "like_count", 0) or 0),
                    int(self._candidate_value(candidate, "favorite_count", 0) or 0),
                    int(self._candidate_value(candidate, "collect_count", 0) or 0),
                    int(self._candidate_value(candidate, "comment_count", 0) or 0),
                    int(self._candidate_value(candidate, "share_count", 0) or 0),
                    int(self._candidate_value(candidate, "danmaku_count", 0) or 0),
                    int(self._candidate_value(candidate, "reply_count", 0) or 0),
                    int(self._candidate_value(candidate, "retweet_count", 0) or 0),
                    int(self._candidate_value(candidate, "bookmark_count", 0) or 0),
                    tags,
                    str(self._candidate_value(candidate, "candidate_tier", "primary") or "primary"),
                    score_threshold,
                    raw_payload,
                    self._coerce_source_keyword_id(
                        self._candidate_value(candidate, "source_keyword_id", None)
                    ),
                ),
            )
            if source_platform:
                touched_sources.add(source_platform)
            if cursor.rowcount > 0:
                inserted += 1
                continue
            self._execute_write(
                """
                UPDATE discovery_candidates
                SET last_seen_at = CURRENT_TIMESTAMP
                WHERE candidate_key = ?
                """,
                (candidate_key,),
            )
        if max_pending_per_source is not None:
            max_pending = max(0, int(max_pending_per_source))
            if max_pending > 0:
                for source in touched_sources:
                    self.trim_discovery_candidates_for_source(
                        source_platform=source,
                        max_pending=max_pending,
                    )
        return inserted

    def trim_discovery_candidates_for_source(
        self,
        *,
        source_platform: str,
        max_pending: int,
    ) -> int:
        """Drop oldest candidate rows for one source over a queue cap.

        In-flight ``evaluating`` rows are never deleted. Terminal rows are
        trimmed before pending/evaluated rows so active raw material is kept
        whenever possible.
        """

        source = str(source_platform or "").strip()
        cap = max(0, int(max_pending))
        if not source or cap <= 0:
            return 0
        self._ensure_fresh_read()
        row = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM discovery_candidates
            WHERE source_platform = ?
            """,
            (source,),
        ).fetchone()
        current = int(row["count"] if row else 0)
        excess = current - cap
        if excess <= 0:
            return 0
        cursor = self._execute_write(
            """
            DELETE FROM discovery_candidates
            WHERE id IN (
                SELECT id
                FROM discovery_candidates
                WHERE source_platform = ?
                  AND status != 'evaluating'
                ORDER BY
                    CASE
                        WHEN status IN (
                            'cached',
                            'rejected_low_score',
                            'rejected_duplicate',
                            'rejected_cache_admission',
                            'rejected_recently_viewed',
                            'rejected_franchise_quota',
                            'failed_eval'
                        ) THEN 0
                        ELSE 1
                    END ASC,
                    last_seen_at ASC,
                    id ASC
                LIMIT ?
            )
            """,
            (source, excess),
        )
        return int(cursor.rowcount)

    def reset_stale_discovery_candidate_evaluations(
        self,
        *,
        max_age_minutes: int = 30,
    ) -> int:
        """Release evaluator claims left behind by a crashed process."""

        minutes = max(1, int(max_age_minutes))
        cursor = self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = 'pending_eval',
                claimed_at = NULL,
                eval_error = 'stale evaluating claim reset'
            WHERE status = 'evaluating'
              AND claimed_at IS NOT NULL
              AND claimed_at < datetime('now', ?)
            """,
            (f"-{minutes} minutes",),
        )
        return int(cursor.rowcount)

    def claim_discovery_candidates_for_eval(self, *, limit: int) -> list[dict[str, Any]]:
        """Claim a mixed-source batch of pending candidates for evaluation."""

        claim_limit = max(0, int(limit))
        if claim_limit <= 0:
            return []
        self._ensure_fresh_read()
        # Peek a bounded window and round-robin in Python so one noisy source
        # cannot monopolize a mixed evaluator batch.
        cursor = self.conn.execute(
            """
            SELECT *
            FROM discovery_candidates
            WHERE status = 'pending_eval'
            ORDER BY last_seen_at ASC, id ASC
            LIMIT ?
            """,
            (max(claim_limit * 4, claim_limit),),
        )
        pending = [dict(row) for row in cursor.fetchall()]
        if not pending:
            return []

        source_order: list[str] = []
        by_source: dict[str, list[dict[str, Any]]] = {}
        for row in pending:
            source = str(row.get("source_platform") or "unknown")
            if source not in by_source:
                source_order.append(source)
                by_source[source] = []
            by_source[source].append(row)

        selected: list[dict[str, Any]] = []
        while len(selected) < claim_limit:
            added = False
            for source in source_order:
                rows = by_source[source]
                if not rows:
                    continue
                selected.append(rows.pop(0))
                added = True
                if len(selected) >= claim_limit:
                    break
            if not added:
                break

        ids = [int(row["id"]) for row in selected]
        placeholders = ", ".join("?" for _ in ids)
        self._execute_write(
            f"""
            UPDATE discovery_candidates
            SET status = 'evaluating',
                claimed_at = CURRENT_TIMESTAMP,
                eval_error = ''
            WHERE id IN ({placeholders})
              AND status = 'pending_eval'
            """,
            ids,
        )
        claimed_rows = self.conn.execute(
            f"""
            SELECT id
            FROM discovery_candidates
            WHERE id IN ({placeholders})
              AND status = 'evaluating'
            """,
            ids,
        ).fetchall()
        claimed_ids = {int(row["id"]) for row in claimed_rows}
        claimed = [row for row in selected if int(row["id"]) in claimed_ids]
        for row in claimed:
            row["status"] = "evaluating"
        return claimed

    def get_evaluated_discovery_candidates_for_admission(
        self,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Return evaluated candidates still waiting for content-cache admission."""

        admission_limit = max(0, int(limit))
        if admission_limit <= 0:
            return []
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT *
            FROM discovery_candidates
            WHERE status = 'evaluated'
            ORDER BY evaluated_at ASC, last_seen_at ASC, id ASC
            LIMIT ?
            """,
            (admission_limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_discovery_candidate_evaluations(
        self,
        evaluations: Sequence[Mapping[str, Any]],
    ) -> int:
        """Persist evaluator output back onto claimed candidate rows."""

        updated = 0
        for evaluation in evaluations:
            candidate_id = int(evaluation.get("candidate_id") or evaluation.get("id") or 0)
            if candidate_id <= 0:
                continue
            cursor = self._execute_write(
                """
                UPDATE discovery_candidates
                SET status = ?,
                    topic_key = ?,
                    topic_group = ?,
                    style_key = ?,
                    franchise_key = ?,
                    relevance_score = ?,
                    relevance_reason = ?,
                    pool_expression = ?,
                    pool_topic_label = ?,
                    eval_error = ?,
                    eval_attempts = 0,
                    batch_eval_attempts = 0,
                    evaluated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'evaluating'
                """,
                (
                    str(evaluation.get("status") or "evaluated"),
                    str(evaluation.get("topic_key") or ""),
                    str(evaluation.get("topic_group") or ""),
                    _normalize_style_key_for_storage(evaluation.get("style_key")),
                    str(evaluation.get("franchise_key") or ""),
                    float(evaluation.get("relevance_score") or evaluation.get("score") or 0.0),
                    str(evaluation.get("relevance_reason") or evaluation.get("reason") or ""),
                    str(evaluation.get("pool_expression") or ""),
                    str(evaluation.get("pool_topic_label") or ""),
                    str(evaluation.get("eval_error") or ""),
                    candidate_id,
                ),
            )
            if cursor.rowcount > 0:
                updated += 1
        return updated

    def reset_discovery_candidates_to_pending(
        self,
        candidate_ids: Sequence[int],
        *,
        reason: str = "",
        max_attempts: int = 5,
        max_batch_attempts: int = 50,
        increment_attempts: bool = True,
    ) -> int:
        """Release claimed candidates after a transient evaluator failure."""

        ids = [int(candidate_id) for candidate_id in candidate_ids if int(candidate_id) > 0]
        if not ids:
            return 0
        placeholders = ", ".join("?" for _ in ids)
        if not increment_attempts:
            batch_attempts_limit = max(1, int(max_batch_attempts))
            cursor = self._execute_write(
                f"""
                UPDATE discovery_candidates
                SET batch_eval_attempts = batch_eval_attempts + 1,
                    status = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN 'failed_eval'
                        ELSE 'pending_eval'
                    END,
                    claimed_at = NULL,
                    eval_error = ?,
                    evaluated_at = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN CURRENT_TIMESTAMP
                        ELSE evaluated_at
                    END,
                    last_seen_at = CASE
                        WHEN batch_eval_attempts + 1 >= ? THEN last_seen_at
                        ELSE CURRENT_TIMESTAMP
                    END
                WHERE id IN ({placeholders})
                  AND status = 'evaluating'
                """,
                (
                    batch_attempts_limit,
                    str(reason),
                    batch_attempts_limit,
                    batch_attempts_limit,
                    *ids,
                ),
            )
            return int(cursor.rowcount)

        attempts_limit = max(1, int(max_attempts))
        cursor = self._execute_write(
            f"""
            UPDATE discovery_candidates
            SET eval_attempts = eval_attempts + 1,
                status = CASE
                    WHEN eval_attempts + 1 >= ? THEN 'failed_eval'
                    ELSE 'pending_eval'
                END,
                claimed_at = NULL,
                eval_error = ?,
                evaluated_at = CASE
                    WHEN eval_attempts + 1 >= ? THEN CURRENT_TIMESTAMP
                    ELSE evaluated_at
                END,
                last_seen_at = CASE
                    WHEN eval_attempts + 1 >= ? THEN last_seen_at
                    ELSE CURRENT_TIMESTAMP
                END
            WHERE id IN ({placeholders})
              AND status = 'evaluating'
            """,
            (attempts_limit, str(reason), attempts_limit, attempts_limit, *ids),
        )
        return int(cursor.rowcount)

    def mark_discovery_candidate_cached(self, candidate_id: int) -> None:
        """Mark an evaluated candidate as successfully inserted into content_cache."""

        self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = 'cached',
                cached_at = CURRENT_TIMESTAMP,
                eval_error = '',
                eval_attempts = 0,
                batch_eval_attempts = 0
            WHERE id = ?
              AND status IN ('evaluating', 'evaluated')
            """,
            (int(candidate_id),),
        )

    def reject_discovery_candidate(
        self,
        candidate_id: int,
        *,
        status: str,
        reason: str = "",
    ) -> None:
        """Mark a candidate as rejected before it enters content_cache."""

        self._execute_write(
            """
            UPDATE discovery_candidates
            SET status = ?,
                eval_error = ?,
                evaluated_at = COALESCE(evaluated_at, CURRENT_TIMESTAMP)
            WHERE id = ?
              AND status IN ('evaluating', 'evaluated')
            """,
            (status, reason, int(candidate_id)),
        )

    def count_discovery_candidates_by_status(self) -> dict[str, int]:
        """Return candidate queue counts grouped by lifecycle status."""

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM discovery_candidates
            GROUP BY status
            ORDER BY status ASC
            """
        )
        return {str(row["status"]): int(row["count"]) for row in cursor.fetchall()}

    def get_existing_discovery_candidate_keys(self, candidate_keys: Sequence[str]) -> set[str]:
        """Return candidate keys already present in the raw evaluation queue."""

        clean = _unique_clean_strings(candidate_keys)
        if not clean:
            return set()
        self._ensure_fresh_read()
        existing: set[str] = set()
        for chunk in _chunks(clean, 900):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT candidate_key
                FROM discovery_candidates
                WHERE candidate_key IN ({placeholders})
                """,
                chunk,
            )
            existing.update(str(row["candidate_key"]) for row in cursor.fetchall())
        return existing

    def get_existing_content_cache_ids(self, content_ids: Sequence[str]) -> set[str]:
        """Return BVID/content ids that already exist in the evaluated content cache."""

        clean = _unique_clean_strings(content_ids)
        if not clean:
            return set()
        self._ensure_fresh_read()
        existing: set[str] = set()
        for chunk in _chunks(clean, 450):
            placeholders = ", ".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"""
                SELECT bvid, content_id
                FROM content_cache
                WHERE bvid IN ({placeholders})
                   OR content_id IN ({placeholders})
                """,
                [*chunk, *chunk],
            )
            for row in cursor.fetchall():
                bvid = str(row["bvid"] or "").strip()
                content_id = str(row["content_id"] or "").strip()
                if bvid:
                    existing.add(bvid)
                if content_id:
                    existing.add(content_id)
        return existing

    def count_discovery_candidates_by_source_status(self) -> dict[str, dict[str, int]]:
        """Return candidate queue counts grouped by source and lifecycle status."""

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT source_platform, status, COUNT(*) AS count
            FROM discovery_candidates
            GROUP BY source_platform, status
            ORDER BY source_platform ASC, status ASC
            """
        )
        counts: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            source = str(row["source_platform"] or "unknown")
            status = str(row["status"])
            counts.setdefault(source, {})[status] = int(row["count"])
        return counts

    def count_discovery_pending_raw_material_by_source(self) -> dict[str, int]:
        """Return not-yet-cached raw candidate counts grouped by source."""

        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT source_platform, COUNT(*) AS count
            FROM discovery_candidates
            WHERE status IN ('pending_eval', 'evaluating', 'evaluated')
            GROUP BY source_platform
            ORDER BY source_platform ASC
            """
        )
        return {str(row["source_platform"] or "unknown"): int(row["count"]) for row in cursor}

    def _count_pending_discovery_raw_material(self) -> int:
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM discovery_candidates
            WHERE status IN ('pending_eval', 'evaluating', 'evaluated')
            """
        )
        row = cursor.fetchone()
        return int(row["count"] if row else 0)

    def get_cached_content(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get cached discovered content ordered by basic quality signals."""
        cursor = self.conn.execute(
            """
            SELECT *
            FROM content_cache
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

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

    def get_pool_candidates(
        self,
        limit: int = 20,
        *,
        max_per_topic_group: int = 0,
        xhs_self_nickname: str = "",
        platform: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get fresh recommendation candidates directly from the discovery pool.

        ``max_per_topic_group`` caps how many items from any single
        ``topic_group`` enter the relevance-ordered head. Without this
        cap, a 600-item pool that contains 270 distinct topic_groups still
        produces a top-50 shortlist concentrated in ~10 head groups,
        because high-relevance candidates cluster around the user's
        primary interests; long-tail groups (197 with a single item each
        in the typical pool) never reach the candidate window. A cap lets
        obvious favourites keep a strong presence while opening room for
        different groups in the candidate window.

        v0.3.153+ (dd09b3d0): the default is ``0`` (unrestricted) — the
        cap is opt-in now. Callers that want concentrated-topic head
        trimming pass an explicit cap (e.g. ``max_per_topic_group=5``).

        ``platform`` (optional) restricts candidates to a single
        ``source_platform`` (e.g. ``"bilibili"`` / ``"xiaohongshu"``),
        letting the recommender serve a platform-filtered batch instead of
        fetching everything and filtering on the client. Empty/None = all
        platforms (unchanged behaviour).

        Rows claimed by the surprise (delight) channel are excluded via
        ``_DELIGHT_CLAIM_GUARD_SQL`` — a delight that was delivered or is
        currently queue-eligible must never be duplicated by the regular
        feed. ``count_pool_candidates`` applies the same guard so the
        "还有 N 条" display stays in sync with what serve() can load.

        Notes:
            xhs rows without ``xsec_token`` in their ``content_url`` are
            excluded. Bare xhs URLs get rejected by xhs with error 300031
            when shared outbound, so surfacing them in recommendations
            would just mint dead links. Tokens get backfilled by the
            MAIN-world sniffer as the user browses xhs; bare rows become
            eligible again once ``_backfill_xhs_tokens`` upgrades them.
        """
        self._ensure_fresh_read()
        # Over-fetch widely so the per-group filter still leaves headroom
        # for the downstream balance pass.
        fetch_limit = max(limit * 8, 80)
        min_score = self._pool_admission_min_score()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_guard_sql = _DELIGHT_CLAIM_GUARD_SQL
        platform_clause = ""
        platform_term: str = (platform or "").strip().lower()
        if platform_term:
            platform_clause = " AND source_platform = ?"
        if max_per_topic_group <= 0:
            sql = f"""
                SELECT *
                FROM content_cache
                WHERE {_POOL_SERVABLE_STATUS_SQL}
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND COALESCE(style_key, '') != ''
                  AND COALESCE(topic_group, '') != ''
                  AND (
                    source_platform != 'xiaohongshu'
                    OR (content_url LIKE '%xsec_token=%' AND discovered_at >= '2026-08-15')
                  )
                  {guard_sql}
                  {delight_guard_sql}
                  {platform_clause}
                  {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
                ORDER BY
                    CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                    relevance_score DESC,
                    last_scored_at DESC,
                    view_count DESC,
                    bvid ASC
                LIMIT ?
            """
            params: tuple[Any, ...] = (min_score, *guard_params, fetch_limit)
            if platform_term:
                params = (min_score, *guard_params, platform_term, fetch_limit)
        else:
            # Per-group rank via window function: keep the top-N classified
            # items of each topic_group, then order the remainder by relevance.
            sql = f"""
                WITH ranked AS (
                    SELECT *,
                           ROW_NUMBER() OVER (
                               PARTITION BY topic_group
                               ORDER BY
                                   relevance_score DESC,
                                   last_scored_at DESC,
                                   view_count DESC,
                                   bvid ASC
                           ) AS group_rank
                    FROM content_cache
                    WHERE {_POOL_SERVABLE_STATUS_SQL}
                      AND COALESCE(feedback_type, '') != 'dislike'
                      AND COALESCE(relevance_score, 0.0) >= ?
                      AND COALESCE(pool_expression, '') != ''
                      AND COALESCE(pool_topic_label, '') != ''
                      AND COALESCE(style_key, '') != ''
                      AND COALESCE(topic_group, '') != ''
                      AND (
                        source_platform != 'xiaohongshu'
                        OR (content_url LIKE '%xsec_token=%' AND discovered_at >= '2026-08-15')
                      )
                      {guard_sql}
                      {delight_guard_sql}
                      {platform_clause}
                      {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
                )
                SELECT * FROM ranked
                WHERE group_rank <= ?
                ORDER BY
                    CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                    relevance_score DESC,
                    last_scored_at DESC,
                    view_count DESC,
                    bvid ASC
                LIMIT ?
            """
            params = (min_score, *guard_params, max_per_topic_group, fetch_limit)
            if platform_term:
                params = (min_score, *guard_params, platform_term, max_per_topic_group, fetch_limit)
        cursor = self.conn.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return self._balance_pool_rows(rows, limit=limit)

    def count_pool_candidates(
        self, *, max_per_topic_group: int = 0, xhs_self_nickname: str = ""
    ) -> int:
        """Return how many fresh candidates are immediately available for reshuffle.

        v0.3.57+: matches ``get_pool_candidates`` precompute gate — rows
        without ``pool_expression`` / ``pool_topic_label`` are excluded so
        the popup's "还有 N 条" never overstates what serve() can actually
        return.

        v0.3.66+: also requires ``style_key`` / ``topic_group`` — content
        must be classified before it can be served, regardless of source
        platform.

        v0.3.91+: applies the same ``max_per_topic_group`` window as
        ``get_pool_candidates`` so concentrated topic groups don't inflate
        the displayed count beyond what ``serve()`` can actually load.
        """
        return len(
            self._load_available_pool_candidate_rows(
                max_per_topic_group=max_per_topic_group,
                xhs_self_nickname=xhs_self_nickname,
            )
        )

    def _load_available_pool_candidate_rows(
        self, *, max_per_topic_group: int = 0, xhs_self_nickname: str = ""
    ) -> list[dict[str, Any]]:
        """Load rows counted by the frontend-visible pool availability gate.

        Applies ``_DELIGHT_CLAIM_GUARD_SQL`` like ``get_pool_candidates`` so
        the availability count never includes surprise-channel rows serve()
        would refuse to load.
        """
        self._ensure_fresh_read()
        min_score = self._pool_admission_min_score()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        delight_guard_sql = _DELIGHT_CLAIM_GUARD_SQL
        if max_per_topic_group > 0:
            cursor = self.conn.execute(
                f"""
                WITH ranked AS (
                    SELECT bvid, source, source_platform, content_url,
                           ROW_NUMBER() OVER (
                               PARTITION BY topic_group
                               ORDER BY
                                   relevance_score DESC,
                                   last_scored_at DESC,
                                   view_count DESC,
                                   bvid ASC
                           ) AS group_rank
                    FROM content_cache
                    WHERE {_POOL_SERVABLE_STATUS_SQL}
                      AND COALESCE(feedback_type, '') != 'dislike'
                      AND COALESCE(relevance_score, 0.0) >= ?
                      AND COALESCE(pool_expression, '') != ''
                      AND COALESCE(pool_topic_label, '') != ''
                      AND COALESCE(style_key, '') != ''
                      AND COALESCE(topic_group, '') != ''
                      AND (
                        source_platform != 'xiaohongshu'
                        OR (content_url LIKE '%xsec_token=%' AND discovered_at >= '2026-08-15')
                      )
                      {guard_sql}
                      {delight_guard_sql}
                      {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
                )
                SELECT bvid, source, source_platform, content_url
                FROM ranked
                WHERE group_rank <= ?
                """,
                (min_score, *guard_params, max_per_topic_group),
            )
        else:
            cursor = self.conn.execute(
                f"""
                SELECT bvid, source, source_platform, content_url
                FROM content_cache
                WHERE {_POOL_SERVABLE_STATUS_SQL}
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND COALESCE(style_key, '') != ''
                  AND COALESCE(topic_group, '') != ''
                  AND (
                    source_platform != 'xiaohongshu'
                    OR (content_url LIKE '%xsec_token=%' AND discovered_at >= '2026-08-15')
                  )
                  {guard_sql}
                  {delight_guard_sql}
                  {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
                """,
                (min_score, *guard_params),
            )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            rows.append(row_dict)
        return rows

    def count_pool_available_candidates_by_source(
        self, *, max_per_topic_group: int = 0, xhs_self_nickname: str = ""
    ) -> dict[str, int]:
        """Return frontend-visible pool availability grouped by source family."""
        rows = self._load_available_pool_candidate_rows(
            max_per_topic_group=max_per_topic_group,
            xhs_self_nickname=xhs_self_nickname,
        )
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)

    def _load_pool_raw_material_rows(self) -> list[dict[str, Any]]:
        """Load raw fresh material rows governed by the raw ceiling."""
        self._ensure_fresh_read()
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT
                bvid,
                source,
                source_platform,
                content_url,
                relevance_score,
                last_scored_at,
                pool_expression,
                pool_topic_label,
                style_key,
                topic_group
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            (min_score,),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        rows: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            row_dict = dict(row)
            if not str(row_dict.get("bvid", "")).strip():
                continue
            if self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            rows.append(row_dict)
        return rows

    def count_pool_raw_material_candidates(self) -> int:
        """Return raw fresh material count used for raw-ceiling headroom."""
        return (
            len(self._load_pool_raw_material_rows()) + self._count_pending_discovery_raw_material()
        )

    def count_pool_raw_material_by_source(self) -> dict[str, int]:
        """Return raw fresh material grouped by source family.

        Unlike ``count_pool_candidates_by_source()``, this intentionally counts
        pending/unopenable rows such as XHS notes waiting for ``xsec_token``.
        """
        counts: dict[str, int] = defaultdict(int)
        for row in self._load_pool_raw_material_rows():
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        cursor = self.conn.execute(
            """
            SELECT source_platform, source_strategy, COUNT(*) AS count
            FROM discovery_candidates
            WHERE status IN ('pending_eval', 'evaluating', 'evaluated')
            GROUP BY source_platform, source_strategy
            """
        )
        for row in cursor.fetchall():
            source_family = _pool_source_family(row["source_strategy"], row["source_platform"])
            counts[source_family] += int(row["count"])
        return dict(counts)

    def count_pool_readiness(
        self, *, xhs_self_nickname: str = "", allow_stale: bool = False
    ) -> dict[str, int]:
        """Return pool inventory split by immediately servable and pending rows.

        ``available`` is the public "可换" count. ``raw`` is broad fresh
        material before readiness gates. ``pending`` is counted independently:
        recently viewed rows are unavailable, but they are not pending.

        结果缓存 300 秒，避免频繁重复计算（该函数做 4~5 次查询 + 逐行处理，
        开销较大）。``allow_stale=True`` 时缓存过期仍先返回旧值，并在后台
        线程重算（stale-while-revalidate），让读接口永不阻塞在冷算上。
        """
        import time as _time

        # 检查缓存
        if self._pool_readiness_cache is not None:
            cached_at, cached_result = self._pool_readiness_cache
            if _time.time() - cached_at < self._pool_readiness_cache_ttl:
                return dict(cached_result)
            if allow_stale:
                # 过期但允许旧值：先返回，后台线程重算（防抖，避免并发刷爆）
                if not self._pool_readiness_refreshing:
                    self._pool_readiness_refreshing = True
                    import threading

                    def _recalc() -> None:
                        try:
                            self.count_pool_readiness(xhs_self_nickname=xhs_self_nickname)
                        except Exception:
                            pass
                        finally:
                            self._pool_readiness_refreshing = False

                    threading.Thread(target=_recalc, daemon=True).start()
                return dict(cached_result)

        self._ensure_fresh_read()
        min_score = self._pool_admission_min_score()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        raw_cursor = self.conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM content_cache
            WHERE {_POOL_SERVABLE_STATUS_SQL}
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              {guard_sql}
              {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
            """,
            (min_score, *guard_params),
        )
        raw_count = int(raw_cursor.fetchone()["count"])
        # pending 计数：先把"缺池子字段 / 不可链接"的行用 SQL 过滤出来，
        # 再对剩下的少量行做 viewed 判断——避免对全部 servable 行（数万行）
        # 逐行 Python 判断导致冷算 2~7s 阻塞读接口。
        pending_cursor = self.conn.execute(
            f"""
            SELECT bvid, content_id, source, source_platform
            FROM content_cache
            WHERE {_POOL_SERVABLE_STATUS_SQL}
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              {guard_sql}
              {_POOL_NOT_RECENTLY_RECOMMENDED_SQL}
              AND (
                  COALESCE(pool_expression, '') = ''
                  OR COALESCE(pool_topic_label, '') = ''
                  OR COALESCE(style_key, '') = ''
                  OR COALESCE(topic_group, '') = ''
                  OR (
                      (
                          LOWER(COALESCE(source_platform, '')) IN ('xiaohongshu', 'xhs')
                          OR LOWER(COALESCE(source, '')) LIKE 'xhs-%'
                          OR LOWER(COALESCE(source, '')) LIKE 'xhs\_%'
                          OR LOWER(COALESCE(source, '')) LIKE 'xiaohongshu%'
                      )
                      AND COALESCE(content_url, '') NOT LIKE '%xsec_token=%'
                  )
              )
            """,
            (min_score, *guard_params),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        pending_count = 0
        for row in pending_cursor.fetchall():
            if self._is_viewed_row(dict(row), viewed_content_keys):
                continue
            pending_count += 1

        status_counts = self.count_discovery_candidates_by_status()
        pending_eval_count = int(status_counts.get("pending_eval", 0)) + int(
            status_counts.get("evaluating", 0)
        )
        evaluated_pending_count = int(status_counts.get("evaluated", 0))
        discovery_pending_count = pending_eval_count + evaluated_pending_count

        result = {
            "available": self.count_pool_candidates(xhs_self_nickname=xhs_self_nickname),
            "raw": raw_count + discovery_pending_count,
            "pending": pending_count + discovery_pending_count,
            "pending_eval": pending_eval_count,
            "evaluated_pending": evaluated_pending_count,
        }
        # 保存缓存
        self._pool_readiness_cache = (_time.time(), dict(result))
        return result

    def count_pool_candidates_by_source(self) -> dict[str, int]:
        """Return fresh pool counts grouped by discovery source family."""
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            (min_score,),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            counts[source_family] += 1
        return dict(counts)

    def get_pool_distribution_counts(self) -> dict[str, dict[str, int]]:
        """Return fresh pool counts grouped by topic, style, and franchise."""
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT bvid, topic_group, style_key, franchise_key, source, source_platform, content_url
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND COALESCE(pool_expression, '') != ''
              AND COALESCE(pool_topic_label, '') != ''
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            """,
            (min_score,),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        counts: dict[str, dict[str, int]] = {
            "topic_group": defaultdict(int),
            "style_key": defaultdict(int),
            "franchise_key": defaultdict(int),
        }
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            for axis in ("topic_group", "style_key", "franchise_key"):
                value = str(row[axis] or "").strip()
                if value:
                    counts[axis][value] += 1
        return {axis: dict(axis_counts) for axis, axis_counts in counts.items()}

    def get_pool_topic_counts_by_platform(self) -> dict[str, dict[str, int]]:
        """Per-platform ``topic_group`` counts of fresh servable pool rows (P3.1).

        Same servable filter as :meth:`get_pool_distribution_counts`, but keyed by
        ``source_platform`` → ``{platform: {topic_group: count}}`` so the keyword
        planner can avoid topics saturated *on that platform* instead of pool-wide
        (a topic piled up on B站 may be absent on 小红书). Returns ``{}`` on error.
        """
        try:
            min_score = self._pool_admission_min_score()
            cursor = self.conn.execute(
                """
                SELECT bvid, topic_group, style_key, franchise_key,
                       source, source_platform, content_url
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') = 'fresh'
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND COALESCE(pool_expression, '') != ''
                  AND COALESCE(pool_topic_label, '') != ''
                  AND NOT EXISTS (
                    SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
                  )
                """,
                (min_score,),
            )
            viewed_content_keys = self.get_recent_viewed_content_keys()
        except Exception:
            logger.debug("get_pool_topic_counts_by_platform query failed", exc_info=True)
            return {}
        counts: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"], row["source_platform"], row["content_url"]
            ):
                continue
            platform = str(row["source_platform"] or "").strip()
            topic = str(row["topic_group"] or "").strip()
            if not platform or not topic:
                continue
            counts.setdefault(platform, defaultdict(int))[topic] += 1
        return {platform: dict(topics) for platform, topics in counts.items()}

    def get_admitted_topic_counts_by_platform(self) -> dict[str, dict[str, int]]:
        """Per-platform ``topic_group`` counts of ALL admitted content (P3.3).

        Where :meth:`get_pool_topic_counts_by_platform` counts the *current
        servable pool* (a saturation signal — too much right now), this counts
        every non-disliked, linkable row that ever made it into the cache from
        each platform, served or not — a *supply-advantage* signal: which topics
        each platform has actually delivered for this user. The keyword planner
        feeds the top topics back as a data-driven complement to the static
        ``<supply_advantage>`` table (after subtracting the platform's current
        avoid set). Returns ``{}`` on error.
        """
        try:
            min_score = self._pool_admission_min_score()
            cursor = self.conn.execute(
                """
                SELECT topic_group, source, source_platform, content_url
                FROM content_cache
                WHERE COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND COALESCE(topic_group, '') != ''
                """,
                (min_score,),
            )
        except Exception:
            logger.debug("get_admitted_topic_counts_by_platform query failed", exc_info=True)
            return {}
        counts: dict[str, dict[str, int]] = {}
        for row in cursor.fetchall():
            if not _is_linkable_pool_source(
                row["source"], row["source_platform"], row["content_url"]
            ):
                continue
            platform = str(row["source_platform"] or "").strip()
            topic = str(row["topic_group"] or "").strip()
            if not platform or not topic:
                continue
            counts.setdefault(platform, defaultdict(int))[topic] += 1
        return {platform: dict(topics) for platform, topics in counts.items()}

    def canonicalize_topic_groups(self, canonical_map: dict[str, str]) -> int:
        """Rewrite ``content_cache.topic_group`` to canonical form per map.

        v0.3.56+: ``canonical_map`` is built by
        ``RecommendationEngine.prewarm_supergroup_embeddings`` and maps
        normalized (lowered + stripped) topic_group → canonical form.
        Without applying it to the database rows, the merge only fires
        at serve time and downstream analytics (``get_topic_group_samples``,
        per-topic counts in popup status) see the un-merged labels.

        Returns the number of rows actually updated. Empty input or all-
        identity mappings short-circuit to 0.
        """
        if not canonical_map:
            return 0
        # Bulk update: one statement per (src → dst) pair. Pure SQL,
        # no row-level fetch. WAL-friendly because we batch in a single
        # transaction. Only rewrites rows whose lowercased+trimmed
        # topic_group exactly matches the source key — case-preserving
        # storage stays intact for non-matching rows.
        total = 0
        for src, dst in canonical_map.items():
            if src == dst or not src or not dst:
                continue
            cursor = self._execute_write(
                """
                UPDATE content_cache
                SET topic_group = ?
                WHERE LOWER(TRIM(COALESCE(topic_group, ''))) = ?
                  AND COALESCE(topic_group, '') != ?
                """,
                (dst, src, dst),
            )
            total += cursor.rowcount or 0
        return total

    def count_pool_by_franchise(self) -> dict[str, int]:
        """Return ``{franchise_key_lower: count}`` for fresh pool items.

        Used by discovery's pool-wide franchise quota check (v0.3.50+)
        so a franchise that already has many items in the pool can't
        keep accumulating across discovery rounds. Empty franchise_key
        is excluded — most generic content has no IP signal and the
        quota is only meaningful for series / IP / UP-driven groups.
        """
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT LOWER(TRIM(franchise_key)) AS fk, COUNT(*) AS n
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND franchise_key IS NOT NULL
              AND TRIM(franchise_key) != ''
            GROUP BY LOWER(TRIM(franchise_key))
            """,
            (min_score,),
        )
        return {str(row["fk"]): int(row["n"]) for row in cursor.fetchall() if row["fk"]}

    def get_distinct_topic_groups(self) -> list[str]:
        """Return distinct non-empty ``topic_group`` values in the fresh pool.

        Used by recommendation pre-warming so the embedding cache is hot
        before the popup hits ``serve()``. Cheap GROUP BY on a small
        column with no JOIN.
        """
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT DISTINCT topic_group
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND COALESCE(topic_group, '') != ''
            """,
            (min_score,),
        )
        return [str(row[0]) for row in cursor.fetchall() if row and row[0]]

    def get_active_pool_topic_groups(
        self,
        *,
        limit: int = 30,
        min_count: int = 2,
    ) -> list[str]:
        """Return the top ``limit`` topic_group names currently in active pool.

        Used by ExploreStrategy to know which topics the pool already
        covers, so the LLM that generates explore domains can avoid
        re-proposing those (the v0.3.31 explore-blind-spot pattern).
        Filters to groups with at least ``min_count`` members so a
        single one-off item doesn't block exploration of an actually-
        empty area. Result is sorted by group size DESC.
        """
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT topic_group, COUNT(*) AS n
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND COALESCE(topic_group, '') != ''
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            GROUP BY topic_group
            HAVING COUNT(*) >= ?
            ORDER BY n DESC, topic_group ASC
            LIMIT ?
            """,
            (min_score, max(1, int(min_count)), max(1, int(limit))),
        )
        return [str(row["topic_group"]) for row in cursor.fetchall()]

    def get_topic_group_samples(
        self,
        *,
        samples_per_group: int = 5,
        top_n_groups: int = 60,
    ) -> list[tuple[str, list[str]]]:
        """For each fresh-pool ``topic_group``, return up to N sample titles.

        Returns the top ``top_n_groups`` groups by member count (tie-break
        on highest in-group ``relevance_score``). Long-tail micro-topics
        (1-2 items) almost never show up together in a single 40-candidate
        recommendation batch, so investing API budget to merge-map them
        adds latency without affecting visible diversity.

        Used by the recommendation prewarmer to build an accurate
        supergroup-merge map: short Chinese labels (``赛博朋克``,
        ``动漫`` …) are catastrophically ambiguous in embedding space
        when embedded standalone — they need title-context disambiguation.
        Sample titles are picked top-by-``relevance_score`` within each
        group, so the input is reasonably stable while the pool is steady.
        """
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT topic_group, title, relevance_score
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND COALESCE(topic_group, '') != ''
              AND COALESCE(title, '') != ''
            ORDER BY topic_group, relevance_score DESC, bvid
            """,
            (min_score,),
        )
        by_group: dict[str, list[str]] = defaultdict(list)
        group_max_score: dict[str, float] = {}
        group_count: dict[str, int] = defaultdict(int)
        for row in cursor.fetchall():
            group = str(row["topic_group"]).strip()
            title = str(row["title"]).strip()
            if not group or not title:
                continue
            group_count[group] += 1
            score = float(row["relevance_score"] or 0.0)
            if score > group_max_score.get(group, -1.0):
                group_max_score[group] = score
            if len(by_group[group]) < samples_per_group:
                by_group[group].append(title)

        # Rank groups by member count desc, score desc, label asc (stable).
        ranked = sorted(
            by_group.keys(),
            key=lambda g: (-group_count[g], -group_max_score.get(g, 0.0), g),
        )
        return [(group, by_group[group]) for group in ranked[:top_n_groups]]

    def trim_explore_cluster_overflow(self, *, max_per_cluster: int = 3) -> int:
        """Suppress excess fresh explore items from high-risk topic clusters."""
        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT bvid, title, topic_key, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND COALESCE(source, '') = 'explore'
            """,
            (min_score,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            cluster = self._explore_risk_cluster(row)
            if not cluster:
                continue
            grouped[cluster].append(row)

        overflow_bvids: list[str] = []
        for items in grouped.values():
            ranked = sorted(
                items,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            overflow_bvids.extend(
                str(row.get("bvid", "")).strip() for row in ranked[max(0, max_per_cluster) :]
            )

        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        return len(clean_bvids)

    def trim_topic_group_overflow(self, *, max_per_group: int) -> int:
        """Suppress fresh items where any single ``topic_group`` exceeds *max_per_group*.

        Generalises the source-and-keyword-specific
        :meth:`trim_explore_cluster_overflow` to a cross-source, dynamic cap on
        every populated ``topic_group`` value. Without this, a single topic
        (e.g. ``人工智能``) can accumulate hundreds of fresh candidates as
        related_chain/search/explore each keep returning the same coarse group
        across rounds — m118's per-call ``_compress_topic_repeats`` doesn't
        compose across rounds, and the explore-only cluster cap doesn't see
        related_chain or search.

        Items with empty ``topic_group`` are ignored. Within an over-cap
        group, the highest-scored / most-recently-scored items are kept;
        the rest get ``pool_status='suppressed'``.

        v0.3.31+: emits an INFO log when something gets dropped, naming
        the over-flowing groups + how many items each lost. Without this,
        the function ran silently — operators couldn't tell whether the
        diversity machinery was actually cutting anything or sleeping.
        """
        if max_per_group <= 0:
            return 0

        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT bvid, topic_group, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND COALESCE(topic_group, '') != ''
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            (min_score,),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        if not rows:
            return 0

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            group = str(row.get("topic_group", "") or "").strip().lower()
            if not group:
                continue
            grouped[group].append(row)

        overflow_bvids: list[str] = []
        # v0.3.31+: track per-group drop counts for the INFO log
        drops_per_group: dict[str, int] = {}
        for group_name, items in grouped.items():
            if len(items) <= max_per_group:
                continue
            ranked = sorted(
                items,
                key=lambda row: (
                    -float(row.get("relevance_score", 0.0) or 0.0),
                    -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
                    str(row.get("bvid", "")),
                ),
            )
            losers = ranked[max_per_group:]
            drops_per_group[group_name] = len(losers)
            overflow_bvids.extend(str(row.get("bvid", "")).strip() for row in losers)

        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )

        # Top 10 most-trimmed groups so the log line stays readable.
        # Demoted to DEBUG: this runs once per minute from the refresh
        # tick. When the pool is steady-state and a single group
        # consistently sits ~8 items over the cap, the same line gets
        # logged 1440x per day at INFO. Caller can lift to INFO when
        # the trim shape actually changes (see refresh.enforce_pool_cap).
        top = sorted(drops_per_group.items(), key=lambda kv: -kv[1])[:10]
        logger.debug(
            "[diversity] trim_topic_group_overflow: cap=%d, dropped=%d items "
            "across %d over-cap groups, top: %s",
            max_per_group,
            len(clean_bvids),
            len(drops_per_group),
            ", ".join(f"{g}:{c}" for g, c in top),
        )
        return len(clean_bvids)

    def trim_pool_to_target_count(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Suppress overflow fresh items so the pool does not exceed *target*.

        Ranking (what we keep): higher ``relevance_score`` > newer
        ``last_scored_at`` > non-``explore`` source > stable ``bvid``. Items
        already surfaced as recommendations are excluded from the count — the
        recommendation side treats the pool as a queue, so consumed rows are
        never trimmed here.

        When ``source_share_quotas`` is provided, the trim respects per-source-family
        share targets: items from source families already at or above their quota
        get suppressed *before* lower-scored items from under-quota sources.
        Without this, score-only trim systematically axes low-relevance
        sources (trending, explore) when high-relevance sources (search,
        related_chain) overflow — defeating the per-source diversity goal.
        Xiaohongshu extension channels (task/search/explore/profile) are
        collapsed under the single ``xiaohongshu`` family.
        """
        if target <= 0:
            return 0

        rows = self._load_pool_raw_material_rows()
        if len(rows) <= target:
            return 0

        ranked = sorted(
            rows,
            key=self._pool_trim_keep_key,
        )

        if source_share_quotas:
            # Three-tier protection so under-quota sources stay fully intact:
            #   protected: items from sources whose total ≤ quota, OR top-N
            #              items from sources whose total > quota (where N=quota)
            #   negotiable_tracked: bottom (total-quota) items from over-quota
            #              tracked sources
            #   negotiable_untracked: items from sources without a declared
            #              share — eligible to be cut before touching protected.
            # Order for the final keep walk: protected → negotiable_untracked
            # → negotiable_tracked.  This ensures trending (under quota) stays
            # 100% protected even when sum of in_quota > target due to
            # untracked sources eating slots.
            counts_per_source: dict[str, int] = defaultdict(int)
            for row in rows:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                counts_per_source[source_family] += 1

            protected: list[dict[str, Any]] = []
            negotiable_tracked: list[dict[str, Any]] = []
            negotiable_untracked: list[dict[str, Any]] = []
            seen: dict[str, int] = defaultdict(int)
            for row in ranked:
                source_family = _pool_source_family(
                    row.get("source", ""),
                    row.get("source_platform", ""),
                )
                quota = source_share_quotas.get(source_family)
                if quota is None:
                    negotiable_untracked.append(row)
                    continue
                if counts_per_source[source_family] <= quota:
                    # entire source under quota — every item protected
                    protected.append(row)
                else:
                    # over quota: top `quota` items protected, rest negotiable
                    if seen[source_family] < quota:
                        protected.append(row)
                        seen[source_family] += 1
                    else:
                        negotiable_tracked.append(row)
            ranked = protected + negotiable_untracked + negotiable_tracked

        overflow_rows = ranked[target:]
        overflow_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in overflow_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        # v0.3.31+: log per-source breakdown so operators see whether the
        # quota guard is biting (e.g. "explore overflowing 80%" → fix the
        # discovery cycle, not the recommender).
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_to_target_count: target=%d, before=%d, "
            "suppressed=%d, by-source: %s",
            target,
            len(rows),
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def trim_pool_source_overflow(self, *, source_share_quotas: dict[str, int]) -> int:
        """Suppress fresh rows that exceed platform-family pool quotas.

        ``trim_pool_to_target_count`` caps the total pool size. This pass caps
        each tracked platform family independently, so an over-filled family
        cannot occupy capacity reserved for another source while the total pool
        is still below target.
        """
        clean_quotas: dict[str, int] = {}
        for source_family, quota in source_share_quotas.items():
            try:
                clean_quotas[str(source_family)] = max(0, int(quota))
            except (TypeError, ValueError):
                continue
        if not clean_quotas:
            return 0

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in self._load_pool_raw_material_rows():
            source_family = _pool_source_family(row["source"], row["source_platform"])
            if source_family in clean_quotas:
                grouped[source_family].append(row)

        overflow_rows: list[dict[str, Any]] = []
        for source_family, rows in grouped.items():
            quota = clean_quotas[source_family]
            if len(rows) <= quota:
                continue
            ranked = sorted(
                rows,
                key=self._pool_trim_keep_key,
            )
            overflow_rows.extend(ranked[quota:])

        clean_bvids = [str(row.get("bvid", "")).strip() for row in overflow_rows]
        clean_bvids = [bvid for bvid in clean_bvids if bvid]
        if not clean_bvids:
            return 0

        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )
        per_source: dict[str, int] = defaultdict(int)
        for row in overflow_rows:
            family = _pool_source_family(
                row.get("source", ""),
                row.get("source_platform", ""),
            )
            per_source[family] += 1
        breakdown = ", ".join(
            f"{src}:{cnt}" for src, cnt in sorted(per_source.items(), key=lambda kv: -kv[1])
        )
        logger.info(
            "[diversity] trim_pool_source_overflow: suppressed=%d, by-source: %s",
            len(clean_bvids),
            breakdown or "(none)",
        )
        return len(clean_bvids)

    def reactivate_under_quota_pool_sources(
        self,
        *,
        target: int,
        source_share_quotas: dict[str, int],
        raw_source_share_quotas: dict[str, int] | None = None,
    ) -> int:
        """Move suppressed candidates back to fresh for under-quota source families.

        This is a source-balance repair pass for pools that are already full but
        uneven. It only reactivates rows that are otherwise eligible for the
        recommendation pool. Reactivation is driven by frontend-available
        deficits, but bounded by raw-material headroom so pending rows already
        occupying a source's raw ceiling do not trigger more fresh inventory.
        """
        if target <= 0 or not source_share_quotas:
            return 0

        current_counts = self.count_pool_available_candidates_by_source()
        raw_counts = self.count_pool_raw_material_by_source()
        raw_quotas = raw_source_share_quotas or source_share_quotas
        deficits = {
            source_family: min(
                min(target, max(0, int(quota))) - int(current_counts.get(source_family, 0)),
                max(
                    0,
                    int(raw_quotas.get(source_family, quota))
                    - int(raw_counts.get(source_family, 0)),
                ),
            )
            for source_family, quota in source_share_quotas.items()
            if int(quota) > 0
        }
        deficits = {source: deficit for source, deficit in deficits.items() if deficit > 0}
        if not deficits:
            return 0

        min_score = self._pool_admission_min_score()
        cursor = self.conn.execute(
            """
            SELECT bvid, source, source_platform, content_url, relevance_score, last_scored_at
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'suppressed'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                bvid ASC
            """,
            (min_score,),
        )
        viewed_content_keys = self.get_recent_viewed_content_keys()
        selected_bvids: list[str] = []
        selected_counts: dict[str, int] = defaultdict(int)
        target_selection_count = sum(deficits.values())

        for row in cursor.fetchall():
            bvid = str(row["bvid"]).strip()
            row_dict = dict(row)
            if not bvid or self._is_viewed_row(row_dict, viewed_content_keys):
                continue
            if not _is_linkable_pool_source(
                row["source"],
                row["source_platform"],
                row["content_url"],
            ):
                continue
            source_family = _pool_source_family(row["source"], row["source_platform"])
            deficit = deficits.get(source_family, 0)
            if deficit <= 0 or selected_counts[source_family] >= deficit:
                continue
            selected_bvids.append(bvid)
            selected_counts[source_family] += 1
            if len(selected_bvids) >= target_selection_count:
                break

        if not selected_bvids:
            return 0

        placeholders = ", ".join("?" for _ in selected_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'fresh'
            WHERE bvid IN ({placeholders})
            """,
            selected_bvids,
        )
        return len(selected_bvids)

    @staticmethod
    def _balance_pool_rows(rows: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
        """Round-robin sample from a relevance-ordered pool, balanced by content topic.

        Buckets by ``topic_group`` (with fallback to ``topic_key`` then a
        sentinel) so that one dominant topic in the relevance head can't
        crowd out the candidate window. Source/platform are intentionally
        ignored — content-side features drive richness, not provenance.

        The round-robin always runs (even when ``len(rows) <= limit``) so
        that the returned ordering is balanced for downstream callers
        that may sub-select; otherwise the SQL ordering can place several
        items of the same topic back-to-back at the top.
        """
        if limit <= 0 or len(rows) <= 1:
            return rows[:limit]

        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        topic_order: list[str] = []
        for row in rows:
            key = str(row.get("topic_group", "") or "").strip().lower()
            if not key:
                key = str(row.get("topic_key", "") or "").strip().lower()
            if not key:
                key = "unknown"
            if key not in buckets:
                topic_order.append(key)
            buckets[key].append(row)

        balanced: list[dict[str, Any]] = []
        while len(balanced) < limit:
            progressed = False
            for key in topic_order:
                bucket = buckets[key]
                if not bucket:
                    continue
                balanced.append(bucket.pop(0))
                progressed = True
                if len(balanced) >= limit:
                    break
            if not progressed:
                break
        return balanced[:limit]

    def get_recent_viewed_bvids(self, limit: int = 2000) -> set[str]:
        """Return recently viewed BVIDs from view events."""
        cursor = self.conn.execute(
            """
            SELECT url, metadata
            FROM events
            WHERE event_type = 'view'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        viewed_bvids: set[str] = set()
        for row in cursor.fetchall():
            bvid = self._extract_bvid_from_view_event(dict(row))
            if bvid:
                viewed_bvids.add(bvid)
        return viewed_bvids

    def get_recent_viewed_content_keys(self, limit: int = 2000) -> set[str]:
        """Return recently viewed content identities across supported sources.

        Keys are source-aware (``source_platform:content_id``) and include
        raw BVIDs for legacy Bilibili callers.
        """
        cursor = self.conn.execute(
            """
            SELECT url, metadata
            FROM events
            WHERE event_type = 'view'
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        viewed_keys: set[str] = set()
        for row in cursor.fetchall():
            viewed_keys.update(self._extract_content_keys_from_view_event(dict(row)))
        return viewed_keys

    @staticmethod
    def _explore_risk_cluster(row: dict[str, Any]) -> str:
        haystack = " ".join(
            [
                str(row.get("topic_key", "") or ""),
                str(row.get("title", "") or ""),
            ]
        ).lower()
        if not haystack.strip():
            return ""
        compact = re.sub(r"\s+", "", haystack)
        for cluster, keywords in _EXPLORE_HIGH_RISK_CLUSTERS:
            if any(keyword in compact for keyword in keywords):
                return cluster
        return ""

    @staticmethod
    def _sort_timestamp_score(value: str) -> float:
        if not value:
            return 0.0
        normalized = value.replace(" ", "T")
        try:
            from datetime import datetime

            return datetime.fromisoformat(normalized).timestamp()
        except ValueError:
            return 0.0

    def _pool_trim_keep_key(self, row: dict[str, Any]) -> tuple[int, int, float, float, int, str]:
        """Sort fresh raw material from most worth keeping to least.

        Raw-ceiling trims include pending rows, so servability has to outrank
        relevance: never keep an unopenable row over an openable one from the
        same trim candidate set just because the pending row has a higher score.
        """
        linkable = _is_linkable_pool_source(
            row.get("source"),
            row.get("source_platform"),
            row.get("content_url"),
        )
        ready = all(
            str(row.get(field, "") or "").strip()
            for field in ("pool_expression", "pool_topic_label", "style_key", "topic_group")
        )
        return (
            0 if linkable else 1,
            0 if ready else 1,
            -float(row.get("relevance_score", 0.0) or 0.0),
            -self._sort_timestamp_score(str(row.get("last_scored_at", ""))),
            1 if str(row.get("source", "") or "") == "explore" else 0,
            str(row.get("bvid", "")),
        )

    def mark_pool_items_shown(self, bvids: list[str]) -> None:
        """Mark discovery-pool items as already shown in recommendations."""
        clean_bvids = [item for item in bvids if item]
        if not clean_bvids:
            return
        placeholders = ", ".join("?" for _ in clean_bvids)
        self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'shown',
                recommended_at = CURRENT_TIMESTAMP
            WHERE bvid IN ({placeholders})
            """,
            clean_bvids,
        )

    def evict_stale_pool_items(self, *, max_age_days: int = 90) -> int:
        """Mark pool items older than *max_age_days* as stale."""
        cursor = self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'stale'
            WHERE pool_status = 'fresh'
              AND discovered_at < datetime('now', '-' || ? || ' days')
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            """,
            (max_age_days,),
        )
        return cursor.rowcount

    def prune_events_by_retention(
        self,
        *,
        retention_days: int,
        low_value_types: tuple[str, ...] = ("view", "scroll", "hover", "snapshot"),
        batch_size: int = 5000,
    ) -> int:
        """Delete old low-value behavior events to bound ``events`` growth.

        ``events`` is the largest table (800k+ rows, +~12k/day) but the
        cognition pipeline folds each event into the persistent ``preference``
        / ``soul`` layers exactly once (read incrementally by ``id`` watermark,
        never re-derived from raw rows), so raw low-signal events (page views,
        scrolls, hovers, DOM snapshots) carry no long-term value after they have
        been processed. Pruning them by age keeps aggregates and the activity
        feed fast without losing any profiling signal.

        High-signal events (favorite, follow, like, comment, share, search,
        feedback, click) are intentionally NOT pruned — they are sparse and
        directly feed interest modeling.

        Args:
            retention_days: Keep the given types newer than this many days.
                ``<= 0`` disables pruning (no-op, returns 0).
            low_value_types: Event types eligible for age-based pruning.
            batch_size: Rows deleted per statement so each write transaction
                stays short and avoids a long write lock on the main DB.

        Returns:
            Total rows deleted.
        """
        if retention_days <= 0 or not low_value_types:
            return 0
        placeholders = ", ".join("?" for _ in low_value_types)
        cutoff = f"-{int(retention_days)} days"
        total = 0
        while True:
            cursor = self._execute_write(
                f"""
                DELETE FROM events
                WHERE id IN (
                    SELECT id FROM events
                    WHERE event_type IN ({placeholders})
                      AND created_at < datetime('now', ?)
                    LIMIT ?
                )
                """,
                (*low_value_types, cutoff, batch_size),
            )
            deleted = cursor.rowcount
            total += deleted
            if deleted < batch_size:
                break
            # Yield the SQLite write lock between batches so request-loop
            # writers (event ingest, task-result merges) aren't blocked
            # behind a long prune pass via busy_timeout contention. A heavy
            # first run (many aged low-value events) would otherwise hold the
            # lock continuously and stall unrelated requests for seconds.
            time.sleep(0.02)
        if total:
            logger.info(
                "Pruned %d old low-value events (types=%s, older_than=%dd)",
                total,
                low_value_types,
                retention_days,
            )
        return total

    def prune_task_history(
        self,
        *,
        retention_days: int = 30,
        batch_size: int = 2000,
    ) -> dict[str, int]:
        """Delete old terminal rows from the producer task/candidate tables.

        Three append-only tables grow without bound because nothing ever
        deletes from them:

        - ``zhihu_tasks`` / ``dy_tasks``: crawl task rows (payload_json +
          result_json ≈ 10KB/row). ``completed``/``failed`` rows are terminal —
          their results have long since been merged into the pool, so only a
          short retention is needed for debugging.
        - ``discovery_candidates``: every evaluated discovery candidate
          (~1.2k/day, mostly ``rejected_*``). Rejections are terminal; only
          ``rejected_*`` rows are pruned. ``cached`` rows are kept — they are
          the dedup ledger that stops already-known content from being
          re-enqueued.

        Args:
            retention_days: Delete terminal rows older than this many days.
                ``<= 0`` disables pruning.
            batch_size: Rows deleted per statement (short write transactions,
                yielding the lock between batches).

        Returns:
            Mapping of table name -> rows deleted (only non-zero entries).
        """
        if retention_days <= 0:
            return {}
        cutoff = f"-{int(retention_days)} days"
        targets: tuple[tuple[str, str, tuple[object, ...]], ...] = (
            (
                "zhihu_tasks",
                "DELETE FROM zhihu_tasks WHERE id IN ("
                "SELECT id FROM zhihu_tasks WHERE status IN ('completed','failed') "
                "AND created_at < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            ),
            (
                "dy_tasks",
                "DELETE FROM dy_tasks WHERE id IN ("
                "SELECT id FROM dy_tasks WHERE status IN ('completed','failed') "
                "AND created_at < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            ),
            (
                "discovery_candidates",
                "DELETE FROM discovery_candidates WHERE id IN ("
                "SELECT id FROM discovery_candidates "
                "WHERE status LIKE 'rejected%' "
                "AND last_seen_at < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            ),
        )
        results: dict[str, int] = {}
        for table, sql, params in targets:
            total = 0
            while True:
                cursor = self._execute_write(sql, params)
                deleted = cursor.rowcount
                total += deleted
                if deleted < batch_size:
                    break
                time.sleep(0.02)  # yield the write lock between batches
            if total:
                logger.info(
                    "Pruned %d terminal rows from %s (older_than=%dd)",
                    total,
                    table,
                    retention_days,
                )
                results[table] = total
        return results

    def prune_recommendations(
        self,
        *,
        retention_days: int = 7,
        batch_size: int = 2000,
    ) -> int:
        """Delete old ``recommendations`` rows past their de-dup window.

        The table is only consulted as a 24-hour de-dup ledger
        (``NOT EXISTS`` against recent ``created_at`` in pool-serve SQL), so
        rows older than the retention window have zero readers. Pruning keeps
        the table small and the ``NOT EXISTS`` probe fast.

        Args:
            retention_days: Delete rows older than this many days.
                ``<= 0`` disables pruning (no-op, returns 0).
            batch_size: Rows deleted per statement (short write transactions,
                yielding the lock between batches).

        Returns:
            Total rows deleted.
        """
        if retention_days <= 0:
            return 0
        cutoff = f"-{int(retention_days)} days"
        total = 0
        while True:
            cursor = self._execute_write(
                "DELETE FROM recommendations WHERE id IN ("
                "SELECT id FROM recommendations "
                "WHERE created_at < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            )
            deleted = cursor.rowcount
            total += deleted
            if deleted < batch_size:
                break
            time.sleep(0.02)  # yield the write lock between batches
        if total:
            logger.info(
                "Pruned %d old recommendation rows (older_than=%dd)",
                total,
                retention_days,
            )
        return total

    def prune_llm_usage(
        self,
        *,
        retention_days: int = 90,
        batch_size: int = 2000,
    ) -> int:
        """Delete old ``llm_usage`` accounting rows.

        The table is a write-only cost ledger (``usage_recorder`` appends a
        row per LLM call; nothing ever reads it back), so rows past the
        retention window are dead weight. Keeping ~90 days covers recent
        token/cost auditing without unbounded growth.

        Args:
            retention_days: Delete rows older than this many days.
                ``<= 0`` disables pruning (no-op, returns 0).
            batch_size: Rows deleted per statement (short write transactions,
                yielding the lock between batches).

        Returns:
            Total rows deleted.
        """
        if retention_days <= 0:
            return 0
        cutoff = f"-{int(retention_days)} days"
        total = 0
        while True:
            cursor = self._execute_write(
                "DELETE FROM llm_usage WHERE id IN ("
                "SELECT id FROM llm_usage "
                "WHERE timestamp < datetime('now', ?) LIMIT ?)",
                (cutoff, batch_size),
            )
            deleted = cursor.rowcount
            total += deleted
            if deleted < batch_size:
                break
            time.sleep(0.02)  # yield the write lock between batches
        if total:
            logger.info(
                "Pruned %d old llm_usage rows (older_than=%dd)",
                total,
                retention_days,
            )
        return total

    def purge_pool_by_disliked_topics(self, topics: list[str]) -> int:
        """Mark fresh pool candidates matching new dislikes as purged.

        Matching strategy (all case-sensitive at the SQLite layer — Chinese
        text makes case folding moot and ASCII matching still works):
          1. Exact match on ``topic_key``, ``topic_group``, or ``pool_topic_label``
          2. Substring match on ``title`` or ``pool_topic_label``
             (catches "鬼畜合集" when the dislike is "鬼畜")

        Only candidates in ``pool_status = 'fresh'`` are affected — historical
        rows (``shown``, ``feedbacked``, ``stale``) are preserved for audit.
        Already-recommended items are skipped so the recommendation history
        remains intact.

        Args:
            topics: Newly added disliked topics (stripped, non-empty strings).

        Returns:
            Number of rows transitioned to ``pool_status = 'purged_by_dislike'``.
        """
        clean = [t.strip() for t in topics if t and t.strip()]
        if not clean:
            return 0

        # Build the match clause dynamically. Use parameterized queries
        # throughout — topic values may contain SQL metacharacters that must
        # not be interpolated into the query string.
        exact_placeholders = ", ".join("?" for _ in clean)
        like_conditions = " OR ".join("title LIKE ? OR pool_topic_label LIKE ?" for _ in clean)

        params: list[Any] = []
        params.extend(clean)  # topic_key IN (...)
        params.extend(clean)  # topic_group IN (...)
        params.extend(clean)  # pool_topic_label IN (...)
        for topic in clean:
            like = f"%{topic}%"
            params.append(like)  # title LIKE ?
            params.append(like)  # pool_topic_label LIKE ?

        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'purged_by_dislike'
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
              AND (
                topic_key IN ({exact_placeholders})
                OR topic_group IN ({exact_placeholders})
                OR pool_topic_label IN ({exact_placeholders})
                OR {like_conditions}
              )
            """,
            params,
        )
        return cursor.rowcount

    def suppress_pool_rows_by_url(self, url: str) -> int:
        """Suppress fresh pool candidates matching a blocked article's URL.

        Reading-library "屏蔽" is an article-level action, but the same URL is
        frequently also a live recommendation candidate (RSS items injected via
        ``inject_article_to_pool``, recommendations whose content the user
        later filed into the library). Sync-suppressing them makes the block
        feel immediate instead of waiting for the async soul pipeline.

        Uses ``suppressed`` — not ``purged_by_dislike`` — deliberately:
        suppressed rows revive to ``fresh`` when discovery re-scores them,
        which pairs with the un-block ("恢复") action in the management view.
        Only ``fresh`` rows are touched; shown / feedbacked history rows are
        preserved for audit.

        Returns:
            Number of rows moved to ``pool_status = 'suppressed'``.
        """
        clean = (url or "").strip()
        if not clean:
            return 0
        cursor = self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'suppressed'
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND content_url = ?
            """,
            (clean,),
        )
        return cursor.rowcount

    def revive_suppressed_pool_rows_by_url(self, url: str) -> int:
        """Un-block counterpart of :meth:`suppress_pool_rows_by_url`.

        Returns:
            Number of suppressed rows for *url* moved back to ``fresh``.
        """
        clean = (url or "").strip()
        if not clean:
            return 0
        cursor = self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'fresh'
            WHERE COALESCE(pool_status, 'fresh') = 'suppressed'
              AND content_url = ?
            """,
            (clean,),
        )
        return cursor.rowcount

    def get_fresh_pool_candidates_for_purge_scan(
        self,
        *,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Return fresh, not-yet-recommended pool candidates for a semantic scan.

        Returns only the fields needed for embedding-based matching:
        bvid, title, topic_key, topic_group, pool_topic_label.
        """
        cursor = self.conn.execute(
            """
            SELECT bvid, title, topic_key, topic_group, pool_topic_label
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND NOT EXISTS (
                SELECT 1 FROM recommendations AS r WHERE r.bvid = content_cache.bvid
              )
            ORDER BY discovered_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_pool_items_purged_by_dislike(self, bvids: list[str]) -> int:
        """Mark specified bvids as purged_by_dislike (only if currently fresh)."""
        clean = [b.strip() for b in bvids if b and b.strip()]
        if not clean:
            return 0
        placeholders = ", ".join("?" for _ in clean)
        cursor = self._execute_write(
            f"""
            UPDATE content_cache
            SET pool_status = 'purged_by_dislike'
            WHERE bvid IN ({placeholders})
              AND COALESCE(pool_status, 'fresh') = 'fresh'
            """,
            clean,
        )
        return cursor.rowcount

    def get_pool_candidates_needing_evaluation(
        self, limit: int = 20, *, xhs_self_nickname: str = ""
    ) -> list[dict[str, Any]]:
        """Return fresh pool candidates that lack LLM content classification.

        Targets items with empty ``style_key`` AND empty ``topic_group`` —
        typically content from non-bilibili sources (e.g. xiaohongshu) that
        was inserted directly into ``content_cache`` without passing through
        the discovery engine's ``evaluate_content`` pipeline.

        These items need LLM evaluation to receive ``style_key``,
        ``topic_group``, and ``relevance_score`` so the diversity mechanism
        in ``_select_diversified_batch`` can treat them equally alongside
        bilibili content.
        """
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(style_key, '') = ''
              AND COALESCE(topic_group, '') = ''
              AND COALESCE(relevance_score, 0) = 0
              {guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                last_scored_at DESC,
                bvid ASC
            LIMIT ?
            """,
            (*guard_params, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return rows[:limit]

    def get_pool_candidates_needing_copy(
        self, limit: int = 20, *, xhs_self_nickname: str = ""
    ) -> list[dict[str, Any]]:
        """Return fresh pool candidates missing precomputed popup copy.

        v0.3.66+: requires ``style_key`` / ``topic_group`` — content must
        be classified before expression generation.  This prevents
        unclassified items (e.g. raw XHS notes) from getting an expression
        and leaking through the serve gate without proper relevance scoring.
        """
        min_score = self._pool_admission_min_score()
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(pool_status, 'fresh') = 'fresh'
              AND COALESCE(feedback_type, '') != 'dislike'
              AND COALESCE(relevance_score, 0.0) >= ?
              AND COALESCE(style_key, '') != ''
              AND COALESCE(topic_group, '') != ''
              AND (
                COALESCE(pool_expression, '') = ''
                OR COALESCE(pool_topic_label, '') = ''
              )
              {guard_sql}
              AND NOT EXISTS (
                SELECT 1
                FROM recommendations AS r
                WHERE r.bvid = content_cache.bvid
              )
            ORDER BY
                CASE candidate_tier WHEN 'primary' THEN 0 ELSE 1 END ASC,
                relevance_score DESC,
                last_scored_at DESC,
                view_count DESC,
                bvid ASC
            LIMIT ?
            """,
            (min_score, *guard_params, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
        rows = self._exclude_viewed_rows(
            rows,
            self.get_recent_viewed_content_keys(),
            limit=len(rows),
        )
        return rows[:limit]

    def update_pool_copy(
        self,
        bvid: str,
        *,
        expression: str,
        topic_label: str,
    ) -> None:
        """Persist precomputed popup copy for one pooled candidate."""
        self._execute_write(
            """
            UPDATE content_cache
            SET pool_expression = ?,
                pool_topic_label = ?
            WHERE bvid = ?
            """,
            (expression, topic_label, bvid),
        )

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

    def insert_recommendation(
        self,
        bvid: str,
        *,
        confidence: float,
        expression: str = "",
        topic: str = "",
        presented: int = 0,
    ) -> int:
        """Insert a recommendation history record."""
        cursor = self._execute_write(
            """
            INSERT INTO recommendations (bvid, expression, topic, confidence, presented)
            VALUES (?, ?, ?, ?, ?)
            """,
            (bvid, expression, topic, confidence, presented),
        )
        return cursor.lastrowid or 0

    def batch_insert_recommendations(
        self,
        items: list[dict[str, Any]],
    ) -> list[int]:
        """Insert N recommendation rows in one transaction; return row IDs in order.

        Single fsync replaces N (was 200-300ms each under discovery write
        contention → ~3s for the popup's 10-item batch). Returns
        ``lastrowid`` per item, computed from the auto-increment delta
        since this connection's last id.
        """
        return self.batch_insert_recommendations_and_mark_shown(items, [])

    def batch_insert_recommendations_and_mark_shown(
        self,
        items: list[dict[str, Any]],
        shown_bvids: list[str],
    ) -> list[int]:
        """Insert recommendations + mark pool items shown in **one transaction**.

        v0.3.45+: serve() used to fire two separate writes (insert recs,
        then UPDATE content_cache.pool_status='shown') and pay two
        fsyncs. Under refresh-tick write contention this stretched the
        tail to ~1s. One BEGIN IMMEDIATE / COMMIT pair gives the same
        atomic semantics with a single fsync, and the rare lost-write
        case (insert succeeds, mark fails) is now structurally
        impossible — both succeed or both rollback together.

        Returns ``lastrowid`` per item, in the same order as ``items``.
        """
        if not items and not shown_bvids:
            return []
        clean_bvids = [b for b in shown_bvids if b]
        attempts = _LOCK_RETRY_ATTEMPTS
        while True:
            try:
                cursor = self.conn.cursor()
                cursor.execute("BEGIN IMMEDIATE")
                try:
                    ids: list[int] = []
                    for item in items:
                        cursor.execute(
                            """
                            INSERT INTO recommendations
                                (bvid, expression, topic, confidence, presented)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (
                                str(item.get("bvid", "")),
                                str(item.get("expression", "")),
                                str(item.get("topic", "")),
                                float(item.get("confidence", 0.0) or 0.0),
                                int(item.get("presented", 0) or 0),
                            ),
                        )
                        ids.append(cursor.lastrowid or 0)
                    if clean_bvids:
                        placeholders = ", ".join("?" for _ in clean_bvids)
                        cursor.execute(
                            f"""
                            UPDATE content_cache
                            SET pool_status = 'shown',
                                recommended_at = CURRENT_TIMESTAMP
                            WHERE bvid IN ({placeholders})
                            """,
                            clean_bvids,
                        )
                    self.conn.commit()
                    return ids
                except Exception:
                    self.conn.rollback()
                    raise
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc).lower() or attempts <= 1:
                    raise
                attempts -= 1
                time.sleep(_LOCK_RETRY_SLEEP_SECONDS)

    def get_recent_recommendation_signals(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent recommendations with topic/source for scoring context.

        Includes both ``topic_key`` (fine, e.g. ``"洛克王国"``) and
        ``topic_group`` (coarse, e.g. ``"游戏"``) so the curator can fatigue
        on both axes. Without ``topic_group``, sibling fine-grained keys
        like ``动漫杂谈`` / ``动漫补番`` / ``动漫解说`` are independent and
        per-key fatigue never fires across them.
        """
        cursor = self.conn.execute(
            """
            SELECT r.bvid, c.topic_key, c.topic_group, c.source, r.created_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recent_recommendation_signals_since(
        self,
        *,
        since: datetime,
    ) -> list[dict[str, Any]]:
        """Return recommendation topic/source rows shown since a timestamp."""
        self._ensure_fresh_read()
        since_text = since.isoformat(sep=" ")
        cursor = self.conn.execute(
            """
            SELECT r.bvid,
                   c.topic_key,
                   c.topic_group,
                   c.source,
                   r.created_at,
                   r.presented_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE COALESCE(r.presented_at, r.created_at) >= ?
            ORDER BY COALESCE(r.presented_at, r.created_at) DESC, r.id DESC
            """,
            (since_text,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_feedback_signals(self, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent feedback with UP/topic/franchise info for score
        adjustment.

        ``franchise_key`` is the LLM-tagged IP / series column (added in
        v0.3.18). Disliking one 原神 video used to only block its exact
        bvid; now the curator collects ``franchise_key`` across recent
        dislikes and down-ranks any candidate whose own ``franchise_key``
        matches — without relying on title-string heuristics.
        """
        cursor = self.conn.execute(
            """
            SELECT r.feedback_type, c.up_mid, c.up_name, c.topic_key,
                   c.source, c.title, c.franchise_key
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.feedback_type IS NOT NULL
            ORDER BY r.feedback_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_bandit_impressions(
        self,
        *,
        since: datetime,
        positive_feedback_types: Sequence[str] = ("like", "save", "favorite"),
        deep_dwell_seconds: float = 60.0,
    ) -> list[dict[str, Any]]:
        """Aggregate per-``(source strategy, topic_group)`` bandit impressions.

        Feeds :class:`openbiliclaw.recommendation.bandit.SlidingWindowThompsonSampler`:
        one row per arm with the exposure (presentation) count and reward count
        inside the sliding window.

        A presentation is a *reward* when the user gave explicit positive
        feedback, or stayed on the item at least ``deep_dwell_seconds`` — the
        same implicit-positive definition :meth:`get_dwell_scores` uses, so the
        two implicit-feedback signals cannot diverge. Dwell per bvid is capped
        at 600s and taken as the longest view in the window, mirroring how the
        dwell aggregator treats abandoned long-lived tabs.

        Recommendations that never made it into ``content_cache`` (deleted pool
        rows) drop out via the inner join — they carry no arm labels anyway.
        """
        self._ensure_fresh_read()
        since_text = since.isoformat(sep=" ")
        placeholders = ", ".join("?" for _ in positive_feedback_types)
        cursor = self.conn.execute(
            f"""
            WITH dwell AS (
                SELECT bvid, MAX(MIN(dwell_seconds, 600)) AS dwell_max
                FROM view_history
                WHERE viewed_at >= ?
                GROUP BY bvid
            )
            SELECT COALESCE(c.source, '') AS source,
                   COALESCE(c.topic_group, '') AS topic_group,
                   COUNT(*) AS exposures,
                   SUM(CASE
                         WHEN r.feedback_type IN ({placeholders}) THEN 1
                         WHEN COALESCE(d.dwell_max, 0) >= ? THEN 1
                         ELSE 0
                       END) AS rewards
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            LEFT JOIN dwell AS d ON d.bvid = c.bvid
            WHERE COALESCE(r.presented_at, r.created_at) >= ?
            GROUP BY COALESCE(c.source, ''), COALESCE(c.topic_group, '')
            """,
            (
                since_text,
                *positive_feedback_types,
                float(deep_dwell_seconds),
                since_text,
            ),
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_recommendations(
        self,
        limit: int = 100,
        *,
        exclude_processed: bool = False,
    ) -> list[dict[str, Any]]:
        """Get recommendation history ordered by newest first.

        xhs rows whose cached ``content_url`` is missing ``xsec_token``
        are filtered out — clicking them hits xhs's 300031 login wall.

        When *exclude_processed* is True, rows that have already been
        acted upon (liked / disliked / dismissed / commented / clicked) are
        omitted so the API only returns actionable items.

        ``franchise_key`` (v0.3.18) is exposed so /api/recommendations
        can apply a final per-IP cap before returning to the client —
        otherwise five 原神 / 提瓦特 items can land in one popup view.
        """
        self._ensure_fresh_read()
        min_score = self._pool_admission_min_score()
        processed_clause = (
            "AND (r.feedback_type IS NULL OR r.feedback_type = '') AND r.clicked_at IS NULL"
            if exclude_processed
            else ""
        )
        cursor = self.conn.execute(
            f"""
            SELECT
                r.*,
                COALESCE(c.title, '') AS title,
                COALESCE(c.up_name, '') AS up_name,
                COALESCE(c.cover_url, '') AS cover_url,
                COALESCE(c.content_id, r.bvid) AS content_id,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform,
                COALESCE(c.content_type, 'video') AS content_type,
                COALESCE(c.body_text, '') AS body_text,
                COALESCE(c.franchise_key, '') AS franchise_key,
                COALESCE(c.quality_score, 0.0) AS quality_score,
                COALESCE(c.quality_reason, '') AS quality_reason
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE (
                COALESCE(c.source_platform, '') != 'xiaohongshu'
                OR (COALESCE(c.content_url, '') LIKE '%xsec_token=%' AND c.discovered_at >= '2026-08-15')
            )
            AND COALESCE(r.confidence, 0.0) >= ?
            {processed_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (min_score, limit),
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_recommendations(self) -> int:
        """Return the total number of stored recommendations."""
        self._ensure_fresh_read()
        cursor = self.conn.execute("SELECT COUNT(*) AS count FROM recommendations")
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def count_unread_recommendations(self) -> int:
        """Return the number of unpresented recommendations."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            "SELECT COUNT(*) AS count FROM recommendations WHERE presented = 0"
        )
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def get_notification_candidate(
        self,
        *,
        min_confidence: float = 0.82,
    ) -> dict[str, Any] | None:
        """Return one recommendation worth notifying the user about."""
        cursor = self.conn.execute(
            """
            SELECT
                r.id,
                r.bvid,
                r.expression,
                r.confidence,
                c.title,
                c.notification_sent,
                c.notified_at
            FROM recommendations AS r
            JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.presented = 0
              AND c.notification_sent = 0
              AND r.confidence >= ?
            ORDER BY r.confidence DESC, r.created_at DESC, r.id DESC
            LIMIT 1
            """,
            (min_confidence,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def mark_notification_sent(self, bvid: str) -> None:
        """Mark one cached item as already notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET notification_sent = 1,
                notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def update_recommendation_content(
        self,
        recommendation_id: int,
        *,
        expression: str,
        topic: str,
    ) -> None:
        """Update the generated expression fields of a recommendation."""
        self._execute_write(
            """
            UPDATE recommendations
            SET expression = ?, topic = ?
            WHERE id = ?
            """,
            (expression, topic, recommendation_id),
        )

    def get_recommendation_by_id(self, recommendation_id: int) -> dict[str, Any] | None:
        """Return a single recommendation row by primary key."""
        self._ensure_fresh_read()
        cursor = self.conn.execute(
            """
            SELECT
                r.*,
                r.topic AS topic_label,
                c.title AS title,
                c.up_name AS up_name,
                COALESCE(c.content_id, r.bvid) AS content_id,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform
            FROM recommendations AS r
            LEFT JOIN content_cache AS c ON c.bvid = COALESCE(
                (SELECT bvid FROM content_cache WHERE bvid = r.bvid),
                (SELECT bvid FROM content_cache WHERE content_id = r.bvid LIMIT 1)
            )
            WHERE r.id = ?
            """,
            (recommendation_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    def update_recommendation_feedback(
        self,
        recommendation_id: int,
        *,
        feedback_type: str,
        feedback_note: str = "",
    ) -> None:
        """Update the current feedback state of a recommendation."""
        self._execute_write(
            """
            UPDATE recommendations
            SET feedback = ?,
                feedback_type = ?,
                feedback_note = ?,
                feedback_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (feedback_type, feedback_type, feedback_note, recommendation_id),
        )
        self._execute_write(
            """
            UPDATE content_cache
            SET pool_status = 'feedbacked',
                feedback_type = ?,
                feedback_at = CURRENT_TIMESTAMP
            WHERE bvid = (
                SELECT bvid
                FROM recommendations
                WHERE id = ?
            )
            """,
            (feedback_type, recommendation_id),
        )

    def mark_recommendations_presented(self, recommendation_ids: list[int]) -> None:
        """Mark recommendations as presented and set their presented timestamp."""
        if not recommendation_ids:
            return
        placeholders = ", ".join("?" for _ in recommendation_ids)
        self._execute_write(
            f"""
            UPDATE recommendations
            SET presented = 1,
                presented_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            recommendation_ids,
        )

    def mark_recommendations_clicked(self, recommendation_ids: list[int]) -> None:
        """Mark recommendations as clicked-through and record click timestamp.

        E1 (real-time feedback loop): a click-through is the strongest
        consumption signal. Marking it closes the exposure→click loop:
        clicked items are excluded from future serves (see
        ``get_recommendations(exclude_processed=True)``) so the user never
        sees the same card again, while ``presented_at`` / ``clicked_at``
        together yield real CTR data for online metrics.
        """
        if not recommendation_ids:
            return
        placeholders = ", ".join("?" for _ in recommendation_ids)
        self._execute_write(
            f"""
            UPDATE recommendations
            SET presented = 1,
                presented_at = COALESCE(presented_at, CURRENT_TIMESTAMP),
                clicked_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            recommendation_ids,
        )

    def get_clicked_bvids(self, limit: int = 200) -> list[str]:
        """Return bvids of recently clicked-through recommendations.

        E1: the serve/reshuffle/append paths draw from the live candidate
        pool and only learn about consumed items through ``excluded_bvids``.
        This helper lets the API merge historically clicked items into that
        exclusion set so a video the user already opened is never served
        again, even after it re-enters the pool.
        """
        rows = self.conn.execute(
            "SELECT bvid FROM recommendations "
            "WHERE clicked_at IS NOT NULL AND bvid != '' "
            "ORDER BY clicked_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [str(row["bvid"]) for row in rows]

    def update_content_quality_score(
        self, bvid: str, *, quality_score: float, quality_reason: str
    ) -> None:
        """Update LLM quality score and recommendation reason for a content item."""
        self._execute_write(
            """
            UPDATE content_cache
            SET quality_score = ?,
                quality_reason = ?,
                last_scored_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (quality_score, quality_reason, bvid),
        )

    def batch_update_content_quality_scores(self, scores: list[tuple[str, float, str]]) -> None:
        """Batch update quality scores for multiple content items."""
        cursor = self.conn.cursor()
        try:
            cursor.executemany(
                """
                UPDATE content_cache
                SET quality_score = ?,
                    quality_reason = ?,
                    last_scored_at = CURRENT_TIMESTAMP
                WHERE bvid = ?
                """,
                [(score, reason, bvid) for bvid, score, reason in scores],
            )
            self.conn.commit()
        except Exception:
            logger.exception("Failed to batch update quality scores")
            self.conn.rollback()

    def batch_get_quality_scores(self, bvids: list[str]) -> list[dict[str, object]]:
        """Fetch quality scores for a batch of bvids.

        Returns list of dicts with bvid and quality_score.
        """
        if not bvids:
            return []
        placeholders = ",".join("?" for _ in bvids)
        try:
            cursor = self.conn.execute(
                f"""
                SELECT bvid, quality_score, quality_reason
                FROM content_cache
                WHERE bvid IN ({placeholders})
                AND quality_score > 0.0
                """,
                bvids,
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to batch get quality scores")
            return []

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
            CREATE INDEX IF NOT EXISTS pool.idx_content_cache_content_id ON content_cache (content_id);
        """)

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
        """
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS pool.idx_content_cache_pool_status ON content_cache (pool_status);
            CREATE INDEX IF NOT EXISTS pool.idx_content_cache_source_platform ON content_cache (source_platform, pool_status);
            -- v0.3.19x: pool 浏览（/api/pool/all?source=…）按 source 过滤时
            -- 之前全表 SCAN 75244 行取 rowid，1.4s；此索引后走索引查找毫秒级。
            CREATE INDEX IF NOT EXISTS pool.idx_content_cache_source ON content_cache (source);
            CREATE INDEX IF NOT EXISTS pool.idx_content_cache_topic_group ON content_cache (topic_group);
            CREATE INDEX IF NOT EXISTS pool.idx_content_cache_feedback_type ON content_cache (feedback_type);
            CREATE INDEX IF NOT EXISTS pool.idx_content_cache_style_key ON content_cache (style_key);
        """)

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

    def add_to_watch_later(self, bvid: str, note: str = "") -> bool:
        """Bookmark a video. Returns True if newly inserted, False if updated."""
        self._execute_write(
            """
            INSERT INTO watch_later (bvid, note)
            VALUES (?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note
            """,
            (bvid.strip(), note),
        )
        return self.conn.total_changes > 0

    def remove_from_watch_later(self, bvid: str) -> bool:
        """Remove a bookmark. Returns True if a row was deleted."""
        self._execute_write(
            "DELETE FROM watch_later WHERE bvid = ?",
            (bvid.strip(),),
        )
        return self.conn.total_changes > 0

    def is_in_watch_later(self, bvid: str) -> bool:
        """Check whether a video is bookmarked."""
        row = self.conn.execute(
            "SELECT 1 FROM watch_later WHERE bvid = ?",
            (bvid.strip(),),
        ).fetchone()
        return row is not None

    def count_watch_later(self) -> int:
        """Return total number of bookmarked videos."""
        row = self.conn.execute("SELECT COUNT(*) FROM watch_later").fetchone()
        return int(row[0]) if row else 0

    def list_watch_later(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return bookmarked videos with content_cache metadata, newest first."""
        cursor = self.conn.execute(
            """
            SELECT
                w.bvid,
                w.added_at,
                w.note,
                COALESCE(c.title, '') AS title,
                COALESCE(c.up_name, '') AS up_name,
                COALESCE(c.cover_url, '') AS cover_url,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform
            FROM watch_later AS w
            LEFT JOIN content_cache AS c ON c.bvid = w.bvid
            ORDER BY w.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

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

    def add_to_favorites(self, bvid: str, note: str = "") -> bool:
        """Save a video to favorites. Returns True if newly inserted."""
        self._execute_write(
            """
            INSERT INTO favorites (bvid, note)
            VALUES (?, ?)
            ON CONFLICT(bvid) DO UPDATE SET
                added_at = CURRENT_TIMESTAMP,
                note = excluded.note
            """,
            (bvid.strip(), note),
        )
        return self.conn.total_changes > 0

    def remove_from_favorites(self, bvid: str) -> bool:
        """Remove a favorite. Returns True if a row was deleted."""
        self._execute_write(
            "DELETE FROM favorites WHERE bvid = ?",
            (bvid.strip(),),
        )
        return self.conn.total_changes > 0

    def is_in_favorites(self, bvid: str) -> bool:
        """Check whether a video is favorited."""
        row = self.conn.execute(
            "SELECT 1 FROM favorites WHERE bvid = ?",
            (bvid.strip(),),
        ).fetchone()
        return row is not None

    def count_favorites(self) -> int:
        """Return total number of favorited videos."""
        row = self.conn.execute("SELECT COUNT(*) FROM favorites").fetchone()
        return int(row[0]) if row else 0

    def list_favorites(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return favorited videos with content_cache metadata, newest first."""
        cursor = self.conn.execute(
            """
            SELECT
                f.bvid,
                f.added_at,
                f.note,
                COALESCE(c.title, '') AS title,
                COALESCE(c.up_name, '') AS up_name,
                COALESCE(c.cover_url, '') AS cover_url,
                COALESCE(c.content_url, '') AS content_url,
                COALESCE(c.source_platform, '') AS source_platform
            FROM favorites AS f
            LEFT JOIN content_cache AS c ON c.bvid = f.bvid
            ORDER BY f.added_at DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]

    def upsert_article(
        self,
        source_type: str,
        source_name: str,
        title: str,
        url: str,
        author: str = "",
        summary: str = "",
        content_text: str = "",
        published_at: str = "",
        tags: list[str] | None = None,
    ) -> int | None:
        """Insert or update an article. Returns row id or None on failure.

        ``tags`` defaults to the source name so every entry is filterable by
        origin; existing rows keep whatever tags they already have.
        """
        import json as _json
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        # 北京时间(UTC+8): articles 表所有时间字段统一存本地时间字符串
        cn_tz = _tz(_td(hours=8))
        if not published_at:
            published_at = _dt.now(cn_tz).strftime("%Y-%m-%d %H:%M:%S")

        # Knowledge Forge 任务 1.0：入库时同步调用正文清理器，
        # 生成 content_cleaned 及清理质量/验证标记（规则清理，确定性且快速）。
        # 清理失败不阻断入库，仅降级为新列留空，由批量清理管线后补。
        content_cleaned: str | None = None
        content_clean_score: float | None = None
        content_clean_log: str | None = None
        content_verified: int | None = None
        content_verify_result: str | None = None
        if content_text and content_text.strip():
            try:
                from openbiliclaw.knowledge_forge.content_cleaner import ContentCleaner

                cr = ContentCleaner().clean(
                    content_text, title=title, source_type=source_type
                )
                content_cleaned = cr.cleaned_text or None
                content_clean_score = cr.clean_score
                content_clean_log = _json.dumps(
                    cr.operations, ensure_ascii=False, default=str
                )
                content_verified = 1 if cr.verified else 0
                content_verify_result = _json.dumps(
                    cr.verify_issues, ensure_ascii=False, default=str
                )
            except Exception:
                logger.exception("Knowledge Forge clean failed for article: %s", title)

        tag_value = _json.dumps(
            tags if tags else ([source_name] if source_name else []),
            ensure_ascii=False,
        )
        try:
            cursor = self.conn.execute(
                """INSERT INTO articles (source_type, source_name, title, url,
                    author, summary, content_text, published_at, tags,
                    content_cleaned, content_clean_score, content_clean_log,
                    content_verified, content_verify_result,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           datetime('now','localtime'), datetime('now','localtime'))
                   ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title, summary=excluded.summary,
                    content_text=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_text
                      ELSE articles.content_text END,
                    content_cleaned=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_cleaned
                      ELSE articles.content_cleaned END,
                    content_clean_score=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_clean_score
                      ELSE articles.content_clean_score END,
                    content_clean_log=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_clean_log
                      ELSE articles.content_clean_log END,
                    content_verified=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_verified
                      ELSE articles.content_verified END,
                    content_verify_result=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_verify_result
                      ELSE articles.content_verify_result END,
                    tags=CASE
                      WHEN articles.tags IS NULL OR articles.tags IN ('', '[]')
                        THEN excluded.tags ELSE articles.tags END,
                    updated_at=datetime('now','localtime')""",
                (
                    source_type,
                    source_name,
                    title,
                    url,
                    author,
                    summary,
                    content_text,
                    published_at,
                    tag_value,
                    content_cleaned,
                    content_clean_score,
                    content_clean_log,
                    content_verified,
                    content_verify_result,
                ),
            )
            self.conn.commit()
            return cursor.lastrowid
        except Exception:
            logger.exception("Failed to upsert article: %s", title)
            return None

    def inject_article_to_pool(
        self,
        bvid: str,
        title: str,
        url: str,
        author: str,
        source_name: str,
        description: str,
        published_at: str,
        source_platform: str = "rss",
    ) -> None:
        """Inject an article into the recommendation pool (content_cache).

        Uses a simplified INSERT that sets only the fields relevant to
        text-based articles — all numeric / video-only fields are
        zeroed.  ``ON CONFLICT(bvid) DO NOTHING`` prevents re-insertion
        on subsequent polling cycles.

        Args:
            source_platform: Platform identifier, defaults to "rss".
        """
        try:
            self.conn.execute(
                """INSERT OR IGNORE INTO content_cache (
                    bvid, title, up_name, up_mid, duration, tags,
                    topic_key, style_key, franchise_key, description,
                    cover_url, view_count, like_count, favorite_count,
                    collect_count, comment_count, share_count, danmaku_count,
                    reply_count, retweet_count, bookmark_count,
                    relevance_score, relevance_reason, pool_expression,
                    pool_topic_label, candidate_tier, source, content_id,
                    content_url, source_platform, author_name, body_text,
                    content_type
                ) VALUES (
                    ?, ?, ?, 0, 0, '[]',
                    '', '', '', ?,
                    '', 0, 0, 0,
                    0, 0, 0, 0,
                    0, 0, 0,
                    0.0, '', '',
                    '', 'primary', 'rss_polling', ?,
                    ?, ?, ?,
                    '',
                    'article'
                )""",
                (
                    bvid,
                    title,
                    source_name,
                    (description or "")[:500],
                    bvid,
                    url,
                    source_platform,
                    author,
                ),
            )
            self.conn.commit()
        except Exception:
            logger.exception("Failed to inject article to pool: %s", title)

    def get_recent_articles(
        self,
        limit: int = 50,
        offset: int = 0,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
        random_order: bool = False,
    ) -> list[dict[str, Any]]:
        """Get recent articles, optionally filtered by source_type, status, or tag."""
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if source_type:
                conditions.append("source_type = ?")
                params.append(source_type)
            if status:
                conditions.append("status = ?")
                params.append(status)
            else:
                # 屏蔽位终态：无显式 status 过滤时永不再出现（见 ARTICLE_STATUSES）。
                conditions.append("status != 'hidden'")
            if tag:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            if random_order:
                # Do NOT use "ORDER BY RANDOM()": it materializes every
                # matching row (all selected columns) into a sorter B-tree,
                # which on a ~80k-row library blows the SQLite page cache and
                # makes the first shuffle pay a full cold read (~1s). Instead
                # scan only the rowid (PK) — a few MB at most — sample ids in
                # Python, then fetch just the sampled rows by PK.
                id_rows = self.conn.execute(
                    f"SELECT id FROM articles {where}", tuple(params)
                ).fetchall()
                ids = [row["id"] for row in id_rows]
                if not ids:
                    return []
                sample = random.sample(ids, limit) if len(ids) > limit else ids
                placeholders = ",".join("?" * len(sample))
                cursor = self.conn.execute(
                    f"""SELECT id, source_type, source_name, title, url, author,
                               summary, published_at, tags, status, created_at,
                               reading_percent, favorited, ai_summary
                        FROM articles
                        WHERE id IN ({placeholders})""",
                    tuple(sample),
                )
                return [dict(row) for row in cursor.fetchall()]
            order_sql = "ORDER BY published_at DESC, created_at DESC"
            cursor = self.conn.execute(
                f"""SELECT id, source_type, source_name, title, url, author,
                           summary, published_at, tags, status, created_at,
                           reading_percent, favorited, ai_summary
                    FROM articles
                    {where}
                    {order_sql}
                    LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to query articles")
            return []

    def count_articles(
        self,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> int:
        """Count articles matching the same filters as :meth:`get_recent_articles`."""
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if source_type:
                conditions.append("source_type = ?")
                params.append(source_type)
            if status:
                conditions.append("status = ?")
                params.append(status)
            else:
                # 与 get_recent_articles 保持同口径：屏蔽位不计入未指定 status 的计数。
                conditions.append("status != 'hidden'")
            if tag:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            row = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM articles {where}",
                tuple(params),
            ).fetchone()
            return int(row["n"]) if row else 0
        except Exception:
            logger.exception("Failed to count articles")
            return 0

    def count_readarchive(
        self,
        source_type: str | None = None,
        tag: str | None = None,
    ) -> int:
        """Count items in the read_archive table, optional filters."""
        try:
            conditions: list[str] = []
            params: list[Any] = []
            if source_type:
                conditions.append("source_type = ?")
                params.append(source_type)
            if tag:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            row = self.conn.execute(
                f"SELECT COUNT(*) AS n FROM read_archive {where}",
                tuple(params),
            ).fetchone()
            return int(row["n"]) if row else 0
        except Exception:
            logger.exception("Failed to count read_archive")
            return 0

    def get_recent_readarchive(
        self,
        limit: int = 50,
        offset: int = 0,
        source_type: str | None = None,
        tag: str | None = None,
        random_order: bool = False,
    ) -> list[dict[str, Any]]:
        """Get recent items from read_archive, filtered similarly to get_recent_articles.

        read_archive doesn't track reading progress/status, only finished reads.
        """
        import random

        try:
            conditions: list[str] = []
            params: list[Any] = []
            if source_type:
                conditions.append("source_type = ?")
                params.append(source_type)
            if tag:
                conditions.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            where = "WHERE " + " AND ".join(conditions) if conditions else ""
            if random_order:
                id_rows = self.conn.execute(
                    f"SELECT id FROM read_archive {where}", tuple(params)
                ).fetchall()
                ids = [row["id"] for row in id_rows]
                if not ids:
                    return []
                sample = random.sample(ids, limit) if len(ids) > limit else ids
                placeholders = ",".join("?" * len(sample))
                cursor = self.conn.execute(
                    f"""SELECT id, source_type, source_name, title, url, author,
                               summary, content_text, published_at, tags, created_at,
                               updated_at
                        FROM read_archive
                        WHERE id IN ({placeholders})""",
                    tuple(sample),
                )
                return [dict(row) for row in cursor.fetchall()]
            order_sql = "ORDER BY published_at DESC, created_at DESC"
            cursor = self.conn.execute(
                f"""SELECT id, source_type, source_name, title, url, author,
                           summary, content_text, published_at, tags, created_at,
                           updated_at
                    FROM read_archive
                    {where}
                    {order_sql}
                    LIMIT ? OFFSET ?""",
                (*params, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to get read_archive items")
            return []

    def search_readarchive(
        self,
        q: str,
        limit: int = 30,
        offset: int = 0,
        source_type: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search over the read_archive table."""
        q = (q or "").strip()
        if not q:
            return []
        cols = (
            "a.id, a.source_type, a.source_name, a.title, a.url, a.author, "
            "a.summary, a.published_at, a.tags, a.created_at"
        )
        filters: list[str] = []
        fparams: list[Any] = []
        if source_type:
            filters.append("a.source_type = ?")
            fparams.append(source_type)
        if tag:
            filters.append("a.tags LIKE ?")
            fparams.append(f'%"{tag}"%')
        fsql = (" AND " + " AND ".join(filters)) if filters else ""

        if len(q) >= 3:
            try:
                fts_q = '"' + q.replace('"', '""') + '"'
                cursor = self.conn.execute(
                    f"""SELECT {cols} FROM read_archive a
                        JOIN read_archive_fts f ON a.id = f.rowid
                        WHERE read_archive_fts MATCH ? {fsql}
                        ORDER BY bm25(read_archive_fts)
                        LIMIT ? OFFSET ?""",
                    (fts_q, *fparams, limit, offset),
                )
                return [dict(row) for row in cursor.fetchall()]
            except Exception:
                logger.exception("FTS search failed, falling back to LIKE")

        like = f"%{q}%"
        try:
            cursor = self.conn.execute(
                f"""SELECT {cols} FROM read_archive a
                    WHERE (a.title LIKE ? OR a.content_text LIKE ?
                          OR a.tags LIKE ? OR a.author LIKE ? OR a.summary LIKE ?)
                          {fsql}
                    ORDER BY a.published_at DESC, a.created_at DESC
                    LIMIT ? OFFSET ?""",
                (like, like, like, like, like, *fparams, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("LIKE search failed")
            return []

    def get_readarchive_article(self, article_id: int) -> dict[str, Any] | None:
        """Fetch a single read_archive article including its full body."""
        try:
            cursor = self.conn.execute(
                """SELECT id, source_type, source_name, title, url, author,
                           summary, content_text, published_at, tags,
                           created_at, updated_at
                    FROM read_archive WHERE id = ?""",
                (article_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception:
            logger.exception("Failed to fetch read_archive article %d", article_id)
            return None

    def search_articles(
        self,
        q: str,
        limit: int = 30,
        offset: int = 0,
        source_type: str | None = None,
        status: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Full-text search over the reading library.

        Uses the ``articles_fts`` trigram index for queries of 3+ characters
        (Chinese substring friendly) and falls back to ``LIKE`` for shorter
        queries or when FTS fails. Respects the same source/status/tag filters
        as :meth:`get_recent_articles`. Returns the same column shape.
        """
        q = (q or "").strip()
        if not q:
            return []
        cols = (
            "a.id, a.source_type, a.source_name, a.title, a.url, a.author, "
            "a.summary, a.published_at, a.tags, a.status, a.created_at, "
            "a.reading_percent, a.favorited, a.ai_summary"
        )
        filters: list[str] = []
        fparams: list[Any] = []
        if source_type:
            filters.append("a.source_type = ?")
            fparams.append(source_type)
        if status:
            filters.append("a.status = ?")
            fparams.append(status)
        else:
            # 与 get_recent_articles / count_articles 同口径：屏蔽位不进搜索结果。
            filters.append("a.status != 'hidden'")
        if tag:
            filters.append("a.tags LIKE ?")
            fparams.append(f'%"{tag}"%')
        fsql = (" AND " + " AND ".join(filters)) if filters else ""

        # Trigram FTS for queries >= 3 chars (phrase-quoted to avoid syntax errors)
        if len(q) >= 3:
            try:
                fts_q = '"' + q.replace('"', '""') + '"'
                cursor = self.conn.execute(
                    f"""SELECT {cols} FROM articles a
                        JOIN articles_fts f ON a.id = f.rowid
                        WHERE articles_fts MATCH ? {fsql}
                        ORDER BY bm25(articles_fts)
                        LIMIT ? OFFSET ?""",
                    (fts_q, *fparams, limit, offset),
                )
                return [dict(row) for row in cursor.fetchall()]
            except Exception:
                logger.exception("FTS search failed, falling back to LIKE")

        # LIKE fallback (short queries or FTS error)
        like = f"%{q}%"
        try:
            cursor = self.conn.execute(
                f"""SELECT {cols} FROM articles a
                    WHERE (a.title LIKE ? OR a.content_text LIKE ?
                          OR a.tags LIKE ? OR a.author LIKE ? OR a.summary LIKE ?)
                          {fsql}
                    ORDER BY a.published_at DESC, a.created_at DESC
                    LIMIT ? OFFSET ?""",
                (like, like, like, like, like, *fparams, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to search articles")
            return []

    def get_article(self, article_id: int) -> dict[str, Any] | None:
        """Fetch a single article including its full body text."""
        try:
            cursor = self.conn.execute(
                """SELECT id, source_type, source_name, title, url, author,
                           summary, content_text, published_at, tags, status,
                           reading_percent, reading_progress, favorited,
                           ai_summary,
                           created_at, updated_at
                    FROM articles WHERE id = ?""",
                (article_id,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception:
            logger.exception("Failed to fetch article %d", article_id)
            return None

    def update_article_tags(self, article_id: int, tags: list[str]) -> bool:
        """Update tags for an article. Returns True on success."""
        import json

        try:
            self.conn.execute(
                "UPDATE articles SET tags = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (json.dumps(tags, ensure_ascii=False), article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to update tags for article %d", article_id)
            return False

    #: Canonical reading-library states. Kept in sync with the
    #: ``/api/articles/{id}`` PATCH endpoint validation.
    #: ``hidden`` is the terminal "屏蔽 / 不再出现" state: the user dismissed
    #: the article, so the listing / count / search queries exclude it unless a
    #: caller explicitly filters ``status='hidden'``. Re-syncing the same URL
    #: never resurrects it (``upsert_article``'s ON CONFLICT leaves ``status``
    #: untouched).
    ARTICLE_STATUSES = ("unread", "reading", "finished", "archived", "hidden")

    def update_article_status(self, article_id: int, status: str) -> bool:
        """Update reading status for an article. Returns True on success."""
        if status not in self.ARTICLE_STATUSES:
            return False
        try:
            self.conn.execute(
                "UPDATE articles SET status = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (status, article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to update status for article %d", article_id)
            return False

    # ── 阅读辅助：进度 / 收藏 / 笔记 / AI 摘要 ────────────────────────

    def update_article_reading_progress(
        self, article_id: int, *, percent: float, progress: str = ""
    ) -> bool:
        """Persist reading position (percent 0-100 + optional scroll anchor).

        ``percent`` is clamped to [0, 100]; a finished article keeps its
        own status but the progress is still recorded for the stats panel.
        """
        try:
            percent = max(0.0, min(100.0, float(percent)))
            self.conn.execute(
                "UPDATE articles SET reading_percent = ?, reading_progress = ?, "
                "updated_at = datetime('now','localtime') WHERE id = ?",
                (percent, (progress or "")[:4000], article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to save reading progress for article %d", article_id)
            return False

    def set_article_favorited(self, article_id: int, favorited: bool) -> bool:
        """Mark / unmark an article as favorited. Returns True on success."""
        try:
            self.conn.execute(
                "UPDATE articles SET favorited = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (1 if favorited else 0, article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to set favorited for article %d", article_id)
            return False

    def add_article_note(
        self,
        article_id: int,
        *,
        quote: str = "",
        note: str = "",
        color: str = "",
    ) -> int | None:
        """Add a note / highlight to an article. Returns note id or None."""
        try:
            cursor = self.conn.execute(
                "INSERT INTO article_notes (article_id, quote, note, color) VALUES (?, ?, ?, ?)",
                (article_id, (quote or "")[:2000], (note or "")[:4000], (color or "")[:20]),
            )
            self.conn.commit()
            return cursor.lastrowid or None
        except Exception:
            logger.exception("Failed to add note for article %d", article_id)
            return None

    def get_article_notes(self, article_id: int) -> list[dict[str, Any]]:
        """Return all notes/highlights for an article, oldest first."""
        try:
            cursor = self.conn.execute(
                """SELECT id, article_id, quote, note, color, created_at, updated_at
                   FROM article_notes WHERE article_id = ? ORDER BY id ASC""",
                (article_id,),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to load notes for article %d", article_id)
            return []

    def delete_article_note(self, note_id: int) -> bool:
        """Delete one note by its own id. Returns True on success."""
        try:
            self.conn.execute("DELETE FROM article_notes WHERE id = ?", (note_id,))
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to delete note %d", note_id)
            return False

    def update_article_ai_summary(self, article_id: int, ai_summary: str) -> bool:
        """Store the generated AI summary JSON for an article."""
        try:
            self.conn.execute(
                "UPDATE articles SET ai_summary = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                ((ai_summary or "")[:6000], article_id),
            )
            self.conn.commit()
            return True
        except Exception:
            logger.exception("Failed to store AI summary for article %d", article_id)
            return False

    def get_articles_missing_summary(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Return recent articles that have body text but no AI summary yet."""
        try:
            cursor = self.conn.execute(
                """SELECT id, source_type, source_name, title, url, author,
                          summary, content_text, tags, status, created_at,
                          ai_summary
                   FROM articles
                   WHERE length(content_text) > 200
                     AND (ai_summary IS NULL OR ai_summary = '')
                   ORDER BY created_at DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to list articles missing AI summary")
            return []

    def get_article_reading_stats(self) -> dict[str, Any]:
        """Reading-library statistics for the dashboard.

        Returns totals by status, source-type distribution, notes count,
        finished counts by month (UTC), the top-10 most-read tags, a weekly
        finished trend (``by_week``) and a last-60-day daily timeline
        (``timeline``). ``by_week`` / ``timeline`` attribute a finished article
        to the day it was completed (``date(updated_at)``), matching
        :meth:`get_daily_reading_summary`.
        """
        import json as _json

        stats: dict[str, Any] = {
            "by_status": {},
            "by_source": {},
            "notes": 0,
            "by_month": {},
            "top_tags": [],
            "by_week": {},
            "timeline": [],
        }
        try:
            row = self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM articles GROUP BY status"
            ).fetchall()
            stats["by_status"] = {str(r["status"]): int(r["n"]) for r in row}
            row = self.conn.execute(
                "SELECT source_type, COUNT(*) AS n FROM articles GROUP BY source_type ORDER BY n DESC"
            ).fetchall()
            stats["by_source"] = {str(r["source_type"]): int(r["n"]) for r in row}
            stats["notes"] = int(
                self.conn.execute("SELECT COUNT(*) AS n FROM article_notes").fetchone()["n"]
            )
            row = self.conn.execute(
                """SELECT substr(published_at, 1, 7) AS ym, COUNT(*) AS n
                   FROM articles WHERE status = 'finished' AND published_at != ''
                   GROUP BY ym ORDER BY ym DESC LIMIT 12"""
            ).fetchall()
            stats["by_month"] = {str(r["ym"]): int(r["n"]) for r in row}
            tag_counter: dict[str, int] = {}
            for r in self.conn.execute(
                "SELECT tags FROM articles WHERE tags IS NOT NULL AND tags != '[]' LIMIT 2000"
            ).fetchall():
                try:
                    for t in _json.loads(r["tags"]):
                        tag_counter[str(t)] = tag_counter.get(str(t), 0) + 1
                except Exception:
                    continue
            stats["top_tags"] = sorted(tag_counter.items(), key=lambda kv: kv[1], reverse=True)[:10]
            try:
                row = self.conn.execute(
                    """SELECT strftime('%Y-W%W', updated_at) AS yw, COUNT(*) AS n
                       FROM articles
                       WHERE status = 'finished' AND updated_at != ''
                       GROUP BY yw ORDER BY yw DESC LIMIT 14"""
                ).fetchall()
                stats["by_week"] = {str(r["yw"]): int(r["n"]) for r in reversed(row)}
            except Exception:
                logger.exception("Failed to compute weekly reading trend")
            try:
                row = self.conn.execute(
                    """SELECT date(updated_at) AS d, COUNT(*) AS n
                       FROM articles
                       WHERE status = 'finished'
                         AND date(updated_at) >= date('now', '-60 days')
                       GROUP BY d ORDER BY d ASC"""
                ).fetchall()
                stats["timeline"] = [{"date": str(r["d"]), "count": int(r["n"])} for r in row]
            except Exception:
                logger.exception("Failed to compute reading timeline")
            return stats
        except Exception:
            logger.exception("Failed to compute reading stats")
            return stats

    def get_articles_for_reading_stats(self, *, limit: int = 500) -> list[dict[str, Any]]:
        """Recent finished/reading articles with percent & timestamps.

        Consumed by the dashboard's interest-shift chart: it maps each
        article's tags onto the current interest profile.
        """
        try:
            cursor = self.conn.execute(
                """SELECT id, source_type, source_name, title, url, author,
                          tags, status, reading_percent, published_at,
                          created_at, updated_at
                   FROM articles
                   WHERE status IN ('reading', 'finished')
                   ORDER BY updated_at DESC
                   LIMIT ?""",
                (max(1, int(limit)),),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to load articles for reading stats")
            return []

    def iter_articles_for_tagging(
        self,
        *,
        limit: int = 500,
        status: str | None = None,
        only_sparse: bool = True,
    ) -> list[dict[str, Any]]:
        """Lightweight rows for the auto-tag backfill (id, title, summary,
        ``content_text`` truncated, raw ``tags`` JSON).

        ``only_sparse`` keeps the scan cheap and focused on under-tagged items
        (existing tag count <= 1, usually just the source name). ``status='hidden'``
        is always excluded.
        """
        try:
            limit = max(1, min(int(limit), 2000))
            conditions = ["COALESCE(status, 'unread') != 'hidden'"]
            params: list[Any] = []
            if status:
                conditions.append("status = ?")
                params.append(status)
            sparse_clause = (
                " AND (tags IS NULL OR tags = '' OR json_array_length(tags) <= 1)"
                if only_sparse
                else ""
            )
            where = " AND ".join(conditions)
            cursor = self.conn.execute(
                f"""SELECT id, title, substr(content_text, 1, 4000) AS content_text,
                           summary, tags
                    FROM articles
                    WHERE {where}{sparse_clause}
                    ORDER BY updated_at DESC
                    LIMIT ?""",
                (*params, limit),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception:
            logger.exception("Failed to scan articles for tagging")
            return []

    def get_daily_reading_summary(self, *, day: str) -> dict[str, Any]:
        """当日已读回顾（每日简报「今日阅读回顾」板块的数据源）。

        统计本地时区 ``day``（``YYYY-MM-DD``）内被标记为 finished 的文章：
        读完数、来源分布、主题标签。按 ``date(updated_at)`` 判定——
        ``update_article_status`` 写本地时间(北京时间)，标记读完即归入
        当天，与用户感知一致。
        """
        import json as _json

        summary: dict[str, Any] = {
            "finished_today": 0,
            "by_source": {},
            "top_topics": [],
        }
        try:
            rows = self.conn.execute(
                """SELECT source_type, tags FROM articles
                   WHERE status = 'finished' AND date(updated_at) = ?""",
                (day,),
            ).fetchall()
        except Exception:
            logger.exception("Failed to load daily reading summary for %s", day)
            return summary
        tag_counter: dict[str, int] = {}
        for row in rows:
            source = str(row["source_type"] or "其他")
            summary["by_source"][source] = summary["by_source"].get(source, 0) + 1
            try:
                tags = _json.loads(row["tags"]) if row["tags"] else []
            except Exception:
                tags = []
            if isinstance(tags, list):
                for tag in tags:
                    text = str(tag).strip()
                    if text:
                        tag_counter[text] = tag_counter.get(text, 0) + 1
        summary["finished_today"] = len(rows)
        summary["top_topics"] = [
            tag for tag, _ in sorted(tag_counter.items(), key=lambda kv: kv[1], reverse=True)[:6]
        ]
        return summary

    def iter_cover_lifecycle(self) -> list[tuple[str, str, bool]]:
        """Return ``(cover_url, pool_status, is_saved)`` for every cached-cover candidate.

        ``is_saved`` is True when the bvid is in favorites or watch_later. Consumed
        by the image-cache cleanup (:mod:`openbiliclaw.runtime.image_cache`) to decide
        which cached cover files are safe to evict: covers of saved or still-pending
        content are kept; covers of consumed, unsaved content are eligible for removal.
        """
        cursor = self.conn.execute(
            """
            SELECT
                COALESCE(cc.cover_url, '') AS cover_url,
                COALESCE(cc.pool_status, 'fresh') AS pool_status,
                CASE WHEN f.bvid IS NOT NULL OR w.bvid IS NOT NULL THEN 1 ELSE 0 END AS is_saved
            FROM content_cache AS cc
            LEFT JOIN favorites AS f ON f.bvid = cc.bvid
            LEFT JOIN watch_later AS w ON w.bvid = cc.bvid
            WHERE COALESCE(cc.cover_url, '') <> ''
            """
        )
        return [
            (str(row["cover_url"]), str(row["pool_status"]), bool(row["is_saved"]))
            for row in cursor.fetchall()
        ]

    def iter_servable_cover_urls(self, *, recent_hours: int = 12, limit: int = 300) -> list[str]:
        """Recent, still-servable cover URLs (newest first) for discovery-time prefetch.

        Returns covers of content that may still be shown — ``pool_status`` in
        ``fresh / shown / suppressed``, or saved (favorites / watch_later) — limited
        to the last ``recent_hours`` of discoveries and ordered newest-first, so the
        prefetch sweep (:mod:`openbiliclaw.runtime.image_cache`) caches the freshest
        CDN tokens (notably XHS) before they expire. The recency window also keeps the
        sweep from endlessly retrying old content whose signed token is already dead.
        """
        cursor = self.conn.execute(
            """
            SELECT cc.cover_url
            FROM content_cache AS cc
            LEFT JOIN favorites AS f ON f.bvid = cc.bvid
            LEFT JOIN watch_later AS w ON w.bvid = cc.bvid
            WHERE COALESCE(cc.cover_url, '') <> ''
              AND cc.discovered_at >= datetime('now', ?)
              AND (
                COALESCE(cc.pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
                OR f.bvid IS NOT NULL
                OR w.bvid IS NOT NULL
              )
            ORDER BY cc.discovered_at DESC
            LIMIT ?
            """,
            (f"-{int(recent_hours)} hours", limit),
        )
        return [str(row["cover_url"]) for row in cursor.fetchall()]

    # ── XHS observed URL ingest ───────────────────────────────────

    def save_xhs_observed_urls(self, urls: list[str], page_type: str) -> int:
        """Insert observed xhs URLs, skipping duplicates. Returns count inserted."""
        inserted = 0
        for url in urls:
            # Skip if we've already seen this URL
            existing = self.conn.execute(
                "SELECT 1 FROM xhs_observed_urls WHERE url = ?", (url,)
            ).fetchone()
            if existing:
                continue
            self._execute_write(
                "INSERT INTO xhs_observed_urls (url, page_type) VALUES (?, ?)",
                (url, page_type),
            )
            inserted += 1
        return inserted

    # ── Source recipe CRUD ──────────────────────────────────────────

    def save_source_recipe(self, recipe: dict[str, Any]) -> None:
        """Insert or update a source recipe."""
        import json as _json

        self._execute_write(
            """
            INSERT INTO source_recipes (id, source_type, name, strategy, config,
                                        target_share, enabled, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                strategy = excluded.strategy,
                config = excluded.config,
                target_share = excluded.target_share,
                enabled = excluded.enabled
            """,
            (
                str(recipe["id"]),
                str(recipe["source_type"]),
                str(recipe["name"]),
                str(recipe["strategy"]),
                _json.dumps(recipe.get("config", {}), ensure_ascii=False),
                int(recipe.get("target_share", 4)),
                int(recipe.get("enabled", True)),
                str(recipe.get("created_by", "system")),
                recipe.get("created_at") or None,
            ),
        )

    def get_all_recipes(self) -> list[dict[str, Any]]:
        """Return all source recipes."""
        self._ensure_fresh_read()
        rows = self.conn.execute("SELECT * FROM source_recipes ORDER BY created_at").fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def get_enabled_recipes(self) -> list[dict[str, Any]]:
        """Return only enabled source recipes."""
        self._ensure_fresh_read()
        rows = self.conn.execute(
            "SELECT * FROM source_recipes WHERE enabled = 1 ORDER BY created_at"
        ).fetchall()
        return [self._row_to_recipe(row) for row in rows]

    def update_recipe(self, recipe_id: str, **fields: Any) -> bool:
        """Update specific fields of a recipe. Returns True if a row was updated."""
        import json as _json

        allowed = {"name", "strategy", "config", "target_share", "enabled", "last_fetched_at"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        if "config" in updates and not isinstance(updates["config"], str):
            updates["config"] = _json.dumps(updates["config"], ensure_ascii=False)
        if "enabled" in updates:
            updates["enabled"] = int(updates["enabled"])

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [recipe_id]
        cursor = self._execute_write(
            f"UPDATE source_recipes SET {set_clause} WHERE id = ?",
            tuple(values),
        )
        return cursor.rowcount > 0

    def delete_recipe(self, recipe_id: str) -> bool:
        """Delete a recipe by id. Returns True if a row was deleted."""
        cursor = self._execute_write(
            "DELETE FROM source_recipes WHERE id = ?",
            (recipe_id,),
        )
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_recipe(row: Any) -> dict[str, Any]:
        import json as _json

        config_raw = row["config"] if row["config"] else "{}"
        try:
            config = _json.loads(config_raw)
        except (ValueError, TypeError):
            config = {}
        return {
            "id": str(row["id"]),
            "source_type": str(row["source_type"]),
            "name": str(row["name"]),
            "strategy": str(row["strategy"]),
            "config": config,
            "target_share": int(row["target_share"]),
            "enabled": bool(row["enabled"]),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"] or ""),
            "last_fetched_at": str(row["last_fetched_at"] or ""),
        }

    def get_delight_candidate(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 1,
    ) -> dict[str, Any] | None:
        """Return one un-notified pool item with the highest delight_score.

        Backwards-compatible: ``limit=1`` returns a single dict (or None);
        callers that want multiple candidates (for example to filter
        disliked topics in Python) should call
        ``get_delight_candidates`` instead.
        """
        rows = self.get_delight_candidates(
            min_delight_score=min_delight_score,
            limit=max(1, int(limit)),
        )
        return rows[0] if rows else None

    def get_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
        limit: int = 20,
        include_liked: bool = False,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` un-notified delight candidates ordered by score.

        Restricts to ``pool_status IN ('fresh', 'shown')`` —  ``suppressed``
        items have been trimmed out of the active pool by topic-group cap
        or source-share quota and shouldn't reappear as delights. Without
        this guard, popup re-hydration would pull historical delight
        scores baked under earlier (looser) calibrations from the
        suppressed graveyard and surface 20 stale "surprises" on every
        extension reload (observed 2026-05-04: 562 suppressed items
        carried delight metadata vs 2 in fresh).

        ``include_liked`` keeps ``feedback_type='like'`` rows in the result.
        Queue re-hydration (``/api/delight/pending-batch``) passes True so a
        liked delight stays visible until the user explicitly dismisses it —
        positive feedback must not remove the card (v0.3.63 contract). New
        delivery paths (WS push, counts, CLI) keep the default False so an
        already-liked item is never re-pushed as a fresh surprise.
        """
        feedback_clause = (
            "COALESCE(feedback_type, '') IN ('', 'like')"
            if include_liked
            else "COALESCE(feedback_type, '') = ''"
        )
        admission_sql, admission_params = self._admission_predicate_sql()
        cursor = self.conn.execute(
            f"""
            SELECT *
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND {admission_sql}
              AND COALESCE(delight_notified, 0) = 0
              AND COALESCE(delight_reason, '') != ''
              AND COALESCE(delight_hook, '') != ''
              AND {feedback_clause}
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown')
            ORDER BY delight_score DESC, relevance_score DESC, discovered_at DESC
            LIMIT ?
            """,
            (min_delight_score, *admission_params, max(1, int(limit))),
        )
        return [dict(row) for row in cursor.fetchall()]

    def mark_delight_notified(self, bvid: str) -> None:
        """Mark one content item as delight-notified."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_notified = 1,
                delight_notified_at = CURRENT_TIMESTAMP
            WHERE bvid = ?
            """,
            (bvid,),
        )

    def update_delight_score(
        self,
        bvid: str,
        *,
        delight_score: float,
        delight_reason: str,
        delight_hook: str = "",
    ) -> None:
        """Persist the computed delight score and explanation for a pool item."""
        self._execute_write(
            """
            UPDATE content_cache
            SET delight_score = ?,
                delight_reason = ?,
                delight_hook = ?
            WHERE bvid = ?
            """,
            (delight_score, delight_reason, delight_hook, bvid),
        )

    def count_delight_candidates(
        self,
        *,
        min_delight_score: float = 0.85,
    ) -> int:
        """Return the number of un-notified delight candidates."""
        admission_sql, admission_params = self._admission_predicate_sql()
        cursor = self.conn.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM content_cache
            WHERE COALESCE(delight_score, 0.0) >= ?
              AND {admission_sql}
              AND COALESCE(delight_notified, 0) = 0
              AND COALESCE(delight_reason, '') != ''
              AND COALESCE(delight_hook, '') != ''
              AND COALESCE(feedback_type, '') = ''
              AND COALESCE(pool_status, 'fresh') IN ('fresh', 'shown', 'suppressed')
            """,
            (min_delight_score, *admission_params),
        )
        row = cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    def get_pool_candidates_needing_delight_score(
        self,
        limit: int = 30,
        *,
        min_delight_score_for_reason: float | None = None,
        min_relevance_score: float = 0.55,
        xhs_self_nickname: str = "",
    ) -> list[dict[str, Any]]:
        """Return pool candidates that still need delight evaluation or copy.

        Two-stage retrieval: ``relevance_score >= min_relevance_score``
        is the cheap pre-filter (the discovery LLM already judged user-
        content fit during ``evaluate_batch``), then the caller runs the
        expensive LLM delight scorer only on this shortlist.

        Default 0.55 is calibrated to the discovery rubric:
          0.6+ strong fit, 0.5-0.6 moderate, <0.5 weak fit.
        Items below ``min_relevance_score`` skip delight scoring
        entirely — they're not going to delight anyone they don't
        already half-fit, and burning LLM calls on weak-fit items just
        wastes budget.
        """
        guard_sql = _xhs_self_author_guard_sql()
        guard_params = _xhs_self_author_guard_params(xhs_self_nickname)
        effective_min_relevance_score = _normalize_admission_min_score(min_relevance_score)
        if min_delight_score_for_reason is None:
            cursor = self.conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(delight_score, 0.0) = 0.0
                  AND COALESCE(relevance_score, 0.0) >= ?
                  {guard_sql}
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                ORDER BY relevance_score DESC, discovered_at DESC
                LIMIT ?
                """,
                (effective_min_relevance_score, *guard_params, limit),
            )
        else:
            cursor = self.conn.execute(
                f"""
                SELECT *
                FROM content_cache
                WHERE COALESCE(pool_status, 'fresh') IN ('fresh', 'suppressed')
                  AND COALESCE(feedback_type, '') != 'dislike'
                  AND COALESCE(relevance_score, 0.0) >= ?
                  AND (
                    COALESCE(delight_score, 0.0) = 0.0
                    OR (
                      COALESCE(delight_score, 0.0) >= ?
                      AND (
                        COALESCE(delight_reason, '') = ''
                        OR COALESCE(delight_hook, '') = ''
                      )
                    )
                  )
                  {guard_sql}
                  AND NOT EXISTS (
                    SELECT 1
                    FROM recommendations AS r
                    WHERE r.bvid = content_cache.bvid
                  )
                ORDER BY
                    CASE WHEN COALESCE(delight_score, 0.0) > 0.0 THEN 0 ELSE 1 END ASC,
                    delight_score DESC,
                    relevance_score DESC,
                    discovered_at DESC
                LIMIT ?
                """,
                (
                    effective_min_relevance_score,
                    min_delight_score_for_reason,
                    *guard_params,
                    limit,
                ),
            )
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
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

    @classmethod
    def _extract_content_keys_from_view_event(cls, row: dict[str, Any]) -> set[str]:
        metadata = cls._decode_event_metadata(row)
        url = str(row.get("url", "")).strip()

        platform = _normalize_source_platform_key(metadata.get("source_platform", ""))
        if not platform:
            platform = cls._infer_source_platform_from_url(url)

        content_ids: set[str] = set()
        for key in _VIEW_CONTENT_ID_METADATA_KEYS:
            raw_value = metadata.get(key, "")
            if isinstance(raw_value, (str, int)):
                value = str(raw_value).strip()
                if value:
                    content_ids.add(value)

        url_content_id = cls._extract_content_id_from_url(platform, url)
        if url_content_id:
            content_ids.add(url_content_id)

        bvid = cls._extract_bvid_from_view_event(row)
        if bvid:
            content_ids.add(bvid)
            platform = platform or _BILIBILI_SOURCE_FAMILY

        keys: set[str] = set()
        for content_id in content_ids:
            if content_id.startswith("BV"):
                keys.add(content_id)
            if platform:
                keys.add(f"{platform}:{content_id}")
        return keys

    @staticmethod
    def _infer_source_platform_from_url(url: str) -> str:
        if not url:
            return ""
        host = urlparse(url).netloc.lower()
        if "bilibili.com" in host or host == "b23.tv":
            return _BILIBILI_SOURCE_FAMILY
        if "xiaohongshu.com" in host or "xhslink.com" in host:
            return _XHS_SOURCE_FAMILY
        if "douyin.com" in host:
            return _DOUYIN_SOURCE_FAMILY
        if "youtube.com" in host or host == "youtu.be":
            return _YOUTUBE_SOURCE_FAMILY
        if (
            host == "x.com"
            or host.endswith(".x.com")
            or host == "twitter.com"
            or host.endswith(".twitter.com")
        ):
            return _TWITTER_SOURCE_FAMILY
        return ""

    @staticmethod
    def _extract_content_id_from_url(platform: str, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url)
        path_parts = [part for part in parsed.path.split("/") if part]
        if platform == _XHS_SOURCE_FAMILY:
            if len(path_parts) >= 2 and path_parts[0] == "explore":
                return path_parts[1]
            if len(path_parts) >= 3 and path_parts[:2] == ["discovery", "item"]:
                return path_parts[2]
        if platform == _DOUYIN_SOURCE_FAMILY and "video" in path_parts:
            video_index = path_parts.index("video")
            if len(path_parts) > video_index + 1:
                return path_parts[video_index + 1]
        if platform == _YOUTUBE_SOURCE_FAMILY:
            query_video_id = parse_qs(parsed.query).get("v", [""])[0].strip()
            if query_video_id:
                return query_video_id
            if parsed.netloc.lower() == "youtu.be" and path_parts:
                return path_parts[0]
            for prefix in ("shorts", "embed", "live"):
                if prefix in path_parts:
                    prefix_index = path_parts.index(prefix)
                    if len(path_parts) > prefix_index + 1:
                        return path_parts[prefix_index + 1]
        if platform == _BILIBILI_SOURCE_FAMILY:
            match = _BVID_PATTERN.search(url)
            if match:
                return match.group(1)
        return ""

    @staticmethod
    def _extract_bvid_from_view_event(row: dict[str, Any]) -> str:
        metadata = Database._decode_event_metadata(row)
        bvid = str(metadata.get("bvid", "")).strip()
        if bvid:
            return bvid

        url = str(row.get("url", "")).strip()
        match = _BVID_PATTERN.search(url)
        if match:
            return match.group(1)
        return ""

    @staticmethod
    def _content_row_view_keys(row: dict[str, Any]) -> set[str]:
        platform = _normalize_source_platform_key(row.get("source_platform", ""))
        if not platform:
            platform = _pool_source_family(row.get("source", ""), row.get("source_platform", ""))
            if platform == "unknown":
                platform = ""

        keys: set[str] = set()
        raw_bvid = str(row.get("bvid", "") or "").strip()
        content_id = str(row.get("content_id", "") or "").strip() or raw_bvid
        for value in {raw_bvid, content_id}:
            if not value:
                continue
            if value.startswith("BV"):
                keys.add(value)
            if platform:
                keys.add(f"{platform}:{value}")
        return keys

    @staticmethod
    def _is_viewed_row(row: dict[str, Any], viewed_content_keys: set[str]) -> bool:
        if not viewed_content_keys:
            return False
        return bool(Database._content_row_view_keys(row) & viewed_content_keys)

    @staticmethod
    def _exclude_viewed_rows(
        rows: list[dict[str, Any]],
        viewed_content_keys: set[str],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        if not viewed_content_keys:
            return rows[:limit]
        filtered = [row for row in rows if not Database._is_viewed_row(row, viewed_content_keys)]
        return filtered[:limit]

    # ── user feedback (like / dislike) ─────────────────────────────────

    def _ensure_user_feedback_table(self) -> None:
        self.conn.executescript(_USER_FEEDBACK_DDL)

    def insert_user_feedback(
        self,
        bvid: str,
        action: str,
        *,
        source_platform: str = "",
        title: str = "",
        topic_group: str = "",
        body_text: str = "",
    ) -> bool:
        """Record a like/dislike for a content item.  Returns True if
        inserted, False if the same (bvid, action) already exists
        (upsert-style: replace the existing row)."""
        existing = self.conn.execute(
            "SELECT id FROM user_feedback WHERE bvid = ? AND action = ?",
            (bvid, action),
        ).fetchone()
        if existing:
            # Update timestamp
            self.conn.execute(
                "UPDATE user_feedback SET created_at = CURRENT_TIMESTAMP WHERE id = ?",
                (existing["id"],),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            """INSERT INTO user_feedback (bvid, action, source_platform, title, topic_group, body_text)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (bvid, action, source_platform, title, topic_group, body_text),
        )
        self.conn.commit()
        return True

    def remove_user_feedback(self, bvid: str, action: str) -> bool:
        """Remove a specific feedback action for a content item."""
        cur = self.conn.execute(
            "DELETE FROM user_feedback WHERE bvid = ? AND action = ?",
            (bvid, action),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_user_feedback(self, bvid: str) -> list[dict[str, Any]]:
        """Get all feedback actions for a content item."""
        rows = self.conn.execute(
            "SELECT action, created_at FROM user_feedback WHERE bvid = ? ORDER BY created_at DESC",
            (bvid,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_user_feedback_batch(self, bvids: list[str]) -> dict[str, str]:
        """Get the latest feedback action for each bvid. Returns {bvid: action}."""
        if not bvids:
            return {}
        placeholders = ",".join("?" for _ in bvids)
        rows = self.conn.execute(
            f"""SELECT bvid, action FROM user_feedback
                WHERE bvid IN ({placeholders})
                GROUP BY bvid
                ORDER BY MAX(created_at) DESC""",
            bvids,
        ).fetchall()
        return {r["bvid"]: r["action"] for r in rows}

    def get_total_feedback_count(self) -> int:
        """Return total number of feedback entries (likes + dislikes)."""
        row = self.conn.execute("SELECT COUNT(*) as cnt FROM user_feedback").fetchone()
        return row["cnt"] if row else 0

    def get_feedback_aggregated(self) -> list[dict[str, Any]]:
        """Aggregate feedback per topic_group with like/dislike counts."""
        rows = self.conn.execute("""
            SELECT topic_group,
                   SUM(CASE WHEN action = 'like' THEN 1 ELSE 0 END) as likes,
                   SUM(CASE WHEN action = 'dislike' THEN 1 ELSE 0 END) as dislikes
            FROM user_feedback
            WHERE topic_group != '' AND topic_group IS NOT NULL
            GROUP BY topic_group
            ORDER BY likes DESC
        """).fetchall()
        return [
            {
                "topic_group": str(r["topic_group"] or ""),
                "likes": int(r["likes"] or 0),
                "dislikes": int(r["dislikes"] or 0),
            }
            for r in rows
        ]

    def get_interest_tags(self, limit: int = 20) -> list[dict[str, Any]]:
        """Aggregate interest tags from liked content.

        Returns a list of {tag, weight, source_platforms, count}
        sorted by descending weight.
        """
        # Get topic_group from liked items
        rows = self.conn.execute(
            """
            SELECT topic_group, source_platform, COUNT(*) as cnt
            FROM user_feedback
            WHERE action = 'like' AND topic_group != '' AND topic_group IS NOT NULL
            GROUP BY topic_group, source_platform
            ORDER BY cnt DESC
            LIMIT ?
        """,
            (limit * 3,),
        ).fetchall()

        # Aggregate by topic_group across platforms
        tags: dict[str, dict[str, Any]] = {}
        for r in rows:
            tg = str(r["topic_group"]).strip()
            if not tg:
                continue
            sp = str(r["source_platform"] or "")
            if tg not in tags:
                tags[tg] = {"tag": tg, "weight": 0, "source_platforms": [], "count": 0}
            tags[tg]["count"] += int(r["cnt"])
            tags[tg]["weight"] = tags[tg]["count"]
            if sp and sp not in tags[tg]["source_platforms"]:
                tags[tg]["source_platforms"].append(sp)

        # Also extract keywords from title of liked items
        title_rows = self.conn.execute("""
            SELECT title, source_platform FROM user_feedback
            WHERE action = 'like' AND title != '' AND title IS NOT NULL
            ORDER BY created_at DESC
            LIMIT 100
        """).fetchall()

        import re

        # Common Chinese stop words and generic terms
        stop_words = {
            "的",
            "了",
            "是",
            "在",
            "有",
            "和",
            "就",
            "不",
            "人",
            "都",
            "一",
            "一个",
            "这个",
            "那个",
            "什么",
            "怎么",
            "如何",
            "为什么",
            "可以",
            "没有",
            "不是",
            "就是",
            "还是",
            "我们",
            "他们",
            "你们",
            "自己",
            "知道",
            "觉得",
            "看到",
            "可能",
            "已经",
            "这样",
            "通过",
            "之后",
            "因为",
            "所以",
            "但是",
            "而且",
            "如果",
            "虽然",
            "然后",
        }

        word_counts: dict[str, int] = {}
        for r in title_rows:
            title = str(r["title"] or "")
            # Extract meaningful Chinese words (2-6 chars) and English words
            words = re.findall(r"[\u4e00-\u9fff]{2,6}|[a-zA-Z][a-zA-Z0-9]{2,}", title)
            for w in words:
                wl = w.lower()
                if wl in stop_words or len(w) < 2:
                    continue
                word_counts[wl] = word_counts.get(wl, 0) + 1

        # Merge title keywords into tags (words appearing 2+ times)
        for w, c in sorted(word_counts.items(), key=lambda x: -x[1]):
            if c < 2:
                continue
            if w not in tags:
                tags[w] = {"tag": w, "weight": 0, "source_platforms": [], "count": 0}
            tags[w]["weight"] += c
            tags[w]["count"] += c

        result = sorted(tags.values(), key=lambda x: -x["weight"])[:limit]
        return result

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
            str(row["name"])
            for row in self.conn.execute("PRAGMA table_info(articles)").fetchall()
        }
        required_columns = {
            "content_cleaned": "TEXT",           # 清理后的正文
            "content_clean_score": "REAL",       # 清理质量评分 0-100
            "content_clean_log": "TEXT",         # 清理日志（JSON）
            "content_verified": "INTEGER DEFAULT 0",  # 是否通过验证 0/1
            "content_verify_result": "TEXT",     # 验证结果（JSON）
            "summary_detailed": "TEXT",          # 详细版摘要
            "summary_compact": "TEXT",           # 精简版摘要
            "summary_ultra_compact": "TEXT",     # 超精简版摘要
            "summary_quality": "REAL",           # 摘要质量评分（0-1）
            "summary_version": "INTEGER DEFAULT 0",  # 摘要版本号
            "summary_generated_at": "TEXT",      # 摘要生成时间
        }
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            self.conn.execute(
                f"ALTER TABLE articles ADD COLUMN {column_name} {column_type}"
            )

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
            for row in self.conn.execute(
                "PRAGMA table_info(entity_relations)"
            ).fetchall()
        }
        if "co_occur" not in _er_columns:
            self.conn.execute(
                "ALTER TABLE entity_relations ADD COLUMN co_occur INTEGER DEFAULT 1"
            )

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
    def create_topic(
        self,
        *,
        name: str,
        slug: str,
        description: str = "",
        keywords: list[str] | None = None,
        platforms: list[str] | None = None,
    ) -> int:
        """Create a topic; returns its id (raises on duplicate name/slug)."""
        import json as _json

        cursor = self.conn.execute(
            """INSERT INTO topics (name, slug, description, keywords, platforms, status)
               VALUES (?, ?, ?, ?, ?, 'active')""",
            (
                name,
                slug,
                description,
                _json.dumps(keywords or [], ensure_ascii=False),
                _json.dumps(platforms or ["bilibili"], ensure_ascii=False),
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid or 0)

    def list_topics(self, *, include_paused: bool = True) -> list[dict[str, Any]]:
        """Return topics newest-first with item counts."""
        where = "" if include_paused else "WHERE status = 'active'"
        rows = self.conn.execute(
            f"""SELECT t.*,
                       (SELECT COUNT(*) FROM topic_items i WHERE i.topic_id = t.id) AS item_count
                FROM topics t {where}
                ORDER BY t.created_at DESC, t.id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    def get_topic_by_slug(self, slug: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM topics WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def get_topic_by_id(self, topic_id: int) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
        return dict(row) if row else None

    def add_topic_item(self, topic_id: int, item: dict[str, Any]) -> bool:
        """Insert one collected item (idempotent by topic_id+content_key).

        Returns True if inserted, False if the item already exists.
        """
        content_key = str(item.get("content_key") or "").strip()
        title = str(item.get("title") or "").strip()
        if not content_key or not title:
            return False
        existing = self.conn.execute(
            "SELECT id FROM topic_items WHERE topic_id = ? AND content_key = ?",
            (topic_id, content_key),
        ).fetchone()
        if existing:
            return False
        self.conn.execute(
            """INSERT INTO topic_items
               (topic_id, content_key, title, url, source_platform, source_name,
                cover_url, summary, topic_label)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                topic_id,
                content_key,
                title,
                str(item.get("url") or ""),
                str(item.get("source_platform") or ""),
                str(item.get("source_name") or ""),
                str(item.get("cover_url") or ""),
                str(item.get("summary") or ""),
                str(item.get("topic_label") or ""),
            ),
        )
        self.conn.commit()
        return True

    def get_topic_items(
        self,
        topic_id: int,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return a topic's collected items, newest first."""
        rows = self.conn.execute(
            """SELECT * FROM topic_items
               WHERE topic_id = ?
               ORDER BY collected_at DESC, id DESC
               LIMIT ? OFFSET ?""",
            (topic_id, limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_topic_items(self, topic_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM topic_items WHERE topic_id = ?", (topic_id,)
        ).fetchone()
        return int(row["n"]) if row else 0

    def mark_topic_collected(self, topic_id: int) -> None:
        """Stamp last_collected_at and refresh the stored item_count."""
        count = self.count_topic_items(topic_id)
        self.conn.execute(
            "UPDATE topics SET last_collected_at = CURRENT_TIMESTAMP, "
            "item_count = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (count, topic_id),
        )
        self.conn.commit()

    def insert_view_history(self, item: dict[str, Any]) -> None:
        """Record a content view / click."""
        self.conn.execute(
            """INSERT INTO view_history
               (bvid, title, source_platform, topic_group, content_url, up_name, quality_score, fit_score, dwell_seconds)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(item.get("bvid", "")),
                str(item.get("title", "") or ""),
                str(item.get("source_platform", "") or ""),
                str(item.get("topic_group", "") or ""),
                str(item.get("content_url", "") or ""),
                str(item.get("up_name", "") or item.get("author_name", "") or ""),
                float(item.get("quality_score", 0) or 0),
                float(item.get("fit_score", 0) or 0),
                float(item.get("dwell_seconds", 0) or 0),
            ),
        )
        self.conn.commit()

    def update_view_dwell(self, bvid: str, dwell_seconds: float) -> bool:
        """Attach dwell seconds to the most recent view of bvid."""
        row = self.conn.execute(
            "SELECT id FROM view_history WHERE bvid = ? ORDER BY id DESC LIMIT 1",
            (bvid,),
        ).fetchone()
        if not row:
            return False
        self.conn.execute(
            "UPDATE view_history SET dwell_seconds = ? WHERE id = ?",
            (float(dwell_seconds), row["id"]),
        )
        self.conn.commit()
        return True

    def get_dwell_scores(self, days: int = 14) -> dict[str, float]:
        """Aggregate dwell-weighted interest per topic_group (implicit feedback).

        Dwell per view is capped at 600s (10 min) so long abandoned tabs
        don't dominate. A topic accumulates toward 1.0 with ~30 min of
        total capped dwell.
        """
        import datetime

        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        try:
            rows = self.conn.execute(
                """SELECT topic_group,
                          SUM(MIN(dwell_seconds, 600)) AS dwell_sum,
                          COUNT(*) AS views,
                          SUM(CASE WHEN dwell_seconds >= 60 THEN 1 ELSE 0 END) AS deep_views,
                          SUM(CASE WHEN dwell_seconds > 0 AND dwell_seconds < 15 THEN 1 ELSE 0 END) AS quick_exits
                   FROM view_history
                   WHERE viewed_at >= ? AND COALESCE(topic_group, '') != ''
                   GROUP BY topic_group""",
                (cutoff,),
            ).fetchall()
        except Exception:
            return {}
        scores: dict[str, float] = {}
        for r in rows:
            dwell_sum = float(r["dwell_sum"] or 0)
            deep = int(r["deep_views"] or 0)
            quick = int(r["quick_exits"] or 0)
            # base: capped dwell accumulation, quick exits erode interest
            base = min(1.0, dwell_sum / 1800.0)
            penalty = 0.05 * quick
            boost = 0.1 * deep
            scores[str(r["topic_group"])] = max(0.0, min(1.0, base + boost - penalty))
        return scores

    def get_total_view_count(self, days: int = 30) -> int:
        """Count views recorded in the last N days (implicit feedback volume)."""
        import datetime

        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        try:
            row = self.conn.execute(
                "SELECT COUNT(*) AS cnt FROM view_history WHERE viewed_at >= ?",
                (cutoff,),
            ).fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            return 0

    def get_interest_centroid_sources(
        self,
        *,
        days: int = 30,
        min_dwell: float = 60.0,
    ) -> list[dict[str, Any]]:
        """Recent positive-signal rows backing the RankAgent interest centroids.

        Two signal kinds, unified shape: explicit likes (``user_feedback``) and
        deep views (``view_history`` with ``dwell_seconds >= min_dwell``), both
        windowed to the last ``days`` days. Each row is LEFT JOINed to
        ``content_cache`` to recover ``description`` so the caller can rebuild
        the canonical MMR embedding cache key (``title + description``); rows
        whose content was trimmed from the pool simply yield ``description=''``
        and become a cache miss downstream. Ordered by signal time DESC.
        """
        import datetime

        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        try:
            rows = self.conn.execute(
                """
                SELECT uf.topic_group AS topic_group,
                       uf.title       AS title,
                       COALESCE(cc.description, '') AS description,
                       uf.created_at  AS signaled_at
                FROM user_feedback uf
                LEFT JOIN content_cache cc ON cc.bvid = uf.bvid
                WHERE uf.action = 'like'
                  AND uf.created_at >= ?
                  AND COALESCE(uf.topic_group, '') != ''
                UNION ALL
                SELECT vh.topic_group AS topic_group,
                       vh.title       AS title,
                       COALESCE(cc.description, '') AS description,
                       vh.viewed_at   AS signaled_at
                FROM view_history vh
                LEFT JOIN content_cache cc ON cc.bvid = vh.bvid
                WHERE vh.viewed_at >= ?
                  AND vh.dwell_seconds >= ?
                  AND COALESCE(vh.topic_group, '') != ''
                ORDER BY signaled_at DESC
                """,
                (cutoff, cutoff, float(min_dwell)),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to load interest centroid sources")
            return []

    def get_recent_views(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get the most recent view history."""
        rows = self.conn.execute(
            """SELECT * FROM view_history
               ORDER BY viewed_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_view_count(self, bvid: str) -> int:
        """Get how many times a content item has been viewed."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM view_history WHERE bvid = ?",
            (bvid,),
        ).fetchone()
        return row["cnt"] if row else 0

    def get_viewed_bvids(self, days: int = 30) -> set[str]:
        """Get bvids viewed in the last N days."""
        import datetime

        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
        rows = self.conn.execute(
            "SELECT DISTINCT bvid FROM view_history WHERE viewed_at >= ?",
            (cutoff,),
        ).fetchall()
        return {r["bvid"] for r in rows}
