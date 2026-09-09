"""On-demand TL;DR (Too Long; Didn't Read) generation module.

Generates concise summaries (3-5 key points + one-line conclusion) for
articles in the content library.  Inspired by Cruxwire's TL;DR feature.

Use cases:
- Quick preview of long articles before deciding to read fully
- Batch generate TL;DRs for favorited articles
- Reading time estimation
"""

from __future__ import annotations

import json
import logging
import sqlite3
from openbiliclaw.storage.database import open_db_conn
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger("self_evolution.tldr")


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class TLDR:
    """A concise summary of an article."""

    article_id: int
    title: str = ""
    url: str = ""
    source_type: str = ""
    key_points: list[str] = field(default_factory=list)  # 3-5 key points
    conclusion: str = ""  # One-line conclusion
    reading_minutes: int = 0  # Estimated reading time
    word_count: int = 0
    generated_at: str = ""
    model: str = ""  # Which LLM model generated this

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "title": self.title,
            "url": self.url,
            "source_type": self.source_type,
            "key_points": self.key_points,
            "conclusion": self.conclusion,
            "reading_minutes": self.reading_minutes,
            "word_count": self.word_count,
            "generated_at": self.generated_at,
            "model": self.model,
        }


# ---------------------------------------------------------------------------
# Generator
# ---------------------------------------------------------------------------


class TLDRGenerator:
    """Generate TL;DR summaries for articles.

    Args:
        db_path: Path to the SQLite database.
        llm_service: Optional LLM service for generation.

    """

    # Reading speed: words per minute (Cruxwire uses 200)
    WORDS_PER_MINUTE = 200
    # Chinese characters per minute (rough estimate)
    CHARS_PER_MINUTE = 400

    def __init__(self, db_path: str, *, llm_service: Any | None = None) -> None:
        self.db_path = db_path
        self.llm_service = llm_service

    def _get_conn(self) -> sqlite3.Connection:
        conn = open_db_conn(self.db_path)
        conn.row_factory = sqlite3.Row
        # v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
        from pathlib import Path as _Path
        from contextlib import suppress as _suppress
        _content_path = _Path(str(self.db_path)).with_name('content.db')
        if _content_path.exists():
            with _suppress(Exception):
                conn.execute('ATTACH DATABASE ? AS content', (str(_content_path),))
        return conn

    def _ensure_table(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS article_tldrs (
                article_id INTEGER PRIMARY KEY,
                title TEXT,
                url TEXT,
                source_type TEXT,
                key_points_json TEXT,
                conclusion TEXT,
                reading_minutes INTEGER,
                word_count INTEGER,
                generated_at TEXT,
                model TEXT
            )
            """
        )
        conn.commit()

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def generate_tldr(self, article_id: int, *, force: bool = False) -> TLDR | None:
        """Generate a TL;DR for a specific article.

        Args:
            article_id: The article ID.
            force: If True, regenerate even if one exists.

        Returns:
            The generated TLDR, or None if the article doesn't exist.

        """
        # Check cache
        if not force:
            existing = self.get_tldr(article_id)
            if existing is not None:
                return existing

        # Load article
        article = self._load_article(article_id)
        if article is None:
            return None

        # Estimate reading time
        content = article.get("content_text") or ""
        word_count = self._count_words(content)
        reading_minutes = self._estimate_reading_time(content, word_count)

        # Generate with LLM if available
        key_points: list[str] = []
        conclusion = ""
        model = ""

        if self.llm_service is not None and len(content) > 200:
            try:
                result = self._generate_with_llm(
                    title=article.get("title") or "",
                    content=content[:8000],  # Limit for context
                    source_type=article.get("source_type") or "",
                )
                key_points = result.get("key_points", [])
                conclusion = result.get("conclusion", "")
                model = result.get("model", "")
            except Exception:
                logger.exception("LLM TL;DR generation failed for article %d", article_id)

        # Fallback: extractive summary
        if not key_points:
            key_points = self._extractive_fallback(content, article.get("title") or "")
            conclusion = f"《{article.get('title', '')[:30]}》的核心要点速览"

        tldr = TLDR(
            article_id=article_id,
            title=article.get("title") or "",
            url=article.get("url") or "",
            source_type=article.get("source_type") or "",
            key_points=key_points[:5],  # Max 5 points
            conclusion=conclusion,
            reading_minutes=reading_minutes,
            word_count=word_count,
            generated_at=datetime.now().isoformat(),
            model=model,
        )

        # Save
        self._save_tldr(tldr)
        return tldr

    def batch_generate(
        self,
        *,
        only_favorited: bool = True,
        limit: int = 20,
        force: bool = False,
    ) -> list[TLDR]:
        """Batch generate TL;DRs for articles.

        Args:
            only_favorited: If True, only generate for favorited articles.
            limit: Maximum number to generate.
            force: If True, regenerate even if TL;DR exists.

        Returns:
            List of generated TL;DRs.

        """
        conn = self._get_conn()
        try:
            self._ensure_table(conn)

            # Find articles needing TL;DR
            query = """
                SELECT a.id FROM articles a
                WHERE a.content_text IS NOT NULL AND length(a.content_text) > 200
            """
            params: list[Any] = []

            if only_favorited:
                query += " AND a.favorited = 1"

            if not force:
                query += " AND a.id NOT IN (SELECT article_id FROM article_tldrs)"

            query += " ORDER BY a.created_at DESC LIMIT ?"
            params.append(limit)

            rows = conn.execute(query, params).fetchall()
        finally:
            conn.close()

        results = []
        for row in rows:
            tldr = self.generate_tldr(row["id"], force=force)
            if tldr is not None:
                results.append(tldr)

        logger.info("Batch generated %d TL;DRs", len(results))
        return results

    def get_tldr(self, article_id: int) -> TLDR | None:
        """Get a cached TL;DR for an article."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            row = conn.execute(
                "SELECT * FROM article_tldrs WHERE article_id = ?", (article_id,)
            ).fetchone()
            return self._row_to_tldr(row) if row else None
        finally:
            conn.close()

    def list_tldrs(self, *, limit: int = 50, source_type: str | None = None) -> list[TLDR]:
        """List all cached TL;DRs."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            query = "SELECT * FROM article_tldrs"
            params: list[Any] = []
            if source_type:
                query += " WHERE source_type = ?"
                params.append(source_type)
            query += " ORDER BY generated_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_tldr(row) for row in rows]
        finally:
            conn.close()

    def delete_tldr(self, article_id: int) -> bool:
        """Delete a cached TL;DR."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            cursor = conn.execute("DELETE FROM article_tldrs WHERE article_id = ?", (article_id,))
            conn.commit()
            return cursor.rowcount > 0
        finally:
            conn.close()

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _load_article(self, article_id: int) -> dict[str, Any] | None:
        """Load an article from the database."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                """
                SELECT id, title, url, source_type, content_text, ai_summary, tags, author
                FROM articles WHERE id = ?
                """,
                (article_id,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def _count_words(self, text: str) -> int:
        """Count words in text (handles both English and Chinese)."""
        import re

        # English words
        english_words = len(re.findall(r"[a-zA-Z]+", text))
        # Chinese characters (each counts as ~1 word)
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        return english_words + chinese_chars

    def _estimate_reading_time(self, text: str, word_count: int | None = None) -> int:
        """Estimate reading time in minutes.

        Uses 200 wpm for English, 400 cpm for Chinese (Cruxwire approach).
        """
        import re

        if word_count is None:
            word_count = self._count_words(text)

        # Mixed content: use average speed
        english_words = len(re.findall(r"[a-zA-Z]+", text))
        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))

        if english_words + chinese_chars == 0:
            return max(1, word_count // self.WORDS_PER_MINUTE)

        english_minutes = english_words / self.WORDS_PER_MINUTE
        chinese_minutes = chinese_chars / self.CHARS_PER_MINUTE
        total_minutes = english_minutes + chinese_minutes

        return max(1, round(total_minutes))

    def _generate_with_llm(self, *, title: str, content: str, source_type: str) -> dict[str, Any]:
        """Generate TL;DR using LLM.

        Returns dict with key_points, conclusion, model.
        """
        from openbiliclaw.llm.generation import generate_structured
        from openbiliclaw.self_evolution.insight_report import _run_async

        system_instruction = (
            "你是一个内容摘要专家。请为给定的文章生成简洁的TL;DR（太长不看版）摘要。\n"
            "要求：\n"
            "1. 提取3-5个核心要点，每个要点不超过50字，用数字编号\n"
            "2. 最后给出一句话结论（不超过30字），概括文章最核心的观点\n"
            "3. 只基于文章内容，不要编造或添加外部信息\n"
            "4. 如果文章是技术内容，要点要包含关键技术概念和结论\n"
            "5. 如果文章是经验分享，要点要包含可操作的建议\n"
            "返回格式：\n"
            "要点：\n"
            "1. ...\n"
            "2. ...\n"
            "3. ...\n"
            "结论：..."
        )

        user_input = f"文章标题：{title}\n来源平台：{source_type}\n文章内容：\n{content[:6000]}"

        result = _run_async(
            generate_structured(
                self.llm_service,
                system_instruction=system_instruction,
                user_input=user_input,
                parse=lambda x: x,
                label="tldr_generation",
                temperature=0.3,
                max_tokens=800,
            )
        )

        text = str(result).strip()
        return self._parse_tldr_response(text)

    def _parse_tldr_response(self, text: str) -> dict[str, Any]:
        """Parse LLM response into key_points and conclusion."""
        import re

        key_points: list[str] = []
        conclusion = ""

        # Try to find conclusion
        conclusion_match = re.search(
            r"(?:结论|总结|一句话|核心观点)[:：]\s*(.+?)(?:\n|$)", text, re.IGNORECASE
        )
        if conclusion_match:
            conclusion = conclusion_match.group(1).strip().rstrip("。.")

        # Find numbered points
        # Pattern: "1. xxx" or "1、xxx" or "1) xxx"
        point_pattern = re.compile(r"^\s*\d+[.、)）]\s*(.+?)$", re.MULTILINE)
        for match in point_pattern.finditer(text):
            point = match.group(1).strip().rstrip("。.")
            # Skip if it looks like the conclusion line
            if conclusion and point.startswith(conclusion[:10]):
                continue
            if len(point) > 5 and point not in key_points:
                key_points.append(point[:80])  # Truncate long points

        # If no numbered points found, try bullet points
        if not key_points:
            bullet_pattern = re.compile(r"^\s*[-*•]\s*(.+?)$", re.MULTILINE)
            for match in bullet_pattern.finditer(text):
                point = match.group(1).strip().rstrip("。.")
                if len(point) > 5 and point not in key_points:
                    key_points.append(point[:80])

        # If still no points, split by newlines
        if not key_points:
            lines = [
                line.strip().rstrip("。.")
                for line in text.split("\n")
                if line.strip()
                and len(line.strip()) > 10
                and not line.strip().startswith(("结论", "总结", "要点", "文章"))
            ]
            key_points = lines[:5]

        # Fallback conclusion
        if not conclusion and key_points:
            conclusion = key_points[0][:30]

        return {
            "key_points": key_points[:5],
            "conclusion": conclusion,
            "model": "llm",
        }

    def _extractive_fallback(self, content: str, title: str) -> list[str]:
        """Fallback: extractive summary using sentence scoring.

        Simple algorithm: score sentences by word frequency, pick top sentences.
        """
        import re
        from collections import Counter

        # Split into sentences (Chinese and English)
        sentences = re.split(r"[。！？!?\n]+", content)
        sentences = [s.strip() for s in sentences if len(s.strip()) > 15]

        if not sentences:
            return [f"《{title[:30]}》内容较短，建议直接阅读原文"]

        # Word frequency (simple)
        words = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]+", content.lower())
        word_freq = Counter(words)
        # Remove very common words
        stopwords = {
            "的",
            "了",
            "是",
            "在",
            "我",
            "有",
            "和",
            "就",
            "不",
            "人",
            "都",
            "一",
            "一个",
            "上",
            "也",
            "很",
            "到",
            "说",
            "要",
            "去",
            "你",
            "会",
            "着",
            "没有",
            "看",
            "好",
            "自己",
            "这",
        }
        for sw in stopwords:
            word_freq.pop(sw, None)

        # Score sentences
        scored = []
        for i, sent in enumerate(sentences):
            sent_words = re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z]+", sent.lower())
            score = float(sum(word_freq.get(w, 0) for w in sent_words))
            # Position bonus: first and last sentences are more important
            if i == 0:
                score *= 1.3
            if i == len(sentences) - 1:
                score *= 1.1
            scored.append((score, sent))

        # Pick top 3-5 sentences in original order
        scored.sort(key=lambda x: -x[0])
        top = scored[:5]
        top.sort(key=lambda x: sentences.index(x[1]))

        return [sent[:80] for _, sent in top]

    def _save_tldr(self, tldr: TLDR) -> None:
        """Save a TL;DR to the database."""
        conn = self._get_conn()
        try:
            self._ensure_table(conn)
            key_points_json = json.dumps(tldr.key_points, ensure_ascii=False)
            conn.execute(
                """
                INSERT OR REPLACE INTO article_tldrs
                (article_id, title, url, source_type, key_points_json, conclusion,
                 reading_minutes, word_count, generated_at, model)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tldr.article_id,
                    tldr.title,
                    tldr.url,
                    tldr.source_type,
                    key_points_json,
                    tldr.conclusion,
                    tldr.reading_minutes,
                    tldr.word_count,
                    tldr.generated_at,
                    tldr.model,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _row_to_tldr(self, row: sqlite3.Row) -> TLDR:
        """Convert a database row to a TLDR object."""
        key_points = json.loads(row["key_points_json"] or "[]")
        return TLDR(
            article_id=row["article_id"],
            title=row["title"],
            url=row["url"],
            source_type=row["source_type"],
            key_points=key_points,
            conclusion=row["conclusion"],
            reading_minutes=row["reading_minutes"],
            word_count=row["word_count"],
            generated_at=row["generated_at"],
            model=row["model"],
        )
