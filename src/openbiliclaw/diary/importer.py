"""日记数据导入器。

支持从多种文本格式导入日记：
- 乐乐日记格式（日期 + 编号条目）
- 通用纯文本格式（按日期分段）
- Markdown 格式
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

from .models import DiaryEntry, DiaryEntryCreate, MoodLevel
from .service import DiaryService

logger = logging.getLogger(__name__)


# 匹配中文日期格式：2025年04月08日 / 2024年6月21日 / 2024年03月11日
_DATE_PATTERN = re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日\s*$")
# 匹配编号条目：1、xxx / 1. xxx / 1) xxx
_ITEM_PATTERN = re.compile(r"^\d+[、\.）)]\s*(.+)$")


class DiaryImporter:
    """日记数据导入器。"""

    def __init__(self, service: DiaryService) -> None:
        self.service = service

    def import_lele_diary(
        self, file_path: str | Path, source: str = "import_lele"
    ) -> tuple[int, list[DiaryEntry]]:
        """导入乐乐日记格式的文本文件。

        格式说明：
        - 文件开头可能有标题行
        - 日期行格式：YYYY年MM月DD日
        - 日期下为编号条目：1、内容 / 2、内容
        - 没有日期的开头条目归入最早日期或单独处理

        Args:
            file_path: 文件路径
            source: 来源标识

        Returns:
            (导入数量, 导入的日记列表)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        text = path.read_text(encoding="utf-8")
        entries_data = self._parse_lele_format(text)
        if not entries_data:
            logger.warning("未解析到任何日记条目: %s", path)
            return 0, []

        imported: list[DiaryEntry] = []
        for date_str, content, title in entries_data:
            # 检查是否已存在同日期同来源的日记
            existing = self.service.store.list_entries(
                start_date=date_str,
                end_date=date_str,
                source=source,
                limit=1,
            )
            if existing:
                logger.debug("跳过已存在的日记: %s", date_str)
                continue

            entry = self.service.create_entry(
                DiaryEntryCreate(
                    entry_date=date_str,
                    title=title,
                    content=content,
                    source=source,
                    tags=["乐乐", "成长日记"],
                    mood=MoodLevel.UNKNOWN,
                )
            )
            imported.append(entry)

        logger.info("乐乐日记导入完成: %d 篇 (文件: %s)", len(imported), path)
        return len(imported), imported

    def import_text_file(
        self,
        file_path: str | Path,
        source: str = "import_text",
        default_date: str | None = None,
    ) -> tuple[int, list[DiaryEntry]]:
        """导入通用文本文件。

        按空行分段，每段作为一篇日记。
        如果段首包含日期，则使用该日期，否则使用 default_date 或当前日期。

        Args:
            file_path: 文件路径
            source: 来源标识
            default_date: 默认日期

        Returns:
            (导入数量, 导入的日记列表)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        text = path.read_text(encoding="utf-8")
        entries_data = self._parse_generic_text(text, default_date)

        imported: list[DiaryEntry] = []
        for date_str, content, title in entries_data:
            entry = self.service.create_entry(
                DiaryEntryCreate(
                    entry_date=date_str,
                    title=title,
                    content=content,
                    source=source,
                    tags=[],
                    mood=MoodLevel.UNKNOWN,
                )
            )
            imported.append(entry)

        logger.info("文本日记导入完成: %d 篇 (文件: %s)", len(imported), path)
        return len(imported), imported

    def import_markdown_file(
        self,
        file_path: str | Path,
        source: str = "import_markdown",
    ) -> tuple[int, list[DiaryEntry]]:
        """导入 Markdown 文件。

        以一级标题作为日期/标题，内容作为日记正文。

        Args:
            file_path: 文件路径
            source: 来源标识

        Returns:
            (导入数量, 导入的日记列表)
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {path}")

        text = path.read_text(encoding="utf-8")
        entries_data = self._parse_markdown(text)

        imported: list[DiaryEntry] = []
        for date_str, content, title in entries_data:
            entry = self.service.create_entry(
                DiaryEntryCreate(
                    entry_date=date_str,
                    title=title,
                    content=content,
                    source=source,
                    tags=[],
                    mood=MoodLevel.UNKNOWN,
                )
            )
            imported.append(entry)

        logger.info("Markdown 日记导入完成: %d 篇 (文件: %s)", len(imported), path)
        return len(imported), imported

    # ── 解析方法 ─────────────────────────────────────────────────

    def _parse_lele_format(self, text: str) -> list[tuple[str, str, str]]:
        """解析乐乐日记格式。

        Returns:
            [(日期, 内容, 标题), ...]
        """
        lines = text.split("\n")
        result: list[tuple[str, str, str]] = []

        current_date: str | None = None
        current_items: list[str] = []
        pending_items: list[str] = []  # 日期之前的条目

        def _flush() -> None:
            nonlocal current_date, current_items, pending_items
            if current_date and current_items:
                content = "\n".join(f"{i + 1}、{item}" for i, item in enumerate(current_items))
                title = f"乐乐成长日记 - {current_date}"
                result.append((current_date, content, title))
            current_items = []

        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            # 跳过标题行
            if line.startswith("生活 - ") or line.startswith("# "):
                continue

            # 匹配日期行
            date_match = _DATE_PATTERN.match(line)
            if date_match:
                # 先 flush 上一个日期
                _flush()
                year, month, day = (
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3)),
                )
                current_date = f"{year:04d}-{month:02d}-{day:02d}"
                # 如果有待处理的条目（日期之前的），归入当前日期
                if pending_items:
                    current_items.extend(pending_items)
                    pending_items = []
                continue

            # 匹配编号条目
            item_match = _ITEM_PATTERN.match(line)
            if item_match:
                item_content = item_match.group(1).strip()
                if item_content:
                    if current_date:
                        current_items.append(item_content)
                    else:
                        pending_items.append(item_content)
                continue

            # 非日期非编号的行，作为当前条目的延续
            if current_items and current_date:
                current_items[-1] += " " + line
            elif pending_items:
                pending_items[-1] += " " + line

        # 处理最后一个日期
        _flush()

        # 如果还有 pending_items（整个文件没有日期），用文件修改日期
        if pending_items and not result:
            default_date = datetime.now().strftime("%Y-%m-%d")
            content = "\n".join(f"{i + 1}、{item}" for i, item in enumerate(pending_items))
            title = f"乐乐成长日记 - {default_date}"
            result.append((default_date, content, title))

        return result

    def _parse_generic_text(
        self, text: str, default_date: str | None = None
    ) -> list[tuple[str, str, str]]:
        """解析通用文本格式。

        按空行分段，每段作为一篇日记。
        如果某段只有日期（无内容），则该日期应用到下一段。
        """
        if default_date is None:
            default_date = datetime.now().strftime("%Y-%m-%d")

        paragraphs = re.split(r"\n\s*\n", text.strip())
        result: list[tuple[str, str, str]] = []
        pending_date: str | None = None

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            lines = para.split("\n")
            first_line = lines[0].strip()

            # 尝试从首行提取日期
            date_str = default_date
            content_start = 0

            date_match = _DATE_PATTERN.match(first_line)
            if date_match:
                year, month, day = (
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3)),
                )
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
                content_start = 1
            elif re.match(r"^\d{4}-\d{2}-\d{2}", first_line):
                date_str = first_line[:10]
                content_start = 1

            content = "\n".join(lines[content_start:]).strip()

            # 如果段落只有日期没有内容，记录 pending_date 并跳过
            if not content:
                pending_date = date_str
                continue

            # 如果有 pending_date，使用它
            if pending_date:
                date_str = pending_date
                pending_date = None

            title = content[:30] + ("..." if len(content) > 30 else "")
            result.append((date_str, content, title))

        return result

    def _parse_markdown(self, text: str) -> list[tuple[str, str, str]]:
        """解析 Markdown 格式。"""
        default_date = datetime.now().strftime("%Y-%m-%d")
        sections = re.split(r"^# ", text, flags=re.MULTILINE)
        result: list[tuple[str, str, str]] = []

        for section in sections:
            section = section.strip()
            if not section:
                continue
            lines = section.split("\n", 1)
            heading = lines[0].strip()
            content = lines[1].strip() if len(lines) > 1 else ""

            if not content:
                continue

            # 尝试从标题提取日期
            date_str = default_date
            date_match = _DATE_PATTERN.search(heading)
            if date_match:
                year, month, day = (
                    int(date_match.group(1)),
                    int(date_match.group(2)),
                    int(date_match.group(3)),
                )
                date_str = f"{year:04d}-{month:02d}-{day:02d}"
            else:
                iso_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", heading)
                if iso_match:
                    year, month, day = (
                        int(iso_match.group(1)),
                        int(iso_match.group(2)),
                        int(iso_match.group(3)),
                    )
                    date_str = f"{year:04d}-{month:02d}-{day:02d}"

            title = re.sub(r"\d{4}年\d{1,2}月\d{1,2}日", "", heading).strip()
            title = re.sub(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", "", title).strip(" -—:：")
            if not title:
                title = content[:30] + ("..." if len(content) > 30 else "")

            result.append((date_str, content, title))

        return result
