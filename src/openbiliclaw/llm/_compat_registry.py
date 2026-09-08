"""主项目 Config → obc_llm 的 LLM 构建适配入口。

``openbiliclaw/llm/registry.py`` 通过 ``import *`` 再覆盖导出同名包装，
mypy（no-redef ignore）会把符号类型解析回 obc_llm 原签名；需要类型检查的
调用方（CLI / API）请直接从本模块导入——签名即真实契约：接受主项目 Config。
"""

from __future__ import annotations

from typing import Any

from obc_llm.base import LLMRegistry
from obc_llm.embedding import SupportsEmbeddingService
from obc_llm.registry import (
    RegistrySummary,
)
from obc_llm.registry import (
    _maybe_openai_compatible_provider as _maybe_openai_compatible_provider_impl,
)
from obc_llm.registry import (
    _ollama_is_chat_capable as _ollama_is_chat_capable_impl,
)
from obc_llm.registry import (
    build_embedding_service as _build_embedding_service,
)
from obc_llm.registry import (
    build_llm_registry as _build_llm_registry,
)
from obc_llm.registry import (
    summarize_registry as _summarize_registry_impl,
)

from openbiliclaw.llm._compat import to_llm_config


def build_llm_registry(
    config: Any,
    *,
    provider_overrides: dict[str, Any] | None = None,
    fallback_order: list[str] | None = None,
) -> LLMRegistry:
    """适配入口：接受主项目 Config / Config.llm，映射为 obc_llm LLMConfig。"""
    return _build_llm_registry(
        to_llm_config(config),
        provider_overrides=provider_overrides,
        fallback_order=fallback_order,
    )


def build_embedding_service(
    config: Any,
    registry: LLMRegistry | None = None,
) -> SupportsEmbeddingService | None:
    """适配入口：接受主项目 Config / Config.llm，映射为 obc_llm LLMConfig。"""
    # obc_llm 侧 registry 参数仅为兼容旧调用保留、实现不使用，允许传 None
    return _build_embedding_service(to_llm_config(config), registry)  # type: ignore[arg-type]


def _maybe_openai_compatible_provider(
    config: Any,
    overrides: dict[str, Any],
) -> Any:
    """适配入口：接受主项目 Config，映射后再调 obc_llm 实现。"""
    return _maybe_openai_compatible_provider_impl(to_llm_config(config), overrides)


def _ollama_is_chat_capable(config: Any) -> bool:
    """适配入口：接受主项目 Config，映射后再调 obc_llm 实现。"""
    return _ollama_is_chat_capable_impl(to_llm_config(config))


def summarize_registry(config: Any, registry: LLMRegistry) -> RegistrySummary:
    """适配入口：接受主项目 Config，映射后再调 obc_llm 实现。"""
    return _summarize_registry_impl(to_llm_config(config), registry)
