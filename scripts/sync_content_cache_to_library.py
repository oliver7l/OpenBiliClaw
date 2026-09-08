#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sync content_cache (recommendation pool) into the reading library (articles).

The recommendation pool (content_cache) holds ~9k cross-platform items:
zhihu, bilibili, youtube, douyin, xiaohongshu, v2ex, reddit, ... The reading
library (articles) only ever received subscription-sourced items
(rss / wechat / xiaoyuzhou). This script backfills the rest so every platform
is readable in one place.

Behaviour:
- Dedupes content_cache by content_url, keeping the richest body_text/description.
- Normalizes source_platform -> source_type (user_favorite -> bilibili, etc.).
- Upserts via the same ON CONFLICT(url) logic as database.upsert_article:
  existing content_text / tags are preserved, only empty slots are filled.
- Idempotent: re-running only adds new urls / fills empty metadata. Use
  --new-only to skip urls already present in articles.
"""
import os
import sys
import sqlite3
import json
import email.utils
import datetime

# 中国本地时间(UTC+8)。articles 表所有时间字段统一存北京时间字符串。
CN_TZ = datetime.timezone(datetime.timedelta(hours=8))


def _norm_pub(s):
    """Normalize an arbitrary published_at string to 'YYYY-MM-DD HH:MM:SS' (Beijing UTC+8)."""
    if not s:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt is not None:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return dt.astimezone(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            # 无时区字面量按"已是北京时间"处理, 不再二次换算
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    return None
import argparse

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, "data", "openbiliclaw.db")

PLATFORM_MAP = {
    "zhihu": "zhihu",
    "youtube": "youtube",
    "user_favorite": "bilibili",   # B站 user favorites feed
    "bilibili": "bilibili",
    "douyin": "douyin",
    "xiaohongshu": "xiaohongshu",
    "rss": "rss",
    "xiaoyuzhou": "xiaoyuzhou",
    "v2ex": "v2ex",
    "wechat": "wechat",
    "reddit": "reddit",
}


def _norm_tags(raw, fallback):
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
        except Exception:
            pass
    return json.dumps([fallback] if fallback else [], ensure_ascii=False)


def main():
    ap = argparse.ArgumentParser(description="Sync content_cache into reading library (articles).")
    ap.add_argument("--new-only", action="store_true",
                    help="Only upsert urls not already present in articles.")
    ap.add_argument("--limit", type=int, default=0, help="Max rows to upsert (debug).")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """SELECT content_url, title, up_name, author_name, source, description,
                  body_text, discovered_at, tags, source_platform
           FROM content_cache
           WHERE content_url IS NOT NULL AND content_url <> ''"""
    ).fetchall()

    # Dedup by url, keep the richest body/description
    best = {}
    for r in rows:
        url = r["content_url"]
        bt = r["body_text"] or ""
        de = r["description"] or ""
        cur = best.get(url)
        if cur is None or len(bt) > len(cur["_bt"]) or (
            len(bt) == len(cur["_bt"]) and len(de) > len(cur["_de"])
        ):
            best[url] = {**dict(r), "_bt": bt, "_de": de}

    existing = set()
    if args.new_only:
        existing = {u for (u,) in conn.execute(
            "SELECT url FROM articles WHERE url IS NOT NULL AND url <> ''")}

    upsert_sql = """INSERT INTO articles (source_type, source_name, title, url,
                        author, summary, content_text, published_at, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(url) DO UPDATE SET
                    title=excluded.title, summary=excluded.summary,
                    content_text=CASE
                      WHEN excluded.content_text <> '' THEN excluded.content_text
                      ELSE articles.content_text END,
                    tags=CASE
                      WHEN articles.tags IS NULL OR articles.tags IN ('', '[]')
                        THEN excluded.tags ELSE articles.tags END,
                    updated_at=CURRENT_TIMESTAMP"""

    done = 0
    skipped = 0
    for url, r in best.items():
        if args.new_only and url in existing:
            skipped += 1
            continue
        st = PLATFORM_MAP.get(r["source_platform"] or "", r["source_platform"] or "other")
        author = r["up_name"] or r["author_name"] or ""
        sname = author or r["source"] or st
        tags = _norm_tags(r["tags"], sname)
        conn.execute(upsert_sql, (
            st, sname, r["title"] or "(无标题)", url, author,
            r["description"] or "", r["body_text"] or "",
            (_norm_pub(r["discovered_at"]) or datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")), tags,
        ))
        done += 1
        if args.limit and done >= args.limit:
            break

    conn.commit()
    conn.close()
    print(f"DONE upserted={done} skipped={skipped} unique_urls={len(best)} total_cache_rows={len(rows)}")


if __name__ == "__main__":
    main()
