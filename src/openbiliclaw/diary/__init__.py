"""日记系统：记录、存储、分析与回顾个人日记。

支持多来源日记导入（纯文本、Markdown）、LLM 驱动的情绪与主题分析、
时间线浏览、关键词检索，以及与灵魂画像系统的联动。
"""

from __future__ import annotations

from .insights import (
    DiaryInsightsService,
    MoodAnalyzer,
    MoodTrendPoint,
    WritingStreak,
    YearlyInsight,
)
from .knowledge_graph import (
    GraphEdge,
    GraphNode,
    KnowledgeGraph,
    KnowledgeGraphService,
    KnowledgeNodeDetail,
    PersonRelation,
)
from .models import (
    DiaryAnalysis,
    DiaryEntry,
    DiaryEntryCreate,
    DiaryEntryUpdate,
    DiaryFragment,
    DiaryFragmentCreate,
    DiaryPerson,
    DiaryPersonDetail,
    DiaryStats,
    DiaryTag,
    ExtractionResult,
    MoodLevel,
    TagType,
)
from .rag import DiaryRAGService, RAGAnswer, SearchResult
from .reflection import (
    Milestone,
    MonthlyReflection,
    ReflectionService,
    WeeklyReport,
    YearlyReview,
)
from .service import DiaryService

__all__ = [
    "DiaryAnalysis",
    "DiaryEntry",
    "DiaryEntryCreate",
    "DiaryEntryUpdate",
    "DiaryFragment",
    "DiaryFragmentCreate",
    "DiaryInsightsService",
    "DiaryPerson",
    "DiaryPersonDetail",
    "DiaryRAGService",
    "DiaryService",
    "DiaryStats",
    "DiaryTag",
    "ExtractionResult",
    "GraphEdge",
    "GraphNode",
    "KnowledgeGraph",
    "KnowledgeGraphService",
    "KnowledgeNodeDetail",
    "Milestone",
    "MoodAnalyzer",
    "MoodLevel",
    "MoodTrendPoint",
    "MonthlyReflection",
    "PersonRelation",
    "RAGAnswer",
    "ReflectionService",
    "SearchResult",
    "TagType",
    "WeeklyReport",
    "WritingStreak",
    "YearlyInsight",
    "YearlyReview",
]
