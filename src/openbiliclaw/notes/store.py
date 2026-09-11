"""笔记存储层：SQLite 数据持久化。

独立管理笔记相关数据表，提供 CRUD、检索、统计等底层操作。
表结构与项目主数据库共存，通过独立的 store 类访问。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from openbiliclaw.storage.database import open_db_conn

from .models import Note, NoteCreate, NoteTask, NoteTaskCreate, NoteUpdate

if TYPE_CHECKING:
    from ..storage.database import Database

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT '',
    content_md TEXT NOT NULL DEFAULT '',
    note_type TEXT NOT NULL DEFAULT 'manual',
    source_platform TEXT DEFAULT '',
    source_url TEXT DEFAULT '',
    source_ref TEXT DEFAULT '',
    author TEXT DEFAULT '',
    tags TEXT DEFAULT '[]',
    metadata TEXT DEFAULT '{}',
    raw_ref TEXT DEFAULT '',
    task_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_notes_type ON notes(note_type);
CREATE INDEX IF NOT EXISTS idx_notes_platform ON notes(source_platform);
CREATE INDEX IF NOT EXISTS idx_notes_source_ref ON notes(source_ref);
CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_updated_at ON notes(updated_at DESC);

CREATE TABLE IF NOT EXISTS note_tasks (
    task_id TEXT PRIMARY KEY,
    source_platform TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    resume_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    current_stage TEXT DEFAULT '',
    items_json TEXT DEFAULT '[]',
    error_summary TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_note_tasks_status ON note_tasks(status);
CREATE INDEX IF NOT EXISTS idx_note_tasks_platform ON note_tasks(source_platform);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    title, content_md, tags, author,
    tokenize='trigram',
    content='notes',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS notes_fts_ai AFTER INSERT ON notes BEGIN
    INSERT INTO notes_fts(rowid, title, content_md, tags, author)
    VALUES (new.id, new.title, new.content_md, new.tags, new.author);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_ad AFTER DELETE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, content_md, tags, author)
    VALUES ('delete', old.id, old.title, old.content_md, old.tags, old.author);
END;

CREATE TRIGGER IF NOT EXISTS notes_fts_au AFTER UPDATE ON notes BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, content_md, tags, author)
    VALUES ('delete', old.id, old.title, old.content_md, old.tags, old.author);
    INSERT INTO notes_fts(rowid, title, content_md, tags, author)
    VALUES (new.id, new.title, new.content_md, new.tags, new.author);
END;
"""


class NoteStore:
    """笔记数据存储。

    可以传入已有的 Database 实例复用连接，也可以传入独立路径。
    """

    def __init__(self, database: Database | None = None, db_path: str | Path | None = None) -> None:
        self._database = database
        self._db_path = Path(db_path) if db_path else None
        self._thread_local = threading.local()
        self._initialized = False

    @property
    def conn(self) -> sqlite3.Connection:
        if self._database is not None:
            return self._database.conn
        if not hasattr(self._thread_local, "conn") or self._thread_local.conn is None:
            assert self._db_path is not None
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = open_db_conn(str(self._db_path))
            self._thread_local.conn = conn
        return self._thread_local.conn

    def initialize(self) -> None:
        """初始化笔记数据表。"""
        if self._initialized:
            return
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.commit()
        self._initialized = True

    # ── 笔记 CRUD ──

    def create_note(self, data: NoteCreate) -> Note:
        """创建笔记。"""
        now = datetime.now().isoformat()
        tags_json = json.dumps(data.tags, ensure_ascii=False)
        metadata_json = json.dumps(data.metadata, ensure_ascii=False)
        cursor = self.conn.execute(
            """INSERT INTO notes (title, content_md, note_type, source_platform, source_url,
               source_ref, author, tags, metadata, raw_ref, task_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data.title,
                data.content_md,
                data.note_type,
                data.source_platform,
                data.source_url,
                data.source_ref,
                data.author,
                tags_json,
                metadata_json,
                data.raw_ref,
                data.task_id,
                now,
                now,
            ),
        )
        self.conn.commit()
        note = self.get_note(cursor.lastrowid)
        assert note is not None
        return note

    def get_note(self, note_id: int) -> Note | None:
        """根据 ID 获取笔记。"""
        row = self.conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return self._row_to_note(row) if row else None

    def update_note(self, note_id: int, data: NoteUpdate) -> Note | None:
        """更新笔记。"""
        existing = self.get_note(note_id)
        if existing is None:
            return None

        fields = []
        values = []
        for field in (
            "title",
            "content_md",
            "note_type",
            "source_platform",
            "source_url",
            "source_ref",
            "author",
        ):
            val = getattr(data, field, None)
            if val is not None:
                fields.append(f"{field} = ?")
                values.append(val)

        if data.tags is not None:
            fields.append("tags = ?")
            values.append(json.dumps(data.tags, ensure_ascii=False))
        if data.metadata is not None:
            fields.append("metadata = ?")
            values.append(json.dumps(data.metadata, ensure_ascii=False))

        if not fields:
            return existing

        fields.append("updated_at = ?")
        values.append(datetime.now().isoformat())
        values.append(note_id)

        self.conn.execute(f"UPDATE notes SET {', '.join(fields)} WHERE id = ?", values)
        self.conn.commit()
        return self.get_note(note_id)

    def delete_note(self, note_id: int) -> bool:
        """删除笔记。"""
        cursor = self.conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def list_notes(
        self,
        limit: int = 50,
        offset: int = 0,
        note_type: str | None = None,
        source_platform: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "DESC",
    ) -> list[Note]:
        """列出笔记，支持筛选、搜索和排序。"""
        conditions = []
        params: list = []

        if note_type:
            conditions.append("note_type = ?")
            params.append(note_type)
        if source_platform:
            if source_platform == "none":
                conditions.append("(source_platform IS NULL OR source_platform = '')")
            else:
                conditions.append("source_platform = ?")
                params.append(source_platform)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')

        if search:
            conditions.append("id IN (SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?)")
            params.append(search)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        sort = (
            sort_by
            if sort_by in ("created_at", "updated_at", "title", "note_type")
            else "created_at"
        )
        order = "ASC" if sort_order.upper() == "ASC" else "DESC"

        rows = self.conn.execute(
            f"SELECT * FROM notes {where} ORDER BY {sort} {order} LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

        return [self._row_to_note(r) for r in rows]

    def count_notes(
        self,
        note_type: str | None = None,
        source_platform: str | None = None,
        tag: str | None = None,
        search: str | None = None,
    ) -> int:
        """统计笔记数量。"""
        conditions = []
        params: list = []

        if note_type:
            conditions.append("note_type = ?")
            params.append(note_type)
        if source_platform:
            if source_platform == "none":
                conditions.append("(source_platform IS NULL OR source_platform = '')")
            else:
                conditions.append("source_platform = ?")
                params.append(source_platform)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f'%"{tag}"%')
        if search:
            conditions.append("id IN (SELECT rowid FROM notes_fts WHERE notes_fts MATCH ?)")
            params.append(search)

        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        row = self.conn.execute(f"SELECT COUNT(*) AS n FROM notes {where}", params).fetchone()
        return row["n"] if row else 0

    def get_stats(self) -> dict:
        """获取笔记统计信息。"""
        total = self.conn.execute("SELECT COUNT(*) AS n FROM notes").fetchone()["n"]

        by_type_rows = self.conn.execute(
            "SELECT note_type, COUNT(*) AS n FROM notes GROUP BY note_type ORDER BY n DESC"
        ).fetchall()
        by_type = {r["note_type"]: r["n"] for r in by_type_rows}

        by_platform_rows = self.conn.execute(
            "SELECT source_platform, COUNT(*) AS n FROM notes GROUP BY source_platform ORDER BY n DESC"
        ).fetchall()
        by_platform = {r["source_platform"] or "none": r["n"] for r in by_platform_rows}

        top_tags = self._collect_top_tags()

        return {
            "total": total,
            "by_type": by_type,
            "by_platform": by_platform,
            "top_tags": top_tags,
        }

    def _collect_top_tags(self, limit: int = 20) -> list[dict]:
        """从 notes 表的 tags JSON 字段收集热门标签。"""
        rows = self.conn.execute("SELECT tags FROM notes WHERE tags != '[]'").fetchall()
        tag_count: dict[str, int] = {}
        for row in rows:
            try:
                tags = json.loads(row["tags"])
                for t in tags:
                    tag_count[t] = tag_count.get(t, 0) + 1
            except (json.JSONDecodeError, TypeError):
                continue
        sorted_tags = sorted(tag_count.items(), key=lambda x: -x[1])[:limit]
        return [{"name": name, "count": count} for name, count in sorted_tags]

    # ── 笔记任务 ──

    def create_task(self, data: NoteTaskCreate) -> NoteTask:
        """创建笔记生成任务。"""
        now = datetime.now().isoformat()
        self.conn.execute(
            """INSERT OR IGNORE INTO note_tasks (task_id, source_platform, source_ref, resume_key, items_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                data.task_id,
                data.source_platform,
                data.source_ref,
                data.resume_key,
                data.items_json,
                now,
                now,
            ),
        )
        self.conn.commit()
        task = self.get_task(data.task_id)
        assert task is not None
        return task

    def get_task(self, task_id: str) -> NoteTask | None:
        """根据 ID 获取任务。"""
        row = self.conn.execute("SELECT * FROM note_tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def get_task_by_resume_key(self, resume_key: str) -> NoteTask | None:
        """根据恢复键获取任务。"""
        row = self.conn.execute(
            "SELECT * FROM note_tasks WHERE resume_key = ?", (resume_key,)
        ).fetchone()
        return self._row_to_task(row) if row else None

    def update_task_status(
        self, task_id: str, status: str, stage: str = "", error: str = ""
    ) -> None:
        """更新任务状态。"""
        now = datetime.now().isoformat()
        self.conn.execute(
            "UPDATE note_tasks SET status = ?, current_stage = ?, error_summary = ?, updated_at = ? WHERE task_id = ?",
            (status, stage, error, now, task_id),
        )
        self.conn.commit()

    def list_tasks(
        self, limit: int = 20, offset: int = 0, status: str | None = None
    ) -> list[NoteTask]:
        """列出任务。"""
        if status:
            rows = self.conn.execute(
                "SELECT * FROM note_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, limit, offset),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM note_tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_task(r) for r in rows]

    # ── 辅助方法 ──

    def _row_to_note(self, row: sqlite3.Row) -> Note:
        tags = json.loads(row["tags"]) if row["tags"] else []
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}
        return Note(
            id=row["id"],
            title=row["title"],
            content_md=row["content_md"],
            note_type=row["note_type"],
            source_platform=row["source_platform"],
            source_url=row["source_url"],
            source_ref=row["source_ref"],
            author=row["author"],
            tags=tags,
            metadata=metadata,
            raw_ref=row["raw_ref"],
            task_id=row["task_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_task(self, row: sqlite3.Row) -> NoteTask:
        return NoteTask(
            task_id=row["task_id"],
            source_platform=row["source_platform"],
            source_ref=row["source_ref"],
            resume_key=row["resume_key"],
            status=row["status"],
            current_stage=row["current_stage"],
            items_json=row["items_json"],
            error_summary=row["error_summary"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
