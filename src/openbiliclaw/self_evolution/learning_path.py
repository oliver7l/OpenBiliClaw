"""Learning path generation module.

Generates structured learning paths from the user's content library.
Given a topic and goal, searches for relevant articles, uses LLM to
order them into a progressive curriculum, and tracks learning progress.

Inspired by Trove AI's learning path feature.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from openbiliclaw.storage.database import open_db_conn

logger = logging.getLogger("self_evolution.learning_path")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class PathStep:
    """A single step in a learning path."""

    article_id: int
    title: str
    url: str
    source_type: str
    learning_point: str = ""  # What to focus on in this article
    estimated_minutes: int = 10
    completed: bool = False
    completed_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "title": self.title,
            "url": self.url,
            "source_type": self.source_type,
            "learning_point": self.learning_point,
            "estimated_minutes": self.estimated_minutes,
            "completed": self.completed,
            "completed_at": self.completed_at,
            "notes": self.notes,
        }


@dataclass
class LearningPath:
    """A structured learning path."""

    path_id: str
    title: str
    topic: str
    description: str = ""
    steps: list[PathStep] = field(default_factory=list)
    status: str = "active"  # active | completed | archived
    progress: float = 0.0  # 0-100
    created_at: str = ""
    updated_at: str = ""
    total_estimated_minutes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path_id": self.path_id,
            "title": self.title,
            "topic": self.topic,
            "description": self.description,
            "steps": [s.to_dict() for s in self.steps],
            "status": self.status,
            "progress": self.progress,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_estimated_minutes": self.total_estimated_minutes,
            "step_count": len(self.steps),
            "completed_count": sum(1 for s in self.steps if s.completed),
        }


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class LearningPathGenerator:
    """Generate learning paths from the content library.

    Args:
        db_path: Path to the SQLite database.
        llm_service: Optional LLM service for ordering and generating learning points.

    """

    def __init__(self, db_path: str, *, llm_service: Any | None = None) -> None:
        self.db_path = db_path
        self.llm_service = llm_service

    def _get_conn(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
        from contextlib import suppress as _suppress
        from pathlib import Path as _Path
        _content_path = _Path(str(self.db_path)).with_name('content.db')
        if _content_path.exists():
            with _suppress(Exception):
                conn.execute('ATTACH DATABASE ? AS content', (str(_content_path),))
        return conn

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS knowledge.learning_paths (
                path_id TEXT PRIMARY KEY,
                title TEXT,
                topic TEXT,
                description TEXT,
                steps_json TEXT,
                status TEXT DEFAULT 'active',
                progress REAL DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                total_estimated_minutes INTEGER DEFAULT 0
            )
            """
        )
        conn.commit()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate_path(
        self,
        topic: str,
        *,
        description: str = "",
        max_articles: int = 30,
        min_articles: int = 3,
    ) -> LearningPath:
        """Generate a learning path for a given topic.

        Args:
            topic: The topic to learn about (e.g., "程序化广告面试").
            description: Additional context or goal for the path.
            max_articles: Maximum articles to consider.
            min_articles: Minimum articles required to generate a path.

        Returns:
            A LearningPath with ordered steps.

        Raises:
            ValueError: If not enough articles are found for the topic.

        """
        # Step 1: Search for relevant articles
        articles = self._search_articles(topic, limit=max_articles)
        logger.info("Found %d articles for topic '%s'", len(articles), topic)

        if len(articles) < min_articles:
            raise ValueError(
                f"内容库中关于'{topic}'的文章不足（找到{len(articles)}篇，至少需要{min_articles}篇）。"
                f"可以先收藏更多相关内容，或者换一个更宽泛的主题。"
            )

        # Step 2: Use LLM to order articles and generate learning points
        if self.llm_service is not None:
            ordered = self._order_with_llm(topic, description, articles)
        else:
            ordered = self._order_fallback(articles)

        # Step 3: Build the path
        now = datetime.now().isoformat()
        path = LearningPath(
            path_id="lp-" + uuid.uuid4().hex[:12],
            title=ordered.get("title", f"{topic}学习路径"),
            topic=topic,
            description=description or ordered.get("description", ""),
            steps=[],
            status="active",
            progress=0.0,
            created_at=now,
            updated_at=now,
        )

        for item in ordered.get("steps", []):
            article_id = item.get("article_id")
            if article_id is None:
                continue
            # Find the matching article
            article = next((a for a in articles if a["id"] == article_id), None)
            if article is None:
                continue
            step = PathStep(
                article_id=article_id,
                title=article["title"],
                url=article["url"],
                source_type=article["source_type"],
                learning_point=item.get("learning_point", ""),
                estimated_minutes=item.get("estimated_minutes", 10),
            )
            path.steps.append(step)

        path.total_estimated_minutes = sum(s.estimated_minutes for s in path.steps)

        # Step 4: Save
        self._save_path(path)
        logger.info("Generated learning path '%s' with %d steps", path.title, len(path.steps))

        return path

    def list_paths(self, *, status: str | None = None, limit: int = 20) -> list[LearningPath]:
        """List all learning paths."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            query = "SELECT * FROM knowledge.learning_paths"
            params: list[Any] = []
            if status:
                query += " WHERE status = ?"
                params.append(status)
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_path(row) for row in rows]
        finally:
            conn.close()

    def get_path(self, path_id: str) -> LearningPath | None:
        """Get a learning path by ID."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            row = conn.execute(
                "SELECT * FROM knowledge.learning_paths WHERE path_id = ?", (path_id,)
            ).fetchone()
            return self._row_to_path(row) if row else None
        finally:
            conn.close()

    def update_step_progress(
        self,
        path_id: str,
        step_index: int,
        *,
        completed: bool | None = None,
        notes: str | None = None,
    ) -> LearningPath | None:
        """Update progress on a specific step."""
        path = self.get_path(path_id)
        if path is None or step_index >= len(path.steps):
            return None

        step = path.steps[step_index]
        if completed is not None:
            step.completed = completed
            step.completed_at = datetime.now().isoformat() if completed else ""
        if notes is not None:
            step.notes = notes

        # Recalculate progress
        completed_count = sum(1 for s in path.steps if s.completed)
        path.progress = round(completed_count / len(path.steps) * 100, 1) if path.steps else 0
        if path.progress >= 100:
            path.status = "completed"
        path.updated_at = datetime.now().isoformat()

        self._save_path(path)
        return path

    def delete_path(self, path_id: str) -> bool:
        """Delete a learning path."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            cursor = conn.execute("DELETE FROM knowledge.learning_paths WHERE path_id = ?", (path_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _search_articles(self, topic: str, *, limit: int = 30) -> list[dict[str, Any]]:
        """Search for articles relevant to a topic.

        Searches in title, tags, ai_summary, and content_text.
        Ranks by match quality (title match > tags match > summary match > content match).
        """
        conn = self._get_conn()
        try:
            # Split topic into keywords for broader matching
            keywords = [
                w.strip()
                for w in topic.replace("，", " ").replace(",", " ").split()
                if len(w.strip()) >= 2
            ]
            if not keywords:
                keywords = [topic]

            # Build WHERE clause with OR conditions
            conditions = []
            params: list[Any] = []
            for kw in keywords[:5]:  # Limit to 5 keywords for performance
                conditions.append(
                    "(title LIKE ? OR tags LIKE ? OR ai_summary LIKE ? OR content_text LIKE ?)"
                )
                like = f"%{kw}%"
                params.extend([like, like, like, like])

            where = " OR ".join(conditions) if conditions else "1=1"

            query = f"""
                SELECT id, title, url, source_type, tags, ai_summary, reading_percent,
                       length(content_text) as content_length, created_at
                FROM articles
                WHERE ({where})
                  AND content_text IS NOT NULL AND length(content_text) > 100
                ORDER BY
                    CASE WHEN title LIKE ? THEN 0 ELSE 1 END,
                    CASE WHEN tags LIKE ? THEN 0 ELSE 1 END,
                    reading_percent DESC,
                    created_at DESC
                LIMIT ?
            """
            # Add ORDER BY params
            params.extend([f"%{keywords[0]}%", f"%{keywords[0]}%", limit])

            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def _order_with_llm(
        self,
        topic: str,
        description: str,
        articles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Use LLM to order articles into a progressive learning path.

        Returns a dict with: title, description, steps (list of {article_id, learning_point, estimated_minutes})  # noqa: E501
        """
        from openbiliclaw.llm.generation import generate_structured
        from openbiliclaw.self_evolution.insight_report import _run_async

        # Build article list for LLM (compact)
        articles_data = []
        for a in articles[:20]:  # Limit to 20 for LLM context
            summary = (a.get("ai_summary") or "")[:200]
            tags = a.get("tags") or ""
            articles_data.append(
                {
                    "id": a["id"],
                    "title": a["title"][:100],
                    "source": a["source_type"],
                    "tags": tags[:100],
                    "summary": summary,
                }
            )

        system_instruction = (
            "你是一个学习路径规划专家。根据用户提供的文章列表，为指定主题生成一个循序渐进的学习路径。"
            "要求：\n"
            "1. 将文章按学习顺序排列：入门概念 → 基础理论 → 技术细节 → 实战案例 → 进阶优化\n"
            "2. 为每篇文章生成一个学习要点（这篇文章应该重点学什么），不超过50字\n"
            "3. 估算每篇文章的阅读时间（分钟）\n"
            "4. 为整个学习路径生成一个标题和描述\n"
            "5. 只使用提供的文章，不要编造不存在的文章\n"
            "6. 如果文章太多，挑选最相关的10-15篇\n"
            '返回JSON格式：{"title": "...", "description": "...", "steps": [{"article_id": 123, "learning_point": "...", "estimated_minutes": 15}]}'
        )

        user_input = (
            f"学习主题：{topic}\n"
            f"学习目标：{description or '系统掌握该主题'}\n\n"
            f"可用文章列表（共{len(articles_data)}篇）：\n"
            f"{json.dumps(articles_data, ensure_ascii=False, indent=2)}"
        )

        try:
            result = _run_async(
                generate_structured(
                    self.llm_service,
                    system_instruction=system_instruction,
                    user_input=user_input,
                    parse=lambda x: x,
                    label="learning_path_generation",
                    temperature=0.3,
                    max_tokens=2000,
                )
            )

            # Parse the result
            text = str(result).strip()
            # Try to extract JSON from the response
            parsed = self._parse_llm_json(text)
            if parsed and "steps" in parsed:
                return parsed

            logger.warning("LLM returned invalid format, using fallback")
        except Exception:
            logger.exception("LLM learning path generation failed")

        return self._order_fallback(articles)

    def _parse_llm_json(self, text: str) -> dict[str, Any] | None:
        """Parse JSON from LLM response, handling markdown code blocks."""
        import re

        # Try direct JSON parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to extract JSON from markdown code block
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find first { and last }
        first = text.find("{")
        last = text.rfind("}")
        if first != -1 and last != -1 and last > first:
            try:
                return json.loads(text[first : last + 1])
            except json.JSONDecodeError:
                pass

        return None

    def _order_fallback(self, articles: list[dict[str, Any]]) -> dict[str, Any]:
        """Fallback ordering when LLM is unavailable.

        Simple heuristic: sort by content length (shorter = more introductory),
        then by reading_percent.
        """
        sorted_articles = sorted(
            articles,
            key=lambda a: (a.get("content_length") or 0, -(a.get("reading_percent") or 0)),
        )

        steps = []
        for a in sorted_articles[:15]:
            content_len = a.get("content_length") or 1000
            estimated = max(5, min(60, content_len // 200))  # ~200 chars per minute
            steps.append(
                {
                    "article_id": a["id"],
                    "learning_point": f"阅读《{a['title'][:30]}》，理解核心概念",
                    "estimated_minutes": estimated,
                }
            )

        return {
            "title": f"{sorted_articles[0]['title'][:20]}...学习路径",
            "description": "基于内容库自动生成的学习路径",
            "steps": steps,
        }

    def _save_path(self, path: LearningPath) -> None:
        """Save a learning path to the database."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            steps_json = json.dumps([s.to_dict() for s in path.steps], ensure_ascii=False)
            conn.execute(
                """
                INSERT OR REPLACE INTO knowledge.learning_paths
                (path_id, title, topic, description, steps_json, status, progress,
                 created_at, updated_at, total_estimated_minutes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    path.path_id,
                    path.title,
                    path.topic,
                    path.description,
                    steps_json,
                    path.status,
                    path.progress,
                    path.created_at,
                    path.updated_at,
                    path.total_estimated_minutes,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _row_to_path(self, row: sqlite3.Row) -> LearningPath:
        """Convert a database row to a LearningPath object."""
        steps_data = json.loads(row["steps_json"] or "[]")
        steps = [
            PathStep(
                article_id=s.get("article_id", 0),
                title=s.get("title", ""),
                url=s.get("url", ""),
                source_type=s.get("source_type", ""),
                learning_point=s.get("learning_point", ""),
                estimated_minutes=s.get("estimated_minutes", 10),
                completed=s.get("completed", False),
                completed_at=s.get("completed_at", ""),
                notes=s.get("notes", ""),
            )
            for s in steps_data
        ]
        return LearningPath(
            path_id=row["path_id"],
            title=row["title"],
            topic=row["topic"],
            description=row["description"],
            steps=steps,
            status=row["status"],
            progress=row["progress"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            total_estimated_minutes=row["total_estimated_minutes"],
        )
