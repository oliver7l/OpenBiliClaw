"""日记存储层：SQLite 数据持久化。

独立管理日记相关数据表，提供 CRUD、检索、统计等底层操作。
表结构与项目主数据库共存，通过独立的 store 类访问。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from ..storage.database import Database
from .models import (
    DiaryAnalysis,
    DiaryEntry,
    DiaryEntryCreate,
    DiaryEntryUpdate,
    DiaryFragment,
    DiaryPerson,
    DiaryStats,
    DiaryTag,
    MoodLevel,
    TagType,
)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS diary_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_date TEXT NOT NULL,
    title TEXT DEFAULT '',
    content TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    tags TEXT DEFAULT '[]',
    mood TEXT DEFAULT 'unknown',
    mood_score REAL DEFAULT 0.0,
    word_count INTEGER DEFAULT 0,
    analysis_id INTEGER DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_diary_entries_date ON diary_entries(entry_date);
CREATE INDEX IF NOT EXISTS idx_diary_entries_mood ON diary_entries(mood);
CREATE INDEX IF NOT EXISTS idx_diary_entries_source ON diary_entries(source);

CREATE TABLE IF NOT EXISTS diary_analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    diary_id INTEGER NOT NULL,
    summary TEXT DEFAULT '',
    key_points TEXT DEFAULT '[]',
    emotions TEXT DEFAULT '{}',
    themes TEXT DEFAULT '[]',
    people_mentioned TEXT DEFAULT '[]',
    growth_insight TEXT DEFAULT '',
    mood_score REAL DEFAULT 0.0,
    model_used TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (diary_id) REFERENCES diary_entries(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_diary_analyses_diary ON diary_analyses(diary_id);

CREATE TABLE IF NOT EXISTS diary_fts (
    rowid INTEGER PRIMARY KEY,
    title TEXT,
    content TEXT,
    tags TEXT
);

CREATE TABLE IF NOT EXISTS diary_fragments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    mood TEXT DEFAULT 'unknown',
    fragment_date TEXT NOT NULL,
    source TEXT DEFAULT 'manual',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_diary_fragments_date ON diary_fragments(fragment_date);
CREATE INDEX IF NOT EXISTS idx_diary_fragments_mood ON diary_fragments(mood);

-- 标签表：AI 自动提取的结构化标签
CREATE TABLE IF NOT EXISTS diary_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    type TEXT DEFAULT 'other',
    count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_diary_tags_type ON diary_tags(type);
CREATE INDEX IF NOT EXISTS idx_diary_tags_count ON diary_tags(count DESC);

-- 日记-标签关联表
CREATE TABLE IF NOT EXISTS diary_entry_tags (
    entry_id INTEGER NOT NULL,
    tag_id INTEGER NOT NULL,
    confidence REAL DEFAULT 1.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entry_id, tag_id),
    FOREIGN KEY (entry_id) REFERENCES diary_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (tag_id) REFERENCES diary_tags(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_diary_entry_tags_tag ON diary_entry_tags(tag_id);

-- 人物表：AI 自动识别的人物档案
CREATE TABLE IF NOT EXISTS diary_persons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    aliases TEXT DEFAULT '[]',
    relation TEXT DEFAULT '',
    description TEXT DEFAULT '',
    first_appeared TEXT DEFAULT '',
    last_appeared TEXT DEFAULT '',
    appearance_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_diary_persons_relation ON diary_persons(relation);
CREATE INDEX IF NOT EXISTS idx_diary_persons_count ON diary_persons(appearance_count DESC);

-- 日记-人物关联表
CREATE TABLE IF NOT EXISTS diary_entry_persons (
    entry_id INTEGER NOT NULL,
    person_id INTEGER NOT NULL,
    context TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entry_id, person_id),
    FOREIGN KEY (entry_id) REFERENCES diary_entries(id) ON DELETE CASCADE,
    FOREIGN KEY (person_id) REFERENCES diary_persons(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_diary_entry_persons_person ON diary_entry_persons(person_id);

-- 日记向量表：存储每篇日记的 embedding，用于语义搜索和 RAG
CREATE TABLE IF NOT EXISTS diary_embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL UNIQUE,
    vector TEXT NOT NULL,
    model TEXT DEFAULT '',
    dimension INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entry_id) REFERENCES diary_entries(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_diary_embeddings_entry ON diary_embeddings(entry_id);
CREATE INDEX IF NOT EXISTS idx_diary_embeddings_model ON diary_embeddings(model);
"""


class DiaryStore:
    """日记数据存储。

    可以传入已有的 Database 实例复用连接，也可以传入独立路径。
    """

    def __init__(self, database: Database | None = None, db_path: str | Path | None = None) -> None:
        self._database = database
        self._db_path = Path(db_path) if db_path else None
        self._thread_local = threading.local()
        self._initialized = False

    @property
    def conn(self) -> sqlite3.Connection:
        """获取当前线程的数据库连接。"""
        if self._database is not None:
            return self._database.conn
        if not hasattr(self._thread_local, "conn") or self._thread_local.conn is None:
            assert self._db_path is not None
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), timeout=30.0, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout = 30000")
            self._thread_local.conn = conn
        return self._thread_local.conn

    def initialize(self) -> None:
        """初始化日记数据表。"""
        if self._initialized:
            return
        self.conn.executescript(_SCHEMA_SQL)
        self.conn.commit()
        self._initialized = True

    # ── 日记条目 CRUD ──────────────────────────────────────────

    def create_entry(self, data: DiaryEntryCreate) -> DiaryEntry:
        """创建一条日记。"""
        self.initialize()
        now = datetime.now()
        word_count = len(data.content)
        cursor = self.conn.execute(
            """
            INSERT INTO diary_entries (entry_date, title, content, source, tags, mood, mood_score, word_count, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.entry_date,
                data.title,
                data.content,
                data.source,
                json.dumps(data.tags, ensure_ascii=False),
                data.mood.value,
                0.0,
                word_count,
                now.isoformat(),
                now.isoformat(),
            ),
        )
        self.conn.commit()
        return self.get_entry(cursor.lastrowid)  # type: ignore[arg-type]

    def get_entry(self, entry_id: int) -> DiaryEntry:
        """根据 ID 获取日记。"""
        self.initialize()
        row = self.conn.execute("SELECT * FROM diary_entries WHERE id = ?", (entry_id,)).fetchone()
        if row is None:
            raise ValueError(f"日记不存在: id={entry_id}")
        return self._row_to_entry(row)

    def update_entry(self, entry_id: int, data: DiaryEntryUpdate) -> DiaryEntry:
        """更新日记。"""
        self.initialize()
        existing = self.get_entry(entry_id)
        updates: dict[str, object] = {}
        if data.title is not None:
            updates["title"] = data.title
        if data.content is not None:
            updates["content"] = data.content
            updates["word_count"] = len(data.content)
        if data.tags is not None:
            updates["tags"] = json.dumps(data.tags, ensure_ascii=False)
        if data.mood is not None:
            updates["mood"] = data.mood.value
        if data.entry_date is not None:
            updates["entry_date"] = data.entry_date
        if not updates:
            return existing
        updates["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        self.conn.execute(
            f"UPDATE diary_entries SET {set_clause} WHERE id = ?",
            (*updates.values(), entry_id),
        )
        self.conn.commit()
        return self.get_entry(entry_id)

    def delete_entry(self, entry_id: int) -> None:
        """删除日记及其分析。"""
        self.initialize()
        self.conn.execute("DELETE FROM diary_analyses WHERE diary_id = ?", (entry_id,))
        self.conn.execute("DELETE FROM diary_entries WHERE id = ?", (entry_id,))
        self.conn.commit()

    def list_entries(
        self,
        limit: int = 50,
        offset: int = 0,
        start_date: str | None = None,
        end_date: str | None = None,
        mood: MoodLevel | None = None,
        source: str | None = None,
        tag: str | None = None,
        search: str | None = None,
        sort_by: str = "entry_date",
        sort_order: str = "DESC",
    ) -> list[DiaryEntry]:
        """列出日记，支持多条件筛选与搜索。"""
        self.initialize()
        conditions: list[str] = []
        params: list[object] = []
        if start_date:
            conditions.append("entry_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("entry_date <= ?")
            params.append(end_date)
        if mood:
            conditions.append("mood = ?")
            params.append(mood.value)
        if source:
            conditions.append("source = ?")
            params.append(source)
        if tag:
            conditions.append("tags LIKE ?")
            params.append(f"%{tag}%")
        if search:
            conditions.append("(title LIKE ? OR content LIKE ?)")
            params.extend([f"%{search}%", f"%{search}%"])
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        allowed_sort = {"entry_date", "created_at", "updated_at", "word_count"}
        sort_col = sort_by if sort_by in allowed_sort else "entry_date"
        order = "ASC" if sort_order.upper() == "ASC" else "DESC"
        rows = self.conn.execute(
            f"SELECT * FROM diary_entries{where} ORDER BY {sort_col} {order} LIMIT ? OFFSET ?",
            (*params, limit, offset),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    def count_entries(
        self,
        start_date: str | None = None,
        end_date: str | None = None,
        mood: MoodLevel | None = None,
        source: str | None = None,
    ) -> int:
        """统计日记数量。"""
        self.initialize()
        conditions: list[str] = []
        params: list[object] = []
        if start_date:
            conditions.append("entry_date >= ?")
            params.append(start_date)
        if end_date:
            conditions.append("entry_date <= ?")
            params.append(end_date)
        if mood:
            conditions.append("mood = ?")
            params.append(mood.value)
        if source:
            conditions.append("source = ?")
            params.append(source)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        row = self.conn.execute(
            f"SELECT COUNT(*) as cnt FROM diary_entries{where}", params
        ).fetchone()
        return int(row["cnt"])

    # ── 分析记录 ────────────────────────────────────────────────

    def create_analysis(self, diary_id: int, analysis: dict, model_used: str = "") -> DiaryAnalysis:
        """保存日记分析结果。"""
        self.initialize()
        now = datetime.now()
        cursor = self.conn.execute(
            """
            INSERT INTO diary_analyses (diary_id, summary, key_points, emotions, themes, people_mentioned, growth_insight, mood_score, model_used, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                diary_id,
                analysis.get("summary", ""),
                json.dumps(analysis.get("key_points", []), ensure_ascii=False),
                json.dumps(analysis.get("emotions", {}), ensure_ascii=False),
                json.dumps(analysis.get("themes", []), ensure_ascii=False),
                json.dumps(analysis.get("people_mentioned", []), ensure_ascii=False),
                analysis.get("growth_insight", ""),
                analysis.get("mood_score", 0.0),
                model_used,
                now.isoformat(),
            ),
        )
        analysis_id = cursor.lastrowid
        self.conn.execute(
            "UPDATE diary_entries SET analysis_id = ?, mood_score = ? WHERE id = ?",
            (analysis_id, analysis.get("mood_score", 0.0), diary_id),
        )
        self.conn.commit()
        return self.get_analysis(analysis_id)  # type: ignore[arg-type]

    def get_analysis(self, analysis_id: int) -> DiaryAnalysis:
        """获取分析记录。"""
        self.initialize()
        row = self.conn.execute(
            "SELECT * FROM diary_analyses WHERE id = ?", (analysis_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"分析记录不存在: id={analysis_id}")
        return self._row_to_analysis(row)

    def get_analysis_by_diary(self, diary_id: int) -> DiaryAnalysis | None:
        """根据日记 ID 获取分析。"""
        self.initialize()
        row = self.conn.execute(
            "SELECT * FROM diary_analyses WHERE diary_id = ? ORDER BY id DESC LIMIT 1", (diary_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_analysis(row)

    def list_analyses(self, limit: int = 50, offset: int = 0) -> list[DiaryAnalysis]:
        """列出分析记录。"""
        self.initialize()
        rows = self.conn.execute(
            "SELECT * FROM diary_analyses ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._row_to_analysis(row) for row in rows]

    # ── 统计 ────────────────────────────────────────────────────

    def get_stats(self) -> DiaryStats:
        """获取日记统计信息。"""
        self.initialize()
        total = self.conn.execute("SELECT COUNT(*) as cnt FROM diary_entries").fetchone()["cnt"]
        total_words_row = self.conn.execute(
            "SELECT COALESCE(SUM(word_count), 0) as total FROM diary_entries"
        ).fetchone()
        total_words = int(total_words_row["total"])
        date_range = self.conn.execute(
            "SELECT MIN(entry_date) as earliest, MAX(entry_date) as latest FROM diary_entries"
        ).fetchone()
        avg_words = round(total_words / total, 1) if total > 0 else 0.0

        mood_rows = self.conn.execute(
            "SELECT mood, COUNT(*) as cnt FROM diary_entries GROUP BY mood"
        ).fetchall()
        mood_dist = {row["mood"]: int(row["cnt"]) for row in mood_rows}

        tag_counts: dict[str, int] = {}
        all_tags = self.conn.execute("SELECT tags FROM diary_entries").fetchall()
        for row in all_tags:
            try:
                tags = json.loads(row["tags"])
                for t in tags:
                    tag_counts[t] = tag_counts.get(t, 0) + 1
            except (json.JSONDecodeError, TypeError):
                pass
        top_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:20]

        month_rows = self.conn.execute(
            "SELECT substr(entry_date, 1, 7) as month, COUNT(*) as cnt FROM diary_entries GROUP BY month ORDER BY month"
        ).fetchall()
        by_month = {row["month"]: int(row["cnt"]) for row in month_rows}

        analyzed = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM diary_entries WHERE analysis_id IS NOT NULL"
        ).fetchone()["cnt"]

        return DiaryStats(
            total_entries=total,
            total_words=total_words,
            earliest_date=date_range["earliest"] or "",
            latest_date=date_range["latest"] or "",
            avg_words_per_entry=avg_words,
            mood_distribution=mood_dist,
            top_tags=top_tags,
            entries_by_month=by_month,
            analyzed_count=analyzed,
        )

    def get_unanalyzed_entries(self, limit: int = 100) -> list[DiaryEntry]:
        """获取未分析的日记。"""
        self.initialize()
        rows = self.conn.execute(
            "SELECT * FROM diary_entries WHERE analysis_id IS NULL ORDER BY entry_date LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_entry(row) for row in rows]

    # ── 内部转换 ────────────────────────────────────────────────

    def _row_to_entry(self, row: sqlite3.Row) -> DiaryEntry:
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        return DiaryEntry(
            id=row["id"],
            entry_date=row["entry_date"],
            title=row["title"] or "",
            content=row["content"],
            source=row["source"] or "manual",
            tags=tags,
            mood=MoodLevel(row["mood"]) if row["mood"] else MoodLevel.UNKNOWN,
            mood_score=float(row["mood_score"] or 0.0),
            word_count=int(row["word_count"] or 0),
            created_at=datetime.fromisoformat(row["created_at"])
            if isinstance(row["created_at"], str)
            else row["created_at"],
            updated_at=datetime.fromisoformat(row["updated_at"])
            if isinstance(row["updated_at"], str)
            else row["updated_at"],
            analysis_id=row["analysis_id"],
        )

    def _row_to_analysis(self, row: sqlite3.Row) -> DiaryAnalysis:
        try:
            key_points = json.loads(row["key_points"]) if row["key_points"] else []
        except (json.JSONDecodeError, TypeError):
            key_points = []
        try:
            emotions = json.loads(row["emotions"]) if row["emotions"] else {}
        except (json.JSONDecodeError, TypeError):
            emotions = {}
        try:
            themes = json.loads(row["themes"]) if row["themes"] else []
        except (json.JSONDecodeError, TypeError):
            themes = []
        try:
            people = json.loads(row["people_mentioned"]) if row["people_mentioned"] else []
        except (json.JSONDecodeError, TypeError):
            people = []
        return DiaryAnalysis(
            id=row["id"],
            diary_id=row["diary_id"],
            summary=row["summary"] or "",
            key_points=key_points,
            emotions=emotions,
            themes=themes,
            people_mentioned=people,
            growth_insight=row["growth_insight"] or "",
            mood_score=float(row["mood_score"] or 0.0),
            model_used=row["model_used"] or "",
            created_at=datetime.fromisoformat(row["created_at"])
            if isinstance(row["created_at"], str)
            else row["created_at"],
        )

    # ─── 碎片（随手记）操作 ───

    def create_fragment(self, content: str, mood: MoodLevel = MoodLevel.UNKNOWN,
                         fragment_date: str | None = None, source: str = "manual") -> DiaryFragment:
        """创建一条碎片。"""
        self.initialize()
        if fragment_date is None:
            fragment_date = datetime.now().strftime("%Y-%m-%d")
        cursor = self.conn.execute(
            """
            INSERT INTO diary_fragments (content, mood, fragment_date, source)
            VALUES (?, ?, ?, ?)
            """,
            (content, mood.value, fragment_date, source),
        )
        self.conn.commit()
        return self.get_fragment(cursor.lastrowid)

    def get_fragment(self, fragment_id: int) -> DiaryFragment | None:
        """根据 ID 获取碎片。"""
        self.initialize()
        cursor = self.conn.execute(
            "SELECT * FROM diary_fragments WHERE id = ?", (fragment_id,)
        )
        row = cursor.fetchone()
        return self._row_to_fragment(row) if row else None

    def list_fragments(self, fragment_date: str | None = None,
                        limit: int = 100, offset: int = 0) -> list[DiaryFragment]:
        """列出碎片，可按日期筛选。"""
        self.initialize()
        if fragment_date:
            cursor = self.conn.execute(
                """
                SELECT * FROM diary_fragments
                WHERE fragment_date = ?
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (fragment_date, limit, offset),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT * FROM diary_fragments
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
        return [self._row_to_fragment(row) for row in cursor.fetchall()]

    def delete_fragment(self, fragment_id: int) -> bool:
        """删除碎片。"""
        self.initialize()
        cursor = self.conn.execute(
            "DELETE FROM diary_fragments WHERE id = ?", (fragment_id,)
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def count_fragments(self, fragment_date: str | None = None) -> int:
        """统计碎片数量。"""
        self.initialize()
        if fragment_date:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM diary_fragments WHERE fragment_date = ?",
                (fragment_date,),
            )
        else:
            cursor = self.conn.execute("SELECT COUNT(*) FROM diary_fragments")
        return int(cursor.fetchone()[0])

    def _row_to_fragment(self, row: sqlite3.Row) -> DiaryFragment:
        """将数据库行转换为 DiaryFragment 对象。"""
        return DiaryFragment(
            id=row["id"],
            content=row["content"],
            mood=MoodLevel(row["mood"]) if row["mood"] else MoodLevel.UNKNOWN,
            fragment_date=row["fragment_date"],
            source=row["source"] or "manual",
            created_at=datetime.fromisoformat(row["created_at"])
            if isinstance(row["created_at"], str)
            else row["created_at"],
        )

    # ═══════════════════════════════════════════
    # 标签相关方法
    # ═══════════════════════════════════════════

    def get_or_create_tag(self, name: str, tag_type: TagType = TagType.OTHER) -> DiaryTag:
        """获取或创建标签（按名称唯一）。"""
        self.initialize()
        cursor = self.conn.execute("SELECT * FROM diary_tags WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return self._row_to_tag(row)
        cursor = self.conn.execute(
            "INSERT INTO diary_tags (name, type) VALUES (?, ?)",
            (name, tag_type.value),
        )
        self.conn.commit()
        return self.get_tag(cursor.lastrowid)

    def get_tag(self, tag_id: int) -> DiaryTag | None:
        """根据 ID 获取标签。"""
        self.initialize()
        cursor = self.conn.execute("SELECT * FROM diary_tags WHERE id = ?", (tag_id,))
        row = cursor.fetchone()
        return self._row_to_tag(row) if row else None

    def get_tag_by_name(self, name: str) -> DiaryTag | None:
        """根据名称获取标签。"""
        self.initialize()
        cursor = self.conn.execute("SELECT * FROM diary_tags WHERE name = ?", (name,))
        row = cursor.fetchone()
        return self._row_to_tag(row) if row else None

    def list_tags(self, tag_type: TagType | None = None,
                  limit: int = 200, min_count: int = 1) -> list[DiaryTag]:
        """列出标签，可按类型筛选，按使用次数排序。"""
        self.initialize()
        if tag_type:
            cursor = self.conn.execute(
                """
                SELECT * FROM diary_tags
                WHERE type = ? AND count >= ?
                ORDER BY count DESC
                LIMIT ?
                """,
                (tag_type.value, min_count, limit),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT * FROM diary_tags
                WHERE count >= ?
                ORDER BY count DESC
                LIMIT ?
                """,
                (min_count, limit),
            )
        return [self._row_to_tag(row) for row in cursor.fetchall()]

    def add_tag_to_entry(self, entry_id: int, tag_id: int, confidence: float = 1.0) -> None:
        """给日记添加标签（幂等）。"""
        self.initialize()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO diary_entry_tags (entry_id, tag_id, confidence)
            VALUES (?, ?, ?)
            """,
            (entry_id, tag_id, confidence),
        )
        self.conn.execute(
            "UPDATE diary_tags SET count = count + 1 WHERE id = ?",
            (tag_id,),
        )
        self.conn.commit()

    def get_entry_tags(self, entry_id: int) -> list[DiaryTag]:
        """获取某篇日记的所有标签。"""
        self.initialize()
        cursor = self.conn.execute(
            """
            SELECT t.* FROM diary_tags t
            JOIN diary_entry_tags et ON t.id = et.tag_id
            WHERE et.entry_id = ?
            ORDER BY et.confidence DESC
            """,
            (entry_id,),
        )
        return [self._row_to_tag(row) for row in cursor.fetchall()]

    def get_entries_by_tag(self, tag_id: int, limit: int = 100) -> list[int]:
        """获取包含某标签的所有日记 ID。"""
        self.initialize()
        cursor = self.conn.execute(
            """
            SELECT entry_id FROM diary_entry_tags
            WHERE tag_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (tag_id, limit),
        )
        return [row[0] for row in cursor.fetchall()]

    def count_tags(self) -> int:
        """统计标签总数。"""
        self.initialize()
        cursor = self.conn.execute("SELECT COUNT(*) FROM diary_tags WHERE count > 0")
        return int(cursor.fetchone()[0])

    def _row_to_tag(self, row: sqlite3.Row) -> DiaryTag:
        """将数据库行转换为 DiaryTag 对象。"""
        return DiaryTag(
            id=row["id"],
            name=row["name"],
            type=TagType(row["type"]) if row["type"] else TagType.OTHER,
            count=row["count"] or 0,
            created_at=datetime.fromisoformat(row["created_at"])
            if isinstance(row["created_at"], str)
            else row["created_at"],
        )

    # ═══════════════════════════════════════════
    # 人物相关方法
    # ═══════════════════════════════════════════

    def get_or_create_person(self, name: str, relation: str = "") -> DiaryPerson:
        """获取或创建人物（按名称唯一）。"""
        self.initialize()
        cursor = self.conn.execute("SELECT * FROM diary_persons WHERE name = ?", (name,))
        row = cursor.fetchone()
        if row:
            return self._row_to_person(row)
        cursor = self.conn.execute(
            "INSERT INTO diary_persons (name, relation) VALUES (?, ?)",
            (name, relation),
        )
        self.conn.commit()
        return self.get_person(cursor.lastrowid)

    def get_person(self, person_id: int) -> DiaryPerson | None:
        """根据 ID 获取人物。"""
        self.initialize()
        cursor = self.conn.execute("SELECT * FROM diary_persons WHERE id = ?", (person_id,))
        row = cursor.fetchone()
        return self._row_to_person(row) if row else None

    def get_person_by_name(self, name: str) -> DiaryPerson | None:
        """根据名称获取人物。"""
        self.initialize()
        cursor = self.conn.execute("SELECT * FROM diary_persons WHERE name = ?", (name,))
        row = cursor.fetchone()
        return self._row_to_person(row) if row else None

    def list_persons(self, relation: str | None = None,
                     limit: int = 200, min_appearances: int = 1) -> list[DiaryPerson]:
        """列出人物，可按关系筛选，按出现次数排序。"""
        self.initialize()
        if relation:
            cursor = self.conn.execute(
                """
                SELECT * FROM diary_persons
                WHERE relation = ? AND appearance_count >= ?
                ORDER BY appearance_count DESC
                LIMIT ?
                """,
                (relation, min_appearances, limit),
            )
        else:
            cursor = self.conn.execute(
                """
                SELECT * FROM diary_persons
                WHERE appearance_count >= ?
                ORDER BY appearance_count DESC
                LIMIT ?
                """,
                (min_appearances, limit),
            )
        return [self._row_to_person(row) for row in cursor.fetchall()]

    def add_person_to_entry(self, entry_id: int, person_id: int,
                             context: str = "", entry_date: str = "") -> None:
        """给日记添加人物关联（幂等），并更新人物统计。"""
        self.initialize()
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO diary_entry_persons (entry_id, person_id, context)
            VALUES (?, ?, ?)
            """,
            (entry_id, person_id, context),
        )
        if cursor.rowcount > 0:
            # 更新出现次数
            self.conn.execute(
                "UPDATE diary_persons SET appearance_count = appearance_count + 1 WHERE id = ?",
                (person_id,),
            )
            # 更新首次/最后出现日期
            if entry_date:
                self.conn.execute(
                    """
                    UPDATE diary_persons
                    SET first_appeared = CASE WHEN first_appeared = '' OR first_appeared > ? THEN ? ELSE first_appeared END,
                        last_appeared = CASE WHEN last_appeared = '' OR last_appeared < ? THEN ? ELSE last_appeared END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (entry_date, entry_date, entry_date, entry_date, person_id),
                )
            self.conn.commit()

    def get_entry_persons(self, entry_id: int) -> list[tuple[DiaryPerson, str]]:
        """获取某篇日记的所有人物，返回 (人物, 上下文) 列表。"""
        self.initialize()
        cursor = self.conn.execute(
            """
            SELECT p.*, ep.context as ep_context FROM diary_persons p
            JOIN diary_entry_persons ep ON p.id = ep.person_id
            WHERE ep.entry_id = ?
            ORDER BY ep.created_at ASC
            """,
            (entry_id,),
        )
        results = []
        for row in cursor.fetchall():
            person = self._row_to_person(row)
            context = row["ep_context"] if "ep_context" in row.keys() else ""
            results.append((person, context))
        return results

    def get_person_entries(self, person_id: int, limit: int = 100) -> list[dict]:
        """获取某人物出现的所有日记，返回包含上下文的列表。"""
        self.initialize()
        cursor = self.conn.execute(
            """
            SELECT e.id, e.entry_date, e.title, e.content, ep.context
            FROM diary_entries e
            JOIN diary_entry_persons ep ON e.id = ep.entry_id
            WHERE ep.person_id = ?
            ORDER BY e.entry_date DESC
            LIMIT ?
            """,
            (person_id, limit),
        )
        results = []
        for row in cursor.fetchall():
            results.append({
                "id": row["id"],
                "entry_date": row["entry_date"],
                "title": row["title"],
                "context": row["context"] or "",
                "content_preview": row["content"][:200] if row["content"] else "",
            })
        return results

    def count_persons(self) -> int:
        """统计人物总数。"""
        self.initialize()
        cursor = self.conn.execute("SELECT COUNT(*) FROM diary_persons WHERE appearance_count > 0")
        return int(cursor.fetchone()[0])

    def get_person_relations(self) -> list[tuple[str, int]]:
        """获取人物关系分布统计。"""
        self.initialize()
        cursor = self.conn.execute(
            """
            SELECT relation, COUNT(*) as cnt
            FROM diary_persons
            WHERE relation != '' AND appearance_count > 0
            GROUP BY relation
            ORDER BY cnt DESC
            """
        )
        return [(row[0], row[1]) for row in cursor.fetchall()]

    def _row_to_person(self, row: sqlite3.Row) -> DiaryPerson:
        """将数据库行转换为 DiaryPerson 对象。"""
        try:
            aliases = json.loads(row["aliases"]) if row["aliases"] else []
        except (json.JSONDecodeError, TypeError):
            aliases = []
        return DiaryPerson(
            id=row["id"],
            name=row["name"],
            aliases=aliases,
            relation=row["relation"] or "",
            description=row["description"] or "",
            first_appeared=row["first_appeared"] or "",
            last_appeared=row["last_appeared"] or "",
            appearance_count=row["appearance_count"] or 0,
            created_at=datetime.fromisoformat(row["created_at"])
            if isinstance(row["created_at"], str)
            else row["created_at"],
            updated_at=datetime.fromisoformat(row["updated_at"])
            if isinstance(row["updated_at"], str)
            else row["updated_at"],
        )

    # ── 日记向量（Embedding）CRUD ─────────────────────────────

    def upsert_embedding(self, entry_id: int, vector: list[float], model: str = "") -> None:
        """插入或更新某篇日记的向量。

        Args:
            entry_id: 日记 ID
            vector: embedding 向量
            model: 使用的 embedding 模型
        """
        self.initialize()
        now = datetime.now()
        self.conn.execute(
            """
            INSERT INTO diary_embeddings (entry_id, vector, model, dimension, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(entry_id) DO UPDATE SET
                vector = excluded.vector,
                model = excluded.model,
                dimension = excluded.dimension,
                updated_at = excluded.updated_at
            """,
            (entry_id, json.dumps(vector), model, len(vector), now, now),
        )
        self.conn.commit()

    def get_embedding(self, entry_id: int) -> list[float] | None:
        """获取某篇日记的向量。"""
        self.initialize()
        cursor = self.conn.execute(
            "SELECT vector FROM diary_embeddings WHERE entry_id = ?",
            (entry_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["vector"])
        except (json.JSONDecodeError, TypeError):
            return None

    def get_all_embeddings(self) -> list[tuple[int, list[float]]]:
        """获取所有日记的向量，返回 [(entry_id, vector), ...]。"""
        self.initialize()
        cursor = self.conn.execute(
            "SELECT entry_id, vector FROM diary_embeddings ORDER BY entry_id"
        )
        results = []
        for row in cursor.fetchall():
            try:
                vector = json.loads(row["vector"])
                results.append((row["entry_id"], vector))
            except (json.JSONDecodeError, TypeError):
                continue
        return results

    def get_unembedded_entries(self, limit: int = 100) -> list[DiaryEntry]:
        """获取尚未生成向量的日记列表。"""
        self.initialize()
        cursor = self.conn.execute(
            """
            SELECT e.* FROM diary_entries e
            LEFT JOIN diary_embeddings de ON e.id = de.entry_id
            WHERE de.id IS NULL
            ORDER BY e.entry_date DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [self._row_to_entry(row) for row in cursor.fetchall()]

    def count_embeddings(self) -> int:
        """统计已生成向量的日记数量。"""
        self.initialize()
        cursor = self.conn.execute("SELECT COUNT(*) FROM diary_embeddings")
        return int(cursor.fetchone()[0])

    def delete_embedding(self, entry_id: int) -> None:
        """删除某篇日记的向量。"""
        self.initialize()
        self.conn.execute("DELETE FROM diary_embeddings WHERE entry_id = ?", (entry_id,))
        self.conn.commit()
