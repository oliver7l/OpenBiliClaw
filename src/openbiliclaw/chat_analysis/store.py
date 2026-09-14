"""聊天记录分析存储层：SQLite 数据持久化。

独立管理聊天记录相关数据表，提供 CRUD、检索、统计等底层操作。
表结构与项目主数据库共存，通过独立的 store 类访问。
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any

from openbiliclaw.storage.database import open_db_conn

if TYPE_CHECKING:
    from pathlib import Path

    from ..storage.database import Database

from .models import (
    ChatAnalysisChunk,
    ChatAnalysisChunkCreate,
    ChatEmbedding,
    ChatInsight,
    ChatMessage,
    ChatMessageCreate,
    ChatSearchResult,
    ChatSession,
    ChatSessionCreate,
    ChatSessionUpdate,
    ChatTag,
    ChatTopic,
    ChatType,
    MessageType,
)

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    source_file TEXT DEFAULT '',
    time_range TEXT DEFAULT '最早 ~ 最新',
    export_time TEXT DEFAULT '',
    message_count INTEGER DEFAULT 0,
    chat_type TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    file_size INTEGER DEFAULT 0,
    analyzed INTEGER DEFAULT 0,
    last_analyzed_at TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_title ON chat_sessions(title);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_type ON chat_sessions(chat_type);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_count ON chat_sessions(message_count DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    timestamp TEXT DEFAULT '',
    sender TEXT DEFAULT '',
    content TEXT DEFAULT '',
    message_type TEXT DEFAULT 'text',
    sender_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_sender ON chat_messages(sender);
CREATE INDEX IF NOT EXISTS idx_chat_messages_ts ON chat_messages(timestamp);

CREATE TABLE IF NOT EXISTS chat_analysis_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_title TEXT NOT NULL,
    start_line INTEGER DEFAULT 0,
    end_line INTEGER DEFAULT 0,
    analysis_content TEXT DEFAULT '',
    analysis_file TEXT DEFAULT '',
    model_used TEXT DEFAULT 'deepseek',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_analysis_session ON chat_analysis_chunks(session_title);

CREATE TABLE IF NOT EXISTS chat_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT DEFAULT 'other',
    mention_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_chat_tags_category ON chat_tags(category);
CREATE INDEX IF NOT EXISTS idx_chat_tags_count ON chat_tags(mention_count DESC);

CREATE TABLE IF NOT EXISTS chat_session_tags (
    session_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (session_id, tag_id),
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES chat_tags(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_session_tags_tag ON chat_session_tags(tag_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chat_messages_fts USING fts5(
    content,
    content=chat_messages,
    content_rowid=id,
    tokenize=trigram
);

-- 触发器：插入消息时同步更新 FTS
CREATE TRIGGER IF NOT EXISTS chat_messages_fts_insert AFTER INSERT ON chat_messages
BEGIN
    INSERT INTO chat_messages_fts(rowid, content) VALUES (new.id, new.content);
END;

-- 触发器：删除消息时同步删除 FTS
CREATE TRIGGER IF NOT EXISTS chat_messages_fts_delete AFTER DELETE ON chat_messages
BEGIN
    DELETE FROM chat_messages_fts WHERE rowid = old.id;
END;

-- 新增话题、洞见、嵌入表
CREATE TABLE IF NOT EXISTS chat_topics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    topic_name TEXT NOT NULL,
    keywords TEXT NOT NULL,
    start_time TEXT DEFAULT '',
    end_time TEXT DEFAULT '',
    participant_count INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    summary TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_topics_session ON chat_topics(session_id);

CREATE TABLE IF NOT EXISTS chat_insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    insight_type TEXT NOT NULL,
    content TEXT NOT NULL,
    evidence_messages TEXT DEFAULT '[]',
    confidence REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_insights_session ON chat_insights(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_insights_type ON chat_insights(insight_type);

CREATE TABLE IF NOT EXISTS chat_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    session_id INTEGER NOT NULL,
    vector BLOB NOT NULL,
    dimension INTEGER NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (message_id) REFERENCES chat_messages(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chat_embeddings_message ON chat_embeddings(message_id);
CREATE INDEX IF NOT EXISTS idx_chat_embeddings_session ON chat_embeddings(session_id);
"""


class ChatAnalysisStore:
    """聊天记录分析存储层。

    管理聊天会话、消息、分析片段等数据表的 CRUD 操作。
    """

    def __init__(
        self,
        database: Database | None = None,
        db_path: Path | None = None,
    ):
        self._database = database
        self._db_path = db_path
        self._local = threading.local()
        self._initialized = False

    @property
    def conn(self) -> sqlite3.Connection:
        if self._database:
            conn = self._database.conn
            if not self._initialized:
                self._initialize_tables(conn)
                self._initialized = True
            return conn
        conn = getattr(self._local, "connection", None)
        if conn is None:
            conn = open_db_conn(str(self._db_path), isolation_level=None)
            conn.execute("PRAGMA foreign_keys=ON")
            self._local.connection = conn
        if not self._initialized:
            self._initialize_tables(conn)
            self._initialized = True
        return conn

    def _initialize_tables(self, conn: sqlite3.Connection | None = None) -> None:
        c = conn or self.conn
        c.executescript(_SCHEMA_SQL)
        # Migration: add analyzed columns if missing (existing databases)
        for col in ("analyzed", "last_analyzed_at"):
            try:  # noqa: SIM105
                c.execute(
                    f"ALTER TABLE chat_sessions ADD COLUMN {col} TEXT DEFAULT ''"
                    if col == "last_analyzed_at"
                    else f"ALTER TABLE chat_sessions ADD COLUMN {col} INTEGER DEFAULT 0"
                )
            except sqlite3.OperationalError:
                pass  # column already exists
        c.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    # ── 会话 CRUD ──

    def create_session(self, data: ChatSessionCreate) -> ChatSession:
        c = self.conn
        now = datetime.now().isoformat()
        cur = c.execute(
            """INSERT INTO chat_sessions
               (title, source_file, time_range, export_time,
                message_count, chat_type, file_path, file_size,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.title,
                data.source_file or "",
                data.time_range,
                data.export_time or "",
                data.message_count,
                data.chat_type.value if data.chat_type else "",
                data.file_path or "",
                data.file_size or 0,
                now,
                now,
            ),
        )
        return self.get_session(cur.lastrowid)

    def get_session(self, session_id: int) -> ChatSession | None:
        row = self.conn.execute(
            "SELECT * FROM chat_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return self._row_to_session(row) if row else None

    def get_session_by_title(self, title: str) -> ChatSession | None:
        row = self.conn.execute("SELECT * FROM chat_sessions WHERE title = ?", (title,)).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(
        self,
        offset: int = 0,
        limit: int = 50,
        chat_type: str | None = None,
        sort_by: str = "message_count",
        sort_desc: bool = True,
    ) -> list[ChatSession]:
        where = "WHERE chat_type = ?" if chat_type else ""
        params: tuple = (chat_type,) if chat_type else ()
        order = "DESC" if sort_desc else "ASC"
        allowed_sort = {"message_count", "title", "created_at", "updated_at"}
        sort_col = sort_by if sort_by in allowed_sort else "message_count"
        rows = self.conn.execute(
            f"SELECT * FROM chat_sessions {where} ORDER BY {sort_col} {order} LIMIT ? OFFSET ?",
            params + (limit, offset),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def get_unanalyzed_sessions(self, limit: int = 30) -> list[ChatSession]:
        """获取未分析过的会话，按消息数降序排列。"""
        rows = self.conn.execute(
            "SELECT * FROM chat_sessions WHERE analyzed = 0 ORDER BY message_count DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def mark_session_analyzed(self, session_id: int) -> None:
        """标记会话为已分析。"""
        now = datetime.now().isoformat()
        self.conn.execute(
            "UPDATE chat_sessions SET analyzed = 1, last_analyzed_at = ?, updated_at = ? WHERE id = ?",
            (now, now, session_id),
        )

    def count_sessions(self, chat_type: str | None = None) -> int:
        where = "WHERE chat_type = ?" if chat_type else ""
        params: tuple = (chat_type,) if chat_type else ()
        row = self.conn.execute(f"SELECT COUNT(*) FROM chat_sessions {where}", params).fetchone()
        return row[0]

    def update_session(self, session_id: int, data: ChatSessionUpdate) -> ChatSession | None:
        updates: list[str] = []
        params: list[Any] = []
        for field in (
            "title",
            "source_file",
            "time_range",
            "export_time",
            "message_count",
            "chat_type",
            "analyzed",
            "last_analyzed_at",
        ):
            val = getattr(data, field, None)
            if val is not None:
                updates.append(f"{field} = ?")
                params.append(
                    val.value
                    if field == "chat_type" and isinstance(val, ChatType)
                    else int(val)
                    if field == "analyzed" and isinstance(val, bool)
                    else val
                )
        if not updates:
            return self.get_session(session_id)
        updates.append("updated_at = ?")
        params.append(datetime.now().isoformat())
        params.append(session_id)
        self.conn.execute(f"UPDATE chat_sessions SET {', '.join(updates)} WHERE id = ?", params)
        return self.get_session(session_id)

    def delete_session(self, session_id: int) -> bool:
        c = self.conn
        c.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        c.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
        return c.total_changes > 0

    # ── 消息 CRUD ──

    def create_message(self, data: ChatMessageCreate) -> ChatMessage:
        c = self.conn
        now = datetime.now().isoformat()
        cur = c.execute(
            """INSERT INTO chat_messages
               (session_id, timestamp, sender, content, message_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data.session_id,
                data.timestamp or "",
                data.sender,
                data.content,
                data.message_type.value,
                now,
            ),
        )
        return self._row_to_message(
            c.execute("SELECT * FROM chat_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
        )

    def create_messages_batch(self, messages: list[ChatMessageCreate]) -> int:
        c = self.conn
        now = datetime.now().isoformat()
        rows = [
            (
                m.session_id,
                m.timestamp or "",
                m.sender,
                m.content,
                m.message_type.value,
                now,
            )
            for m in messages
        ]
        c.executemany(
            """INSERT INTO chat_messages
               (session_id, timestamp, sender, content, message_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            rows,
        )
        return len(rows)

    def get_messages(
        self,
        session_id: int,
        offset: int = 0,
        limit: int = 100,
        sender: str | None = None,
        message_type: str | None = None,
    ) -> list[ChatMessage]:
        conditions = ["session_id = ?"]
        params: list[Any] = [session_id]
        if sender:
            conditions.append("sender = ?")
            params.append(sender)
        if message_type:
            conditions.append("message_type = ?")
            params.append(message_type)
        rows = self.conn.execute(
            f"SELECT * FROM chat_messages WHERE {' AND '.join(conditions)} ORDER BY timestamp ASC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    def count_messages(self, session_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return row[0]

    def rebuild_fts_index(self) -> int:
        """重建 FTS5 全文索引。"""
        self.conn.execute("INSERT INTO chat_messages_fts(chat_messages_fts) VALUES('rebuild')")
        row = self.conn.execute("SELECT COUNT(*) FROM chat_messages_fts").fetchone()
        return row[0]

    def search_messages(
        self,
        query: str,
        session_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatSearchResult], int]:
        """FTS5 BM25 全文搜索。

        使用 FTS5 trigram 分词 + BM25 相关度排序。
        如果 FTS 表为空，回退到 LIKE 搜索。
        """
        fts_count = self.conn.execute("SELECT COUNT(*) FROM chat_messages_fts").fetchone()[0]

        if fts_count == 0:
            return self._search_like_fallback(query, session_id, limit, offset)

        conditions = ["chat_messages_fts MATCH ?"]
        params: list[Any] = [query]
        if session_id is not None:
            conditions.append("cm.session_id = ?")
            params.append(session_id)
        where = " AND ".join(conditions)

        count_row = self.conn.execute(
            f"SELECT COUNT(*) FROM chat_messages_fts fts JOIN chat_messages cm ON fts.rowid = cm.id WHERE {where}",
            params,
        ).fetchone()
        total = count_row[0]

        rows = self.conn.execute(
            f"""SELECT cm.id, cm.session_id, cs.title as session_title,
                      cm.sender, cm.timestamp, cm.content,
                      bm25(chat_messages_fts) as score
                FROM chat_messages_fts fts
                JOIN chat_messages cm ON fts.rowid = cm.id
                JOIN chat_sessions cs ON cm.session_id = cs.id
                WHERE {where}
                ORDER BY score ASC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        results = [
            ChatSearchResult(
                message_id=r["id"],
                session_id=r["session_id"],
                session_title=r["session_title"],
                sender=r["sender"],
                timestamp=r["timestamp"],
                content=r["content"],
                # BM25 负数 = 更相关，反转为正数
                rank_score=round(-r["score"], 4),
            )
            for r in rows
        ]
        return results, total

    def _search_like_fallback(
        self,
        query: str,
        session_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[ChatSearchResult], int]:
        """FTS 为空时的 LIKE 回退搜索。"""
        like = f"%{query}%"
        conditions = ["cm.content LIKE ?"]
        params: list[Any] = [like]
        if session_id is not None:
            conditions.append("cm.session_id = ?")
            params.append(session_id)
        where = " AND ".join(conditions)
        count_row = self.conn.execute(
            f"SELECT COUNT(*) FROM chat_messages cm WHERE {where}",
            params,
        ).fetchone()
        total = count_row[0]
        rows = self.conn.execute(
            f"""SELECT cm.id, cm.session_id, cs.title as session_title,
                      cm.sender, cm.timestamp, cm.content
                FROM chat_messages cm
                JOIN chat_sessions cs ON cm.session_id = cs.id
                WHERE {where}
                ORDER BY cm.timestamp ASC
                LIMIT ? OFFSET ?""",
            params + [limit, offset],
        ).fetchall()
        return [
            ChatSearchResult(
                message_id=r["id"],
                session_id=r["session_id"],
                session_title=r["session_title"],
                sender=r["sender"],
                timestamp=r["timestamp"],
                content=r["content"],
                rank_score=1.0,
            )
            for r in rows
        ], total

    # ── 分析片段 CRUD ──

    def create_analysis_chunk(self, data: ChatAnalysisChunkCreate) -> ChatAnalysisChunk:
        c = self.conn
        cur = c.execute(
            """INSERT INTO chat_analysis_chunks
               (session_title, start_line, end_line, analysis_content, analysis_file, model_used)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                data.session_title,
                data.start_line,
                data.end_line,
                data.analysis_content,
                data.analysis_file,
                data.model_used,
            ),
        )
        return self._row_to_chunk(
            c.execute(
                "SELECT * FROM chat_analysis_chunks WHERE id = ?", (cur.lastrowid,)
            ).fetchone()
        )

    def get_analysis_chunks(
        self,
        session_title: str | None = None,
        offset: int = 0,
        limit: int = 50,
    ) -> list[ChatAnalysisChunk]:
        if session_title:
            rows = self.conn.execute(
                "SELECT * FROM chat_analysis_chunks WHERE session_title = ? ORDER BY start_line ASC LIMIT ? OFFSET ?",
                (session_title, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM chat_analysis_chunks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def count_analysis_chunks(self, session_title: str | None = None) -> int:
        if session_title:
            row = self.conn.execute(
                "SELECT COUNT(*) FROM chat_analysis_chunks WHERE session_title = ?",
                (session_title,),
            ).fetchone()
        else:
            row = self.conn.execute("SELECT COUNT(*) FROM chat_analysis_chunks").fetchone()
        return row[0]

    def get_analysis_distinct_sessions(self) -> list[tuple[str, int]]:
        rows = self.conn.execute(
            "SELECT session_title, COUNT(*) FROM chat_analysis_chunks GROUP BY session_title ORDER BY COUNT(*) DESC"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    # ── 标签 CRUD ──

    def get_or_create_tag(self, name: str, category: str = "other") -> ChatTag:
        existing = self.conn.execute("SELECT * FROM chat_tags WHERE name = ?", (name,)).fetchone()
        if existing:
            return self._row_to_tag(existing)
        c = self.conn
        c.execute("INSERT INTO chat_tags (name, category) VALUES (?, ?)", (name, category))
        return self._row_to_tag(
            c.execute("SELECT * FROM chat_tags WHERE name = ?", (name,)).fetchone()
        )

    def list_tags(self, category: str | None = None) -> list[ChatTag]:
        if category:
            rows = self.conn.execute(
                "SELECT * FROM chat_tags WHERE category = ? ORDER BY mention_count DESC",
                (category,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM chat_tags ORDER BY mention_count DESC"
            ).fetchall()
        return [self._row_to_tag(r) for r in rows]

    # ── 话题 CRUD ──

    def create_topic(
        self,
        session_id: int,
        topic_name: str,
        keywords: list[str] | None = None,
        summary: str = "",
        participant_count: int = 0,
        message_count: int = 0,
        start_time: str = "",
        end_time: str = "",
    ) -> ChatTopic:
        c = self.conn
        now = datetime.now().isoformat()
        cur = c.execute(
            """INSERT INTO chat_topics
               (session_id, topic_name, keywords, start_time, end_time,
                participant_count, message_count, summary, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                topic_name,
                ",".join(keywords or []),
                start_time,
                end_time,
                participant_count,
                message_count,
                summary,
                now,
            ),
        )
        return self._row_to_topic(
            c.execute("SELECT * FROM chat_topics WHERE id = ?", (cur.lastrowid,)).fetchone()
        )

    def get_topics(self, session_id: int | None = None, limit: int = 50) -> list[ChatTopic]:
        if session_id:
            rows = self.conn.execute(
                "SELECT * FROM chat_topics WHERE session_id = ? ORDER BY message_count DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM chat_topics ORDER BY message_count DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_topic(r) for r in rows]

    def count_topics(self, session_id: int | None = None) -> int:
        if session_id:
            return self.conn.execute(
                "SELECT COUNT(*) FROM chat_topics WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM chat_topics").fetchone()[0]

    # ── 洞见 CRUD ──

    def create_insight(
        self,
        session_id: int,
        insight_type: str,
        content: str,
        evidence_messages: list[int] | None = None,
        confidence: float = 0.0,
    ) -> ChatInsight:
        c = self.conn
        now = datetime.now().isoformat()
        import json

        ev = json.dumps(evidence_messages or [])
        cur = c.execute(
            """INSERT INTO chat_insights
               (session_id, insight_type, content, evidence_messages, confidence, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (session_id, insight_type, content, ev, confidence, now),
        )
        return self._row_to_insight(
            c.execute("SELECT * FROM chat_insights WHERE id = ?", (cur.lastrowid,)).fetchone()
        )

    def get_insights(
        self, session_id: int | None = None, insight_type: str | None = None, limit: int = 50
    ) -> list[ChatInsight]:
        conditions: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            conditions.append("session_id = ?")
            params.append(session_id)
        if insight_type is not None:
            conditions.append("insight_type = ?")
            params.append(insight_type)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        rows = self.conn.execute(
            f"SELECT * FROM chat_insights {where} ORDER BY confidence DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [self._row_to_insight(r) for r in rows]

    def count_insights(self, session_id: int | None = None) -> int:
        if session_id:
            return self.conn.execute(
                "SELECT COUNT(*) FROM chat_insights WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM chat_insights").fetchone()[0]

    # ── 嵌入向量 CRUD ──

    def create_embedding(
        self,
        message_id: int,
        session_id: int,
        vector: list[float],
        model: str = "sentence-transformers",
    ) -> ChatEmbedding:
        import struct

        c = self.conn
        now = datetime.now().isoformat()
        blob = struct.pack(f"{len(vector)}f", *vector)
        cur = c.execute(
            """INSERT INTO chat_embeddings
               (message_id, session_id, vector, dimension, model, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (message_id, session_id, blob, len(vector), model, now),
        )
        return ChatEmbedding(
            id=cur.lastrowid,
            message_id=message_id,
            session_id=session_id,
            vector=vector,
            dimension=len(vector),
            model=model,
            created_at=datetime.now(),
        )

    def get_embeddings(
        self, session_id: int | None = None, limit: int = 100
    ) -> list[ChatEmbedding]:
        import struct

        if session_id:
            rows = self.conn.execute(
                "SELECT * FROM chat_embeddings WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM chat_embeddings ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        results = []
        for r in rows:
            dim = r["dimension"]
            vec = list(struct.unpack(f"{dim}f", r["vector"]))
            results.append(
                ChatEmbedding(
                    id=r["id"],
                    message_id=r["message_id"],
                    session_id=r["session_id"],
                    vector=vec,
                    dimension=dim,
                    model=r["model"],
                    created_at=r["created_at"],
                )
            )
        return results

    def search_similar_messages(
        self, query_vector: list[float], session_id: int | None = None, limit: int = 20
    ) -> list[tuple[ChatSearchResult, float]]:
        """向量相似度搜索（余弦相似度）。"""
        import math

        embeddings = self.get_embeddings(session_id=session_id, limit=5000)
        if not embeddings:
            return []

        q_norm = math.sqrt(sum(v * v for v in query_vector))
        if q_norm == 0:
            return []

        scored: list[tuple[int, float]] = []
        for emb in embeddings:
            v_norm = math.sqrt(sum(v * v for v in emb.vector))
            if v_norm == 0:
                continue
            dot = sum(a * b for a, b in zip(query_vector, emb.vector, strict=False))
            sim = dot / (q_norm * v_norm)
            scored.append((emb.message_id, sim))

        scored.sort(key=lambda x: -x[1])
        top = scored[:limit]

        results: list[tuple[ChatSearchResult, float]] = []
        for msg_id, sim in top:
            row = self.conn.execute(
                """SELECT cm.id, cm.session_id, cs.title as session_title,
                cm.sender, cm.timestamp, cm.content
                FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id
                WHERE cm.id = ?""",
                (msg_id,),
            ).fetchone()
            if row:
                results.append(
                    (
                        ChatSearchResult(
                            message_id=row["id"],
                            session_id=row["session_id"],
                            session_title=row["session_title"],
                            sender=row["sender"],
                            timestamp=row["timestamp"],
                            content=row["content"],
                            rank_score=round(sim, 4),
                        ),
                        sim,
                    )
                )
        return results

    def count_embeddings(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM chat_embeddings").fetchone()[0]

    # ── 统计 ──

    def get_session_stats(self, session_id: int) -> dict[str, Any]:
        c = self.conn
        session = self.get_session(session_id)
        if not session:
            return {}
        sender_rows = c.execute(
            """SELECT sender, COUNT(*) as msg_count,
                      LENGTH(COALESCE(content, '')) as total_chars
               FROM chat_messages
               WHERE session_id = ?
               GROUP BY sender
               ORDER BY msg_count DESC""",
            (session_id,),
        ).fetchall()
        sender_stats = []
        for sr in sender_rows:
            sender_stats.append(
                {
                    "sender": sr["sender"],
                    "message_count": sr["msg_count"],
                    "total_chars": sr["total_chars"],
                }
            )
        type_rows = c.execute(
            "SELECT message_type, COUNT(*) FROM chat_messages WHERE session_id = ? GROUP BY message_type",
            (session_id,),
        ).fetchall()
        type_dist = {r[0]: r[1] for r in type_rows}
        time_range_row = c.execute(
            "SELECT MIN(timestamp), MAX(timestamp) FROM chat_messages WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        analysis_count = c.execute(
            "SELECT COUNT(*) FROM chat_analysis_chunks WHERE session_title = ?",
            (session.title,),
        ).fetchone()[0]
        return {
            "session_id": session_id,
            "session_title": session.title,
            "total_messages": session.message_count,
            "distinct_senders": len(sender_stats),
            "sender_stats": sender_stats,
            "message_types": type_dist,
            "time_range": (time_range_row[0], time_range_row[1])
            if time_range_row
            else (None, None),
            "analysis_count": analysis_count,
        }

    def get_global_stats(self) -> dict[str, Any]:
        c = self.conn
        session_count = c.execute("SELECT COUNT(*) FROM chat_sessions").fetchone()[0]
        msg_count = c.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        analysis_count = c.execute("SELECT COUNT(*) FROM chat_analysis_chunks").fetchone()[0]
        total_chars = (
            c.execute("SELECT SUM(LENGTH(COALESCE(content, ''))) FROM chat_messages").fetchone()[0]
            or 0
        )
        type_counts = c.execute(
            "SELECT chat_type, COUNT(*) FROM chat_sessions WHERE chat_type != '' GROUP BY chat_type"
        ).fetchall()
        return {
            "total_sessions": session_count,
            "total_messages": msg_count,
            "total_chars": total_chars,
            "total_analysis_chunks": analysis_count,
            "total_topics": self.count_topics(),
            "total_insights": self.count_insights(),
            "total_embeddings": self.count_embeddings(),
            "session_types": {r[0]: r[1] for r in type_counts},
        }

    def get_top_senders(self, session_id: int, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT sender, COUNT(*) as msg_count,
                      LENGTH(COALESCE(content, '')) as total_chars,
                      MIN(timestamp) as first_msg,
                      MAX(timestamp) as last_msg
               FROM chat_messages
               WHERE session_id = ?
               GROUP BY sender
               ORDER BY msg_count DESC
               LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        return [
            {
                "sender": r["sender"],
                "message_count": r["msg_count"],
                "total_chars": r["total_chars"],
                "first_message_time": r["first_msg"],
                "last_message_time": r["last_msg"],
            }
            for r in rows
        ]

    # ── 行转对象 ──

    def _row_to_session(self, row: sqlite3.Row) -> ChatSession:
        chat_type = row["chat_type"]
        ct = None
        if chat_type == "group":
            ct = ChatType.GROUP
        elif chat_type == "private":
            ct = ChatType.PRIVATE
        return ChatSession(
            id=row["id"],
            title=row["title"],
            source_file=row["source_file"] or None,
            time_range=row["time_range"],
            export_time=row["export_time"] or None,
            message_count=row["message_count"],
            chat_type=ct,
            file_path=row["file_path"] or None,
            file_size=row["file_size"],
            # 2026-09-15 修复：``analyzed`` / ``last_analyzed_at`` 原先**完全没被映射**，
            # 于是 get_session() / list_sessions() / 各 API 永远报告 ``analyzed=False``
            # ——已分析的会话在界面上显示为未分析。列是迁移时 ALTER 加的，可能不存在
            # 于极旧的库，故按列名取值而非下标，并做缺省处理。
            analyzed=bool(self._row_value(row, "analyzed", 0)),
            last_analyzed_at=(self._row_value(row, "last_analyzed_at", "") or None),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _row_value(row: sqlite3.Row, key: str, default):  # noqa: ANN001, ANN205
        """按列名取值，列不存在（旧库缺迁移列）时返回默认值。"""
        try:
            value = row[key]
        except (IndexError, KeyError):
            return default
        return default if value is None else value

    def _row_to_message(self, row: sqlite3.Row) -> ChatMessage:
        return ChatMessage(
            id=row["id"],
            session_id=row["session_id"],
            timestamp=row["timestamp"] or None,
            sender=row["sender"],
            content=row["content"],
            message_type=MessageType(row["message_type"])
            if row["message_type"]
            else MessageType.TEXT,
            created_at=row["created_at"],
        )

    def _row_to_chunk(self, row: sqlite3.Row) -> ChatAnalysisChunk:
        return ChatAnalysisChunk(
            id=row["id"],
            session_title=row["session_title"],
            start_line=row["start_line"],
            end_line=row["end_line"],
            analysis_content=row["analysis_content"],
            analysis_file=row["analysis_file"],
            model_used=row["model_used"],
            created_at=row["created_at"],
        )

    def _row_to_tag(self, row: sqlite3.Row) -> ChatTag:
        return ChatTag(
            id=row["id"],
            name=row["name"],
            category=row["category"],
            mention_count=row["mention_count"],
            created_at=row["created_at"],
        )

    def _row_to_topic(self, row: sqlite3.Row) -> ChatTopic:
        return ChatTopic(
            id=row["id"],
            session_id=row["session_id"],
            topic_name=row["topic_name"],
            keywords=[k.strip() for k in row["keywords"].split(",") if k.strip()],
            start_time=row["start_time"] or None,
            end_time=row["end_time"] or None,
            participant_count=row["participant_count"],
            message_count=row["message_count"],
            summary=row["summary"],
            created_at=row["created_at"],
        )

    def _row_to_insight(self, row: sqlite3.Row) -> ChatInsight:
        import json

        ev = json.loads(row["evidence_messages"]) if row["evidence_messages"] else []
        return ChatInsight(
            id=row["id"],
            session_id=row["session_id"],
            insight_type=row["insight_type"],
            content=row["content"],
            evidence_messages=ev,
            confidence=row["confidence"],
            created_at=row["created_at"],
        )
