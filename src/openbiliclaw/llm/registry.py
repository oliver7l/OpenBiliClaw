"""Re-export from obc_llm.registry — compatibility stub.

``build_llm_registry`` / ``build_embedding_service`` 包装为接受主项目
``Config`` 的适配入口（内部经 ``_compat.to_llm_config`` 映射），其余名字
直接 re-export。
"""

from obc_llm.registry import *  # noqa: F401, F403
from obc_llm.registry import (
    RegistryBuildError,  # noqa: F401 — 显式导出供静态检查与调用方
    RegistrySummary,  # noqa: F401
    _embedding_compat_warned,  # noqa: F401 — 模块级 set，测试经 stub 读写同一对象
    _emit_embedding_compat_warning,  # noqa: F401 — private name, explicit re-export
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


def build_llm_registry(  # noqa: F811 — 覆盖 import * 的同名导出
    config,
    *,
    provider_overrides=None,
    fallback_order=None,
):
    """适配入口：接受主项目 Config / Config.llm，映射为 obc_llm LLMConfig。"""
    return _build_llm_registry(
        to_llm_config(config),
        provider_overrides=provider_overrides,
        fallback_order=fallback_order,
    )


def build_embedding_service(config, registry=None):  # noqa: F811
    """适配入口：接受主项目 Config / Config.llm，映射为 obc_llm LLMConfig。"""
    return _build_embedding_service(to_llm_config(config), registry)


def _maybe_openai_compatible_provider(config, overrides):  # noqa: F811
    """适配入口：接受主项目 Config，映射后再调 obc_llm 实现。"""
    return _maybe_openai_compatible_provider_impl(to_llm_config(config), overrides)


def _ollama_is_chat_capable(config):  # noqa: F811
    """适配入口：接受主项目 Config，映射后再调 obc_llm 实现。"""
    return _ollama_is_chat_capable_impl(to_llm_config(config))


def summarize_registry(config, registry):  # noqa: F811
    """适配入口：接受主项目 Config，映射后再调 obc_llm 实现。"""
    return _summarize_registry_impl(to_llm_config(config), registry)
