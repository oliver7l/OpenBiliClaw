"""视频转写子模块。

提供 B 站视频音频获取、切片、本地转录、文本清洗等能力。
音频文件等中间产物写入系统临时目录，管线完成后清理。

本模块代码改编自 bili-video2book (MIT License)。
原始版权：Copyright (c) 2026 Bilibili Audio Knowledge Skill Contributors
"""

from .chunker import AudioChunker
from .cleaner import TextCleaner

__all__ = [
    "AudioChunker",
    "TextCleaner",
]
