"""面试题阅读追踪系统存储层（SQLite）。"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from openbiliclaw.storage.database import open_db_conn

from .models import (
    DailyProgress,
    MasteryLevel,
    Priority,
    Question,
    QuestionCategory,
    QuestionCreate,
    QuestionStats,
    ReadingPlan,
    ReadingRecord,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS iq_questions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    answer TEXT DEFAULT '',
    category TEXT DEFAULT 'other',
    difficulty INTEGER DEFAULT 3,
    source TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    url TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_iq_category ON iq_questions(category);
CREATE INDEX IF NOT EXISTS idx_iq_difficulty ON iq_questions(difficulty);
CREATE INDEX IF NOT EXISTS idx_iq_source ON iq_questions(source);

CREATE TABLE IF NOT EXISTS iq_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL UNIQUE,
    priority TEXT DEFAULT 'medium',
    added_at TEXT NOT NULL,
    planned_date TEXT,
    FOREIGN KEY (question_id) REFERENCES iq_questions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_iq_queue_priority ON iq_queue(priority);
CREATE INDEX IF NOT EXISTS idx_iq_queue_planned ON iq_queue(planned_date);

CREATE TABLE IF NOT EXISTS iq_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question_id INTEGER NOT NULL,
    read_date TEXT NOT NULL,
    mastery TEXT DEFAULT 'reading',
    review_count INTEGER DEFAULT 0,
    last_reviewed TEXT,
    notes TEXT DEFAULT '',
    time_spent_min INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (question_id) REFERENCES iq_questions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_iq_records_question ON iq_records(question_id);
CREATE INDEX IF NOT EXISTS idx_iq_records_date ON iq_records(read_date);
CREATE INDEX IF NOT EXISTS idx_iq_records_mastery ON iq_records(mastery);

CREATE TABLE IF NOT EXISTS iq_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT,
    daily_target INTEGER DEFAULT 5,
    categories TEXT DEFAULT '',
    min_difficulty INTEGER DEFAULT 1,
    max_difficulty INTEGER DEFAULT 5,
    is_active INTEGER DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS iq_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL,
    progress_date TEXT NOT NULL,
    questions_read INTEGER DEFAULT 0,
    questions_mastered INTEGER DEFAULT 0,
    target INTEGER DEFAULT 0,
    notes TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (plan_id) REFERENCES iq_plans(id) ON DELETE CASCADE,
    UNIQUE(plan_id, progress_date)
);

CREATE INDEX IF NOT EXISTS idx_iq_daily_date ON iq_daily(progress_date);
"""


def _row_to_question(row: sqlite3.Row) -> Question:
    return Question(
        id=row["id"],
        title=row["title"],
        answer=row["answer"] or "",
        category=QuestionCategory(row["category"]),
        difficulty=row["difficulty"],
        source=row["source"] or "",
        tags=row["tags"] or "",
        url=row["url"] or "",
        notes=row["notes"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_record(row: sqlite3.Row) -> ReadingRecord:
    return ReadingRecord(
        id=row["id"],
        question_id=row["question_id"],
        read_date=date.fromisoformat(row["read_date"]),
        mastery=MasteryLevel(row["mastery"]),
        review_count=row["review_count"],
        last_reviewed=datetime.fromisoformat(row["last_reviewed"]) if row["last_reviewed"] else None,
        notes=row["notes"] or "",
        time_spent_min=row["time_spent_min"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_plan(row: sqlite3.Row) -> ReadingPlan:
    return ReadingPlan(
        id=row["id"],
        name=row["name"],
        start_date=date.fromisoformat(row["start_date"]),
        end_date=date.fromisoformat(row["end_date"]) if row["end_date"] else None,
        daily_target=row["daily_target"],
        categories=row["categories"] or "",
        min_difficulty=row["min_difficulty"],
        max_difficulty=row["max_difficulty"],
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_daily(row: sqlite3.Row) -> DailyProgress:
    return DailyProgress(
        id=row["id"],
        plan_id=row["plan_id"],
        progress_date=date.fromisoformat(row["progress_date"]),
        questions_read=row["questions_read"],
        questions_mastered=row["questions_mastered"],
        target=row["target"],
        notes=row["notes"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class InterviewQuestionStore:
    """面试题阅读追踪系统存储层。"""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @staticmethod
    def _configure(conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")

    def _init_schema(self) -> None:
        conn = open_db_conn(self.db_path)
        try:
            self._configure(conn)
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        self._configure(conn)
        return conn

    # ── 题目 CRUD ──────────────────────────────────────────────

    def add_question(self, data: QuestionCreate) -> Question:
        now = datetime.now().isoformat()
        row = data.model_dump()
        row["category"] = data.category.value
        row["created_at"] = now
        row["updated_at"] = now
        cols = ", ".join(row.keys())
        placeholders = ", ".join(f":{k}" for k in row)
        conn = self._conn()
        try:
            cur = conn.execute(
                f"INSERT INTO iq_questions ({cols}) VALUES ({placeholders})", row
            )
            conn.commit()
            return self.get_question(cur.lastrowid)
        finally:
            conn.close()

    def get_question(self, qid: int) -> Question | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM iq_questions WHERE id = ?", (qid,)).fetchone()
            return _row_to_question(row) if row else None
        finally:
            conn.close()

    def list_questions(
        self,
        *,
        category: QuestionCategory | None = None,
        difficulty_min: int | None = None,
        difficulty_max: int | None = None,
        source: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[Question]:
        sql = "SELECT * FROM iq_questions"
        where: list[str] = []
        params: list[Any] = []
        if category:
            where.append("category = ?")
            params.append(category.value)
        if difficulty_min is not None:
            where.append("difficulty >= ?")
            params.append(difficulty_min)
        if difficulty_max is not None:
            where.append("difficulty <= ?")
            params.append(difficulty_max)
        if source:
            where.append("source LIKE ?")
            params.append(f"%{source}%")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY difficulty DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        conn = self._conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_question(r) for r in rows]
        finally:
            conn.close()

    def update_question(self, qid: int, **kwargs) -> Question | None:
        if not kwargs:
            return self.get_question(qid)
        kwargs["updated_at"] = datetime.now().isoformat()
        if "category" in kwargs and isinstance(kwargs["category"], QuestionCategory):
            kwargs["category"] = kwargs["category"].value
        set_clause = ", ".join(f"{k} = :{k}" for k in kwargs)
        kwargs["id"] = qid
        conn = self._conn()
        try:
            conn.execute(f"UPDATE iq_questions SET {set_clause} WHERE id = :id", kwargs)
            conn.commit()
            return self.get_question(qid)
        finally:
            conn.close()

    def delete_question(self, qid: int) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute("DELETE FROM iq_questions WHERE id = ?", (qid,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── 待看队列 ───────────────────────────────────────────────

    def add_to_queue(self, qid: int, priority: Priority = Priority.MEDIUM, planned_date: date | None = None) -> bool:
        now = datetime.now().isoformat()
        conn = self._conn()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO iq_queue (question_id, priority, added_at, planned_date) VALUES (?, ?, ?, ?)",
                (qid, priority.value, now, planned_date.isoformat() if planned_date else None),
            )
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def get_queue(self, *, limit: int = 50) -> list[tuple[Question, Priority, date | None]]:
        conn = self._conn()
        try:
            rows = conn.execute(
                """SELECT q.*, qq.priority, qq.planned_date
                   FROM iq_queue qq
                   JOIN iq_questions q ON q.id = qq.question_id
                   ORDER BY CASE qq.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                            qq.planned_date IS NULL, qq.planned_date, qq.added_at
                   LIMIT ?""",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                q = _row_to_question(r)
                p = Priority(r["priority"])
                pd = date.fromisoformat(r["planned_date"]) if r["planned_date"] else None
                result.append((q, p, pd))
            return result
        finally:
            conn.close()

    def remove_from_queue(self, qid: int) -> bool:
        conn = self._conn()
        try:
            cur = conn.execute("DELETE FROM iq_queue WHERE question_id = ?", (qid,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # ── 阅读记录 ───────────────────────────────────────────────

    def mark_read(
        self,
        qid: int,
        *,
        mastery: MasteryLevel = MasteryLevel.READING,
        notes: str = "",
        time_spent_min: int = 0,
    ) -> ReadingRecord:
        today = date.today().isoformat()
        now = datetime.now().isoformat()
        conn = self._conn()
        try:
            existing = conn.execute(
                "SELECT id, review_count FROM iq_records WHERE question_id = ? ORDER BY id DESC LIMIT 1",
                (qid,),
            ).fetchone()
            if existing:
                review_count = existing["review_count"] + 1
                conn.execute(
                    """UPDATE iq_records SET read_date = ?, mastery = ?, review_count = ?,
                       last_reviewed = ?, notes = ?, time_spent_min = ? WHERE id = ?""",
                    (today, mastery.value, review_count, now, notes, time_spent_min, existing["id"]),
                )
                conn.commit()
                rid = existing["id"]
            else:
                cur = conn.execute(
                    """INSERT INTO iq_records (question_id, read_date, mastery, review_count,
                       last_reviewed, notes, time_spent_min, created_at)
                       VALUES (?, ?, ?, 0, ?, ?, ?, ?)""",
                    (qid, today, mastery.value, now, notes, time_spent_min, now),
                )
                conn.commit()
                rid = cur.lastrowid
            self.remove_from_queue(qid)
            row = conn.execute("SELECT * FROM iq_records WHERE id = ?", (rid,)).fetchone()
            return _row_to_record(row)
        finally:
            conn.close()

    def get_records(self, *, qid: int | None = None, limit: int = 50) -> list[ReadingRecord]:
        sql = "SELECT * FROM iq_records"
        params: list[Any] = []
        if qid:
            sql += " WHERE question_id = ?"
            params.append(qid)
        sql += " ORDER BY read_date DESC, id DESC LIMIT ?"
        params.append(limit)
        conn = self._conn()
        try:
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_record(r) for r in rows]
        finally:
            conn.close()

    def get_question_mastery(self, qid: int) -> MasteryLevel:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT mastery FROM iq_records WHERE question_id = ? ORDER BY id DESC LIMIT 1",
                (qid,),
            ).fetchone()
            return MasteryLevel(row["mastery"]) if row else MasteryLevel.NOT_STARTED
        finally:
            conn.close()

    # ── 阅读计划 ───────────────────────────────────────────────

    def create_plan(
        self,
        name: str,
        *,
        daily_target: int = 5,
        categories: str = "",
        min_difficulty: int = 1,
        max_difficulty: int = 5,
        start_date: date | None = None,
    ) -> ReadingPlan:
        now = datetime.now().isoformat()
        sd = (start_date or date.today()).isoformat()
        conn = self._conn()
        try:
            conn.execute("UPDATE iq_plans SET is_active = 0 WHERE is_active = 1")
            cur = conn.execute(
                """INSERT INTO iq_plans (name, start_date, daily_target, categories,
                   min_difficulty, max_difficulty, is_active, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?)""",
                (name, sd, daily_target, categories, min_difficulty, max_difficulty, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM iq_plans WHERE id = ?", (cur.lastrowid,)).fetchone()
            return _row_to_plan(row)
        finally:
            conn.close()

    def get_active_plan(self) -> ReadingPlan | None:
        conn = self._conn()
        try:
            row = conn.execute("SELECT * FROM iq_plans WHERE is_active = 1 ORDER BY id DESC LIMIT 1").fetchone()
            return _row_to_plan(row) if row else None
        finally:
            conn.close()

    def get_today_queue(self, plan: ReadingPlan) -> list[Question]:
        """获取今日待读题目（按计划筛选，优先未开始和需要复习的）。"""
        cats = [c.strip() for c in plan.categories.split(",") if c.strip()] if plan.categories else []
        conn = self._conn()
        try:
            sql = """SELECT q.* FROM iq_questions q
                     LEFT JOIN iq_records r ON r.question_id = q.id
                     WHERE q.difficulty BETWEEN ? AND ?"""
            params: list[Any] = [plan.min_difficulty, plan.max_difficulty]
            if cats:
                placeholders = ", ".join("?" for _ in cats)
                sql += f" AND q.category IN ({placeholders})"
                params.extend(cats)
            sql += """ GROUP BY q.id
                       ORDER BY
                         CASE COALESCE(MAX(r.mastery), 'not_started')
                           WHEN 'need_review' THEN 0
                           WHEN 'not_started' THEN 1
                           WHEN 'reading' THEN 2
                           WHEN 'understood' THEN 3
                           ELSE 4
                         END,
                         q.difficulty DESC
                       LIMIT ?"""
            params.append(plan.daily_target)
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_question(r) for r in rows]
        finally:
            conn.close()

    def record_daily(
        self,
        plan_id: int,
        progress_date: date,
        *,
        questions_read: int,
        questions_mastered: int = 0,
        target: int = 0,
        notes: str = "",
    ) -> DailyProgress:
        now = datetime.now().isoformat()
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO iq_daily (
                       plan_id, progress_date, questions_read, questions_mastered,
                       target, notes, created_at
                   )
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(plan_id, progress_date) DO UPDATE SET
                     questions_read = questions_read + excluded.questions_read,
                     questions_mastered = questions_mastered + excluded.questions_mastered,
                     notes = excluded.notes""",
                (plan_id, progress_date.isoformat(), questions_read, questions_mastered, target, notes, now),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM iq_daily WHERE plan_id = ? AND progress_date = ?",
                (plan_id, progress_date.isoformat()),
            ).fetchone()
            return _row_to_daily(row)
        finally:
            conn.close()

    def get_daily_progress(self, plan_id: int, days: int = 7) -> list[DailyProgress]:
        cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT * FROM iq_daily WHERE plan_id = ? AND progress_date >= ? ORDER BY progress_date",
                (plan_id, cutoff),
            ).fetchall()
            return [_row_to_daily(r) for r in rows]
        finally:
            conn.close()

    # ── 统计 ───────────────────────────────────────────────────

    def stats(self) -> QuestionStats:
        conn = self._conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM iq_questions").fetchone()[0]
            by_cat = {r[0]: r[1] for r in conn.execute("SELECT category, COUNT(*) FROM iq_questions GROUP BY category")}
            by_diff = {
                r[0]: r[1]
                for r in conn.execute("SELECT difficulty, COUNT(*) FROM iq_questions GROUP BY difficulty")
            }
            by_mastery = {}
            not_started = total
            reading = understood = mastered = need_review = 0
            for r in conn.execute(
                """SELECT mastery, COUNT(DISTINCT question_id) FROM iq_records
                   WHERE id IN (SELECT MAX(id) FROM iq_records GROUP BY question_id)
                   GROUP BY mastery"""
            ):
                by_mastery[r[0]] = r[1]
                if r[0] == "reading":
                    reading = r[1]
                elif r[0] == "understood":
                    understood = r[1]
                elif r[0] == "mastered":
                    mastered = r[1]
                elif r[0] == "need_review":
                    need_review = r[1]
            not_started = total - reading - understood - mastered - need_review
            return QuestionStats(
                total=total,
                by_category=by_cat,
                by_difficulty=by_diff,
                by_mastery=by_mastery,
                not_started=not_started,
                reading=reading,
                understood=understood,
                mastered=mastered,
                need_review=need_review,
            )
        finally:
            conn.close()

    def bulk_add_questions(self, questions: list[QuestionCreate]) -> int:
        """批量添加题目。"""
        count = 0
        for q in questions:
            self.add_question(q)
            count += 1
        return count
