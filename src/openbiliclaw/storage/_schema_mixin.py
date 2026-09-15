"""Database mixin: schema initialization (columns + indexes).

Contains backfill methods for existing databases (_ensure_*_columns)
and read-optimization index creation (_ensure_*_read_indexes).
"""

from __future__ import annotations

from contextlib import suppress
from typing import Any


class SchemaMixin:
    """Database methods for schema initialization (columns + indexes)."""

    conn: Any

    def _ensure_llm_usage_cache_columns(self) -> None:
        """Backfill v0.3.28+ prompt-cache columns on existing llm_usage tables.

        v0.4.0+: 同时检查主库和 llm.db。
        """
        required_columns = {
            "cached_input_tokens": "INTEGER NOT NULL DEFAULT 0",
        }
        # 主库
        self._ensure_columns_on_connection(self.conn, "llm_usage", required_columns)
        # llm.db
        llm_conn = getattr(self, "_llm_conn", None)
        if llm_conn is not None:
            self._ensure_columns_on_connection(llm_conn, "llm_usage", required_columns)

    @staticmethod
    def _ensure_columns_on_connection(
        conn: Any,
        table_name: str,
        required_columns: dict[str, str],
        schema: str | None = None,
    ) -> None:
        """Ensure required columns exist on a table in the given connection.

        ``schema`` 用于经 ATTACH 访问的子库（如 events.db）：SQLite 的
        PRAGMA 约定是 ``PRAGMA <schema>.table_info(<表>)``（schema 在 pragma
        名前置、表名仍用裸名），不支持 ``table_info(events.events)`` 点分形式。
        """
        pragma = (
            f"PRAGMA {schema}.table_info({table_name})"
            if schema
            else f"PRAGMA table_info({table_name})"
        )
        qual = f"{schema}.{table_name}" if schema else table_name
        try:
            existing_columns = {str(row["name"]) for row in conn.execute(pragma).fetchall()}
        except Exception:
            return
        for column_name, column_type in required_columns.items():
            if column_name in existing_columns:
                continue
            with suppress(Exception):
                conn.execute(f"ALTER TABLE {qual} ADD COLUMN {column_name} {column_type}")

    def _ensure_event_satisfaction_columns(self) -> None:
        """Backfill v0.3.x event-satisfaction columns for pre-migration DBs.

        v0.4.x+: events.db 拆分后，行为事件主写 events.db（别名 events）。
        这里镜像到主库旧 events 表（双写验证期）与 events.db 两份，确保两处结构一致。
        """
        required_columns = {
            "inferred_satisfaction": "TEXT",
            "satisfaction_reason": "TEXT",
            "article_id": "INTEGER",
        }
        # 主库旧 events 表（双写验证期）
        self._ensure_columns_on_connection(self.conn, "events", required_columns)
        # events.db（P2 事件子库，别名 events，经 self.conn ATTACH 访问）
        self._ensure_columns_on_connection(self.conn, "events", required_columns, schema="events")

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
            for row in self._discovery.execute("PRAGMA table_info(discovery_candidates)").fetchall()
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
            self._discovery.execute(
                f"ALTER TABLE discovery_candidates ADD COLUMN {column_name} {column_type}"
            )

    def _normalize_legacy_style_keys(self) -> None:
        """Rewrite known legacy content-form style keys to viewing-mode keys."""
        from openbiliclaw.storage.database import _LEGACY_STYLE_KEY_MAP

        # content_cache 位于 pool.db（self.conn 默认 schema 落到 pool）；
        # discovery_candidates 位于 discovery.db（独立 _discovery 连接）。
        # 两条路径都要归一化，否则子库里的候选永远不会被收敛。
        self._rename_legacy_style_keys(self.conn, "content_cache", _LEGACY_STYLE_KEY_MAP)
        self._rename_legacy_style_keys(
            self._discovery, "discovery_candidates", _LEGACY_STYLE_KEY_MAP
        )

    @staticmethod
    def _rename_legacy_style_keys(conn: Any, table_name: str, style_map: dict[str, str]) -> None:
        existing_columns = {
            str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        }
        if "style_key" not in existing_columns:
            return
        for legacy_key, style_key in style_map.items():
            conn.execute(
                f"UPDATE {table_name} SET style_key = ? WHERE style_key = ?",
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
        """Create indexes for the high-volume ``events`` table (in events.db)."""
        import sqlite3

        index_defs = [
            ("events.idx_events_created_at", "created_at DESC"),
            ("events.idx_events_event_type", "event_type"),
        ]
        for idx, cols in index_defs:
            with suppress(sqlite3.Error):
                self.conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx} ON events ({cols})"
                )
        existing_cols = {
            row[1] for row in self.conn.execute("PRAGMA events.table_info(events)").fetchall()
        }
        if "source_platform" not in existing_cols:
            try:  # noqa: SIM105
                self.conn.execute(
                    "ALTER TABLE events.events ADD COLUMN source_platform TEXT "
                    "GENERATED ALWAYS AS "
                    "(COALESCE(json_extract(metadata, '$.source_platform'), 'unknown')) VIRTUAL"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS events.idx_events_source_platform ON events (source_platform)"
        )
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS events.idx_events_agg_stats ON events (event_type, source_platform, inferred_satisfaction)"
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
            try:  # noqa: SIM105
                self.conn.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} ON content_cache ({', '.join(cols)})"
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
        # v0.4.0+: watch_later 表迁移到 content.db
        content_conn = getattr(self, "_content_conn", None)
        if content_conn is not None:
            content_conn.executescript("""
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
        import logging
        from contextlib import suppress

        logger = logging.getLogger(__name__)

        # v0.4.0+: favorites/watch_later/articles 相关表全部迁移到 content.db
        content_conn = getattr(self, "_content_conn", None)
        if content_conn is not None:
            content_conn.executescript("""
                CREATE TABLE IF NOT EXISTS favorites (
                    bvid     TEXT PRIMARY KEY,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    note     TEXT DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_favorites_added
                    ON favorites(added_at DESC);
            """)
            content_conn.executescript("""
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
            for col, typ, default in [
                ("tags", "TEXT", "'[]'"),
                ("status", "TEXT", "'unread'"),
                ("body_fetch_attempts", "INTEGER", "0"),
                ("reading_percent", "REAL", "0"),
                ("reading_progress", "TEXT", "''"),
                ("favorited", "INTEGER", "0"),
                ("ai_summary", "TEXT", "''"),
                ("topic_group", "TEXT", "''"),
            ]:
                with suppress(Exception):
                    content_conn.execute(f"ALTER TABLE articles ADD COLUMN {col} {typ} DEFAULT {default}")

            try:
                if content_conn.execute("SELECT count(*) FROM articles_fts").fetchone()[0] == 0:
                    content_conn.execute("INSERT INTO articles_fts(articles_fts) VALUES('rebuild')")
                    content_conn.commit()
            except Exception:
                logger.exception("Failed to rebuild articles FTS")

        # read_archive 表保留在主库
        self.conn.executescript("""
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
        """)

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

    # ── Table creation (user_feedback / view_history / topic / knowledge_forge) ─────

    def _ensure_user_feedback_table(self) -> None:
        from openbiliclaw.storage.database import _USER_FEEDBACK_DDL

        self.conn.executescript(_USER_FEEDBACK_DDL)
        # view_history 已随 db sharding 迁移至 events.db（_EVENTS_SCHEMA），主库不再建表

    def _ensure_topic_tables(self) -> None:
        """Create the topic (专题) tables.

        A topic is a user-curated collection: a name + keyword set + source
        platforms whose matching content is continuously collected into
        ``topic_items``. Multiple topics can coexist (e.g. 广告, 去有风的地方).
        """
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge.topics (
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
            CREATE TABLE IF NOT EXISTS knowledge.topic_items (
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
            CREATE INDEX IF NOT EXISTS knowledge.idx_topic_items_topic
                ON topic_items(topic_id, collected_at DESC);
            CREATE INDEX IF NOT EXISTS knowledge.idx_topic_items_key
                ON topic_items(content_key);
        """)

    def _ensure_knowledge_forge_tables(self) -> None:
        """Create Knowledge Forge (知识锻造炉) columns and tables.

        Docs: docs/knowledge-forge-design.md. 渐进式迁移：articles 表只加列
        不删改，旧数据/旧字段完全保留；新表全部 CREATE IF NOT EXISTS，幂等。
        本方法在 Database.initialize() 中调用，每次启动自动补齐缺失结构。
        """
        # 1. articles 表新增正文清理器字段（3.0.5）+ 分层摘要字段（3.1.3）
        # v0.4.0+: articles 表迁移到 content.db
        content_conn = getattr(self, "_content_conn", None) or self.conn
        existing_columns = {
            str(row["name"]) for row in content_conn.execute("PRAGMA table_info(articles)").fetchall()
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
            content_conn.execute(f"ALTER TABLE articles ADD COLUMN {column_name} {column_type}")

        # 2. 实体表（3.2.2）
        # P8：entities/entity_relations 独立存于 knowledge.db，故用 knowledge. 前缀；
        # article_entities / article_relations 属内容域，真实数据存于 content.db，
        # 用 content. 前缀建表/索引（主库连接 ATTACH content），避免裸名在主库重建空表
        # 挡住 ATTACH 子库真实数据的裸名解析（P9 收尾发现）。
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS knowledge.entities (
                id INTEGER PRIMARY KEY,
                name TEXT UNIQUE,
                type TEXT,
                description TEXT,
                article_count INTEGER DEFAULT 0,
                first_seen_at TEXT,
                last_updated_at TEXT,
                metadata TEXT
            );
            CREATE INDEX IF NOT EXISTS knowledge.idx_entities_type ON entities(type);
            CREATE INDEX IF NOT EXISTS knowledge.idx_entities_name ON entities(name);

            -- 文章-实体关联
            CREATE TABLE IF NOT EXISTS content.article_entities (
                article_id INTEGER,
                entity_id INTEGER,
                relevance REAL,
                context TEXT,
                PRIMARY KEY (article_id, entity_id),
                FOREIGN KEY (article_id) REFERENCES articles(id),
                FOREIGN KEY (entity_id) REFERENCES entities(id)
            );
            CREATE INDEX IF NOT EXISTS content.idx_article_entities_article ON article_entities(article_id);
            CREATE INDEX IF NOT EXISTS content.idx_article_entities_entity ON article_entities(entity_id);

            -- 实体间关联
            CREATE TABLE IF NOT EXISTS knowledge.entity_relations (
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
            for row in self.conn.execute("PRAGMA knowledge.table_info(entity_relations)").fetchall()
        }
        if "co_occur" not in _er_columns:
            self.conn.execute("ALTER TABLE knowledge.entity_relations ADD COLUMN co_occur INTEGER DEFAULT 1")

        # 3. 文章间关联（3.3.2）
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS content.article_relations (
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
            CREATE INDEX IF NOT EXISTS content.idx_article_relations_a ON article_relations(article_id_a);
            CREATE INDEX IF NOT EXISTS content.idx_article_relations_b ON article_relations(article_id_b);
            CREATE INDEX IF NOT EXISTS content.idx_article_relations_type ON article_relations(relation_type);
        """)

        # 4. 质量审计表（3.5.3）
        # audit_tasks / audit_issues / article_quality_scores / audit_config 已随
        # db sharding 迁至 knowledge_audit.db（quality_auditor 初始化时幂等建 audit_config），
        # 主库不再建任何知识审计表。

        # 5. 知识缺口分析表（3.4.4）
        # gap_analysis_tasks / gap_records 已随 db sharding 迁至 knowledge_audit.db，主库不再建。

    # ------------------------------------------------------------------ #

    # ── Table creation (saved_sync) ─────────────────────────────

    def _ensure_saved_sync_tables(self) -> None:
        """Create the saved-sync (reading library) tables.

        Cross-platform unified saved-item management:
        - saved_items: normalized metadata for each item (shared across lists)
        - saved_memberships: membership in favorite/watch_later lists
        - native_save_states: native-sync (to platform) execution state
        - native_save_task_items: per-item membership within a sync batch
        - saved_item_removals: history of removed items for retention
        """
        from openbiliclaw.storage._saved_sync_vocab import NATIVE_SAVE_STATUSES

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

            CREATE TABLE IF NOT EXISTS native_save_tasks (
                task_id TEXT PRIMARY KEY,
                runner_id TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'pending'
                    CHECK (state IN ('pending', 'running', 'released')),
                heartbeat_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_native_save_tasks_state
                ON native_save_tasks(state, heartbeat_at);
        """)
