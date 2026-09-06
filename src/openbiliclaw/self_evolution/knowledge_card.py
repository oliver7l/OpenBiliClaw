"""Knowledge card generation module.

Extracts key insights from user's favorited / deeply-read content and
generates reviewable knowledge cards.  Provides:
- Key point extraction from articles
- Knowledge card generation (Q&A, concept, summary types)
- Spaced repetition scheduling (SM-2 algorithm)
- Card categorization and tagging
- Review session generation
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

logger = logging.getLogger("self_evolution.knowledge_card")


def _run_async(coro: Any) -> Any:
    """Run an async coroutine from sync or async context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeCard:
    """A single knowledge card for review."""

    card_id: str
    source_article_id: int | None
    source_title: str
    source_url: str
    card_type: str  # "qa" | "concept" | "summary" | "action"
    front: str  # question / concept name
    back: str  # answer / explanation
    tags: list[str] = field(default_factory=list)
    difficulty: float = 0.5  # 0-1, higher = harder
    quality: float = 0.5  # 0-1, card quality score
    # Spaced repetition fields (SM-2)
    ease_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    next_review: str = ""
    last_reviewed: str = ""
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "source_article_id": self.source_article_id,
            "source_title": self.source_title,
            "source_url": self.source_url,
            "card_type": self.card_type,
            "front": self.front,
            "back": self.back,
            "tags": self.tags,
            "difficulty": self.difficulty,
            "quality": self.quality,
            "ease_factor": self.ease_factor,
            "interval_days": self.interval_days,
            "repetitions": self.repetitions,
            "next_review": self.next_review,
            "last_reviewed": self.last_reviewed,
            "created_at": self.created_at,
        }


@dataclass
class ReviewSession:
    """A review session with cards due for review."""

    session_id: str
    generated_at: str
    cards: list[KnowledgeCard] = field(default_factory=list)
    total_cards: int = 0
    estimated_minutes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "generated_at": self.generated_at,
            "cards": [c.to_dict() for c in self.cards],
            "total_cards": self.total_cards,
            "estimated_minutes": self.estimated_minutes,
        }


# ---------------------------------------------------------------------------
# SM-2 Spaced Repetition Algorithm
# ---------------------------------------------------------------------------


class SM2Scheduler:
    """SM-2 spaced repetition scheduler.

    Based on the SuperMemo 2 algorithm:
    - quality: 0-5 (how well the user remembered)
    - ease_factor: starts at 2.5, minimum 1.3
    - interval: increases with successful repetitions
    """

    MIN_EASE = 1.3
    QUALITY_THRESHOLD = 3  # quality >= 3 = correct

    def schedule(self, card: KnowledgeCard, quality: int) -> KnowledgeCard:
        """Update card scheduling based on review quality.

        Args:
            card: The card being reviewed.
            quality: Quality of recall (0-5, 5 = perfect).

        Returns:
            The updated card.
        """
        quality = max(0, min(5, quality))
        now = datetime.now()

        if quality < self.QUALITY_THRESHOLD:
            # Failed review: reset repetitions, short interval
            card.repetitions = 0
            card.interval_days = 1
        else:
            card.repetitions += 1
            if card.repetitions == 1:
                card.interval_days = 1
            elif card.repetitions == 2:
                card.interval_days = 6
            else:
                card.interval_days = math.ceil(card.interval_days * card.ease_factor)

        # Update ease factor
        card.ease_factor = card.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        card.ease_factor = max(self.MIN_EASE, card.ease_factor)

        # Update review dates
        card.last_reviewed = now.isoformat()
        card.next_review = (now + timedelta(days=card.interval_days)).isoformat()

        return card


# ---------------------------------------------------------------------------
# Card Generator
# ---------------------------------------------------------------------------


class KnowledgeCardGenerator:
    """Generate knowledge cards from articles.

    Args:
        db_path: Path to the SQLite database.
        llm_service: Optional LLM service for card generation.
    """

    def __init__(self, db_path: str, *, llm_service: Any | None = None) -> None:
        self.db_path = db_path
        self.llm_service = llm_service
        self.scheduler = SM2Scheduler()

    def _get_conn(self) -> Any:
        import sqlite3

        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def generate_cards_from_article(
        self,
        article_id: int,
        *,
        max_cards: int = 5,
        card_types: list[str] | None = None,
    ) -> list[KnowledgeCard]:
        """Generate knowledge cards from a single article.

        Args:
            article_id: ID of the article to generate cards from.
            max_cards: Maximum number of cards to generate.
            card_types: Types of cards to generate (default: all).

        Returns:
            List of generated KnowledgeCard objects.
        """
        if card_types is None:
            card_types = ["qa", "concept", "summary", "action"]

        conn = self._get_conn()
        try:
            article = conn.execute(
                "SELECT id, title, url, source_type, tags, content_text, ai_summary FROM articles WHERE id = ?",
                (article_id,),
            ).fetchone()

            if not article:
                logger.warning("Article not found: %d", article_id)
                return []

            content = article["content_text"] or ""
            summary = article["ai_summary"] or ""
            title = article["title"] or ""

            # Use LLM if available, otherwise use heuristic extraction
            if self.llm_service is not None:
                cards = self._generate_cards_with_llm(
                    article=dict(article),
                    max_cards=max_cards,
                    card_types=card_types,
                )
            else:
                cards = self._generate_cards_heuristic(
                    article=dict(article),
                    max_cards=max_cards,
                    card_types=card_types,
                )

            # Save cards to database
            for card in cards:
                self._save_card(card)

            return cards

        finally:
            conn.close()

    def generate_cards_batch(
        self,
        *,
        article_ids: list[int] | None = None,
        limit: int = 20,
        max_cards_per_article: int = 3,
        only_favorited: bool = True,
    ) -> list[KnowledgeCard]:
        """Generate cards from multiple articles.

        Args:
            article_ids: Specific article IDs (default: auto-select).
            limit: Maximum articles to process.
            max_cards_per_article: Cards per article.
            only_favorited: Only process favorited articles.

        Returns:
            List of all generated cards.
        """
        conn = self._get_conn()
        try:
            if article_ids is None:
                # Auto-select: favorited, high-quality, long articles
                query = """
                    SELECT a.id FROM articles a
                    WHERE a.content_text IS NOT NULL AND length(a.content_text) > 500
                """
                params: list[Any] = []

                if only_favorited:
                    query += " AND a.id IN (SELECT article_id FROM events WHERE event_type = 'favorite')"

                query += " ORDER BY length(a.content_text) DESC LIMIT ?"
                params.append(limit)

                rows = conn.execute(query, params).fetchall()
                article_ids = [r["id"] for r in rows]

            all_cards: list[KnowledgeCard] = []
            for aid in article_ids:
                cards = self.generate_cards_from_article(aid, max_cards=max_cards_per_article)
                all_cards.extend(cards)

            return all_cards

        finally:
            conn.close()

    def _generate_cards_with_llm(
        self,
        *,
        article: dict[str, Any],
        max_cards: int,
        card_types: list[str],
    ) -> list[KnowledgeCard]:
        """Generate cards using LLM."""
        from openbiliclaw.llm.generation import generate_structured
        import json as json_mod

        content = (article.get("content_text") or "")[:3000]
        summary = article.get("ai_summary") or ""
        title = article.get("title") or ""

        system_instruction = (
            f"你是一个知识卡片生成器。从下面的文章中提取{max_cards}个最重要的知识点，"
            f"生成可复习的知识卡片。卡片类型包括：{', '.join(card_types)}。\n"
            "输出JSON数组，每个卡片包含：type(qa/concept/summary/action), front(问题/概念名), "
            "back(答案/解释), tags(标签数组), difficulty(0-1难度)。\n"
            "要求：1. 只基于文章内容，不要编造；2. 卡片要简洁，front不超过50字，back不超过200字；"
            "3. 优先提取核心概念、关键数据、可操作的建议。"
        )

        user_input = f"文章标题：{title}\n文章摘要：{summary}\n文章内容：{content}"

        try:
            result = _run_async(generate_structured(
                self.llm_service,
                system_instruction=system_instruction,
                user_input=user_input,
                parse=lambda x: json_mod.loads(x) if isinstance(x, str) else x,
                label="knowledge_card_generation",
                temperature=0.3,
                max_tokens=1000,
            ))

            cards: list[KnowledgeCard] = []
            if isinstance(result, list):
                for i, item in enumerate(result[:max_cards]):
                    card = KnowledgeCard(
                        card_id=f"card-{article['id']}-{i}-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                        source_article_id=article["id"],
                        source_title=title,
                        source_url=article.get("url") or "",
                        card_type=item.get("type", "concept"),
                        front=item.get("front", "")[:100],
                        back=item.get("back", "")[:500],
                        tags=item.get("tags", [])[:5],
                        difficulty=float(item.get("difficulty", 0.5)),
                        quality=0.7,
                        created_at=datetime.now().isoformat(),
                        next_review=datetime.now().isoformat(),
                    )
                    cards.append(card)

            return cards

        except Exception:
            logger.exception("LLM card generation failed, falling back to heuristic")
            return self._generate_cards_heuristic(
                article=article, max_cards=max_cards, card_types=card_types
            )

    def _generate_cards_heuristic(
        self,
        *,
        article: dict[str, Any],
        max_cards: int,
        card_types: list[str],
    ) -> list[KnowledgeCard]:
        """Generate cards using heuristic extraction (no LLM)."""
        import hashlib

        title = article.get("title") or "无标题"
        summary = article.get("ai_summary") or ""
        content = article.get("content_text") or ""
        tags = (article.get("tags") or "").split(",")[:5]

        cards: list[KnowledgeCard] = []

        # Summary card
        if "summary" in card_types and summary:
            cards.append(
                KnowledgeCard(
                    card_id=hashlib.md5(f"{article['id']}-summary".encode()).hexdigest()[:12],
                    source_article_id=article["id"],
                    source_title=title,
                    source_url=article.get("url") or "",
                    card_type="summary",
                    front=f"《{title[:30]}》的核心内容是什么？",
                    back=summary[:300],
                    tags=tags,
                    difficulty=0.3,
                    quality=0.6,
                    created_at=datetime.now().isoformat(),
                    next_review=datetime.now().isoformat(),
                )
            )

        # Concept cards: extract from key sentences
        if "concept" in card_types and content:
            # Find sentences with key terms
            key_terms = ["定义", "概念", "原理", "机制", "算法", "模型", "方法", "框架", "体系"]
            sentences = [s.strip() for s in content.replace("。", "。\n").split("\n") if s.strip()]

            concept_count = 0
            for sent in sentences:
                if any(term in sent for term in key_terms) and len(sent) > 20 and concept_count < max_cards:
                    # Extract concept name (simplified)
                    concept_name = sent[:30] + "..." if len(sent) > 30 else sent
                    cards.append(
                        KnowledgeCard(
                            card_id=hashlib.md5(f"{article['id']}-concept-{concept_count}".encode()).hexdigest()[:12],
                            source_article_id=article["id"],
                            source_title=title,
                            source_url=article.get("url") or "",
                            card_type="concept",
                            front=f"什么是{concept_name}？",
                            back=sent[:200],
                            tags=tags,
                            difficulty=0.5,
                            quality=0.5,
                            created_at=datetime.now().isoformat(),
                            next_review=datetime.now().isoformat(),
                        )
                    )
                    concept_count += 1

        # Action cards: extract actionable advice
        if "action" in card_types and content:
            action_terms = ["应该", "需要", "可以", "建议", "步骤", "方法", "技巧", "注意"]
            for sent in sentences:
                if any(term in sent for term in action_terms) and len(sent) > 15:
                    cards.append(
                        KnowledgeCard(
                            card_id=hashlib.md5(f"{article['id']}-action".encode()).hexdigest()[:12],
                            source_article_id=article["id"],
                            source_title=title,
                            source_url=article.get("url") or "",
                            card_type="action",
                            front="这篇文章有什么可操作的建议？",
                            back=sent[:200],
                            tags=tags,
                            difficulty=0.4,
                            quality=0.5,
                            created_at=datetime.now().isoformat(),
                            next_review=datetime.now().isoformat(),
                        )
                    )
                    break

        return cards[:max_cards]

    def get_due_cards(self, *, limit: int = 20) -> list[KnowledgeCard]:
        """Get cards due for review."""
        conn = self._get_conn()
        try:
            now = datetime.now().isoformat()
            rows = conn.execute(
                """
                SELECT * FROM knowledge_cards
                WHERE next_review <= ? OR next_review IS NULL OR next_review = ''
                ORDER BY next_review ASC
                LIMIT ?
                """,
                (now, limit),
            ).fetchall()

            return [self._row_to_card(row) for row in rows]
        finally:
            conn.close()

    def create_review_session(self, *, limit: int = 20) -> ReviewSession:
        """Create a review session with due cards."""
        cards = self.get_due_cards(limit=limit)
        session = ReviewSession(
            session_id=f"review-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            generated_at=datetime.now().isoformat(),
            cards=cards,
            total_cards=len(cards),
            estimated_minutes=max(1, len(cards) * 2),  # ~2 min per card
        )
        return session

    def record_review(self, card_id: str, quality: int) -> KnowledgeCard | None:
        """Record a review and update scheduling."""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT * FROM knowledge_cards WHERE card_id = ?",
                (card_id,),
            ).fetchone()

            if not row:
                return None

            card = self._row_to_card(row)
            card = self.scheduler.schedule(card, quality)

            # Update in database
            conn.execute(
                """
                UPDATE knowledge_cards
                SET ease_factor = ?, interval_days = ?, repetitions = ?,
                    next_review = ?, last_reviewed = ?
                WHERE card_id = ?
                """,
                (
                    card.ease_factor,
                    card.interval_days,
                    card.repetitions,
                    card.next_review,
                    card.last_reviewed,
                    card_id,
                ),
            )
            conn.commit()
            return card

        finally:
            conn.close()

    def _save_card(self, card: KnowledgeCard) -> None:
        """Save a card to the database."""
        import json as json_mod

        conn = self._get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS knowledge_cards (
                    card_id TEXT PRIMARY KEY,
                    source_article_id INTEGER,
                    source_title TEXT,
                    source_url TEXT,
                    card_type TEXT,
                    front TEXT,
                    back TEXT,
                    tags TEXT,
                    difficulty REAL,
                    quality REAL,
                    ease_factor REAL DEFAULT 2.5,
                    interval_days INTEGER DEFAULT 0,
                    repetitions INTEGER DEFAULT 0,
                    next_review TEXT,
                    last_reviewed TEXT,
                    created_at TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO knowledge_cards
                (card_id, source_article_id, source_title, source_url, card_type,
                 front, back, tags, difficulty, quality, ease_factor, interval_days,
                 repetitions, next_review, last_reviewed, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card.card_id,
                    card.source_article_id,
                    card.source_title,
                    card.source_url,
                    card.card_type,
                    card.front,
                    card.back,
                    json_mod.dumps(card.tags, ensure_ascii=False),
                    card.difficulty,
                    card.quality,
                    card.ease_factor,
                    card.interval_days,
                    card.repetitions,
                    card.next_review,
                    card.last_reviewed,
                    card.created_at,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _row_to_card(self, row: Any) -> KnowledgeCard:
        """Convert a database row to a KnowledgeCard."""
        import json as json_mod

        tags = []
        if row["tags"]:
            try:
                tags = json_mod.loads(row["tags"])
            except (json_mod.JSONDecodeError, TypeError):
                tags = [t.strip() for t in str(row["tags"]).split(",") if t.strip()]

        return KnowledgeCard(
            card_id=row["card_id"],
            source_article_id=row["source_article_id"],
            source_title=row["source_title"] or "",
            source_url=row["source_url"] or "",
            card_type=row["card_type"] or "concept",
            front=row["front"] or "",
            back=row["back"] or "",
            tags=tags,
            difficulty=row["difficulty"] or 0.5,
            quality=row["quality"] or 0.5,
            ease_factor=row["ease_factor"] or 2.5,
            interval_days=row["interval_days"] or 0,
            repetitions=row["repetitions"] or 0,
            next_review=row["next_review"] or "",
            last_reviewed=row["last_reviewed"] or "",
            created_at=row["created_at"] or "",
        )

    def list_cards(self, *, limit: int = 50, card_type: str | None = None) -> list[KnowledgeCard]:
        """List all cards, optionally filtered by type."""
        conn = self._get_conn()
        try:
            if card_type:
                rows = conn.execute(
                    "SELECT * FROM knowledge_cards WHERE card_type = ? ORDER BY created_at DESC LIMIT ?",
                    (card_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM knowledge_cards ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_card(row) for row in rows]
        finally:
            conn.close()
