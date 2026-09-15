"""日记导入来源的公共契约层。

只做三件事，全部是纯函数 / 纯数据，不碰数据库、不碰网络：

1. ``RawNote`` —— 各来源统一产出的中间表示；
2. ``NoteSource`` —— 来源适配器协议（``fetch()`` 产出 ``RawNote``）；
3. 去重键与「月度汇总拆分」两个共享工具 —— 三个来源都用得上。

设计纪律（借鉴 ``references/apple-notes-cli``）：解析与 IO 分离，本模块
可被单测直接覆盖，不需要任何外部依赖。
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator
from typing import Protocol, runtime_checkable

# 去重键长度：沿用历史导入策略（changelog v0.3.180「日期 + 内容前 50 字」）
DEDUP_PREFIX_LEN = 50

# 完整日期（带年份）—— 月度汇总里的分隔符必须带年份才认为是"新的一天"
_DATE_CN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
_DATE_ISO = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})")
# 去重归一：所有空白（含换行、全角空格）压缩成单个半角空格
_WS_RUN = re.compile(r"\s+")


@dataclasses.dataclass(frozen=True)
class RawNote:
    """来源无关的日记中间表示。

    Attributes:
        title: 标题（可为空字符串，由落库层兜底）。
        body: 纯文本正文。
        note_date: ISO 日期 ``YYYY-MM-DD``；无法确定时为 ``None``。
        source: 来源标识，落库后即 ``diary_entries.source``。
        raw_id: 来源内唯一 ID（用于增量与排错）。

    """

    title: str
    body: str
    note_date: str | None
    source: str
    raw_id: str


@runtime_checkable
class NoteSource(Protocol):
    """日记来源适配器协议。

    实现方只需保证 ``fetch()`` 逐条产出 ``RawNote``；增量（``since``）、
    去重、落库统统由上层 ``upsert`` 负责。
    """

    name: str

    def fetch(self, since: str | None = None) -> Iterator[RawNote]:
        """产出日记条目。

        Args:
            since: ISO 日期，仅返回该日期之后（含）的条目；``None`` 表示全量。

        Yields:
            RawNote: 逐条日记。

        """
        ...


def normalize_for_dedup(text: str) -> str:
    """把正文归一化成可比较的形态（所有空白压缩成单空格、去首尾）。

    换行也一并折叠：同一天的两条记录若只是断行不同，应视为重复。
    """
    return _WS_RUN.sub(" ", text).strip()


def dedup_key(body: str, prefix_len: int = DEDUP_PREFIX_LEN) -> str:
    """计算去重键：归一化正文的前 ``prefix_len`` 个字符。

    与历史导入策略保持一致（日期 + 内容前 50 字），因此对已有 928 篇数据
    的重复导入是安全的。
    """
    return normalize_for_dedup(body)[:prefix_len]


def _format_date(year: str | int, month: str | int, day: str | int) -> str:
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def find_date(text: str) -> str | None:
    """从任意文本（标题 / 首行）里提取第一个完整日期。

    支持 ``2026年9月3日`` 与 ``2026-09-03`` / ``2026/09/03`` 两类写法。
    修复了历史数据中出现过的 ``202026年`` 异常格式（取末 4 位年份）。
    """
    if not text:
        return None
    for cn_match in _DATE_CN.finditer(text):
        year, month, day = cn_match.groups()
        if len(year) > 4:  # 「202026年」-> 「2026」
            year = year[-4:]
        try:
            return _format_date(year, month, day)
        except ValueError:
            continue
    iso_match = _DATE_ISO.search(text)
    if iso_match:
        year, month, day = iso_match.groups()
        try:
            return _format_date(year, month, day)
        except ValueError:
            return None
    return None


_DATE_LINE_DECOR = " \t\u3000【】[]（）()#*-—–=：:、,，."
_DATE_LINE_MAX = 20


def _date_only_line(raw: str) -> str | None:
    """若整行「就是一个日期」（允许少量装饰符），返回 ISO 日期，否则 None。"""
    probe = raw.strip().strip(_DATE_LINE_DECOR)
    if not probe or len(probe) > _DATE_LINE_MAX:
        return None
    if _DATE_CN.fullmatch(probe) or _DATE_ISO.fullmatch(probe):
        return find_date(probe)
    return None


def split_monthly_summary(
    text: str,
    fallback_date: str | None = None,
) -> list[tuple[str | None, str]]:
    """把「月度汇总」型文本按内嵌的完整日期分隔符拆成多天。

    苹果备忘录 / 有道云 / WPS 都存在「一篇笔记里按月堆了 N 天的记录」的形态。
    分隔符必须是**独立成行的完整日期**（可带少量装饰符），避免把正文里顺带
    提到的日期误判成新的一天。

    Args:
        text: 原始正文。
        fallback_date: 日期缺失时（含整段无分隔符）使用的日期。

    Returns:
        ``[(日期 or None, 正文), ...]``；无分隔符时返回单元素列表。

    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    sections: list[tuple[str | None, str]] = []
    current_date: str | None = None
    current_lines: list[str] = []

    for raw in lines:
        date_only = _date_only_line(raw)
        if date_only is not None:
            if any(line.strip() for line in current_lines):
                sections.append((current_date, "\n".join(current_lines).strip()))
            current_lines = []
            current_date = date_only
            continue
        current_lines.append(raw)
    if any(line.strip() for line in current_lines):
        sections.append((current_date, "\n".join(current_lines).strip()))

    if not sections:
        return [(fallback_date, text.strip())]
    if len(sections) == 1 and sections[0][0] is None:
        return [(fallback_date, sections[0][1])]
    return [(date or fallback_date, body) for date, body in sections if body]


__all__ = [
    "DEDUP_PREFIX_LEN",
    "NoteSource",
    "RawNote",
    "dedup_key",
    "find_date",
    "normalize_for_dedup",
    "split_monthly_summary",
]
