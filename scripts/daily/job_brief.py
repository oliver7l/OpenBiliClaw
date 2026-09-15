#!/usr/bin/env python3
"""每日求职简报（A1）：今日面试 + 未完成待办 + 近期面试安排。

只读，不做任何写入。数据来源：
    1. GET /api/interview/job/todos   —— 面试待办（interview.db.todo）
    2. GET /api/interview/job/status  —— 岗位备战索引（求职知识库）
    3. resume.db.applications         —— 投递状态里的面试时间（interview_at）

用法：
    .venv/bin/python scripts/daily/job_brief.py            # 默认看未来 3 天
    .venv/bin/python scripts/daily/job_brief.py --days 7

输出：人类可读简报 + 最后一行 JSON（todos_today/overdue/upcoming），供自动化判断。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_BASE = os.environ.get("OBC_API_BASE", "http://127.0.0.1:8420").rstrip("/")
RESUME_DB = ROOT / "data" / "resume.db"

PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}
UNFINISHED_JOB_STATES = ("已面", "已结束", "已终止")


def api_get(path: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(f"{API_BASE}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_todos() -> list[dict]:
    try:
        payload = api_get("/api/interview/job/todos")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 待办接口不可用：{exc}")
        return []
    return [t for t in (payload.get("items") or []) if t.get("status") != "done"]


def fetch_jobs() -> list[dict]:
    try:
        payload = api_get("/api/interview/job/status")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 岗位索引接口不可用：{exc}")
        return []
    return payload.get("jobs") or []


def fetch_application_interviews() -> list[dict]:
    """从 resume.db.applications 里取带 interview_at 的记录（只读）。"""
    if not RESUME_DB.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{RESUME_DB}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT company, role, status, interview_at FROM applications "
            "WHERE interview_at IS NOT NULL AND interview_at != '' "
            "AND interview_at NOT LIKE '未约面%' ORDER BY interview_at"
        ).fetchall()
        conn.close()
        return [{"company": r[0], "role": r[1], "status": r[2], "interview_at": r[3]} for r in rows]
    except Exception:  # noqa: BLE001
        return []


def _parse_date(text: str) -> date | None:
    """从任意含日期的字符串里抠出 'YYYY-MM-DD'（支持 2026-09-17 16:00 / 2026-09下旬 等）。"""
    text = (text or "").strip()
    if len(text) < 10:
        return None
    head = text[:10]
    try:
        return datetime.strptime(head, "%Y-%m-%d").date()
    except ValueError:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=3, help="未来几天内的面试纳入简报")
    args = parser.parse_args()

    today = date.today()
    horizon = today + timedelta(days=args.days)

    todos = fetch_todos()
    overdue, due_today, due_soon = [], [], []
    for t in todos:
        d = _parse_date(t.get("due_date", ""))
        if d is None:
            due_soon.append(t)
        elif d < today:
            overdue.append(t)
        elif d == today:
            due_today.append(t)
        elif d <= horizon:
            due_soon.append(t)

    def key(t: dict) -> tuple[int, str]:
        return (PRIORITY_ORDER.get(t.get("priority", "中"), 1), t.get("due_date", ""))

    for bucket in (overdue, due_today, due_soon):
        bucket.sort(key=key)

    upcoming = []
    for app in fetch_application_interviews():
        d = _parse_date(app["interview_at"])
        if d and today <= d <= horizon:
            upcoming.append(app)
    for job in fetch_jobs():
        state = (job.get("状态") or "").strip()
        if any(state.startswith(s) for s in UNFINISHED_JOB_STATES):
            continue
        d = _parse_date(job.get("面试时间", ""))
        if d and today <= d <= horizon:
            upcoming.append({
                "company": job.get("公司", ""),
                "role": job.get("岗位", ""),
                "status": f"备战索引:{state}",
                "interview_at": job.get("面试时间", ""),
            })

    print(f"📋 求职简报 · {today.isoformat()}（未来 {args.days} 天）")
    if overdue:
        print(f"\n🔴 已逾期 {len(overdue)} 条：")
        for t in overdue:
            print(f"  · [{t['due_date']}] {t['title']}（{t.get('company','')}·{t.get('priority','')}）")
    if due_today:
        print(f"\n📌 今天到期 {len(due_today)} 条：")
        for t in due_today:
            print(f"  · {t['title']}（{t.get('company','')}·{t.get('priority','')}）")
            if t.get("detail"):
                print(f"     {t['detail'][:180]}")
    if due_soon:
        print(f"\n🕐 未来 {args.days} 天待办 {len(due_soon)} 条：")
        for t in due_soon:
            print(f"  · [{t.get('due_date') or '无期限'}] {t['title']}（{t.get('company','')}）")
    if not (overdue or due_today or due_soon):
        print("\n✅ 未来 3 天没有到期待办。")

    if upcoming:
        print(f"\n🎯 未来 {args.days} 天面试安排 {len(upcoming)} 场：")
        for app in sorted(upcoming, key=lambda x: x["interview_at"]):
            print(f"  · {app['interview_at']} | {app['company']} · {app['role']} | {app['status']}")
    else:
        print(f"\n🎯 未来 {args.days} 天没有已确认的面试。")

    print("\n" + json.dumps({
        "date": today.isoformat(),
        "overdue": len(overdue),
        "due_today": len(due_today),
        "due_soon": len(due_soon),
        "pending_total": len(todos),
        "upcoming_interviews": len(upcoming),
        "top_todo": (overdue + due_today + due_soon)[:1][0]["title"] if (overdue or due_today or due_soon) else "",
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
