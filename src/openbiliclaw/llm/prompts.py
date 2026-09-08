"""Re-export from obc_llm.prompts — compatibility stub."""

from obc_llm.prompts import *  # noqa: F401, F403
from obc_llm.prompts import (  # noqa: F401 — 私有名显式 re-export，与 registry stub 的 _ollama_is_chat_capable 同模式
    _AWARENESS_SYSTEM_PROMPT,
    _BATCH_CONTENT_EVALUATION_SYSTEM_PROMPT,
    _MERGED_KEYWORDS_SYSTEM_PROMPT,
    _PROFILE_CONSOLIDATION_SYSTEM_PROMPT,
    _platform_content_label,
    _platform_friend_label,
)
