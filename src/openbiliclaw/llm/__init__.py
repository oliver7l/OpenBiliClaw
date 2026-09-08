"""LLM package — multi-model provider support (re-export from obc-llm).

This is a compatibility stub that re-exports everything from the extracted
obc-llm package. All existing imports continue to work unchanged.
"""

from obc_llm._config import EmbeddingConfig, LLMConfig, LLMProviderConfig
from obc_llm.base import (
    HealthCheckResult,
    LLMFallbackError,
    LLMProvider,
    LLMProviderError,
    LLMRateLimitError,
    LLMResponse,
    LLMResponseError,
    LLMTimeoutError,
)
from obc_llm.claude_provider import ClaudeProvider
from obc_llm.gemini_provider import GeminiProvider
from obc_llm.ollama_provider import OllamaProvider
from obc_llm.openai_provider import DeepSeekProvider, OpenAIProvider
from obc_llm.openrouter_provider import OpenRouterProvider
from obc_llm.service import (
    LLMProviderExecutionError,
    LLMResponseContentError,
    LLMService,
    LLMServiceError,
    is_llm_rate_limit_error,
)

from openbiliclaw.llm.registry import (
    RegistryBuildError,
    RegistrySummary,
    build_llm_registry,
    summarize_registry,
)

__all__ = [
    "ClaudeProvider",
    "DeepSeekProvider",
    "EmbeddingConfig",
    "GeminiProvider",
    "HealthCheckResult",
    "LLMConfig",
    "LLMFallbackError",
    "LLMProvider",
    "LLMProviderConfig",
    "LLMProviderError",
    "LLMRateLimitError",
    "LLMResponse",
    "LLMResponseError",
    "LLMTimeoutError",
    "OllamaProvider",
    "OpenAIProvider",
    "OpenRouterProvider",
    "RegistryBuildError",
    "RegistrySummary",
    "LLMProviderExecutionError",
    "LLMService",
    "LLMServiceError",
    "LLMResponseContentError",
    "build_llm_registry",
    "is_llm_rate_limit_error",
    "summarize_registry",
]
