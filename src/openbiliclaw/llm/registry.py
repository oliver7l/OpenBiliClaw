"""Re-export from obc_llm.registry — compatibility stub.

适配入口（``build_llm_registry`` / ``build_embedding_service`` /
``summarize_registry`` / ``_maybe_openai_compatible_provider`` /
``_ollama_is_chat_capable``）的**唯一实现**位于
``openbiliclaw.llm._compat_registry``：它们接受主项目 ``Config`` / ``Config.llm``，
内部经 ``_compat.to_llm_config`` 映射为 ``obc_llm._config.LLMConfig``。

本模块只做转发。此前 ``registry`` 与 ``_compat_registry`` 各存一份逐字相同的
包装函数（后者 docstring 解释：``import *`` + 覆盖会让 mypy 把符号解析回
obc_llm 原签名，故另设一个类型正确的模块），两份实现即两处漂移点。现在签名
与实现都归 ``_compat_registry``，本 stub 仅保留
``openbiliclaw.llm.registry.*`` 这条稳定导入路径。

私有名（``_embedding_compat_warned`` / ``_emit_embedding_compat_warning``）
经 ``import *`` 拿不到，故显式 re-export —— 测试经本 stub 读写的仍是 obc_llm
中同一个对象。
"""

from obc_llm._config import LLMConfig  # noqa: F401 — re-export 供调用方注解
from obc_llm.base import LLMProvider, LLMRegistry  # noqa: F401 — 同上
from obc_llm.embedding import SupportsEmbeddingService  # noqa: F401 — 同上
from obc_llm.registry import *  # noqa: F401, F403
from obc_llm.registry import (
    RegistryBuildError,  # noqa: F401 — 显式导出供静态检查与调用方
    RegistrySummary,  # noqa: F401
    _embedding_compat_warned,  # noqa: F401 — 模块级 set，测试经 stub 读写同一对象
    _emit_embedding_compat_warning,  # noqa: F401 — private name, explicit re-export
)

from openbiliclaw.llm._compat_registry import (  # noqa: F401
    _maybe_openai_compatible_provider as _maybe_openai_compatible_provider,
)
from openbiliclaw.llm._compat_registry import (
    _ollama_is_chat_capable as _ollama_is_chat_capable,
)
from openbiliclaw.llm._compat_registry import (
    build_embedding_service as build_embedding_service,
)
from openbiliclaw.llm._compat_registry import (
    build_llm_registry as build_llm_registry,
)
from openbiliclaw.llm._compat_registry import (
    summarize_registry as summarize_registry,
)
