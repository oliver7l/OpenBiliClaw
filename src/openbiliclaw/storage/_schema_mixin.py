"""Database mixin: schema initialization (columns + indexes).

Contains backfill methods for existing databases (_ensure_*_columns)
and read-optimization index creation (_ensure_*_read_indexes).
"""

from __future__ import annotations

from typing import Any


class SchemaMixin:
    """Database methods for schema initialization (columns + indexes)."""

    conn: Any

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
        """Backfill v0.3.x event-satisfaction columns for pre-migration DBs."""
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
        """Backfill the recommendation click-through column for existing DBs."""
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
        from openbiliclaw.storage.database import _LEGACY_STYLE_KEY_MAP

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
        self.conn.executescript("""
            CREATE INDEX IF NOT EXISTS pool.idx_recommendations_created_id ON recommendations (created_at DESC, id DESC);
            CREATE INDEX IF NOT EXISTS pool.idx_recommendations_bvid ON recommendations (bvid);
        """)
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
        """Create indexes for the high-volume ``events`` table."""
        import sqlite3

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
                if "duplicate column" not in str(exc).lower():
                    raise
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_source_platform ON events (source_platform)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_agg_stats ON events (event_type, source_platform, inferred_satisfaction)"
        )

    def _ensure_content_cache_read_indexes(self) -> None:
        """Create indexes for content_cache columns used in observability / read queries."""
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
            ("pool.idx_content_cache_source", ["source"]),
            ("pool.idx_content_cache_topic_group", ["topic_group"]),
            ("pool.idx_content_cache_feedback_type", ["feedback_type"]),
            ("pool.idx_content_cache_style_key", ["style_key"]),
        ]
        for idx_name, cols in index_defs:
            if not all(c in pool_cols for c in cols):
                continue
            try:
                self.conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON pool.content_cache ({', '.join(cols)})"
                )
            except Exception:  # noqa: BLE001 — 单个索引失败不阻断启动
                pass

    # ── Table creation (small tables) ─────────────────────────────

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
        from openbiliclaw.storage.database import _XHS_OBSERVED_URLS_DDL

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

    # ── Table creation (favorites / articles / read_archive) ─────

    def _ensure_favorites_table(self) -> None:
        """Create the favorites (收藏夹) table for existing databases."""
        from contextlib import suppress
        import logging

        logger = logging.getLogger(__name__)
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

        try:
            if self.conn.execute("SELECT count(*) FROM articles_fts").fetchone()[0] == 0:
                self.conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
                self.conn.commit()
        except Exception:
            logger.exception("Failed to rebuild articles FTS")

    def _ensure_auth_state_table(self) -> None:
        """Create the auth_state key/value table."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS auth_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def _ensure_init_runs_table(self) -> None:
        """Create the init_runs table backing guided (GUI) initialization."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS init_runs (
                run_id          TEXT PRIMARY KEY,
                status          TEXT NOT NULL,
                stage           INTEGER NOT NULL DEFAULT 0,
                stages_json     TEXT,
                partial_success INTEGER NOT NULL DEFAULT 0,
                error_reason    TEXT,
                sequence        INTEGER NOT NULL DEFAULT 0,
                started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                finished_at     TIMESTAMP
            );
        """)
