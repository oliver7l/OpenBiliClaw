"""面试复盘记录业务逻辑层。

提供创建、查询、更新、删除、搜索、统计等能力，
以及从转录文件自动导入的功能。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .review_models import (
    InterviewReview,
    InterviewReviewCreate,
    InterviewReviewStats,
    InterviewReviewSummary,
    InterviewReviewUpdate,
)
from .review_store import InterviewReviewStore

logger = logging.getLogger(__name__)


class InterviewReviewService:
    """面试复盘记录业务逻辑。"""

    def __init__(self, db_path: str | Path) -> None:
        self.store = InterviewReviewStore(db_path)

    def create(self, data: InterviewReviewCreate) -> InterviewReview:
        """创建面试复盘记录。"""
        return self.store.create(data)

    def get(self, review_id: int) -> InterviewReview | None:
        """获取面试复盘详情。"""
        return self.store.get_by_id(review_id)

    def list_reviews(
        self,
        *,
        company: str | None = None,
        result: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InterviewReviewSummary]:
        """列出面试复盘记录。"""
        return self.store.list_reviews(
            company=company, result=result, limit=limit, offset=offset
        )

    def search(self, query: str, limit: int = 20) -> list[InterviewReviewSummary]:
        """全文检索面试复盘记录。"""
        if not query.strip():
            return []
        return self.store.search(query, limit=limit)

    def update(self, review_id: int, data: InterviewReviewUpdate) -> InterviewReview | None:
        """更新面试复盘记录。"""
        return self.store.update(review_id, data)

    def delete(self, review_id: int) -> bool:
        """删除面试复盘记录。"""
        return self.store.delete(review_id)

    def stats(self) -> InterviewReviewStats:
        """获取面试复盘统计。"""
        return self.store.stats()

    def get_companies(self) -> list[str]:
        """获取所有面试过的公司。"""
        return self.store.get_all_companies()

    def import_from_transcript(
        self,
        *,
        company: str,
        position: str,
        interview_date: Any,
        round_value: str = "first",
        result: str = "cancelled",
        duration_min: int = 18,
        transcript_path: str = "",
        audio_path: str = "",
        ai_evaluation: str = "",
        transcript_text: str = "",
        emotional_review: str = "",
        technical_review: str = "",
        key_questions: str = "",
        action_items: str = "",
        emotion_level: str = "frustrated",
        tags: str = "",
        notes: str = "",
    ) -> InterviewReview:
        """从转录文件导入面试复盘记录。"""
        # 如果有转录文件路径，读取内容
        if transcript_path and not transcript_text:
            p = Path(transcript_path)
            if p.exists():
                transcript_text = p.read_text(encoding="utf-8", errors="replace")

        data = InterviewReviewCreate(
            company=company,
            position=position,
            interview_date=interview_date,
            round=round_value,
            result=result,
            duration_min=duration_min,
            transcript_text=transcript_text,
            transcript_path=transcript_path,
            audio_path=audio_path,
            ai_evaluation=ai_evaluation,
            key_questions=key_questions,
            self_assessment="",
            emotional_review=emotional_review,
            technical_review=technical_review,
            action_items=action_items,
            emotion_level=emotion_level,
            tags=tags,
            notes=notes,
        )
        return self.store.create(data)
