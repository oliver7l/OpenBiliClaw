"""面试复盘记录存储层（SQLite + FTS5 全文检索）。

表结构：
- interview_reviews: 面试复盘主表
- interview_reviews_fts: FTS5 全文检索虚拟表
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import open_db_conn

from .models import (
    InterviewReview,
    InterviewReviewCreate,
    InterviewReviewStats,
    InterviewReviewSummary,
    InterviewReviewUpdate,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS interview_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    position TEXT NOT NULL,
    interview_date TEXT NOT NULL,
    round TEXT NOT NULL DEFAULT 'first',
    result TEXT NOT NULL DEFAULT 'pending',
    duration_min INTEGER DEFAULT 0,
    transcript_text TEXT DEFAULT '',
    transcript_path TEXT DEFAULT '',
    audio_path TEXT DEFAULT '',
    ai_evaluation TEXT DEFAULT '',
    key_questions TEXT DEFAULT '',
    self_assessment TEXT DEFAULT '',
    emotional_review TEXT DEFAULT '',
    technical_review TEXT DEFAULT '',
    action_items TEXT DEFAULT '',
    emotion_level TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_review_company ON interview_reviews(company);
CREATE INDEX IF NOT EXISTS idx_review_date ON interview_reviews(interview_date);
CREATE INDEX IF NOT EXISTS idx_review_result ON interview_reviews(result);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS interview_reviews_fts USING fts5(
    company,
    position,
    key_questions,
    self_assessment,
    emotional_review,
    technical_review,
    action_items,
    ai_evaluation,
    notes,
    content='interview_reviews',
    content_rowid='id',
    tokenize='trigram'
);
"""

FTS_TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS review_fts_ai AFTER INSERT ON interview_reviews BEGIN
    INSERT INTO interview_reviews_fts(rowid, company, position, key_questions,
        self_assessment, emotional_review, technical_review, action_items,
        ai_evaluation, notes)
    VALUES (new.id, new.company, new.position, new.key_questions,
        new.self_assessment, new.emotional_review, new.technical_review,
        new.action_items, new.ai_evaluation, new.notes);
END;

CREATE TRIGGER IF NOT EXISTS review_fts_ad AFTER DELETE ON interview_reviews BEGIN
    INSERT INTO interview_reviews_fts(interview_reviews_fts, rowid, company, position,
        key_questions, self_assessment, emotional_review, technical_review,
        action_items, ai_evaluation, notes)
    VALUES ('delete', old.id, old.company, old.position, old.key_questions,
        old.self_assessment, old.emotional_review, old.technical_review,
        old.action_items, old.ai_evaluation, old.notes);
END;

CREATE TRIGGER IF NOT EXISTS review_fts_au AFTER UPDATE ON interview_reviews BEGIN
    INSERT INTO interview_reviews_fts(interview_reviews_fts, rowid, company, position,
        key_questions, self_assessment, emotional_review, technical_review,
        action_items, ai_evaluation, notes)
    VALUES ('delete', old.id, old.company, old.position, old.key_questions,
        old.self_assessment, old.emotional_review, old.technical_review,
        old.action_items, old.ai_evaluation, old.notes);
    INSERT INTO interview_reviews_fts(rowid, company, position, key_questions,
        self_assessment, emotional_review, technical_review, action_items,
        ai_evaluation, notes)
    VALUES (new.id, new.company, new.position, new.key_questions,
        new.self_assessment, new.emotional_review, new.technical_review,
        new.action_items, new.ai_evaluation, new.notes);
END;
"""


def _row_to_review(row: sqlite3.Row) -> InterviewReview:
    """从数据库行构造 InterviewReview。"""
    return InterviewReview(
        id=row["id"],
        company=row["company"],
        position=row["position"],
        interview_date=date.fromisoformat(row["interview_date"]),
        round=row["round"],
        result=row["result"],
        duration_min=row["duration_min"],
        transcript_text=row["transcript_text"] or "",
        transcript_path=row["transcript_path"] or "",
        audio_path=row["audio_path"] or "",
        ai_evaluation=row["ai_evaluation"] or "",
        key_questions=row["key_questions"] or "",
        self_assessment=row["self_assessment"] or "",
        emotional_review=row["emotional_review"] or "",
        technical_review=row["technical_review"] or "",
        action_items=row["action_items"] or "",
        emotion_level=row["emotion_level"] or None,
        tags=row["tags"] or "",
        notes=row["notes"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_summary(row: sqlite3.Row) -> InterviewReviewSummary:
    """从数据库行构造 InterviewReviewSummary。"""
    return InterviewReviewSummary(
        id=row["id"],
        company=row["company"],
        position=row["position"],
        interview_date=date.fromisoformat(row["interview_date"]),
        round=row["round"],
        result=row["result"],
        duration_min=row["duration_min"],
        emotion_level=row["emotion_level"] or None,
        tags=row["tags"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class InterviewReviewStore:
    """面试复盘记录 SQLite 存储层。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @staticmethod
    def _configure(conn: sqlite3.Connection) -> None:
        """配置 SQLite 连接参数（WAL + 性能调优；写锁由 open_db_conn 统一）。"""
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")

    def _init_schema(self) -> None:
        """初始化表结构。"""
        conn = open_db_conn(self.db_path)
        try:
            self._configure(conn)
            conn.executescript(SCHEMA)
            conn.executescript(FTS_SCHEMA)
            conn.executescript(FTS_TRIGGERS)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        return conn

    def create(self, data: InterviewReviewCreate) -> InterviewReview:
        """创建一条面试复盘记录。"""
        now = datetime.now().isoformat()
        row = data.model_dump()
        row["round"] = data.round.value
        row["result"] = data.result.value
        row["emotion_level"] = data.emotion_level.value if data.emotion_level else ""
        row["interview_date"] = data.interview_date.isoformat()
        row["created_at"] = now
        row["updated_at"] = now

        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        conn = self._conn()
        try:
            cur = conn.execute(
                f"INSERT INTO interview_reviews ({cols}) VALUES ({placeholders})", row
            )
            conn.commit()
            new_id = cur.lastrowid
            created = self.get_by_id(new_id)
            assert created is not None
            return created
        finally:
            conn.close()

    def get_by_id(self, review_id: int) -> InterviewReview | None:
        """按 ID 获取面试复盘记录。"""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM interview_reviews WHERE id = ?", (review_id,)
            ).fetchone()
            return _row_to_review(row) if row else None
        finally:
            conn.close()

    def list_reviews(
        self,
        *,
        company: str | None = None,
        result: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InterviewReviewSummary]:
        """列出面试复盘记录（摘要，不含转录等长文本）。"""
        sql = "SELECT id, company, position, interview_date, round, result, duration_min, emotion_level, tags, created_at FROM interview_reviews"
        where: list[str] = []
        params: list[Any] = []
        if company:
            where.append("company LIKE ?")
            params.append(f"%{company}%")
        if result:
            where.append("result = ?")
            params.append(result)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY interview_date DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        conn = self._conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_summary(r) for r in rows]
        finally:
            conn.close()

    def search(self, query: str, limit: int = 20) -> list[InterviewReviewSummary]:
        """全文检索面试复盘记录（trigram FTS5 + LIKE 回退）。"""
        q = query.strip()
        if not q:
            return []
        conn = self._conn()
        try:
            if len(q) >= 3:
                fts_query = q.replace('"', '""')
                sql = """
                    SELECT r.id, r.company, r.position, r.interview_date, r.round,
                           r.result, r.duration_min, r.emotion_level, r.tags, r.created_at
                    FROM interview_reviews_fts
                    JOIN interview_reviews r ON r.id = interview_reviews_fts.rowid
                    WHERE interview_reviews_fts MATCH ?
                    ORDER BY rank
                    LIMIT ?
                """
                rows = conn.execute(sql, (f'"{fts_query}"', limit)).fetchall()
                if rows:
                    return [_row_to_summary(r) for r in rows]
            # LIKE 回退（短查询或 FTS 无结果）
            pattern = f"%{q}%"
            sql = """
                SELECT id, company, position, interview_date, round, result,
                       duration_min, emotion_level, tags, created_at
                FROM interview_reviews
                WHERE company LIKE ? OR position LIKE ? OR key_questions LIKE ?
                   OR self_assessment LIKE ? OR emotional_review LIKE ?
                   OR technical_review LIKE ? OR action_items LIKE ?
                   OR ai_evaluation LIKE ? OR notes LIKE ?
                ORDER BY interview_date DESC
                LIMIT ?
            """
            rows = conn.execute(sql, [pattern] * 9 + [limit]).fetchall()
            return [_row_to_summary(r) for r in rows]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    def update(self, review_id: int, data: InterviewReviewUpdate) -> InterviewReview | None:
        """更新面试复盘记录（部分更新）。"""
        updates = data.model_dump(exclude_unset=True)
        if not updates:
            return self.get_by_id(review_id)

        if "round" in updates and updates["round"]:
            updates["round"] = updates["round"].value if hasattr(updates["round"], "value") else updates["round"]
        if "result" in updates and updates["result"]:
            updates["result"] = updates["result"].value if hasattr(updates["result"], "value") else updates["result"]
        if "emotion_level" in updates:
            el = updates["emotion_level"]
            updates["emotion_level"] = el.value if hasattr(el, "value") else (el or "")
        if "interview_date" in updates and updates["interview_date"]:
            updates["interview_date"] = updates["interview_date"].isoformat() if hasattr(updates["interview_date"], "isoformat") else updates["interview_date"]

        updates["updated_at"] = datetime.now().isoformat()
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = review_id

        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE interview_reviews SET {set_clause} WHERE id = :id", updates
            )
            conn.commit()
            return self.get_by_id(review_id)
        finally:
            conn.close()

    def delete(self, review_id: int) -> bool:
        """删除面试复盘记录。"""
        conn = self._conn()
        try:
            cur = conn.execute("DELETE FROM interview_reviews WHERE id = ?", (review_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def stats(self) -> InterviewReviewStats:
        """面试复盘统计。"""
        conn = self._conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM interview_reviews").fetchone()[0]
            avg_dur = conn.execute(
                "SELECT AVG(duration_min) FROM interview_reviews WHERE duration_min > 0"
            ).fetchone()[0] or 0.0
            recent_cutoff = (date.today() - timedelta(days=30)).isoformat()
            recent = conn.execute(
                "SELECT COUNT(*) FROM interview_reviews WHERE interview_date >= ?",
                (recent_cutoff,),
            ).fetchone()[0]
            by_result: dict[str, int] = {}
            for row in conn.execute(
                "SELECT result, COUNT(*) FROM interview_reviews GROUP BY result"
            ).fetchall():
                by_result[row[0]] = row[1]
            by_company: dict[str, int] = {}
            for row in conn.execute(
                "SELECT company, COUNT(*) FROM interview_reviews GROUP BY company ORDER BY COUNT(*) DESC"
            ).fetchall():
                by_company[row[0]] = row[1]
            by_emotion: dict[str, int] = {}
            for row in conn.execute(
                "SELECT emotion_level, COUNT(*) FROM interview_reviews WHERE emotion_level != '' GROUP BY emotion_level"
            ).fetchall():
                by_emotion[row[0]] = row[1]
            return InterviewReviewStats(
                total=total,
                by_result=by_result,
                by_company=by_company,
                by_emotion=by_emotion,
                avg_duration=round(avg_dur, 1),
                recent_count=recent,
            )
        finally:
            conn.close()

    def get_all_companies(self) -> list[str]:
        """获取所有面试过的公司列表。"""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT company FROM interview_reviews ORDER BY company"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()
