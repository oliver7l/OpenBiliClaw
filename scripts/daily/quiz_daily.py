#!/usr/bin/env python3
"""每日刷题（B1）：从统一刷题队列选出今天该读的题，接进早报。

- 复用 iq_plans 里的激活计划（如「秋招面试冲刺」）的 daily_target。
- 当天已选过则原样返回（把已选 id 存在 iq_daily.notes 的 JSON 里，幂等）。
- 只写 iq_daily 一张表的当日行，其余只读。

用法：
    .venv/bin/python scripts/daily/quiz_daily.py            # 按计划目标选题
    .venv/bin/python scripts/daily/quiz_daily.py --count 3

输出：今日题目清单 + 欠账提醒 + 最后一行 JSON（today_ids/count/backlog）。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "interview_questions.db"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=0, help="覆盖计划目标")
    args = parser.parse_args()

    conn = sqlite3.connect(str(DB), timeout=20)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    plan = conn.execute(
        "SELECT id, name, daily_target FROM iq_plans WHERE is_active=1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if not plan:
        print("⚠️ 没有激活的刷题计划（iq_plans.is_active=1），先建一个计划。")
        print(json.dumps({"ok": False, "reason": "no_active_plan"}, ensure_ascii=False))
        return
    plan_id, plan_name, target = plan
    if args.count:
        target = args.count

    backlog = conn.execute(
        "SELECT COUNT(*) FROM iq_queue q WHERE q.question_id NOT IN (SELECT question_id FROM iq_records)"
    ).fetchone()[0]

    row = conn.execute(
        "SELECT id, questions_read, notes FROM iq_daily WHERE plan_id=? AND progress_date=?",
        (plan_id, today),
    ).fetchone()

    ids: list[int] = []
    if row and row[2]:
        try:
            ids = json.loads(row[2]).get("ids", [])
        except ValueError:
            ids = []
    if not ids:
        rows = conn.execute(
            "SELECT q.question_id, t.title, t.category, t.difficulty, t.source "
            "FROM iq_queue q JOIN iq_questions t ON t.id = q.question_id "
            "WHERE q.question_id NOT IN (SELECT question_id FROM iq_records) "
            "ORDER BY CASE q.priority WHEN 'high' THEN 0 ELSE 1 END, q.id LIMIT ?",
            (target,),
        ).fetchall()
        ids = [r[0] for r in rows]
        if row:
            conn.execute("UPDATE iq_daily SET notes=?, target=? WHERE id=?", (json.dumps({"ids": ids}), target, row[0]))
        else:
            conn.execute(
                "INSERT INTO iq_daily(plan_id, progress_date, questions_read, questions_mastered, target, notes, created_at)"
                " VALUES(?,?,0,0,?,?,?)",
                (plan_id, today, target, json.dumps({"ids": ids}), today),
            )
        conn.commit()

    items = []
    for qid in ids:
        r = conn.execute(
            "SELECT title, category, difficulty, source FROM iq_questions WHERE id=?", (qid,)
        ).fetchone()
        if r:
            items.append((qid, *r))

    y_row = conn.execute(
        "SELECT questions_read, target FROM iq_daily WHERE plan_id=? AND progress_date=?",
        (plan_id, yesterday),
    ).fetchone()
    y_read, y_target = (y_row if y_row else (None, None))

    conn.close()

    print(f"📚 今日刷题 · {plan_name}（目标 {target} 题，待刷积压 {backlog} 题）")
    if not items:
        print("   队列已清空或无可选题，考虑补充新题。")
    for n, (qid, title, cat, diff, source) in enumerate(items, 1):
        cat = f"{cat} · " if cat else ""
        print(f"   {n}. [难度{diff}] {title[:80]}  （{cat}{source}）")

    if y_read is not None and y_target:
        if y_read < y_target:
            print(f"   ⏰ 昨天读了 {y_read}/{y_target}，还欠 {y_target - y_read} 题——今天优先补上。")
        else:
            print(f"   ✅ 昨天目标达成（{y_read}/{y_target}），保持节奏。")

    print(json.dumps({
        "ok": True, "plan_id": plan_id, "today_ids": ids, "count": len(items),
        "target": target, "backlog": backlog,
        "yesterday_done": y_read, "yesterday_target": y_target,
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
