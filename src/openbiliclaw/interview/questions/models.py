"""面试题阅读追踪系统数据模型。"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel


class MasteryLevel(StrEnum):
    """掌握程度。"""
    NOT_STARTED = "not_started"  # 未开始
    READING = "reading"  # 阅读中
    UNDERSTOOD = "understood"  # 理解了
    MASTERED = "mastered"  # 掌握了
    NEED_REVIEW = "need_review"  # 需要复习


class QuestionCategory(StrEnum):
    """题目分类。"""
    RECOMMENDATION = "recommendation"  # 推荐算法
    LLM_ENGINEERING = "llm_engineering"  # 大模型工程
    AGENT = "agent"  # Agent
    MACHINE_LEARNING = "machine_learning"  # 机器学习基础
    SYSTEM_DESIGN = "system_design"  # 系统设计
    CODING = "coding"  # 手撕代码
    OTHER = "other"  # 其他


class Priority(StrEnum):
    """优先级。"""
    HIGH = "high"  # 高
    MEDIUM = "medium"  # 中
    LOW = "low"  # 低


class Question(BaseModel):
    """面试题。"""
    id: int
    title: str  # 题目标题/问题
    answer: str = ""  # 答案要点
    category: QuestionCategory = QuestionCategory.OTHER
    difficulty: int = 3  # 难度 1-5
    source: str = ""  # 来源（公司/笔记/书籍）
    tags: str = ""  # 标签，逗号分隔
    url: str = ""  # 原始链接
    notes: str = ""  # 个人笔记
    created_at: datetime
    updated_at: datetime


class QuestionCreate(BaseModel):
    """创建面试题。"""
    title: str
    answer: str = ""
    category: QuestionCategory = QuestionCategory.OTHER
    difficulty: int = 3
    source: str = ""
    tags: str = ""
    url: str = ""
    notes: str = ""


class ReadingRecord(BaseModel):
    """阅读记录。"""
    id: int
    question_id: int
    read_date: date  # 阅读日期
    mastery: MasteryLevel = MasteryLevel.READING  # 掌握程度
    review_count: int = 0  # 复习次数
    last_reviewed: datetime | None = None  # 最后复习时间
    notes: str = ""  # 阅读笔记
    time_spent_min: int = 0  # 花费时间（分钟）
    created_at: datetime


class ReadingPlan(BaseModel):
    """阅读计划。"""
    id: int
    name: str  # 计划名称
    start_date: date  # 开始日期
    end_date: date | None = None  # 结束日期
    daily_target: int = 5  # 每日目标题数
    categories: str = ""  # 目标分类，逗号分隔（空=全部）
    min_difficulty: int = 1  # 最低难度
    max_difficulty: int = 5  # 最高难度
    is_active: bool = True  # 是否激活
    created_at: datetime


class DailyProgress(BaseModel):
    """每日完成情况。"""
    id: int
    plan_id: int
    progress_date: date  # 日期
    questions_read: int = 0  # 已读题数
    questions_mastered: int = 0  # 掌握题数
    target: int = 0  # 目标题数
    notes: str = ""  # 备注
    created_at: datetime


class QuestionStats(BaseModel):
    """题库统计。"""
    total: int = 0
    by_category: dict[str, int] = {}
    by_difficulty: dict[int, int] = {}
    by_mastery: dict[str, int] = {}
    not_started: int = 0
    reading: int = 0
    understood: int = 0
    mastered: int = 0
    need_review: int = 0
