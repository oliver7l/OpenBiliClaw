"""面试复盘记录数据模型。

参考日记模块的 models.py，使用 Pydantic 定义面试复盘的核心数据结构。
支持转录、AI评价、情绪复盘、技术复盘、行动计划等维度的结构化记录。
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InterviewResult(str, Enum):
    """面试结果。"""

    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    CANDIDATE_CANCELLED = "cancelled"  # 候选人主动终止
    NO_OFFER = "no_offer"


class InterviewRound(str, Enum):
    """面试轮次。"""

    PHONE_SCREEN = "phone_screen"
    FIRST = "first"
    SECOND = "second"
    THIRD = "third"
    HR = "hr"
    FINAL = "final"
    ONSITE = "onsite"


class EmotionLevel(str, Enum):
    """面试时情绪状态。"""

    CALM = "calm"
    NERVOUS = "nervous"
    FRUSTRATED = "frustrated"
    ANGRY = "angry"
    CONFIDENT = "confident"
    BURNOUT = "burnout"


class InterviewReviewCreate(BaseModel):
    """创建面试复盘记录。"""

    company: str = Field(..., min_length=1, max_length=200, description="公司名")
    position: str = Field(..., min_length=1, max_length=200, description="岗位名")
    interview_date: date = Field(..., description="面试日期")
    round: InterviewRound = Field(default=InterviewRound.FIRST, description="面试轮次")
    result: InterviewResult = Field(default=InterviewResult.PENDING, description="面试结果")
    duration_min: int = Field(default=0, ge=0, description="面试时长（分钟）")

    # 转录与附件
    transcript_text: str = Field(default="", description="面试逐字转录文本")
    transcript_path: str = Field(default="", description="转录文件路径")
    audio_path: str = Field(default="", description="音频文件路径")
    ai_evaluation: str = Field(default="", description="AI 生成的面试评价")

    # 复盘维度
    key_questions: str = Field(default="", description="被问要点（分号分隔）")
    self_assessment: str = Field(default="", description="自我评估")
    emotional_review: str = Field(default="", description="情绪复盘")
    technical_review: str = Field(default="", description="技术复盘")
    action_items: str = Field(default="", description="行动计划")

    # 状态
    emotion_level: EmotionLevel | None = Field(default=None, description="面试时情绪")
    tags: str = Field(default="", description="标签（逗号分隔）")
    notes: str = Field(default="", description="补充备注")


class InterviewReviewUpdate(BaseModel):
    """更新面试复盘记录（所有字段可选）。"""

    position: str | None = None
    round: InterviewRound | None = None
    result: InterviewResult | None = None
    duration_min: int | None = None
    transcript_text: str | None = None
    transcript_path: str | None = None
    audio_path: str | None = None
    ai_evaluation: str | None = None
    key_questions: str | None = None
    self_assessment: str | None = None
    emotional_review: str | None = None
    technical_review: str | None = None
    action_items: str | None = None
    emotion_level: EmotionLevel | None = None
    tags: str | None = None
    notes: str | None = None


class InterviewReview(BaseModel):
    """面试复盘记录完整模型。"""

    id: int
    company: str
    position: str
    interview_date: date
    round: InterviewRound
    result: InterviewResult
    duration_min: int = 0
    transcript_text: str = ""
    transcript_path: str = ""
    audio_path: str = ""
    ai_evaluation: str = ""
    key_questions: str = ""
    self_assessment: str = ""
    emotional_review: str = ""
    technical_review: str = ""
    action_items: str = ""
    emotion_level: EmotionLevel | None = None
    tags: str = ""
    notes: str = ""
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, Any]:
        """转为字典（用于存储层）。"""
        return {
            "company": self.company,
            "position": self.position,
            "interview_date": self.interview_date.isoformat(),
            "round": self.round.value,
            "result": self.result.value,
            "duration_min": self.duration_min,
            "transcript_text": self.transcript_text,
            "transcript_path": self.transcript_path,
            "audio_path": self.audio_path,
            "ai_evaluation": self.ai_evaluation,
            "key_questions": self.key_questions,
            "self_assessment": self.self_assessment,
            "emotional_review": self.emotional_review,
            "technical_review": self.technical_review,
            "action_items": self.action_items,
            "emotion_level": self.emotion_level.value if self.emotion_level else "",
            "tags": self.tags,
            "notes": self.notes,
        }


class InterviewReviewSummary(BaseModel):
    """面试复盘列表摘要（不含转录等长文本）。"""

    id: int
    company: str
    position: str
    interview_date: date
    round: InterviewRound
    result: InterviewResult
    duration_min: int = 0
    emotion_level: EmotionLevel | None = None
    tags: str = ""
    created_at: datetime


class InterviewReviewStats(BaseModel):
    """面试复盘统计。"""

    total: int = 0
    by_result: dict[str, int] = Field(default_factory=dict)
    by_company: dict[str, int] = Field(default_factory=dict)
    by_emotion: dict[str, int] = Field(default_factory=dict)
    avg_duration: float = 0.0
    recent_count: int = 0  # 最近30天
