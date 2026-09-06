#!/usr/bin/env python3
"""从 MindBack 日记数据库导入日记到 OpenBiliClaw 日记系统。

数据源：备份项目中的 unified_notes.db 的 diary_entries 表
"""

import re
import sys
from pathlib import Path

import sqlite3

# 确保项目根目录在 path 中
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from openbiliclaw.diary import DiaryService, DiaryEntryCreate, MoodLevel
from openbiliclaw.storage.database import Database


# 匹配中文日期：2025年08月03日 / 2025年8月3日
_DATE_PATTERN = re.compile(r"(\d{4})年(\d{1,2})月(\d{1,2})日")


def parse_chinese_date(date_str: str) -> str | None:
    """将中文日期格式转为 YYYY-MM-DD。"""
    if not date_str or date_str == "未知日期":
        return None
    match = _DATE_PATTERN.search(date_str)
    if match:
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    # 尝试匹配 ISO 格式
    iso_match = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", date_str)
    if iso_match:
        year, month, day = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
        return f"{year:04d}-{month:02d}-{day:02d}"
    return None


def main():
    # 源数据库
    source_db_path = "/Volumes/固态硬盘1T/007-备份项目/06-Web前端项目/024-笔记网站/0001-我的网站-优化/unified_notes.db"
    print(f"源数据库: {source_db_path}")

    # 目标数据库
    target_db_path = project_root / "data" / "openbiliclaw.db"
    print(f"目标数据库: {target_db_path}")

    # 读取源数据
    source_conn = sqlite3.connect(source_db_path)
    source_conn.row_factory = sqlite3.Row

    # 读取 diary_entries
    rows = source_conn.execute(
        "SELECT entry_id, title, content, date, source_file FROM diary_entries ORDER BY date"
    ).fetchall()
    print(f"\ndiary_entries 总数: {len(rows)}")

    # 过滤有效日期并去重
    valid_entries = []
    seen_dates = set()
    skipped_unknown = 0
    skipped_duplicate = 0
    skipped_empty = 0

    for row in rows:
        date_str = row["date"] or ""
        parsed_date = parse_chinese_date(date_str)

        if not parsed_date:
            skipped_unknown += 1
            continue

        content = (row["content"] or "").strip()
        if not content or len(content) < 5:
            skipped_empty += 1
            continue

        # 去重：同一天只保留第一条（内容较长的优先）
        if parsed_date in seen_dates:
            # 检查是否已有该日期，如果新内容更长，替换
            existing_idx = next((i for i, e in enumerate(valid_entries) if e["date"] == parsed_date), None)
            if existing_idx is not None and len(content) > len(valid_entries[existing_idx]["content"]):
                valid_entries[existing_idx] = {
                    "date": parsed_date,
                    "title": row["title"] or "",
                    "content": content,
                    "source_file": row["source_file"] or "",
                    "entry_id": row["entry_id"] or "",
                }
            skipped_duplicate += 1
            continue

        seen_dates.add(parsed_date)
        valid_entries.append({
            "date": parsed_date,
            "title": row["title"] or "",
            "content": content,
            "source_file": row["source_file"] or "",
            "entry_id": row["entry_id"] or "",
        })

    print(f"  有效日期: {len(valid_entries)}")
    print(f"  跳过未知日期: {skipped_unknown}")
    print(f"  跳过重复日期: {skipped_duplicate}")
    print(f"  跳过空内容: {skipped_empty}")

    # 初始化目标数据库和服务
    db = Database(target_db_path)
    db.initialize()
    service = DiaryService(database=db)

    # 检查已存在的日记（避免重复导入）
    existing, total = service.list_entries(limit=10000, source="import_mindback")
    existing_dates = {e.entry_date for e in existing}
    print(f"\n已存在的 MindBack 日记: {len(existing_dates)} 篇")

    # 导入
    imported = 0
    skipped_existing = 0
    errors = 0

    for entry_data in valid_entries:
        if entry_data["date"] in existing_dates:
            skipped_existing += 1
            continue

        try:
            # 标题处理：如果标题是日期或"日记条目"，用日期作为标题
            title = entry_data["title"]
            if not title or title == "日记条目 1" or _DATE_PATTERN.match(title):
                title = f"{entry_data['date']} 日记"

            # 内容清理：移除开头的日期行
            content = entry_data["content"]
            content_lines = content.split("\n")
            if content_lines and _DATE_PATTERN.search(content_lines[0]):
                content = "\n".join(content_lines[1:]).strip()

            service.create_entry(
                DiaryEntryCreate(
                    entry_date=entry_data["date"],
                    title=title,
                    content=content,
                    source="import_mindback",
                    tags=["MindBack", "个人日记"],
                    mood=MoodLevel.UNKNOWN,
                )
            )
            imported += 1
        except Exception as exc:
            errors += 1
            print(f"  导入失败 [{entry_data['date']}]: {exc}")

    print(f"\n导入完成！")
    print(f"  新导入: {imported} 篇")
    print(f"  跳过已存在: {skipped_existing} 篇")
    print(f"  失败: {errors} 篇")

    # 显示统计
    stats = service.get_stats()
    print(f"\n日记系统统计:")
    print(f"  总数: {stats.total_entries}")
    print(f"  总字数: {stats.total_words}")
    print(f"  平均字数: {stats.avg_words_per_entry}")
    print(f"  时间范围: {stats.earliest_date} ~ {stats.latest_date}")
    print(f"  已分析: {stats.analyzed_count}")

    # 按来源统计
    print(f"\n按来源统计:")
    source_conn2 = sqlite3.connect(target_db_path)
    source_counts = source_conn2.execute(
        "SELECT source, COUNT(*) as cnt FROM diary_entries GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    for row in source_counts:
        print(f"  {row[0]}: {row[1]} 篇")

    source_conn.close()
    source_conn2.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
