#!/usr/bin/env python3
"""日记晨间简报（B2）：晨间简报 + 昨日口述碎片 + 待收口的未闭环事项。

只读。数据来源：
    1. GET /api/diary/insights/morning-briefing  —— 昨日回顾 / 今日提醒 / 历史上的今天
    2. diary.db.diary_fragments                  —— 昨日口述碎片（简报里的 entry_count 读的是 diary_entries，常有偏差）
    3. diary.db.diary_open_loops                 —— 未闭环事项（按最后提及时间取最近的几条）

用法：
    .venv/bin/python scripts/daily/diary_morning.py
    .venv/bin/python scripts/daily/diary_morning.py --loops 3

输出：可直接发送的简报文本 + 最后一行 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_BASE = os.environ.get("OBC_API_BASE", "http://127.0.0.1:8420").rstrip("/")
DIARY_DB = ROOT / "data" / "diary.db"


def api_get(path: str, query: dict | None = None, timeout: int = 30) -> dict:
    url = f"{API_BASE}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def fetch_briefing(day: date) -> dict:
    try:
        payload = api_get("/api/diary/insights/morning-briefing", {"date": day.isoformat()})
        return payload.get("data") or {}
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 晨间简报接口不可用：{exc}")
        return {}


def fetch_fragments(day: date) -> list[dict]:
    if not DIARY_DB.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{DIARY_DB}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT content, mood, tags, fragment_type FROM diary_fragments "
            "WHERE fragment_date = ? ORDER BY id",
            (day.isoformat(),),
        ).fetchall()
        conn.close()
        return [{"content": r[0] or "", "mood": r[1] or "", "tags": r[2] or "", "type": r[3] or ""} for r in rows]
    except Exception:  # noqa: BLE001
        return []


def fetch_open_loops(limit: int, recent_days: int = 60) -> list[dict]:
    """取未闭环事项：优先最近 recent_days 天内提及过的，避免翻出陈年库存。"""
    if not DIARY_DB.exists():
        return []
    cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
    try:
        conn = sqlite3.connect(f"file:{DIARY_DB}?mode=ro", uri=True)
        rows = conn.execute(
            "SELECT content, loop_type, priority, last_mentioned_date, mention_count "
            "FROM diary_open_loops WHERE status = 'open' AND last_mentioned_date >= ? "
            "ORDER BY last_mentioned_date DESC, mention_count DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        conn.close()
        return [{"content": r[0] or "", "type": r[1] or "", "priority": r[2] or "",
                 "last": r[3] or "", "mentions": r[4] or 0} for r in rows]
    except Exception:  # noqa: BLE001
        return []


def fetch_recent_open_loop_total(recent_days: int = 60) -> tuple[int, int]:
    """返回 (近期未闭环数, 未闭环总数)。"""
    if not DIARY_DB.exists():
        return 0, 0
    cutoff = (date.today() - timedelta(days=recent_days)).isoformat()
    try:
        conn = sqlite3.connect(f"file:{DIARY_DB}?mode=ro", uri=True)
        total = conn.execute("SELECT COUNT(*) FROM diary_open_loops WHERE status='open'").fetchone()[0]
        recent = conn.execute(
            "SELECT COUNT(*) FROM diary_open_loops WHERE status='open' AND last_mentioned_date >= ?",
            (cutoff,),
        ).fetchone()[0]
        conn.close()
        return recent, total
    except Exception:  # noqa: BLE001
        return 0, 0


def clip(text: str, limit: int = 58) -> str:
    """按标点截断，避免把句子砍在括号里。"""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    for punct in ("。", "，", "、", "；", "：", "!", "?", "！", "？"):
        idx = cut.rfind(punct)
        if idx >= limit // 2:
            return cut[: idx + 1]
    return cut + "…"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loops", type=int, default=3, help="最多提醒几条未闭环事项")
    args = parser.parse_args()

    today = date.today()
    yesterday = today - timedelta(days=1)
    briefing = fetch_briefing(today)
    fragments = fetch_fragments(yesterday)
    loops = fetch_open_loops(args.loops)
    loop_recent, loop_total = fetch_recent_open_loop_total()
    print(f"🌅 {today.isoformat()} 晨间简报")

    y = briefing.get("yesterday_summary") or {}
    print(f"\n【昨天 {y.get('date', yesterday.isoformat())}】")
    if fragments:
        print(f"  口述 {len(fragments)} 条：")
        for f in fragments[:5]:
            mood = f"（心情 {f['mood']}）" if f["mood"] else ""
            print(f"   · {clip(f['content'])}{mood}")
        if len(fragments) > 5:
            print(f"   · …还有 {len(fragments) - 5} 条")
    elif y.get("entry_count"):
        print(f"  日记 {y['entry_count']} 篇 / {y.get('total_words', 0)} 字")
    else:
        print("  昨天没有记录（要不要补一段？）")

    reminders = briefing.get("today_reminders") or []
    if reminders:
        print("\n【今天想做的事】")
        for r in reminders[:4]:
            print(f"   {clip(r, 70)}")

    history = briefing.get("historical_context") or []
    if history:
        print("\n【历史上的今天】")
        for h in history[:2]:
            print(f"   {clip(h, 90)}")

    if loops:
        # 去掉与「今天想做的事」重复的条目（同一件事常同时出现在两处）
        reminder_blob = "".join(reminders)
        noise = ("目标：", "📌", "打算买个")
        filtered = [
            lp for lp in loops
            if not any(n in lp["content"] for n in noise)
            and lp["content"][:12] and lp["content"][:12] not in reminder_blob
        ]
        if filtered:
            print(f"\n【待收口 · 近期 {loop_recent} 条 / 累计 {loop_total} 条】")
            for lp in filtered:
                tag = {"goal": "目标", "todo": "待办", "promise": "承诺"}.get(lp["type"], lp["type"] or "事项")
                print(f"   · [{tag}] {clip(lp['content'])}（最后提及 {lp['last']}）")

    mood_forecast = briefing.get("mood_forecast") or ""
    if mood_forecast:
        print(f"\n情绪提示：{mood_forecast}")

    print("\n" + json.dumps({
        "date": today.isoformat(),
        "fragments_yesterday": len(fragments),
        "reminders": len(reminders),
        "open_loops_total": loop_total,
        "has_content": bool(fragments or reminders or loops),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
