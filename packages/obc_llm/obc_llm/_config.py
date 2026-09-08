"""Config dataclasses for obc-llm.

These replace the dependency on openbiliclaw.config.Config,
providing a minimal interface that the main project maps its Config onto.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMProviderConfig:
    """Configuration for a single LLM provider."""

    api_key: str = ""
    base_url: str = ""
    model: str = ""
    auth_mode: str = ""
    reasoning_effort: str = ""
    num_ctx: int = 8192
    http_referer: str = ""
    x_title: str = ""


@dataclass
class EmbeddingConfig:
    """Configuration for embedding service."""

    provider: str = ""
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    similarity_threshold: float = 0.85
    fallback_provider: str = ""
    fallback_enabled: bool = False
    output_dimensionality: int = 1024


@dataclass
class ModuleLLMConfig:
    """Per-module LLM route override configuration."""

    provider: str = ""
    model: str = ""


@dataclass
class LLMConfig:
    """LLM configuration for obc-llm registry building.

    Main project maps its Config.llm onto this dataclass.
    """

    default_provider: str = "openai"
    fallback_provider: str = ""
    fallback_enabled: bool = False
    timeout: float = 60.0
    concurrency: int = 3

    # Per-provider configs
    openai: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    claude: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    gemini: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    deepseek: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    ollama: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    openrouter: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    openai_compatible: LLMProviderConfig = field(default_factory=LLMProviderConfig)

    # Embedding
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)

    # Per-module overrides
    soul: ModuleLLMConfig = field(default_factory=ModuleLLMConfig)
    discovery: ModuleLLMConfig = field(default_factory=ModuleLLMConfig)
    recommendation: ModuleLLMConfig = field(default_factory=ModuleLLMConfig)
    evaluation: ModuleLLMConfig = field(default_factory=ModuleLLMConfig)

    # Data path for embedding cache
    data_path: str = ""

    def __getattr__(self, name: str) -> Any:
        """Return empty ModuleLLMConfig for unknown module overrides."""
        return ModuleLLMConfig()