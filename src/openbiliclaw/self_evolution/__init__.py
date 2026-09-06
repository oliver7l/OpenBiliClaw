"""Self-evolution module: auto-generate insights, detect interest drift, mine topics, generate knowledge cards, build knowledge graph, and proactively push valuable content."""

from openbiliclaw.self_evolution.insight_report import (
    InsightReport,
    InsightReportGenerator,
    PlatformStats,
    TopicStats,
    InterestDrift,
    DeepDiveCandidate,
    extract_topics,
    infer_platform_from_url,
)
from openbiliclaw.self_evolution.interest_drift import (
    DriftReport,
    InterestDriftDetector,
    PlatformDrift,
    TopicDrift,
)
from openbiliclaw.self_evolution.topic_miner import (
    MiningReport,
    TopicCandidate,
    TopicMiner,
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
    PushConfig,
    PushNotification,
    ProactivePushEngine,
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
    # Proactive push
    "PushConfig",
    "PushNotification",
    "ProactivePushEngine",
]
