"""Inbox 子库管理

每个 platform producer 写入独立的 inbox 子库，避免并发写总库导致锁定。
合并器定期将 inbox 数据合并到 pool.db。

子库位置: data/inbox/<platform>.db
子库表: content_cache（与 pool.db 同 schema）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# content_cache 完整 DDL（与 pool.db 保持一致）
_INBOX_CONTENT_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS content_cache (
    bvid        TEXT PRIMARY KEY,
    title       TEXT,
    up_name     TEXT,
    up_mid      INTEGER,
    duration    INTEGER,
    tags        TEXT,
    topic_key   TEXT DEFAULT '',
    style_key   TEXT DEFAULT '',
    franchise_key TEXT DEFAULT '',
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
    source      TEXT,
    body_text   TEXT DEFAULT '',
    content_type TEXT DEFAULT 'video',
    source_keyword_id INTEGER,
    topic_group TEXT DEFAULT '',
    delight_score REAL DEFAULT 0.0,
    delight_reason TEXT DEFAULT '',
    delight_hook TEXT DEFAULT '',
    delight_notified INTEGER DEFAULT 0,
    delight_notified_at TIMESTAMP,
    content_id TEXT DEFAULT '',
    content_url TEXT DEFAULT '',
    source_platform TEXT DEFAULT 'bilibili',
    author_name TEXT DEFAULT '',
    quality_score REAL DEFAULT 0.0,
    quality_reason TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_inbox_pool_status ON content_cache (pool_status);
CREATE INDEX IF NOT EXISTS idx_inbox_source_platform ON content_cache (source_platform);
"""


def get_inbox_dir(data_dir: str | Path = "data") -> Path:
    """获取 inbox 子库目录，不存在则创建。"""
    inbox_dir = Path(data_dir) / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    return inbox_dir


def get_inbox_path(platform: str, data_dir: str | Path = "data") -> Path:
    """获取指定 platform 的 inbox 子库路径。"""
    return get_inbox_dir(data_dir) / f"{platform}.db"


def connect_inbox(platform: str, data_dir: str | Path = "data") -> sqlite3.Connection:
    """连接（或创建）指定 platform 的 inbox 子库。

    自动创建 content_cache 表和索引。
    返回的连接已启用 WAL 模式和 busy_timeout。
    """
    db_path = get_inbox_path(platform, data_dir)
    conn = sqlite3.connect(str(db_path), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_INBOX_CONTENT_CACHE_DDL)
    return conn


def list_inbox_platforms(data_dir: str | Path = "data") -> list[str]:
    """列出所有存在的 inbox 子库 platform 名称。"""
    inbox_dir = get_inbox_dir(data_dir)
    if not inbox_dir.exists():
        return []
    return [p.stem for p in inbox_dir.glob("*.db") if p.is_file()]
