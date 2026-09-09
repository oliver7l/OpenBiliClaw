#!/usr/bin/env python3
"""把某个话题/剧集/关键词相关的多平台内容收进阅读库(articles)。

用法:
  python3 scripts/collect_topic_to_library.py "去有风的地方" --limit 15
  python3 scripts/collect_topic_to_library.py "某关键词" --sources bili,zhihu

行为:
  - 用 bili search(--type video) 抓 B站视频, zhihu search(--type content) 抓知乎图文
  - 只收标题含关键词的结果, 按 url 去重 upsert 进 articles
  - source_type 归一 (bilibili / zhihu), source_name 用 UP主/作者名
  - published_at 设为当前时间, 让新收内容在阅读库列表排最前
  - tag 写入关键词, 便于以后聚合
  - 知乎若登录过期 (not_authenticated) 自动跳过并提示重新扫码
幂等: 已存在的 url 不会重复插入。
"""
import argparse
import datetime

# 中国本地时间(UTC+8)。articles 表所有时间字段统一存北京时间字符串。
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))
import email.utils
import json
import os
import sqlite3
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "data", "openbiliclaw.db")

BILI_PLATFORM = "bilibili"
ZHIHU_PLATFORM = "zhihu"


def _run(cmd, timeout=120):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        print(f"  ! 调用失败 {' '.join(cmd)}: {e}")
        return None
    # 即便退出码非 0, 只要 stdout 是 JSON(如知乎 {ok:false,error}) 也返回, 便于上层检测
    try:
        return json.loads(out.stdout)
    except Exception:  # noqa: BLE001
        return None


def _fetch_bili(keyword, limit):
    data = _run(["bili", "search", "--type", "video", keyword, "-n", str(limit), "--json"])
    if not data:
        return []
    if isinstance(data, dict):
        if not data.get("ok"):
            print(f"  ! B站搜索未返回结果 (可能未登录或限流)")
            return []
        items = data.get("data", [])
    else:
        items = data
    out = []
    for it in items:
        bvid = it.get("bvid") or it.get("id")
        if not bvid:
            continue
        title = (it.get("title") or "").strip()
        if keyword not in title:
            continue
        out.append({
            "url": f"https://www.bilibili.com/video/{bvid}",
            "title": title,
            "author": (it.get("author") or "").strip(),
            "summary": f"{(it.get('author') or '').strip()} · 时长 {it.get('duration','')} · 播放 {it.get('play') or 0}",
            "platform": BILI_PLATFORM,
        })
    return out


def _fetch_zhihu(keyword, limit):
    data = _run(["zhihu", "search", "--type", "content", keyword, "-n", str(limit), "--json"])
    if not data:
        return []
    if isinstance(data, dict) and not data.get("ok"):
        err = (data.get("error") or {}).get("message", "")
        if "not_authenticated" in err or "TICKET" in err:
            print(f"  ! 知乎登录态已过期, 跳过知乎。请运行 `zhihu login --qrcode` 重新扫码后再补。({err})")
        else:
            print(f"  ! 知乎搜索失败: {err}")
        return []
    items = data.get("data", []) if isinstance(data, dict) else data
    out = []
    for it in items:
        url = it.get("url") or it.get("link") or ""
        title = (it.get("title") or "").strip()
        if not url or keyword not in title:
            continue
        out.append({
            "url": url,
            "title": title,
            "author": (it.get("author") or it.get("author_name") or "").strip(),
            "summary": (it.get("excerpt") or it.get("summary") or "").strip(),
            "platform": ZHIHU_PLATFORM,
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword", help="话题/剧集关键词, 如 '去有风的地方'")
    ap.add_argument("--limit", type=int, default=15)
    ap.add_argument("--sources", default="bili,zhihu", help="逗号分隔: bili,zhihu")
    args = ap.parse_args()

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    collected = []
    if "bili" in sources:
        print(f"[B站] 搜索 '{args.keyword}' ...")
        collected += _fetch_bili(args.keyword, args.limit)
    if "zhihu" in sources:
        print(f"[知乎] 搜索 '{args.keyword}' ...")
        collected += _fetch_zhihu(args.keyword, args.limit)
    print(f"候选 (标题含关键词): {len(collected)} 条")

    now_iso = datetime.datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    tag_json = json.dumps([args.keyword], ensure_ascii=False)
    conn = sqlite3.connect(DB_PATH)
# v0.4.0+: articles 表迁移到 content.db，ATTACH 以便跨库查询
try:
    from pathlib import Path as _Path
    _content_db = _Path(__file__).parent.parent / "data" / "content.db"
    if _content_db.exists():
        conn.execute("ATTACH DATABASE ? AS content", (str(_content_db),))
except Exception:
    pass

    cur = conn.cursor()
    inserted = skipped = 0
    for item in collected:
        if cur.execute("SELECT 1 FROM articles WHERE url=?", (item["url"],)).fetchone():
            skipped += 1
            continue
        cur.execute(
            """INSERT INTO articles
               (source_type, source_name, title, url, author, summary, content_text,
                published_at, tags, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (item["platform"], item["author"] or item["platform"], item["title"],
             item["url"], item["author"], item["summary"], "", now_iso, tag_json,
             "unread", now_iso, now_iso),
        )
        inserted += 1
    conn.commit()
    conn.close()
    print(f"完成: 新增 {inserted} 条, 已存在跳过 {skipped} 条")


if __name__ == "__main__":
    main()
