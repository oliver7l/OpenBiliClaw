"""合成结果持久化存储层，支持增量追踪和版本化。"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from .models import (
    CrossModulePattern,
    DiarySynthesisResult,
    SynthesisState,
    SynthesisVersion,
)

logger = logging.getLogger(__name__)


class SynthesisStore:
    """合成结果存储，自动创建表结构并追踪增量状态。"""

    def __init__(self, db_path: str = "data/openbiliclaw.db"):
        self._db_path = db_path
        self._ensure_tables()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _connect_chat_db(self) -> sqlite3.Connection:
        conn = sqlite3.connect("data/chat_analysis.db")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    # ── 建表 ───────────────────────────────────────────────────────

    def _ensure_tables(self) -> None:
        with self._connect() as conn:

            # 合成版本表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS synthesis_versions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    version     INTEGER NOT NULL,
                    parent_version INTEGER DEFAULT NULL,
                    created_at  TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                    new_diary_count       INTEGER DEFAULT 0,
                    new_chat_insight_count INTEGER DEFAULT 0,
                    new_chat_topic_count   INTEGER DEFAULT 0,
                    summary     TEXT DEFAULT '',
                    patterns    TEXT DEFAULT '[]',
                    insights    TEXT DEFAULT '[]',
                    themes      TEXT DEFAULT '[]',
                    concerns    TEXT DEFAULT '[]',
                    cross_patterns TEXT DEFAULT '[]',
                    model_used  TEXT DEFAULT '',
                    tokens_used INTEGER DEFAULT 0
                )
            """)

            # 增量合成状态表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS synthesis_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            # 日记增强回写表
            conn.execute("""
                CREATE TABLE IF NOT EXISTS synthesis_diary_enhancements (
                    diary_id        INTEGER PRIMARY KEY,
                    version         INTEGER NOT NULL,
                    enhanced_tags   TEXT DEFAULT '[]',
                    enhanced_insight TEXT DEFAULT '',
                    cross_refs      TEXT DEFAULT '[]',
                    updated_at      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
            """)

            # 初始化状态
            defaults = {
                "last_diary_analysis_id": "0",
                "last_chat_insight_id": "0",
                "last_chat_topic_id": "0",
                "current_version": "0",
                "last_synthesis_at": "",
                "total_synthesis_runs": "0",
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO synthesis_state (key, value) VALUES (?, ?)",
                    (k, v),
                )

    # ── 状态管理 ───────────────────────────────────────────────────

    def get_state(self) -> SynthesisState:
        with self._connect() as conn:
            rows = dict(conn.execute("SELECT key, value FROM synthesis_state").fetchall())
        return SynthesisState(
            last_diary_analysis_id=int(rows.get("last_diary_analysis_id", "0")),
            last_chat_insight_id=int(rows.get("last_chat_insight_id", "0")),
            last_chat_topic_id=int(rows.get("last_chat_topic_id", "0")),
            current_version=int(rows.get("current_version", "0")),
            last_synthesis_at=rows.get("last_synthesis_at") or None,
            total_synthesis_runs=int(rows.get("total_synthesis_runs", "0")),
        )

    def update_state(self, state: SynthesisState) -> None:
        with self._connect() as conn:
            updates = {
                "last_diary_analysis_id": str(state.last_diary_analysis_id),
                "last_chat_insight_id": str(state.last_chat_insight_id),
                "last_chat_topic_id": str(state.last_chat_topic_id),
                "current_version": str(state.current_version),
                "last_synthesis_at": state.last_synthesis_at or "",
                "total_synthesis_runs": str(state.total_synthesis_runs),
            }
            for k, v in updates.items():
                conn.execute(
                    "INSERT OR REPLACE INTO synthesis_state (key, value) VALUES (?, ?)",
                    (k, v),
                )

    # ── 版本管理 ───────────────────────────────────────────────────

    def save_version(self, v: SynthesisVersion) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO synthesis_versions
                   (version, parent_version, created_at,
                    new_diary_count, new_chat_insight_count, new_chat_topic_count,
                    summary, patterns, insights, themes, concerns,
                    cross_patterns, model_used, tokens_used)
                   VALUES (?, ?, ?,
                           ?, ?, ?,
                           ?, ?, ?, ?, ?,
                           ?, ?, ?)""",
                (
                    v.version,
                    v.parent_version,
                    v.created_at or datetime.now(timezone.utc).isoformat(),
                    v.new_diary_count,
                    v.new_chat_insight_count,
                    v.new_chat_topic_count,
                    v.summary,
                    json.dumps(v.patterns, ensure_ascii=False),
                    json.dumps(v.insights, ensure_ascii=False),
                    json.dumps(v.themes, ensure_ascii=False),
                    json.dumps(v.concerns, ensure_ascii=False),
                    json.dumps(
                        [p.model_dump() for p in v.cross_patterns],
                        ensure_ascii=False,
                    ),
                    v.model_used,
                    v.tokens_used,
                ),
            )
            return cur.lastrowid or 0

    def get_latest_version(self) -> SynthesisVersion | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM synthesis_versions ORDER BY version DESC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        return self._row_to_version(row)

    def get_version(self, version: int) -> SynthesisVersion | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM synthesis_versions WHERE version = ?", (version,)
            ).fetchone()
        if not row:
            return None
        return self._row_to_version(row)

    def list_versions(self, limit: int = 20) -> list[SynthesisVersion]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM synthesis_versions ORDER BY version DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_version(r) for r in rows]

    @staticmethod
    def _row_to_version(row: sqlite3.Row | dict[str, Any]) -> SynthesisVersion:
        if isinstance(row, sqlite3.Row):
            row = dict(row)
        return SynthesisVersion(
            id=row.get("id", 0),
            version=row.get("version", 0),
            parent_version=row.get("parent_version"),
            created_at=row.get("created_at", ""),
            new_diary_count=row.get("new_diary_count", 0),
            new_chat_insight_count=row.get("new_chat_insight_count", 0),
            new_chat_topic_count=row.get("new_chat_topic_count", 0),
            summary=row.get("summary", ""),
            patterns=_safe_json_load(row.get("patterns", "[]")),
            insights=_safe_json_load(row.get("insights", "[]")),
            themes=_safe_json_load(row.get("themes", "[]")),
            concerns=_safe_json_load(row.get("concerns", "[]")),
            cross_patterns=[
                CrossModulePattern(**p)
                for p in _safe_json_load(row.get("cross_patterns", "[]"))
            ],
            model_used=row.get("model_used", ""),
            tokens_used=row.get("tokens_used", 0),
        )

    # ── 日记增强回写 ───────────────────────────────────────────────

    def save_diary_enhancement(self, de: DiarySynthesisResult) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO synthesis_diary_enhancements
                   (diary_id, version, enhanced_tags, enhanced_insight, cross_refs, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    de.diary_id,
                    de.version,
                    json.dumps(de.enhanced_tags, ensure_ascii=False),
                    de.enhanced_insight,
                    json.dumps(de.cross_refs, ensure_ascii=False),
                    de.updated_at or datetime.now(timezone.utc).isoformat(),
                ),
            )

    # ── 数据查询（跨模块拉取） ─────────────────────────────────────

    def get_new_diary_analyses(
        self, since_id: int, limit: int = 50
    ) -> list[dict[str, Any]]:
        """获取上次合成以来的新增日记分析。"""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT da.id, da.diary_id, de.entry_date, de.title,
                          da.summary, da.emotions, da.themes, da.mood_score,
                          da.growth_insight, da.created_at
                   FROM diary_analyses da
                   JOIN diary_entries de ON de.id = da.diary_id
                   WHERE da.id > ?
                   ORDER BY da.id ASC
                   LIMIT ?""",
                (since_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_new_chat_insights(
        self, since_id: int, limit: int = 30
    ) -> list[dict[str, Any]]:
        """获取上次合成以来的新增聊天洞察。"""
        try:
            conn = self._connect()
            # Check if chat insights are in main db
            rows = conn.execute(
                """SELECT id, session_id, insight_type, content, confidence, created_at
                   FROM chat_insights WHERE id > ?
                   ORDER BY id ASC LIMIT ?""",
                (since_id, limit),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            # Chat insights might be in chat_analysis.db
            pass
        try:
            conn = self._connect_chat_db()
            rows = conn.execute(
                """SELECT id, session_id, insight_type, content, confidence, created_at
                   FROM chat_insights WHERE id > ?
                   ORDER BY id ASC LIMIT ?""",
                (since_id, limit),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_new_chat_topics(
        self, since_id: int, limit: int = 20
    ) -> list[dict[str, Any]]:
        """获取上次合成以来的新增聊天话题。"""
        try:
            conn = self._connect()
            rows = conn.execute(
                """SELECT id, session_id, topic_name AS topic, keywords, summary, created_at
                   FROM chat_topics WHERE id > ?
                   ORDER BY id ASC LIMIT ?""",
                (since_id, limit),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            pass
        try:
            conn = self._connect_chat_db()
            rows = conn.execute(
                """SELECT id, session_id, topic_name AS topic, keywords, summary, created_at
                   FROM chat_topics WHERE id > ?
                   ORDER BY id ASC LIMIT ?""",
                (since_id, limit),
            ).fetchall()
            conn.close()
            return [dict(r) for r in rows]
        except Exception:
            return []


def _safe_json_load(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return []