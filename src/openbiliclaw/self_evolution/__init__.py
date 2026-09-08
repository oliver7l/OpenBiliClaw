"""Self-evolution module: auto-generate insights, detect interest drift, mine topics, generate knowledge cards, build knowledge graph, and proactively push valuable content."""  # noqa: E501

from openbiliclaw.self_evolution.insight_report import (
    DeepDiveCandidate,
    InsightReport,
    InsightReportGenerator,
    InterestDrift,
    PlatformStats,
    TopicStats,
    extract_topics,
    infer_platform_from_url,
)
from openbiliclaw.self_evolution.insights import (
    ContentInsightsAnalyzer,
    CrossPlatformInsight,
    InsightsReport,
    KnowledgeGap,
)
from openbiliclaw.self_evolution.interest_drift import (
    DriftReport,
    InterestDriftDetector,
    PlatformDrift,
    TopicDrift,
)
from openbiliclaw.self_evolution.knowledge_card import (
    KnowledgeCard,
    KnowledgeCardGenerator,
    ReviewSession,
    SM2Scheduler,
)
from openbiliclaw.self_evolution.knowledge_graph import (
    Entity,
    KnowledgeGraph,
    KnowledgeGraphBuilder,
    Relationship,
)
from openbiliclaw.self_evolution.learning_path import (
    LearningPath,
    LearningPathGenerator,
    PathStep,
)
from openbiliclaw.self_evolution.proactive_push import (
    ProactivePushEngine,
    PushConfig,
    PushNotification,
)
from openbiliclaw.self_evolution.tldr import (
    TLDR,
    TLDRGenerator,
)
from openbiliclaw.self_evolution.topic_miner import (
    MiningReport,
    TopicCandidate,
    TopicMiner,
)

__all__ = [
    # Insight report
    "InsightReport",
    "InsightReportGenerator",
    "PlatformStats",
    "TopicStats",
    "InterestDrift",
    "DeepDiveCandidate",
    "extract_topics",
    "infer_platform_from_url",
    # Interest drift
    "DriftReport",
    "InterestDriftDetector",
    "PlatformDrift",
    "TopicDrift",
    # Topic miner
    "MiningReport",
    "TopicCandidate",
    "TopicMiner",
    # Knowledge card
    "KnowledgeCard",
    "KnowledgeCardGenerator",
    "ReviewSession",
    "SM2Scheduler",
    # Knowledge graph
    "Entity",
    "KnowledgeGraph",
    "KnowledgeGraphBuilder",
    "Relationship",
    # Learning path
    "LearningPath",
    "LearningPathGenerator",
    "PathStep",
    # TL;DR
    "TLDR",
    "TLDRGenerator",
    # Content Insights
    "ContentInsightsAnalyzer",
    "CrossPlatformInsight",
    "InsightsReport",
    "KnowledgeGap",
    # Proactive push
    "PushConfig",
    "PushNotification",
    "ProactivePushEngine",
]
