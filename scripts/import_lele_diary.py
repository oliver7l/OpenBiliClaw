#!/usr/bin/env python3
"""导入乐乐日记数据到日记系统。"""

import sys
from pathlib import Path

# 确保项目根目录在 path 中
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root / "src"))

from openbiliclaw.diary import DiaryService
from openbiliclaw.diary.importer import DiaryImporter
from openbiliclaw.storage.database import Database


def main():
    db_path = project_root / "data" / "openbiliclaw.db"
    print(f"使用数据库: {db_path}")

    # 初始化数据库
    db = Database(db_path)
    db.initialize()

    # 创建日记服务
    service = DiaryService(database=db)
    importer = DiaryImporter(service)

    # 乐乐日记文件路径
    lele_diary_path = "/Volumes/固态硬盘1T/002-探索项目/wechat-tools/scripts/lele-diary.txt"
    print(f"导入文件: {lele_diary_path}")

    # 导入
    count, entries = importer.import_lele_diary(lele_diary_path, source="import_lele")
    print(f"\n导入完成！共导入 {count} 篇日记")

    # 显示统计
    stats = service.get_stats()
    print("\n日记统计:")
    print(f"  总数: {stats.total_entries}")
    print(f"  总字数: {stats.total_words}")
    print(f"  平均字数: {stats.avg_words_per_entry}")
    print(f"  时间范围: {stats.earliest_date} ~ {stats.latest_date}")
    print(f"  已分析: {stats.analyzed_count}")

    # 显示最近5篇
    print("\n最近5篇日记:")
    recent, _ = service.list_entries(limit=5, sort_by="entry_date", sort_order="DESC")
    for entry in recent:
        preview = entry.content[:50].replace("\n", " ")
        print(f"  [{entry.entry_date}] {entry.title or '(无标题)'} - {preview}...")

    return 0


if __name__ == "__main__":
    sys.exit(main())
