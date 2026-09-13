"""对话归档存储层：把用户与 AI 的对话内容（用户问题、知乎原文、我的分析、我的回答）持久化到 SQLite。

表与主数据库共存（conversation_archive + FTS5 全文索引），通过 ``ConversationArchiveStore`` 访问。
设计克隆自 chat_analysis/store.py 的建表 + 懒初始化模式，但保持轻量（直接返回 dict，不引入 pydantic 模型）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from typing import TYPE_CHECKING, Any

from openbiliclaw.storage.database import open_db_conn

if TYPE_CHECKING:
    from pathlib import Path

    from ..storage.database import Database

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS conversation_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seq INTEGER NOT NULL UNIQUE,
    kind TEXT NOT NULL DEFAULT 'zhihu_eval',
    user_question TEXT NOT NULL,
    question_title TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    source_type TEXT DEFAULT '',
    author TEXT DEFAULT '',
    headline TEXT DEFAULT '',
    voteup_count INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    published_at TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    extracted_original_md TEXT DEFAULT '',
    my_analysis_md TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_conv_seq ON conversation_archive(seq);
CREATE INDEX IF NOT EXISTS idx_conv_kind ON conversation_archive(kind);
CREATE INDEX IF NOT EXISTS idx_conv_author ON conversation_archive(author);

CREATE VIRTUAL TABLE IF NOT EXISTS conversation_archive_fts USING fts5(
    user_question,
    question_title,
    author,
    extracted_original_md,
    my_analysis_md,
    content=conversation_archive,
    content_rowid=id,
    tokenize=trigram
);

CREATE TRIGGER IF NOT EXISTS conversation_archive_fts_insert AFTER INSERT ON conversation_archive
BEGIN
    INSERT INTO conversation_archive_fts(
        rowid, user_question, question_title, author, extracted_original_md, my_analysis_md
    )
    VALUES (
        new.id, new.user_question, new.question_title, new.author,
        new.extracted_original_md, new.my_analysis_md
    );
END;

CREATE TRIGGER IF NOT EXISTS conversation_archive_fts_delete AFTER DELETE ON conversation_archive
BEGIN
    DELETE FROM conversation_archive_fts WHERE rowid = old.id;
END;

CREATE TRIGGER IF NOT EXISTS conversation_archive_fts_update AFTER UPDATE ON conversation_archive
BEGIN
    UPDATE conversation_archive_fts SET
        user_question=new.user_question,
        question_title=new.question_title,
        author=new.author,
        extracted_original_md=new.extracted_original_md,
        my_analysis_md=new.my_analysis_md
    WHERE rowid=new.id;
END;
"""


class ConversationArchiveStore:
    """对话归档存储层：单表 CRUD + FTS5 全文搜索。"""

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
            # 与 chat_analysis 同源模式：database= 模式下也懒建表（DDL 幂等），
            # 这样即便没跑过 import 脚本，API 也能自愈，不会报 no such table。
            if not self._initialized:
                self._initialize_tables(conn)
                self._initialized = True
            return conn
        return self._get_conn()

    def _get_conn(self) -> sqlite3.Connection:
        conn: sqlite3.Connection | None = getattr(self._local, "connection", None)
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
        c.execute("PRAGMA wal_checkpoint(TRUNCATE);")

    # ── 写入 ──

    def upsert_item(self, record: dict[str, Any]) -> int:
        """按 seq 幂等写入一条归档。返回行 id。"""
        c = self.conn
        c.execute(
            """INSERT INTO conversation_archive
               (seq, kind, user_question, question_title, source_url, source_type,
                author, headline, voteup_count, comment_count, published_at, tags,
                extracted_original_md, my_analysis_md, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?, CURRENT_TIMESTAMP)
               ON CONFLICT(seq) DO UPDATE SET
                   kind=excluded.kind,
                   user_question=excluded.user_question,
                   question_title=excluded.question_title,
                   source_url=excluded.source_url,
                   source_type=excluded.source_type,
                   author=excluded.author,
                   headline=excluded.headline,
                   voteup_count=excluded.voteup_count,
                   comment_count=excluded.comment_count,
                   published_at=excluded.published_at,
                   tags=excluded.tags,
                   extracted_original_md=excluded.extracted_original_md,
                   my_analysis_md=excluded.my_analysis_md,
                   updated_at=CURRENT_TIMESTAMP"""
            ,
            (
                int(record["seq"]),
                record.get("kind", "zhihu_eval"),
                record.get("user_question", ""),
                record.get("question_title", ""),
                record.get("source_url", ""),
                record.get("source_type", ""),
                record.get("author", ""),
                record.get("headline", ""),
                int(record.get("voteup_count", 0) or 0),
                int(record.get("comment_count", 0) or 0),
                record.get("published_at", ""),
                json.dumps(record.get("tags", []), ensure_ascii=False),
                record.get("extracted_original_md", ""),
                record.get("my_analysis_md", ""),
            ),
        )
        row: sqlite3.Row | None = c.execute(
            "SELECT id FROM conversation_archive WHERE seq = ?", (int(record["seq"]),)
        ).fetchone()
        # use conn.lastrowid if no row found (new insert scenario)
        return row["id"] if row else c.execute("SELECT last_insert_rowid()").fetchone()[0]

    def upsert_many(self, records: list[dict[str, Any]]) -> int:
        """批量写入，返回导入条数（非行 id 之和）。"""
        for r in records:
            self.upsert_item(r)
        return len(records)

    # ── 读取 ──

    def list_items(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "seq",
        sort_order: str = "ASC",
    ) -> list[dict[str, Any]]:
        c = self.conn
        order = "DESC" if sort_order.upper() == "DESC" else "ASC"
        if sort_by not in ("seq", "id", "created_at", "updated_at", "voteup_count", "published_at"):
            sort_by = "seq"
        if search:
            rows = c.execute(
                """SELECT ca.* FROM conversation_archive_fts fts
                   JOIN conversation_archive ca ON fts.rowid = ca.id
                   WHERE conversation_archive_fts MATCH ?
                   ORDER BY bm25(conversation_archive_fts) ASC
                   LIMIT ? OFFSET ?""",
                (search, limit, offset),
            ).fetchall()
        else:
            rows = c.execute(
                f"SELECT * FROM conversation_archive ORDER BY {sort_by} {order} LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM conversation_archive WHERE id = ?", (item_id,)
        ).fetchone()
        return self._row_to_dict(row) if row else None

    def count_items(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM conversation_archive").fetchone()
        return row[0]

    def get_stats(self) -> dict[str, Any]:
        c = self.conn
        total = c.execute("SELECT COUNT(*) FROM conversation_archive").fetchone()[0]
        by_kind = {}
        for kind, n in c.execute(
            "SELECT kind, COUNT(*) FROM conversation_archive GROUP BY kind"
        ).fetchall():
            by_kind[kind] = n
        by_author = []
        for author, n in c.execute(
            "SELECT author, COUNT(*) FROM conversation_archive "
            "WHERE author != '' GROUP BY author ORDER BY COUNT(*) DESC"
        ).fetchall():
            by_author.append({"author": author, "count": n})
        return {
            "total": total,
            "by_kind": by_kind,
            "by_author": by_author,
            "with_original": c.execute(
                "SELECT COUNT(*) FROM conversation_archive WHERE LENGTH(extracted_original_md) > 50"
            ).fetchone()[0],
            "with_analysis": c.execute(
                "SELECT COUNT(*) FROM conversation_archive WHERE LENGTH(my_analysis_md) > 50"
            ).fetchone()[0],
        }

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        try:
            d["tags"] = json.loads(d["tags"]) if d.get("tags") else []
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        return d
