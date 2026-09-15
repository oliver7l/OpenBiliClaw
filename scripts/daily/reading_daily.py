#!/usr/bin/env python3
"""阅读库日报（C1）：昨天读了什么 + 明天推荐读什么 + 画像更新。

只读。数据来源：GET /api/reading/daily-brief（服务线内部读 content.db / pool / 画像）。

用法：
    .venv/bin/python scripts/daily/reading_daily.py
    .venv/bin/python scripts/daily/reading_daily.py --top 5

输出：可直接发送的日报文本 + 最后一行 JSON。
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_BASE = os.environ.get("OBC_API_BASE", "http://127.0.0.1:8420").rstrip("/")

SOURCE_LABEL = {
    "rss": "RSS/公众号",
    "zhihu": "知乎",
    "bilibili": "B站",
    "xiaoyuzhou": "小宇宙",
    "v2ex": "V2EX",
    "youtube": "YouTube",
    "xhs": "小红书",
    "douyin": "抖音",
    "twitter": "X",
    "reddit": "Reddit",
    "hupu": "虎扑",
    "toutiao": "头条",
    "douban": "豆瓣",
}


def api_get(path: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(f"{API_BASE}{path}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def clip(text: str, limit: int = 52) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=5, help="明天推荐几条")
    args = parser.parse_args()

    try:
        payload = api_get("/api/reading/daily-brief")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 阅读库日报接口不可用：{exc}")
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return

    day = payload.get("date", "")
    reading = payload.get("reading") or {}
    profile = payload.get("profile") or {}
    tomorrow = payload.get("tomorrow") or []

    print(f"📚 阅读库日报 · {day}")

    finished = reading.get("finished_today", 0)
    by_source = reading.get("by_source") or {}
    topics = reading.get("top_topics") or []
    if finished:
        detail = "、".join(f"{SOURCE_LABEL.get(k, k)} {v}" for k, v in by_source.items())
        print(f"\n昨天读完 {finished} 篇（{detail}）")
    else:
        print("\n昨天没有标记读完的内容")
    if topics:
        print(f"涉及主题：{'、'.join(str(t) for t in topics[:5])}")

    updates = profile.get("updates") or []
    if updates:
        print("\n【画像更新】")
        for u in updates[:3]:
            print(f"  · {updates.index(u) + 1}. {clip(u.get('summary', ''), 70)}")

    if tomorrow:
        print(f"\n【明天推荐 {min(len(tomorrow), args.top)} 条】")
        for item in tomorrow[: args.top]:
            score = item.get("fit_score")
            score_text = f"（匹配 {score:.2f}）" if isinstance(score, (int, float)) else ""
            src = SOURCE_LABEL.get(item.get("source_type", ""), item.get("source_type", ""))
            print(f"  · [{src}] {clip(item.get('title', ''), 60)}{score_text}")
            reason = item.get("fit_reason") or []
            if reason:
                print(f"     因为：{'、'.join(str(r) for r in reason[:4])}")
            if item.get("url"):
                print(f"     {item['url']}")
    else:
        print("\n【明天推荐】暂无（候选池可能为空，检查采集线）")

    print("\n" + json.dumps({
        "date": day,
        "finished_today": finished,
        "profile_updates": len(updates),
        "tomorrow": len(tomorrow),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
