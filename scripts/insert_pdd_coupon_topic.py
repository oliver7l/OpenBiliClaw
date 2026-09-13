#!/usr/bin/env python3
"""将「发券如何融入推荐系统」专题写入 interview.db 的 kb_documents 表（doc_type='面试专题'）。

直连 SQLite，不依赖 InterviewEngine（避免触发 LLM 429）。
撤销：DELETE FROM kb_documents WHERE doc_type='面试专题' AND topic LIKE '发券如何融入推荐系统%';
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "interview.db"
SRC = ROOT / "求职知识库" / "03_岗位弹药库" / "拼多多-面试准备" / "02_面试备战资料" / "发券融入推荐系统_专题.md"

TOPIC = "发券如何融入推荐系统：完整改造专题"
CATEGORY = "拼多多"
CHAPTER = "因果推断·Uplift·智能补贴"
TAGS = "发券,uplift,因果推断,推荐系统,拼多多,智能补贴,预算分配,AUUC"
DOC_TYPE = "面试专题"


def main() -> None:
    content = SRC.read_text(encoding="utf-8")
    now = datetime.now().isoformat(timespec="seconds")
    con = sqlite3.connect(str(DB))
    con.execute(
        """
        INSERT INTO kb_documents
            (topic, category, chapter, content, tags, source_path, doc_type, updated, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (TOPIC, CATEGORY, CHAPTER, content, TAGS, str(SRC), DOC_TYPE, now, now),
    )
    con.commit()
    row_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.close()
    print(f"inserted kb_documents id={row_id} topic={TOPIC!r} len(content)={len(content)}")


if __name__ == "__main__":
    main()
