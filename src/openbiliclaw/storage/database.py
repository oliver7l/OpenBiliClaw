"""SQLite database management.

Provides async-compatible SQLite operations for event logs,
content cache, and recommendation history.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from openbiliclaw.storage._article_mixin import ArticleMixin
from openbiliclaw.storage._auth_mixin import AuthMixin
from openbiliclaw.storage._chat_turn_mixin import ChatTurnMixin
from openbiliclaw.storage._content_cache_mixin import ContentCacheMixin
from openbiliclaw.storage._cover_mixin import CoverMixin
from openbiliclaw.storage._delight_mixin import DelightMixin
from openbiliclaw.storage._discovery_candidates_mixin import DiscoveryCandidatesMixin
from openbiliclaw.storage._discovery_keywords_mixin import DiscoveryKeywordsMixin
from openbiliclaw.storage._events_mixin import EventsMixin
from openbiliclaw.storage._favorites_mixin import FavoritesMixin
from openbiliclaw.storage._init_runs_mixin import InitRunsMixin
from openbiliclaw.storage._llm_usage_mixin import LLMUsageMixin
from openbiliclaw.storage._native_sync_mixin import NativeSyncMixin
from openbiliclaw.storage._pool_candidate_mixin import PoolCandidateMixin
from openbiliclaw.storage._prune_mixin import PruneMixin
from openbiliclaw.storage._quality_mixin import QualityMixin
from openbiliclaw.storage._recommendation_mixin import RecommendationMixin
from openbiliclaw.storage._saved_memberships_mixin import SavedMembershipsMixin
from openbiliclaw.storage._schema_mixin import SchemaMixin
from openbiliclaw.storage._source_recipe_mixin import SourceRecipeMixin
from openbiliclaw.storage._topic_mixin import TopicMixin
from openbiliclaw.storage._user_feedback_mixin import UserFeedbackMixin
from openbiliclaw.storage._view_history_mixin import ViewHistoryMixin
from openbiliclaw.storage._watch_later_mixin import WatchLaterMixin

if TYPE_CHECKING:
    from collections.abc import Sequence
    pass

logger = logging.getLogger(__name__)

# ── 进程内统一写锁 + 连接工厂 ────────────────────────────────────────
# 大量离线模块（self_evolution/、knowledge_forge/ 等）不走 Database 类，
# 各自 sqlite3.connect 并同时写库，在 WAL 单写者模型下互相挤兑导致
# database is locked。这里提供一个进程级全局写锁：所有走 open_db_conn()
# 连接发出的写语句都会先取同一把锁，把随机撞锁变成排队写（读不受限）。
# WAL 下读写可并行，故只对写语句加锁，SELECT/WITH 不加，避免拖慢读。
DB_WRITE_LOCK = threading.RLock()

_WRITE_STATEMENT_PREFIXES = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "REPLACE",
    "CREATE",
    "ALTER",
    "DROP",
    "ATTACH",
    "DETACH",
    "BEGIN",
    "START",
    "COMMIT",
    "END",
    "ROLLBACK",
    "VACUUM",
    "REINDEX",
    "PRAGMA",
)


class LockedConnection(sqlite3.Connection):
    """SQLite connection that serializes write statements via DB_WRITE_LOCK.

    Reads (SELECT / WITH) bypass the lock so WAL read-parallelism is
    preserved; only writers queue on the shared process-wide lock.
    """

    def execute(self, sql, parameters=(), /):
        if _is_write_statement(sql):
            with DB_WRITE_LOCK:
                return super().execute(sql, parameters)
        return super().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters, /):
        if _is_write_statement(sql):
            with DB_WRITE_LOCK:
                return super().executemany(sql, seq_of_parameters)
        return super().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script, /):
        with DB_WRITE_LOCK:
            return super().executescript(sql_script)


def _is_write_statement(sql: object) -> bool:
    try:
        head = str(sql).lstrip()[:16].upper()
    except Exception:
        return True
    return head.startswith(_WRITE_STATEMENT_PREFIXES)


def open_db_conn(
    db_path: str | Path,
    *,
    isolation_level: str | None = "",
) -> LockedConnection:
    """Open a lock-serialized SQLite connection with sane WAL defaults.

    Replaces ad-hoc ``sqlite3.connect`` in modules that bypass Database.
    Enables WAL, raises busy_timeout to 60s (was Python default ~5s, the
    main reason writers gave up prematurely), and sets a sane cache so
    long batch writes are not as likely to hold the write lock open.
    """
    import sqlite3 as _sqlite3

    conn = _sqlite3.connect(
        str(db_path),
        timeout=60.0,
        check_same_thread=False,
        isolation_level=isolation_level,
        factory=LockedConnection,
    )
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-65536")
    # 附加兄弟子库：pool.db / events.db 与主库同目录且存在时，让 pool.* /
    # events.* 前缀在任意裸连（如 self_evolution 等离线模块）上也能解析到子库表，
    # 与 Database 自身连接保持一致。ATTACH 别名恒等于子库文件名（pool ↔ pool.db、
    # events ↔ events.db），避免 act/activity 等多套叫法。
    _base = Path(db_path)
    for _alias, _sibling in (("pool", _base.with_name("pool.db")), ("events", _base.with_name("events.db"))):
        if _sibling.exists():
            conn.execute(f"ATTACH DATABASE ? AS {_alias}", (str(_sibling),))
    return conn


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


class Database(AuthMixin, InitRunsMixin, SchemaMixin, DiscoveryKeywordsMixin, SavedMembershipsMixin, ViewHistoryMixin, QualityMixin, PruneMixin, PoolCandidateMixin, TopicMixin, NativeSyncMixin, WatchLaterMixin, DelightMixin, SourceRecipeMixin, CoverMixin, ArticleMixin, FavoritesMixin, UserFeedbackMixin, RecommendationMixin, DiscoveryCandidatesMixin, ContentCacheMixin, ChatTurnMixin, EventsMixin, LLMUsageMixin):
    """Lightweight SQLite wrapper for OpenBiliClaw.

    Manages the event log, content cache, and recommendation history.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        # 推荐流子库：content_cache/recommendations/user_feedback/xhs_observed_urls
        # 独立存放于 pool.db（总库+子库）。所有 Database 连接 ATTACH 该子库，
        # 无前缀 SQL 自动落到 pool schema（主库已不含这些表）。
        self._pool_db_path = self._db_path.with_name("pool.db")
        # 事件子库 events.db：高频读写的动态行为表（events / view_history）
        # 独立存放，与主库（笔记/日记/阅读库）及推荐流子库 pool.db 三者锁域隔离。
        # 主库 ATTACH 为 events，SQL 以 events. 前缀显式访问。对应 db sharding 计划 P2。
        self._events_db_path = self._db_path.with_name("events.db")
        # LLM 用量库 llm.db：极高频写入的 llm_usage 表（每次 LLM 调用都写）
        # 独立存放，与主库锁域隔离，避免 billing 写入阻塞核心业务。
        self._llm_db_path = self._db_path.with_name("llm.db")
        self._llm_conn: sqlite3.Connection | None = None
        # Discovery 库 discovery.db：搜索发现相关表（keywords/candidates/runs）
        # 独立存放，与主库锁域隔离。
        self._discovery_db_path = self._db_path.with_name("discovery.db")
        self._discovery_conn: sqlite3.Connection | None = None
        # Content 库 content.db：文章内容相关表（articles/favorites/watch_later）
        # 独立存放，与主库锁域隔离。
        self._content_db_path = self._db_path.with_name("content.db")
        self._content_conn: sqlite3.Connection | None = None
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

    _EVENTS_SCHEMA = (
        # 行为事件表：与主库历史 schema 一致（含生成列 source_platform + 索引）
        "CREATE TABLE IF NOT EXISTS events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " event_type TEXT NOT NULL, url TEXT, title TEXT, context TEXT, metadata TEXT,"
        " inferred_satisfaction TEXT, satisfaction_reason TEXT,"
        " created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,"
        " source_platform TEXT GENERATED ALWAYS AS"
        " (COALESCE(json_extract(metadata, '$.source_platform'), 'unknown')) VIRTUAL,"
        " article_id INTEGER);"
        "CREATE INDEX IF NOT EXISTS idx_events_created_at ON events (created_at DESC);"
        "CREATE INDEX IF NOT EXISTS idx_events_event_type ON events (event_type);"
        "CREATE INDEX IF NOT EXISTS idx_events_source_platform ON events (source_platform);"
        "CREATE INDEX IF NOT EXISTS idx_events_agg_stats"
        " ON events (event_type, source_platform, inferred_satisfaction);"
        # 浏览历史表 + 索引
        "CREATE TABLE IF NOT EXISTS view_history ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, bvid TEXT NOT NULL,"
        " title TEXT DEFAULT '', source_platform TEXT DEFAULT '', topic_group TEXT DEFAULT '',"
        " content_url TEXT DEFAULT '', up_name TEXT DEFAULT '',"
        " quality_score REAL DEFAULT 0.0, fit_score REAL DEFAULT 0.0,"
        " viewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, dwell_seconds REAL DEFAULT 0);"
        "CREATE INDEX IF NOT EXISTS idx_view_history_bvid ON view_history(bvid);"
        "CREATE INDEX IF NOT EXISTS idx_view_history_viewed_at ON view_history(viewed_at);"
    )

    def _ensure_events_database(self) -> None:
        """Ensure the events sub-database (events.db) exists.

        hosts events / view_history 两张高频动态表。首次缺失时按与主库一致的
        schema 建表，然后由 scripts/migrate_events_db.py 迁移历史数据。
        """
        if self._events_db_path.exists():
            return
        import sqlite3 as _sqlite3

        ac_conn = _sqlite3.connect(str(self._events_db_path), timeout=30.0)
        try:
            ac_conn.executescript(self._EVENTS_SCHEMA)
            # 让事件/浏览历史落到事件子库而不是主库：建库即建迁移后索引供查询
            ac_conn.commit()
            self._logger().warning(
                "events.db 不存在，已创建 events/view_history 事件子库。"
                "请运行 scripts/migrate_events_db.py 迁移历史数据。"
            )
        finally:
            ac_conn.close()

    def _attach_events(self, conn: sqlite3.Connection) -> None:
        """ATTACH the events sub-database to a connection (idempotent, alias events).

        别名为 events，与 events.db 及 db sharding 计划一致；SQL 用
        ``events.events`` / ``events.view_history`` 前缀显式访问。
        """
        with suppress(sqlite3.OperationalError):
            conn.execute("ATTACH DATABASE ? AS events", (str(self._events_db_path),))

    # ── LLM 用量子库（llm.db）──────────────────────────────────────────
    # 极高频写入的 llm_usage 表（每次 LLM 调用都写）独立存放，
    # 与主库锁域隔离，避免 billing 写入阻塞核心业务。

    _LLM_USAGE_SCHEMA = """
        CREATE TABLE IF NOT EXISTS llm_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            provider TEXT NOT NULL,
            model TEXT NOT NULL DEFAULT '',
            caller TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cached_input_tokens INTEGER NOT NULL DEFAULT 0,
            estimated_cost_cny REAL NOT NULL DEFAULT 0.0,
            success INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS idx_llm_usage_timestamp ON llm_usage(timestamp);
        CREATE INDEX IF NOT EXISTS idx_llm_usage_provider ON llm_usage(provider, model);
    """

    def _ensure_llm_database(self) -> None:
        """Ensure the LLM usage sub-database (llm.db) exists."""
        if self._llm_db_path.exists():
            return
        import sqlite3 as _sqlite3

        llm_conn = _sqlite3.connect(str(self._llm_db_path), timeout=30.0)
        try:
            llm_conn.executescript(self._LLM_USAGE_SCHEMA)
            llm_conn.commit()
            self._logger().warning(
                "llm.db 不存在，已创建 llm_usage 表。"
                "若主库存在旧 llm_usage，请先运行迁移脚本。"
            )
        finally:
            llm_conn.close()

    def _init_llm_connection(self) -> None:
        """Initialize the LLM database connection."""
        self._ensure_llm_database()
        self._llm_conn = sqlite3.connect(
            str(self._llm_db_path),
            timeout=30.0,
            check_same_thread=False,
            factory=LockedConnection,
        )
        self._llm_conn.row_factory = sqlite3.Row
        self._llm_conn.execute("PRAGMA journal_mode=WAL")
        self._llm_conn.execute("PRAGMA busy_timeout = 30000")
        self._llm_conn.execute("PRAGMA synchronous=NORMAL")
        self._llm_conn.execute("PRAGMA cache_size = -65536")

    def _init_discovery_connection(self) -> None:
        """Initialize the discovery database connection."""
        self._discovery_conn = sqlite3.connect(
            str(self._discovery_db_path),
            timeout=30.0,
            check_same_thread=False,
            factory=LockedConnection,
        )
        self._discovery_conn.row_factory = sqlite3.Row
        self._discovery_conn.execute("PRAGMA journal_mode=WAL")
        self._discovery_conn.execute("PRAGMA busy_timeout = 30000")
        self._discovery_conn.execute("PRAGMA synchronous=NORMAL")
        self._discovery_conn.execute("PRAGMA cache_size = -65536")
        # discovery.db 是 db sharding P5 独立子库：生产库迁移时已建好 discovery_candidates；
        # 全新环境（测试/首次启动）discovery.db 只是空文件，这里幂等补建该表，
        # 否则 initialize() 里 reset_stale_discovery_candidate_evaluations 的
        # UPDATE discovery_candidates 会报 no such table。
        existing = {
            str(row["name"])
            for row in self._discovery_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='discovery_candidates'"
            )
        }
        if "discovery_candidates" not in existing:
            ddl = self._extract_create_table_sql(_SCHEMA_SQL, "discovery_candidates")
            if ddl:
                self._discovery_conn.executescript(ddl)
            self._discovery_conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_status_seen "
                "ON discovery_candidates(status, last_seen_at, id)"
            )
            self._discovery_conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_source_status "
                "ON discovery_candidates(source_platform, status)"
            )
            self._discovery_conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_discovery_candidates_content_id "
                "ON discovery_candidates(source_platform, content_id)"
            )
            self._discovery_conn.commit()
            self._logger().warning(
                "discovery.db 不存在，已按主库完整 schema 创建 discovery_candidates 表。"
            )

    def _init_content_connection(self) -> None:
        """Initialize the content database connection."""
        self._content_conn = sqlite3.connect(
            str(self._content_db_path),
            timeout=30.0,
            check_same_thread=False,
            factory=LockedConnection,
        )
        self._content_conn.row_factory = sqlite3.Row
        self._content_conn.execute("PRAGMA journal_mode=WAL")
        self._content_conn.execute("PRAGMA busy_timeout = 30000")
        self._content_conn.execute("PRAGMA synchronous=NORMAL")
        self._content_conn.execute("PRAGMA cache_size = -65536")
        # content.db 是 P4 独立子库，全新环境（测试/首次启动）需要幂等创建所有 content 表
        # 注意：只创建真正属于 content.db 的表，其他表属于主库/pool.db/knowledge_audit.db
        content_tables = [
            "articles",
            "article_entities",
            "article_notes",
            "article_relations",
            "article_snapshots",
            "article_tldrs",
            "favorites",
            "watch_later",
        ]
        existing = {
            str(row["name"])
            for row in self._content_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table_name in content_tables:
            if table_name not in existing:
                ddl = self._extract_create_table_sql(_SCHEMA_SQL, table_name)
                if ddl:
                    self._content_conn.executescript(ddl)
        # 重新查询已存在的表（创建后可能新增）
        existing = {
            str(row["name"])
            for row in self._content_conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # 主要索引：用户行为表（仅在表存在时创建）
        if "favorites" in existing:
            self._content_conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_favorites_bvid ON favorites(bvid)")
        if "watch_later" in existing:
            self._content_conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_watch_later_bvid ON watch_later(bvid)")
        self._content_conn.commit()

    def _logger(self):
        import logging

        return logging.getLogger(__name__)

    def initialize(self) -> None:
        """Initialize the database and run migrations if needed."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False, factory=LockedConnection)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout = 30000")
        # WAL 模式下 NORMAL 已足够安全，写入性能比 FULL 提升明显
        self._conn.execute("PRAGMA synchronous=NORMAL")
        # 增加页面缓存到 64MB（负数表示 KB），减少磁盘 IO，提升查询性能
        self._conn.execute("PRAGMA cache_size = -65536")
        # 256MB 内存映射，减少 IO 开销
        self._conn.execute("PRAGMA mmap_size = 268435456")
        # 提升 WAL 检查点阈值，减少频繁检查点
        self._conn.execute("PRAGMA wal_autocheckpoint = 1000")
        # 推荐流子库：确保 pool.db 存在后 ATTACH，使无前缀 SQL 落到 pool schema
        self._ensure_pool_database()
        self._attach_pool(self._conn)
        # 推荐流子库 pool.db 一并启用 WAL，与主库一致，降低并发写锁冲突
        with suppress(sqlite3.OperationalError):
            self._conn.execute("PRAGMA pool.journal_mode=WAL")
        # 事件子库：确保 events.db 存在后 ATTACH，使 events.* 前缀落到事件 schema
        self._ensure_events_database()
        self._attach_events(self._conn)
        with suppress(sqlite3.OperationalError):
            self._conn.execute("PRAGMA events.journal_mode=WAL")
        # LLM 用量库：独立连接，极高频写入的 llm_usage 表与主库锁域隔离
        self._init_llm_connection()
        # Discovery 库：独立连接，搜索发现相关表与主库锁域隔离
        self._init_discovery_connection()
        # Content 库：独立连接，文章内容相关表与主库锁域隔离
        self._init_content_connection()
        # 主库连接也 ATTACH content.db，使跨库 JOIN（content_cache JOIN favorites）正常工作
        with suppress(sqlite3.OperationalError):
            self._conn.execute("ATTACH DATABASE ? AS content", (str(self._content_db_path),))
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
        # view_history 已随 db sharding 迁移至 events.db（_EVENTS_SCHEMA），主库不再建表
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

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        # Per-thread connection (see __init__). Lazily opened on first access
        # from any thread other than the initializing one; the initializing
        # thread reuses the primary connection bound in initialize().
        local_conn = getattr(self._thread_local, "conn", None)
        if local_conn is None:
            local_conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False, factory=LockedConnection)
            local_conn.row_factory = sqlite3.Row
            local_conn.execute("PRAGMA journal_mode=WAL")
            local_conn.execute("PRAGMA busy_timeout = 30000")
            local_conn.execute("PRAGMA synchronous=NORMAL")
            # 增加页面缓存到 64MB，减少磁盘 IO
            local_conn.execute("PRAGMA cache_size = -65536")
            local_conn.execute("PRAGMA mmap_size = 268435456")
            self._attach_pool(local_conn)
            self._attach_events(local_conn)
            self._thread_local.conn = local_conn
        return local_conn

    def open_connection(self) -> sqlite3.Connection:
        """Open a short-lived connection to the initialized database.

        Use this for explicit transactions that may run from FastAPI's
        threadpool. A separate connection lets SQLite serialize writers
        with ``busy_timeout`` instead of nesting transactions on the
        process-wide connection.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False, factory=LockedConnection)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA mmap_size = 268435456")
        self._attach_pool(conn)
        self._attach_events(conn)
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
