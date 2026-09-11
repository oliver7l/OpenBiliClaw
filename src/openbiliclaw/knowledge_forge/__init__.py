"""Knowledge Forge（知识锻造炉）。

将阅读库从「文章集合」升级为「自进化知识网络」：分层摘要、实体/概念聚合、
知识 Wiki、缺口分析、质量审计。

子模块（按 docs/knowledge-forge-design.md 实现）：
    - content_cleaner:   3.0 正文清理器（其他模块的基础，最先执行）
    - summary_engine:    3.1 分层摘要引擎
    - entity_extractor:  3.2 实体/概念提取器
    - wiki_builder:      3.3 知识 Wiki 构建器（阶段二）
    - gap_analyst:       3.4 知识缺口分析器（阶段二）
    - quality_auditor:   3.5 文章质量审计器

设计原则（文档 1.3）：
    - 渐进式：不改现有数据结构，新增字段和表，向后兼容
    - 低频异步：重计算异步执行
    - 可配置：阈值/策略/批大小均可配置
    - 可追溯：自动操作记录日志，支持回滚
"""

from __future__ import annotations

from .config import (
    AuditConfig,
    CleanerConfig,
    EntityConfig,
    GapConfig,
    KnowledgeForgeConfig,
    LLMProviderSpec,
    load_kf_config,
)
from .models import (
    ArticleQualityScore,
    AuditIssue,
    CleanResult,
    Entity,
    SummaryResult,
    VerifyResult,
)

__all__ = [
    "AuditConfig",
    "AuditIssue",
    "ArticleQualityScore",
    "CleanerConfig",
    "CleanResult",
    "Entity",
    "EntityConfig",
    "GapConfig",
    "KnowledgeForgeConfig",
    "LLMProviderSpec",
    "SummaryResult",
    "VerifyResult",
    "load_kf_config",
]

__version__ = "0.2.0"

# K5：向 storage 注册正文清洗器（本包导入时自注册，替代 storage 侧的
# 反向懒加载 import）。领域→基础设施方向，合法。
from openbiliclaw.storage._article_cleaning import register_content_cleaner  # noqa: E402

from .content_cleaner import ContentCleaner as _ContentCleaner  # noqa: E402

register_content_cleaner(_ContentCleaner)
