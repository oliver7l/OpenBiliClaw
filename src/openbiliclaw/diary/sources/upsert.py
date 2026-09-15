"""把 ``RawNote`` 幂等落库到日记系统。

去重规则与历史导入保持一致：**日期 + 正文前 50 字**。因此对着已含 928 篇
日记的库重复导入是安全的——命中即跳过，不会产生重复条目。

批次内也会去重（同一批里日期 + 前缀相同的只落一条），避免月度汇总拆分后
出现同一天内容重合的段落。
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from collections.abc import Iterable

from ..models import DiaryEntryCreate, MoodLevel
from ..service import DiaryService
from .base import RawNote, dedup_key

SAMPLE_LIMIT = 5


@dataclasses.dataclass
class ImportStats:
    """一次导入的统计结果。"""

    source: str
    total: int = 0
    imported: int = 0
    skipped: int = 0
    failed: int = 0
    undated: int = 0
    dry_run: bool = True
    imported_samples: list[str] = dataclasses.field(default_factory=list)
    skipped_samples: list[str] = dataclasses.field(default_factory=list)
    errors: list[str] = dataclasses.field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """转成可 JSON 序列化的字典。"""
        return dataclasses.asdict(self)


def _existing_keys(service: DiaryService, date_str: str) -> set[str]:
    entries = service.store.list_entries(start_date=date_str, end_date=date_str, limit=1000)
    return {dedup_key(entry.content or "") for entry in entries}


def _summary(note: RawNote, date_str: str) -> str:
    head = " ".join((note.body or "").split())[:40]
    return f"{date_str} | {head}"


def import_notes(
    service: DiaryService,
    notes: Iterable[RawNote],
    *,
    source: str,
    dry_run: bool = True,
    default_date: str | None = None,
    extra_tags: list[str] | None = None,
) -> ImportStats:
    """把 ``notes`` 幂等写入日记库。

    Args:
        service: 已构造好的 DiaryService。
        notes: 来源适配器产出的条目。
        source: 来源标识（写入 ``diary_entries.source``）。
        dry_run: 为 True 时只统计不写库。
        default_date: 条目缺日期时使用的兜底日期（默认今天）。
        extra_tags: 额外附加到每条日记的标签。

    Returns:
        ImportStats: 统计结果。

    """
    stats = ImportStats(source=source, dry_run=dry_run)
    fallback = default_date or dt.datetime.now().strftime("%Y-%m-%d")
    tags = list(extra_tags or [])
    seen_keys: set[tuple[str, str]] = set()
    existing_cache: dict[str, set[str]] = {}

    for note in notes:
        stats.total += 1
        date_str = note.note_date or fallback
        if note.note_date is None:
            stats.undated += 1
        key = dedup_key(note.body)
        if not key:
            stats.failed += 1
            stats.errors.append(f"{note.raw_id}: 正文为空")
            continue
        if (date_str, key) in seen_keys:
            stats.skipped += 1
            continue
        seen_keys.add((date_str, key))
        if date_str not in existing_cache:
            existing_cache[date_str] = _existing_keys(service, date_str)
        if key in existing_cache[date_str]:
            stats.skipped += 1
            if len(stats.skipped_samples) < SAMPLE_LIMIT:
                stats.skipped_samples.append(_summary(note, date_str))
            continue
        if dry_run:
            stats.imported += 1
            if len(stats.imported_samples) < SAMPLE_LIMIT:
                stats.imported_samples.append(_summary(note, date_str))
            continue
        try:
            service.create_entry(
                DiaryEntryCreate(
                    entry_date=date_str,
                    title=note.title or date_str,
                    content=note.body,
                    source=source,
                    tags=tags,
                    mood=MoodLevel.UNKNOWN,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 单条失败不应中断整批
            stats.failed += 1
            stats.errors.append(f"{note.raw_id}: {exc}")
            continue
        existing_cache[date_str].add(key)  # 落库后立刻入缓存，防止批内重复
        stats.imported += 1
        if len(stats.imported_samples) < SAMPLE_LIMIT:
            stats.imported_samples.append(_summary(note, date_str))

    return stats


__all__ = ["ImportStats", "import_notes"]
