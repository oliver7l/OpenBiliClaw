#!/usr/bin/env python3
"""合并三套题表（B4）：把 interview.db.interview_questions（岗位预测题）并入
interview_questions.db 的 iq_questions / iq_queue，统一成一个刷题队列。

幂等：以 (title, source) 为唯一键，重复运行不会产生重复题。
只新增，不修改/删除已有题目。

用法：
    .venv/bin/python scripts/daily/merge_question_pools.py

输出：导入统计 + 最后一行 JSON（imported/enqueued/skipped）。
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_DB = ROOT / "data" / "interview.db"
DST_DB = ROOT / "data" / "interview_questions.db"

HIGH_KW = re.compile(r"必考|高频|最高概率|高概率|必问")


def main() -> None:
    src = sqlite3.connect(f"file:{SRC_DB}?mode=ro", uri=True)
    dst = sqlite3.connect(str(DST_DB), timeout=20)

    existing = {
        (t, s) for (t, s) in dst.execute("SELECT title, source FROM iq_questions")
    }
    queued = {q for (q,) in dst.execute("SELECT question_id FROM iq_queue")}

    rows = src.execute(
        "SELECT company, position, category, question, answer FROM interview_questions ORDER BY id"
    ).fetchall()

    imported = enqueued = skipped = 0
    now = datetime.now().isoformat()
    for company, position, category, question, answer in rows:
        title = (question or "").strip()
        if not title:
            skipped += 1
            continue
        source = f"{company}·面试预测题"
        if (title, source) in existing:
            skipped += 1
            continue
        cat = (category or "").strip() or "面试题"
        tags = ",".join(x for x in (company, position, cat) if x)
        cur = dst.execute(
            "INSERT INTO iq_questions(title, answer, category, difficulty, source, tags, url, notes,"
            " created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (title, answer or "", cat, 3, source, tags, "", "", now, now),
        )
        qid = cur.lastrowid
        existing.add((title, source))
        imported += 1
        if qid not in queued:
            prio = "high" if HIGH_KW.search(cat) else "normal"
            dst.execute(
                "INSERT INTO iq_queue(question_id, priority, added_at, planned_date) VALUES(?,?,?,NULL)",
                (qid, prio, now),
            )
            queued.add(qid)
            enqueued += 1

    dst.commit()
    total = dst.execute("SELECT COUNT(*) FROM iq_questions").fetchone()[0]
    in_queue = dst.execute("SELECT COUNT(DISTINCT question_id) FROM iq_queue").fetchone()[0]
    backlog = dst.execute(
        "SELECT COUNT(*) FROM iq_queue q WHERE q.question_id NOT IN (SELECT question_id FROM iq_records)"
    ).fetchone()[0]
    src.close()
    dst.close()

    print(f"🧩 题池合并完成：预测题导入 {imported} 条（跳过重复 {skipped}），入队 {enqueued} 条")
    print(f"   当前统一题库 {total} 题，已入队 {in_queue} 题，待刷 {backlog} 题")
    print(json.dumps({
        "imported": imported, "enqueued": enqueued, "skipped": skipped,
        "total": total, "backlog": backlog,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
