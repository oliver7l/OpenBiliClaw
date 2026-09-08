"""obc-llm 配置适配层。

obc-llm 抽取后，``build_llm_registry`` / ``build_embedding_service`` 期望
``obc_llm._config.LLMConfig``；主项目调用方仍传旧的 ``Config`` 对象（或
``Config.llm`` 段）。本模块做一次防御性映射，两套字段名完全对齐：

- 传入已是 ``obc_llm._config.LLMConfig`` → 原样返回；
- 传入主项目 ``Config`` → 取其 ``.llm`` 段；
- 其余按字段逐项拷贝，缺失字段用各自默认值兜底。

本文件属于 K1（模块抽取未收口）的适配层，待调用方全部改为直接使用
``obc_llm._config`` 后可删除。
"""

from __future__ import annotations

from typing import Any

_PROVIDER_NAMES = (
    "openai",
    "claude",
    "gemini",
    "deepseek",
    "ollama",
    "openrouter",
    "openai_compatible",
    "zhipu",
    "modelscope",
    "siliconflow",
)
_MODULE_NAMES = ("soul", "discovery", "recommendation", "evaluation")


def to_llm_config(config: Any) -> Any:
    """把主项目 Config / Config.llm 映射为 obc_llm._config.LLMConfig。"""
    from obc_llm._config import (
        EmbeddingConfig as _OEmbeddingConfig,
    )
    from obc_llm._config import (
        LLMConfig as _OLLMConfig,
    )
    from obc_llm._config import (
        LLMProviderConfig as _OProviderConfig,
    )
    from obc_llm._config import (
        ModuleLLMConfig as _OModuleConfig,
    )

    if isinstance(config, _OLLMConfig):
        return config
    llm = getattr(config, "llm", None)
    if llm is None:
        # 传入的已是 llm 段本身（或非标准对象），按通用属性拷贝
        llm = config
    if isinstance(llm, _OLLMConfig):
        return llm

    out = _OLLMConfig(
        default_provider=str(getattr(llm, "default_provider", "") or "openai"),
        fallback_provider=str(getattr(llm, "fallback_provider", "") or ""),
        fallback_enabled=bool(getattr(llm, "fallback_enabled", False)),
        timeout=float(getattr(llm, "timeout", 60.0) or 60.0),
        concurrency=int(getattr(llm, "concurrency", 3) or 3),
        data_path=str(getattr(llm, "data_path", "") or ""),
    )
    for name in _PROVIDER_NAMES:
        provider = getattr(llm, name, None)
        if provider is None:
            continue
        setattr(
            out,
            name,
            _OProviderConfig(
                api_key=str(getattr(provider, "api_key", "") or ""),
                base_url=str(getattr(provider, "base_url", "") or ""),
                model=str(getattr(provider, "model", "") or ""),
                auth_mode=str(getattr(provider, "auth_mode", "") or ""),
                reasoning_effort=str(getattr(provider, "reasoning_effort", "") or ""),
                num_ctx=int(getattr(provider, "num_ctx", 0) or 0),
                http_referer=str(getattr(provider, "http_referer", "") or ""),
                x_title=str(getattr(provider, "x_title", "") or ""),
            ),
        )
    embedding = getattr(llm, "embedding", None)
    if embedding is not None:
        out.embedding = _OEmbeddingConfig(
            provider=str(getattr(embedding, "provider", "") or ""),
            model=str(getattr(embedding, "model", "") or ""),
            api_key=str(getattr(embedding, "api_key", "") or ""),
            base_url=str(getattr(embedding, "base_url", "") or ""),
            similarity_threshold=float(getattr(embedding, "similarity_threshold", 0.85) or 0.85),
            fallback_provider=str(getattr(embedding, "fallback_provider", "") or ""),
            fallback_enabled=bool(getattr(embedding, "fallback_enabled", False)),
            output_dimensionality=int(getattr(embedding, "output_dimensionality", 1024) or 1024),
        )
    for name in _MODULE_NAMES:
        module_cfg = getattr(llm, name, None)
        if module_cfg is None:
            continue
        setattr(
            out,
            name,
            _OModuleConfig(
                provider=str(getattr(module_cfg, "provider", "") or ""),
                model=str(getattr(module_cfg, "model", "") or ""),
            ),
        )
    return out
