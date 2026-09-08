"""笔记合成子模块。

提供基于 LLM 的笔记生成能力，包括 ASR 校对、结构化笔记生成等。
Prompt 模板改编自 bili-video2book (MIT License)。
原始版权：Copyright (c) 2026 Bilibili Audio Knowledge Skill Contributors
"""

from .prompts import (
    ARTICLE_LEARNING_PROMPT,
    ASR_RECTIFY_PROMPT,
    NOTE_GENERAL_PROMPT,
    NOTE_NEWS_PROMPT,
    NOTE_STUDY_PROMPT,
)

__all__ = [
    "ASR_RECTIFY_PROMPT",
    "NOTE_GENERAL_PROMPT",
    "NOTE_NEWS_PROMPT",
    "NOTE_STUDY_PROMPT",
    "ARTICLE_LEARNING_PROMPT",
]
