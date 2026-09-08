"""笔记生成器。

使用 LLMService 将清洗后的转录文本生成为结构化笔记。
支持 ASR 校对和多种内容类型的笔记模板。
"""

from __future__ import annotations

import logging
from typing import Any

from .prompts import (
    ASR_RECTIFY_PROMPT,
    get_prompt_for_content_type,
)

logger = logging.getLogger(__name__)


class NoteGenerator:
    """笔记生成器。

    基于 LLMService 提供 ASR 校对和结构化笔记生成能力。
    """

    def __init__(self, llm_service: Any) -> None:
        """初始化笔记生成器。

        Args:
            llm_service: LLMService 实例，需支持 complete_structured_task() 方法。

        """
        self._llm = llm_service

    async def rectify_asr(
        self,
        raw_text: str,
        domain_hint: str = "",
    ) -> str:
        """对 ASR 转写文本进行字面级校对。

        核心原则：只改"字"，不改"话"。

        Args:
            raw_text: 原始 ASR 转写文本。
            domain_hint: 领域线索，帮助纠正专有名词。

        Returns:
            校对后的文本。

        """
        if not raw_text.strip():
            return ""

        prompt = ASR_RECTIFY_PROMPT.format(
            domain_hint=domain_hint or "通用领域，根据上下文自行判断",
            raw_text=raw_text,
        )

        try:
            resp = await self._llm.complete_structured_task(
                system_instruction="",
                user_input=prompt,
                temperature=0.3,
                max_tokens=4096,
                caller="notes.rectify_asr",
                reasoning_effort="none",
                inject_core_memory=False,
            )
            result = getattr(resp, "content", "")
            return result.strip()
        except Exception as e:
            logger.warning("ASR 校对失败，使用原始文本: %s", e)
            return raw_text

    async def generate_note(
        self,
        title: str,
        content: str,
        *,
        part_title: str = "P1",
        content_type: str = "article",
    ) -> str:
        """生成结构化笔记。

        Args:
            title: 视频/内容标题。
            content: 转录文本内容。
            part_title: 分集/分P 标题。
            content_type: 内容类型（study/news/general/article）。

        Returns:
            生成的 Markdown 格式笔记。

        """
        if not content.strip():
            return ""

        prompt_template = get_prompt_for_content_type(content_type)
        prompt = prompt_template.format(
            title=title,
            part_title=part_title,
            content=content,
        )

        try:
            resp = await self._llm.complete_structured_task(
                system_instruction="",
                user_input=prompt,
                temperature=0.3,
                max_tokens=4096,
                caller="notes.generate_note",
                reasoning_effort="none",
                inject_core_memory=False,
            )
            result = getattr(resp, "content", "")
            return result.strip()
        except Exception as e:
            logger.error("笔记生成失败: %s", e)
            raise
