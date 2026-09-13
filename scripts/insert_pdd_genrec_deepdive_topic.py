#!/usr/bin/env python3
"""将「生成式推荐双项目深读：MiniOneRec × OpenOneRec」专题写入 interview.db 的 kb_documents 表。

直连 SQLite，不依赖 InterviewEngine（避免触发 LLM 429）。
撤销：DELETE FROM kb_documents WHERE doc_type='面试专题' AND topic LIKE '生成式推荐双项目深读%';
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "interview.db"
SRC = (
    ROOT
    / "求职知识库"
    / "03_岗位弹药库"
    / "拼多多-面试准备"
    / "02_面试备战资料"
    / "生成式推荐双项目深读_MiniOneRec与OpenOneRec.md"
)

TOPIC = "生成式推荐双项目深读：MiniOneRec × OpenOneRec（SID/Itemic Token、GRPO 奖励、Scaling Law、抗遗忘）"
CATEGORY = "拼多多"
CHAPTER = "生成式推荐·生成式重排·推荐大模型"
TAGS = "生成式推荐,MiniOneRec,OpenOneRec,OneRec,SID,ItemicToken,RQ-VAE,RQ-Kmeans,GRPO,约束解码,ScalingLaw,灾难性遗忘,on-policy蒸馏,RecIF-Bench,拼多多"
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
