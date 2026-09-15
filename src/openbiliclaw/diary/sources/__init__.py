"""日记多来源导入适配器。

统一契约见 :mod:`openbiliclaw.diary.sources.base`；落库见
:mod:`openbiliclaw.diary.sources.upsert`。

| 来源 | 适配器 | 机制 | 自动化 |
|---|---|---|---|
| 苹果备忘录 | :class:`~.apple_notes.AppleNotesSource` | 只读 ``NoteStore.sqlite`` | 可全自动 + 定时 |
| 有道云笔记 | :class:`~.youdao.YoudaoNoteSource` | Cookie + 浏览器自动化 | 手动触发 |
| WPS 笔记 | :class:`~.wps.WpsNoteSource` | Cookie + 浏览器自动化 | 手动触发 |
"""

from __future__ import annotations

from typing import Any

from .base import NoteSource, RawNote, dedup_key, find_date, split_monthly_summary
from .upsert import ImportStats, import_notes

SOURCE_NAMES = ("apple", "youdao", "wps")


def get_source(name: str, **kwargs: Any) -> NoteSource:
    """按名称构造来源适配器。

    Args:
        name: ``apple`` / ``youdao`` / ``wps``。
        **kwargs: 透传给具体适配器的参数。

    Raises:
        ValueError: 名称未知。

    """
    if name == "apple":
        from .apple_notes import AppleNotesSource

        return AppleNotesSource(**kwargs)
    if name == "youdao":
        from .youdao import YoudaoNoteSource

        return YoudaoNoteSource(**kwargs)
    if name == "wps":
        from .wps import WpsNoteSource

        return WpsNoteSource(**kwargs)
    raise ValueError(f"未知来源：{name}（可选：{', '.join(SOURCE_NAMES)}）")


__all__ = [
    "SOURCE_NAMES",
    "ImportStats",
    "NoteSource",
    "RawNote",
    "dedup_key",
    "find_date",
    "get_source",
    "import_notes",
    "split_monthly_summary",
]
