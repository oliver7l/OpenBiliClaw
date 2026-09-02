#!/usr/bin/env python3
"""迁移已读库文章：将当前 articles 表中 source_type=read-archive 的记录移到 read_archive 表。

数据库已经创建 read_archive 表结构，这个脚本做数据迁移。
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "openbiliclaw.db"

def main() -> None:
    if not DB_PATH.exists():
        print(f"[错误] 数据库不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 先检查表存在
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='read_archive'")
    if not cur.fetchone():
        print("[错误] read_archive 表不存在，请先重启服务让 schema migration 创建它")
        return

    # 找出articles里read-archive的记录，这些需要迁移
    cur.execute("SELECT * FROM articles WHERE source_type = 'read-archive'")
    rows = list(cur.fetchall())
    print(f"找到 {len(rows)} 条 read-archive 记录在 articles 表")
    if not rows:
        print("无待迁移记录，退出")
        return

    imported = 0
    skipped = 0
    for row in rows:
        # 拿所有字段插入 read_archive
        # 跳过 articles 里read_archive不需要的字段: status, reading_percent, reading_progress, favorited, ai_summary
        cols = [
            "id", "source_type", "source_name", "title", "url", "author", "summary",
            "content_text", "published_at", "tags", "created_at", "updated_at",
        ]
        values = [
            row["id"],
            row["source_type"],
            row["source_name"],
            row["title"],
            row["url"],
            row["author"],
            row["summary"],
            row["content_text"],
            row["published_at"],
            row["tags"],
            row["created_at"],
            row["updated_at"],
        ]
        placeholders = ",".join(["?"] * len(values))
        sql = f"""INSERT OR REPLACE INTO read_archive ({','.join(cols)}) VALUES ({placeholders})"""
        try:
            cur.execute(sql, values)
            imported += 1
        except Exception as e:
            print(f"[错误] 插入失败 id={row['id']} title={row['title'][:40]}: {e}")
            skipped += 1

    conn.commit()
    print(f"\n迁移完成: 导入 {imported} 条, 跳过 {skipped} 条")

    # 删除articles里的记录
    delete = input("\n确认删除 articles 里已经迁移完的记录? (y/N) ").strip().lower()
    if delete == "y":
        cur.execute("DELETE FROM articles WHERE source_type = 'read-archive'")
        deleted = cur.rowcount
        conn.commit()
        print(f"删除了 {deleted} 条记录")
    else:
        print("已跳过删除步骤")

    conn.close()
    print("done")


if __name__ == "__main__":
    main()
