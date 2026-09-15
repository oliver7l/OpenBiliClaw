"""日记系统：记录、存储、分析与回顾个人日记。

支持文件导入（纯文本 / Markdown）与**多来源导入**（苹果备忘录 / 有道云笔记 /
WPS 笔记，见 :mod:`openbiliclaw.diary.sources`）、LLM 驱动的情绪与主题分析、
时间线浏览、关键词检索，以及与灵魂画像系统的联动。
"""

from __future__ import annotations

from .advanced_memory import (
    AdvancedMemoryService,
    Belief,
    BeliefConflict,
    ConsolidationResult,
    DreamStateReview,
    MemoryLayer,
)
from .emotion import (
    BurnoutAssessment,
    EmotionAnalyzer,
    EmotionForecast,
    EmotionTrendPoint,
    ValenceArousal,
)
from .insight_engine import (
    InsightEngineService,
    InsightReport,
    MemoryOnThisDay,
    MorningBriefing,
    OpenLoop,
    PatternInsight,
)
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
from .memory_system import (
    MemoryCompressionResult,
    MemoryEntry,
    MemoryStats,
    MemorySystemService,
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
from .self_evolution import (
    DriftEvent,
    NightlyLog,
    SelfEvolutionService,
    TagOptimization,
    UserProfile,
)
from .service import DiaryService
from .timeline import (
    TimelineCard,
    TimelineService,
    TimelineStats,
)

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
    # 洞察引擎
    "InsightEngineService",
    "InsightReport",
    "MemoryOnThisDay",
    "MorningBriefing",
    "OpenLoop",
    "PatternInsight",
    # 记忆系统
    "MemoryCompressionResult",
    "MemoryEntry",
    "MemoryStats",
    "MemorySystemService",
]
