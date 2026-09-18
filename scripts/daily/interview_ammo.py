#!/usr/bin/env python3
"""面试前夜弹药（B3）：找出明天（含今天剩余）的面试，生成速记卡弹药文本。

数据来源（只读）：
    1. interview.db.todo        —— kind='interview' 且 due_date 命中
    2. resume.db.applications   —— interview_at 以日期前缀命中
    3. interview.db.interview_questions —— 该公司预测题
    4. 求职知识库/03_岗位弹药库   —— 同名速记卡文件（若存在，给出路径）

用法：
    .venv/bin/python scripts/daily/interview_ammo.py          # 看今天+明天
    .venv/bin/python scripts/daily/interview_ammo.py --days 2 # 看未来 2 天

输出：弹药卡文本 + 最后一行 JSON（has_interviews/items）。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IV_DB = ROOT / "data" / "interview.db"
RESUME_DB = ROOT / "data" / "resume.db"
AMMO_DIR = ROOT / "求职知识库" / "03_岗位弹药库"

TIME_RE = re.compile(r"\d{1,2}:\d{2}")
DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def todos_on(day: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{IV_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT id, title, company, due_date, detail FROM todo "
        "WHERE kind='interview' AND status='pending' AND due_date=?",
        (day,),
    ).fetchall()
    conn.close()
    return [{"src": "todo", "id": r[0], "title": r[1], "company": r[2] or "", "time_hint": "",
             "detail": r[4] or "", "when": r[3]} for r in rows]


def applications_on(day: str) -> list[dict]:
    if not RESUME_DB.exists():
        return []
    conn = sqlite3.connect(f"file:{RESUME_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT company, role, stage, interview_at, note, round_note FROM applications "
        "WHERE interview_at IS NOT NULL AND interview_at != ''"
    ).fetchall()
    conn.close()
    out = []
    for company, role, stage, iv, note, round_note in rows:
        m = DATE_RE.search(iv or "")
        if m and m.group(1) == day:
            out.append({"src": "resume", "id": 0, "title": f"{company} {role}（{iv}）",
                        "company": company, "time_hint": iv, "detail": "\n".join(x for x in (note, round_note) if x),
                        "when": day, "stage": stage})
    return out


def company_questions(company: str, limit: int = 12) -> list[str]:
    if not company:
        return []
    conn = sqlite3.connect(f"file:{IV_DB}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT category, question FROM interview_questions WHERE company=? AND company != '' ORDER BY id LIMIT ?",
        (company, limit),
    ).fetchall()
    conn.close()
    return [f"[{c}] {q}" for c, q in rows]


def ammo_note_file(company: str) -> str:
    if not company or not AMMO_DIR.exists():
        return ""
    for p in AMMO_DIR.rglob("*速记卡*.md"):
        if company[:2] in p.name or company in p.name:
            return str(p)
    for p in AMMO_DIR.rglob("*弹药*.md"):
        if company[:2] in str(p.parent) or company[:2] in p.name:
            return str(p)
    return ""


def build_card(item: dict) -> str:
    company = item["company"]
    qs = company_questions(company)
    card = [f"⏰ 明日面试弹药 · {item['title']}"]
    detail = (item.get("detail") or "").strip()
    if detail:
        card.append(f"📌 面试信息：{detail[:600]}")
    if qs:
        card.append("🎯 该公司预测题（重点看带★/必考）：")
        for q in qs[:10]:
            card.append(f"   - {q[:100]}")
    note = ammo_note_file(company)
    if note:
        card.append(f"📇 速记卡：{note}")
    return "\n".join(card)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1, help="往后看几天（默认 1=只看明天）")
    parser.add_argument("--include-today", action="store_true", help="额外包含今天剩余的面试")
    args = parser.parse_args()

    start = 0 if args.include_today else 1
    days = [(date.today() + timedelta(days=i)).isoformat() for i in range(start, args.days + 1)]
    items: list[dict] = []
    seen = set()
    for day in days:
        for it in todos_on(day) + applications_on(day):
            key = (it["company"] or it["title"], day)
            if key in seen:
                continue
            seen.add(key)
            items.append(it)

    if not items:
        print(json.dumps({"has_interviews": False, "items": []}, ensure_ascii=False))
        return

    items.sort(key=lambda x: x["when"])
    cards = [build_card(it) for it in items]
    print("\n\n".join(cards))
    print("\n" + json.dumps({
        "has_interviews": True,
        "items": [{"company": it["company"], "when": it["when"], "title": it["title"]} for it in items],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
