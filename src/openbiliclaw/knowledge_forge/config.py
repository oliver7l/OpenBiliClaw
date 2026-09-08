"""Knowledge Forge 模块配置。

读取 config.toml 的 [knowledge_forge] 段；未配置时回退到文档默认值。
Provider 选择遵循文档 7.1：主 provider + fallback provider，
且遵守「硅基流动只用 9B 以下免费模型」「智谱 glm-4-flash 免费兜底」等约束。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _load_raw() -> dict[str, Any]:
    """读取 config.toml 的 [knowledge_forge] 段，失败则返回空字典。"""
    try:
        from openbiliclaw.config import load_config

        cfg = load_config()
        raw = getattr(cfg, "raw", None) or {}
        kf = raw.get("knowledge_forge") or {}
        return kf if isinstance(kf, dict) else {}
    except Exception:  # noqa: BLE001 — 配置不可用时用默认值，不阻塞模块导入
        return {}


@dataclass
class LLMProviderSpec:
    """一个模块的 provider 选择：主 + 降级。"""

    provider: str = "openai"
    fallback: str = "zhipu"
    model: str = ""

    @classmethod
    def from_raw(
        cls, raw: dict[str, Any], default_provider: str, default_fallback: str
    ) -> LLMProviderSpec:
        return cls(
            provider=str(raw.get("provider", default_provider)),
            fallback=str(raw.get("fallback", default_fallback)),
            model=str(raw.get("model", "")),
        )


@dataclass
class CleanerConfig:
    """3.0 正文清理器配置（文档 3.0.6）。"""

    enabled: bool = True
    # HTML 清理
    remove_script_tags: bool = True
    remove_style_tags: bool = True
    remove_nav_footer: bool = True
    remove_links: bool = False
    decode_html_entities: bool = True
    # 平台特定
    zhihu_remove_comments: bool = True
    zhihu_remove_recommendations: bool = True
    xhs_remove_ads: bool = True
    xhs_remove_hashtags: bool = False
    bilibili_remove_danmaku: bool = True
    youtube_remove_description_links: bool = True
    # 通用
    compress_whitespace: bool = True
    remove_empty_lines: bool = True
    # 质量验证
    min_content_length: int = 200
    min_title_similarity: float = 0.3
    min_effective_sentences: int = 3
    # LLM 抽样检测
    llm_detection_enabled: bool = False
    llm_detection_sample_rate: float = 0.05

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> CleanerConfig:
        d = cls()
        for k in cls().__dict__:
            if k in raw:
                setattr(d, k, raw[k])
        return d


@dataclass
class EntityConfig:
    """3.2 实体/概念提取配置。"""

    extraction_enabled: bool = True
    concept_extraction_enabled: bool = True
    concept_max_per_article: int = 8
    entity_synonym_merge: bool = True
    min_article_count_for_page: int = 1  # 生成聚合页的最小文章数
    llm: LLMProviderSpec = field(default_factory=lambda: LLMProviderSpec("zhipu", "openai"))

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> EntityConfig:
        d = cls()
        for k in (
            "extraction_enabled",
            "concept_extraction_enabled",
            "concept_max_per_article",
            "entity_synonym_merge",
            "min_article_count_for_page",
        ):
            if k in raw:
                setattr(d, k, raw[k])
        d.llm = LLMProviderSpec.from_raw(raw.get("llm", {}) or {}, "zhipu", "openai")
        return d


@dataclass
class GapConfig:
    """3.4 缺口分析配置。"""

    schedule: str = "weekly"
    high_priority_threshold: int = 5
    time_decay_days: int = 90
    llm: LLMProviderSpec = field(default_factory=lambda: LLMProviderSpec("openai", "zhipu"))

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> GapConfig:
        d = cls()
        for k in ("schedule", "high_priority_threshold", "time_decay_days"):
            if k in raw:
                setattr(d, k, raw[k])
        d.llm = LLMProviderSpec.from_raw(raw.get("llm", {}) or {}, "openai", "zhipu")
        return d


@dataclass
class AuditConfig:
    """3.5 质量审计配置。"""

    schedule: str = "weekly"
    min_content_length: int = 200
    min_summary_length: int = 100
    simhash_threshold: float = 0.9
    dead_link_timeout: int = 10
    dead_link_concurrency: int = 5
    auto_fix_enabled: bool = False
    batch_size: int = 500
    llm: LLMProviderSpec = field(default_factory=lambda: LLMProviderSpec("zhipu", "zhipu"))

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> AuditConfig:
        d = cls()
        for k in (
            "schedule",
            "min_content_length",
            "min_summary_length",
            "simhash_threshold",
            "dead_link_timeout",
            "dead_link_concurrency",
            "auto_fix_enabled",
            "batch_size",
        ):
            if k in raw:
                setattr(d, k, raw[k])
        d.llm = LLMProviderSpec.from_raw(raw.get("llm", {}) or {}, "zhipu", "zhipu")
        return d


@dataclass
class ContradictionConfig:
    """3.3.3 观点矛盾检测配置。"""

    enabled: bool = True
    min_shared_tags: int = 1  # 共享标签数阈值（实际数据多为单标签，默认 1；组大小 ≤20 已防爆炸）
    confidence_threshold: float = 0.7  # confidence > 阈值标记为矛盾
    max_pairs_per_run: int = 100  # 单次运行最多检测的对数
    max_concurrent: int = 5  # LLM 并发数
    summary_chars: int = 800  # 输入 LLM 的摘要截断长度
    llm: LLMProviderSpec = field(default_factory=lambda: LLMProviderSpec("zhipu", "openai"))

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> ContradictionConfig:
        d = cls()
        for k in (
            "enabled",
            "min_shared_tags",
            "confidence_threshold",
            "max_pairs_per_run",
            "max_concurrent",
            "summary_chars",
        ):
            if k in raw:
                setattr(d, k, raw[k])
        d.llm = LLMProviderSpec.from_raw(raw.get("llm", {}) or {}, "zhipu", "openai")
        return d


@dataclass
class LowQualityConfig:
    """2.5 低质量内容检测配置。"""

    enabled: bool = True
    sample_ratio: float = 0.05  # 全库抽样比例
    max_samples: int = 100  # 单次最多抽样数
    llm: LLMProviderSpec = field(default_factory=lambda: LLMProviderSpec("zhipu", "openai"))

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> LowQualityConfig:
        d = cls()
        for k in ("enabled", "sample_ratio", "max_samples"):
            if k in raw:
                setattr(d, k, raw[k])
        d.llm = LLMProviderSpec.from_raw(raw.get("llm", {}) or {}, "zhipu", "openai")
        return d


@dataclass
class KnowledgeForgeConfig:
    """Knowledge Forge 总配置。"""

    enabled: bool = True
    content_cleaner: CleanerConfig = field(default_factory=CleanerConfig)
    summary: LLMProviderSpec = field(default_factory=lambda: LLMProviderSpec("openai", "zhipu"))
    summary_detailed_max_length: int = 5000
    summary_compact_max_length: int = 1000
    summary_ultra_compact_max_length: int = 200
    summary_quality_check: bool = True
    summary_batch_size: int = 50
    entity: EntityConfig = field(default_factory=EntityConfig)
    gap: GapConfig = field(default_factory=GapConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    contradiction: ContradictionConfig = field(default_factory=ContradictionConfig)
    low_quality: LowQualityConfig = field(default_factory=LowQualityConfig)
    # 降级与成本（文档 7.2 / 7.3）
    fallback_max_failures: int = 5
    fallback_cooldown_seconds: int = 300
    embedding_cache_days: int = 30

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> KnowledgeForgeConfig:
        d = cls()
        d.enabled = bool(raw.get("enabled", True))
        d.content_cleaner = CleanerConfig.from_raw(raw.get("content_cleaner") or {})
        d.summary = LLMProviderSpec.from_raw(raw.get("summary") or {}, "openai", "zhipu")
        for k in (
            "summary_detailed_max_length",
            "summary_compact_max_length",
            "summary_ultra_compact_max_length",
            "summary_quality_check",
            "summary_batch_size",
            "fallback_max_failures",
            "fallback_cooldown_seconds",
            "embedding_cache_days",
        ):
            if k in raw:
                setattr(d, k, raw[k])
        d.entity = EntityConfig.from_raw(raw.get("entity") or {})
        d.gap = GapConfig.from_raw(raw.get("gap") or {})
        d.audit = AuditConfig.from_raw(raw.get("audit") or {})
        d.contradiction = ContradictionConfig.from_raw(raw.get("contradiction") or {})
        d.low_quality = LowQualityConfig.from_raw(raw.get("low_quality") or {})
        return d


def load_kf_config() -> KnowledgeForgeConfig:
    """加载 [knowledge_forge] 配置段。"""
    return KnowledgeForgeConfig.from_raw(_load_raw())
