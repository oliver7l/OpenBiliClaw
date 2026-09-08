"""非破坏性口语化文本清洗与规范化。

设计哲学：
1. 废除破坏性的字符级正则，避免 mutilate 中文成语（如"代代相传"、"常常"）。
2. 保留所有语法动词（如"是不是"）和指示代词（如"这个"、"那个"）。
3. 安全地规范化连续重复标点和过多空白。
4. 复杂的语义去重和填充词优化交给上层 LLM 处理。

本模块改编自 bili-video2book (MIT License)。
原始版权：Copyright (c) 2026 Bilibili Audio Knowledge Skill Contributors
"""

from __future__ import annotations

import re
from typing import Any


class TextCleaner:
    """非破坏性文本清洗器。"""

    @classmethod
    def clean(cls, raw_transcript: str) -> dict[str, Any]:
        """执行非破坏性清洗和规范化。

        Args:
            raw_transcript: 原始转录文本。

        Returns:
            包含清洗后文本和统计信息的字典。
        """
        if not raw_transcript:
            return {
                "original_length": 0,
                "cleaned_length": 0,
                "compression_ratio": 0.0,
                "cleaned_text": "",
            }

        orig_len = len(raw_transcript)
        text = raw_transcript

        # 1. 中文字符之间的空格（不含换行，保留段落结构）
        text = re.sub(r"(?<=[\u4e00-\u9fa5])[^\S\n]+(?=[\u4e00-\u9fa5])", "", text)

        # 2. 折叠连续相同的标点符号
        text = re.sub(r"([，。！？、,!?])\1+", r"\1", text)

        # 3. 折叠多个空行
        text = re.sub(r"\n{3,}", "\n\n", text)

        # 4. 行级空白去除
        lines = [line.strip() for line in text.split("\n")]
        cleaned_text = "\n".join(lines).strip()

        cleaned_len = len(cleaned_text)
        compression_ratio = (
            round((1 - cleaned_len / max(1, orig_len)) * 100, 2) if orig_len > 0 else 0.0
        )

        return {
            "original_length": orig_len,
            "cleaned_length": cleaned_len,
            "compression_ratio": max(0.0, compression_ratio),
            "cleaned_text": cleaned_text,
        }
