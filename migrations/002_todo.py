"""待办事项（todo）表迁移。

迁移内容（幂等，可重复执行）：
    在 data/interview.db 创建 todo 表（求职域待办：追问 HR、投递跟进、面试准备等）。

执行：
    .venv/bin/python migrations/002_todo.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "interview.db"

DDL = """
CREATE TABLE IF NOT EXISTS todo (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    company TEXT DEFAULT '',
    due_date TEXT DEFAULT '',
    priority TEXT DEFAULT '中',
    status TEXT DEFAULT 'pending',
    created_at TEXT NOT NULL,
    done_at TEXT DEFAULT ''
)
"""


def migrate() -> None:
    """执行迁移（幂等）。"""
    print(f"数据库路径：{DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(DDL)
        conn.commit()
        print("✅ todo 表已确保")
    finally:
        conn.close()


def verify() -> None:
    """迁移后验证。"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(todo)").fetchall()]
        expected = ["id", "title", "detail", "company", "due_date", "priority", "status", "created_at", "done_at"]
        missing = [c for c in expected if c not in cols]
        if missing:
            print(f"❌ todo 缺失字段：{missing}")
        else:
            print(f"✅ todo 字段就位（{len(cols)} 列）")
        n = conn.execute("SELECT COUNT(*) AS n FROM todo").fetchone()["n"]
        print(f"ℹ️  现有待办数：{n}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
    verify()
    print("🎉 待办表迁移完成")
