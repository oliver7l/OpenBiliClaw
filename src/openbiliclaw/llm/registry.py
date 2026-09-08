"""Re-export from obc_llm.registry — compatibility stub."""

from obc_llm.registry import *  # noqa: F401, F403
from obc_llm.registry import (
    _ollama_is_chat_capable,  # noqa: F401 — private name, explicit re-export
    build_embedding_service,  # noqa: F401 — 显式导出便于静态检查
)
