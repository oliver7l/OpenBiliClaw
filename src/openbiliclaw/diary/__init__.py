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
from .insight_engine import (
    InsightEngineService,
    InsightReport,
    MemoryOnThisDay,
    MorningBriefing,
    OpenLoop,
    PatternInsight,
)
from .memory_system import (
    MemoryCompressionResult,
    MemoryEntry,
    MemoryStats,
    MemorySystemService,
)
from .reflection import (
    Milestone,
    MonthlyReflection,
    ReflectionService,
    WeeklyReport,
    YearlyReview,
)
from .self_evolution import (
    DriftEvent,
    NightlyLog,
    SelfEvolutionService,
    TagOptimization,
    UserProfile,
)
from .emotion import (
    BurnoutAssessment,
    EmotionAnalyzer,
    EmotionForecast,
    EmotionTrendPoint,
    ValenceArousal,
)
from .advanced_memory import (
    AdvancedMemoryService,
    Belief,
    BeliefConflict,
    ConsolidationResult,
    DreamStateReview,
    MemoryLayer,
)
from .timeline import (
    TimelineCard,
    TimelineService,
    TimelineStats,
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
    "DriftEvent",
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
    "NightlyLog",
    "PersonRelation",
    "RAGAnswer",
    "ReflectionService",
    "SearchResult",
    "SelfEvolutionService",
    "TagOptimization",
    "TagType",
    "WeeklyReport",
    "WritingStreak",
    "YearlyInsight",
    "YearlyReview",
    "UserProfile",
    # 情绪系统
    "EmotionAnalyzer",
    "ValenceArousal",
    "EmotionTrendPoint",
    "EmotionForecast",
    "BurnoutAssessment",
    # 高级记忆系统
    "AdvancedMemoryService",
    "MemoryLayer",
    "Belief",
    "BeliefConflict",
    "ConsolidationResult",
    "DreamStateReview",
    # 智能时间线
    "TimelineService",
    "TimelineCard",
    "TimelineStats",
]
