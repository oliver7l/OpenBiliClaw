"""obc-llm — multi-model LLM provider abstraction for OpenBiliClaw.

Provides a unified interface to multiple LLM providers:
- OpenAI / DeepSeek / Claude / Gemini / Ollama / OpenRouter
- Priority-based concurrency control
- Structured JSON generation with fault tolerance
- Built-in embedding caching
- Module-level provider overrides
"""

from ._config import EmbeddingConfig, LLMConfig, LLMProviderConfig
from ._protocols import (
    InterestTag,
    MemorySummarizer,
    PreferenceLayer,
    ProfileRenderer,
    ToneProfile,
    ToneProvider,
    UsageRecorder,
    build_tone_profile,
    preference_layer_from_dict,
)
from .base import (
    HealthCheckResult,
    LLMFallbackError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)
from .claude_provider import ClaudeProvider
from .gemini_provider import GeminiProvider
from .ollama_provider import OllamaProvider
from .openai_provider import DeepSeekProvider, OpenAIProvider
from .openrouter_provider import OpenRouterProvider
from .registry import (
    RegistryBuildError,
    RegistrySummary,
    build_embedding_service,
    build_llm_registry,
    summarize_registry,
)
from .service import (
    LLMProviderExecutionError,
    LLMResponseContentError,
    LLMService,
    LLMServiceError,
    is_llm_rate_limit_error,
)

__all__ = [
    # Core config and protocols
    "LLMConfig",
    "EmbeddingConfig",
    "LLMProviderConfig",
    "ProfileRenderer",
    "ToneProfile",
    "ToneProvider",
    "UsageRecorder",
    "MemorySummarizer",
    # Data classes
    "InterestTag",
    "PreferenceLayer",
    # Helpers
    "preference_layer_from_dict",
    "build_tone_profile",
    # Base types
    "HealthCheckResult",
    "LLMFallbackError",
    "LLMProvider",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    # Providers
    "ClaudeProvider",
    "DeepSeekProvider",
    "GeminiProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    # Registry factory
    "RegistryBuildError",
    "RegistrySummary",
    "build_llm_registry",
    "build_embedding_service",
    "summarize_registry",
    # Service
    "LLMProviderExecutionError",
    "LLMResponseContentError",
    "LLMService",
    "LLMServiceError",
    "is_llm_rate_limit_error",
]
